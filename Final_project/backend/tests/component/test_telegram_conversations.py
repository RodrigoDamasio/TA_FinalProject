"""Telegram conversations (v1.2, plan Improving_Chat_UI_UX §7, §10 component): a normal
message continues the chat's conversation, `/new` resets it, the chat survives a restart
(table chat_conversations), and a full or swept conversation is replaced automatically.
Real RunService over temp storage, fake LLM, InlineRunner (0 real calls)."""

from app.application.outbox.payloads import TelegramTexts
from app.application.telegram import TelegramService, TelegramUpdate
from app.domain.outbox import EVENT_BOT_REPLY
from app.infrastructure.sqlite_store import SqliteChatConversations

TEXTS = TelegramTexts("https://ui.example", runs_per_day=50)
QUESTION = "What are the trade-offs between RAG and fine-tuning?"
CHAT = "42"


def update(text: str, update_id: int) -> TelegramUpdate:
    message = {"message_id": update_id, "chat": {"id": int(CHAT)}, "text": text}
    return TelegramUpdate.model_validate({"update_id": update_id, "message": message})


class Clock:
    t = 1000.0

    def __call__(self) -> float:
        Clock.t += 120  # never hit the per-minute limit
        return Clock.t


def bot(h) -> TelegramService:
    """The bot over the harness; accepted runs are recorded in order (`runs_of`)."""
    service = h.container.service
    if not hasattr(h, "accepted"):
        h.accepted, submit = [], service.submit

        def recording(cmd):  # noqa: ANN001, ANN202
            run = submit(cmd)
            h.accepted.append(run.id)
            return run

        service.submit = recording
    return TelegramService(
        h.container.service, h.container.outbox, allowed_chats=[CHAT], texts=TEXTS,
        clock=Clock(), chats=SqliteChatConversations(h.container.settings.database_path),
    )  # fmt: skip


def replies(h) -> list[str]:
    return [m.payload["text"] for m in h.container.outbox.for_run("") if m.event == EVENT_BOT_REPLY]


def runs_of(h):
    return [h.get(run_id) for run_id in h.accepted]


def test_a_message_continues_the_chat_and_new_resets_it(harness):
    h = harness()
    service = bot(h)
    assert service.handle(update(QUESTION, 1)) == "submitted"
    assert service.handle(update("and cost?", 2)) == "submitted"  # 2+ characters: a follow-up
    first, second = runs_of(h)
    assert second.conversation_id == first.conversation_id and second.turn == 2
    assert second.context_turns == 1

    assert service.handle(update("/new", 3)) == "new_chat"
    assert "New chat started. Ask your question." in replies(h)
    assert service.handle(update("and cost?", 4)) == "too_short"  # a new chat: 10+ characters
    assert "Please send a longer question (at least 10 characters)." in replies(h)
    assert service.handle(update(QUESTION, 5)) == "submitted"
    third = runs_of(h)[-1]
    assert third.conversation_id != first.conversation_id and third.turn == 1


def test_the_chat_survives_a_restart(harness):
    h = harness()
    assert bot(h).handle(update(QUESTION, 1)) == "submitted"
    restarted = bot(h)  # a new service over the same database
    assert restarted.handle(update("and the cost?", 2)) == "submitted"
    first, second = runs_of(h)
    assert second.conversation_id == first.conversation_id and second.turn == 2


def test_a_full_conversation_starts_a_new_one_with_a_note(harness):
    h = harness(conversation_max_turns=2)
    service = bot(h)
    for i, text in enumerate((QUESTION, "and the cost?", "and the latency?"), 1):
        assert service.handle(update(text, i)) == "submitted"
    runs = runs_of(h)
    assert [r.turn for r in runs] == [1, 2, 1]
    assert runs[2].conversation_id != runs[0].conversation_id
    assert "This chat reached 2 questions, so I started a new chat." in replies(h)
    stored = SqliteChatConversations(h.container.settings.database_path).get(CHAT)
    assert stored == runs[2].conversation_id


def test_a_swept_conversation_starts_a_new_one_silently(harness):
    h = harness()
    chats = SqliteChatConversations(h.container.settings.database_path)
    chats.set(CHAT, "conv_" + "0" * 16)  # its runs were deleted by retention
    assert bot(h).handle(update(QUESTION, 1)) == "submitted"
    (run,) = runs_of(h)
    assert run.turn == 1 and chats.get(CHAT) == run.conversation_id
    assert replies(h) == []


def test_chat_conversations_store(tmp_path):
    db = str(tmp_path / "c.db")
    chats = SqliteChatConversations(db)
    assert chats.get("7") is None
    chats.set("7", "conv_" + "a" * 16)
    chats.set("7", "conv_" + "b" * 16)  # upsert
    assert SqliteChatConversations(db).get("7") == "conv_" + "b" * 16
    chats.clear("7")
    assert chats.get("7") is None


def test_texts_mention_new_and_continuing():
    assert "/new" in TEXTS.help() and "/start /new /help" in TEXTS.help()
    assert "/new" in TEXTS.start()
    assert TEXTS.too_short(2) == "Please send a longer question (at least 2 characters)."


def test_a_turn_still_running_elsewhere_makes_the_chat_busy(harness):
    from app.domain.run import ResearchRun

    h = harness()
    service = bot(h)
    assert service.handle(update(QUESTION, 1)) == "submitted"
    (first,) = runs_of(h)
    web_turn = ResearchRun.create(  # e.g. the same chat continued from the web link
        "A web follow-up", 5, False, False, conversation_id=first.conversation_id, turn=2
    )
    h.container.service.runs.create(web_turn)
    assert service.handle(update("and the cost?", 2)) == "busy"
