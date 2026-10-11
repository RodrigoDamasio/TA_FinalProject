"""Provider-neutral abstractions (LLM types from Labs 2-4) and the ports of the final project
(BACKEND_PLAN §7-§8, contracts v1.1 §24)."""

import builtins
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

from .documents import Document, DocumentStatus, Workspace
from .evidence import EvidenceChunk, SourceKind
from .outbox import OutboxMessage

if TYPE_CHECKING:
    from .run import ResearchRun, RunEvent, RunStatus

FinishReason = Literal["stop", "max_tokens", "safety", "other"]


# ---- LLM (Lab 4 + a per-request model) ------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str | None
    name: str
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    thinking: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input + other.input, self.output + other.output, self.thinking + other.thinking
        )


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str | None
    tool_calls: list[ToolCall]
    raw_turn: Any = None  # provider turn, echoed back unchanged in the next request


@dataclass(frozen=True)
class ToolResultsMessage:
    results: list[ToolResult]


Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    response_schema: type[BaseModel] | None = None
    max_output_tokens: int = 2048
    thinking_budget: int | None = None
    model: str | None = None  # model routing: None = the client's default model


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    parsed: dict[str, Any] | None
    usage: TokenUsage
    finish_reason: FinishReason
    raw_turn: Any = None
    cached: bool = False  # served by CachingLLMClient (0 real calls)

    def as_message(self) -> AssistantMessage:
        return AssistantMessage(self.text, self.tool_calls, self.raw_turn)


class LLMClient(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...


# ---- knowledge source (the Researcher's Wikipedia access) ------------------------------


@dataclass(frozen=True)
class SearchHit:
    title: str
    page_id: int


@dataclass(frozen=True)
class SourceDraft:
    """A fetched source before the run numbers it."""

    title: str
    url: str
    extract: str


class KnowledgeSource(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchHit]: ...

    def fetch(self, hits: list[SearchHit], max_chars: int) -> list[SourceDraft]: ...

    def available(self) -> bool: ...


# ---- memory (extension) ----------------------------------------------------------------


@dataclass(frozen=True)
class Memory:
    run_id: str
    question: str
    summary: str
    reference_titles: list[str]  # v1.1 (was source_titles)
    created_at: str


class MemoryStore(Protocol):
    """Same shape as Lab 3's long-term memory port: recall(query, limit)."""

    def recall(
        self,
        query: str,
        limit: int,
        exclude_question: str,
        exclude_runs: frozenset[str] = frozenset(),  # v1.2: the current conversation's runs
    ) -> list[Memory]: ...

    def record(self, memory: Memory) -> None: ...


class MemoryRepository(Protocol):
    def record(self, memory: Memory) -> None: ...

    def recent(self, limit: int) -> list[Memory]: ...

    def delete_older_than(self, cutoff_iso: str) -> int: ...


# ---- runs ------------------------------------------------------------------------------


class RunRepository(Protocol):
    def create(self, run: "ResearchRun") -> None: ...

    def save(self, run: "ResearchRun") -> None: ...  # state + pending events, atomically

    def get(self, run_id: str) -> "ResearchRun": ...

    def events_since(self, run_id: str, after_id: int) -> list["RunEvent"]: ...

    def runs_in(self, statuses: set["RunStatus"]) -> list["ResearchRun"]: ...

    def conversation(self, conversation_id: str, limit: int) -> list["ResearchRun"]: ...  # v1.2

    def delete_older_than(self, cutoff_iso: str) -> list[str]: ...  # v1.1: the deleted ids

    def status_counts(self) -> dict[str, int]: ...

    def recent_durations_ms(self, limit: int) -> list[float]: ...


class JobRunner(Protocol):
    def submit(self, job_id: str) -> None: ...

    def has_capacity(self) -> bool: ...


# ---- web search and page fetching (SEARXNG) ------------------------------------------------


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str
    engines: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    status: int
    media_type: str
    text: str  # decoded body, ≤ FETCH_MAX_BYTES


class WebSearch(Protocol):
    def search(self, query: str, limit: int) -> list[WebResult]: ...  # raises SearchUnavailable

    def available(self) -> bool: ...


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...  # raises FetchRefused / FetchFailed


class HtmlExtractor(Protocol):  # v1.1: split from TextExtractor (SEARXNG)
    def html_to_markdown(self, html: str, url: str) -> str: ...


class PdfExtractor(Protocol):  # v1.1: split from TextExtractor (RAG)
    def pdf_to_text(self, data: bytes) -> tuple[str, tuple[tuple[int, int], ...]]: ...


# ---- RAG (BACKEND_PLAN §7) ------------------------------------------------------------------


class Embedder(Protocol):
    name: str
    dim: int
    max_tokens: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    def upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None: ...

    def query(
        self,
        vector: list[float],
        scopes: list[str],
        n: int,
        source_keys: list[str] | None = None,  # v1.1
    ) -> list[tuple[str, float]]:
        """(chunk id, cosine distance), nearest first."""
        ...

    def get(self, ids: list[str]) -> list[EvidenceChunk]: ...

    def iter_chunks(self, scope: str) -> Iterator[EvidenceChunk]: ...

    def delete_scope(self, scope: str) -> int: ...

    def delete_source(self, scope: str, source_key: str) -> int: ...

    def count(self, scope: str | None = None) -> int: ...


class EmbeddingCache(Protocol):
    def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]: ...

    def put_many(self, model: str, items: dict[str, list[float]]) -> None: ...

    def stats(self) -> dict[str, int]: ...


@dataclass(frozen=True)
class SourceText:
    """Input to indexing: one fetched or extracted source."""

    scope: str
    source_kind: SourceKind
    source_key: str
    title: str
    text: str  # Markdown or plain text
    url: str | None = None
    document_id: str | None = None
    document_name: str | None = None
    pages: tuple[tuple[int, int], ...] = ()  # (page number, start offset) for PDFs


class Indexer(Protocol):
    def index(self, sources: list[SourceText]) -> list[EvidenceChunk]: ...

    def adopt(self, chunks: list[EvidenceChunk], scope: str) -> list[EvidenceChunk]: ...  # v1.1

    def remove(self, scope: str, source_key: str | None = None) -> int: ...  # v1.1


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: EvidenceChunk
    score: float  # for ordering only


class Retriever(Protocol):
    def search(
        self, query: str, scopes: list[str], k: int, source_keys: list[str] | None = None
    ) -> list[RetrievedChunk]: ...

    def get(self, chunk_ids: list[str]) -> list[EvidenceChunk]: ...


# ---- documents (RAG) --------------------------------------------------------------------------


class DocumentRepository(Protocol):
    def save(self, document: Document) -> None: ...

    def get(self, document_id: str) -> Document | None: ...

    def list(self, workspace_id: str) -> builtins.list[Document]: ...

    def by_hash(self, workspace_id: str, sha256: str) -> Document | None: ...

    def delete(self, document_id: str) -> None: ...

    def with_status(self, status: DocumentStatus) -> builtins.list[Document]: ...

    def get_workspace(self, workspace_id: str) -> Workspace | None: ...

    def upsert_workspace(self, workspace: Workspace) -> None: ...

    def delete_workspace(self, workspace_id: str) -> None: ...

    def expired(self, now: datetime) -> builtins.list[Workspace]: ...


# ---- webhooks and Telegram (WEBHOOKS) ----------------------------------------------------------


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status: int | None = None
    retry_after_s: float | None = None  # Telegram 429 / Retry-After
    error: str | None = None  # short, never a secret


class OutboxRepository(Protocol):
    def add(self, message: OutboxMessage) -> None: ...  # v1.1: standalone bot replies

    def due(self, now: datetime, limit: int) -> list[OutboxMessage]: ...

    def mark_delivered(self, message_id: str, at: datetime, status: int | None = None) -> None: ...

    def mark_retry(
        self,
        message_id: str,
        attempts: int,
        next_at: str,
        error: str,
        status: int | None = None,
    ) -> None: ...

    def mark_dead(
        self, message_id: str, attempts: int, error: str, status: int | None = None
    ) -> None: ...

    def for_run(self, run_id: str) -> list[OutboxMessage]: ...

    def delete_older_than(self, cutoff_iso: str) -> int: ...

    def counts(self) -> dict[str, float]: ...  # pending, dead, oldest_pending_s


class TelegramGateway(Protocol):
    def send_message(self, chat_id: str, text: str) -> SendResult: ...


class WebhookSender(Protocol):
    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult: ...


class UpdateLedger(Protocol):
    def seen(self, update_id: int) -> bool: ...  # atomic insert-or-ignore: True = duplicate

    def forget_older_than(self, cutoff_iso: str) -> int: ...


class ChatConversations(Protocol):
    """v1.2: the current conversation of each Telegram chat (table chat_conversations)."""

    def get(self, chat_id: str) -> str | None: ...

    def set(self, chat_id: str, conversation_id: str) -> None: ...

    def clear(self, chat_id: str) -> None: ...


class Reranker(Protocol):  # additive (RAG): optional cross-encoder, local evaluation only
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...
