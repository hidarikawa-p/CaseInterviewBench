#!/usr/bin/env python3
"""Command-line entrypoint for scenario-based LLM evaluation.

Usage:
    python run_eval.py --scenarios ./scenarios [--config config.json] \\
        [--output ./results] [--force]

Evaluates every ``*.json`` scenario in the scenarios folder. A scenario whose
result already exists in the output folder is skipped (unless ``--force``) but
still included in the final aggregation, so an interrupted run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from scenario_eval.aggregate import aggregate, load_results, print_summary
from scenario_eval.config import load_scenario, load_settings
from scenario_eval.providers import build_adapter
from scenario_eval.runner import run_scenario


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scenario-based LLM problem-solving evaluation.")
    p.add_argument(
        "--scenarios", required=True,
        help="Folder containing scenario *.json files.",
    )
    p.add_argument(
        "--config", default="config.json",
        help="Path to the model/provider config file (default: config.json).",
    )
    p.add_argument(
        "--output", default="./results",
        help="Folder for per-scenario results and the aggregate summary "
             "(default: ./results).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-run scenarios even if a result file already exists.",
    )
    return p.parse_args()


def result_path(output_dir: Path, scenario_file: Path) -> Path:
    return output_dir / f"eval_result_{scenario_file.stem}.json"


def main() -> int:
    args = parse_args()

    scenarios_dir = Path(args.scenarios)
    if not scenarios_dir.is_dir():
        print(f"Error: scenarios folder not found: {scenarios_dir}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(args.config)
    guideline_template = settings.guideline_path.read_text(encoding="utf-8")
    judge_template = settings.judge_path.read_text(encoding="utf-8")

    # Adapters are built once and reused across all scenarios.
    target = build_adapter(settings.target, settings.max_retries)
    facilitator = build_adapter(settings.facilitator, settings.max_retries)
    judge = build_adapter(settings.judge, settings.max_retries)

    scenario_files = sorted(scenarios_dir.glob("*.json"))
    if not scenario_files:
        print(f"No *.json scenarios found in {scenarios_dir}", file=sys.stderr)
        return 2

    print(f"Found {len(scenario_files)} scenario(s) in {scenarios_dir}")
    print(f"Target      : {settings.target.provider}:{settings.target.model}")
    print(f"Facilitator : {settings.facilitator.provider}:{settings.facilitator.model}")
    print(f"Judge       : {settings.judge.provider}:{settings.judge.model}\n")

    n_run = n_skipped = n_failed = 0

    for sf in scenario_files:
        out_path = result_path(output_dir, sf)
        if out_path.exists() and not args.force:
            print(f"[skip] {sf.name} (result exists: {out_path.name})")
            n_skipped += 1
            continue

        print(f"[run ] {sf.name} ...")
        try:
            scenario = load_scenario(sf)
            result = run_scenario(
                scenario=scenario,
                scenario_name=sf.name,
                settings=settings,
                guideline_template=guideline_template,
                judge_template=judge_template,
                target=target,
                facilitator=facilitator,
                judge=judge,
            )
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            sc = result["scores"]
            print(
                f"       done: overall {sc['overall']} "
                f"(problem {sc['problem_recognition']}, resolution {sc['resolution']}, "
                f"efficiency {sc['efficiency']}) | "
                f"compliance {result['compliance_rate']:.0%} | saved {out_path.name}"
            )
            n_run += 1
        except Exception as e:  # noqa: BLE001 - report and continue with next scenario
            n_failed += 1
            print(f"       FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\nRun: {n_run}  Skipped: {n_skipped}  Failed: {n_failed}")

    # Aggregate over everything present in the output folder.
    print("\n" + "=" * 78)
    print("AGGREGATE")
    print("=" * 78)
    runs = load_results(output_dir)
    summary = aggregate(runs)
    print_summary(summary)

    agg_path = output_dir / "aggregate_summary.json"
    agg_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved aggregate summary: {agg_path}")

    return 1 if n_failed and n_run == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
