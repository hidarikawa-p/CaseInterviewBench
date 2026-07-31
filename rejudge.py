#!/usr/bin/env python3
"""Re-score existing result files with a different judge model.

Reads finished result JSONs, replays only the judging step through the very
same functions the original run used (``run_judge``, ``zero_judge_result``,
``build_result``), and writes new result files with an identical schema. The
dialogue is never replayed: the transcript, disclosures, and violation counts
are carried over unchanged, so the only thing that varies is the judge.

Usage:
    python rejudge.py --results ./results --scenarios ./scenarios \\
        --config config_judge_b.json --output ./results_judge_b

The judge model is taken from the ``judge`` section of the config file, so
switching judges means pointing ``--config`` at a config whose ``judge`` section
names the new model. The ``target`` and ``facilitator`` sections are ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from scenario_eval.aggregate import aggregate, load_results, print_summary
from scenario_eval.config import load_scenario, load_settings
from scenario_eval.human import build_conclusion_raw
from scenario_eval.judge import (
    REQUIRED_KEYS,
    run_judge,
    validate_conclusion_obj,
    validate_final_answer,
    zero_judge_result,
)
from scenario_eval.providers import build_adapter
from scenario_eval.results import build_result
from scenario_eval.runner import _required_sets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Re-judge existing result files with a different judge model."
    )
    p.add_argument(
        "--results", required=True,
        help="Folder containing existing eval_result_*.json files to re-judge.",
    )
    p.add_argument(
        "--scenarios", required=True,
        help="Folder with the scenario *.json files the results were produced from "
             "(needed for ground truth and hidden facts).",
    )
    p.add_argument(
        "--config", default="config.json",
        help="Config file whose 'judge' section names the new judge "
             "(default: config.json).",
    )
    p.add_argument(
        "--output", default="./results_rejudged",
        help="Folder for the re-judged results (default: ./results_rejudged). "
             "Must differ from --results.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-judge even if an output file already exists.",
    )
    return p.parse_args()


def recover_conclusion(result: dict, word_limit: int) -> tuple[str, dict | None, dict]:
    """Recover the exact final answer that should be handed to the judge.

    Returns ``(conclusion_raw, conclusion_obj, validation)``. LLM runs are
    re-parsed from ``final_answer_raw`` so the judge sees the identical string
    it saw originally; human runs are rebuilt from the stored four fields.
    """
    final_raw = result.get("final_answer_raw") or ""

    # LLM path: the original answer carried a <conclusion> block.
    if "<conclusion>" in final_raw:
        validation = validate_final_answer(final_raw, word_limit)
        return validation["conclusion_raw"], validation["conclusion_obj"], validation

    # Human path (or already-normalised JSON): rebuild from the stored fields.
    obj = result.get("conclusion")
    if isinstance(obj, dict):
        raw, rebuilt = build_conclusion_raw(obj)
        return raw, rebuilt, validate_conclusion_obj(rebuilt, word_limit)

    # Nothing usable; fall back to the raw text and let format_valid decide.
    return final_raw.strip(), None, {"valid": False, "issues": ["no_recoverable_conclusion"]}


def rejudge_one(result: dict, scenario: dict, settings, judge_adapter,
                judge_template: str, judge_label: str) -> dict:
    """Re-run the judge for one stored result and rebuild the result dict."""
    hidden_facts = scenario["hidden_facts"]
    disclosed_ids = set(result.get("disclosed_fact_ids", []))
    acquired = [f for f in hidden_facts if f["fact_id"] in disclosed_ids]
    unacquired = [f for f in hidden_facts if f["fact_id"] not in disclosed_ids]

    conclusion_raw, conclusion_obj, validation = recover_conclusion(
        result, settings.word_limit
    )

    # Honour the original format verdict: a run that failed validation was
    # scored zero without a judge call, and must stay zero under a new judge.
    # Older files may predate this field; derive it from the recovered answer
    # rather than defaulting to False, which would silently zero the run.
    if "format_valid" in result:
        format_valid = bool(result["format_valid"])
    else:
        format_valid = bool(validation.get("valid"))
        print(f"        note: 'format_valid' missing; derived as {format_valid} "
              f"from the recovered final answer")

    if format_valid:
        judge_result = run_judge(
            judge_adapter, judge_template, scenario, acquired, unacquired, conclusion_raw
        )
        judge_skipped = False
    else:
        reason = (
            "Final answer did not conform to the required format in the original run; "
            "scored 0 by rule without judge evaluation (re-judge preserved this verdict)."
        )
        judge_result = zero_judge_result(reason)
        judge_skipped = True

    required_pf, required_res = _required_sets(hidden_facts)
    first_disclosure = {int(k): v for k, v in (result.get("first_disclosure") or {}).items()}

    # max_questions is persisted by newer runs; older files fall back to the
    # value in --config. Report it, since the efficiency score depends on it.
    if result.get("max_questions"):
        max_questions = int(result["max_questions"])
        max_q_source = "result"
    else:
        max_questions = int(settings.max_questions)
        max_q_source = "config"
        print(f"        note: 'max_questions' missing; using {max_questions} "
              f"from --config (efficiency score depends on this)")

    rebuilt = build_result(
        scenario_name=result["scenario_file"],
        target_label=result["models"]["target"],
        target_provider=result.get("target_provider", "unknown"),
        target_model=result.get("target_model", "unknown"),
        facilitator_label=result["models"].get("facilitator", "unknown"),
        judge_label=judge_label,
        transcript=result.get("transcript", []),
        final_answer_text=result.get("final_answer_raw", ""),
        conclusion_raw=conclusion_raw,
        conclusion_obj=conclusion_obj,
        format_valid=format_valid,
        format_retried=bool(result.get("format_retried", False)),
        format_issues=result.get("format_issues", []),
        judge_result=judge_result,
        judge_skipped=judge_skipped,
        disclosed_ids=disclosed_ids,
        first_disclosure=first_disclosure,
        required_pf=required_pf,
        required_res=required_res,
        n_hidden_facts=len(hidden_facts),
        q_count=int(result.get("question_count", 0)),
        max_questions=max_questions,
        n_format_violations=int(result.get("n_format_violations", 0)),
        n_severe_violations=int(result.get("n_severe_violations", 0)),
        n_violating_turns=int(result.get("n_violating_turns", 0)),
        n_sanitized=int(result.get("n_sanitized_turns", 0)),
        n_multi_qualifying=int(result.get("n_multi_qualifying", 0)),
    )

    # Provenance, so a re-judged file is distinguishable and comparable.
    rebuilt["rejudged"] = True
    rebuilt["original_judge"] = result["models"].get("judge")
    rebuilt["original_scores"] = result.get("scores")
    rebuilt["max_questions_source"] = max_q_source
    return rebuilt


def main() -> int:
    args = parse_args()

    results_dir = Path(args.results)
    output_dir = Path(args.output)
    if not results_dir.is_dir():
        print(f"Error: results folder not found: {results_dir}", file=sys.stderr)
        return 2
    if output_dir.resolve() == results_dir.resolve():
        print(
            "Error: --output must differ from --results. Writing re-judged files next "
            "to the originals would make both appear in aggregation for the same "
            "target model and double-count every scenario.",
            file=sys.stderr,
        )
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(args.config)
    judge_template = settings.judge_path.read_text(encoding="utf-8")
    judge_adapter = build_adapter(settings.judge, settings.max_retries)
    judge_label = f"{settings.judge.provider}:{settings.judge.model}"

    result_files = sorted(results_dir.glob("eval_result_*.json"))
    if not result_files:
        print(f"No eval_result_*.json found in {results_dir}", file=sys.stderr)
        return 2

    print(f"Found {len(result_files)} result file(s) in {results_dir}")
    print(f"New judge : {judge_label}")
    print(f"Output    : {output_dir}\n")

    n_done = n_skipped = n_failed = 0

    for rf in result_files:
        out_path = output_dir / rf.name
        if out_path.exists() and not args.force:
            print(f"[skip] {rf.name} (already re-judged)")
            n_skipped += 1
            continue

        print(f"[judge] {rf.name} ...")
        try:
            result = json.loads(rf.read_text(encoding="utf-8"))
            scenario_file = result["scenario_file"]
            scenario_path = Path(args.scenarios) / scenario_file
            if not scenario_path.exists():
                raise FileNotFoundError(
                    f"scenario '{scenario_file}' not found in {args.scenarios}"
                )
            scenario = load_scenario(scenario_path)

            rebuilt = rejudge_one(
                result, scenario, settings, judge_adapter, judge_template, judge_label
            )
            out_path.write_text(
                json.dumps(rebuilt, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            old = result.get("scores", {})
            new = rebuilt["scores"]
            print(
                f"        overall {old.get('overall')} -> {new['overall']} "
                f"(problem {old.get('problem_recognition')} -> {new['problem_recognition']}, "
                f"resolution {old.get('resolution')} -> {new['resolution']}) "
                f"| saved {out_path.name}"
            )
            n_done += 1
        except Exception as e:  # noqa: BLE001 - report and continue
            n_failed += 1
            print(f"        FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\nRe-judged: {n_done}  Skipped: {n_skipped}  Failed: {n_failed}")

    print("\n" + "=" * 78)
    print(f"AGGREGATE (judge = {judge_label})")
    print("=" * 78)
    runs = load_results(output_dir)
    summary = aggregate(runs)
    print_summary(summary)

    agg_path = output_dir / "aggregate_summary.json"
    agg_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved aggregate summary: {agg_path}")

    return 1 if n_failed and n_done == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
