"""Heatmap rendering and HTML report generation for model comparison.

matplotlib is imported lazily so the comparison tool still runs (text and CSV
output only) when it is not installed. No seaborn dependency: heatmaps are drawn
with matplotlib's imshow.

Vector formats are written directly by matplotlib rather than converted from
PNG, so PDF/SVG output stays sharp at any zoom and keeps selectable text - which
is what publication figures need. PDFs embed TrueType (Type 42) fonts, since
many venues reject the Type 3 fonts matplotlib emits by default.
"""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path
from typing import Optional

from .compare import Matrix

# Formats matplotlib can write for a figure.
SUPPORTED_FORMATS = ["png", "pdf", "svg"]


def _matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    # Type 42 (TrueType) keeps text selectable and satisfies most publishers.
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt
    return plt


def _label_color(value: float, lo: float, hi: float, diverging: bool) -> str:
    """Pick a readable text colour for a cell.

    Sequential (viridis): high values are bright, so they need dark text.
    Diverging (RdBu_r): both extremes are dark, the centre is light.
    """
    span = (hi - lo) or 1.0
    if diverging:
        extreme = abs(value) / (max(abs(lo), abs(hi)) or 1.0)
        return "white" if extreme > 0.55 else "black"
    norm = (value - lo) / span
    return "black" if norm > 0.55 else "white"


def render_heatmap(matrix: Matrix, out_dir: Optional[Path] = None,
                   stem: Optional[str] = None,
                   formats: Optional[list[str]] = None,
                   cmap: str = "viridis",
                   annotate: bool = True) -> Optional[bytes]:
    """Draw a heatmap for a Matrix and write it in each requested format.

    Returns the PNG bytes (for embedding in the HTML report), or None when
    matplotlib is unavailable or the matrix has no finite values.
    """
    try:
        plt = _matplotlib()
    except ImportError:
        return None

    if not matrix.finite_values():
        return None

    formats = formats or ["png"]
    diverging = matrix.diverging
    lo, hi = matrix.color_range()
    if diverging:
        lim = max(abs(v) for v in matrix.finite_values()) or 1.0
        lo, hi, use_cmap = -lim, lim, "RdBu_r"
    else:
        use_cmap = cmap

    data = [[matrix.value(r, c) for c in matrix.cols] for r in matrix.rows]
    masked = [[float("nan") if v is None else v for v in row] for row in data]

    fig_w = max(6.0, 1.15 * len(matrix.cols) + 3.5)
    fig_h = max(3.0, 0.6 * len(matrix.rows) + 2.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    im = ax.imshow(masked, cmap=use_cmap, vmin=lo, vmax=hi, aspect="auto")

    ax.set_xticks(range(len(matrix.cols)))
    ax.set_xticklabels(matrix.cols, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(matrix.rows)))
    ax.set_yticklabels(matrix.rows, fontsize=9)
    ax.set_xlabel(matrix.col_label)
    ax.set_ylabel(matrix.row_label)
    ax.set_title(matrix.title, fontsize=11, pad=12)

    if annotate:
        for i, r in enumerate(matrix.rows):
            for j, c in enumerate(matrix.cols):
                v = matrix.value(r, c)
                if v is None:
                    ax.text(j, i, "-", ha="center", va="center",
                            fontsize=8, color="#999999")
                    continue
                ax.text(j, i, matrix.value_fmt.format(v), ha="center", va="center",
                        fontsize=8, color=_label_color(v, lo, hi, diverging))

    fig.colorbar(im, ax=ax, shrink=0.85)
    fig.tight_layout()

    if out_dir and stem:
        out_dir.mkdir(parents=True, exist_ok=True)
        for fmt in formats:
            if fmt not in SUPPORTED_FORMATS:
                continue
            fig.savefig(out_dir / f"{stem}.{fmt}", format=fmt, bbox_inches="tight")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _matrix_to_html_table(matrix: Matrix) -> str:
    lo, hi = matrix.color_range()
    span = (hi - lo) or 1.0

    head = "".join(f"<th>{html.escape(c)}</th>" for c in matrix.cols)
    rows_html = []
    for r in matrix.rows:
        cells = []
        for c in matrix.cols:
            v = matrix.value(r, c)
            if v is None:
                cells.append('<td class="na">-</td>')
                continue
            norm = min(1.0, max(0.0, (v - lo) / span))
            # Light-to-dark blue shading; dark cells get white text.
            bg = f"rgba(31,110,189,{0.06 + 0.74 * norm:.3f})"
            fg = "#ffffff" if norm > 0.55 else "#111111"
            n = matrix.counts.get((r, c))
            title = f"n={n}" if n is not None else ""
            cells.append(
                f'<td style="background:{bg};color:{fg}" title="{title}">'
                f"{matrix.value_fmt.format(v)}</td>"
            )
        rm = matrix.row_mean(r)
        cells.append(
            f'<td class="mean">{"-" if rm is None else matrix.value_fmt.format(rm)}</td>')
        rows_html.append(f"<tr><th>{html.escape(r)}</th>{''.join(cells)}</tr>")

    foot = "".join(
        f'<td class="mean">'
        f'{"-" if matrix.col_mean(c) is None else matrix.value_fmt.format(matrix.col_mean(c))}'
        f"</td>"
        for c in matrix.cols
    )
    return (
        f'<table><thead><tr><th>{html.escape(matrix.row_label)}</th>{head}'
        f'<th class="mean">mean</th></tr></thead>'
        f"<tbody>{''.join(rows_html)}</tbody>"
        f'<tfoot><tr><th class="mean">mean</th>{foot}<td></td></tr></tfoot></table>'
    )


HTML_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
margin:32px auto;max-width:1180px;color:#1a1a1a;line-height:1.5}
h1{font-size:24px;border-bottom:2px solid #1f6ebd;padding-bottom:8px}
h2{font-size:18px;margin-top:36px;color:#1f6ebd}
p.meta{color:#666;font-size:13px}
table{border-collapse:collapse;margin:12px 0;font-size:13px}
th,td{border:1px solid #d8d8d8;padding:5px 9px;text-align:right}
thead th,tbody th{background:#f4f6f8;text-align:left;font-weight:600}
td.mean,th.mean{background:#eef2f6;font-weight:600}
td.na{color:#bbb;text-align:center}
img{max-width:100%;margin:12px 0;border:1px solid #e2e2e2;border-radius:4px}
.warn{background:#fff8e1;border-left:4px solid #f5a623;padding:10px 14px;
margin:14px 0;font-size:13px}
.note{color:#666;font-size:12px;margin-top:-6px}
"""


def build_html_report(sections: list[dict], meta: dict,
                      warnings: list[str], path: Path) -> None:
    """Write a self-contained HTML report with embedded PNG heatmaps."""
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Model Comparison Report</title>",
        f"<style>{HTML_CSS}</style></head><body>",
        "<h1>Model Comparison Report</h1>",
        f"<p class='meta'>Models: {html.escape(', '.join(meta.get('models', [])))}<br>"
        f"Runs: {html.escape(str(meta.get('n_runs', '')))}<br>"
        f"Logical types: {html.escape(str(meta.get('n_logical_types', '')))} &middot; "
        f"Domains: {html.escape(str(meta.get('n_domains', '')))}<br>"
        f"Human runs excluded: {html.escape(str(meta.get('exclude_human', True)))}</p>",
    ]
    for w in warnings:
        parts.append(f"<div class='warn'>{html.escape(w)}</div>")

    for sec in sections:
        matrix: Matrix = sec["matrix"]
        parts.append(f"<h2>{html.escape(matrix.title)}</h2>")
        if sec.get("note"):
            parts.append(f"<p class='note'>{html.escape(sec['note'])}</p>")
        png = sec.get("png")
        if png:
            b64 = base64.b64encode(png).decode("ascii")
            parts.append(f"<img src='data:image/png;base64,{b64}' alt='heatmap'>")
        parts.append(_matrix_to_html_table(matrix))

    parts.append("</body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
