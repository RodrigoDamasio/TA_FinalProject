"""The v1.1 domain types (BACKEND_PLAN §5, §24): ids, serialization, retry schedule."""

from datetime import UTC, datetime

from app.domain.depth import PROFILES, Depth
from app.domain.documents import (
    DEMO_WORKSPACE,
    Document,
    DocumentStatus,
    new_document_id,
    valid_workspace_id,
    workspace_scope,
)
from app.domain.evidence import (
    REQUESTABLE_SOURCES,
    EvidenceRef,
    SourceKind,
    chunk_id,
    content_hash,
    ref_from_dict,
    ref_to_dict,
)
from app.domain.outbox import (
    RETRY_SCHEDULE_S,
    OutboxMessage,
    OutboxStatus,
    new_message_id,
    next_attempt,
)
from app.domain.report import Reference, Report, ReportSection, report_from_dict, report_to_dict

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def test_evidence_ids_are_stable_and_dedup_ignores_whitespace():
    assert content_hash("a  b\nc") == content_hash("a b c")
    one = chunk_id("run:1", "https://x.org", 0, content_hash("text"))
    assert one == chunk_id("run:1", "https://x.org", 0, content_hash("text")) and len(one) == 24
    assert one != chunk_id("run:2", "https://x.org", 0, content_hash("text"))
    assert SourceKind.MEMORY not in REQUESTABLE_SOURCES and len(REQUESTABLE_SOURCES) == 3


def test_evidence_ref_round_trip_and_label():
    ref = EvidenceRef(3, "c" * 24, SourceKind.DOCUMENTS, "Guide", None, "guide.pdf", 4)
    assert ref.label == "E3" and ref_from_dict(ref_to_dict(ref)) == ref


def test_report_round_trip():
    report = Report(
        "RAG vs fine-tuning",
        "Summary [E1].",
        (ReportSection("Overview", "Text [E1]."),),
        (Reference("E1", "A page", "https://a.org", None, None, SourceKind.WEB, "snippet"),),
        Depth.BRIEF,
        12,
    )
    assert report_from_dict(report_to_dict(report)) == report


def test_depth_profiles_are_the_contract_numbers():
    assert PROFILES[Depth.BRIEF].words == (200, 400) and PROFILES[Depth.BRIEF].sections == (2, 3)
    assert [PROFILES[d].default_max_iterations for d in Depth] == [4, 5, 6]
    assert all(p.guidance for p in PROFILES.values())


def test_workspace_ids_and_scopes():
    assert valid_workspace_id("3f2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c")
    for bad in (None, "", "demo", "3F2B8C1E-9A4D-4C7E-8B1A-2D3E4F5A6B7C", "../etc", "x" * 36):
        assert not valid_workspace_id(bad)
    assert workspace_scope("abc") == "ws:abc" and workspace_scope(DEMO_WORKSPACE) == "demo"
    doc = Document(
        new_document_id(), DEMO_WORKSPACE, "a.md", "text/markdown", 10, "h",
        DocumentStatus.READY, None, 1, None, "t", None,
    )  # fmt: skip
    assert doc.demo and doc.id.startswith("doc_")


def test_outbox_messages_and_retry_schedule():
    msg = OutboxMessage.new("run_1", "callback", "research.completed", "https://a.org/h", now=NOW)
    assert msg.status is OutboxStatus.PENDING and msg.next_attempt_at == msg.created_at
    assert msg.id.startswith("msg_") and new_message_id() != msg.id
    assert next_attempt(1, NOW) == "2026-10-11T12:00:10+00:00"
    assert next_attempt(len(RETRY_SCHEDULE_S), NOW) is None  # give up: dead
