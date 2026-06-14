"""Device provisioning: scan, connect to device hotspot, send WiFi credentials."""

import json
import re
import secrets
import socket
import string
import time
import urllib.error
import urllib.request

from .config import DEVICE_HOTSPOT_IP, DEVICE_SSID_PATTERN, SETUP_TIMEOUTS
from .log import get_logger
from .wifi_win import WindowsWiFi
from .lan_secure import SecureLANServer, generate_rsa_keypair, send_local_reg

_log = get_logger("hismart.provision")


def _random_token(length: int = 8) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class DeviceProvisioner:
    """Handle the SoftAP provisioning of a Hisense smart device."""

    def __init__(self, wifi: WindowsWiFi):
        self._wifi = wifi
        self._dsn: str | None = None
        self._setup_token: str | None = None
        self._device_ssid: str | None = None
        self._device_ip: str = DEVICE_HOTSPOT_IP

    @property
    def dsn(self) -> str:
        if not self._dsn:
            raise RuntimeError("DSN not available. Connect to device first.")
        return self._dsn

    @property
    def device_ip(self) -> str:
        return self._device_ip

    @property
    def setup_token(self) -> str:
        if not self._setup_token:
            raise RuntimeError("Setup token not available. Send credentials first.")
        return self._setup_token

    @property
    def device_ssid(self) -> str:
        if not self._device_ssid:
            raise RuntimeError("Device SSID not selected.")
        return self._device_ssid

    def scan_for_devices(self) -> list[dict]:
        """Scan WiFi and return list of Hisense device SSIDs found."""
        devices = self._wifi.find_device_ssids()
        return devices

    def connect_to_device(self, ssid: str) -> bool:
        """Connect the PC to the device's SoftAP hotspot."""
        self._device_ssid = ssid
        timeout = SETUP_TIMEOUTS["connect_device"]
        _log.info("Connecting to device hotspot: %s (timeout=%ss)", ssid, timeout)
        ok = self._wifi.connect(ssid, password=None, timeout=timeout)
        if ok:
            _log.info("Connected to device %s", ssid)
            time.sleep(3)
            gw = self._wifi.get_gateway()
            if gw:
                self._device_ip = gw
                _log.info("Device gateway IP: %s", gw)
            else:
                _log.warning("Could not detect gateway IP, using default %s", DEVICE_HOTSPOT_IP)
        else:
            _log.error("Failed to connect to device %s", ssid)
        return ok

    def fetch_device_info(self) -> dict:
        """Fetch device status/info from the device's HTTP API (non-secure path)."""
        url = f"http://{self._device_ip}/local_lan/status.json"
        _log.info("Fetching device info: %s", url)
        try:
            data = self._device_http_post(url, {})
            _log.debug("Device info response: %s", data)
            if "dsn" in data:
                self._dsn = data["dsn"]
                _log.info("Device DSN: %s", self._dsn)
            return data
        except Exception as e:
            _log.warning("Could not fetch device info: %s", e)
            return {}

    def start_wifi_scan(self) -> bool:
        """Command the device to start scanning for WiFi networks."""
        url = f"http://{self._device_ip}/local_lan/wifi_scan.json"
        _log.info("Starting WiFi scan on device: %s", url)
        try:
            self._device_http_post(url, {})
            return True
        except Exception as e:
            _log.warning("WiFi scan command failed: %s", e)
            return False

    def get_wifi_scan_results(self) -> list[dict]:
        """Fetch WiFi scan results from the device, filtering out other Hisense devices."""
        url = f"http://{self._device_ip}/local_lan/wifi_scan_results.json"
        _log.info("Fetching WiFi scan results: %s", url)
        try:
            data = self._device_http_get(url)
            results = data.get("wifi_scan", {}).get("results", [])
            _log.info("Device found %d WiFi networks", len(results))
            pattern = re.compile(DEVICE_SSID_PATTERN)
            filtered = [r for r in results if not pattern.match(r.get("ssid", ""))]
            _log.info("After filtering HiSmart APs: %d networks", len(filtered))
            return filtered
        except Exception as e:
            _log.warning("Could not fetch scan results: %s", e)
            return []

    def send_credentials(self, ssid: str, password: str) -> bool:
        """Send home WiFi credentials to the device and wait for connection."""
        self._setup_token = _random_token(8)

        connect_url = f"http://{self._device_ip}/local_lan/connect_status"
        connect_body = {
            "ssid": ssid,
            "key": password,
            "setup_token": self._setup_token,
        }

        _log.info("Sending credentials to device: %s", connect_url)
        _log.debug("Connect body: ssid=%s setup_token=%s key=***", ssid, self._setup_token)
        self._device_http_post(connect_url, connect_body)

        deadline = time.time() + SETUP_TIMEOUTS["send_password"]
        status_url = f"http://{self._device_ip}/local_lan/wifi_status.json"
        last_state = ""

        _log.info("Polling device WiFi status (timeout=%ss)...", SETUP_TIMEOUTS["send_password"])
        while time.time() < deadline:
            time.sleep(2)
            try:
                data = self._device_http_get(status_url)
                state = data.get("wifi_status", {}).get("state", "")

                if state != last_state:
                    _log.info("Device WiFi state: %s -> %s", last_state or "?", state)
                    last_state = state

                if state == "up":
                    _log.info("Device connected to WiFi!")
                    return True

                history = data.get("wifi_status", {}).get("history", [])
                if history:
                    last_item = history[-1]
                    error_code = last_item.get("error", 0)
                    if error_code != 0 and error_code != 20:
                        error_names = {
                            3: "Invalid key (wrong password)",
                            4: "SSID not found",
                            6: "Incorrect key",
                            7: "DHCP error - no IP assigned",
                        }
                        msg = error_names.get(error_code, f"Error code {error_code}")
                        _log.error("Device WiFi error: %s", msg)
                        raise RuntimeError(f"Device reported error: {msg}")
            except RuntimeError:
                raise
            except Exception as e:
                _log.debug("Status poll error (ignorable): %s", e)

        _log.error("Device did not connect within %ss. Last state: %s", SETUP_TIMEOUTS["send_password"], last_state)
        raise TimeoutError(f"Device did not connect within {SETUP_TIMEOUTS['send_password']}s. Last state: {last_state}")

    def stop_ap_mode(self) -> None:
        """Command the device to stop its AP mode (optional)."""
        url = f"http://{self._device_ip}/local_lan/wifi_stop_ap.json"
        try:
            self._device_http_post(url, {})
        except Exception:
            pass

    def disconnect_from_device(self) -> None:
        """Disconnect PC from device hotspot."""
        self._wifi.disconnect()
        self._wifi.delete_profile(self.device_ssid)
        time.sleep(2)

    def reconnect_to_home_wifi(self, ssid: str, password: str) -> bool:
        """Reconnect PC to the home WiFi network."""
        self.disconnect_from_device()
        return self._wifi.connect(ssid, password, timeout=SETUP_TIMEOUTS["reconnect_wifi"])

    def _device_http_get(self, url: str, timeout: int = 10) -> dict:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError:
            raise
        except json.JSONDecodeError:
            return {}

    def _device_http_post(self, url: str, data: dict, timeout: int = 10) -> dict:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError:
            raise
        except json.JSONDecodeError:
            return {}


    def is_secure_mode(self) -> bool:
        """Check if the device requires secure (RSA+AES) setup."""
        url = f"http://{self._device_ip}/local_lan/status.json"
        try:
            req = urllib.request.Request(url, method="POST", data=b"{}",
                headers={"Content-Type": "application/json", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return False
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _log.info("Device returned 404 -> secure mode required")
                return True
            return False
        except Exception:
            return False

    def send_credentials_secure(self, ssid: str, password: str) -> bool:
        """Send WiFi credentials using the secure RSA+AES protocol."""
        self._setup_token = _random_token(8)

        private_pem, public_pem = generate_rsa_keypair()

        phone_ip = _get_own_ip(self._device_ip)
        _log.info("Phone IP on device network: %s", phone_ip)

        server = SecureLANServer()
        server.set_rsa_key(private_pem)
        server.start()

        # Phase 1: just queue WiFi connect (device pushes status on its own)
        server.queue_connect_command(ssid, password, self._setup_token)

        ok = send_local_reg(self._device_ip, phone_ip, server.port, public_pem)
        if not ok:
            server.stop()
            return False

        _log.info("Waiting for key exchange from device (30s)...")
        if not server.wait_for_key_exchange(30):
            _log.error("Key exchange timed out")
            server.stop()
            return False

        _log.info("Key exchange complete. Waiting for device to report DSN...")
        for _ in range(5):
            time.sleep(1)
            if server.dsn:
                self._dsn = server.dsn
                _log.info("Got DSN from device: %s", self._dsn)
                break

        if not self._dsn:
            suffix = self._device_ssid.split("-", 2)[-1] if self._device_ssid else "unknown"
            self._dsn = suffix
            _log.info("DSN not from device, using SSID suffix: %s", self._dsn)

        server.stop()
        _log.info("Secure setup complete")
        return True

def _get_own_ip(target_ip: str) -> str:
    """Determine our own IP on the device's network by binding a UDP socket."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect((target_ip, 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.0.100"
