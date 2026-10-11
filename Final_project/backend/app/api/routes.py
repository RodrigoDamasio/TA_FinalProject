"""HTTP endpoints. Thin: validate, rate-limit, call a use case, shape the response."""

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from app.application.runs import SubmitCommand
from app.application.views import research_view
from app.domain.documents import valid_workspace_id
from app.domain.errors import InvalidWorkspace
from app.domain.evidence import REQUESTABLE_SOURCES
from app.domain.outbox import Channel
from app.domain.run import ResearchRun

from .dependencies import Container, get_container
from .problems import PROBLEM_RESPONSE, WaitTimeout
from .schemas import Accepted, RejectRequest, ResearchRequest

router = APIRouter()
logger = logging.getLogger("app.api")
SSE_POLL_S = 0.5
TERMINAL_EVENTS = {"completed", "failed", "cancelled"}


def _problems(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: PROBLEM_RESPONSE for code in codes}


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _view(c: Container, run: ResearchRun) -> dict[str, Any]:
    return research_view(run, c.pricing, demo=c.settings.fake)


# ---- observability ---------------------------------------------------------------------------


@router.get("/health", tags=["observability"])
def health(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Liveness + service level (full | reduced | minimal), models and today's usage."""
    return c.health()


@router.get("/stats", tags=["observability"])
def stats(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Cost and quota monitoring: calls today vs the limit, cache hit rate, tokens, a
    paid-tier cost equivalent, runs by status and run latency."""
    return c.stats.stats()


@router.get("/agents", tags=["agents"])
def agents(c: Container = Depends(get_container)) -> list[dict[str, str]]:
    """The Supervisor and the worker registry."""
    supervisor = {
        "name": "supervisor",
        "title": "Supervisor",
        "role": "supervisor",
        "description": "decides which worker acts next (DELEGATE/TASK) and writes the final "
        "answer (FINAL)",
    }
    return [supervisor] + [
        {"name": w.name, "title": w.title, "role": "worker", "description": w.description}
        for w in c.registry.all()
    ]


# ---- runs --------------------------------------------------------------------------------


def _field_error(pointer: str, detail: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", *pointer[2:].split("/")), "msg": detail}]
    )


@router.post(
    "/research",
    status_code=202,
    responses={200: {"description": "Finished run (with ?wait=true)"}}
    | _problems(400, 413, 422, 429, 503, 504),
    tags=["research"],
)
def start_research(
    body: ResearchRequest,
    request: Request,
    response: Response,
    wait: bool = Query(False, description="Block until the run ends (needs no approval)."),
    x_workspace_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> Accepted | dict[str, Any]:
    """Start research on a question. Async by default (202 + Location + SSE events); with
    `?wait=true` the finished run is returned (200) in the assignment's shape:
    `report`, `metadata`, `conversation_history`."""
    if wait and body.require_approval:
        raise _field_error("#/require_approval", "Must be false when wait=true.")
    if x_workspace_id is not None and not valid_workspace_id(x_workspace_id):
        raise InvalidWorkspace()
    cmd = SubmitCommand(
        question=body.question,
        depth=body.depth,
        sources=frozenset(body.sources) if body.sources is not None else REQUESTABLE_SOURCES,
        max_iterations=body.max_iterations,
        require_approval=body.require_approval,
        use_memory=body.use_memory,
        workspace_id=x_workspace_id,
        channel=Channel.API if body.callback_url else Channel.WEB,
        callback_url=str(body.callback_url) if body.callback_url else None,
        client_key=_client(request),
    )
    c.service.check_command(cmd)  # free checks first
    c.limiter.check(_client(request))  # only requests that would start a run count
    run = c.service.submit(cmd)
    status_url = f"/research/{run.id}"
    response.headers["Location"] = status_url
    if wait:
        run = c.service.wait(run.id, c.settings.sync_wait_timeout_s)
        if not run.done:
            raise WaitTimeout(run.id, c.settings.sync_wait_timeout_s)
        response.status_code = 200
        return _view(c, run)
    return Accepted(
        run_id=run.id,
        status=run.status.value,
        status_url=status_url,
        events_url=f"{status_url}/events",
        web_url=c.dispatcher.web_url(run.id),
        callback_secret=c.callback_secret(run.id) if cmd.callback_url else None,
    )


@router.get("/research/{run_id}", responses=_problems(404), tags=["research"])
def get_run(run_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return _view(c, c.service.get(run_id))


def _existing_run(run_id: str, c: Container = Depends(get_container)) -> str:
    """Checked before the stream starts, so an unknown id is a normal 404 problem."""
    return c.service.get(run_id).id


@router.get(
    "/research/{run_id}/events",
    response_class=EventSourceResponse,
    responses=_problems(404),
    tags=["research"],
)
async def run_events(
    request: Request,
    run_id: str = Depends(_existing_run),
    last_event_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> AsyncIterable[ServerSentEvent]:
    """Replays the run's events after `Last-Event-ID`, then follows new ones; ends after
    `completed`, `failed` or `cancelled`. Events come from the database, so a reconnect
    loses nothing."""
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    while True:
        events = await run_in_threadpool(c.service.runs.events_since, run_id, after)
        if not events:
            run = await run_in_threadpool(c.service.get, run_id)
            if run.done:  # the terminal event was already sent (or is read just now)
                events = await run_in_threadpool(c.service.runs.events_since, run_id, after)
                if not events:
                    return
        for event in events:
            after = event.id or after
            yield ServerSentEvent(data=event.data, event=event.type, id=str(event.id))
            if event.type in TERMINAL_EVENTS:
                return
        if await request.is_disconnected():
            return
        await asyncio.sleep(SSE_POLL_S)


@router.post("/research/{run_id}/approve", responses=_problems(404, 409), tags=["research"])
def approve(run_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return _view(c, c.service.approve(run_id))


@router.post(
    "/research/{run_id}/reject", responses=_problems(404, 409, 422, 503), tags=["research"]
)
def reject(
    run_id: str, body: RejectRequest | None = None, c: Container = Depends(get_container)
) -> dict[str, Any]:
    feedback = body.feedback.strip() if body and body.feedback else None
    return _view(c, c.service.reject(run_id, feedback or None))


@router.get("/research/{run_id}/deliveries", responses=_problems(404), tags=["research"])
def deliveries(run_id: str, c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    """Webhook delivery attempts (no payloads, no secrets, host only)."""
    c.service.get(run_id)  # 404 for unknown runs
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "event": m.event,
            "target_host": urlsplit(m.target).hostname if m.kind == "callback" else "telegram",
            "status": m.status.value,
            "attempts": m.attempts,
            "last_status": m.last_status,
            "last_error": m.last_error,
            "next_attempt_at": m.next_attempt_at if m.status.value == "pending" else None,
            "delivered_at": m.delivered_at,
        }
        for m in c.outbox.for_run(run_id)
    ]


@router.get("/tools", tags=["agents"])
def tools(c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    """The tools the Researcher can be offered (per run, filtered by `sources`)."""
    return c.tools.specs()


# ---- samples -------------------------------------------------------------------------------


@router.get("/samples", tags=["samples"])
def list_samples(c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    """Stored completed runs (0 LLM calls), replayable in the UI."""
    return c.samples.catalog()


@router.get("/samples/{sample_id}", responses=_problems(404), tags=["samples"])
def get_sample(sample_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.samples.get(sample_id)
