"""Structured worker output (Labs 2-4 pattern): a lenient twin is sent to Gemini as the
response schema (no length limits, plain strings), the strict model is validated locally,
and a failure triggers one repair call with the exact errors (REVIEWER_PLAN §3.4).

`mode="before"` validators absorb what flash-lite tends to vary (case, `""` for "none",
`[E3]` / `e3` / `3` for an evidence id) so those never cost a repair call. The class names
`ReviewLLM` and `FactCheckLLM` are what the demo client and the test fake switch on."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

IssueCategory = Literal[
    "task_fit", "depth_fit", "structure", "unsupported_claim", "balance", "citation", "injection"
]
ISSUE_CATEGORIES: tuple[str, ...] = IssueCategory.__args__  # type: ignore[attr-defined]
ClaimStatus = Literal["supported", "unsupported", "contradicted"]

_EVIDENCE_ID = re.compile(r"^\[?\s*[Ee]?\s*(\d{1,4})\s*\]?$")
_CLAIM_ID = re.compile(r"^\[?\s*[Cc]\s*(\d{1,2})\s*\]?$")


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _token(value: Any) -> Any:
    """'Depth fit' / 'depth-fit' → 'depth_fit'."""
    return re.sub(r"[\s-]+", "_", value.strip().lower()) if isinstance(value, str) else value


def evidence_id(value: Any) -> Any:
    """'[E3]', 'e3', '3', 3 → 'E3'; anything else is returned as is (and fails later)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f"E{value}"
    if isinstance(value, str) and (m := _EVIDENCE_ID.match(value.strip())):
        return f"E{int(m.group(1))}"
    return value


def claim_id(value: Any) -> Any:
    if isinstance(value, str) and (m := _CLAIM_ID.match(value.strip())):
        return f"C{int(m.group(1))}"
    return value


# ---- Reviewer ------------------------------------------------------------------------


class ReviewIssueLLM(BaseModel):  # lenient: sent to Gemini
    severity: str
    category: str
    section: str  # "" = the whole report
    text: str


class ReviewLLM(BaseModel):
    verdict: str
    issues: list[ReviewIssueLLM]
    balance_flags: list[str]


class ReviewIssue(BaseModel):  # strict: validated locally
    severity: Literal["high", "medium", "low"]
    category: IssueCategory
    section: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1, max_length=400)
    origin: Literal["model", "check"] = "model"

    _severity = field_validator("severity", mode="before")(_lower)
    _category = field_validator("category", mode="before")(_token)

    @field_validator("section", mode="before")
    @classmethod
    def _section(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().lstrip("#").strip()
            return value or None
        return value


class Review(BaseModel):
    verdict: Literal["approve", "revise"]
    issues: list[ReviewIssue] = Field(max_length=8)
    balance_flags: list[Annotated[str, Field(max_length=120)]] = Field(max_length=5)

    _verdict = field_validator("verdict", mode="before")(_lower)


# ---- Fact-Checker ----------------------------------------------------------------------


class ClaimVerdictLLM(BaseModel):
    claim: str
    status: str
    evidence_ids: list[str]
    note: str


class FactCheckLLM(BaseModel):
    claims: list[ClaimVerdictLLM]


class ClaimVerdict(BaseModel):
    claim: str = Field(pattern=r"^C\d{1,2}$")
    status: ClaimStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)  # "E3"
    note: str = Field(default="", max_length=300)

    _claim = field_validator("claim", mode="before")(claim_id)
    _status = field_validator("status", mode="before")(_lower)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> Any:
        if isinstance(value, list):
            out: list[Any] = []
            for item in value:
                normalized = evidence_id(item)
                if normalized not in out:
                    out.append(normalized)
            return out
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _ids_shape(cls, value: list[str]) -> list[str]:
        bad = [v for v in value if not re.fullmatch(r"E\d{1,4}", v)]
        if bad:
            raise ValueError(f"evidence ids look like 'E3', got {bad[:3]}")
        return value

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, value: Any) -> Any:
        return "" if value is None else value


class FactCheck(BaseModel):
    claims: list[ClaimVerdict] = Field(max_length=12)
