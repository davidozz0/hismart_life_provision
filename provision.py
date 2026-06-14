#!/usr/bin/env python3
"""HiSmart Provision - Interactive Wi-Fi provisioning for Hisense smart devices."""

import json, os, sys, getpass, time

from hismart_provision.auth import AylaAuth
from hismart_provision.wifi_win import WindowsWiFi
from hismart_provision.provision import DeviceProvisioner
from hismart_provision.bind import DeviceBinder
from hismart_provision.credentials import save, load, clear
from hismart_provision.log import get_logger

ARGS = {"device": None, "yes": False, "clear": False, "dsn": None}
for a in sys.argv[1:]:
    if a == "--yes": ARGS["yes"] = True
    elif a == "--clear": ARGS["clear"] = True
    elif a.startswith("--device="): ARGS["device"] = a.split("=",1)[1]
    elif a.startswith("--dsn="): ARGS["dsn"] = a.split("=",1)[1]

LOG_FILE = os.path.join(os.path.dirname(__file__), "provision.log")
import logging
fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("[%(asctime)s.%(msecs)03d] %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S"))
logging.getLogger("hismart").addHandler(fh)

SEP  = "  " + "-" * 55
SEP2 = "  " + "=" * 55

_step = [0]
def step(title: str) -> None:
    _step[0] += 1
    line = "-" * 55
    msg = f"\n  {line}\n  --- STEP {_step[0]}/9: {title} ---\n  {line}"
    print(msg)
    get_logger("hismart").info("STEP %d/9: %s", _step[0], title)

def info(msg: str) -> None:
    print(f"  {msg}")

def confirm(msg: str) -> bool:
    if ARGS["yes"]: return True
    return input(f"  {msg} [Y/n] ").strip().lower() in ("","y","yes")


def main():
    if ARGS["clear"]:
        clear()
        print("Saved credentials deleted.")
        return

    print()
    print("=" * 60)
    print("  HiSmart Provision - Hisense Smart Device Setup")
    print("=" * 60)

    wifi = WindowsWiFi()
    auth = AylaAuth()
    provisioner = DeviceProvisioner(wifi)
    binder = DeviceBinder(auth)

    # --- 1: Login ---
    step("LOGIN to Ayla Cloud")
    saved = load()
    if saved:
        print(f"  Account : {saved['email']}")
        print(f"  Home Wi-Fi: {saved['home_ssid']}")
        email, password = saved["email"], saved["password"]
        home_ssid, home_pwd = saved["home_ssid"], saved["home_pwd"]
    else:
        email = input("  Email: ").strip()
        password = getpass.getpass("  Password: ")
        home_ssid = input("  Home Wi-Fi SSID: ").strip()
        home_pwd = getpass.getpass("  Home Wi-Fi password: ")
        if confirm("Save credentials?"):
            save(email, password, home_ssid, home_pwd)

    print(f"  POST https://user-field-eu.aylanetworks.com/users/sign_in.json")
    auth.login(email, password)
    print(f"  -> Logged in. Token valid 24h")

    # â”â”â”â”â”â”â”â”â”â” 2: Scan â”â”â”â”â”â”â”â”â”â”
    step("SCAN for Hisense devices")
    device_ssid = ARGS["device"]
    for attempt in range(3):
        devices = provisioner.scan_for_devices()
        if devices: break
        print(f"  Retry {attempt+1}...")
        time.sleep(3)
    if not devices:
        print("  No devices found. Is device in pairing mode?")
        sys.exit(1)
    for d in devices:
        print(f"  Found: {d['ssid']}  signal={d.get('signal','?')}%")
    print(SEP)

    if device_ssid:
        chosen = next((d for d in devices if d["ssid"] == device_ssid), None)
        if not chosen:
            print(f"  {device_ssid} not found!")
            sys.exit(1)
    elif len(devices) == 1:
        chosen = devices[0]
    else:
        choice = input(f"  Select [1-{len(devices)}]: ").strip()
        chosen = devices[int(choice)-1]

    device_ssid = chosen["ssid"]
    print(f"  -> Selected: {device_ssid}")

    # â”â”â”â”â”â”â”â”â”â” 3: Connect â”â”â”â”â”â”â”â”â”â”
    step(f"CONNECT to device hotspot")
    print(f"  SSID: {device_ssid}")
    print(f"  Type: open (no password)")
    if not ARGS["yes"] and not confirm("Switch Wi-Fi to this network?"):
        sys.exit(0)
    if not provisioner.connect_to_device(device_ssid):
        print("  FAILED to connect!")
        sys.exit(1)
    print(f"  -> Connected. PC IP on device: ~192.168.0.100")

    # â”â”â”â”â”â”â”â”â”â” 4: DSN â”â”â”â”â”â”â”â”â”â”
    step("GET device information")
    print(f"  GET http://192.168.0.1/status.json")
    info = provisioner.fetch_device_info()
    if info.get("dsn"):
        print(f"  -> DSN: {info['dsn']}")
        print(f"  -> Model: {info.get('model','?')}")
        print(f"  -> MAC: {info.get('mac','?')}")
    else:
        print("  FAILED to get DSN")
        sys.exit(1)

    # â”â”â”â”â”â”â”â”â”â” 5: WiFi â”â”â”â”â”â”â”â”â”â”
    step("SEND Wi-Fi credentials (SECURE SETUP)")
    print(f"  POST http://192.168.0.1/local_reg.json  (initiates key exchange)")
    print(f"  Device calls: POST http://192.168.0.101:10275/local_lan/key_exchange.json")
    print(f"  Device calls: GET  http://192.168.0.101:10275/local_lan/commands.json")
    print(f"  We respond with encrypted WiFi connect command")
    print(f"  Target Wi-Fi: {home_ssid}")
    try:
        provisioner.send_credentials_secure(home_ssid, home_pwd)
        print(f"  -> Credentials sent via secure channel")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # â”â”â”â”â”â”â”â”â”â” 6: Cloud â”â”â”â”â”â”â”â”â”â”
    step("CONFIRM device on Ayla cloud")
    dsn = provisioner._dsn or device_ssid.split("-",2)[-1]
    setup_token = provisioner.setup_token
    if dsn and dsn != device_ssid.split("-",2)[-1]:
        print(f"  GET https://ads-eu.aylanetworks.com/apiv1/devices/connected.json?dsn={dsn}&setup_token={setup_token}")
        result = binder.confirm_device_connected(dsn, setup_token, timeout=20)
        if result:
            print(f"  -> Device confirmed on cloud!")
        else:
            print("  -> Not confirmed (may need more time)")
    else:
        print(f"  -> Skipped (no real DSN: {dsn})")

    # â”â”â”â”â”â”â”â”â”â” 7: Bind â”â”â”â”â”â”â”â”â”â”
    step("BIND device to account")
    if dsn and dsn != device_ssid.split("-",2)[-1]:
        print(f"  POST https://ads-eu.aylanetworks.com/apiv1/devices.json")
        for attempt in range(5):
            try:
                binder.bind_device(dsn, setup_token,
                    device_service_url=provisioner._device_service_url)
                print(f"  -> Device bound to your account!")
                break
            except RuntimeError:
                if attempt < 4: time.sleep(3)
                else: print("  -> Bind FAILED (API 404)")
    else:
        print("  -> Skipped (no real DSN)")

    # â”â”â”â”â”â”â”â”â”â” 8: Restore â”â”â”â”â”â”â”â”â”â”
    step("RESTORE Wi-Fi connection")
    print(f"  Connecting back to: {home_ssid}")
    provisioner.reconnect_to_home_wifi(home_ssid, home_pwd)
    print(f"  -> Reconnected")

    # â”â”â”â”â”â”â”â”â”â” 9: Cleanup â”â”â”â”â”â”â”â”â”â”
    wifi.cleanup_profiles()

    print(f"\n{SEP2}")
    print(f"  COMPLETE")
    print(f"  DSN: {dsn}")
    print(f"  Setup Token: {setup_token}")
    print(f"  Log: provision.log")
    print(SEP2)


if __name__ == "__main__":
    main()

