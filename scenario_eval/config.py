"""Configuration loading and shared IO / text utilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers import ModelSpec


@dataclass
class Settings:
    """Top-level run settings, assembled from the config file.

    Attributes:
        target: ModelSpec for the participant being evaluated.
        facilitator: ModelSpec for the fact-disclosing interviewer.
        judge: ModelSpec for the scorer.
        guideline_path: Path to the guideline prompt template.
        judge_path: Path to the judge prompt template.
        max_questions: Question limit per scenario.
        word_limit: Soft word limit per conclusion field (logged, not scored).
        max_retries: Provider retry attempts.
    """

    target: ModelSpec
    facilitator: ModelSpec
    judge: ModelSpec
    guideline_path: Path
    judge_path: Path
    max_questions: int = 20
    word_limit: int = 100
    max_retries: int = 4


def load_settings(config_path: str | Path) -> Settings:
    """Load and validate a JSON config file into a Settings object."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))

    for role in ("target", "facilitator", "judge"):
        if role not in cfg:
            raise KeyError(f"Config is missing required section: '{role}'")

    prompts = cfg.get("prompts", {})
    guideline = prompts.get("guideline_path")
    judge_prompt = prompts.get("judge_path")
    if not guideline or not judge_prompt:
        raise KeyError(
            "Config must set prompts.guideline_path and prompts.judge_path."
        )

    run = cfg.get("run", {})
    return Settings(
        target=ModelSpec.from_dict(cfg["target"]),
        facilitator=ModelSpec.from_dict(cfg["facilitator"]),
        judge=ModelSpec.from_dict(cfg["judge"]),
        guideline_path=Path(guideline),
        judge_path=Path(judge_prompt),
        max_questions=int(run.get("max_questions", 20)),
        word_limit=int(run.get("word_limit", 100)),
        max_retries=int(run.get("max_retries", 4)),
    )


def load_scenario(path: str | Path) -> dict[str, Any]:
    """Load one scenario JSON and check the required fields exist."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("public_context", "initial_request", "ground_truth", "hidden_facts"):
        if key not in data:
            raise KeyError(f"Scenario {path} is missing required key '{key}'.")
    return data


def fill_template(template: str, mapping: dict[str, str]) -> str:
    """Replace ``[PLACEHOLDER]`` tokens with values."""
    out = template
    for key, value in mapping.items():
        out = out.replace(f"[{key}]", value)
    return out


def extract_json(text: str) -> dict:
    """Extract the first JSON object from a model response.

    Tolerates code fences and surrounding prose by scanning for a balanced
    top-level ``{...}`` block if a direct parse fails.
    """
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    depth, start = 0, None
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    raise ValueError(f"Could not extract JSON from response:\n{text[:800]}")


def word_count(text: str) -> int:
    """Count whitespace-separated tokens."""
    return len(re.findall(r"\S+", text or ""))
