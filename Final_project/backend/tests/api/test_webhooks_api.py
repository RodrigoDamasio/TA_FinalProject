"""POST /webhooks/telegram end to end through FastAPI (WEBHOOKS_PLAN §3.1, §9 API): mode,
auth, size cap, parsing, duplicates, and an allowed question becoming a brief Telegram run."""

import json
import logging

import pytest
from fakes import FakeTelegram

from app.api.webhooks import MAX_UPDATE_BYTES, authorized
from app.domain.outbox import EVENT_BOT_REPLY
from app.domain.run import RunStatus

SECRET = "s3cret-token_ABC"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
QUESTION = "What are the trade-offs between RAG and fine-tuning?"


def update(update_id=1, chat=42, text=QUESTION) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": 3, "chat": {"id": chat, "type": "private"}, "text": text},
    }


@pytest.fixture
def bot(api):
    def make(**settings):
        telegram = FakeTelegram()
        a = api(
            raise_errors=False,
            ports={"telegram": telegram},
            **({"telegram_mode": "fake", "telegram_webhook_secret": SECRET,
                "telegram_allowed_chats": ["42"]} | settings),
        )  # fmt: skip
        return a, telegram

    return make


def runs(a):
    return a.container.service.runs.runs_in(set(RunStatus))


def test_off_mode_answers_404(bot):
    a, _ = bot(telegram_mode="off")
    r = a.client.post("/webhooks/telegram", json=update(), headers=HEADERS)
    assert r.status_code == 404 and runs(a) == []


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}])
def test_missing_or_wrong_secret_is_401_and_writes_nothing(bot, headers, caplog):
    a, _ = bot()
    caplog.set_level(logging.DEBUG)
    r = a.client.post("/webhooks/telegram", json=update(), headers=headers)
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/webhook-unauthorized")
    assert runs(a) == [] and a.container.outbox.for_run("") == []
    assert "wrong" not in caplog.text


def test_no_configured_secret_refuses_everything(bot):
    a, _ = bot(telegram_webhook_secret=None)
    r = a.client.post("/webhooks/telegram", json=update(), headers=HEADERS)
    assert r.status_code == 401


def test_authorized_compares_fingerprints():
    assert authorized("abc", "abc") and not authorized("abc", "abd")
    assert not authorized("abc", None) and not authorized(None, "abc") and not authorized("", "")


def test_oversized_and_unparseable_updates_are_200(bot):
    a, _ = bot()
    big = json.dumps(update(text="x" * (MAX_UPDATE_BYTES + 10)))
    r = a.client.post("/webhooks/telegram", content=big, headers=HEADERS)
    assert r.status_code == 200 and r.json() == {}

    def chunks():  # no content-length: the streamed read stops at the cap
        yield b'{"update_id": 1, "pad": "'
        for _ in range(40):
            yield b"y" * 8192
        yield b'"}'

    r = a.client.post("/webhooks/telegram", content=chunks(), headers=HEADERS)
    assert r.status_code == 200
    for body in (b"not json", b'{"message": {}}', b'{"update_id": "x"}', b"[]"):
        r = a.client.post("/webhooks/telegram", content=body, headers=HEADERS)
        assert r.status_code == 200 and r.json() == {}
    assert runs(a) == [] and a.container.outbox.for_run("") == []


def test_allowed_question_becomes_a_brief_telegram_run_once(bot):
    a, telegram = bot()
    for _ in range(2):  # Telegram resends the same update
        r = a.client.post("/webhooks/telegram", json=update(update_id=77), headers=HEADERS)
        assert r.status_code == 200 and r.json() == {}
    (run,) = runs(a)
    assert run.channel.value == "telegram" and run.depth.value == "brief"
    assert run.status is RunStatus.COMPLETED
    view = a.client.get(f"/research/{run.id}").json()
    assert view["channel"] == "telegram" and view["report"] is not None
    assert a.container.dispatcher.tick() == 2
    texts = [t for chat, t in telegram.sent if chat == "42"]
    assert texts[0].startswith("Working on it. Watch the agents live: ")
    assert texts[1].startswith(view["report"]["title"]) and f"?run={run.id}" in texts[1]
    deliveries = a.client.get(f"/research/{run.id}/deliveries").json()
    assert sorted(d["event"] for d in deliveries) == ["research.accepted", "research.completed"]
    assert all(d["status"] == "delivered" and "target" not in d for d in deliveries)


def test_commands_and_strangers_get_replies_without_runs(bot):
    a, telegram = bot()
    a.client.post("/webhooks/telegram", json=update(1, text="/start"), headers=HEADERS)
    a.client.post("/webhooks/telegram", json=update(2, chat=99), headers=HEADERS)
    a.client.post("/webhooks/telegram", json=update(3, chat=99), headers=HEADERS)  # quiet
    no_text = {"update_id": 4, "message": {"message_id": 1, "chat": {"id": 42}, "sticker": {}}}
    a.client.post("/webhooks/telegram", json=no_text, headers=HEADERS)
    edited = {"update_id": 5, "edited_message": update()["message"]}
    assert a.client.post("/webhooks/telegram", json=edited, headers=HEADERS).status_code == 200
    assert runs(a) == []
    rows = [m for m in a.container.outbox.for_run("") if m.event == EVENT_BOT_REPLY]
    assert sorted(m.target for m in rows) == ["42", "42", "99"]
    a.container.dispatcher.tick()
    sent = dict((chat, text) for chat, text in telegram.sent if chat == "99")
    assert sent["99"] == "This bot is private. Your chat id is 99. Ask the owner to add it."


def test_the_endpoint_is_wired_once_per_app(bot):
    a, _ = bot()
    a.client.post("/webhooks/telegram", json=update(1, text="/help"), headers=HEADERS)
    endpoint = a.client.app.state.telegram_endpoint
    a.client.post("/webhooks/telegram", json=update(2, text="/help"), headers=HEADERS)
    assert a.client.app.state.telegram_endpoint is endpoint and endpoint.service.wired
