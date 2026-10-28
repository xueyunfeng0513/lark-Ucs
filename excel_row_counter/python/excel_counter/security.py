import base64
import hashlib
import hmac
import json
import time

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


class AuthenticationError(Exception):
    pass


def _same(value, expected):
    return isinstance(value, str) and hmac.compare_digest(value.encode(), expected.encode())


def decode_event(raw, headers, settings):
    """Verify Feishu v2 signature against raw bytes; decrypt AES-CBC callbacks."""
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Expected an object")
    if "encrypt" in payload:
        try:
            encrypted = base64.b64decode(payload["encrypt"], validate=True)
            cipher = AES.new(hashlib.sha256(settings.encrypt_key.encode()).digest(),
                             AES.MODE_CBC, encrypted[:16])
            payload = json.loads(unpad(cipher.decrypt(encrypted[16:]), 16))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthenticationError from exc

    # URL registration has its own token-protected challenge contract.
    if payload.get("type") == "url_verification":
        if not _same(payload.get("token"), settings.verification_token):
            raise AuthenticationError
        return payload

    timestamp = headers.get("X-Lark-Request-Timestamp", "")
    nonce = headers.get("X-Lark-Request-Nonce", "")
    signature = headers.get("X-Lark-Signature", "")
    try:
        fresh = abs(time.time() - int(timestamp)) <= 300
    except ValueError:
        fresh = False
    expected = hashlib.sha256((timestamp + nonce + settings.encrypt_key).encode() + raw).hexdigest()
    if not fresh or not nonce or not _same(signature, expected):
        raise AuthenticationError
    header = payload.get("header")
    if not isinstance(header, dict):
        raise ValueError("Missing event header")
    if not _same(header.get("token"), settings.verification_token) or header.get("app_id") != settings.app_id:
        raise AuthenticationError
    return payload
