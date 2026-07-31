"""Assembles the common result dict shared by the LLM and human-target paths.

Both the automated CLI loop and the Streamlit human UI accumulate the same
per-turn state (transcript, disclosures, violation counters) and then call
``build_result`` so that the saved output has an identical schema regardless of
whether the target was a model or a person.
"""

from __future__ import annotations

from typing import Optional

from .scoring import compute_scores


def build_result(
    *,
    scenario_name: str,
    target_label: str,
    target_provider: str,
    target_model: str,
    facilitator_label: str,
    judge_label: str,
    transcript: list[dict],
    final_answer_text: str,
    conclusion_raw: str,
    conclusion_obj: Optional[dict],
    format_valid: bool,
    format_retried: bool,
    format_issues: list,
    judge_result: dict,
    judge_skipped: bool,
    disclosed_ids: set[int],
    first_disclosure: dict[int, int],
    required_pf: set[int],
    required_res: set[int],
    n_hidden_facts: int,
    q_count: int,
    max_questions: int,
    n_format_violations: int,
    n_severe_violations: int,
    n_violating_turns: int,
    n_sanitized: int,
    n_multi_qualifying: int,
) -> dict:
    """Return the serialisable result dict (same schema for LLM and human runs)."""
    scored = compute_scores(
        judge_result,
        disclosed_ids,
        first_disclosure,
        required_pf,
        required_res,
        n_hidden_facts,
        q_count,
        max_questions,
    )
    compliance_rate = 1.0 - n_violating_turns / q_count if q_count else 0.0

    return {
        "scenario_file": scenario_name,
        "models": {
            "target": target_label,
            "facilitator": facilitator_label,
            "judge": judge_label,
        },
        "target_provider": target_provider,
        "target_model": target_model,
        "transcript": transcript,
        "final_answer_raw": final_answer_text,
        "conclusion": conclusion_obj,
        "format_valid": format_valid,
        "format_retried": format_retried,
        "format_issues": format_issues,
        "n_format_violations": n_format_violations,
        "n_severe_violations": n_severe_violations,
        "n_violating_turns": n_violating_turns,
        "n_sanitized_turns": n_sanitized,
        "compliance_rate": round(compliance_rate, 3),
        "had_severe_violation": n_severe_violations > 0,
        "had_format_violation": n_format_violations > 0,
        "had_any_violation": n_violating_turns > 0,
        "severe_violation_turns": [
            t["index"] for t in transcript if t["severe_violations"]
        ],
        "format_violation_turns": [
            t["index"] for t in transcript
            if t["format_violations"] and not t["severe_violations"]
        ],
        "n_multi_qualifying": n_multi_qualifying,
        "disclosure_policy": "one_fact_per_question",
        "violation_policy": "turn_consumption_only_traced",
        "disclosed_fact_ids": sorted(disclosed_ids),
        "first_disclosure": {str(k): v for k, v in sorted(first_disclosure.items())},
        "question_count": q_count,
        # Persisted so a later re-judge can reproduce the efficiency score, which
        # depends on the question limit in force during the original run.
        "max_questions": max_questions,
        "judge_skipped": judge_skipped,
        "judge_result": judge_result,
        "efficiency_components": scored["components"],
        "scores": scored["scores"],
    }
