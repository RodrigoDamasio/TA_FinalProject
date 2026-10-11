"""Demo Reviewer and Fact-Checker (structured outputs). Owner: REVIEWER."""

import re

_SENTENCE = re.compile(r"^(\d+)\. \[cites ([\d, ]+)\] (.+)$", re.MULTILINE)


def review() -> dict:
    return {
        "verdict": "approve",
        "issues": [{"severity": "low", "text": "Demo review: a short example would help."}],
        "balance_flags": [],
    }


def fact_check(prompt: str) -> dict:
    claims = [
        {
            "sentence": s.strip(),
            "status": "supported",
            "source_n": int(ns.split(",")[0]),
            "note": "",
        }
        for _, ns, s in _SENTENCE.findall(prompt)
    ]
    return {"claims": claims[:12]}
