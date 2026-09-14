# Utilities

Tools for working with CaseInterviewBench results after a run. See
[README.md](README.md) for running the benchmark itself.

| Tool | Purpose | API calls |
|---|---|---|
| [`aggregate_results.py`](#aggregate_resultspy) | Summarise existing result files | none |
| [`compare_models.py`](#compare_modelspy) | Cross-model tables, heatmaps, HTML report | none |
| [`rejudge.py`](#rejudgepy) | Re-score finished runs with a different judge | judge only |
| [`app_human.py`](#app_humanpy) | Run one scenario with a human as the target | facilitator + judge |

---

## aggregate_results.py

Prints the same per-model summary that `run_eval.py` prints at the end of a run,
from result files that already exist. No config file and no API calls, so it
works on any mix of LLM and human results.

```bash
python aggregate_results.py --results ./results
python aggregate_results.py --results ./results --filter-target human --per-scenario
```

- `--filter-target` keeps only runs whose target label contains the substring.
- `--per-scenario` lists each run with its overall score and violation tier.
- `--output` sets the summary path (default `<results>/aggregate_summary.json`);
  pass `none` to print without writing.

The summary reports, per target model: the share of scenarios with a severe
violation, with a format-only violation, and with none (mutually exclusive,
summing to 1); a per-violation-code breakdown; mean compliance rate; and score
means, including `mean_overall_severe_zeroed` (overall with severe-violation
scenarios counted as 0). Result files written before that field existed have it
derived from `had_severe_violation`, so old runs aggregate correctly.

Repeated runs of the same scenario are counted as separate runs, and the script
prints a note listing any duplicates so the denominator is not a surprise.

---

## compare_models.py

Builds comparison matrices across models, grouped by logical type and domain.

**Each model needs its own results folder.** Results for the same scenario share
a filename across models, so they cannot coexist in one folder.

```bash
# label=path pairs (quick)
python compare_models.py --results "Fable 5=./results_fable" \
    "GPT-5.6 Sol=./results_gpt" --output ./comparison

# manifest file (reproducible)
python compare_models.py --manifest models.json --output ./comparison
```

`models.json`:

```json
{
  "models": [
    {"label": "Fable 5",     "results_dir": "./results_fable"},
    {"label": "GPT-5.6 Sol", "results_dir": "./results_gpt"}
  ]
}
```

A bare path is also accepted, in which case the folder name becomes the label.

Options: `--score` picks the score for the M1/M2 matrices (default `overall`);
`--formats` selects figure formats (default `png pdf`, also `svg`);
`--include-human` adds `human` runs, excluded by default; `--no-heatmaps` skips
figures and HTML; `--split-by-target` groups runs inside one folder by target
label (subject to the filename caveat above).

### Axis labels

Scenario filenames are parsed as `NNN_<domain>__NNN_<LogicalType>.json`, where
the leading numbers are the benchmark IDs. Axis labels come from the canonical
benchmark tables keyed by those IDs, not from the abbreviated filename token, so
`008_creative__005_Surface.json` reads as domain `D8 Creative` and logical type
`L5 Surface Reversal`. Axes are ordered by ID (L1…L13, D1…D8), and models appear
in the order they were supplied. Unmatched names become `unknown`, sort last,
and are reported as a warning.

### Matrices

| ID | Matrix |
|----|--------|
| M1 | model × logical type (mean score) |
| M2 | model × domain (mean score) |
| M3/M4 | model × score dimension |
| M5 | model × logical type, deviation from each column's cross-model mean |
| M6a / M6b | severe / format violation rate by logical type |
| M7 | violation code × model (% of scenarios) |
| M8 | model × coverage, S7, S8, question count, compliance rate |

M5 is the one to read alongside M1: subtracting the column mean removes scenario
difficulty, so a positive cell means the model is *relatively* strong on that
logical type rather than merely facing easy scenarios.

Score heatmaps (M1, M2, M3/M4) use a fixed 0–100 colour scale so shading is
comparable across matrices and reports; violation-rate matrices are fixed to
0–100 %, M5 is centred on zero, and M8 (mixed units) uses its data range.

### Outputs

- console tables for all matrices
- `csv/*.csv` — one file per matrix, plus `per_run_records.csv` (one row per run)
- `figures/*.png`, `figures/*.pdf` — one heatmap per matrix. PDF and SVG are
  written directly by matplotlib as vector graphics with embedded TrueType
  (Type 42) fonts, ready to drop into a paper without rasterising.
- `comparison_report.html` — self-contained report with embedded figures
- `comparison_summary.json` — M1/M2/M3 values as JSON

matplotlib is required for the figures; without it the tool still emits console
tables, CSVs, and an HTML report with shaded tables.

---

## rejudge.py

Re-scores finished runs with a different judge model. Only the judging step is
replayed, through the same functions the original run used, so scoring stays
consistent. The dialogue is never replayed: transcript, disclosures, and
violation counts carry over unchanged.

```bash
python rejudge.py --results ./results --scenarios ./scenarios \
    --config config_judge_b.json --output ./results_judge_b
```

The new judge comes from the `judge` section of `--config`; the `target` and
`facilitator` sections are ignored. `--scenarios` is required because ground
truth and hidden-fact contents are not stored in result files.

- **`--output` must differ from `--results`.** Re-judged files keep the same
  target label, so mixing them with the originals would double-count every
  scenario in aggregation. The script refuses to run in that case.
- **Resumable**: an existing output file is skipped unless `--force`.
- **Zero-scored runs stay zero**: if the original answer failed format
  validation, that verdict is preserved without spending a judge call.
- **Provenance**: each file adds `rejudged`, `original_judge`, and
  `original_scores`.
- **Efficiency and fact-acquisition scores are unchanged** by design — only the
  judged dimensions can move.

### Older result files

Missing fields are handled as follows, and every fallback is reported on the
console:

- **`max_questions`** (added later): the value from `--config` is used and
  recorded as `"max_questions_source": "config"`. Make sure it matches the
  question limit of the original run — the efficiency score depends on it.
- **`format_valid`**: derived by re-validating the recovered final answer rather
  than assumed false, so a valid old answer still reaches the judge.
- **Violation counters, `target_provider`, `target_model`**: default to `0` /
  `"unknown"`; they do not affect the judged dimensions.

---

## app_human.py

A Streamlit UI for running a single scenario with a person as the target. The
facilitator and judge still run on the providers in the config; only the target
is replaced by human input. Output uses the same schema as an LLM run, with
`target_provider` / `target_model` set to `human`.

```bash
pip install streamlit
streamlit run app_human.py -- --config config.json \
    --scenarios ./scenarios --output ./results_human
```

Note the `--` separator: everything after it goes to the app, not to Streamlit.
All three arguments are optional and default to `config.json`, `./scenarios`,
and `./results`. The `target` section of the config is ignored, so it does not
need a valid provider.

In the UI the person picks a scenario from the sidebar, reads the public context
and initial request, asks questions one at a time in a plain text box (one
sentence, no tags or JSON), then presses **Stop and write final answer** — or
hits the question limit — and fills four separate fields: problem formulation,
problem evidence, resolution, and resolution rationale. Scores appear
immediately and the result is written to
`eval_result_<scenario>__human_<timestamp>.json`.

The same protocol rules apply: a multi-sentence question, or one that reads as a
fabricated facilitator turn, counts as a violation, consumes a question, and
discloses nothing. Only one scenario is evaluated per session.

Two notes when aggregating human sessions:

- Result files are timestamped, so repeated attempts at the same scenario never
  overwrite each other and count as separate runs.
- Every session is labelled `human`, so runs by different people are pooled into
  one group. Keep separate people in separate output folders if they need to be
  reported separately.
