"""Standard Webhooks v1 signing (WEBHOOKS_PLAN §3). The per-run callback secret is DERIVED
from the server secret (v1.1 §24): it is returned once at submit and never stored.
Owner: WEBHOOKS."""

import base64
import hashlib
import hmac


def callback_secret(server_key: bytes, run_id: str) -> str:
    mac = hmac.new(server_key, f"callback-secret:v1:{run_id}".encode(), hashlib.sha256).digest()
    return "whsec_" + base64.b64encode(mac).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(secret: str, msg_id: str, timestamp: int, body: bytes, header: str) -> bool:
    """Receiver side (used by the demo receiver and tests)."""
    expected = sign(secret, msg_id, timestamp, body)
    return any(hmac.compare_digest(expected, s) for s in header.split())
