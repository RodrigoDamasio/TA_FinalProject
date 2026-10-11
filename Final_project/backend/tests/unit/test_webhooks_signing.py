"""Standard Webhooks signing, secret derivation and the retry schedule (WEBHOOKS_PLAN §3.5,
§3.7, §9 unit)."""

import base64
from datetime import UTC, datetime

import pytest

from app.application.outbox.schedule import retry_at, retry_delay_s
from app.application.outbox.signing import callback_secret, sign, signed_headers, verify

SECRET_2 = "whsec_" + base64.b64encode(bytes(range(32))).decode()
BODY_2 = (
    b'{"type":"research.completed","timestamp":"2026-10-11T12:00:00Z",'
    b'"data":{"run_id":"run_0123456789ab","status":"completed"}}'
)
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("secret", "msg_id", "ts", "body", "signature"),
    [
        (
            "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
            "msg_p5jXN8AQM9LWM0D4loKWxJek",
            1614265330,
            b'{"test": 2432232314}',
            "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
        ),
        (
            SECRET_2,
            "msg_000000000000000000000001",
            1791720000,
            BODY_2,
            "v1,jVbe0on+79nIe8ZWxBp4QWi2OKsbxB0iFkVFI4yL/bo=",
        ),
        (
            SECRET_2,
            "msg_000000000000000000000001",
            1791720001,
            BODY_2,
            "v1,BZGp8+cX4YxFZvfS0PD+m3onszoDvRkQMyD6LM7COAI=",
        ),
    ],
)
def test_signature_vectors(secret, msg_id, ts, body, signature):
    assert sign(secret, msg_id, ts, body) == signature
    assert verify(secret, msg_id, ts, body, signature)
    assert verify(secret, msg_id, ts, body, f"v1,bm90LWl0 {signature}")  # rotation: any match
    assert not verify(secret, msg_id, ts + 1, body, signature)
    assert not verify(secret, msg_id, ts, body, signature.replace("v1,", "v2,"))


def test_secret_is_derived_deterministically_per_run():
    key = b"server-key"
    secret = callback_secret(key, "run_a")
    assert secret == callback_secret(key, "run_a")
    assert secret != callback_secret(key, "run_b") != callback_secret(b"other", "run_a")
    assert secret.startswith("whsec_")
    assert len(base64.b64decode(secret.removeprefix("whsec_"))) == 32


def test_header_set_of_one_attempt():
    headers = signed_headers(SECRET_2, "msg_1", 1791720000, BODY_2)
    assert headers == {
        "content-type": "application/json",
        "user-agent": "TA-Research-Webhooks/1",
        "webhook-id": "msg_1",
        "webhook-timestamp": "1791720000",
        "webhook-signature": sign(SECRET_2, "msg_1", 1791720000, BODY_2),
    }


@pytest.mark.parametrize(
    ("attempts", "delay"), [(1, 10), (2, 60), (3, 300), (4, 1800), (5, 7200), (6, None)]
)
def test_retry_schedule(attempts, delay):
    assert retry_delay_s(attempts, 6) == delay


def test_retry_at_honors_max_attempts_and_caps_retry_after():
    assert retry_at(1, NOW, 6) == "2026-10-11T12:00:10+00:00"
    assert retry_at(2, NOW, 2) is None  # OUTBOX_MAX_ATTEMPTS lower than the table
    assert retry_at(1, NOW, 6, retry_after_s=7) == "2026-10-11T12:00:07+00:00"
    assert retry_at(1, NOW, 6, retry_after_s=99_999) == "2026-10-11T14:00:00+00:00"
    assert retry_at(1, NOW, 6, retry_after_s=-5) == "2026-10-11T12:00:00+00:00"
    assert retry_at(6, NOW, 6, retry_after_s=1) is None  # 429s count as attempts too
