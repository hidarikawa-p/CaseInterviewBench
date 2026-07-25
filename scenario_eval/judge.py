"""Final-answer validation and judge invocation."""

from __future__ import annotations

import re
from typing import Any

from .config import extract_json, fill_template, word_count
from .providers import BaseAdapter

REQUIRED_KEYS = [
    "problem_formulation",
    "problem_evidence",
    "resolution",
    "resolution_rationale",
]


def validate_conclusion_obj(obj: dict, word_limit: int = 100) -> dict:
    """Validate an already-structured conclusion object (human target path).

    The human UI collects the four fields directly, so there is no
    ``<conclusion>`` tag to parse. This checks the required keys are present and
    non-empty and records word-limit overflows without failing on them.
    """
    issues: list[str] = []
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        issues.append(f"missing_keys={missing}")
        return {"valid": False, "conclusion_obj": obj, "issues": issues}

    empty = [k for k in REQUIRED_KEYS if not str(obj.get(k, "")).strip()]
    if empty:
        issues.append(f"empty_fields={empty}")

    for k in REQUIRED_KEYS:
        n = word_count(str(obj[k]))
        if n > word_limit:
            issues.append(f"word_limit_exceeded:{k}={n}")

    # Valid as long as every required field has content; empties fail validation.
    return {"valid": not empty and not missing, "conclusion_obj": obj, "issues": issues}


def validate_final_answer(text: str, word_limit: int = 100) -> dict:
    """Validate the ``<conclusion>`` block, JSON, and required keys.

    Returns a dict with: valid, conclusion_raw, conclusion_obj, issues.
    Word-limit overflows are recorded in ``issues`` but do not make the answer
    invalid (they are logged, not scored).
    """
    issues: list[str] = []
    m = re.search(r"<conclusion>(.*?)</conclusion>", text, re.S)
    if not m:
        return {
            "valid": False,
            "conclusion_raw": text.strip(),
            "conclusion_obj": None,
            "issues": ["no_conclusion_block"],
        }

    raw = m.group(1).strip()
    if len(re.findall(r"<conclusion>", text)) > 1:
        issues.append("multiple_conclusion_blocks")
    if text.strip() != m.group(0).strip():
        issues.append("text_outside_conclusion_block")
    if "```" in raw:
        issues.append("markdown_fence")

    try:
        obj = extract_json(raw)
    except ValueError:
        issues.append("invalid_json")
        return {"valid": False, "conclusion_raw": raw, "conclusion_obj": None, "issues": issues}

    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        issues.append(f"missing_keys={missing}")
        return {"valid": False, "conclusion_raw": raw, "conclusion_obj": obj, "issues": issues}

    extra = [k for k in obj if k not in REQUIRED_KEYS]
    if extra:
        issues.append(f"extra_keys={extra}")
    for k in REQUIRED_KEYS:
        n = word_count(str(obj[k]))
        if n > word_limit:
            issues.append(f"word_limit_exceeded:{k}={n}")

    return {"valid": True, "conclusion_raw": raw, "conclusion_obj": obj, "issues": issues}


def _format_fact_list(facts: list[dict]) -> str:
    if not facts:
        return "(None)"
    return "\n".join(
        f"- fact_id: {f['fact_id']}\n  content: {f['content']}" for f in facts
    )


def run_judge(
    adapter: BaseAdapter,
    judge_template: str,
    scenario: dict,
    acquired_facts: list[dict],
    unacquired_facts: list[dict],
    conclusion_raw: str,
) -> dict:
    """Fill the judge prompt, call the judge model, and parse its JSON verdict."""
    gt = scenario["ground_truth"]
    system = fill_template(
        judge_template,
        {
            "PUBLIC_CONTEXT": scenario["public_context"],
            "INITIAL_REQUEST": scenario["initial_request"],
            "EXPECTED_PROBLEM_FORMULATION": gt["expected_problem_formulation"],
            "PROBLEM_FORMULATION_RATIONALE": gt["problem_formulation_rationale"],
            "EXPECTED_RESOLUTION": gt["expected_resolution"],
            "RESOLUTION_RATIONALE": gt["resolution_rationale"],
            "ACQUIRED_HIDDEN_FACTS": _format_fact_list(acquired_facts),
            "UNACQUIRED_HIDDEN_FACTS": _format_fact_list(unacquired_facts),
            "PARTICIPANT_FINAL_ANSWER": conclusion_raw,
        },
    )
    raw, _meta = adapter.generate(
        system, [{"role": "user", "content": "Return the evaluation JSON now."}]
    )
    return extract_json(raw)


def zero_judge_result(reason: str) -> dict:
    """Synthetic all-zero verdict used when the final answer is malformed."""
    return {
        "evidence_mapping": {
            "problem_formulation_claims": [],
            "resolution_claims": [],
            "unsupported_or_invented_claims": [],
        },
        "scores": {
            k: {"score": 0, "justification": reason}
            for k in REQUIRED_KEYS
        },
        "cross_dimension_consistency": {"rating": "inconsistent", "justification": reason},
    }
