# HiSmart Life Provision

Python tool for provisioning Hisense smart devices (air conditioners, dehumidifiers, refrigerators) via SoftAP Wi-Fi provisioning -- **without the official app**.

Replicates the exact provisioning flow reverse-engineered from the Hi Smart Life Android APK (v1.0.12, package `com.hisense.hismartinternationalforandroid`).

## How it works

The Hisense Hi Smart Life app uses the **Ayla Networks IoT platform** (now part of Ayla). The provisioning flow is:

1. Device creates a Wi-Fi hotspot named `HiSmart-NN-XXXXXXXXXXXX`
2. Phone connects to the hotspot and exchanges data via HTTP (port 10275)
3. Phone sends home Wi-Fi credentials to the device
4. Device connects to home Wi-Fi and registers with the Ayla cloud
5. Device is bound to the user's Ayla account

This tool replicates all of these steps from a Windows PC using only Python stdlib + `netsh`.

## Prerequisites

- **Windows 10/11** with a Wi-Fi adapter
- **Python 3.11+** (any recent Python 3 works)
- A Hisense smart device with Wi-Fi module (supported: air conditioners, dehumidifiers, refrigerators, mobile AC units)

## Installation

```bash
git clone https://github.com/davidozz0/hismart_life_provision.git
cd hismart_life_provision
```

No pip dependencies required -- only Python standard library.

## Usage

```powershell
# Option 1: Double-click
provision.bat

# Option 2: Command line
python provision.py
```

The interactive wizard will guide you through:

1. Enter your Hisense account email/password
2. Enter your home Wi-Fi SSID and password
3. Put the device in pairing mode (see instructions below)
4. The script scans and connects to the device
5. Credentials are sent to the device
6. The device connects to your home Wi-Fi and binds to your account

### Putting devices in pairing mode

These instructions are extracted from the official Hisense app resources:

| Device type | Pairing method |
|---|---|
| **Air Conditioner** | Press **Horizon Airflow** button 6 times on the remote. Buzzer sounds 5 times, display shows **"77"**. OR press **Sleep** 8 times on wired controller. |
| **Portable AC** | Press **SWING** button 6 times on the remote. Buzzer sounds 5 times, display shows **"77"**. |
| **Dehumidifier** | Press **Mode + Fan** buttons together. Buzzer sounds 3 times, display shows **"P2"**. |
| **Refrigerator** | Hold the network/Wi-Fi button for 3 seconds. WiFi icon flashes on display panel. |

### Network requirements

Your PC should be connected via **Ethernet** while the Wi-Fi adapter is free to connect to the device hotspot. The script will temporarily switch the Wi-Fi adapter to the device, then back to your home network.

## Architecture

```
hismart_life_provision/
├── provision.py                  # Interactive CLI wizard
├── provision.bat                 # Double-click launcher
├── hismart_provision/
│   ├── __init__.py
│   ├── config.py                 # Ayla API endpoints, credentials, constants
│   ├── auth.py                   # Ayla cloud authentication (login, token management)
│   ├── wifi_win.py               # Windows Wi-Fi control (scan, connect, disconnect)
│   ├── provision.py (module)     # Device provisioning: SoftAP HTTP protocol
│   ├── bind.py                   # Device binding to Ayla account
│   └── log.py                    # Structured logging
```

## Reverse engineering notes

The provisioning protocol was reverse-engineered from the Hi Smart Life Android app by decompiling `com.hisense.hismartinternationalforandroid_1.0.12.xapk` with [jadx](https://github.com/skylot/jadx).

Key findings:
- **IoT platform:** Ayla Networks SDK v6.5.05
- **Cloud endpoints:** `user-field-eu.aylanetworks.com` (auth), `ads-eu.aylanetworks.com` (devices)
- **App credentials:** embedded in `BuildConfig.java` as app_id/app_secret
- **Device hotspot pattern:** `HiSmart-[0-9]{2}-[0-9a-zA-Z]{12}`
- **Default device IP:** `192.168.0.1`
- **Local protocol:** HTTP REST on port 10275, AES-256-CBC encrypted in secure mode
- **Device types:** `Smart-1` (AC), `Smart-2` (Fridge), `Smart-21` (Dehumidifier), `Smart-56` (Mobile AC)

The decompiled source code is available in the parent directory `../decompiled/src/`.

## Limitations

- **Windows only** -- Wi-Fi control uses `netsh wlan`. Linux/macOS would need `nmcli` or `wpa_cli`.
- **Non-secure mode only** -- Some devices may require AES key exchange (secure mode). If the device doesn't respond to direct HTTP, the protocol will need the RSA key exchange implementation.
- **Binding may fail** -- The Ayla API for device binding uses internal registration types. If the API rejects requests, manual binding via the app may still be needed after provisioning.

## License

MIT -- see source files.

## Disclaimer

This tool is for educational purposes and personal use with devices you own. It is not affiliated with or endorsed by Hisense or Ayla Networks.
