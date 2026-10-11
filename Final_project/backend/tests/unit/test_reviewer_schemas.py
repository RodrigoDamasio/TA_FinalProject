"""REVIEWER: the Review and FactCheck twins (REVIEWER_PLAN §3.4)."""

import pytest
from pydantic import ValidationError

from app.domain.schemas import (
    ISSUE_CATEGORIES,
    ClaimVerdict,
    FactCheck,
    Review,
    ReviewIssue,
    claim_id,
    evidence_id,
)


def test_review_normalizes_what_flash_lite_varies():
    review = Review.model_validate(
        {
            "verdict": " Revise ",
            "issues": [
                {"severity": "HIGH", "category": "Depth fit", "section": "", "text": "Long."},
                {"severity": "low", "category": "task-fit", "section": "## Costs ", "text": "x"},
            ],
            "balance_flags": ["loaded"],
        }
    )
    assert review.verdict == "revise"
    first, second = review.issues
    assert (first.severity, first.category, first.section) == ("high", "depth_fit", None)
    assert (second.category, second.section, second.origin) == ("task_fit", "Costs", "model")
    assert "injection" in ISSUE_CATEGORIES


@pytest.mark.parametrize(
    "bad",
    [
        {"verdict": "maybe", "issues": [], "balance_flags": []},
        {"verdict": "approve", "issues": [], "balance_flags": ["x" * 121]},
        {"verdict": "approve", "issues": [], "balance_flags": ["a"] * 6},
    ],
)
def test_review_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        Review.model_validate(bad)


def test_review_issue_limits():
    issue = {"severity": "low", "category": "balance", "text": "x"}
    with pytest.raises(ValidationError):
        Review.model_validate({"verdict": "approve", "issues": [issue] * 9, "balance_flags": []})
    for bad in ({"category": "style"}, {"text": ""}, {"text": "x" * 401}, {"section": "s" * 121}):
        with pytest.raises(ValidationError):
            ReviewIssue.model_validate(issue | bad)


@pytest.mark.parametrize("raw", ["E3", "[E3]", "e3", "3", " [ e 3 ] ", 3])
def test_evidence_id_forms(raw):
    assert evidence_id(raw) == "E3"


def test_ids_that_cannot_be_normalized_are_left_alone():
    assert evidence_id("source three") == "source three"
    assert evidence_id(True) is True
    assert claim_id("c4") == "C4" and claim_id("[C 12]") == "C12" and claim_id(7) == 7


def test_claim_verdict_normalizes_and_validates():
    verdict = ClaimVerdict.model_validate(
        {"claim": "c2", "status": "Supported", "evidence_ids": ["[E3]", "e3", 5], "note": None}
    )
    assert verdict.claim == "C2" and verdict.status == "supported"
    assert verdict.evidence_ids == ["E3", "E5"] and verdict.note == ""
    for bad in (
        {"claim": "X1"},
        {"status": "maybe"},
        {"evidence_ids": ["E1", "E2", "E3", "E4", "E5"]},
        {"evidence_ids": ["page 3"]},
        {"note": "n" * 301},
        {"evidence_ids": "E1"},
    ):
        with pytest.raises(ValidationError):
            ClaimVerdict.model_validate({"claim": "C1", "status": "supported"} | bad)


def test_fact_check_caps_the_claims():
    one = {"claim": "C1", "status": "supported", "evidence_ids": [], "note": ""}
    assert len(FactCheck.model_validate({"claims": [one] * 12}).claims) == 12
    with pytest.raises(ValidationError):
        FactCheck.model_validate({"claims": [one] * 13})
