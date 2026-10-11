"""Message bodies, rendered at SEND time from the stored run (v1.1 §24). Step 3 version:
WEBHOOKS_PLAN fixes the exact Telegram texts. Owner: WEBHOOKS."""

import json
from datetime import datetime
from typing import Any

from app.domain.outbox import EVENT_ACCEPTED, EVENT_BOT_REPLY, EVENT_COMPLETED, OutboxMessage
from app.domain.run import ResearchRun

TELEGRAM_LIMIT = 4096


def callback_body(
    msg: OutboxMessage, run: ResearchRun, view: dict[str, Any], base_url: str, web_url: str
) -> bytes:
    payload = {
        "type": msg.event,
        "timestamp": msg.created_at,
        "data": {
            "run_id": run.id,
            "status": run.status.value,
            "links": {"self": f"{base_url}/research/{run.id}", "web": web_url},
            "report": view.get("report"),
            "metadata": view.get("metadata"),
            "error": run.error,
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def telegram_text(msg: OutboxMessage, run: ResearchRun | None, web_url: str) -> str:
    if msg.event == EVENT_BOT_REPLY or run is None:
        return str(msg.payload.get("text", ""))[:TELEGRAM_LIMIT]
    if msg.event == EVENT_ACCEPTED:
        return f"Working on it. Follow the agents live: {web_url}"
    if msg.event == EVENT_COMPLETED:
        tail = f"Full report: {web_url}"
        if run.report is not None:
            r = run.report
            refs = "\n".join(
                f"- {x.title}" + (f" ({x.url})" if x.url else "") for x in r.references[:5]
            )
            text = f"{r.title}\n\n{r.summary}\n\n" + (f"Sources:\n{refs}\n\n" if refs else "")
        else:  # completed without a parsed report: send the start of the final Markdown
            text = (run.final_output or "The research is complete.").strip() + "\n\n"
        return text[: TELEGRAM_LIMIT - len(tail) - 2] + tail
    reason = run.error or "the run did not finish"
    return f"Sorry, the research could not be completed ({reason}). Details: {web_url}"[
        :TELEGRAM_LIMIT
    ]


def unix(ts_iso: str | datetime) -> int:
    dt = datetime.fromisoformat(ts_iso) if isinstance(ts_iso, str) else ts_iso
    return int(dt.timestamp())
