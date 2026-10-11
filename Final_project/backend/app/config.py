import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    google_api_key: str | None
    gemini_model: str
    llm_mode: str  # gemini | fake
    llm_min_interval_s: float
    llm_timeout_s: int
    fake_llm_delay_s: float
    model_supervisor: str
    model_workers: str
    database_path: str
    default_max_iterations: int
    max_iterations_limit: int
    question_min_chars: int
    question_max_chars: int
    delegation_task_max_chars: int
    max_parallel_delegations: int
    revision_iterations: int
    approval_timeout_min: int
    supervisor_context_tokens: int
    worker_context_tokens: int
    wiki_mode: str  # live | fake
    wiki_lang: str
    wiki_max_pages: int
    wiki_max_chars_per_page: int
    wiki_cache_ttl_h: int
    wiki_timeout_s: float
    memory_enabled: bool
    memory_recall_limit: int
    prompt_secret: str | None
    rate_limit_runs_per_minute: int
    rate_limit_runs_per_day: int
    run_retention_days: int
    conversation_max_turns: int  # v1.2
    max_queued_jobs: int
    sync_wait_timeout_s: int
    llm_daily_request_limit: int
    price_input_per_m: float
    price_output_per_m: float
    runner_mode: str  # thread | inline (tests)
    sweep_every_s: float
    base_url: str
    frontend_origins: list[str]
    # ---- final project (BACKEND_PLAN §6, v1.1 §24) ----
    default_depth: str
    llm_run_budget_per_iteration: int
    llm_run_budget_base: int
    writer_context_tokens: int
    researcher_max_tool_calls: int
    evidence_top_k: int
    evidence_snippet_chars: int
    # RAG
    chroma_path: str
    models_path: str
    embed_mode: str  # onnx | fake
    embed_model: str
    rerank_model: str  # "" = off
    onnx_threads: int
    embed_batch_size: int
    chunk_max_chars: int
    chunk_min_chars: int
    chunk_overlap: float
    # search and fetching
    search_mode: str  # live | fake
    searxng_url: str
    search_max_results: int
    search_timeout_s: float
    search_cache_ttl_h: int
    fetch_timeout_s: float
    fetch_max_bytes: int
    fetch_max_redirects: int
    read_max_chars: int
    # documents
    upload_max_bytes: int
    pdf_max_pages: int
    workspace_max_docs: int
    workspace_max_bytes: int
    workspace_ttl_h: int
    demo_library_path: str
    rate_limit_uploads_per_hour: int
    # webhooks and Telegram
    telegram_mode: str  # live | fake | off
    telegram_bot_token: str | None
    telegram_webhook_secret: str | None
    telegram_allowed_chats: list[str]
    telegram_runs_per_day: int
    frontend_url: str
    outbox_poll_s: float
    outbox_max_attempts: int
    webhook_timeout_s: float
    callback_allow_private: bool

    @property
    def fake(self) -> bool:
        return self.llm_mode == "fake"

    def model_label(self, model: str) -> str:
        return "demo" if self.fake else model


def get_settings() -> Settings:
    llm_mode = os.getenv("LLM_MODE", "gemini")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    return Settings(
        google_api_key=os.getenv("GOOGLE_API_KEY") or None,
        gemini_model=model,
        llm_mode=llm_mode,
        llm_min_interval_s=_float("LLM_MIN_INTERVAL_S", 4.0),
        llm_timeout_s=_int("LLM_TIMEOUT_S", 90),
        fake_llm_delay_s=_float("FAKE_LLM_DELAY_S", 0.0),
        model_supervisor=os.getenv("MODEL_SUPERVISOR") or model,
        model_workers=os.getenv("MODEL_WORKERS") or model,
        database_path=os.getenv("DATABASE_PATH", "research.db"),
        default_max_iterations=_int("DEFAULT_MAX_ITERATIONS", 5),
        max_iterations_limit=_int("MAX_ITERATIONS_LIMIT", 8),
        question_min_chars=_int("QUESTION_MIN_CHARS", 10),
        question_max_chars=_int("QUESTION_MAX_CHARS", 2000),
        delegation_task_max_chars=_int("DELEGATION_TASK_MAX_CHARS", 1000),
        max_parallel_delegations=_int("MAX_PARALLEL_DELEGATIONS", 2),
        revision_iterations=_int("REVISION_ITERATIONS", 2),
        approval_timeout_min=_int("APPROVAL_TIMEOUT_MIN", 30),
        supervisor_context_tokens=_int("SUPERVISOR_CONTEXT_TOKENS", 6000),
        worker_context_tokens=_int("WORKER_CONTEXT_TOKENS", 5000),
        wiki_mode=os.getenv("WIKI_MODE", "fake" if llm_mode == "fake" else "live"),
        wiki_lang=os.getenv("WIKI_LANG", "en"),
        wiki_max_pages=_int("WIKI_MAX_PAGES", 3),
        wiki_max_chars_per_page=_int("WIKI_MAX_CHARS_PER_PAGE", 6000),
        wiki_cache_ttl_h=_int("WIKI_CACHE_TTL_H", 168),
        wiki_timeout_s=_float("WIKI_TIMEOUT_S", 8),
        memory_enabled=_bool("MEMORY_ENABLED", True),
        memory_recall_limit=_int("MEMORY_RECALL_LIMIT", 3),
        prompt_secret=os.getenv("PROMPT_SECRET") or None,
        rate_limit_runs_per_minute=_int("RATE_LIMIT_RUNS_PER_MINUTE", 3),
        rate_limit_runs_per_day=_int("RATE_LIMIT_RUNS_PER_DAY", 15),
        run_retention_days=_int("RUN_RETENTION_DAYS", 30),
        conversation_max_turns=_int("CONVERSATION_MAX_TURNS", 30),
        max_queued_jobs=_int("MAX_QUEUED_JOBS", 5),
        sync_wait_timeout_s=_int("SYNC_WAIT_TIMEOUT_S", 180),
        llm_daily_request_limit=_int("LLM_DAILY_REQUEST_LIMIT", 900),
        price_input_per_m=_float("PRICE_INPUT_PER_M", 0.10),
        price_output_per_m=_float("PRICE_OUTPUT_PER_M", 0.40),
        runner_mode=os.getenv("RUNNER_MODE", "thread"),
        sweep_every_s=_float("SWEEP_EVERY_S", 60),
        base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
        frontend_origins=[
            origin.strip().rstrip("/")
            for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
            if origin.strip()
        ],
        default_depth=os.getenv("DEFAULT_DEPTH", "detailed"),
        llm_run_budget_per_iteration=_int("LLM_RUN_BUDGET_PER_ITERATION", 3),
        llm_run_budget_base=_int("LLM_RUN_BUDGET_BASE", 5),
        writer_context_tokens=_int("WRITER_CONTEXT_TOKENS", 12000),
        researcher_max_tool_calls=_int("RESEARCHER_MAX_TOOL_CALLS", 3),
        evidence_top_k=_int("EVIDENCE_TOP_K", 6),
        evidence_snippet_chars=_int("EVIDENCE_SNIPPET_CHARS", 900),
        chroma_path=os.getenv("CHROMA_PATH", "chroma"),
        models_path=os.getenv("MODELS_PATH", "models"),
        embed_mode=os.getenv("EMBED_MODE", "fake" if llm_mode == "fake" else "onnx"),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        rerank_model=os.getenv("RERANK_MODEL", ""),
        onnx_threads=_int("ONNX_THREADS", 2),
        embed_batch_size=_int("EMBED_BATCH_SIZE", 4),
        chunk_max_chars=_int("CHUNK_MAX_CHARS", 1400),
        chunk_min_chars=_int("CHUNK_MIN_CHARS", 400),
        chunk_overlap=_float("CHUNK_OVERLAP", 0.15),
        search_mode=os.getenv("SEARCH_MODE", "fake" if llm_mode == "fake" else "live"),
        searxng_url=os.getenv("SEARXNG_URL", "http://searxng.railway.internal:8080").rstrip("/"),
        search_max_results=_int("SEARCH_MAX_RESULTS", 8),
        search_timeout_s=_float("SEARCH_TIMEOUT_S", 8),
        search_cache_ttl_h=_int("SEARCH_CACHE_TTL_H", 24),
        fetch_timeout_s=_float("FETCH_TIMEOUT_S", 10),
        fetch_max_bytes=_int("FETCH_MAX_BYTES", 2_000_000),
        fetch_max_redirects=_int("FETCH_MAX_REDIRECTS", 3),
        read_max_chars=_int("READ_MAX_CHARS", 60_000),
        upload_max_bytes=_int("UPLOAD_MAX_BYTES", 5 * 1024 * 1024),
        pdf_max_pages=_int("PDF_MAX_PAGES", 150),
        workspace_max_docs=_int("WORKSPACE_MAX_DOCS", 10),
        workspace_max_bytes=_int("WORKSPACE_MAX_BYTES", 20 * 1024 * 1024),
        workspace_ttl_h=_int("WORKSPACE_TTL_H", 24),
        demo_library_path=os.getenv("DEMO_LIBRARY_PATH", "samples/library"),
        rate_limit_uploads_per_hour=_int("RATE_LIMIT_UPLOADS_PER_HOUR", 10),
        telegram_mode=os.getenv("TELEGRAM_MODE", "off"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET") or None,
        telegram_allowed_chats=_list("TELEGRAM_ALLOWED_CHATS"),
        telegram_runs_per_day=_int("TELEGRAM_RUNS_PER_DAY", 10),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/"),
        outbox_poll_s=_float("OUTBOX_POLL_S", 1),
        outbox_max_attempts=_int("OUTBOX_MAX_ATTEMPTS", 6),
        webhook_timeout_s=_float("WEBHOOK_TIMEOUT_S", 10),
        callback_allow_private=_bool("CALLBACK_ALLOW_PRIVATE", False),
    )
