"""Test script to verify encryption matches AylaEncryption.java byte-for-byte."""
import base64
import hashlib
import hmac
import sys
sys.path.insert(0, "E:/dev/hismartlife/hismart_life_provision")

from hismart_provision.lan_secure import AylaEncryption, derive_session_keys, _hmac_sha256, _concat

# ── Use EXACT values from the last run ──────────────────────
enc = AylaEncryption()
enc.random_1 = "WA3kQMZx5EC6PuRA"
enc.time_1 = 14844944071781
enc.random_2 = "jhT0iuG7CSC8Upia"
enc.time_2 = 275617250000000

sec_b64 = "Fsp4cChS3efzB1PmTOc54OfkFO7Y3cBlUVy1KQaIalNV8HL51zWClclbhEE2bAYzQZ76aqGSX5iQMwewOUFghMj1IFWogOuZWnAvxcYPbBX4uwVvlm20PXtsLUadOq1gt73FZkY3LhvqI9alSOJZoE+K7Wor57fV5cn9KlRLPME="

# ── RSA decrypt (need private key from the run, can't reproduce without) ──
# Instead, use a dummy bLanKey for testing key derivation
b_lan_key = bytes(32)  # placeholder - would come from RSA decrypt
derive_session_keys(enc, b_lan_key)

print("=== Key derivation ===")
print(f"random_1: {enc.random_1}")
print(f"random_2: {enc.random_2}")
print(f"time_1: {enc.time_1}")
print(f"time_2: {enc.time_2}")
print(f"bLanKey hex: {b_lan_key.hex()}")
print(f"appSignKey: {enc.app_sign_key.hex()}")
print(f"appCryptoKey: {enc.app_crypto_key.hex()}")
print(f"appIvSeed: {enc.app_iv_seed.hex()}")
print(f"devCryptoKey: {enc.dev_crypto_key.hex()}")
print(f"devIvSeed: {enc.dev_iv_seed.hex()}")

# ── Encrypt a command ───────────────────────────────────────
cmd = {"id": 1, "method": "POST", "resource": "wifi_connect.json?ssid=FASTWEB-Z7EUHL&key=NGX7AS92SG&setup_token=TEST1234", "uri": "/local_lan/connect_status", "data": "none"}
import json
inner = json.dumps({"cmds": [{"cmd": cmd}]})
print(f"\n=== Command to encrypt ===")
print(f"Inner JSON: {inner}")
print(f"Inner length: {len(inner)} bytes")

payload = f'{{"seq_no":0,"data":{inner}}}'
data = payload.encode("utf-8")
print(f"\n=== Encrypting ===")
print(f"Payload: {payload[:200]}...")
print(f"Payload length: {len(data)} bytes")

# Our encryption
sign = base64.b64encode(_hmac_sha256(enc.app_sign_key, data)).decode()
java_length = len(data) + 1
pad_len = (16 - (java_length % 16)) % 16
total_len = java_length + pad_len
padded = data + b"\x00" * (total_len - len(data))

print(f"Sign: {sign}")
print(f"java_length={java_length}, pad_len={pad_len}, total_len={total_len}")
print(f"Padded length: {len(padded)} bytes")
print(f"Padded hex (last 32): ...{padded[-32:].hex()}")

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
cipher = Cipher(algorithms.AES(enc.app_crypto_key), modes.CBC(enc.app_iv_seed))
encryptor = cipher.encryptor()
encrypted = encryptor.update(padded) + encryptor.finalize()
enc_b64 = base64.b64encode(encrypted).decode()

result = f'{{"enc":"{enc_b64}","sign":"{sign}"}}'
print(f"\n=== Final encrypted command ===")
print(f"Length: {len(result)} bytes")
print(f"enc: {enc_b64[:80]}...")
print(f"Full: {result[:300]}")
