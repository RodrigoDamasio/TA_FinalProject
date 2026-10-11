"""TelegramService decision table (WEBHOOKS_PLAN §3.2, §9 component), on the real RunService
over temp storage and the fake LLM (InlineRunner: a submitted run finishes inline)."""

import pytest

from app.application.outbox.payloads import TelegramTexts
from app.application.telegram import (
    ChatLimiter,
    TelegramService,
    TelegramUpdate,
    chat_fingerprint,
    command_of,
)
from app.domain.depth import Depth
from app.domain.errors import (
    InputTooLarge,
    InvalidField,
    LLMQuotaExceeded,
    LLMUnavailable,
    QueueFull,
)
from app.domain.outbox import EVENT_ACCEPTED, EVENT_BOT_REPLY, Channel, Subscriber
from app.domain.run import ResearchRun, RunStatus

TEXTS = TelegramTexts("https://ui.example", runs_per_day=3)
QUESTION = "What are the trade-offs between RAG and fine-tuning?"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def update(text: str | None = QUESTION, chat: int = 42, update_id: int = 1) -> TelegramUpdate:
    message = {"message_id": 5, "chat": {"id": chat, "type": "private"}}
    if text is not None:
        message["text"] = text
    return TelegramUpdate.model_validate({"update_id": update_id, "message": message})


@pytest.fixture
def bot(harness):
    h = harness()
    clock = Clock()
    service = TelegramService(
        h.container.service, h.container.outbox, allowed_chats=[" 42", "-100", ""],
        texts=TEXTS, clock=clock,
    )  # fmt: skip
    return h, service, clock


def replies(h, chat="42") -> list[str]:
    """Sorted: rows queued in the same second have no order between them."""
    return sorted(
        m.payload["text"]
        for m in h.container.outbox.for_run("")
        if m.event == EVENT_BOT_REPLY and m.target == chat
    )


def test_parsing_helpers():
    assert command_of("/start") == "start" and command_of("/START@ResearchBot hi") == "start"
    assert command_of("/help") == "help" and command_of("/weird") == "weird"
    assert command_of("What is /start?") is None
    assert len(chat_fingerprint("42")) == 8 and chat_fingerprint("42") != chat_fingerprint("43")
    parsed = TelegramUpdate.model_validate(
        {"update_id": 9, "edited_message": {"x": 1}, "extra": True}
    )
    assert parsed.message is None


def test_unwired_placeholder():
    assert TelegramService().wired is False


def test_update_without_a_message_is_ignored(bot):
    h, service, _ = bot
    assert service.handle(TelegramUpdate(update_id=3)) == "ignored"
    assert h.container.outbox.for_run("") == []


def test_unknown_chat_gets_its_id_once_per_hour(bot):
    h, service, clock = bot
    assert service.handle(update(chat=7)) == "not_allowed"
    assert service.handle(update("/start", chat=7)) == "not_allowed_quiet"
    clock.t += 3599
    assert service.handle(update(chat=7)) == "not_allowed_quiet"
    clock.t += 2
    assert service.handle(update(chat=7)) == "not_allowed"
    assert replies(h, "7") == [TEXTS.not_allowed("7")] * 2
    assert h.container.service.runs.runs_in(set(RunStatus)) == []  # no run, no LLM call
    assert h.llm.calls() == 0


def test_non_text_and_commands(bot):
    h, service, _ = bot
    assert service.handle(update(None)) == "text_only"
    assert service.handle(update("/start@ResearchBot")) == "start"
    assert service.handle(update("/HELP")) == "help"
    assert service.handle(update("/detailed what is RAG")) == "help"
    assert replies(h) == sorted([TEXTS.text_only(), TEXTS.start(), TEXTS.help(), TEXTS.help()])
    assert service.handle(update("/start", chat=-100)) == "start"  # an allowlisted group


def test_question_starts_a_brief_telegram_run(bot):
    h, service, _ = bot
    assert service.handle(update()) == "submitted"
    (run,) = h.container.service.runs.runs_in(set(RunStatus))
    assert run.channel is Channel.TELEGRAM and run.depth is Depth.BRIEF
    assert run.require_approval is False and run.workspace_id is None
    assert run.subscribers == [Subscriber("telegram", "42")]
    assert run.question == QUESTION and run.status is RunStatus.COMPLETED
    events = sorted(m.event for m in h.container.outbox.for_run(run.id))
    assert events == [EVENT_ACCEPTED, "research.completed"]
    assert replies(h) == []  # submit queued research.accepted itself


def test_busy_while_a_run_of_the_chat_is_active(bot):
    h, service, _ = bot
    active = ResearchRun.create(QUESTION, 4, False, False,
                                subscribers=[Subscriber("telegram", "42")])  # fmt: skip
    h.container.service.runs.create(active)
    other = ResearchRun.create(QUESTION, 4, False, False,
                               subscribers=[Subscriber("telegram", "-100")])  # fmt: skip
    h.container.service.runs.create(other)
    assert service.handle(update()) == "busy"
    assert replies(h) == [TEXTS.busy()]
    active.start()
    active.fail("x")
    h.container.service.runs.save(active)
    assert service.handle(update()) == "submitted"


def test_per_chat_limit_counts_only_accepted_runs(bot):
    h, service, clock = bot
    assert service.handle(update("short")) == "too_short"  # rejected: no allowance used
    assert service.handle(update()) == "submitted"
    assert service.handle(update()) == "submitted"
    assert service.handle(update()) == "rate_limited"  # 2 per minute
    clock.t += 61
    assert service.handle(update()) == "submitted"
    clock.t += 61
    assert service.handle(update()) == "rate_limited"  # 3 per day
    assert replies(h) == sorted(
        [TEXTS.too_short(), TEXTS.rate_limited(60), TEXTS.rate_limited(86400 - 122)]
    )
    assert "about 24 h" in TEXTS.rate_limited(86400 - 122)
    clock.t += 86400
    assert service.handle(update()) == "submitted"


@pytest.mark.parametrize(
    ("error", "decision", "reply"),
    [
        (InvalidField("too short", "#/question"), "too_short", TEXTS.too_short()),
        (InputTooLarge("too long"), "too_long", TEXTS.too_long(len(QUESTION))),
        (QueueFull(), "busy_server", TEXTS.busy_server()),
        (LLMQuotaExceeded("day", 3600), "quota", TEXTS.quota()),
        (LLMUnavailable("down"), "unavailable", TEXTS.unavailable()),
        (RuntimeError("boom"), "error", TEXTS.error()),
    ],
)
def test_submit_errors_become_replies(bot, monkeypatch, error, decision, reply):
    h, service, _ = bot

    def fail(cmd):
        raise error

    monkeypatch.setattr(h.container.service, "submit", fail)
    assert service.handle(update()) == decision
    assert replies(h) == [reply]


def test_too_long_question_is_refused_by_the_real_service(bot):
    h, service, _ = bot
    assert service.handle(update("x" * 2500)) == "too_long"
    assert replies(h) == [TEXTS.too_long(2500)]


def test_handle_never_raises_even_when_the_reply_fails(bot, monkeypatch):
    h, service, _ = bot

    def broken(message):
        raise OSError("disk full")

    monkeypatch.setattr(h.container.outbox, "add", broken)
    assert service.handle(update("/start")) == "error"


def test_chat_limiter_windows():
    clock = Clock()
    limiter = ChatLimiter(2, 3, clock)
    assert limiter.retry_after("a") is None
    limiter.record("a")
    clock.t += 10
    limiter.record("a")
    assert limiter.retry_after("a") == pytest.approx(50)
    assert limiter.retry_after("b") is None
    clock.t += 86400
    assert limiter.retry_after("a") is None  # old hits expire


def test_the_unknown_chat_memory_is_bounded(bot):
    _, service, clock = bot
    service._told = {str(i): clock.t - 7200 for i in range(10_001)}
    assert service.handle(update(chat=7)) == "not_allowed"
    assert service._told == {"7": clock.t}
