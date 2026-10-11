"""Demo Reviewer and Fact-Checker (structured outputs in the REVIEWER_PLAN §3.4 shapes).
Owner: REVIEWER."""

import re

# A claim line of fact_checker_task.md: "C3 [cites E2, E5] (Costs) … → check against: …"
_CLAIM = re.compile(r"^(C\d{1,2}) \[cites (E\d{1,4})", re.MULTILINE)


def review() -> dict:
    return {
        "verdict": "approve",
        "issues": [
            {
                "severity": "low",
                "category": "task_fit",
                "section": "",
                "text": "Demo review: a short example would help.",
            }
        ],
        "balance_flags": [],
    }


def fact_check(prompt: str) -> dict:
    """Every claim of the prompt is supported by the first evidence it cites."""
    claims = [
        {"claim": claim, "status": "supported", "evidence_ids": [first], "note": ""}
        for claim, first in _CLAIM.findall(prompt)
    ]
    return {"claims": claims[:12]}
