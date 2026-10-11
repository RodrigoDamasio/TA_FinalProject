"""POST /webhooks/telegram (WEBHOOKS_PLAN §3.1). Thin: authenticate, cap, parse, de-duplicate,
hand over, answer `200 {}`. Telegram retries anything else, so once authenticated every
outcome (oversized, unparseable, duplicate, handled) is a 200. Nothing about the secret header
or the message text is logged. Owner: WEBHOOKS."""

import hashlib
import hmac
import logging
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.application.outbox.payloads import TelegramTexts
from app.application.telegram import ChatLimiter, TelegramService, TelegramUpdate
from app.domain.errors import WebhookUnauthorized
from app.domain.ports import UpdateLedger
from app.infrastructure.sqlite_store import SqliteUpdateLedger

from .dependencies import Container, get_container
from .problems import PROBLEM_RESPONSE

logger = logging.getLogger("app.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_UPDATE_BYTES = 256 * 1024
RUNS_PER_MINUTE = 2


def fingerprint(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def authorized(expected: str | None, given: str | None) -> bool:
    """Constant-time comparison of SHA-256 fingerprints (equal lengths, no length leak)."""
    if not expected or given is None:
        return False
    return hmac.compare_digest(fingerprint(expected), fingerprint(given))


@dataclass(frozen=True)
class TelegramEndpoint:
    service: TelegramService
    ledger: UpdateLedger


_wiring_lock = threading.Lock()


def telegram_endpoint(request: Request, c: Container) -> TelegramEndpoint:
    """The wired TelegramService and update ledger, built once per app. The step-3 container
    holds an unwired placeholder and no ledger; until the Shared wiring passes them (contract
    change request in the PR), they are built here from the container's own parts."""
    endpoint = getattr(request.app.state, "telegram_endpoint", None)
    if endpoint is not None:
        return endpoint
    with _wiring_lock:
        endpoint = getattr(request.app.state, "telegram_endpoint", None)
        if endpoint is None:
            s = c.settings
            service = c.telegram_service
            if not service.wired:
                texts = TelegramTexts(s.frontend_url, s.telegram_runs_per_day)
                service = TelegramService(
                    c.service,
                    c.outbox,
                    allowed_chats=s.telegram_allowed_chats,
                    texts=texts,
                    limiter=ChatLimiter(RUNS_PER_MINUTE, s.telegram_runs_per_day),
                )
            ledger = getattr(c, "telegram_ledger", None) or SqliteUpdateLedger(s.database_path)
            endpoint = TelegramEndpoint(service, ledger)
            request.app.state.telegram_endpoint = endpoint
    return endpoint


async def read_capped(request: Request, limit: int) -> bytes | None:
    """The body, or None when it is larger than `limit` (read stops there)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


@router.post("/telegram", responses={c: PROBLEM_RESPONSE for c in (401, 404)})
async def telegram(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    s = c.settings
    if s.telegram_mode == "off":
        raise HTTPException(status_code=404)  # the route doesn't exist for scanners
    if not authorized(s.telegram_webhook_secret, x_telegram_bot_api_secret_token):
        raise WebhookUnauthorized()  # nothing parsed, nothing written
    body = await read_capped(request, MAX_UPDATE_BYTES)
    if body is None:
        logger.warning("telegram update oversized")
        return {}  # a 413 would make Telegram retry forever
    try:
        update = TelegramUpdate.model_validate_json(body)
    except ValidationError:
        logger.warning("telegram update unparseable bytes=%d", len(body))
        return {}
    endpoint = telegram_endpoint(request, c)
    if await run_in_threadpool(endpoint.ledger.seen, update.update_id):
        logger.info("telegram update=%d duplicate", update.update_id)
        return {}
    await run_in_threadpool(endpoint.service.handle, update)  # never raises
    return {}
