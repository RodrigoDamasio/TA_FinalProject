"""Composition root. Railway starts it with `uvicorn app.main:app`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, problems, routes, webhooks
from app.api.dependencies import Container, build_container
from app.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings)
        app.state.container.start()
        yield
        app.state.container.stop()

    app = FastAPI(
        title="Multi-Agent Research Assistant",
        description="A Supervisor agent coordinates Researcher, Writer, Reviewer and "
        "Fact-Checker workers (DELEGATE/TASK/FINAL protocol) to answer a research question "
        "with a sectioned, cited report. The Researcher uses tools (web search via SearXNG, "
        "Wikipedia, your documents) and every result is indexed into a per-run evidence store "
        "(agentic RAG). Live progress over SSE, Telegram and signed callback webhooks through "
        "a transactional outbox, human approval, memory, and production guards.",
        lifespan=lifespan,
    )

    # Order matters: Starlette makes the LAST added middleware the outermost.
    # The error middleware is added first so CORS wraps it and 500s keep CORS headers.
    app.add_middleware(problems.UnhandledErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID", "X-Workspace-Id"],
        expose_headers=["Retry-After", "Location"],
    )

    problems.register_problem_handlers(app)
    app.include_router(problems.router)
    app.include_router(routes.router)
    app.include_router(documents.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
