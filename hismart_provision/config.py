"""Constants extracted from the Hisense Hi Smart Life APK decompilation."""

AYLA_APP_ID = "Hisense-mw-id"
AYLA_APP_SECRET = "Hisense-wO1LLP8rWPr2cIeqvFaI-0m0z60"

AYLA_USER_BASE_URL = "https://user-field-eu.aylanetworks.com"
AYLA_DEVICE_BASE_URL = "https://ads-eu.aylanetworks.com"

DEVICE_HOTSPOT_IP = "192.168.0.1"
DEVICE_HOTSPOT_PORT = 10275
DEVICE_SSID_PATTERN = r"HiSmart-\d{2}-[0-9a-zA-Z]{12}"

SETUP_TIMEOUTS = {
    "scan": 30,
    "connect_device": 20,
    "send_password": 60,
    "confirm_connected": 60,
    "reconnect_wifi": 20,
    "reconnect_attempts": 5,
}

WIFI_SECURITY_TYPES = {
    0: "nopass",
    1: "WEP",
    2: "WPA",
    3: "WPA2",
}
