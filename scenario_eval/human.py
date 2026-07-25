"""Helpers for running a scenario with a human as the target.

The dialogue is driven interactively (e.g. by a Streamlit app) rather than by a
generation loop, so this module exposes the per-turn logic and final-answer
assembly as standalone functions. Everything downstream of the target - the
facilitator, judge, scoring, and result schema - is shared with the LLM path.
"""

from __future__ import annotations

import json

from .config import extract_json
from .facilitator import facilitate
from .judge import REQUIRED_KEYS
from .providers import BaseAdapter
from .violations import (
    FORMAT_VIOLATION_REPLY,
    ROLE_LEAKAGE_REPLY,
    check_question_format,
    check_severe_violations,
    extract_question_candidate,
)


def process_question(
    facilitator: BaseAdapter,
    public_context: str,
    hidden_facts: list[dict],
    disclosed_ids: set[int],
    raw_output: str,
) -> dict:
    """Process one submitted question exactly as the LLM loop does.

    Returns a dict with the facilitator reply, the decision, detected
    violations, and the sanitised question. Does not mutate ``disclosed_ids``;
    the caller applies ``disclose_fact_ids``.
    """
    sev_v = check_severe_violations(raw_output)
    fmt_v = check_question_format(raw_output)
    violations = sev_v + fmt_v
    question = extract_question_candidate(raw_output) if violations else raw_output.strip()
    sanitized = bool(violations) and raw_output.strip() != question

    if violations:
        if sev_v:
            reply = ROLE_LEAKAGE_REPLY
            note = f"severe violation {sev_v}; facilitator not consulted"
        else:
            reply = FORMAT_VIOLATION_REPLY + f" Detected: {', '.join(fmt_v)}."
            note = f"format violation {fmt_v}; facilitator not consulted"
        decision = {
            "too_broad": False,
            "qualifying_fact_ids": [],
            "best_fact_id": None,
            "disclose_fact_ids": [],
            "reasoning": note,
        }
    else:
        remaining = [f for f in hidden_facts if f["fact_id"] not in disclosed_ids]
        reply, decision = facilitate(facilitator, public_context, question, remaining)

    return {
        "reply": reply,
        "decision": decision,
        "severe_violations": sev_v,
        "format_violations": fmt_v,
        "question": question,
        "sanitized": sanitized,
    }


def build_conclusion_raw(fields: dict[str, str]) -> tuple[str, dict]:
    """Assemble a canonical conclusion JSON from the four separate answer fields.

    The human UI collects the four fields in dedicated inputs; this produces the
    same ``conclusion_raw`` string the judge and validator expect, so no
    ``<conclusion>`` tag or manual JSON formatting is required from the person.
    """
    obj = {k: (fields.get(k, "") or "").strip() for k in REQUIRED_KEYS}
    raw = json.dumps(obj, ensure_ascii=False, indent=2)
    return raw, obj
