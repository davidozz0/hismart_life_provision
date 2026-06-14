#!/usr/bin/env python3
"""HiSmart Provision - Interactive Wi-Fi provisioning for Hisense smart devices.

Usage:
    python provision.py                            # interactive mode
    python provision.py --device HiSmart-01-xxxx   # auto-select device
    python provision.py --device HiSmart-01-xxxx --yes  # fully automatic
    python provision.py --clear                    # delete saved credentials
"""

import json
import os
import sys
import getpass
import time

from hismart_provision.auth import AylaAuth
from hismart_provision.wifi_win import WindowsWiFi
from hismart_provision.provision import DeviceProvisioner
from hismart_provision.bind import DeviceBinder
from hismart_provision.credentials import save, load, clear
from hismart_provision.log import get_logger

ARGS = {
    "device": None,
    "yes": False,
    "clear": False,
    "dsn": None,
}

for a in sys.argv[1:]:
    if a == "--yes":
        ARGS["yes"] = True
    elif a == "--clear":
        ARGS["clear"] = True
    elif a.startswith("--device="):
        ARGS["device"] = a.split("=", 1)[1]
    elif a == "--device":
        idx = sys.argv.index("--device")
        if idx + 1 < len(sys.argv):
            ARGS["device"] = sys.argv[idx + 1]
    elif a.startswith("--dsn="):
        ARGS["dsn"] = a.split("=", 1)[1]
    elif a == "--dsn":
        idx = sys.argv.index("--dsn")
        if idx + 1 < len(sys.argv):
            ARGS["dsn"] = sys.argv[idx + 1]

# File logging
LOG_FILE = os.path.join(os.path.dirname(__file__), "provision.log")
import logging
fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S"))
logging.getLogger("hismart").addHandler(fh)


def print_step(step: int, msg: str) -> None:
    print(f"\n  [{step}] {msg}")


def confirm(msg: str) -> bool:
    if ARGS["yes"]:
        return True
    answer = input(f"  {msg} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


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

    # ── Step 0: Check current network state ──────────────────
    net = wifi.get_network_info()
    print_step(0, "Network check")
    print(f"  Ethernet: {'connected' if net['ethernet'] else 'not detected'}")
    print(f"  Wi-Fi SSID: {net['wifi_ssid'] or 'not connected'}")
    if net['wifi_gateway']:
        print(f"  Wi-Fi gateway: {net['wifi_gateway']}")
    print()

    auth = AylaAuth()
    provisioner = DeviceProvisioner(wifi)
    binder = DeviceBinder(auth)

    # ── Step 1: Collect credentials ──────────────────────────
    print_step(1, "Account & Wi-Fi credentials")
    print()
    saved = load()
    email = password = home_ssid = home_pwd = ""

    if saved:
        print(f"  Using saved credentials: {saved['email']} / {saved['home_ssid']}")
        email = saved["email"]
        password = saved["password"]
        home_ssid = saved["home_ssid"]
        home_pwd = saved["home_pwd"]

    if not email:
        email = input("  Hisense account email: ").strip()
        password = getpass.getpass("  Hisense account password: ")
        home_ssid = input("  Home Wi-Fi SSID: ").strip()
        home_pwd = getpass.getpass("  Home Wi-Fi password: ")
        print()
        if confirm("Save credentials for next time? (password is obfuscated)"):
            save(email, password, home_ssid, home_pwd)
            print("  Saved.")

    # ── Step 2: Login to Ayla cloud ──────────────────────────
    print_step(2, "Logging in to Hisense cloud...")
    try:
        if not auth.login(email, password):
            print("  Login returned no token. Check credentials.")
            sys.exit(1)
        print(f"  Logged in. Token expires: {auth.expires_at}")
    except RuntimeError as e:
        print(f"  Login failed: {e}")
        sys.exit(1)

    # ── Step 3: Ensure device is in SoftAP mode ──────────────
    if not ARGS["yes"]:
        print_step(3, "Put your device in pairing/SoftAP mode")
        print()
        print("  AIR CONDITIONER:")
        print("    Press 'Horizon Airflow' button 6 times on the remote.")
        print("    Buzzer sounds 5 times. Display shows '77'.")
        print("    OR: press 'Sleep' button 8 times on wired controller.")
        print()
        print("  PORTABLE AC:")
        print("    Press 'SWING' button 6 times on the remote.")
        print("    Buzzer sounds 5 times. Display shows '77'.")
        print()
        print("  DEHUMIDIFIER:")
        print("    Press 'Mode' + 'Fan' together. Buzzer sounds 3 times.")
        print("    Display shows 'P2'.")
        print()
        print("  REFRIGERATOR:")
        print("    Hold network button 3 seconds. WiFi icon flashes.")
        print()
        input("  Press Enter when ready...")

    # ── Step 4: Scan for devices ─────────────────────────────
    print_step(4, "Scanning for Hisense devices...")
    print("  Scanning WiFi networks (this takes ~5 seconds)...")

    device_ssid = ARGS["device"]
    if device_ssid:
        print(f"  Looking for specified device: {device_ssid}")

    for attempt in range(3):
        devices = provisioner.scan_for_devices()
        if devices:
            break
        print(f"  No devices on attempt {attempt + 1}, retrying...")
        time.sleep(3)

    if not devices:
        print("  No Hisense devices found.")
        sys.exit(1)

    print(f"  Found {len(devices)} device(s):")
    for i, d in enumerate(devices):
        print(f"    [{i + 1}] {d['ssid']}  (signal: {d.get('signal', '?')}%)")

    if device_ssid:
        chosen = next((d for d in devices if d["ssid"] == device_ssid), None)
        if not chosen:
            print(f"  Specified device {device_ssid} not found!")
            sys.exit(1)
    elif len(devices) == 1:
        chosen = devices[0]
    else:
        if ARGS["yes"]:
            chosen = devices[0]
        else:
            choice = input(f"  Select device [1-{len(devices)}]: ").strip()
            try:
                chosen = devices[int(choice) - 1]
            except (ValueError, IndexError):
                print("  Invalid selection.")
                sys.exit(1)

    device_ssid = chosen["ssid"]
    print(f"  Selected: {device_ssid}")

    # ── Step 5: Connect PC to device hotspot ─────────────────
    print_step(5, f"Connecting PC to device {device_ssid}...")
    if not ARGS["yes"]:
        print("  NOTE: Your PC will temporarily disconnect from the internet.")
        if not confirm("Continue?"):
            sys.exit(0)

    if not provisioner.connect_to_device(device_ssid):
        print(f"  Failed to connect to {device_ssid}.")
        sys.exit(1)

    print(f"  Connected to {device_ssid}")
    time.sleep(3)

    # ── Step 6: Get device info ──────────────────────────────
    print_step(6, "Fetching device information...")
    try:
        info = provisioner.fetch_device_info()
        print(f"  Device info: {json.dumps(info, indent=2) if info else 'limited'}")
    except Exception as e:
        print(f"  Warning: could not fetch device info: {e}")

    # ── Step 7: Skip scan in auto mode ───────────────────────
    if not ARGS["yes"]:
        print_step(7, "Scanning for home WiFi networks (via device)...")
        provisioner.start_wifi_scan()
        time.sleep(5)
        results = provisioner.get_wifi_scan_results()
        if results:
            print(f"  Device sees {len(results)} networks:")
            for r in results[:15]:
                print(f"    {r['ssid']:30s}  signal={r.get('signal', '?')}")
    else:
        print_step(7, "Skipping WiFi scan (auto mode)")

    # ── Step 8: Send credentials ─────────────────────────────
    print_step(8, "Sending Wi-Fi credentials to device...")
    # Get DSN first (non-secure GET /status.json)
    # Then always use secure mode for WiFi credentials
    if not provisioner._dsn:
        provisioner.is_secure_mode()  # Try to get DSN

    # Always use secure protocol for sending credentials
    print("  Sending credentials via secure protocol...")
    try:
        ok = provisioner.send_credentials_secure(home_ssid, home_pwd)
        print("  Credentials sent!")
    except (RuntimeError, TimeoutError) as e:
        print(f"  Failed: {e}")
        sys.exit(1)

    # ── Step 9: Cloud confirmation ──────────────────────────
    dsn = provisioner.dsn if provisioner._dsn else device_ssid.split("-", 2)[-1]
    setup_token = provisioner.setup_token
    real_dsn = dsn and dsn != device_ssid.split("-", 2)[-1]

    if real_dsn:
        print_step(9, "Confirming device on Ayla cloud...")
        print(f"  DSN: {dsn}  Token: {setup_token}")

        result = binder.confirm_device_connected(dsn, setup_token, timeout=20)
        if result:
            print("  Device confirmed on cloud!")
        else:
            print("  Device did not confirm within timeout.")

        # ── Step 10: Bind device to account ──────────────────
        print_step(10, "Binding device to your account...")
        import time as _time
        for attempt in range(5):
            try:
                binder.bind_device(dsn, setup_token,
                                 device_service_url=provisioner._device_service_url)
                print("  Device bound!")
                break
            except RuntimeError as e:
                if attempt < 4:
                    print(f"  Bind attempt {attempt+1} failed, retrying...")
                    _time.sleep(3)
                else:
                    print(f"  Bind failed after 5 attempts: {e}")
    else:
        print_step(9, "Skipping cloud binding (no real DSN obtained)")
        print(f"  Got DSN: {dsn} -- not a real Ayla DSN, cannot bind")
        print(f"  Setup token: {setup_token}")
        print("  Device may still connect to WiFi. Check router client list.")

    # ── Step 11: Restore WiFi ────────────────────────────────
    print_step(11, "Restoring Wi-Fi connection...")
    if net["wifi_ssid"]:
        provisioner.reconnect_to_home_wifi(net["wifi_ssid"], home_pwd)
        print(f"  Reconnected to {net['wifi_ssid']}")
    elif home_ssid:
        provisioner.reconnect_to_home_wifi(home_ssid, home_pwd)
        print(f"  Reconnected to {home_ssid}")
    else:
        print("  No home Wi-Fi to restore (was on Ethernet)")

    # ── Done ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  DSN: {dsn}  Token: {setup_token}")
    print("  Log saved to: provision.log")
    print("=" * 60)


if __name__ == "__main__":
    main()
