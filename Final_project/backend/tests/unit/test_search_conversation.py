"""search_conversation (plan Improving_Chat_UI_UX §6) over the real hybrid RAG stack with the
hashing embedder, plus the v1.2 unit cases: memory exclusion by conversation and the Telegram
`/new` command (0 network, 0 calls)."""

from dataclasses import replace

from fakes import FakeRetriever, rag_stack

from app.application.memory import HybridMemoryStore
from app.application.security import DataFence
from app.application.telegram import command_of
from app.application.tools.base import ToolContext, ToolRegistry
from app.application.tools.conversation import NAME, SearchConversationTool
from app.application.workers.researcher import SEARCH_TOOLS, check_args
from app.domain.errors import ModelsLoading
from app.domain.evidence import REQUESTABLE_SOURCES, SourceKind, content_hash
from app.domain.ports import Memory, SourceText
from app.domain.run import ResearchRun
from app.infrastructure.sqlite_store import SqliteMemoryRepository

FENCE = DataFence(b"s", "q")
TEXT = (
    "Retrieval-augmented generation keeps costs low: updating the index is cheaper than "
    "fine-tuning a model again. Fine-tuning costs GPU hours for every new version."
)


class Bench:
    def __init__(self, scopes: tuple[str, ...] = ("run:run_earlier",)) -> None:
        self.run = ResearchRun.create("And what about the cost?", 5, False, False, turn=2)
        self.indexer, self.retriever, self.store, _ = rag_stack(300)
        self.ctx = ToolContext(
            self.run.id, f"run:{self.run.id}", [], self.run.add_evidence,
            self.run.evidence_by_n, FENCE, REQUESTABLE_SOURCES | {SourceKind.CONVERSATION},
            evidence_count=lambda: len(self.run.evidence), conversation_scopes=scopes,
        )  # fmt: skip
        self.tool = SearchConversationTool(self.indexer, self.retriever)

    def seed(self, scope: str, kind: SourceKind, key: str, title: str, url: str | None) -> None:
        self.indexer.index([SourceText(scope, kind, key, title, TEXT, url=url)])


def test_spec_family_and_argument_rules():
    tool = Bench().tool
    assert tool.name == NAME == "search_conversation" and tool.family is SourceKind.CONVERSATION
    assert SourceKind.CONVERSATION not in REQUESTABLE_SOURCES
    assert "earlier turns of this chat" in tool.spec.description
    assert NAME in SEARCH_TOOLS
    assert check_args(NAME, {"query": "  RAG Cost? "}) == ({"query": "rag cost"}, "")
    assert check_args(NAME, {"query": "x"})[0] is None


def test_hits_are_copied_into_the_run_with_their_original_metadata():
    b = Bench()
    b.seed(
        "run:run_earlier", SourceKind.WEB, "https://ex.org/cost", "RAG costs", "https://ex.org/cost"
    )
    out = b.tool.run({"query": "fine-tuning cost"}, b.ctx)
    r = out.record
    assert (r.tool, r.provider, r.status, r.results) == (NAME, "conversation", "ok", 1)
    assert r.evidence_ns == (1,) and out.content.startswith("RESULT search_conversation ")
    assert "NOTE From earlier turns of this chat. Oldest source retrieved 20" in out.content
    ref = b.run.evidence_by_n(1)
    assert ref is not None and ref.source_kind is SourceKind.WEB
    assert (ref.title, ref.url) == ("RAG costs", "https://ex.org/cost")
    (copy,) = b.retriever.get([ref.chunk_id])
    assert copy.scope == b.ctx.run_scope and copy.source_key == "https://ex.org/cost"
    assert copy.content_hash == content_hash(copy.text)
    assert 'kind="evidence" ref="E1" source="web"' in out.content


def test_only_the_given_scopes_are_searched():
    b = Bench(scopes=("run:run_mine",))
    b.seed("run:run_other", SourceKind.WEB, "https://other.org", "Other chat", "https://other.org")
    out = b.tool.run({"query": "fine-tuning cost"}, b.ctx)
    assert out.record.status == "empty" and "No matching passages" in out.content
    assert b.run.evidence == []


def test_without_earlier_scopes_it_refuses():
    b = Bench(scopes=())
    out = b.tool.run({"query": "cost"}, b.ctx)
    assert out.record.status == "refused" and "REFUSED no earlier turn" in out.content
    own = Bench()
    own_ctx = replace(own.ctx, conversation_scopes=(own.ctx.run_scope,))  # its own scope
    assert own.tool.run({"query": "cost"}, own_ctx).record.status == "refused"


def test_bad_arguments_and_failures_never_raise():
    b = Bench()
    assert b.tool.run({"query": ""}, b.ctx).record.status == "error"
    broken = SearchConversationTool(b.indexer, FakeRetriever(fail=True))
    out = broken.run({"query": "cost"}, b.ctx)
    assert out.record.error == "conversation search failed"

    class Loading:
        def search(self, *a, **kw):
            raise ModelsLoading()

    out = SearchConversationTool(b.indexer, Loading()).run({"query": "cost"}, b.ctx)
    assert out.record.error == "search index warming up"


def test_the_toolbox_offers_it_only_with_the_internal_family():
    b = Bench()
    registry = ToolRegistry([b.tool])
    assert registry.box(b.ctx, REQUESTABLE_SOURCES, {}).offered() == []
    refused = registry.box(b.ctx, REQUESTABLE_SOURCES, {}).call(NAME, {"query": "cost"})
    assert refused.record.status == "refused"
    box = registry.box(b.ctx, REQUESTABLE_SOURCES | {SourceKind.CONVERSATION}, {})
    assert [s.name for s in box.offered()] == [NAME]
    assert registry.specs()[0]["family"] == "conversation"


def test_memory_recall_excludes_the_runs_of_a_conversation(tmp_path):
    repo = SqliteMemoryRepository(str(tmp_path / "m.db"))
    indexer, retriever, _, _ = rag_stack()
    memory = HybridMemoryStore(repo, indexer, retriever)
    for run_id, day in (("run_turn1", "01"), ("run_other", "02")):
        q = f"RAG or fine-tuning costs {run_id}"
        memory.record(Memory(run_id, q, "RAG is cheaper to update.", [], f"2026-10-{day}"))
    query = "rag fine-tuning costs"
    assert {m.run_id for m in memory.recall(query, 3, "x")} == {"run_turn1", "run_other"}
    kept = memory.recall(query, 3, "x", exclude_runs=frozenset({"run_turn1", "run_now"}))
    assert [m.run_id for m in kept] == ["run_other"]


def test_telegram_new_command_parsing():
    assert command_of("/new") == "new" and command_of("/NEW@ResearchBot") == "new"
    assert command_of("/new please") == "new" and command_of("new chat") is None
