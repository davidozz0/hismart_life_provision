"""Secure credential storage for HiSmart Provision.

Stores email, account password, Wi-Fi SSID, and Wi-Fi password
in an obfuscated JSON file in the user's home directory.
"""

import base64
import json
import os
import secrets
import socket
import sys


_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".hismart_provision")
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")


def _machine_key() -> bytes:
    """Derive a key from the machine hostname."""
    host = socket.gethostname().encode("utf-8")
    key = bytearray(32)
    for i, b in enumerate(host * (32 // len(host) + 1)):
        key[i % 32] ^= b
    key[0] = (key[0] + 0xAB) & 0xFF
    return bytes(key)


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(d ^ key[i % len(key)] for i, d in enumerate(data))


def _obfuscate(plain: str) -> str:
    return base64.b64encode(_xor(plain.encode("utf-8"), _machine_key())).decode()


def _deobfuscate(cipher: str) -> str:
    return _xor(base64.b64decode(cipher), _machine_key()).decode("utf-8")


def save(email: str, password: str, home_ssid: str, home_pwd: str) -> None:
    """Save credentials to disk (obfuscated)."""
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    data = {
        "email": email,
        "password": _obfuscate(password),
        "home_ssid": home_ssid,
        "home_pwd": _obfuscate(home_pwd),
    }
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load() -> dict | None:
    """Load credentials from disk. Returns None if not saved."""
    if not os.path.isfile(_CONFIG_FILE):
        return None
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "email": data.get("email", ""),
            "password": _deobfuscate(data.get("password", "")),
            "home_ssid": data.get("home_ssid", ""),
            "home_pwd": _deobfuscate(data.get("home_pwd", "")),
        }
    except Exception:
        return None


def clear() -> None:
    """Remove saved credentials."""
    if os.path.isfile(_CONFIG_FILE):
        os.remove(_CONFIG_FILE)
