"""Windows WiFi control via netsh commands."""

import re
import subprocess
import time

from .config import DEVICE_SSID_PATTERN
from .log import get_logger

_log = get_logger("hismart.wifi")


class WindowsWiFi:
    """Manage WiFi connections on Windows using netsh."""

    @staticmethod
    def _run_netsh(args: list[str]) -> str:
        """Run a netsh command and return stdout."""
        result = subprocess.run(
            ["netsh", "wlan"] + args,
            capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )
        return result.stdout

    @staticmethod
    def get_current_ssid() -> str | None:
        """Get the SSID of the currently connected WiFi network, or None if on Ethernet."""
        output = WindowsWiFi._run_netsh(["show", "interfaces"])
        for line in output.splitlines():
            m = re.match(r"\s*SSID\s*:\s*(.+)", line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def get_interface_name() -> str | None:
        """Get the name of the WiFi interface."""
        output = WindowsWiFi._run_netsh(["show", "interfaces"])
        for line in output.splitlines():
            m = re.match(r"\s*Name\s*:\s*(.+)", line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def scan() -> list[dict]:
        """Scan for WiFi networks. Returns list of {ssid, bssid, signal, security}."""
        WindowsWiFi._run_netsh(["scan"])
        time.sleep(3)
        output = WindowsWiFi._run_netsh(["show", "networks", "mode=Bssid"])
        networks = []
        current_ssid = None
        current_bssid = None
        current_signal = None
        current_security = None

        for line in output.splitlines():
            m_ssid = re.match(r"^\s*SSID\s+\d+\s*:\s*(.+)", line, re.IGNORECASE)
            m_bssid = re.match(r"^\s*BSSID\s+\d+\s*:\s*(.+)", line, re.IGNORECASE)
            m_signal = re.match(r"^\s*Segnale\s*:\s*(\d+)%", line, re.IGNORECASE)

            if m_ssid:
                if current_ssid:
                    networks.append({
                        "ssid": current_ssid,
                        "bssid": current_bssid,
                        "signal": current_signal,
                        "security": current_security,
                    })
                current_ssid = m_ssid.group(1).strip()
                current_bssid = None
                current_signal = None
                current_security = None
            elif m_bssid:
                current_bssid = m_bssid.group(1).strip()
            elif m_signal:
                current_signal = int(m_signal.group(1))

        if current_ssid:
            networks.append({
                "ssid": current_ssid,
                "bssid": current_bssid,
                "signal": current_signal,
                "security": current_security,
            })
        return networks

    @staticmethod
    def find_device_ssids() -> list[dict]:
        """Scan and return only SSIDs matching the Hisense device pattern."""
        _log.info("Scanning WiFi networks...")
        all_networks = WindowsWiFi.scan()
        pattern = re.compile(DEVICE_SSID_PATTERN)
        devices = [n for n in all_networks if pattern.match(n["ssid"])]
        _log.info("Found %d total networks, %d Hisense device(s)", len(all_networks), len(devices))
        for d in devices:
            _log.info("  Device: %s (signal: %s%%)", d["ssid"], d.get("signal", "?"))
        return devices

    @staticmethod
    def connect(ssid: str, password: str | None = None, timeout: int = 20) -> bool:
        """Connect to a WiFi network. Timeout in seconds."""
        _log.info("Connecting to WiFi: %s (has password: %s, timeout: %ss)", ssid, password is not None, timeout)
        profile_name = f"opencode_temp_{ssid.replace(' ', '_')}"

        if password:
            profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{profile_name}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""
        else:
            profile_xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{profile_name}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>open</authentication>
                <encryption>none</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
        </security>
    </MSM>
</WLANProfile>"""

        import tempfile, os
        tmpdir = tempfile.gettempdir()
        profile_path = os.path.join(tmpdir, f"{profile_name}.xml")
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(profile_xml)
        try:
            result = subprocess.run(
                ["netsh", "wlan", "add", "profile", f"filename={profile_path}"],
                capture_output=True, text=True, timeout=10,
            )
            _log.debug("netsh add profile: %s", result.stdout.strip())
        finally:
            os.unlink(profile_path)

        subprocess.run(
            ["netsh", "wlan", "disconnect"],
            capture_output=True, text=True, timeout=10,
        )
        time.sleep(1)

        connect_result = subprocess.run(
            ["netsh", "wlan", "connect", f"name={profile_name}", f"ssid={ssid}"],
            capture_output=True, text=True, timeout=10,
        )
        _log.debug("netsh connect: %s", connect_result.stdout.strip())
        if connect_result.stderr.strip():
            _log.debug("netsh connect stderr: %s", connect_result.stderr.strip())

        deadline = time.time() + timeout
        while time.time() < deadline:
            current = WindowsWiFi.get_current_ssid()
            if current == ssid:
                _log.info("Successfully connected to %s", ssid)
                return True
            time.sleep(0.5)
        _log.error("Timeout connecting to %s", ssid)
        return False

    @staticmethod
    def disconnect(timeout: int = 10) -> None:
        """Disconnect from current WiFi network."""
        subprocess.run(
            ["netsh", "wlan", "disconnect"],
            capture_output=True, text=True, timeout=timeout,
        )
        time.sleep(2)

    @staticmethod
    def delete_profile(ssid: str) -> None:
        """Delete a saved WiFi profile."""
        profile_name = f"opencode_temp_{ssid.replace(' ', '_')}"
        subprocess.run(
            ["netsh", "wlan", "delete", "profile", f"name={profile_name}"],
            capture_output=True, text=True, timeout=10,
        )

    @staticmethod
    def get_gateway() -> str | None:
        """Get the default gateway of the current Wi-Fi connection via ipconfig."""
        result = subprocess.run(
            ["ipconfig"], capture_output=True, text=True, timeout=10,
        )
        output = result.stdout
        in_wifi = False
        for line in output.splitlines():
            if "Wireless LAN adapter" in line or "Wi-Fi" in line or "Wireless" in line:
                in_wifi = True
            elif in_wifi and ("Default Gateway" in line or "Gateway predefinito" in line):
                parts = line.split(":")
                if len(parts) >= 2:
                    gw = parts[-1].strip()
                    if gw and gw != "0.0.0.0":
                        return gw
            elif in_wifi and line.strip() == "":
                in_wifi = False
        return None

    @staticmethod
    def get_network_info() -> dict:
        """Return current network state: {ethernet: bool, wifi_ssid: str|None, wifi_band: str|None}."""
        result = subprocess.run(
            ["ipconfig"], capture_output=True, text=True, timeout=10,
        )
        output = result.stdout
        info = {"ethernet": False, "wifi_ssid": None, "wifi_gateway": None}

        current_adapter = None
        for line in output.splitlines():
            stripped = line.strip()
            # Match both English and Italian adapter names
            is_eth = ("Ethernet adapter" in line or "Scheda Ethernet" in line) and "Media disconnected" not in line and "Bluetooth" not in line
            is_wifi = ("Wireless LAN adapter" in line or "Wi-Fi" in line or "Scheda LAN wireless" in line) and "Media disconnected" not in line

            if is_eth:
                current_adapter = "eth"
            elif is_wifi:
                current_adapter = "wifi"
            elif current_adapter == "eth" and ("IPv4" in line or "Indirizzo IPv4" in line) and "Autoconfiguration" not in line and "Autoconfigurazione" not in line:
                info["ethernet"] = True
            elif current_adapter == "wifi" and ("Default Gateway" in line or "Gateway predefinito" in line):
                parts = line.split(":")
                if len(parts) >= 2:
                    gw = parts[-1].strip()
                    if gw and gw != "0.0.0.0":
                        info["wifi_gateway"] = gw

        info["wifi_ssid"] = WindowsWiFi.get_current_ssid()
        return info
