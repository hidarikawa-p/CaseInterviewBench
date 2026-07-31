# Scenario-based Problem-Solving Evaluation

Evaluates a target on interactive problem-solving scenarios. In each scenario
the target investigates a hidden situation by asking one question at a time; a
**facilitator** discloses hidden facts under fixed reveal/withhold conditions;
and a **judge** scores the target's final problem formulation and proposed
resolution against a ground truth.

The target can be either an **LLM** (batch evaluation over a folder of
scenarios, `run_eval.py`) or a **human** (interactive Streamlit UI, one
scenario at a time, `app_human.py`). Both share the same scenarios, config,
facilitator, judge, scoring, and output schema, so their results are directly
comparable and can be aggregated together.

The facilitator and judge can each run on a different provider (Anthropic,
OpenAI, Gemini, or AWS Bedrock), selected in a config file. For an LLM target,
the target provider is chosen the same way; for a human target it is the person
using the UI.

## Two ways to run

| Target | Entrypoint | Scope | Command |
|--------|-----------|-------|---------|
| LLM    | `run_eval.py`  | all scenarios in a folder, resumable | `python run_eval.py --scenarios ./scenarios` |
| Human  | `app_human.py` | one scenario per session | `streamlit run app_human.py -- --scenarios ./scenarios` |

Finished results can also be re-scored with a different judge without replaying
the dialogue — see **Re-judging existing results**.


## Installation

```bash
pip install -r requirements.txt
```

Only the SDK for the providers you configure is needed — adapters import their
SDK lazily. Uncomment the relevant lines in `requirements.txt`.

## Quick start

1. Copy the example config and edit it:

   ```bash
   cp config.example.json config.json
   ```

2. Put your prompt templates in `./prompts/`:
   - `prompts/guideline_prompt.md` — shown to the target. Must contain the
     placeholders `[PUBLIC_CONTEXT]` and `[INITIAL_REQUEST]`.
   - `prompts/judge_prompt.md` — shown to the judge. Must contain the judge
     placeholders (e.g. `[PUBLIC_CONTEXT]`, `[EXPECTED_PROBLEM_FORMULATION]`,
     `[ACQUIRED_HIDDEN_FACTS]`, `[PARTICIPANT_FINAL_ANSWER]`, …).

3. Set the API credentials for your providers (see below).

4. Run the evaluation. For an **LLM target**, run against a folder of scenarios:

   ```bash
   python run_eval.py --scenarios ./scenarios --config config.json --output ./results
   ```

   For a **human target**, launch the Streamlit UI instead (steps 1–3 are the
   same; see **Human target** below):

   ```bash
   streamlit run app_human.py -- --scenarios ./scenarios --config config.json --output ./results
   ```

## Human target (Streamlit UI)

To evaluate a **person** as the target instead of a model, use the Streamlit
app. The facilitator and judge still run on the providers in the config; only
the target is replaced by human input, and the saved output uses the same
schema as an LLM run (with `target_provider` / `target_model` set to `human`).

### Prerequisites

The same as the CLI (a config file, prompt templates, and a scenarios folder),
plus Streamlit and the SDKs for whatever providers the facilitator and judge
use. The `target` section of the config is ignored for a human run, so it does
not need a valid provider.

```bash
pip install streamlit
```

### Launch

```bash
streamlit run app_human.py -- --config config.json \
    --scenarios ./scenarios --output ./results
```

Note the `--` separator: everything after it is passed to the app, not to
Streamlit. All three arguments are optional and default to `config.json`,
`./scenarios`, and `./results`.

### What the person does

1. Pick a scenario from the sidebar and press **Start / Restart scenario**.
2. Read the public context and initial request (the full guideline is available
   in an expander).
3. Ask questions one at a time in a plain text box — **one sentence, no
   `<conclusion>` tag, no JSON**. Each facilitator reply appears inline, colour
   coded (green = a fact was disclosed, blue = no information, amber/red = a
   violation).
4. Press **Stop and write final answer** at any point, or keep going until the
   question limit is reached.
5. Fill four separate fields — problem formulation, problem evidence,
   resolution, resolution rationale — and submit. No formatting is required.
6. See the four scores immediately, expand the full result JSON, and download
   the result file.

### Result file

Each session writes `eval_result_<scenario>__human_<timestamp>.json` to the
output folder. The timestamp keeps repeated attempts at the same scenario from
overwriting each other, and the `__human_` marker keeps human runs distinct
from the LLM file name (`eval_result_<scenario>.json`), so both can live in the
same output folder without collision.

### Rules and scope

The same protocol rules apply: a multi-sentence question, or one that reads as
a fabricated interviewer turn, counts as a violation, consumes a question, and
discloses nothing. Only one scenario is evaluated per session (there is no
multi-scenario loop for humans). Human result files share the LLM schema and
aggregate together under the `human` model label — see **Aggregating LLM and
human results** below.

## CLI

```
python run_eval.py --scenarios PATH [--config config.json]
                   [--output ./results] [--force]
```

- `--scenarios` (required): folder of scenario `*.json` files.
- `--config`: model/provider config (default `config.json`).
- `--output`: folder for per-scenario results and the aggregate summary
  (default `./results`).
- `--force`: re-run scenarios even if their result file already exists.

### Resuming

Every scenario writes `results/eval_result_<name>.json`. On the next run, any
scenario whose result file already exists is **skipped** but still included in
the aggregation. An interrupted run therefore resumes simply by re-running the
same command; use `--force` to recompute.

## Re-judging existing results

To re-score finished runs with a **different judge model**, use `rejudge.py`.
It replays only the judging step through the same functions the original run
used (`run_judge`, `zero_judge_result`, `build_result`), so scoring stays
consistent. The dialogue is never replayed — the transcript, disclosures, and
violation counts are carried over unchanged, and the target model is untouched.

```bash
python rejudge.py --results ./results --scenarios ./scenarios \
    --config config_judge_b.json --output ./results_judge_b
```

The new judge comes from the `judge` section of `--config`; the `target` and
`facilitator` sections are ignored. `--scenarios` is required because the
ground truth and hidden-fact contents are not stored in result files.

Behaviour worth knowing:

- **`--output` must differ from `--results`.** Re-judged files keep the same
  target label, so mixing them with the originals in one folder would make
  aggregation count every scenario twice. The script refuses to run in that case.
- **Resumable**: an output file that already exists is skipped unless `--force`.
- **Zero-scored runs stay zero**: if the original answer failed format
  validation, the re-judge preserves that verdict without spending a judge call.
- **Provenance**: each re-judged file adds `rejudged`, `original_judge`, and
  `original_scores`, so judges can be compared without opening both files.
- **Efficiency and fact-acquisition scores are unchanged** by design — only the
  judged dimensions can move.

### Older result files

`rejudge.py` accepts result files produced by earlier versions of the harness.
Missing fields are handled as follows, and every fallback is reported on the
console so it is never silent:

- **`max_questions`** (added later): the value from `--config` is used, and the
  file records `"max_questions_source": "config"`. Make sure the config matches
  the question limit of the original run — the efficiency score depends on it.
- **`format_valid`**: derived by re-validating the recovered final answer rather
  than assumed false, so a valid old answer is still sent to the judge instead
  of being silently zero-scored.
- **Violation counters, `target_provider`, `target_model`**: default to `0` /
  `"unknown"`. These do not affect the judged dimensions.

## Aggregating existing results

To summarise result files without running or re-judging anything, use
`aggregate_results.py`. It calls the same aggregation functions `run_eval.py`
uses, makes no API calls, and needs no config file, so it works on LLM runs,
human sessions, or a mix.

```bash
python aggregate_results.py --results ./results
python aggregate_results.py --results ./results --filter-target human --per-scenario
```

- `--filter-target` keeps only runs whose target label contains the given
  substring (e.g. `human`, `fable`).
- `--per-scenario` also lists each run with its overall score and violation tier.
- `--output` sets the summary path (default `<results>/aggregate_summary.json`);
  pass `none` to print without writing.

Two things to keep in mind when aggregating human sessions:

- Human result files are timestamped, so repeated attempts at the same scenario
  never overwrite each other and are **counted as separate runs**. The script
  prints a note listing any duplicates so the denominator is not a surprise.
- Every human session is labelled `human`, so runs by different people are
  pooled into one group. Keep separate people in separate output folders if
  they need to be reported separately.

## Config file

Each role is configured independently:

```json
{
  "target":      { "provider": "anthropic", "model": "claude-fable-5", "temperature": 1.0 },
  "facilitator": { "provider": "anthropic", "model": "claude-sonnet-5", "temperature": 0.0 },
  "judge":       { "provider": "anthropic", "model": "claude-opus-4-8", "temperature": 0.0 },
  "prompts":     { "guideline_path": "./prompts/guideline_prompt.md",
                   "judge_path": "./prompts/judge_prompt.md" },
  "run":         { "max_questions": 20, "word_limit": 100, "max_retries": 4 }
}
```

Per-role fields: `provider`, `model`, `temperature`, `max_tokens`, `region`
(Bedrock), and `extra` (a dict of provider-specific keyword arguments).

- **`temperature: null`** (or omitted) leaves temperature unset so the provider
  default applies. Note: some providers require `temperature = 1` when extended
  thinking / reasoning is enabled — set it accordingly if you enable that.

## Providers and credentials

| provider    | SDK            | credentials (env)                         | model field example |
|-------------|----------------|-------------------------------------------|---------------------|
| `anthropic` | `anthropic`    | `ANTHROPIC_API_KEY`                       | `claude-sonnet-5` |
| `openai`    | `openai`       | `OPENAI_API_KEY`                          | `gpt-4o` |
| `gemini`    | `google-genai` | `GEMINI_API_KEY` / `GOOGLE_API_KEY`       | `gemini-2.5-pro` |
| `bedrock`   | `boto3`        | standard AWS credential chain + `region`  | `anthropic.claude-3-5-sonnet-20241022-v2:0` |

All adapters share one interface: given a system prompt and a message list,
return one text string. Anthropic additionally reports `stop_reason` and any
dropped non-text blocks; other providers report what they can and leave the
rest `null`.

## Scenario format

Each scenario `*.json` contains:

- `public_context`, `initial_request`
- `ground_truth`: `expected_problem_formulation`,
  `problem_formulation_rationale`, `expected_resolution`, `resolution_rationale`
- `hidden_facts`: list of `{ fact_id, content, reveal_if, do_not_reveal_if,
  required_for_problem_formulation, required_for_resolution }`

## How a scenario runs

1. The guideline prompt (with context filled in) is given to the target, which
   asks one single-sentence question per turn.
2. Each question is checked for protocol violations, then passed to the
   facilitator, which sees only the public context, the **undisclosed** facts,
   and the current question (never the history) and discloses **at most one**
   fact per question.
3. The loop ends when the target emits a `<conclusion>` block or reaches the
   question limit. A malformed final answer gets one strict retry; if it still
   fails, the four judged dimensions are scored 0 without calling the judge.
4. The judge scores the final answer; scores are computed and saved.

### Protocol violations

- **Format** violations (not one single-sentence question) and **severe**
  violations (impersonating or fabricating the interviewer's turn — role
  markers, echoing canned replies, or appending a non-question continuation)
  both cost one consumed question and disclose nothing.
- On any violation, only the extracted question sentence is kept in history, so
  fabricated "interviewer" text cannot re-enter the target's context.
- Violations are **not** folded into any score. They are recorded per turn and
  summarised per scenario for aggregation.

## Scores (0–100)

- **problem_recognition**: mean of judged `problem_formulation`,
  `problem_evidence` (each /3) and the fraction of
  `required_for_problem_formulation` facts acquired.
- **resolution**: mean of judged `resolution`, `resolution_rationale` (each /3)
  and the fraction of `required_for_resolution` facts acquired.
- **efficiency**: mean of `eff_a` and `eff_b`.
  - `eff_a` = required-fact coverage × restraint after acquiring them
    (`restraint = 1 − (questions_used − k) / (max_questions − k)`, where `k` is
    the question index of the last required fact; 0 if none acquired).
  - `eff_b` = disclosed facts / questions used (capped at 1).
- **overall**: mean of the three.

## Output

- `results/eval_result_<scenario>.json` — an LLM run: full transcript, final
  answer, judge verdict, scores, and per-scenario violation flags
  (`had_severe_violation`, `had_format_violation`, `severe_violation_turns`, …).
- `results/eval_result_<scenario>__human_<timestamp>.json` — a human run, same
  schema, with `target_provider` / `target_model` set to `human`.
- `results/aggregate_summary.json` — per target model:
  - `severe_violation_scenario_rate`, `format_only_violation_scenario_rate`,
    `clean_scenario_rate` (mutually exclusive, sum to 1),
  - `mean_compliance_rate`, and score means for reference.

Violation shares and score means are reported side by side and kept
independent: scenarios with violations still contribute their scores to the
means, so the two views can be read separately.

### Aggregating LLM and human results

LLM and human runs can share one output folder. Their file names differ (the
human suffix `__human_<timestamp>` never collides with the LLM name), and the
resumable-skip check only matches the exact LLM file name, so human files are
ignored when deciding what to re-run. Aggregation groups every result file by
its `models.target` label, so an LLM model (e.g. `anthropic:claude-fable-5`)
and `human` appear as separate rows in the same summary and can be compared
directly.

## Package layout

```
run_eval.py                 CLI entrypoint (folder scan, skip, aggregate)
rejudge.py                  Re-score existing results with a different judge
aggregate_results.py        Summarise existing results (no API calls)
app_human.py                Streamlit UI for a human target (single scenario)
config.example.json         Config template
requirements.txt            Optional per-provider SDKs
scenario_eval/
  providers.py              Provider adapters (lazy imports) + ModelSpec
  config.py                 Config/scenario loading, template fill, JSON utils
  violations.py             Format + severe violation detection, canned replies
  facilitator.py            Single-fact disclosure logic + facilitator prompt
  judge.py                  Final-answer validation + judge invocation
  scoring.py                Score computation (case-A efficiency)
  results.py                Shared result-dict builder (LLM + human)
  human.py                  Human-target turn logic + conclusion assembly
  runner.py                 Per-scenario dialogue loop and orchestration
  aggregate.py              Multi-scenario aggregation
```
