#!/usr/bin/env python3
"""Compare evaluation results across models.

Two ways to specify inputs:

1. label=path pairs on the command line (quick, ad hoc)

       python compare_models.py --results "Fable 5=./results_fable" \\
           "GPT-5.6=./results_gpt5" --output ./comparison

   A bare path is also accepted; its folder name becomes the label.

2. a manifest file (reproducible, for reports)

       python compare_models.py --manifest models.json --output ./comparison

   models.json:
   {
     "models": [
       {"label": "Fable 5", "results_dir": "./results_fable"},
       {"label": "GPT-5.6", "results_dir": "./results_gpt5"}
     ]
   }

A single folder holding several models' results also works with
``--split-by-target``, which groups runs by their ``models.target`` label.

Outputs matrices M1-M8 as console tables, CSVs, heatmap PNGs, and one
self-contained HTML report. Human runs are excluded unless --include-human.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scenario_eval.compare import (
    SCORE_KEYS,
    annotate_runs,
    model_order,
    build_fact_matrix,
    build_group_matrix,
    build_relative_matrix,
    build_summary_matrix,
    build_violation_code_matrix,
    build_violation_rate_matrix,
    export_records_csv,
    load_model_runs,
)
from scenario_eval.report import build_html_report, render_heatmap


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare evaluation results across models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--results", nargs="+", metavar="LABEL=DIR",
        help="One or more result folders, optionally as LABEL=DIR.",
    )
    src.add_argument(
        "--manifest",
        help="JSON manifest listing models and their result folders.",
    )
    p.add_argument("--output", default="./comparison",
                   help="Output folder (default: ./comparison).")
    p.add_argument("--score", default="overall", choices=SCORE_KEYS,
                   help="Score used for the M1/M2 matrices (default: overall).")
    p.add_argument("--split-by-target", action="store_true",
                   help="Group runs within a folder by their models.target label. "
                        "Only useful when filenames do not collide; results for the "
                        "same scenario share a filename across models, so normally "
                        "each model needs its own folder.")
    p.add_argument("--include-human", action="store_true",
                   help="Include runs whose target is 'human' (excluded by default).")
    p.add_argument("--no-heatmaps", action="store_true",
                   help="Skip figure/HTML generation (console and CSV only).")
    p.add_argument("--formats", nargs="+", default=["png", "pdf"],
                   choices=["png", "pdf", "svg"],
                   help="Figure formats to write (default: png pdf). PDF and SVG "
                        "are written as vector graphics for publication use.")
    return p.parse_args()


def resolve_sources(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Build the (label, directory) list from either input style."""
    sources: list[tuple[str, str]] = []
    if args.manifest:
        cfg = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        entries = cfg.get("models")
        if not entries:
            raise KeyError("Manifest must contain a non-empty 'models' list.")
        for e in entries:
            if "results_dir" not in e:
                raise KeyError(f"Manifest entry missing 'results_dir': {e}")
            label = e.get("label") or Path(e["results_dir"]).name
            sources.append((str(label), str(e["results_dir"])))
    else:
        for item in args.results:
            if "=" in item:
                label, directory = item.split("=", 1)
                sources.append((label.strip(), directory.strip()))
            else:
                sources.append((Path(item).name, item))
    return sources


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output)
    csv_dir = out_dir / "csv"
    png_dir = out_dir / "figures"

    try:
        sources = resolve_sources(args)
    except (KeyError, json.JSONDecodeError, OSError) as e:
        print(f"Error reading inputs: {e}", file=sys.stderr)
        return 2

    runs_by_model, warnings = load_model_runs(
        sources,
        exclude_human=not args.include_human,
        split_by_target=args.split_by_target,
    )
    if not runs_by_model:
        print("No comparable runs were loaded.", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        return 2

    records, warn2 = annotate_runs(runs_by_model)
    warnings += warn2

    models = model_order(runs_by_model)
    n_types = len({r["logical_type"] for r in records})
    n_domains = len({r["domain"] for r in records})
    print(f"Loaded {len(records)} run(s) across {len(models)} model(s): "
          f"{', '.join(models)}")
    print(f"Logical types: {n_types}   Domains: {n_domains}\n")
    for w in warnings:
        print(f"  [warn] {w}")
    if warnings:
        print()

    # Build M1-M8.
    m1 = build_group_matrix(records, "logical_type", args.score,
                            f"M1. Model x logical type ({args.score} mean)", models)
    m2 = build_group_matrix(records, "domain", args.score,
                            f"M2. Model x domain ({args.score} mean)", models)
    m34 = build_summary_matrix(records, models)
    m5 = build_relative_matrix(
        m1, f"M5. Model x logical type ({args.score}, deviation from column mean)")
    m6s = build_violation_rate_matrix(
        records, "logical_type", "severe",
        "M6a. Severe-violation rate by logical type (% of scenarios)", models)
    m6f = build_violation_rate_matrix(
        records, "logical_type", "format",
        "M6b. Format-violation rate by logical type (% of scenarios)", models)
    m7 = build_violation_code_matrix(records, models)
    m8 = build_fact_matrix(records, models)

    sections = [
        {"matrix": m1, "note": "Cell = mean score over the scenarios of that "
                               "logical type; colour scale fixed to 0-100. "
                               "Hover a cell in the table for n."},
        {"matrix": m2, "note": "Same, grouped by domain."},
        {"matrix": m34, "note": "Overall means per score dimension."},
        {"matrix": m5, "note": "Positive = above the cross-model mean for that "
                               "logical type; isolates relative strength from "
                               "scenario difficulty."},
        {"matrix": m6s, "note": "Share of scenarios with at least one severe "
                                "violation."},
        {"matrix": m6f, "note": "Share of scenarios with a format violation and "
                                "no severe violation."},
        {"matrix": m7, "note": "Share of scenarios in which each violation code "
                               "was triggered at least once."},
        {"matrix": m8, "note": "coverage/eff_a/eff_b are 0-1; question_count is a "
                               "count; compliance_rate is 0-1."},
    ]

    for sec in sections:
        print(sec["matrix"].render_text())
        print()

    for name, matrix in [
        ("m1_model_x_logical_type", m1), ("m2_model_x_domain", m2),
        ("m3_m4_model_x_dimension", m34), ("m5_relative_logical_type", m5),
        ("m6a_severe_by_logical_type", m6s), ("m6b_format_by_logical_type", m6f),
        ("m7_violation_code_x_model", m7), ("m8_fact_components", m8),
    ]:
        matrix.to_csv(csv_dir / f"{name}.csv")
    export_records_csv(records, csv_dir / "per_run_records.csv")
    print(f"Saved CSVs: {csv_dir}")

    if not args.no_heatmaps:
        any_png = False
        for name, sec in zip(
            ["m1_model_x_logical_type", "m2_model_x_domain",
             "m3_m4_model_x_dimension", "m5_relative_logical_type",
             "m6a_severe_by_logical_type", "m6b_format_by_logical_type",
             "m7_violation_code_x_model", "m8_fact_components"],
            sections,
        ):
            png = render_heatmap(sec["matrix"], out_dir=png_dir, stem=name,
                                 formats=args.formats)
            sec["png"] = png
            any_png = any_png or bool(png)

        if not any_png:
            warnings.append(
                "matplotlib is not installed; heatmap images were skipped. "
                "The HTML report still contains colour-shaded tables."
            )
        else:
            print(f"Saved figures ({', '.join(args.formats)}): {png_dir}")

        report_path = out_dir / "comparison_report.html"
        build_html_report(
            sections,
            {"models": models, "n_runs": len(records),
             "n_logical_types": n_types, "n_domains": n_domains,
             "exclude_human": not args.include_human},
            warnings, report_path,
        )
        print(f"Saved report: {report_path}")

    summary = {
        "models": models,
        "n_runs": len(records),
        "n_logical_types": n_types,
        "n_domains": n_domains,
        "score_used": args.score,
        "matrices": {
            "m1_model_x_logical_type": {
                r: {c: m1.value(r, c) for c in m1.cols} for r in m1.rows},
            "m2_model_x_domain": {
                r: {c: m2.value(r, c) for c in m2.cols} for r in m2.rows},
            "m3_m4_model_x_dimension": {
                r: {c: m34.value(r, c) for c in m34.cols} for r in m34.rows},
        },
    }
    (out_dir / "comparison_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved summary: {out_dir / 'comparison_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
