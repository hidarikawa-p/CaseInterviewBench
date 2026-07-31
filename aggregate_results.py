#!/usr/bin/env python3
"""Aggregate existing result files without running or re-judging anything.

Reads ``eval_result_*.json`` from a folder and prints the same per-model summary
that ``run_eval.py`` prints at the end of a run, using the same functions. No
API calls are made and no config file is needed, so this works on results from
LLM runs, human (Streamlit) sessions, or a mix of both.

Usage:
    python aggregate_results.py --results ./results
    python aggregate_results.py --results ./results --filter-target human
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from scenario_eval.aggregate import aggregate, load_results, print_summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Aggregate existing eval_result_*.json files (no API calls)."
    )
    p.add_argument(
        "--results", required=True,
        help="Folder containing eval_result_*.json files.",
    )
    p.add_argument(
        "--output",
        help="Path for the summary JSON "
             "(default: <results>/aggregate_summary.json). Use 'none' to skip writing.",
    )
    p.add_argument(
        "--filter-target",
        help="Only include runs whose target label contains this substring, "
             "e.g. 'human' or 'fable'.",
    )
    p.add_argument(
        "--per-scenario", action="store_true",
        help="Also list each run's scenario and overall score.",
    )
    return p.parse_args()


def report_duplicates(runs: list[dict]) -> None:
    """Warn when a target has several runs of the same scenario.

    Repeated attempts are counted as separate scenarios by the aggregation, so
    the mean and the violation shares are per run, not per unique scenario.
    This matters most for human sessions, which are timestamped and therefore
    never overwrite one another.
    """
    seen: Counter = Counter()
    for r in runs:
        seen[(r.get("models", {}).get("target", "unknown"), r.get("scenario_file"))] += 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    if not dupes:
        return
    print("Note: repeated runs of the same scenario are counted once each:")
    for (target, scenario), n in sorted(dupes.items()):
        print(f"  {target}: {scenario} x{n}")
    print()


def main() -> int:
    args = parse_args()
    results_dir = Path(args.results)
    if not results_dir.is_dir():
        print(f"Error: results folder not found: {results_dir}", file=sys.stderr)
        return 2

    runs = load_results(results_dir)
    if not runs:
        print(f"No eval_result_*.json found in {results_dir}", file=sys.stderr)
        return 2

    total = len(runs)
    if args.filter_target:
        needle = args.filter_target.lower()
        runs = [
            r for r in runs
            if needle in str(r.get("models", {}).get("target", "")).lower()
        ]
        if not runs:
            print(
                f"No runs matched --filter-target '{args.filter_target}' "
                f"(scanned {total} file(s)).",
                file=sys.stderr,
            )
            return 2
        print(f"Loaded {len(runs)} of {total} run(s) matching "
              f"'{args.filter_target}' from {results_dir}\n")
    else:
        print(f"Loaded {total} run(s) from {results_dir}\n")

    report_duplicates(runs)

    if args.per_scenario:
        print("Per-run results")
        print("-" * 78)
        for r in sorted(runs, key=lambda x: (x.get("models", {}).get("target", ""),
                                             str(x.get("scenario_file")))):
            sc = r.get("scores", {})
            flag = ("severe" if r.get("had_severe_violation")
                    else "format" if r.get("had_format_violation") else "clean")
            print(f"  {r.get('models', {}).get('target', '?'):<28} "
                  f"{str(r.get('scenario_file')):<42} "
                  f"overall {sc.get('overall')!s:>6}  [{flag}]")
        print()

    summary = aggregate(runs)
    print_summary(summary)

    if args.output and args.output.lower() == "none":
        return 0
    out_path = Path(args.output) if args.output else results_dir / "aggregate_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved aggregate summary: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
