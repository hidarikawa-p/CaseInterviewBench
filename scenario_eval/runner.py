"""Runs a single scenario end to end: dialogue loop, judging, and scoring."""

from __future__ import annotations

from typing import Any

from .config import Settings, fill_template
from .facilitator import facilitate
from .judge import run_judge, validate_final_answer, zero_judge_result
from .providers import BaseAdapter
from .results import build_result
from .scoring import compute_scores
from .violations import (
    FORMAT_VIOLATION_REPLY,
    LIMIT_PROMPT,
    ROLE_LEAKAGE_REPLY,
    STRICT_RETRY_PROMPT,
    check_question_format,
    check_severe_violations,
    extract_question_candidate,
)

KICKOFF = "Please begin the question phase. Output your first question now."


def _required_sets(hidden_facts: list[dict]) -> tuple[set[int], set[int]]:
    pf = {f["fact_id"] for f in hidden_facts if f.get("required_for_problem_formulation")}
    res = {f["fact_id"] for f in hidden_facts if f.get("required_for_resolution")}
    return pf, res


def run_scenario(
    scenario: dict,
    scenario_name: str,
    settings: Settings,
    guideline_template: str,
    judge_template: str,
    target: BaseAdapter,
    facilitator: BaseAdapter,
    judge: BaseAdapter,
) -> dict:
    """Execute one scenario and return the full result dict (ready to serialise)."""
    public_context = scenario["public_context"]
    initial_request = scenario["initial_request"]
    hidden_facts = scenario["hidden_facts"]
    required_pf, required_res = _required_sets(hidden_facts)
    max_q = settings.max_questions

    target_system = fill_template(
        guideline_template,
        {"PUBLIC_CONTEXT": public_context, "INITIAL_REQUEST": initial_request},
    )

    messages: list[dict] = [{"role": "user", "content": KICKOFF}]
    transcript: list[dict] = []
    disclosed_ids: set[int] = set()
    first_disclosure: dict[int, int] = {}
    q_count = 0
    n_format_violations = 0
    n_severe_violations = 0
    n_violating_turns = 0
    n_multi_qualifying = 0
    n_sanitized = 0
    final_answer_text: str | None = None

    while q_count < max_q:
        raw_out, meta = target.generate(target_system, messages)

        if "<conclusion>" in raw_out:
            messages.append({"role": "assistant", "content": raw_out})
            final_answer_text = raw_out
            break

        q_count += 1
        sev_v = check_severe_violations(raw_out)
        fmt_v = check_question_format(raw_out)
        violations = sev_v + fmt_v
        question = extract_question_candidate(raw_out) if violations else raw_out.strip()
        sanitized = bool(violations) and raw_out.strip() != question

        # On any violation, store only the extracted question in history so that
        # fabricated "interviewer" text cannot re-enter the participant's context.
        messages.append(
            {"role": "assistant", "content": question if violations else raw_out}
        )
        if sanitized:
            n_sanitized += 1

        if violations:
            n_violating_turns += 1
            if sev_v:
                n_severe_violations += 1
                reply = ROLE_LEAKAGE_REPLY
                note = f"severe violation {sev_v}; facilitator not consulted"
            else:
                n_format_violations += 1
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
            reply, dec = facilitate(facilitator, public_context, question, remaining)
            decision = dec
            if len(dec["qualifying_fact_ids"]) > 1:
                n_multi_qualifying += 1
            for fid in dec["disclose_fact_ids"]:
                first_disclosure.setdefault(fid, q_count)
            disclosed_ids |= set(dec["disclose_fact_ids"])

        transcript.append(
            {
                "index": q_count,
                "raw_output": raw_out,
                "question": question,
                "answer": reply,
                "severe_violations": sev_v,
                "format_violations": fmt_v,
                "history_sanitized": sanitized,
                "stop_reason": meta.get("stop_reason"),
                "dropped_blocks": meta.get("dropped_blocks"),
                "too_broad": decision["too_broad"],
                "qualifying_fact_ids": decision["qualifying_fact_ids"],
                "disclosed_fact_ids": decision["disclose_fact_ids"],
                "facilitator_reasoning": decision["reasoning"],
                "cumulative_disclosed": sorted(disclosed_ids),
            }
        )

        if q_count >= max_q:
            messages.append({"role": "user", "content": reply + "\n\n" + LIMIT_PROMPT})
        else:
            messages.append({"role": "user", "content": reply})

    if final_answer_text is None:
        final_answer_text, _ = target.generate(target_system, messages)
        messages.append({"role": "assistant", "content": final_answer_text})

    # Validate the final answer; allow one strict retry.
    validation = validate_final_answer(final_answer_text, settings.word_limit)
    retried = False
    if not validation["valid"]:
        retried = True
        messages.append({"role": "user", "content": STRICT_RETRY_PROMPT})
        final_answer_text, _ = target.generate(target_system, messages)
        messages.append({"role": "assistant", "content": final_answer_text})
        validation = validate_final_answer(final_answer_text, settings.word_limit)

    format_valid = validation["valid"]
    conclusion_raw = validation["conclusion_raw"]
    conclusion_obj = validation["conclusion_obj"]
    format_issues = validation["issues"]

    acquired = [f for f in hidden_facts if f["fact_id"] in disclosed_ids]
    unacquired = [f for f in hidden_facts if f["fact_id"] not in disclosed_ids]

    if format_valid:
        judge_result = run_judge(
            judge, judge_template, scenario, acquired, unacquired, conclusion_raw
        )
        judge_skipped = False
    else:
        reason = (
            f"Final answer did not conform to the required <conclusion> format after one "
            f"strict retry ({format_issues}); scored 0 by rule without judge evaluation."
        )
        judge_result = zero_judge_result(reason)
        judge_skipped = True

    scored = compute_scores(
        judge_result,
        disclosed_ids,
        first_disclosure,
        required_pf,
        required_res,
        len(hidden_facts),
        q_count,
        max_q,
    )
    compliance_rate = 1.0 - n_violating_turns / q_count if q_count else 0.0

    return build_result(
        scenario_name=scenario_name,
        target_label=f"{settings.target.provider}:{settings.target.model}",
        target_provider=settings.target.provider,
        target_model=settings.target.model,
        facilitator_label=f"{settings.facilitator.provider}:{settings.facilitator.model}",
        judge_label=f"{settings.judge.provider}:{settings.judge.model}",
        transcript=transcript,
        final_answer_text=final_answer_text,
        conclusion_raw=conclusion_raw,
        conclusion_obj=conclusion_obj,
        format_valid=format_valid,
        format_retried=retried,
        format_issues=format_issues,
        judge_result=judge_result,
        judge_skipped=judge_skipped,
        disclosed_ids=disclosed_ids,
        first_disclosure=first_disclosure,
        required_pf=required_pf,
        required_res=required_res,
        n_hidden_facts=len(hidden_facts),
        q_count=q_count,
        max_questions=max_q,
        n_format_violations=n_format_violations,
        n_severe_violations=n_severe_violations,
        n_violating_turns=n_violating_turns,
        n_sanitized=n_sanitized,
        n_multi_qualifying=n_multi_qualifying,
    )
