# CaseInterviewBench

An interactive, interview-style benchmark for evaluating the **purpose-reflective
autonomy** of LLMs: the ability to reflect on the purpose underlying a user's
instructions — what the user actually needs and why the task should be done —
rather than following an incomplete request at face value.

Each scenario gives the target model a public context and an initial request
from a client. The model asks questions to uncover hidden facts, then states the
underlying problem and proposes a resolution. A **facilitator** decides which
hidden facts to disclose, and a **judge** scores the final answer against the
scenario's ground truth.

The benchmark contains 104 scenarios: 13 logical types × 8 domains.

## Flow

1. **Initial information** — public context and initial request are given to the
   target model.
2. **Questions & answers** — the target asks up to 20 single-sentence questions.
   For each, the facilitator discloses at most one hidden fact, based on that
   fact's reveal-if / do-not-reveal-if conditions. The target may stop early.
3. **Conclusion** — the target outputs a problem formulation, its evidence, a
   resolution, and its rationale (100 words each).
4. **Evaluation** — LLM-as-a-judge scores plus rule-based metrics.

## Installation

```bash
pip install -r requirements.txt
```

Only the SDK for the providers you configure is needed — adapters import their
SDK lazily. Uncomment the relevant lines in `requirements.txt`.

## Quick start

```bash
cp config.example.json config.json     # edit models / providers
export ANTHROPIC_API_KEY=...           # credentials for the providers you use
python run_eval.py --scenarios ./scenarios --output ./results
```

Default prompts ship in `prompts/` and are referenced by `config.example.json`,
so no prompt setup is required.

## Running the benchmark

```
python run_eval.py --scenarios PATH [--config config.json]
                   [--output ./results] [--force]
```

- `--scenarios` (required): folder of scenario `*.json` files.
- `--config`: model/provider config (default `config.json`).
- `--output`: folder for per-scenario results and the aggregate summary
  (default `./results`).
- `--force`: re-run scenarios even if a result file already exists.

**Resuming.** Every scenario writes `results/eval_result_<name>.json`. On the
next run, any scenario whose result file already exists is skipped but still
included in the aggregation, so an interrupted run resumes by re-running the
same command.

## Configuration

All model selection happens in the config file; there are no model flags on the
command line.

```json
{
  "target":      { "provider": "anthropic", "model": "claude-fable-5",  "temperature": 1.0, "max_tokens": 2000 },
  "facilitator": { "provider": "anthropic", "model": "claude-sonnet-5", "temperature": 0.0, "max_tokens": 1000 },
  "judge":       { "provider": "openai",    "model": "gpt-5.6-sol",     "temperature": 0.0, "max_tokens": 8000 },
  "prompts":     { "guideline_path": "./prompts/guideline_prompt.md",
                   "judge_path": "./prompts/judge_prompt.md" },
  "run":         { "max_questions": 20, "word_limit": 100, "max_retries": 4 }
}
```

Per-role fields: `provider`, `model`, `temperature`, `max_tokens`, `region`
(Bedrock), and any extra provider-specific keys (e.g. `"service_tier": "flex"`
for OpenAI), which are passed through to the SDK.

`temperature: null` (or omitting it) leaves temperature unset so the provider
default applies. Some providers require `temperature = 1` when extended
thinking / reasoning is enabled — set it accordingly if you enable that.

### Providers

| provider | SDK | credentials | model field example |
|---|---|---|---|
| `anthropic` | `anthropic` | `ANTHROPIC_API_KEY` | `claude-fable-5` |
| `openai` | `openai` | `OPENAI_API_KEY` | `gpt-5.6-sol` |
| `gemini` | `google-genai` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `gemini-3.6-flash` |
| `bedrock` | `boto3` | standard AWS credential chain + `region` | `zai.glm-5` |
| `huggingface` (alias `hf`) | `transformers`, `torch` | none (or `HF_TOKEN` for gated repos) | `google/gemma-3-4b-it` or `/path/to/model` |

All adapters share one interface: given a system prompt and a message list,
return one text string. Anthropic additionally reports `stop_reason` and any
dropped non-text blocks; other providers report what they can.

### Local and Hub models

Any role can use a `transformers` model, loaded from the Hub or from a local
directory. The model is loaded once on first use and reused for the run.

```json
{
  "target": {
    "provider": "huggingface",
    "model": "google/gemma-3-4b-it",
    "temperature": 1.0,
    "max_tokens": 2000,
    "device_map": "auto",
    "torch_dtype": "bfloat16"
  }
}
```

Optional keys: `device_map` (default `auto`), `torch_dtype` (default `auto`),
`trust_remote_code` (default `false`), `load_in_4bit`, `load_in_8bit`,
`revision`, `attn_implementation`, `tokenizer_model`, `seed`, and the generation
parameters `top_p`, `top_k`, `repetition_penalty`, `do_sample`, `num_beams`.

Messages are rendered with the tokenizer's chat template. If the tokenizer has
no template, loading **fails** rather than falling back to an invented
`User:`/`Assistant:` format, which would misrepresent the model's training
distribution and invite role-leakage violations. Supply `chat_template`, point
`tokenizer_model` at an instruction-tuned tokenizer, or set
`"use_chat_template": false` to accept a plain concatenation.

`temperature` maps onto sampling: `0` selects greedy decoding
(`do_sample=false`), a positive value enables sampling at that temperature, and
omitting it leaves the model's own defaults.

**Served models.** A model behind an OpenAI-compatible server (vLLM, TGI,
Ollama, LM Studio) does not need this provider — use `openai` with a `base_url`,
which keeps generation on the server and avoids loading weights in-process:

```json
{
  "target": {
    "provider": "openai",
    "model": "my-local-model",
    "base_url": "http://localhost:8000/v1",
    "api_key": "EMPTY",
    "max_tokens": 2000
  }
}
```

## Scenario format

Scenario files are named `NNN_<domain>__NNN_<LogicalType>.json` (e.g.
`008_creative__005_Surface.json` → D8 Creative, L5 Surface Reversal). Each
contains:

- `public_context`, `initial_request`
- `ground_truth`: `expected_problem_formulation`,
  `problem_formulation_rationale`, `expected_resolution`, `resolution_rationale`
- `hidden_facts`: list of `{ fact_id, content, reveal_if, do_not_reveal_if,
  required_for_problem_formulation, required_for_resolution }`

## Protocol violations

Questions must be exactly one sentence ending in one question mark, with no
other text. Two tiers are detected, and both consume a question and disclose
nothing:

- **Format violations** — multiple sentences, multiple question marks, list
  formatting, and similar.
- **Severe violations** — the model emits text formatted as if it came from the
  facilitator: role markers (`User:`), the facilitator's canned replies, or a
  non-question continuation. These can introduce information the facilitator
  never provided, so on any violation only the extracted question sentence is
  kept in history, preventing fabricated text from re-entering the model's
  context.

Violations are **not** folded into any score. They are recorded per turn and
summarised per scenario, and both rates are reported alongside the scores.

## Scores (0–100)

| Category | Components |
|---|---|
| **Problem formulation** | S1 accuracy (judge), S2 evidence accuracy (judge), S3 disclosure rate of required facts |
| **Resolution** | S4 accuracy (judge), S5 rationale accuracy (judge), S6 disclosure rate of required facts |
| **Efficiency** | S7 early stopping (unused question opportunities after the last required fact, weighted by coverage), S8 hidden-fact collection (facts obtained ÷ questions asked) |
| **Overall** | Mean of the three category scores |

Judge scores use a four-point rubric per item. A malformed final answer gets one
strict retry; if it still fails, the four judged dimensions are scored 0 without
calling the judge.

Alongside the normal overall score, each result also carries
`overall_severe_zeroed`: the overall score with any scenario containing a severe
violation counted as 0. A severe violation means the model fabricated part of
the interaction, so this variant reports what the score looks like when such a
scenario is treated as a failure rather than graded on an interaction that did
not actually happen. It is reported alongside the normal score, never in place
of it, and the aggregate summary reports its mean as
`mean_overall_severe_zeroed`.

## Output

- `results/eval_result_<scenario>.json` — transcript, final answer, judge
  verdict, scores, and per-scenario violation flags.
- `results/aggregate_summary.json` — per-model score means (including
  `mean_overall_severe_zeroed`), violation rates, and a per-violation-code
  breakdown.

## Utilities

Separate tools for working with results after a run. See
[UTILITIES.md](UTILITIES.md) for usage.

| Tool | Purpose |
|---|---|
| `aggregate_results.py` | Summarise existing result files without any API calls |
| `compare_models.py` | Cross-model comparison tables, heatmaps (PNG/PDF), and an HTML report, grouped by logical type and domain |
| `rejudge.py` | Re-score finished runs with a different judge model, without replaying the dialogue |
| `app_human.py` | Streamlit UI to run one scenario with a human as the target |

## License

| Part | License |
|---|---|
| Code (all `*.py`) | [MIT](LICENSE) |
| Scenario dataset, prompts, documentation | [CC BY 4.0](LICENSE-CC-BY-4.0.md) |

## Credits

The code in this repository was generated by [Claude](https://claude.ai)
(Anthropic) in collaboration with the authors, who specified the requirements,
reviewed the implementation, and are responsible for its correctness. The
benchmark design, scenario dataset, and evaluation methodology are the authors'
own work.

## Citation

<!-- Replace with the published reference once available. -->

```bibtex
To Be Provided
}
```

## Package layout

```
run_eval.py                 Benchmark entrypoint
config.example.json         Config template
LICENSE                     MIT, for the code
LICENSE-CC-BY-4.0.md        CC BY 4.0, for scenarios/prompts/docs
prompts/                    Default guideline and judge prompts
requirements.txt            Optional per-provider SDKs
UTILITIES.md                Documentation for the tools below
aggregate_results.py        Summarise existing results
compare_models.py           Cross-model comparison
rejudge.py                  Re-score with a different judge
app_human.py                Human-target Streamlit UI
scenario_eval/
  providers.py              Provider adapters (lazy imports) + ModelSpec
  config.py                 Config/scenario loading, template fill, JSON utils
  violations.py             Format + severe violation detection, canned replies
  facilitator.py            Single-fact disclosure logic + facilitator prompt
  judge.py                  Final-answer validation + judge invocation
  scoring.py                Score computation
  results.py                Shared result-dict builder
  runner.py                 Per-scenario dialogue loop
  human.py                  Human-target turn logic
  aggregate.py              Multi-scenario aggregation
  compare.py                Scenario-name parsing and comparison matrices
  report.py                 Heatmap rendering and HTML report
```
