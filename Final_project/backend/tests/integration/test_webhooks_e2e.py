"""End to end in LLM_MODE=fake (WEBHOOKS_PLAN §9 E2E, 0 external calls):
- a run with a callback_url is delivered by the REAL HttpWebhookSender to the sample receiver
  (samples/webhooks/receiver.py) on an in-process loopback server, which verifies it, and a
  replay of the same webhook-id is reported as a duplicate;
- a Telegram update yields "working on it" then the report, in that order."""

import importlib.util
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fakes import FakeTelegram

from app.application.outbox.signing import sign
from app.domain.ports import SendResult
from app.infrastructure.webhook_sender import HttpWebhookSender

RECEIVER = Path(__file__).resolve().parents[2] / "samples" / "webhooks" / "receiver.py"
QUESTION = "What are the trade-offs between RAG and fine-tuning?"


def load_receiver():
    spec = importlib.util.spec_from_file_location("webhook_receiver", RECEIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


receiver = load_receiver()


class Receiver:
    """The sample receiver, served on 127.0.0.1 in a thread. The secret is set after submit
    (it is derived per run), exactly as a real client would."""

    def __init__(self) -> None:
        self.secret = "whsec_" + "A" * 43 + "="
        self.lines: list[str] = []
        self.seen: set[str] = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — the sample's handler, current secret
                sample = receiver.make_handler(outer.secret, outer.seen, outer.lines.append)
                sample.do_POST(self)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hook():
    r = Receiver()
    yield r
    r.close()


class Recording(HttpWebhookSender):
    """The real sender; keeps what it sent so the test can replay it."""

    def __init__(self) -> None:
        super().__init__(5, allow_private=True)
        self.sent: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        self.sent.append((url, body, dict(headers)))
        return super().post(url, body, headers)


def test_signed_callback_verified_by_the_sample_receiver(api, hook):
    sender = Recording()
    a = api(ports={"sender": sender}, callback_allow_private=True)
    r = a.run(callback_url=hook.url, depth="brief")
    assert r.status_code == 202
    hook.secret, run_id = r.json()["callback_secret"], r.json()["run_id"]
    assert a.container.dispatcher.tick() == 1
    assert hook.lines == [
        f"verified research.completed {run_id} id {sender.sent[0][2]['webhook-id']}"
    ]
    (delivery,) = a.client.get(f"/research/{run_id}/deliveries").json()
    assert delivery["status"] == "delivered" and delivery["last_status"] == 204

    url, body, headers = sender.sent[0]
    replay = sender.post(url, body, headers)  # at least once: the same id again
    assert replay.ok and hook.lines[-1] == f"duplicate {headers['webhook-id']}"
    payload = json.loads(body)
    assert payload["data"]["links"]["self"].endswith(f"/research/{run_id}")
    assert "conversation_history" not in payload["data"]

    tampered = sender.post(url, body.replace(b"completed", b"COMPLETED"), headers)
    assert tampered.status == 401 and hook.lines[-1] == "rejected: bad signature"


def test_wrong_secret_is_retried_then_verified(api, hook):
    sender = Recording()
    a = api(ports={"sender": sender}, callback_allow_private=True)
    run_id = a.run(callback_url=hook.url).json()["run_id"]
    a.container.dispatcher.tick()  # the receiver doesn't know the secret yet → 401
    (delivery,) = a.client.get(f"/research/{run_id}/deliveries").json()
    assert delivery["status"] == "pending" and delivery["last_error"] == "http 401"
    hook.secret = a.container.callback_secret(run_id)
    a.container.dispatcher._clock = lambda: _later(delivery["next_attempt_at"])
    assert a.container.dispatcher.tick() == 1
    assert hook.lines[-1].startswith(f"verified research.completed {run_id}")


def _later(iso_time: str) -> datetime:
    return datetime.fromisoformat(iso_time) + timedelta(seconds=1)


def test_receiver_verify_rules():
    secret = "whsec_" + "A" * 43 + "="
    headers = {"webhook-id": "msg_1", "webhook-timestamp": "1000", "webhook-signature": "v1,x"}
    assert not receiver.verify(secret, headers, b"{}", now=1000)
    assert not receiver.verify(secret, {**headers, "webhook-timestamp": "abc"}, b"{}", now=1000)
    assert not receiver.verify(secret, {**headers, "webhook-id": ""}, b"{}", now=1000)

    good = {**headers, "webhook-signature": "v2,x " + sign(secret, "msg_1", 1000, b"{}")}
    assert receiver.verify(secret, good, b"{}", now=1299)
    assert not receiver.verify(secret, good, b"{}", now=1301)  # 5-minute tolerance


def test_telegram_update_gives_accepted_then_the_report(api):
    telegram = FakeTelegram()
    a = api(
        ports={"telegram": telegram}, telegram_mode="fake",
        telegram_webhook_secret="s", telegram_allowed_chats=["42"],
    )  # fmt: skip
    update = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 42}, "text": QUESTION}}
    headers = {"X-Telegram-Bot-Api-Secret-Token": "s"}
    assert a.client.post("/webhooks/telegram", json=update, headers=headers).json() == {}
    assert a.container.dispatcher.tick() == 2
    (first, second) = telegram.sent
    assert first[0] == second[0] == "42"
    assert first[1].startswith("Working on it. Watch the agents live: http")
    assert "Full report: http" in second[1] and "Sources:" in second[1]
