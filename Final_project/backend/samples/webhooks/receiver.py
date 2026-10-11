"""Verify Standard Webhooks deliveries from the research API (stdlib only).

Usage:  WEBHOOK_SECRET=whsec_… python receiver.py [port]      (default port 8001)

Checks the signature over "{webhook-id}.{webhook-timestamp}.{body}", refuses timestamps more
than 5 minutes away, and drops duplicates by webhook-id (delivery is at least once).
Answers 204 to duplicates too: a duplicate is a success the sender didn't hear about."""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer

TOLERANCE_S = 300
MAX_BODY = 2_000_000


def verify(secret: str, headers: Mapping[str, str], body: bytes, now: float | None = None) -> bool:
    msg_id = headers.get("webhook-id") or ""
    ts = headers.get("webhook-timestamp") or ""
    now = time.time() if now is None else now
    if not msg_id or not ts.isdigit() or abs(now - int(ts)) > TOLERANCE_S:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    signatures = (headers.get("webhook-signature") or "").split()
    return any(
        hmac.compare_digest(expected, sig.split(",", 1)[1])
        for sig in signatures
        if sig.startswith("v1,")
    )


def make_handler(
    secret: str, seen: set[str], log: Callable[[str], None] = print
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — the http.server method name
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(min(length, MAX_BODY))
            if not verify(secret, self.headers, body):
                log("rejected: bad signature")
                self.send_response(401)
                self.end_headers()
                return
            msg_id = self.headers["webhook-id"]
            if msg_id in seen:
                log(f"duplicate {msg_id}")
            else:
                seen.add(msg_id)
                event = json.loads(body)
                log(f"verified {event['type']} {event['data']['run_id']} id {msg_id}")
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # keep the output to one line per delivery

    return Handler


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    handler = make_handler(os.environ["WEBHOOK_SECRET"], set())
    print(f"listening on http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    main()
