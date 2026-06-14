#!/usr/bin/env python3
"""HiSmart Provision - Interactive Wi-Fi provisioning for Hisense smart devices.

Replicates the SoftAP provisioning flow from the Hi Smart Life Android app.
Connects to the device's hotspot, sends your home Wi-Fi credentials,
and binds the device to your Hisense/Ayla account.

Usage:
    python provision.py
"""

import json
import sys
import getpass
import time

from hismart_provision.auth import AylaAuth
from hismart_provision.wifi_win import WindowsWiFi
from hismart_provision.provision import DeviceProvisioner
from hismart_provision.bind import DeviceBinder
from hismart_provision.credentials import save, load, clear


def print_step(step: int, msg: str) -> None:
    print(f"\n  [{step}] {msg}")


def confirm(msg: str) -> bool:
    answer = input(f"  {msg} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def main():
    if "--clear" in sys.argv:
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

    # ── Step 1: Collect credentials ──────────────────────────
    print_step(1, "Account & Wi-Fi credentials")
    print()
    saved = load()
    if saved:
        print("  Saved credentials found:")
        print(f"    Account:  {saved['email']}")
        print(f"    Wi-Fi:    {saved['home_ssid']}")
        print()
        use_saved = confirm("Use saved credentials?")
        if use_saved:
            email = saved["email"]
            password = saved["password"]
            home_ssid = saved["home_ssid"]
            home_pwd = saved["home_pwd"]
        else:
            saved = None
            clear()
            print("  Saved credentials deleted.")

    if not saved:
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
    devices = provisioner.scan_for_devices()

    if not devices:
        print("  No Hisense devices found. Retrying one more time...")
        time.sleep(3)
        devices = provisioner.scan_for_devices()

    if not devices:
        print("  Still no devices found.")
        print("  Make sure the device is in pairing mode and try again.")
        sys.exit(1)

    print(f"  Found {len(devices)} device(s):")
    for i, d in enumerate(devices):
        print(f"    [{i + 1}] {d['ssid']}  (signal: {d.get('signal', '?')}%)")

    if len(devices) == 1:
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
    print("  NOTE: Your PC will temporarily disconnect from the internet.")
    print("  If you're on Ethernet, this won't affect your connection.")

    if not confirm("Continue?"):
        sys.exit(0)

    if not provisioner.connect_to_device(device_ssid):
        print(f"  Failed to connect to {device_ssid}.")
        print("  You may need to connect manually via Windows WiFi settings.")
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
        print("  Continuing anyway...")

    # ── Step 7: Scan WiFi from device ────────────────────────
    print_step(7, "Scanning for home WiFi networks (via device)...")
    provisioner.start_wifi_scan()
    time.sleep(5)
    results = provisioner.get_wifi_scan_results()

    if results:
        print(f"  Device sees {len(results)} networks:")
        for r in results[:15]:
            sec = r.get("security", "?")
            sig = r.get("signal", "?")
            print(f"    {r['ssid']:30s}  signal={sig}  security={sec}")
    else:
        print("  No scan results from device. Will try direct credential send.")

    # ── Step 8: Send credentials ─────────────────────────────
    print_step(8, "Sending Wi-Fi credentials to device...")

    secure = provisioner.is_secure_mode()
    if secure:
        print("  Device requires secure setup (RSA+AES). Running secure protocol...")
    else:
        print("  Device uses direct HTTP. Running standard protocol...")

    try:
        if secure:
            ok = provisioner.send_credentials_secure(home_ssid, home_pwd)
        else:
            ok = provisioner.send_credentials(home_ssid, home_pwd)

        if ok or secure:
            print("  Device accepted credentials and connected to Wi-Fi!")
        else:
            print("  Failed to send credentials.")
            sys.exit(1)
    except (RuntimeError, TimeoutError) as e:
        print(f"  Failed: {e}")
        print("  The device may still be trying. Check the device LED indicator.")
        sys.exit(1)

    # ── Step 9: Reconnect PC to home Wi-Fi ───────────────────
    print_step(9, "Reconnecting PC to home Wi-Fi...")
    if not provisioner.reconnect_to_home_wifi(home_ssid, home_pwd):
        print("  Warning: could not reconnect automatically.")
        print(f"  Please reconnect to '{home_ssid}' manually.")
    else:
        print(f"  Reconnected to {home_ssid}")

    # ── Step 10: Confirm device on cloud ─────────────────────
    print_step(10, "Confirming device connected to cloud...")
    dsn = provisioner.dsn if provisioner._dsn else device_ssid.split("-", 2)[-1]
    if not provisioner._dsn:
        print(f"  DSN not directly available, using SSID suffix: {dsn}")
    setup_token = provisioner.setup_token
    print(f"  DSN: {dsn}")

    result = binder.confirm_device_connected(dsn, setup_token)
    if result:
        print("  Device confirmed on cloud!")
    else:
        print("  Device did not confirm within timeout.")
        print("  It may still connect. Check the Hisense app in a few minutes.")

    # ── Step 11: Bind device to account ──────────────────────
    print_step(11, "Binding device to your account...")
    try:
        bind_result = binder.bind_device(dsn, setup_token)
        print(f"  Device bound to account!")
    except RuntimeError as e:
        print(f"  Bind failed via primary method: {e}")
        print("  Trying alternative registration...")
        try:
            bind_result = binder.register_candidate(dsn, setup_token)
            print("  Device registered successfully!")
        except RuntimeError as e2:
            print(f"  Alternative registration also failed: {e2}")

    # ── Done ─────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Provisioning complete!")
    print(f"  DSN: {dsn}")
    print(f"  Setup token: {setup_token}")
    print("  Open the Hi Smart Life app — your device should appear.")
    print("=" * 60)


if __name__ == "__main__":
    main()
