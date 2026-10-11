"""Wiring: the only place that chooses concrete implementations. One Container per app
(tests build their own with temp storage, a fake LLM and fake Wikipedia).

LLM chain for one run, outermost first:
    Metered → RunCounter → Caching → Budgeted(run) → DailyCap → CircuitBreaker → Retrying
    → ConcurrencyLimited(1) → Paced → Gemini
Cache hits never reach the budget or the daily cap: re-runs cost 0 and never stop early."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.application.budget import BudgetedLLMClient, CallBudget, RunCounter
from app.application.indexing.simple import SimpleIndexer
from app.application.memory import BM25Memory
from app.application.outbox.dispatcher import OutboxDispatcher
from app.application.outbox.signing import callback_secret
from app.application.prompts import PromptLibrary
from app.application.retrieval.simple import SimpleRetriever
from app.application.runs import RunLimits, RunService
from app.application.service_level import ServiceLevel
from app.application.stats import (
    DailyCapLLMClient,
    MeteredLLMClient,
    Pricing,
    StatsService,
    UsageMeter,
)
from app.application.supervisor import LoopSettings, Supervisor
from app.application.telegram import TelegramService
from app.application.tools.base import ToolRegistry
from app.application.tools.documents import SearchDocumentsTool
from app.application.tools.web import ReadResultTool, SearchWebTool
from app.application.tools.wikipedia import SearchWikipediaTool
from app.application.views import research_view
from app.application.workers.base import WorkerRegistry
from app.application.workers.fact_checker import FactChecker
from app.application.workers.researcher import Researcher
from app.application.workers.reviewer import Reviewer
from app.application.workers.writer import Writer
from app.config import Settings
from app.domain.errors import LLMQuotaExceeded
from app.domain.ports import (
    Embedder,
    KnowledgeSource,
    LLMClient,
    PageFetcher,
    TelegramGateway,
    VectorStore,
    WebhookSender,
    WebSearch,
)
from app.infrastructure.caching_llm import CachingLLMClient
from app.infrastructure.demo import DemoLLMClient
from app.infrastructure.fake_search import FakePageFetcher, FakeWebSearch
from app.infrastructure.fake_telegram import FakeTelegram
from app.infrastructure.fake_wikipedia import FakeWikipedia
from app.infrastructure.gemini_client import GeminiClient
from app.infrastructure.hashing_embedder import HashingEmbedder
from app.infrastructure.html_extract import TagStripExtractor
from app.infrastructure.llm_decorators import (
    CircuitBreakerLLMClient,
    ConcurrencyLimitedLLMClient,
    PacedLLMClient,
    RetryingLLMClient,
)
from app.infrastructure.memory_vector_store import InMemoryVectorStore
from app.infrastructure.runner import InlineRunner, ThreadRunner
from app.infrastructure.safe_http import StubPageFetcher, check_url
from app.infrastructure.samples import SampleStore
from app.infrastructure.searxng import SearxngSearch
from app.infrastructure.sqlite_store import (
    SqliteMemoryRepository,
    SqliteOutboxRepository,
    SqliteResponseCache,
    SqliteRunRepository,
    SqliteSettings,
    SqliteWikiCache,
)
from app.infrastructure.telegram_client import TelegramClient
from app.infrastructure.webhook_sender import HttpWebhookSender
from app.infrastructure.wikipedia import WikipediaSource

from .guards import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    service: RunService
    supervisor: Supervisor
    registry: WorkerRegistry
    runner: InlineRunner | ThreadRunner
    limiter: RateLimiter
    samples: SampleStore
    stats: StatsService
    prompts: PromptLibrary
    pricing: Pricing
    level: ServiceLevel
    meter: UsageMeter
    knowledge: KnowledgeSource
    response_cache: SqliteResponseCache
    tools: ToolRegistry
    outbox: SqliteOutboxRepository
    dispatcher: OutboxDispatcher
    telegram_service: TelegramService
    callback_secret: Callable[[str], str]
    search: WebSearch

    def start(self) -> None:
        interrupted = self.service.recover()
        logger.info(
            "startup interrupted_runs=%d llm_mode=%s wiki=%s search=%s embed=%s telegram=%s",
            interrupted,
            self.settings.llm_mode,
            self.settings.wiki_mode,
            self.settings.search_mode,
            self.settings.embed_mode,
            self.settings.telegram_mode,
        )
        self.runner.start()
        if self.settings.runner_mode != "inline":
            self.dispatcher.start(self.settings.outbox_poll_s)

    def stop(self) -> None:
        self.dispatcher.stop()
        self.runner.stop()

    def health(self) -> dict[str, Any]:
        s = self.settings
        level = self.level.current()
        return {
            "status": "ok",
            "service_level": level["level"],
            "service_reason": level["reason"],
            "retry_after_s": level["retry_after_s"],
            "llm_mode": s.llm_mode,
            "wikipedia": s.wiki_mode,
            "search": {"mode": s.search_mode, "available": self.search.available()},
            "embedder": s.embed_mode,
            "telegram": s.telegram_mode,
            "outbox": self.outbox.counts(),
            "models": {
                "supervisor": s.model_label(s.model_supervisor),
                "workers": s.model_label(s.model_workers),
            },
            "prompt_version": self.prompts.version,
            "usage_today": {
                "calls": self.meter.snapshot()["real"],
                "limit": s.llm_daily_request_limit,
            },
            "limits": {
                "question_min_chars": s.question_min_chars,
                "question_max_chars": s.question_max_chars,
                "default_max_iterations": s.default_max_iterations,
                "max_iterations_limit": s.max_iterations_limit,
                "max_parallel_delegations": s.max_parallel_delegations,
                "approval_timeout_min": s.approval_timeout_min,
                "feedback_max_chars": 1000,
            },
        }


def gemini_stack(settings: Settings) -> tuple[LLMClient, CircuitBreakerLLMClient]:
    """Uncached chain, outermost first: CircuitBreaker → Retrying → Gate → Paced → Gemini."""
    if not settings.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set (or use LLM_MODE=fake).")
    gemini = GeminiClient(settings.google_api_key, settings.gemini_model, settings.llm_timeout_s)
    gated = ConcurrencyLimitedLLMClient(
        PacedLLMClient(gemini, settings.llm_min_interval_s),
        limit=1,  # one free-tier quota: calls stay serialized (also for parallel workers)
        wait_s=settings.llm_timeout_s * 3,
    )
    breaker = CircuitBreakerLLMClient(RetryingLLMClient(gated))
    return breaker, breaker


def build_container(
    settings: Settings,
    llm: LLMClient | None = None,
    knowledge: KnowledgeSource | None = None,
    *,
    search: WebSearch | None = None,
    fetcher: PageFetcher | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    telegram: TelegramGateway | None = None,
    sender: WebhookSender | None = None,
) -> Container:
    db = settings.database_path
    runs = SqliteRunRepository(db)
    memories = SqliteMemoryRepository(db)
    response_cache = SqliteResponseCache(db)
    secret = SqliteSettings(db).prompt_secret(settings.prompt_secret)
    prompts = PromptLibrary()
    meter = UsageMeter()

    breaker: CircuitBreakerLLMClient | None = None
    if llm is not None:
        base = llm
    elif settings.fake:
        base = DemoLLMClient(settings.fake_llm_delay_s)
    else:
        base, breaker = gemini_stack(settings)
    capped = DailyCapLLMClient(base, meter, settings.llm_daily_request_limit)
    default_model = settings.model_label(settings.gemini_model)

    def llm_for(budget: CallBudget) -> LLMClient:
        cached = CachingLLMClient(BudgetedLLMClient(capped, budget), response_cache, default_model)
        return MeteredLLMClient(RunCounter(cached, budget), meter)

    if knowledge is None:
        if settings.wiki_mode == "fake":
            knowledge = FakeWikipedia()
        else:
            knowledge = WikipediaSource(
                SqliteWikiCache(db),
                settings.wiki_lang,
                settings.wiki_timeout_s,
                settings.wiki_cache_ttl_h * 3600,
            )
    # RAG (step 3: hashing embedder + in-memory store for every mode; RAG adds ONNX + Chroma)
    if settings.embed_mode != "fake" and embedder is None:
        logger.warning("EMBED_MODE=%s is not implemented yet: using the fake", settings.embed_mode)
    embedder = embedder or HashingEmbedder()
    store = store or InMemoryVectorStore()
    indexer = SimpleIndexer(store, embedder, settings.chunk_max_chars)
    retriever = SimpleRetriever(store, embedder)

    # Search and page reading
    if search is None:
        search = (
            FakeWebSearch()
            if settings.search_mode == "fake"
            else SearxngSearch(settings.searxng_url, settings.search_timeout_s)
        )
    if fetcher is None:
        fetcher = FakePageFetcher() if settings.search_mode == "fake" else StubPageFetcher()
    k, snip = settings.evidence_top_k, settings.evidence_snippet_chars
    wiki_tool = SearchWikipediaTool(
        knowledge, indexer, retriever,
        max_pages=settings.wiki_max_pages, max_chars=settings.wiki_max_chars_per_page,
        top_k=k, snippet_chars=snip,
    )  # fmt: skip
    tools = ToolRegistry(
        [
            SearchWebTool(
                search,
                indexer,
                retriever,
                wiki_tool,
                max_results=settings.search_max_results,
                top_k=k,
                snippet_chars=snip,
            ),  # fmt: skip
            wiki_tool,
            SearchDocumentsTool(indexer, retriever, top_k=k, snippet_chars=snip),
            ReadResultTool(
                fetcher,
                TagStripExtractor(),
                indexer,
                retriever,
                max_chars=settings.read_max_chars,
                top_k=k,
                snippet_chars=snip,
            ),  # fmt: skip
        ]
    )
    registry = WorkerRegistry(
        [
            Researcher(prompts, settings.researcher_max_tool_calls),
            Writer(prompts),
            Reviewer(prompts),
            FactChecker(prompts),
        ]
    )
    supervisor = Supervisor(
        runs,
        llm_for,
        registry,
        prompts,
        secret,
        LoopSettings(
            supervisor_model=settings.model_label(settings.model_supervisor),
            worker_model=settings.model_label(settings.model_workers),
            max_parallel=settings.max_parallel_delegations,
            task_max_chars=settings.delegation_task_max_chars,
            revision_iterations=settings.revision_iterations,
            approval_timeout_min=settings.approval_timeout_min,
            supervisor_context_tokens=settings.supervisor_context_tokens,
            worker_context_chars=int(settings.worker_context_tokens * 3.5),
            memory_limit=settings.memory_recall_limit,
            budget_per_iteration=settings.llm_run_budget_per_iteration,
            budget_base=settings.llm_run_budget_base,
        ),
        memory=BM25Memory(memories) if settings.memory_enabled else None,
        tools=tools,
        retriever=retriever,
    )

    def ensure_llm_available() -> None:
        if breaker is not None:
            breaker.ensure_closed()
        capped.ensure_available()

    def llm_blocked() -> float | None:
        try:
            ensure_llm_available()
        except LLMQuotaExceeded as err:
            return err.retry_after_s
        return None

    sweep_target: dict[str, RunService] = {}
    runner: InlineRunner | ThreadRunner = (
        InlineRunner(supervisor.run)
        if settings.runner_mode == "inline"
        else ThreadRunner(
            supervisor.run,
            settings.max_queued_jobs,
            sweep=lambda: sweep_target["service"].sweep(),
            sweep_every_s=settings.sweep_every_s,
        )
    )
    service = RunService(
        runs,
        runner,
        supervisor,
        RunLimits(
            settings.default_max_iterations,
            settings.max_iterations_limit,
            settings.question_min_chars,
            settings.question_max_chars,
            settings.revision_iterations,
            settings.run_retention_days,
            settings.memory_enabled,
        ),
        ensure_llm_available,
        memories,
        url_check=lambda url: check_url(
            url,
            schemes=frozenset({"http", "https"} if settings.callback_allow_private else {"https"}),
            allow_private=settings.callback_allow_private,
        ),
        indexer=indexer,
    )
    sweep_target["service"] = service
    pricing = Pricing(
        settings.llm_daily_request_limit, settings.price_input_per_m, settings.price_output_per_m
    )
    level = ServiceLevel(llm_blocked, knowledge.available)
    outbox = SqliteOutboxRepository(db)
    if telegram is None:
        telegram = (
            TelegramClient(settings.telegram_bot_token or "", settings.webhook_timeout_s)
            if settings.telegram_mode == "live"
            else FakeTelegram()
        )
    sender = sender or HttpWebhookSender(
        settings.webhook_timeout_s, settings.callback_allow_private
    )

    def view(run: Any) -> dict[str, Any]:
        return research_view(run, pricing, demo=settings.fake)

    dispatcher = OutboxDispatcher(
        outbox,
        runs,
        telegram,
        sender,
        view,
        secret,
        base_url=settings.base_url,
        frontend_url=settings.frontend_url,
        max_attempts=settings.outbox_max_attempts,
    )
    container = Container(
        settings=settings,
        service=service,
        supervisor=supervisor,
        registry=registry,
        runner=runner,
        limiter=RateLimiter(settings.rate_limit_runs_per_minute, settings.rate_limit_runs_per_day),
        samples=SampleStore(),
        stats=None,  # type: ignore[arg-type] — set below (needs the container's health view)
        prompts=prompts,
        pricing=pricing,
        level=level,
        meter=meter,
        knowledge=knowledge,
        response_cache=response_cache,
        tools=tools,
        outbox=outbox,
        dispatcher=dispatcher,
        telegram_service=TelegramService(),
        callback_secret=lambda run_id: callback_secret(secret, run_id),
        search=search,
    )
    container.stats = StatsService(runs, meter, pricing, container.health)
    return container


def get_container(request: Request) -> Container:
    return request.app.state.container
