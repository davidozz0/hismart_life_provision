"""Bind a provisioned device to the user's Ayla/HiSense account."""

import json
import time
import urllib.error
import urllib.request

from .auth import AylaAuth
from .config import AYLA_DEVICE_BASE_URL
from .log import get_logger

_log = get_logger("hismart.bind")


class DeviceBinder:
    """Bind a provisioned device to an Ayla cloud account."""

    def __init__(self, auth: AylaAuth):
        self._auth = auth

    def confirm_device_connected(self, dsn: str, setup_token: str, timeout: int = 60) -> dict | None:
        """Poll the Ayla cloud until the device appears as connected.

        Uses the Device service to check if the device has connected to the cloud.
        """
        url = f"{AYLA_DEVICE_BASE_URL}/apiv1/devices/connected.json?dsn={dsn}&setup_token={setup_token}"
        _log.info("Polling device connection: %s", url)
        _log.info("  DSN: %s, timeout: %ss", dsn, timeout)

        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                data = self._auth.api_get(url)
                _log.info("Device confirmed on cloud! (attempt %d)", attempt)
                return data
            except RuntimeError as e:
                _log.debug("Poll attempt %d: not yet (%s)", attempt, e)
                time.sleep(2)
        _log.error("Device did not confirm within %ss", timeout)
        return None

    def bind_device(self, dsn: str, setup_token: str, device_name: str = "") -> dict:
        """Register/bind the device to the user's account.

        Registration type is APMode (4) since we used SoftAP provisioning.
        """
        _log.info("Binding device: dsn=%s name=%s", dsn, device_name or dsn)
        reg_body = {
            "registration": {
                "dsn": dsn,
                "setup_token": setup_token,
                "reg_type": 4,
                "device": {
                    "product_name": device_name or dsn,
                },
            },
        }
        url = f"{AYLA_DEVICE_BASE_URL}/apiv1/devices.json"
        result = self._auth.api_post(url, reg_body, base_url=AYLA_DEVICE_BASE_URL)
        _log.info("Device bound successfully!")
        return result

    def register_candidate(self, dsn: str, setup_token: str,
                           latitude: str = "0.0", longitude: str = "0.0") -> dict:
        """Alternative: use the registration candidate API."""
        _log.info("Binding via registerCandidate: dsn=%s", dsn)
        data = {
            "registration": {
                "dsn": dsn,
                "setup_token": setup_token,
                "reg_type": "APMode",
                "latitude": latitude,
                "longitude": longitude,
            },
        }
        url = f"{AYLA_DEVICE_BASE_URL}/apiv1/devices/register.json?regtype=APMode"
        result = self._auth.api_post(url, data, base_url=AYLA_DEVICE_BASE_URL)
        _log.info("Device registered successfully!")
        return result
