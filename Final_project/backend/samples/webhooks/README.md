# Webhook receiver example

`receiver.py` verifies the signed `callback_url` deliveries of the research API ([Standard Webhooks](https://www.standardwebhooks.com/) v1). Python standard library only.

## What a delivery looks like

`POST` to your `callback_url` when the run ends (`research.completed`, `research.failed` or `research.cancelled`):

| Header | Value |
|---|---|
| `webhook-id` | The message id (`msg_…`). The same on every retry: use it to drop duplicates |
| `webhook-timestamp` | Unix seconds of **this** attempt |
| `webhook-signature` | `v1,` + base64(HMAC-SHA256(secret, `"{id}.{timestamp}.{body}"`)) |
| `content-type` · `user-agent` | `application/json` · `TA-Research-Webhooks/1` |

The body is compact JSON: `{"type", "timestamp", "data": {"run_id", "status", "links": {"self", "web"}, "report", "metadata", "error"?}}`. `report` is `null` unless the run completed; `error` is present only for failed runs. `GET links.self` for the full run, including the conversation history.

The secret (`whsec_…`) is returned **once**, as `callback_secret` in the `202` response of `POST /research`. Keep it.

**Delivery is at least once.** Failed attempts are retried after 10 s, 1 min, 5 min, 30 min and 2 h, then given up. Answer `2xx` quickly; `410 Gone` stops the retries. Redirects are not followed.

## Try it locally

```bash
# 1. the receiver
WEBHOOK_SECRET=whsec_… python samples/webhooks/receiver.py 8001

# 2. a backend that may call localhost (tests and demos only)
CALLBACK_ALLOW_PRIVATE=true LLM_MODE=fake uvicorn app.main:app

# 3. a run with a callback
curl -sS localhost:8000/research -H 'content-type: application/json' \
  -d '{"question": "What is retrieval-augmented generation?", "callback_url": "http://localhost:8001/hook"}'
```

The secret is derived per run, so the receiver can only know it after step 3: if the first attempt arrives before you restart the receiver with the `callback_secret` from the response, it prints `rejected: bad signature` and answers `401`, and the retry 10 s later (or 1 min after that) is verified. It prints `verified research.completed run_… id msg_…`, then `duplicate msg_…` if the same delivery arrives again.

**Against the deployed backend,** expose the receiver with `ngrok http 8001` and use the `https://…ngrok…/hook` URL as `callback_url`: production accepts only public HTTPS addresses on port 443.
