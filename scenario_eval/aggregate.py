"""Aggregate per-scenario result files into per-model summaries.

Violation scores are never folded into the scores. Instead, aggregation reports
the share of scenarios in which each violation tier occurred, alongside score
means for reference. The three violation shares are mutually exclusive and sum
to 1 per model:

* severe_violation_scenario_rate: scenarios with at least one severe violation.
* format_only_violation_scenario_rate: scenarios with format violations but no
  severe violation.
* clean_scenario_rate: scenarios with no violations.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from .violations import FORMAT_CODES, SEVERE_CODES, base_violation_code


def run_violation_codes(run: dict) -> tuple[set[str], set[str]]:
    """Return the (severe, format) violation codes that occurred in one run.

    Codes are normalised and de-duplicated, so a run that trips the same code on
    several turns contributes it once. Runs without a transcript yield empty sets.
    """
    severe: set[str] = set()
    fmt: set[str] = set()
    for turn in run.get("transcript") or []:
        for code in turn.get("severe_violations") or []:
            severe.add(base_violation_code(code))
        for code in turn.get("format_violations") or []:
            fmt.add(base_violation_code(code))
    return severe, fmt


def _code_scenario_counts(runs: list[dict]) -> tuple[dict, dict]:
    """Count how many runs tripped each violation code, at least once.

    Every canonical code is present in the output, including zeros, so the
    breakdown has a stable shape across models.
    """
    severe_counts = {c: 0 for c in SEVERE_CODES}
    format_counts = {c: 0 for c in FORMAT_CODES}
    for r in runs:
        severe, fmt = run_violation_codes(r)
        for c in severe:
            severe_counts[c] = severe_counts.get(c, 0) + 1
        for c in fmt:
            format_counts[c] = format_counts.get(c, 0) + 1
    return severe_counts, format_counts


def load_results(results_dir: str | Path) -> list[dict]:
    """Load every ``eval_result_*.json`` under ``results_dir``."""
    runs = []
    pattern = str(Path(results_dir) / "eval_result_*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            runs.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [skip] {path}: {e}")
    return runs


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def overall_severe_zeroed(run: dict) -> float | None:
    """Overall score with a severe-violation scenario counted as 0.

    Uses the value stored by newer runs, and derives it otherwise so that result
    files written before this variant existed aggregate correctly too.
    """
    scores = run.get("scores") or {}
    stored = scores.get("overall_severe_zeroed")
    if isinstance(stored, (int, float)):
        return float(stored)
    if run.get("had_severe_violation"):
        return 0.0
    overall = scores.get("overall")
    return float(overall) if isinstance(overall, (int, float)) else None


def aggregate(runs: list[dict]) -> dict:
    """Group runs by target model and compute violation shares and score means."""
    by_model: dict[str, list[dict]] = {}
    for r in runs:
        model = r.get("models", {}).get("target", "unknown")
        by_model.setdefault(model, []).append(r)

    summary = {}
    for model, rs in by_model.items():
        n = len(rs)
        severe = [bool(r.get("had_severe_violation")) for r in rs]
        fmt_only = [
            bool(r.get("had_format_violation")) and not bool(r.get("had_severe_violation"))
            for r in rs
        ]
        clean = [not bool(r.get("had_any_violation")) for r in rs]

        def avg(key: str) -> float:
            vals = [r["scores"][key] for r in rs if "scores" in r and key in r["scores"]]
            return sum(vals) / len(vals) if vals else float("nan")

        def _mean_or_nan(values: list) -> float:
            vals = [v for v in values if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else float("nan")

        severe_counts, format_counts = _code_scenario_counts(rs)

        summary[model] = {
            "n_scenarios": n,
            "severe_violation_scenario_rate": round(_rate(severe), 3),
            "format_only_violation_scenario_rate": round(_rate(fmt_only), 3),
            "clean_scenario_rate": round(_rate(clean), 3),
            "severe_violation_code_scenarios": severe_counts,
            "format_violation_code_scenarios": format_counts,
            "severe_violation_code_rates": {
                k: round(v / n, 3) if n else 0.0 for k, v in severe_counts.items()
            },
            "format_violation_code_rates": {
                k: round(v / n, 3) if n else 0.0 for k, v in format_counts.items()
            },
            "mean_overall_severe_zeroed": round(_mean_or_nan(
                [overall_severe_zeroed(r) for r in rs]), 1),
            "mean_compliance_rate": round(
                sum(r.get("compliance_rate", 0.0) for r in rs) / n, 3
            ) if n else 0.0,
            "mean_problem_recognition": round(avg("problem_recognition"), 1),
            "mean_resolution": round(avg("resolution"), 1),
            "mean_efficiency": round(avg("efficiency"), 1),
            "mean_overall": round(avg("overall"), 1),
            "severe_scenarios": [
                r.get("scenario_file") for r, f in zip(rs, severe) if f
            ],
        }
    return summary


def print_summary(summary: dict) -> None:
    """Human-readable console report."""
    if not summary:
        print("No results to aggregate.")
        return
    for model, m in summary.items():
        print("=" * 78)
        print(f"Model: {model}  (scenarios: {m['n_scenarios']})")
        print("-" * 78)
        print(f"  severe-violation scenario rate : {m['severe_violation_scenario_rate']:.0%}")
        print(f"  format-only violation rate     : {m['format_only_violation_scenario_rate']:.0%}")
        print(f"  clean scenario rate            : {m['clean_scenario_rate']:.0%}")
        print(f"  mean compliance rate           : {m['mean_compliance_rate']:.0%}")
        print(
            f"  mean scores (reference)        : "
            f"problem {m['mean_problem_recognition']} / "
            f"resolution {m['mean_resolution']} / "
            f"efficiency {m['mean_efficiency']} / "
            f"overall {m['mean_overall']}"
        )
        print(
            f"  mean overall, severe = 0       : "
            f"{m.get('mean_overall_severe_zeroed')}"
        )
        n = m["n_scenarios"]
        for label, key in (
            ("severe violation codes", "severe_violation_code_scenarios"),
            ("format violation codes", "format_violation_code_scenarios"),
        ):
            counts = m.get(key) or {}
            if not counts:
                continue
            print(f"  {label} (scenarios):")
            width = max(len(c) for c in counts)
            for code, cnt in counts.items():
                share = f"{cnt / n:.0%}" if n else "0%"
                print(f"    {code:<{width}}  {cnt:>3}  ({share})")
        if m["severe_scenarios"]:
            print(f"  severe-violation scenarios     : {m['severe_scenarios']}")
