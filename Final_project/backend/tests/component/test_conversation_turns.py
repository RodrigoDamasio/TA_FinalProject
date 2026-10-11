"""v1.2 conversations, component level (plan Improving_Chat_UI_UX §10): two-turn runs on the
real services with the fake LLM, fake search and the hashing embedder (0 real calls). The
Supervisor and the Writer see the earlier turn, the follow-up TASK stands alone, the report
cites only this run, search_conversation is offered from turn 2 and reads only its own
conversation, the submit rules, and memory exclusion."""

import re

import pytest
from fakes import USAGE, FakeLLM, delegate, final

from app.application.runs import SubmitCommand
from app.domain.errors import ConversationBusy, ConversationFull, ConversationNotFound, InvalidField
from app.domain.evidence import SourceKind
from app.domain.ports import LLMResponse, ToolCall
from app.domain.run import ResearchRun, RunStatus
from app.domain.transcript import EntryKind

FIRST = "When should I use RAG instead of fine-tuning?"
FOLLOW_UP = "And what about the cost?"


def ask(h, question: str, conversation_id: str | None = None, **kw) -> ResearchRun:
    run = h.submit(question, conversation_id=conversation_id, **kw)
    return h.get(run.id)


def tools_offered(llm: FakeLLM) -> list[list[str]]:
    return [[t.name for t in req.tools] for role, req in llm.requests if role == "researcher"]


def tool_records(run: ResearchRun):
    return [e.tool for e in run.transcript if e.kind is EntryKind.TOOL_CALL and e.tool]


# ---- the two-turn run -------------------------------------------------------------------------


def test_a_follow_up_sees_the_earlier_turn_and_writes_a_standalone_task(harness):
    h = harness()
    one = ask(h, FIRST)
    assert one.status is RunStatus.COMPLETED and one.turn == 1 and one.context_turns == 0
    before = len(h.llm.prompts("supervisor"))
    two = ask(h, FOLLOW_UP, one.conversation_id)
    assert two.status is RunStatus.COMPLETED, two.error
    assert (two.conversation_id, two.turn, two.context_turns) == (one.conversation_id, 2, 1)

    supervisor = h.llm.prompts("supervisor")[before]
    earlier = supervisor.split("# Earlier turns in this chat", 1)[1].split("# Research question")[0]
    assert 'kind="earlier_turn" turn="1"' in earlier and f"Question: {FIRST}" in earlier
    assert supervisor.index("# Earlier turns in this chat") < supervisor.index("# Research")
    assert "- conversation: the sources gathered in earlier turns" in supervisor
    assert not re.search(r"\[E\d+\]", earlier)  # numbers of turn 1 never reach turn 2

    task = next(e.decision for e in two.transcript if e.decision).delegations[0]
    assert task.worker == "researcher"
    assert "rag" in task.task.lower() and "fine-tun" in task.task.lower()  # standalone

    writer = h.llm.prompts("writer")[-1]
    assert "# Earlier turns in this chat (context only)" in writer
    assert 'kind="earlier_turn" turn="1"' in writer
    turn_one_prompt = h.llm.prompts("writer")[0]
    assert "(this is the first question of the chat)" in turn_one_prompt
    assert "Earlier turns are context only" in h.llm.systems("writer")[-1]


def test_the_follow_up_report_cites_only_its_own_evidence(harness):
    h = harness()
    one = ask(h, FIRST)
    assert len(one.evidence) >= 3
    h.llm.script = {
        "supervisor": [
            delegate("researcher", "Research the cost of RAG versus fine-tuning."),
            delegate("writer", "Write the report."),
            final("# Costs\n\nRAG is cheaper to update [E1][E99].\n\n## Costs\n\nDetails [E1]."),
        ],
        "writer": ["# Costs\n\nRAG costs [E1] and an old number [E42].\n\n## A\n\nText [E1]."],
    }
    two = ask(h, FOLLOW_UP, one.conversation_id)
    assert two.status is RunStatus.COMPLETED
    cited = {int(n) for n in re.findall(r"\[E(\d+)\]", two.final_output or "")}
    assert cited and cited <= {r.n for r in two.evidence}
    assert "unknown_citation" in [e.flag for e in two.transcript if e.flag]
    draft = two.latest_result("writer")
    assert draft is not None and "[E42]" not in draft.content


# ---- search_conversation ----------------------------------------------------------------------


def test_search_conversation_is_offered_only_from_turn_two(harness):
    h = harness()
    one = ask(h, FIRST)
    assert all("search_conversation" not in names for names in tools_offered(h.llm))
    offered_before = len(tools_offered(h.llm))
    two = ask(h, FOLLOW_UP, one.conversation_id)
    later = tools_offered(h.llm)[offered_before:]
    assert later and all("search_conversation" in names for names in later)
    # the demo Researcher tries it first, with the standalone TASK as its query
    first = tool_records(two)[0]
    assert first.tool == "search_conversation" and first.provider == "conversation"
    assert first.status == "ok" and "fine-tuning" in first.args["query"]
    assert SourceKind.CONVERSATION not in two.sources  # internal: never stored as requested


def test_it_is_not_offered_when_no_earlier_turn_has_evidence(harness):
    h = harness(
        llm=FakeLLM(script={"researcher": ["### Notes\n- From memory.\n\nGaps: everything."]})
    )
    one = ask(h, FIRST)
    assert one.status is RunStatus.COMPLETED and not one.evidence
    before = len(tools_offered(h.llm))
    two = ask(h, FOLLOW_UP, one.conversation_id)
    assert two.turn == 2 and two.context_turns == 1
    assert all("search_conversation" not in n for n in tools_offered(h.llm)[before:])


def test_hits_are_copied_with_their_original_kind_and_numbered_in_the_new_run(harness):
    h = harness()
    one = ask(h, "Summarize the history of the Python language.", sources=_wiki())
    assert {r.source_kind for r in one.evidence} == {SourceKind.WIKIPEDIA}
    two = ask(h, "and its creator?", one.conversation_id, sources=_wiki())
    record = tool_records(two)[0]
    assert record.tool == "search_conversation" and record.evidence_ns
    copied = [two.evidence_by_n(n) for n in record.evidence_ns]
    assert [r.n for r in copied] == list(range(1, len(copied) + 1))  # numbered in this run
    retriever = h.container.supervisor.retriever
    chunks = retriever.get([r.chunk_id for r in copied])
    originals = {
        (c.source_key, c.position): c for c in retriever.get([r.chunk_id for r in one.evidence])
    }
    for c in chunks:
        assert c.scope == f"run:{two.id}" and c.source_kind is SourceKind.WIKIPEDIA
        source = originals[(c.source_key, c.position)]
        assert (c.title, c.url, c.text) == (source.title, source.url, source.text)
    assert all(r.source_kind is SourceKind.WIKIPEDIA for r in copied)


def test_it_never_reads_another_conversation(harness):
    h = harness()
    a1 = ask(h, "How do vector databases index embeddings?")
    b1 = ask(h, FIRST)
    b2 = ask(h, FOLLOW_UP, b1.conversation_id)
    keys_a = {c.source_key for c in _chunks(h, a1)}
    keys_b = {c.source_key for c in _chunks(h, b1)}
    record = tool_records(b2)[0]
    assert record.tool == "search_conversation"
    reused = {c.source_key for c in _chunks(h, b2, record.evidence_ns)}
    assert reused and reused <= keys_b and not reused & keys_a


def test_a_repeated_conversation_search_is_answered_from_the_memo(harness):
    query = {"query": "rag fine-tuning cost"}
    call = ToolCall("c1", "search_conversation", query)
    tool_turn = LLMResponse(None, [call], None, USAGE, "stop")
    again = LLMResponse(None, [ToolCall("c2", "search_conversation", query)], None, USAGE, "stop")
    h = harness()
    one = ask(h, FIRST)
    h.llm.script = {"researcher": [tool_turn, again, "### Costs\n- Cheap [E1].\n\nGaps: none."]}
    two = ask(h, FOLLOW_UP, one.conversation_id)
    first, second = tool_records(two)[:2]
    assert first.status == "ok" and not first.repeat
    assert second.repeat and second.evidence_ns == first.evidence_ns


# ---- submit rules (plan §4.3) -----------------------------------------------------------------


def test_submit_rules_row_by_row(harness):
    h = harness(conversation_max_turns=4)
    service = h.container.service
    # no conversation id: a new conversation, turn 1, the normal 10-character minimum
    with pytest.raises(InvalidField):
        service.submit(SubmitCommand("and cost?"))
    one = ask(h, FIRST)
    assert one.turn == 1
    cid = one.conversation_id
    # unknown or swept id: 404
    with pytest.raises(ConversationNotFound):
        service.submit(SubmitCommand(FIRST, conversation_id="conv_" + "a" * 16))
    # otherwise: turn n+1, and a follow-up may be 2 characters
    two = ask(h, "ok", cid)
    assert two.turn == 2
    with pytest.raises(InvalidField):
        service.submit(SubmitCommand("k", conversation_id=cid))
    # a turn still queued, running or awaiting approval: 409 busy
    pending = ResearchRun.create("Pending turn", 5, False, False, conversation_id=cid, turn=3)
    for status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL):
        pending.status = status
        service.runs.save(pending)
        with pytest.raises(ConversationBusy):
            service.submit(SubmitCommand(FOLLOW_UP, conversation_id=cid))
    pending.status = RunStatus.CANCELLED
    service.runs.save(pending)
    assert ask(h, FOLLOW_UP, cid).turn == 4
    # the conversation has conversation_max_turns runs: 409 full
    with pytest.raises(ConversationFull):
        service.submit(SubmitCommand(FOLLOW_UP, conversation_id=cid))


# ---- memory -----------------------------------------------------------------------------------


def test_memory_never_recalls_runs_of_the_same_conversation(harness):
    h = harness()
    one = ask(h, FIRST, use_memory=True)
    assert one.status is RunStatus.COMPLETED
    related = "What does RAG cost compared with fine-tuning?"
    elsewhere = ask(h, related, use_memory=True)  # a new conversation recalls turn 1
    assert [m["run_id"] for m in elsewhere.memories] == [one.id]
    same = ask(h, related, one.conversation_id, use_memory=True)
    assert same.turn == 2 and one.id not in [m["run_id"] for m in same.memories]


# ---- helpers ----------------------------------------------------------------------------------


def _wiki() -> frozenset[SourceKind]:
    return frozenset({SourceKind.WIKIPEDIA})


def _chunks(h, run: ResearchRun, ns=None):
    refs = [r for r in run.evidence if ns is None or r.n in ns]
    return h.container.supervisor.retriever.get([r.chunk_id for r in refs])
