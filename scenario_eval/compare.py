"""Cross-model comparison of evaluation results.

Loads result files for several models, parses the scenario naming convention to
recover the domain and logical type of each scenario, and builds comparison
matrices and heatmaps.

Scenario files are named ``NNN_<domain>__NNN_<LogicalType>.json``, e.g.
``008_creative__005_Surface.json``. The leading numbers are the canonical IDs
from the benchmark tables, so this becomes domain ``D8 Creative`` and logical
type ``L5 Surface``. Axis order follows those IDs rather than alphabetical
order. Names that do not match fall back to ``unknown`` and are reported.

Human runs (``models.target == "human"``) are excluded by default, since human
result files are anonymised separately and are not directly comparable.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .aggregate import load_results, run_violation_codes
from .violations import FORMAT_CODES, SEVERE_CODES

SCENARIO_PATTERN = re.compile(
    r"^(?P<domain_idx>\d+)_(?P<domain>[^_]+)__(?P<type_idx>\d+)_(?P<logical_type>.+)$"
)

SCORE_KEYS = ["problem_recognition", "resolution", "efficiency", "overall"]

# Scores are always on a 0-100 scale, so score heatmaps use a fixed range to
# keep colours comparable across matrices and across reports.
SCORE_RANGE = (0.0, 100.0)

# group key -> (label field, sort-index field)
GROUP_FIELDS = {
    "logical_type": ("logical_type_label", "logical_type_idx"),
    "domain": ("domain_label", "domain_idx"),
}

# Axis titles shown on tables and figures.
GROUP_DISPLAY = {"logical_type": "Logical Type", "domain": "Domain"}

# Canonical labels from the benchmark tables, keyed by ID. These take priority
# over the abbreviated token in the filename, so axes read the same as the
# published tables (e.g. "L5 Surface Reversal", "D3 Academic"). An ID outside
# these tables falls back to the filename token.
LOGICAL_TYPE_LABELS = {
    1: "No-Action Decision",
    2: "Underdetermined",
    3: "Early Sufficiency",
    4: "Preference Elicitation",
    5: "Surface Reversal",
    6: "Quantitative Reversal",
    7: "Midstream Reversal",
    8: "Problem Reframing",
    9: "Premise Correction",
    10: "Template Trap",
    11: "Distributed Evidence Synthesis",
    12: "Second-Order Effects",
    13: "Authority Boundary",
}

DOMAIN_LABELS = {
    1: "Personal",
    2: "Career",
    3: "Academic",
    4: "Relationships",
    5: "Organization",
    6: "Technology",
    7: "Society",
    8: "Creative",
}


def parse_scenario_name(scenario_file: str) -> dict:
    """Split a scenario filename into domain and logical type, with ID labels."""
    stem = Path(str(scenario_file)).stem
    m = SCENARIO_PATTERN.match(stem)
    if not m:
        return {
            "stem": stem,
            "domain": "unknown", "domain_idx": None, "domain_label": "unknown",
            "logical_type": "unknown", "logical_type_idx": None,
            "logical_type_label": "unknown",
        }
    d_idx = int(m.group("domain_idx"))
    t_idx = int(m.group("type_idx"))
    domain = m.group("domain")
    ltype = m.group("logical_type")
    d_name = DOMAIN_LABELS.get(d_idx, f"{domain[:1].upper()}{domain[1:]}")
    t_name = LOGICAL_TYPE_LABELS.get(t_idx, ltype)
    return {
        "stem": stem,
        "domain": domain,
        "domain_idx": d_idx,
        "domain_label": f"D{d_idx} {d_name}",
        "logical_type": ltype,
        "logical_type_idx": t_idx,
        "logical_type_label": f"L{t_idx} {t_name}",
    }


def load_model_runs(
    sources: list[tuple[str, str]],
    exclude_human: bool = True,
    split_by_target: bool = False,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Load runs for each (label, directory) pair, preserving input order.

    The returned dict keeps the order in which the sources were given, which is
    the order models appear on the heatmap axes.
    """
    runs_by_model: dict[str, list[dict]] = {}
    warnings: list[str] = []

    for label, directory in sources:
        runs = load_results(directory)
        if not runs:
            warnings.append(f"no result files found in '{directory}' (label '{label}')")
            continue

        kept = []
        for r in runs:
            target = str(r.get("models", {}).get("target", "unknown"))
            if exclude_human and (target == "human" or r.get("target_provider") == "human"):
                continue
            kept.append(r)
        n_dropped = len(runs) - len(kept)
        if n_dropped:
            warnings.append(f"'{label}': excluded {n_dropped} human run(s)")
        if not kept:
            warnings.append(f"'{label}': no non-human runs remain")
            continue

        if split_by_target:
            by_target: dict[str, list[dict]] = defaultdict(list)
            for r in kept:
                by_target[str(r.get("models", {}).get("target", "unknown"))].append(r)
            for target, rs in by_target.items():
                name = target if len(by_target) > 1 else label
                if name in runs_by_model:
                    name = f"{label} ({target})"
                runs_by_model[name] = rs
        else:
            if label in runs_by_model:
                warnings.append(f"duplicate label '{label}'; later entry overwrote earlier")
            runs_by_model[label] = kept

    return runs_by_model, warnings


def annotate_runs(runs_by_model: dict[str, list[dict]]) -> tuple[list[dict], list[str]]:
    """Flatten runs into records carrying model, domain, logical type and scores."""
    records: list[dict] = []
    warnings: list[str] = []
    unparsed: set[str] = set()

    for model, runs in runs_by_model.items():
        for r in runs:
            meta = parse_scenario_name(r.get("scenario_file", ""))
            if meta["domain"] == "unknown":
                unparsed.add(str(r.get("scenario_file")))
            scores = r.get("scores") or {}
            eff = r.get("efficiency_components") or {}
            severe, fmt = run_violation_codes(r)
            records.append({
                "model": model,
                "scenario": meta["stem"],
                "domain": meta["domain"],
                "domain_idx": meta["domain_idx"],
                "domain_label": meta["domain_label"],
                "logical_type": meta["logical_type"],
                "logical_type_idx": meta["logical_type_idx"],
                "logical_type_label": meta["logical_type_label"],
                **{k: scores.get(k) for k in SCORE_KEYS},
                "coverage": eff.get("coverage"),
                "eff_a": eff.get("eff_a"),
                "eff_b": eff.get("eff_b"),
                "question_count": r.get("question_count"),
                "compliance_rate": r.get("compliance_rate"),
                "had_severe_violation": bool(r.get("had_severe_violation")),
                "had_format_violation": bool(r.get("had_format_violation")),
                "severe_codes": sorted(severe),
                "format_codes": sorted(fmt),
            })

    if unparsed:
        warnings.append(
            f"{len(unparsed)} scenario name(s) did not match the naming pattern and "
            f"were labelled 'unknown': {sorted(unparsed)[:5]}"
            + (" ..." if len(unparsed) > 5 else "")
        )
    return records, warnings


def model_order(runs_by_model: dict[str, list[dict]]) -> list[str]:
    """Models in the order they were supplied on the command line / manifest."""
    return list(runs_by_model)


def ordered_groups(records: list[dict], group_key: str) -> list[str]:
    """Group labels ordered by their benchmark ID, with 'unknown' last."""
    label_f, idx_f = GROUP_FIELDS[group_key]
    seen: dict[str, Optional[int]] = {}
    for r in records:
        seen.setdefault(r[label_f], r[idx_f])
    return [
        label for label, _ in sorted(
            seen.items(),
            key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else 0, kv[0]),
        )
    ]


def _mean(values: list) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


class Matrix:
    """A labelled 2-D table of floats with optional per-cell counts.

    ``vmin``/``vmax`` fix the colour scale when set; otherwise renderers use the
    data range.
    """

    def __init__(self, title: str, row_label: str, col_label: str,
                 rows: list[str], cols: list[str],
                 cells: dict[tuple[str, str], Optional[float]],
                 counts: Optional[dict[tuple[str, str], int]] = None,
                 value_fmt: str = "{:.1f}",
                 vmin: Optional[float] = None, vmax: Optional[float] = None,
                 diverging: bool = False):
        self.title = title
        self.row_label = row_label
        self.col_label = col_label
        self.rows = rows
        self.cols = cols
        self.cells = cells
        self.counts = counts or {}
        self.value_fmt = value_fmt
        self.vmin = vmin
        self.vmax = vmax
        self.diverging = diverging

    def value(self, r: str, c: str) -> Optional[float]:
        return self.cells.get((r, c))

    def row_mean(self, r: str) -> Optional[float]:
        return _mean([self.cells.get((r, c)) for c in self.cols])

    def col_mean(self, c: str) -> Optional[float]:
        return _mean([self.cells.get((r, c)) for r in self.rows])

    def finite_values(self) -> list[float]:
        return [v for v in self.cells.values() if isinstance(v, (int, float))]

    def color_range(self) -> tuple[float, float]:
        """Colour scale limits: fixed when vmin/vmax are set, else data range."""
        if self.vmin is not None and self.vmax is not None:
            return self.vmin, self.vmax
        finite = self.finite_values()
        if not finite:
            return 0.0, 1.0
        lo, hi = min(finite), max(finite)
        return (lo, hi) if hi > lo else (lo, lo + 1.0)

    def to_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([self.row_label] + self.cols + ["mean"])
            for r in self.rows:
                row = [r]
                for c in self.cols:
                    v = self.value(r, c)
                    row.append("" if v is None else round(v, 3))
                rm = self.row_mean(r)
                row.append("" if rm is None else round(rm, 3))
                w.writerow(row)
            footer = ["mean"]
            for c in self.cols:
                cm = self.col_mean(c)
                footer.append("" if cm is None else round(cm, 3))
            footer.append("")
            w.writerow(footer)

    def render_text(self, width: int = 11, show_counts: bool = False) -> str:
        head_w = max([len(self.row_label)] + [len(r) for r in self.rows]) + 1
        lines = [self.title, "-" * (head_w + (width + 1) * (len(self.cols) + 1))]
        header = f"{self.row_label:<{head_w}}" + "".join(
            f"{c[:width - 1]:>{width}}" for c in self.cols) + f"{'mean':>{width}}"
        lines.append(header)
        for r in self.rows:
            line = f"{r[:head_w - 1]:<{head_w}}"
            for c in self.cols:
                v = self.value(r, c)
                if v is None:
                    line += f"{'-':>{width}}"
                else:
                    txt = self.value_fmt.format(v)
                    if show_counts:
                        txt += f"({self.counts.get((r, c), 0)})"
                    line += f"{txt:>{width}}"
            rm = self.row_mean(r)
            line += f"{'-' if rm is None else self.value_fmt.format(rm):>{width}}"
            lines.append(line)
        footer = f"{'mean':<{head_w}}"
        for c in self.cols:
            cm = self.col_mean(c)
            footer += f"{'-' if cm is None else self.value_fmt.format(cm):>{width}}"
        lines.append(footer)
        return "\n".join(lines)


def build_group_matrix(records: list[dict], group_key: str, score_key: str,
                       title: str, models: list[str]) -> Matrix:
    """M1 / M2: model x (logical type | domain) mean of one score."""
    label_f = GROUP_FIELDS[group_key][0]
    groups = ordered_groups(records, group_key)
    bucket: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        bucket[(r["model"], r[label_f])].append(r.get(score_key))
    cells = {k: _mean(v) for k, v in bucket.items()}
    counts = {k: len([x for x in v if isinstance(x, (int, float))])
              for k, v in bucket.items()}
    return Matrix(title, "Model", GROUP_DISPLAY[group_key], models, groups,
                  cells, counts, vmin=SCORE_RANGE[0], vmax=SCORE_RANGE[1])


def build_summary_matrix(records: list[dict], models: list[str]) -> Matrix:
    """M3 / M4: model x score dimension."""
    bucket: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        for k in SCORE_KEYS:
            bucket[(r["model"], k)].append(r.get(k))
    cells = {k: _mean(v) for k, v in bucket.items()}
    counts = {k: len([x for x in v if isinstance(x, (int, float))])
              for k, v in bucket.items()}
    return Matrix("M3/M4. Model x score dimension (mean)", "Model", "Dimension",
                  models, SCORE_KEYS, cells, counts,
                  vmin=SCORE_RANGE[0], vmax=SCORE_RANGE[1])


def build_relative_matrix(base: Matrix, title: str) -> Matrix:
    """M5: deviation of each cell from that column's cross-model mean."""
    cells: dict[tuple[str, str], Optional[float]] = {}
    for c in base.cols:
        cm = base.col_mean(c)
        for r in base.rows:
            v = base.value(r, c)
            cells[(r, c)] = None if (v is None or cm is None) else v - cm
    return Matrix(title, base.row_label, base.col_label, base.rows, base.cols,
                  cells, base.counts, value_fmt="{:+.1f}", diverging=True)


def build_violation_rate_matrix(records: list[dict], group_key: str, tier: str,
                                title: str, models: list[str]) -> Matrix:
    """M6: model x group violation rate (share of scenarios), in percent."""
    field = "had_severe_violation" if tier == "severe" else "had_format_violation"
    label_f = GROUP_FIELDS[group_key][0]
    groups = ordered_groups(records, group_key)
    bucket: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        bucket[(r["model"], r[label_f])].append(1.0 if r[field] else 0.0)
    cells = {k: (_mean(v) * 100 if _mean(v) is not None else None)
             for k, v in bucket.items()}
    counts = {k: len(v) for k, v in bucket.items()}
    return Matrix(title, "Model", GROUP_DISPLAY[group_key], models, groups,
                  cells, counts, vmin=0.0, vmax=100.0)


def build_violation_code_matrix(records: list[dict], models: list[str]) -> Matrix:
    """M7: violation code x model, share of scenarios in percent."""
    codes = SEVERE_CODES + FORMAT_CODES
    totals = {m: len([r for r in records if r["model"] == m]) for m in models}
    cells: dict[tuple[str, str], Optional[float]] = {}
    counts: dict[tuple[str, str], int] = {}
    for code in codes:
        for m in models:
            hits = len([
                r for r in records
                if r["model"] == m
                and code in (r["severe_codes"] + r["format_codes"])
            ])
            cells[(code, m)] = (hits / totals[m] * 100) if totals[m] else None
            counts[(code, m)] = hits
    return Matrix("M7. Violation code x model (% of scenarios)", "Code", "Model",
                  codes, models, cells, counts, vmin=0.0, vmax=100.0)


def build_fact_matrix(records: list[dict], models: list[str]) -> Matrix:
    """M8: model x fact-acquisition and efficiency components.

    Columns mix units (0-1 ratios and a question count), so the colour scale is
    left to the data range rather than fixed.
    """
    cols = ["coverage", "eff_a", "eff_b", "question_count", "compliance_rate"]
    bucket: dict[tuple[str, str], list] = defaultdict(list)
    for r in records:
        for c in cols:
            bucket[(r["model"], c)].append(r.get(c))
    cells = {k: _mean(v) for k, v in bucket.items()}
    counts = {k: len([x for x in v if isinstance(x, (int, float))])
              for k, v in bucket.items()}
    return Matrix("M8. Model x fact acquisition / efficiency components", "Model",
                  "Metric", models, cols, cells, counts, value_fmt="{:.2f}")


def export_records_csv(records: list[dict], path: Path) -> None:
    """Per-run long-format CSV for downstream analysis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["model", "scenario", "domain_label", "logical_type_label",
            "domain", "logical_type", *SCORE_KEYS,
            "coverage", "eff_a", "eff_b", "question_count", "compliance_rate",
            "had_severe_violation", "had_format_violation"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow(r)
