"""POST /webhooks/telegram (step 3 STUB): 404 when TELEGRAM_MODE=off, 401 without the right
secret header, then hands the update to TelegramService. Owner: WEBHOOKS."""

import hmac
from typing import Any

from fastapi import APIRouter, Body, Depends, Header
from starlette.exceptions import HTTPException

from app.domain.errors import WebhookUnauthorized

from .dependencies import Container, get_container
from .problems import PROBLEM_RESPONSE

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram", responses={c: PROBLEM_RESPONSE for c in (401, 404, 501)})
def telegram(
    update: dict[str, Any] = Body(...),
    x_telegram_bot_api_secret_token: str | None = Header(None),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    s = c.settings
    if s.telegram_mode == "off":
        raise HTTPException(status_code=404)
    expected = s.telegram_webhook_secret or ""
    given = x_telegram_bot_api_secret_token or ""
    if not expected or not hmac.compare_digest(expected, given):
        raise WebhookUnauthorized()
    c.telegram_service.handle(update)
    return {}
