"""RFC 9457 Problem Details: the single place where errors become HTTP responses."""

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import get_settings
from app.domain.errors import (
    ConversationBusy,
    ConversationFull,
    ConversationNotFound,
    DemoReadOnly,
    DocumentNotFound,
    FeatureNotReady,
    FileTooLarge,
    IndexingBusy,
    InputTooLarge,
    InvalidCallbackUrl,
    InvalidField,
    InvalidTransition,
    InvalidWorkspace,
    LLMQuotaExceeded,
    LLMRequestRejected,
    LLMTimeout,
    LLMUnavailable,
    QueueFull,
    RunNotFound,
    SampleNotFound,
    UnsupportedMediaType,
    WebhookUnauthorized,
    WorkspaceQuotaExceeded,
)

from .guards import RateLimited

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger(__name__)


# ---- model -----------------------------------------------------------------


class FieldError(BaseModel):
    detail: str
    pointer: str  # JSON Pointer (RFC 6901) into the request body, e.g. "#/url"


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: list[FieldError] | None = None  # extension member (RFC 9457 §3.2)


# OpenAPI documentation for error responses
PROBLEM_RESPONSE: dict[str, Any] = {
    "model": Problem,
    "content": {PROBLEM_JSON: {}},
    "description": "RFC 9457 Problem Details",
}


# ---- catalog ---------------------------------------------------------------


@dataclass(frozen=True)
class ProblemType:
    slug: str
    status: int
    title: str
    description: str


def _p(slug: str, status: int, title: str, description: str) -> ProblemType:
    return ProblemType(slug, status, title, description)


VALIDATION_ERROR = _p(
    "validation-error",
    422,
    "Your request is not valid.",
    "One or more fields in the request body are invalid. See `errors` for each field.",
)
MALFORMED_REQUEST = _p(
    "malformed-request",
    400,
    "The request body is not valid JSON.",
    "The request body could not be parsed as JSON.",
)
RUN_NOT_FOUND = _p(
    "run-not-found",
    404,
    "Run not found.",
    "No run has this id. Finished runs are deleted after the retention period (30 days).",
)
CONVERSATION_NOT_FOUND = _p(
    "conversation-not-found",
    404,
    "Conversation not found.",
    "No conversation has this id. Conversations are deleted with their last run (30 days). "
    "Start a new chat.",
)
CONVERSATION_FULL = _p(
    "conversation-full",
    409,
    "This chat is full.",
    "A conversation holds at most CONVERSATION_MAX_TURNS questions. Start a new chat.",
)
CONVERSATION_BUSY = _p(
    "conversation-busy",
    409,
    "This chat is still answering.",
    "A question of this conversation is still queued, running or waiting for approval. "
    "Ask the next one when it finishes.",
)
SAMPLE_NOT_FOUND = _p(
    "sample-not-found", 404, "Sample not found.", "No sample run has this id. See GET /samples."
)
INVALID_TRANSITION = _p(
    "invalid-transition",
    409,
    "The run cannot do that now.",
    "Approve and reject only apply to a run that is awaiting approval. `detail` gives the "
    "run's current status.",
)
INPUT_TOO_LARGE = _p(
    "input-too-large",
    413,
    "The question is too long.",
    "The question is longer than the server accepts. `detail` states the limit.",
)
RATE_LIMITED = _p(
    "rate-limited",
    429,
    "Too many requests.",
    "This client started too many runs. Retry after the `Retry-After` delay.",
)
LLM_QUOTA_EXHAUSTED = _p(
    "llm-quota-exhausted",
    503,
    "The daily limit has been reached.",
    "The LLM provider's daily quota is used up (service level `minimal`). Samples and stored "
    "runs still work. Retry after the `Retry-After` delay (midnight Pacific time).",
)
LLM_UNAVAILABLE = _p(
    "llm-unavailable",
    503,
    "The research service is unavailable.",
    "The server is busy (too many queued runs) or the LLM provider could not be reached. "
    "Retry after `Retry-After`.",
)
WAIT_TIMEOUT = _p(
    "wait-timeout",
    504,
    "The run is still going.",
    "`?wait=true` gave up waiting. The run continues: follow the `Location` header.",
)
INVALID_WORKSPACE = _p(
    "invalid-workspace",
    400,
    "Missing or invalid workspace.",
    "Document requests need an `X-Workspace-Id` header: a lowercase UUIDv4 created by the "
    "browser. It identifies a private workspace that expires after 24 hours.",
)
DOCUMENT_NOT_FOUND = _p(
    "document-not-found",
    404,
    "Document not found.",
    "No document with this id in your workspace or the demo library.",
)
DEMO_READ_ONLY = _p(
    "demo-read-only", 409, "The demo library is read-only.", "Demo documents cannot be deleted."
)
UNSUPPORTED_MEDIA_TYPE = _p(
    "unsupported-media-type",
    415,
    "Unsupported file type.",
    "Only Markdown (.md), plain text (.txt) and PDF (.pdf) files are accepted. The type is "
    "checked by content, not only by the file name.",
)
FILE_TOO_LARGE = _p(
    "file-too-large", 413, "The file is too large.", "`detail` states the size limit."
)
WORKSPACE_QUOTA_EXCEEDED = _p(
    "workspace-quota-exceeded",
    507,
    "The workspace is full.",
    "The workspace reached its document count or size limit. Delete a document first.",
)
INDEXING_BUSY = _p(
    "indexing-busy",
    503,
    "Too many documents are being indexed.",
    "The indexing queue is full. Retry after the `Retry-After` delay.",
)
INVALID_CALLBACK_URL = _p(
    "invalid-callback-url",
    422,
    "The callback URL is not allowed.",
    "callback_url must be a public HTTPS address on port 443, without credentials. "
    "Private, loopback and link-local addresses are refused (SSRF protection).",
)
WEBHOOK_UNAUTHORIZED = _p(
    "webhook-unauthorized",
    401,
    "Webhook not authorized.",
    "The request did not carry the expected webhook secret header.",
)
FEATURE_NOT_READY = _p(
    "feature-not-ready",
    501,
    "Not implemented yet.",
    "This endpoint is part of the API contract but its implementation is still in progress.",
)
INTERNAL_ERROR = _p(
    "internal-error",
    500,
    "Internal server error.",
    "An unexpected error occurred. It has been logged.",
)

CATALOG = {
    p.slug: p
    for p in (
        VALIDATION_ERROR, MALFORMED_REQUEST, RUN_NOT_FOUND, SAMPLE_NOT_FOUND, INVALID_TRANSITION,
        CONVERSATION_NOT_FOUND, CONVERSATION_FULL, CONVERSATION_BUSY,
        INPUT_TOO_LARGE, RATE_LIMITED, LLM_QUOTA_EXHAUSTED, LLM_UNAVAILABLE, WAIT_TIMEOUT,
        INVALID_WORKSPACE, DOCUMENT_NOT_FOUND, DEMO_READ_ONLY, UNSUPPORTED_MEDIA_TYPE,
        FILE_TOO_LARGE, WORKSPACE_QUOTA_EXCEEDED, INDEXING_BUSY, INVALID_CALLBACK_URL,
        WEBHOOK_UNAUTHORIZED, FEATURE_NOT_READY, INTERNAL_ERROR,
    )
}  # fmt: skip


class WaitTimeout(Exception):
    def __init__(self, run_id: str, seconds: int) -> None:
        super().__init__(f"Run {run_id} did not finish within {seconds}s; it is still going.")
        self.run_id = run_id


# ---- building responses ----------------------------------------------------


def problem_response(
    request: Request,
    problem_type: ProblemType | None,
    *,
    status: int | None = None,
    detail: str | None = None,
    errors: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if problem_type is None:  # about:blank — title SHOULD be the HTTP reason phrase
        status = status or 500
        type_, title = "about:blank", HTTPStatus(status).phrase
    else:
        status = problem_type.status
        type_ = f"{get_settings().base_url}/problems/{problem_type.slug}"
        title = problem_type.title

    body = Problem(
        type=type_,
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        errors=errors,
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


def _json_pointer(loc: tuple[int | str, ...]) -> str:
    """("body", "url") -> "#/url" (RFC 6901, with ~ and / escaped)."""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in loc[1:]]
    return "#/" + "/".join(parts) if parts else "#"


def _friendly_message(error: dict[str, Any]) -> str:
    kind = error.get("type", "")
    if kind == "missing":
        return "This field is required."
    if kind in ("string_too_short", "too_short"):
        minimum = error.get("ctx", {}).get("min_length", 1)
        if minimum <= 1:
            return "Must not be empty."
        unit = "characters" if kind == "string_too_short" else "items"
        return f"Must be at least {minimum} {unit}."
    if kind == "enum":
        expected = error.get("ctx", {}).get("expected", "")
        return f"Must be one of: {expected}." if expected else "Unsupported value."
    return str(error.get("msg", "Invalid value.")).removeprefix("Value error, ")


# ---- handlers --------------------------------------------------------------


async def _validation_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — narrows the type
    errors = exc.errors()
    if any(e.get("type") == "json_invalid" for e in errors):
        return problem_response(request, MALFORMED_REQUEST)
    field_errors = [
        FieldError(detail=_friendly_message(e), pointer=_json_pointer(tuple(e["loc"])))
        for e in errors
    ]
    count = len(field_errors)
    return problem_response(
        request,
        VALIDATION_ERROR,
        detail=f"The request body has {count} invalid field{'s' if count != 1 else ''}.",
        errors=field_errors,
    )


def _retry_after(seconds: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, round(seconds)))}


def _simple(problem_type: ProblemType, retry_after: float | None = None):
    async def handler(request: Request, exc: Exception) -> Response:
        headers = _retry_after(retry_after) if retry_after else None
        return problem_response(request, problem_type, detail=str(exc), headers=headers)

    return handler


async def _invalid_field_handler(request: Request, exc: Exception) -> Response:
    pointer = exc.pointer if isinstance(exc, InvalidField) else "#"
    return problem_response(
        request,
        VALIDATION_ERROR,
        detail="The request body has 1 invalid field.",
        errors=[FieldError(detail=str(exc), pointer=pointer)],
    )


async def _wait_timeout_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, WaitTimeout)  # noqa: S101 — narrows the type
    return problem_response(
        request, WAIT_TIMEOUT, detail=str(exc), headers={"Location": f"/research/{exc.run_id}"}
    )


async def _invalid_callback_handler(request: Request, exc: Exception) -> Response:
    return problem_response(
        request,
        INVALID_CALLBACK_URL,
        detail=str(exc),
        errors=[FieldError(detail=str(exc), pointer="#/callback_url")],
    )


async def _rate_limited_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RateLimited)  # noqa: S101 — narrows the type
    return problem_response(
        request, RATE_LIMITED, detail=str(exc), headers=_retry_after(exc.retry_after_s)
    )


async def _quota_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, LLMQuotaExceeded)  # noqa: S101 — narrows the type
    when = "tomorrow" if exc.retry_after_s > 3600 else "shortly"
    return problem_response(
        request,
        LLM_QUOTA_EXHAUSTED,
        detail=f"The free-tier LLM quota is used up; try again {when}. Samples still work.",
        headers=_retry_after(exc.retry_after_s or 60),
    )


async def _unavailable_handler(request: Request, exc: Exception) -> Response:
    retry = exc.retry_after_s if isinstance(exc, LLMUnavailable) else 30
    return problem_response(request, LLM_UNAVAILABLE, detail=str(exc), headers=_retry_after(retry))


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 — narrows the type
    phrase = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if exc.detail and exc.detail != phrase else None
    return problem_response(
        request, None, status=exc.status_code, detail=detail, headers=exc.headers
    )


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """Turns unexpected exceptions into an `internal-error` problem.

    Must be registered BEFORE CORSMiddleware so CORS wraps it and 500s keep their
    CORS headers (Starlette makes the last-added middleware the outermost).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            return problem_response(
                request, INTERNAL_ERROR, detail="An unexpected error occurred. Please try again."
            )


def register_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(InvalidField, _invalid_field_handler)
    app.add_exception_handler(RunNotFound, _simple(RUN_NOT_FOUND))
    app.add_exception_handler(SampleNotFound, _simple(SAMPLE_NOT_FOUND))
    app.add_exception_handler(ConversationNotFound, _simple(CONVERSATION_NOT_FOUND))
    app.add_exception_handler(ConversationFull, _simple(CONVERSATION_FULL))
    app.add_exception_handler(ConversationBusy, _simple(CONVERSATION_BUSY))
    app.add_exception_handler(InvalidTransition, _simple(INVALID_TRANSITION))
    app.add_exception_handler(InputTooLarge, _simple(INPUT_TOO_LARGE))
    app.add_exception_handler(RateLimited, _rate_limited_handler)
    app.add_exception_handler(LLMQuotaExceeded, _quota_handler)
    app.add_exception_handler(LLMUnavailable, _unavailable_handler)
    app.add_exception_handler(LLMTimeout, _unavailable_handler)
    app.add_exception_handler(LLMRequestRejected, _unavailable_handler)
    app.add_exception_handler(QueueFull, _simple(LLM_UNAVAILABLE, retry_after=30))
    app.add_exception_handler(WaitTimeout, _wait_timeout_handler)
    app.add_exception_handler(InvalidWorkspace, _simple(INVALID_WORKSPACE))
    app.add_exception_handler(DocumentNotFound, _simple(DOCUMENT_NOT_FOUND))
    app.add_exception_handler(DemoReadOnly, _simple(DEMO_READ_ONLY))
    app.add_exception_handler(UnsupportedMediaType, _simple(UNSUPPORTED_MEDIA_TYPE))
    app.add_exception_handler(FileTooLarge, _simple(FILE_TOO_LARGE))
    app.add_exception_handler(WorkspaceQuotaExceeded, _simple(WORKSPACE_QUOTA_EXCEEDED))
    app.add_exception_handler(IndexingBusy, _simple(INDEXING_BUSY, retry_after=30))
    app.add_exception_handler(InvalidCallbackUrl, _invalid_callback_handler)
    app.add_exception_handler(WebhookUnauthorized, _simple(WEBHOOK_UNAUTHORIZED))
    app.add_exception_handler(FeatureNotReady, _simple(FEATURE_NOT_READY))
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)


# ---- problem type documentation (types SHOULD be dereferenceable) ---------

router = APIRouter(prefix="/problems", tags=["problems"])


@router.get("/{slug}", responses={404: PROBLEM_RESPONSE})
def describe_problem(slug: str) -> dict[str, str | int]:
    problem_type = CATALOG.get(slug)
    if problem_type is None:
        raise StarletteHTTPException(status_code=404)
    return {
        "type": slug,
        "title": problem_type.title,
        "status": problem_type.status,
        "description": problem_type.description,
    }
