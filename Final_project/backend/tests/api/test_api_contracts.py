"""The v1.1 API surface: callbacks end to end, deliveries, tools, workspace header, the
document and Telegram stubs, and the new problem types (0 calls, 0 network)."""

import json

from fakes import FakeWebhookSender

from app.application.outbox.signing import verify

WS = "3f2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c"


def test_end_to_end_fake_run_with_a_signed_callback(api):
    sender = FakeWebhookSender()
    a = api(ports={"sender": sender}, callback_allow_private=True)
    r = a.run(callback_url="http://localhost:8001/hook", depth="brief")
    assert r.status_code == 202
    body = r.json()
    secret, run_id = body["callback_secret"], body["run_id"]
    assert secret.startswith("whsec_")
    view = a.client.get(f"/research/{run_id}").json()
    assert view["status"] == "completed" and view["channel"] == "api" and view["depth"] == "brief"
    actions = [e["action"] for e in view["conversation_history"]]
    assert actions[:3] == ["DELEGATE", "TOOL_CALL", "RESULT"] and actions[-1] == "FINAL"
    assert view["report"]["references"] and view["metadata"]["evidence"] >= 2

    pending = a.client.get(f"/research/{run_id}/deliveries").json()
    assert [d["status"] for d in pending] == ["pending"] and pending[0][
        "target_host"
    ] == "localhost"
    assert a.container.dispatcher.process_due() == 1
    url, payload, headers = sender.posts[0]
    assert url == "http://localhost:8001/hook"
    assert verify(
        secret, headers["webhook-id"], int(headers["webhook-timestamp"]), payload,
        headers["webhook-signature"],
    )  # fmt: skip
    data = json.loads(payload)
    assert data["type"] == "research.completed" and data["data"]["report"] == view["report"]
    delivered = a.client.get(f"/research/{run_id}/deliveries").json()[0]
    assert delivered["status"] == "delivered" and delivered["last_status"] == 200
    assert "secret" not in json.dumps(delivered) and "payload" not in delivered


def test_request_validation_and_new_problem_types(api):
    a = api(raise_errors=False)
    bad_url = a.run(callback_url="http://169.254.169.254/latest")
    assert bad_url.status_code == 422 and bad_url.json()["type"].endswith("/invalid-callback-url")
    assert bad_url.json()["errors"][0]["pointer"] == "#/callback_url"
    no_sources = a.run(sources=[])
    assert (
        no_sources.status_code == 422 and no_sources.json()["errors"][0]["pointer"] == "#/sources"
    )
    unknown = a.run(sources=["memory"])
    assert unknown.status_code == 422
    ws = a.client.post(
        "/research", json={"question": "A good question here"}, headers={"X-Workspace-Id": "x"}
    )
    assert ws.status_code == 400 and ws.json()["type"].endswith("/invalid-workspace")
    ok = a.client.post(
        "/research",
        json={"question": "A good question here", "sources": ["documents"]},
        headers={"X-Workspace-Id": WS},
    )
    assert ok.status_code == 202
    run = a.container.service.get(ok.json()["run_id"])
    assert run.workspace_id == WS and [t.tool for e in run.transcript if (t := e.tool)] == [
        "search_documents"
    ]
    assert a.client.get("/problems/feature-not-ready").json()["status"] == 501


def test_telegram_webhook_is_off_by_default_and_checks_its_secret(api):
    off = api(raise_errors=False)
    assert off.client.post("/webhooks/telegram", json={"update_id": 1}).status_code == 404
    on = api(raise_errors=False, telegram_mode="fake", telegram_webhook_secret="s3cret")
    wrong = on.client.post(
        "/webhooks/telegram", json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "nope"},
    )  # fmt: skip
    assert wrong.status_code == 401 and wrong.json()["type"].endswith("/webhook-unauthorized")
    right = on.client.post(
        "/webhooks/telegram", json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )  # fmt: skip
    assert right.status_code == 200 and right.json() == {}  # authenticated: always 200


def test_tools_and_health(api):
    a = api()
    tools = a.client.get("/tools").json()
    assert [t["name"] for t in tools] == [
        "search_web", "search_wikipedia", "search_documents", "read_result",
    ]  # fmt: skip
    assert {t["family"] for t in tools} == {"web", "wikipedia", "documents"}
    health = a.client.get("/health").json()
    assert health["search"] == {"mode": "fake", "available": True}
    assert health["outbox"]["pending"] == 0 and health["telegram"] == {
        "mode": "off", "configured": False, "secret_fingerprint": None,
    }  # fmt: skip
    assert a.client.get("/research/run_nope/deliveries").status_code == 404
