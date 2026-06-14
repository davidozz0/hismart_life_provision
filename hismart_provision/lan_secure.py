"""Secure LAN communication for Hisense device provisioning.

Implements the Ayla Networks secure setup protocol.
The device runs an HTTP server and connects to our local HTTP server.
Key exchange uses RSA-1024 + HMAC-SHA256 derived AES-256-CBC session keys.
"""

import base64
import hashlib
import hmac
import json
import os
import random
import string
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .log import get_logger

_log = get_logger("hismart.lan")


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
    return private_pem, public_pem


def _random_token(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _concat(*arrays: bytes) -> bytes:
    result = bytearray()
    for a in arrays:
        result.extend(a)
    return bytes(result)


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def derive_session_keys(enc: "AylaEncryption", b_lan_key: bytes) -> None:
    """Derive AES session keys from the shared secret using HMAC-SHA256.

    Mirrors AylaEncryption.generateSessionKeys(type="wifi_setup_rsa", ...)
    """
    rnd1 = enc.random_1.encode("utf-8")
    rnd2 = enc.random_2.encode("utf-8")
    time1 = str(enc.time_1).encode("utf-8")
    time2 = str(enc.time_2).encode("utf-8")

    enc.b_lan_key = b_lan_key

    # appSignKey
    last = bytes([48])
    concat1 = _concat(rnd1, rnd2, time1, time2, last)
    enc.app_sign_key = _hmac_sha256(b_lan_key, _concat(_hmac_sha256(b_lan_key, concat1), concat1))

    # appCryptoKey (AES-256 key)
    last = bytes([49])
    concat2 = _concat(rnd1, rnd2, time1, time2, last)
    enc.app_crypto_key = _hmac_sha256(b_lan_key, _concat(_hmac_sha256(b_lan_key, concat2), concat2))

    # appIvSeed (first 16 bytes)
    last = bytes([50])
    concat3 = _concat(rnd1, rnd2, time1, time2, last)
    app_iv_seed = _hmac_sha256(b_lan_key, _concat(_hmac_sha256(b_lan_key, concat3), concat3))
    enc.app_iv_seed = app_iv_seed[:16]

    # devCryptoKey (for decryption)
    last = bytes([49])
    concat4 = _concat(rnd2, rnd1, time2, time1, last)
    enc.dev_crypto_key = _hmac_sha256(b_lan_key, _concat(_hmac_sha256(b_lan_key, concat4), concat4))

    # devIvSeed
    last = bytes([50])
    concat5 = _concat(rnd2, rnd1, time2, time1, last)
    dev_iv_seed = _hmac_sha256(b_lan_key, _concat(_hmac_sha256(b_lan_key, concat5), concat5))
    enc.dev_iv_seed = dev_iv_seed[:16]

    _log.info("Session keys derived: app_crypto=%d dev_crypto=%d bytes",
              len(enc.app_crypto_key), len(enc.dev_crypto_key))


class AylaEncryption:
    """Session-specific encryption state matching AylaEncryption.java."""

    def __init__(self):
        self.version: int = 0
        self.proto: int = 0
        self.random_1: str = ""
        self.time_1: int = 0
        self.random_2: str = ""
        self.time_2: int = 0
        self.b_lan_key: bytes = b""
        self.app_sign_key: bytes = b""
        self.app_crypto_key: bytes = b""   # AES-256 key for encrypting commands
        self.app_iv_seed: bytes = b""       # IV for encryption
        self.dev_crypto_key: bytes = b""    # AES-256 key for decrypting device messages
        self.dev_iv_seed: bytes = b""       # IV for decryption
        self._seq_no: int = 0
        self._d_cipher: Cipher | None = None

    def encrypt_and_sign(self, plaintext: str) -> str:
        """Encrypt a command to send to device: {enc: base64, sign: base64}"""
        seq = self._seq_no
        self._seq_no += 1
        payload = f'{{"seq_no":{seq},"data":{plaintext}}}'
        data = payload.encode("utf-8")

        sign = base64.b64encode(_hmac_sha256(self.app_sign_key, data)).decode()

        # Pad to 16-byte boundary
        pad_len = (16 - (len(data) % 16)) % 16
        padded = data + b"\x00" * pad_len

        cipher = Cipher(algorithms.AES(self.app_crypto_key), modes.CBC(self.app_iv_seed))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        enc_b64 = base64.b64encode(encrypted).decode()

        return f'{{"enc":"{enc_b64}","sign":"{sign}"}}'

    def decrypt(self, enc_b64: str) -> str | None:
        """Decrypt an encrypted message from the device."""
        try:
            encrypted = base64.b64decode(enc_b64)
            cipher = Cipher(algorithms.AES(self.dev_crypto_key), modes.CBC(self.dev_iv_seed))
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(encrypted) + decryptor.finalize()
            # Remove zero padding
            decrypted = decrypted.rstrip(b"\x00")
            return decrypted.decode("utf-8")
        except Exception as e:
            _log.error("Decryption failed: %s", e)
            return None


class SecureLANServer:
    """HTTP server that the device connects to during secure setup."""

    def __init__(self, port: int = 10275):
        self._port = port
        self._private_key: rsa.RSAPrivateKey | None = None
        self._enc: AylaEncryption = AylaEncryption()
        self._server: HTTPServer | None = None
        self._commands: list[dict] = []
        self._command_responses: dict = {}
        self._cmd_id = 0
        self._key_exchange_done = threading.Event()
        self._dsn: str | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def dsn(self) -> str | None:
        return self._dsn

    def set_rsa_key(self, private_pem: bytes) -> None:
        self._private_key = serialization.load_pem_private_key(private_pem, password=None)

    def start(self) -> None:
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                _log.debug("DEV->PC %s %s", self.command, self.path)

            def _read_body(self) -> bytes:
                cl = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(cl) if cl > 0 else b""

            def do_POST(self):
                body = self._read_body()

                if self.path == "/local_lan/key_exchange.json":
                    resp_body = parent._handle_key_exchange(body)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "keep-alive")
                    resp_bytes = resp_body.encode("utf-8") if isinstance(resp_body, str) else resp_body
                    self.send_header("Content-Length", str(len(resp_bytes)))
                    self.end_headers()
                    self.wfile.write(resp_bytes)

                elif self.path == "/local_lan/status.json":
                    parent._handle_status(body)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"")

                elif self.path in ("/local_lan/wifi_scan_results.json",
                                   "/local_lan/wifi_status.json",
                                   "/local_lan/connect_status"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"")

                elif self.path in ("/local_lan/property/datapoint.json",
                                   "/local_lan/property/datapoint/ack.json",
                                   "/local_lan/node/property/datapoint.json",
                                   "/local_lan/node/conn_status.json",
                                   "/local_lan/node/property/datapoint/ack.json"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_GET(self):
                if self.path == "/local_lan/commands.json":
                    parent._handle_commands(self)
                elif self.path == "/local_lan/regtoken.json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"regtoken":""}')
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")

        self._server = HTTPServer(("0.0.0.0", self._port), _Handler)
        _log.info("Starting secure LAN server on port %d", self._port)
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
            _log.info("Secure LAN server stopped")

    def _handle_key_exchange(self, body: bytes) -> bytes:
        try:
            data = json.loads(body)
            ke = data.get("key_exchange", {})
            _log.info("Key exchange received: ver=%s proto=%s random_1=%s",
                      ke.get("ver"), ke.get("proto"), ke.get("random_1"))
            _log.debug("Full key exchange: %s", data)

            self._enc.version = ke.get("ver", 1)
            self._enc.proto = ke.get("proto", 1)
            self._enc.random_1 = ke.get("random_1", "")
            self._enc.time_1 = ke.get("time_1", 0)

            # Generate our side of the key
            self._enc.random_2 = _random_token(16)
            self._enc.time_2 = int(time.monotonic_ns())  # Match Java System.nanoTime()

            # RSA-decrypt the 'sec' field to get bLanKey
            sec_b64 = ke.get("sec", "")
            if sec_b64 and self._private_key:
                sec_encrypted = base64.b64decode(sec_b64)
                b_lan_key = self._private_key.decrypt(sec_encrypted, padding.PKCS1v15())
                _log.info("RSA decrypted bLanKey: %d bytes", len(b_lan_key))

                # Derive session keys
                derive_session_keys(self._enc, b_lan_key)
                _log.info("Session keys derived successfully")
            else:
                _log.warning("No sec field or no private key - keys not derived")

            # Build response with random_2 and time_2
            resp = json.dumps({
                "random_2": self._enc.random_2,
                "time_2": self._enc.time_2,
            })
            self._key_exchange_done.set()
            _log.info("Key exchange response: random_2=%s time_2=%d", self._enc.random_2, self._enc.time_2)
            return resp.encode("utf-8")

        except Exception as e:
            _log.error("Key exchange failed: %s", e)
            return b'{"error":"key exchange failed"}'

    def _handle_status(self, body: bytes) -> None:
        """Handle device status POST - extract DSN from encrypted payload."""
        if not body:
            return
        try:
            # Try to parse as JSON first (may be wrapped)
            data = json.loads(body)
            enc_str = data.get("enc", "")
            if enc_str and self._enc.dev_crypto_key:
                plain = self._enc.decrypt(enc_str)
                if plain:
                    _log.info("Decrypted status: %s", plain[:200])
                    # Parse inner JSON
                    inner = json.loads(plain)
                    # Look for data field which contains the device JSON as a string
                    inner_data = inner.get("data", "")
                    if inner_data:
                        dev_info = json.loads(inner_data) if isinstance(inner_data, str) else inner_data
                        if dev_info.get("dsn"):
                            self._dsn = dev_info["dsn"]
                            _log.info("Got DSN from device: %s", self._dsn)
                        elif dev_info.get("device", {}).get("dsn"):
                            self._dsn = dev_info["device"]["dsn"]
                            _log.info("Got DSN from device: %s", self._dsn)
        except Exception as e:
            _log.debug("Status parse error (ignorable): %s", e)

    def _handle_commands(self, handler) -> None:
        """Respond to device polling for commands."""
        if self._commands:
            cmd = self._commands.pop(0)
            inner = json.dumps({"cmds": [{"cmd": cmd}]})

            if self._enc.app_crypto_key:
                payload = self._enc.encrypt_and_sign(inner)
                _log.info("Sending encrypted command (id=%d)", cmd.get("id"))
            else:
                payload = inner
                _log.info("Sending plaintext command (id=%d)", cmd.get("id"))

            resp = payload.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(resp)))
            handler.end_headers()
            handler.wfile.write(resp)
        else:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", "2")
            handler.end_headers()
            handler.wfile.write(b"[]")

    def queue_connect_command(self, ssid: str, key: str, setup_token: str) -> int:
        """Queue a WiFi connect command. Returns command ID."""
        self._cmd_id += 1
        cmd = {
            "id": self._cmd_id,
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
        _log.info("Queued WiFi connect command (id=%d)", self._cmd_id)
        return self._cmd_id

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
        headers={"Content-Type": "application/json", "Accept": "application/json"},
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
