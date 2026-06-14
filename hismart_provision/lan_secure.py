"""Secure LAN communication for Hisense device provisioning.

Implements the Ayla Networks secure setup protocol:
1. Start an HTTP server on port 10275 (the device connects to us)
2. Generate RSA-1024 key pair
3. POST local_reg to device with our IP/port/public key
4. Handle key exchange: device encrypts AES keys with our RSA public key
5. Queue commands that the device polls and executes
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .log import get_logger

_log = get_logger("hismart.lan")

AES_KEY_LEN = 32  # AES-256
IV_LEN = 16


def generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Generate RSA-1024 key pair. Returns (private_pem, public_pem)."""
    _log.info("Generating RSA-1024 key pair...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    _log.info("RSA key pair generated")
    return private_pem, public_pem


class SecureLANServer:
    """HTTP server that the device connects to during secure setup."""

    def __init__(self, port: int = 10275):
        self._port = port
        self._private_key: rsa.RSAPrivateKey | None = None
        self._session_key: bytes | None = None
        self._iv: bytes | None = None
        self._server: HTTPServer | None = None
        self._commands: list[dict] = []
        self._command_responses: dict[int, dict] = {}
        self._command_counter = 0
        self._key_exchange_done = threading.Event()
        self._command_done = threading.Event()

    @property
    def port(self) -> int:
        return self._port

    @property
    def session_key(self) -> bytes | None:
        return self._session_key

    @property
    def iv(self) -> bytes | None:
        return self._iv

    def set_rsa_key(self, private_pem: bytes) -> None:
        self._private_key = serialization.load_pem_private_key(
            private_pem, password=None,
        )

    def start(self) -> None:
        """Start the HTTP server in a background thread."""
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                _log.debug("DEV->PC %s %s", self.command, self.path)

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len else b""

                if self.path == "/local_lan/key_exchange.json":
                    parent._handle_key_exchange(body)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')

                elif self.path == "/local_lan/status.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"state":"up"}')

                elif self.path == "/local_lan/property/datapoint.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{}')

                elif self.path == "/local_lan/property/datapoint/ack.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{}')

                elif self.path == "/local_lan/node/property/datapoint.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{}')

                elif self.path == "/local_lan/node/conn_status.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{}')

                elif self.path == "/local_lan/connect_status":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{}')

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_GET(self):
                if self.path == "/local_lan/commands.json":
                    parent._handle_commands_get(self)

                elif self.path == "/local_lan/wifi_status.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"wifi_status":{"state":"up"}}')

                elif self.path == "/local_lan/regtoken.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"regtoken":""}')

                elif self.path == "/local_lan/wifi_scan_results.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"wifi_scan":{"results":[]}}')

                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = HTTPServer(("0.0.0.0", self._port), _Handler)
        _log.info("Starting secure LAN server on port %d", self._port)
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
            _log.info("Secure LAN server stopped")

    def _handle_key_exchange(self, body: bytes) -> None:
        try:
            data = json.loads(body)
            _log.info("Key exchange received from device")
            _log.debug("Key exchange body: %s", data)

            encrypted_key_b64 = data.get("key", "")
            encrypted_iv_b64 = data.get("iv", "")

            if self._private_key and encrypted_key_b64:
                encrypted_key = base64.b64decode(encrypted_key_b64)
                session_key = self._private_key.decrypt(
                    encrypted_key,
                    padding.PKCS1v15(),
                )
                self._session_key = session_key
                _log.info("AES session key decrypted (%d bytes)", len(session_key))

            if self._private_key and encrypted_iv_b64:
                encrypted_iv = base64.b64decode(encrypted_iv_b64)
                iv = self._private_key.decrypt(
                    encrypted_iv,
                    padding.PKCS1v15(),
                )
                self._iv = iv
                _log.info("AES IV decrypted (%d bytes)", len(iv))

            self._key_exchange_done.set()
        except Exception as e:
            _log.error("Key exchange failed: %s", e)

    def _handle_commands_get(self, handler) -> None:
        if self._commands:
            cmd = self._commands.pop(0)
            body = json.dumps({"cmds": [{"cmd": cmd}]}).encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        else:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", "2")
            handler.end_headers()
            handler.wfile.write(b"[]")

    def queue_connect_command(self, ssid: str, key: str, setup_token: str) -> None:
        """Queue a WiFi connect command for the device."""
        self._command_counter += 1
        cmd_id = self._command_counter
        cmd = {
            "id": cmd_id,
            "method": "POST",
            "resource": "wifi_connect.json",
            "uri": "/local_lan/connect_status",
            "data": json.dumps({
                "ssid": ssid,
                "key": key,
                "setup_token": setup_token,
            }),
        }
        self._commands.append(cmd)
        _log.info("Queued WiFi connect command (id=%d)", cmd_id)

    def wait_for_key_exchange(self, timeout: int = 30) -> bool:
        return self._key_exchange_done.wait(timeout)


def send_local_reg(device_ip: str, phone_ip: str, port: int, public_key_pem: bytes) -> bool:
    """Send local_reg to the device to initiate LAN communication."""
    public_key_der = serialization.load_pem_public_key(public_key_pem).public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_b64 = base64.b64encode(public_key_der).decode()

    body = {
        "local_reg": {
            "ip": phone_ip,
            "port": port,
            "uri": "/local_lan",
            "notify": 0,
            "key": public_key_b64,
        },
    }

    url = f"http://{device_ip}/local_reg.json"
    _log.info("Sending local_reg to device: %s", url)
    _log.debug("local_reg body: ip=%s port=%d", phone_ip, port)

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _log.info("local_reg accepted (HTTP %d)", resp.status)
            return True
    except urllib.error.HTTPError as e:
        _log.warning("local_reg HTTP %d: %s", e.code, e.read().decode("utf-8", errors="replace")[:200])
        return False
    except urllib.error.URLError as e:
        _log.error("local_reg failed: %s", e.reason)
        return False
