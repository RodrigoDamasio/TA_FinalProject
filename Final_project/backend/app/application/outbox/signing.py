"""Standard Webhooks v1 signing (WEBHOOKS_PLAN §3.5). The per-run callback secret is DERIVED
from the server secret (v1.1 §24): it is returned once at submit and never stored.
Owner: WEBHOOKS."""

import base64
import hashlib
import hmac

USER_AGENT = "TA-Research-Webhooks/1"


def callback_secret(server_key: bytes, run_id: str) -> str:
    """`whsec_` + base64 of HMAC-SHA256(server key, "callback-secret:v1:" + run id): 32 bytes."""
    mac = hmac.new(server_key, f"callback-secret:v1:{run_id}".encode(), hashlib.sha256).digest()
    return "whsec_" + base64.b64encode(mac).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def signed_headers(secret: str, msg_id: str, timestamp: int, body: bytes) -> dict[str, str]:
    """The headers of ONE attempt: the id is stable across retries, the timestamp is not."""
    return {
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, msg_id, timestamp, body),
    }


def verify(secret: str, msg_id: str, timestamp: int, body: bytes, header: str) -> bool:
    """Receiver side (used by the demo receiver and tests). The header may hold several
    space-separated signatures (key rotation); one valid `v1,` signature is enough."""
    expected = sign(secret, msg_id, timestamp, body)
    return any(hmac.compare_digest(expected, s) for s in header.split() if s.startswith("v1,"))
