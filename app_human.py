#!/usr/bin/env python3
"""Streamlit app: evaluate a human as the target on one scenario.

Run with:
    streamlit run app_human.py -- --config config.json \\
        --scenarios ./scenarios --output ./results

The human plays the target: they read the public context and initial request,
ask questions one at a time (a plain text box - no <conclusion> tag or JSON),
and finally fill four separate answer fields. The facilitator and judge run on
whatever providers the config specifies, and the saved output matches the LLM
target format exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from scenario_eval.config import fill_template, load_scenario, load_settings
from scenario_eval.human import build_conclusion_raw, process_question
from scenario_eval.judge import (
    REQUIRED_KEYS,
    run_judge,
    validate_conclusion_obj,
    zero_judge_result,
)
from scenario_eval.providers import build_adapter
from scenario_eval.results import build_result
from scenario_eval.runner import _required_sets
from scenario_eval.violations import LIMIT_PROMPT


def get_cli_args() -> argparse.Namespace:
    """Parse args passed after Streamlit's ``--`` separator."""
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--scenarios", default="./scenarios")
    p.add_argument("--output", default="./results")
    args, _ = p.parse_known_args(argv)
    return args


ARGS = get_cli_args()

st.set_page_config(page_title="Scenario Evaluation (Human Target)", layout="wide")


@st.cache_resource
def get_settings_and_prompts(config_path: str):
    """Load settings and prompt templates once per session."""
    settings = load_settings(config_path)
    guideline = settings.guideline_path.read_text(encoding="utf-8")
    judge_tmpl = settings.judge_path.read_text(encoding="utf-8")
    return settings, guideline, judge_tmpl


@st.cache_resource
def get_adapters(config_path: str):
    """Build facilitator and judge adapters once per session (target is human)."""
    settings = load_settings(config_path)
    facilitator = build_adapter(settings.facilitator, settings.max_retries)
    judge = build_adapter(settings.judge, settings.max_retries)
    return facilitator, judge


def list_scenarios(folder: str) -> list[Path]:
    return sorted(Path(folder).glob("*.json"))


def init_session(scenario: dict, scenario_name: str, max_q: int) -> None:
    """Initialise per-run session state for a freshly selected scenario."""
    st.session_state.update(
        active=True,
        scenario=scenario,
        scenario_name=scenario_name,
        max_q=max_q,
        transcript=[],
        disclosed_ids=set(),
        first_disclosure={},
        q_count=0,
        n_format_violations=0,
        n_severe_violations=0,
        n_violating_turns=0,
        n_sanitized=0,
        n_multi_qualifying=0,
        phase="questions",   # questions -> answer -> done
        result=None,
    )


def apply_turn(raw_output: str) -> None:
    """Run one submitted question through the shared per-turn logic."""
    ss = st.session_state
    facilitator, _judge = get_adapters(ARGS.config)
    outcome = process_question(
        facilitator,
        ss.scenario["public_context"],
        ss.scenario["hidden_facts"],
        ss.disclosed_ids,
        raw_output,
    )
    ss.q_count += 1
    decision = outcome["decision"]
    sev_v = outcome["severe_violations"]
    fmt_v = outcome["format_violations"]

    if sev_v or fmt_v:
        ss.n_violating_turns += 1
        if sev_v:
            ss.n_severe_violations += 1
        else:
            ss.n_format_violations += 1
        if outcome["sanitized"]:
            ss.n_sanitized += 1
    else:
        if len(decision["qualifying_fact_ids"]) > 1:
            ss.n_multi_qualifying += 1
        for fid in decision["disclose_fact_ids"]:
            ss.first_disclosure.setdefault(fid, ss.q_count)
        ss.disclosed_ids |= set(decision["disclose_fact_ids"])

    ss.transcript.append({
        "index": ss.q_count,
        "raw_output": raw_output,
        "question": outcome["question"],
        "answer": outcome["reply"],
        "severe_violations": sev_v,
        "format_violations": fmt_v,
        "history_sanitized": outcome["sanitized"],
        "stop_reason": None,
        "dropped_blocks": [],
        "too_broad": decision["too_broad"],
        "qualifying_fact_ids": decision["qualifying_fact_ids"],
        "disclosed_fact_ids": decision["disclose_fact_ids"],
        "facilitator_reasoning": decision["reasoning"],
        "cumulative_disclosed": sorted(ss.disclosed_ids),
    })


def finalize(answer_fields: dict[str, str]) -> dict:
    """Assemble the final answer, run the judge, and build the result dict."""
    ss = st.session_state
    settings, _guideline, judge_tmpl = get_settings_and_prompts(ARGS.config)
    _facilitator, judge = get_adapters(ARGS.config)

    conclusion_raw, conclusion_obj = build_conclusion_raw(answer_fields)
    validation = validate_conclusion_obj(conclusion_obj, settings.word_limit)
    format_valid = validation["valid"]
    format_issues = validation["issues"]

    hidden_facts = ss.scenario["hidden_facts"]
    acquired = [f for f in hidden_facts if f["fact_id"] in ss.disclosed_ids]
    unacquired = [f for f in hidden_facts if f["fact_id"] not in ss.disclosed_ids]

    if format_valid:
        judge_result = run_judge(
            judge, judge_tmpl, ss.scenario, acquired, unacquired, conclusion_raw
        )
        judge_skipped = False
    else:
        reason = f"Final answer failed validation ({format_issues}); scored 0 by rule."
        judge_result = zero_judge_result(reason)
        judge_skipped = True

    required_pf, required_res = _required_sets(hidden_facts)

    result = build_result(
        scenario_name=ss.scenario_name,
        target_label="human",
        target_provider="human",
        target_model="human",
        facilitator_label=f"{settings.facilitator.provider}:{settings.facilitator.model}",
        judge_label=f"{settings.judge.provider}:{settings.judge.model}",
        transcript=ss.transcript,
        final_answer_text=conclusion_raw,
        conclusion_raw=conclusion_raw,
        conclusion_obj=conclusion_obj,
        format_valid=format_valid,
        format_retried=False,
        format_issues=format_issues,
        judge_result=judge_result,
        judge_skipped=judge_skipped,
        disclosed_ids=ss.disclosed_ids,
        first_disclosure=ss.first_disclosure,
        required_pf=required_pf,
        required_res=required_res,
        n_hidden_facts=len(hidden_facts),
        q_count=ss.q_count,
        max_questions=ss.max_q,
        n_format_violations=ss.n_format_violations,
        n_severe_violations=ss.n_severe_violations,
        n_violating_turns=ss.n_violating_turns,
        n_sanitized=ss.n_sanitized,
        n_multi_qualifying=ss.n_multi_qualifying,
    )
    return result


def save_result(result: dict) -> Path:
    out_dir = Path(ARGS.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(result["scenario_file"]).stem
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"eval_result_{stem}__human_{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- UI

st.title("Scenario Evaluation — Human Target")

try:
    settings, guideline, _judge_tmpl = get_settings_and_prompts(ARGS.config)
except Exception as e:  # noqa: BLE001
    st.error(f"Failed to load config '{ARGS.config}': {e}")
    st.stop()

with st.sidebar:
    st.header("Setup")
    st.caption(f"Config: `{ARGS.config}`")
    st.caption(f"Facilitator: `{settings.facilitator.provider}:{settings.facilitator.model}`")
    st.caption(f"Judge: `{settings.judge.provider}:{settings.judge.model}`")
    st.caption(f"Question limit: {settings.max_questions}")

    scenario_files = list_scenarios(ARGS.scenarios)
    if not scenario_files:
        st.error(f"No scenarios found in {ARGS.scenarios}")
        st.stop()

    names = [p.name for p in scenario_files]
    chosen = st.selectbox("Scenario", names, key="scenario_pick")

    if st.button("Start / Restart scenario", type="primary"):
        scenario = load_scenario(Path(ARGS.scenarios) / chosen)
        init_session(scenario, chosen, settings.max_questions)
        st.rerun()

if not st.session_state.get("active"):
    st.info("Select a scenario in the sidebar and press **Start**.")
    st.stop()

ss = st.session_state
scenario = ss.scenario

# Briefing
st.subheader("Situation")
st.markdown(f"**Public context.** {scenario['public_context']}")
st.markdown(f"**Initial request.** {scenario['initial_request']}")
with st.expander("Full task instructions (guideline)"):
    st.text(fill_template(guideline, {
        "PUBLIC_CONTEXT": scenario["public_context"],
        "INITIAL_REQUEST": scenario["initial_request"],
    }))

remaining_q = ss.max_q - ss.q_count
st.progress(min(ss.q_count / ss.max_q, 1.0),
            text=f"Questions used: {ss.q_count} / {ss.max_q}")

# Conversation so far
if ss.transcript:
    st.subheader("Conversation")
    for t in ss.transcript:
        st.markdown(f"**Q{t['index']}.** {t['question']}")
        if t["severe_violations"]:
            st.error(f"Severe violation {t['severe_violations']} — no information disclosed.")
        elif t["format_violations"]:
            st.warning(f"Format violation {t['format_violations']} — no information disclosed.")
        if t["disclosed_fact_ids"]:
            st.success(t["answer"])
        else:
            st.info(t["answer"])

# Phase: asking questions
if ss.phase == "questions":
    if remaining_q > 0:
        st.subheader("Ask a question")
        with st.form("question_form", clear_on_submit=True):
            q = st.text_area(
                "One question, one sentence. Plain text — no tags or JSON.",
                height=80,
            )
            c1, c2 = st.columns([1, 1])
            submitted = c1.form_submit_button("Submit question", type="primary")
            proceed = c2.form_submit_button("Stop and write final answer")
        if submitted and q.strip():
            apply_turn(q.strip())
            if ss.q_count >= ss.max_q:
                ss.phase = "answer"
            st.rerun()
        elif proceed:
            ss.phase = "answer"
            st.rerun()
    else:
        st.warning(LIMIT_PROMPT)
        ss.phase = "answer"
        st.rerun()

# Phase: final answer
if ss.phase == "answer":
    st.subheader("Final answer")
    st.caption("Fill each field separately. No formatting, tags, or JSON required.")
    with st.form("answer_form"):
        pf = st.text_area("Problem formulation — the problem that should be addressed",
                          height=110, key="f_pf")
        pe = st.text_area("Problem evidence — the evidence supporting that formulation",
                          height=110, key="f_pe")
        rs = st.text_area("Resolution — the proposed course of action",
                          height=110, key="f_rs")
        rr = st.text_area("Resolution rationale — the reasoning supporting the resolution",
                          height=110, key="f_rr")
        submit_final = st.form_submit_button("Submit final answer and evaluate",
                                             type="primary")
    if submit_final:
        fields = {
            "problem_formulation": pf,
            "problem_evidence": pe,
            "resolution": rs,
            "resolution_rationale": rr,
        }
        if not any(v.strip() for v in fields.values()):
            st.error("Please fill in at least one field before submitting.")
        else:
            with st.spinner("Running judge and scoring..."):
                result = finalize(fields)
                saved = save_result(result)
            ss.result = result
            ss.saved_path = str(saved)
            ss.phase = "done"
            st.rerun()

# Phase: results
if ss.phase == "done" and ss.result:
    result = ss.result
    st.subheader("Scores")
    sc = result["scores"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Problem recognition", sc["problem_recognition"])
    c2.metric("Resolution", sc["resolution"])
    c3.metric("Efficiency", sc["efficiency"])
    c4.metric("Overall", sc["overall"])

    colA, colB = st.columns(2)
    with colA:
        st.markdown("**Facts acquired**")
        st.write(sorted(result["disclosed_fact_ids"]))
        st.markdown("**Compliance**")
        st.write(f"{result['compliance_rate']:.0%} "
                 f"(severe {result['n_severe_violations']}, "
                 f"format {result['n_format_violations']})")
    with colB:
        st.markdown("**Judge dimension scores (/3)**")
        for k in REQUIRED_KEYS:
            js = result["judge_result"]["scores"][k]
            st.write(f"- {k}: {js['score']} — {js['justification'][:160]}")

    with st.expander("Efficiency components"):
        st.json(result["efficiency_components"])
    with st.expander("Full result JSON"):
        st.json(result)

    st.success(f"Saved: {ss.saved_path}")
    st.download_button(
        "Download result JSON",
        data=json.dumps(result, ensure_ascii=False, indent=2),
        file_name=Path(ss.saved_path).name,
        mime="application/json",
    )
    if st.button("Evaluate another scenario"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
