"""v1.2 follow-ups through the API (plan Improving_Chat_UI_UX §7, §10 API): the view of a
follow-up turn, search_conversation in the history, the internal family is not requestable,
and a run stored before v1.2 can be continued as its own conversation (0 real calls)."""

import json
import sqlite3

from app.domain.run import legacy_conversation_id
from app.infrastructure import database


def test_a_follow_up_view_reports_its_context_and_the_new_tool(api):
    a = api()
    first = a.run().json()
    second = a.run("and the cost?", conversation_id=first["conversation_id"]).json()
    view = a.client.get(f"/research/{second['run_id']}").json()
    assert (view["conversation_id"], view["turn"]) == (first["conversation_id"], 2)
    assert view["metadata"]["context_turns"] == 1 and view["status"] == "completed"
    tools = [e for e in view["conversation_history"] if e.get("action") == "TOOL_CALL"]
    assert tools and tools[0]["tool"] == "search_conversation"
    assert tools[0]["provider"] == "conversation"
    turns = a.client.get(f"/conversations/{first['conversation_id']}").json()["turns"]
    assert [t["turn"] for t in turns] == [1, 2]


def test_the_conversation_family_is_never_requestable(api):
    a = api()
    r = a.run(sources=["web", "conversation"])
    assert r.status_code == 422 and r.json()["type"].endswith("/problems/validation-error")


def test_a_run_stored_before_v12_can_be_continued(api):
    a = api()
    run_id = a.run().json()["run_id"]
    path = a.container.settings.database_path
    with sqlite3.connect(path) as conn:  # make it look like a v1.1 row
        (state,) = conn.execute("SELECT state_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        d = json.loads(state)
        for key in ("conversation_id", "turn", "context_turns"):
            d.pop(key)
        conn.execute(
            "UPDATE runs SET conversation_id = NULL, state_json = ? WHERE id = ?",
            (json.dumps(d), run_id),
        )
    database.init(path)  # the startup migration backfills the column
    legacy = legacy_conversation_id(run_id)
    follow = a.run("and the cost?", conversation_id=legacy)
    assert follow.status_code == 202, follow.json()
    assert follow.json()["turn"] == 2 and follow.json()["conversation_id"] == legacy
    view = a.client.get(f"/research/{follow.json()['run_id']}").json()
    assert view["metadata"]["context_turns"] == 1
