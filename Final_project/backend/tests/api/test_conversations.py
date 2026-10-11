"""v1.2 conversations (plan Improving_Chat_UI_UX §4, §7): ids, turns, submit rules, the
conversation endpoint, problems and the SQLite migration (0 real calls)."""

import json
import re
import sqlite3

from app.domain.run import legacy_conversation_id, run_from_dict
from app.infrastructure import database

CONV = re.compile(r"^conv_[a-f0-9]{16}$")


def test_a_run_without_conversation_starts_one(api):
    a = api()
    r = a.run()
    assert r.status_code == 202
    body = r.json()
    assert CONV.match(body["conversation_id"]) and body["turn"] == 1
    view = a.client.get(f"/research/{body['run_id']}").json()
    assert view["conversation_id"] == body["conversation_id"] and view["turn"] == 1
    assert view["metadata"]["context_turns"] == 0


def test_follow_ups_join_the_conversation_and_may_be_short(api):
    a = api()
    first = a.run().json()
    cid = first["conversation_id"]
    second = a.run("and cost?", conversation_id=cid)
    assert second.status_code == 202, second.json()
    assert second.json()["conversation_id"] == cid and second.json()["turn"] == 2
    conv = a.client.get(f"/conversations/{cid}").json()
    assert conv["max_turns"] == 30
    assert [(t["turn"], t["run_id"]) for t in conv["turns"]] == [
        (1, first["run_id"]),
        (2, second.json()["run_id"]),
    ]
    assert conv["turns"][0]["status"] == "completed" and conv["turns"][0]["title"]
    # a NEW conversation still needs a full question
    assert a.run("and cost?").status_code == 422


def test_conversation_problems(api):
    a = api(conversation_max_turns=2)
    unknown = a.run(conversation_id="conv_" + "0" * 16)
    assert unknown.status_code == 404
    assert unknown.json()["type"].endswith("/problems/conversation-not-found")
    assert a.client.get("/conversations/conv_" + "1" * 16).status_code == 404
    bad = a.run(conversation_id="conv_XYZ")
    assert bad.status_code == 422  # pattern
    cid = a.run().json()["conversation_id"]
    assert a.run("a follow-up", conversation_id=cid).status_code == 202
    full = a.run("one more", conversation_id=cid)
    assert full.status_code == 409 and full.json()["type"].endswith("/conversation-full")
    assert a.client.get("/problems/conversation-busy").status_code == 200


def test_a_turn_awaiting_approval_makes_the_conversation_busy(api):
    a = api()
    first = a.run(require_approval=True).json()
    assert a.client.get(f"/research/{first['run_id']}").json()["status"] == "awaiting_approval"
    busy = a.run("a follow-up", conversation_id=first["conversation_id"])
    assert busy.status_code == 409 and busy.json()["type"].endswith("/conversation-busy")
    a.client.post(f"/research/{first['run_id']}/approve")
    assert a.run("a follow-up", conversation_id=first["conversation_id"]).status_code == 202


def test_runs_stored_before_v12_become_their_own_conversation(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:  # the v1.1 table, without the column
        conn.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, state_json TEXT "
            "NOT NULL, duration_ms INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO runs VALUES ('run_abcdef012345', 'completed', '{}', NULL, 't', 't')"
        )
    database.init(path)
    database.init(path)  # idempotent
    with sqlite3.connect(path) as conn:
        (cid,) = conn.execute("SELECT conversation_id FROM runs").fetchone()
    assert cid == legacy_conversation_id("run_abcdef012345") == "conv_abcdef0123450000"


def test_old_state_json_loads_with_a_derived_conversation(api):
    a = api()
    run = a.container.service.get(a.run().json()["run_id"])
    from app.domain.run import run_to_dict

    d = run_to_dict(run)
    for key in ("conversation_id", "turn", "context_turns"):
        d.pop(key)
    old = run_from_dict(json.loads(json.dumps(d)))
    assert old.conversation_id == legacy_conversation_id(run.id) and old.turn == 1
