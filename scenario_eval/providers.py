"""Provider abstraction layer.

Every provider is reduced to a single common operation: given a system prompt
and a list of ``{"role": "user"|"assistant", "content": str}`` messages, return
one text string plus a small ``meta`` dict.

Provider SDKs are imported lazily inside each adapter, so only the SDKs for the
providers actually configured need to be installed.

Supported provider keys: ``anthropic``, ``openai``, ``gemini``, ``bedrock``,
and ``huggingface`` (alias ``hf``) for local or Hub models run in-process with
transformers. Self-hosted models behind an OpenAI-compatible server (vLLM, TGI,
Ollama, LM Studio) use the ``openai`` provider with a ``base_url``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


class ProviderError(RuntimeError):
    """Raised when a provider call fails after all retries."""


@dataclass
class ModelSpec:
    """Configuration for one role's model.

    Attributes:
        provider: One of ``anthropic``, ``openai``, ``gemini``, ``bedrock``,
            ``huggingface`` (alias ``hf``).
        model: Provider-specific model identifier. For ``huggingface`` this is a
            Hub repo id or a local directory path.
        temperature: Sampling temperature. ``None`` means "do not set it" and
            lets the provider use its own default.
        max_tokens: Maximum tokens to generate.
        region: AWS region (Bedrock only).
        extra: Optional provider-specific keyword arguments.
    """

    provider: str
    model: str
    temperature: Optional[float] = None
    max_tokens: int = 4000
    region: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelSpec":
        known = {"provider", "model", "temperature", "max_tokens", "region", "extra"}
        extra = dict(d.get("extra", {}))
        # Fold any unknown top-level keys into extra for forward compatibility.
        for k, v in d.items():
            if k not in known:
                extra[k] = v
        return cls(
            provider=str(d["provider"]).lower(),
            model=str(d["model"]),
            temperature=d.get("temperature"),
            max_tokens=int(d.get("max_tokens", 4000)),
            region=d.get("region"),
            extra=extra,
        )


class BaseAdapter:
    """Common retry wrapper. Subclasses implement ``_call`` only."""

    def __init__(self, spec: ModelSpec, max_retries: int = 4):
        self.spec = spec
        self.max_retries = max_retries
        self._client: Any = None

    def _ensure_client(self) -> None:
        raise NotImplementedError

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        raise NotImplementedError

    def generate(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        """Return ``(text, meta)``, retrying transient failures with backoff."""
        self._ensure_client()
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                return self._call(system, messages)
            except Exception as e:  # noqa: BLE001 - normalized into ProviderError below
                last_err = e
                wait = 2 ** attempt
                print(
                    f"  [retry {attempt + 1}/{self.max_retries}] "
                    f"{self.spec.provider}:{self.spec.model} "
                    f"{type(e).__name__}: {e} -> waiting {wait}s"
                )
                time.sleep(wait)
        raise ProviderError(
            f"{self.spec.provider}:{self.spec.model} failed after "
            f"{self.max_retries} attempts: {last_err}"
        )


class AnthropicAdapter(BaseAdapter):
    """Anthropic Messages API. Exposes stop_reason and dropped-block diagnostics."""

    def _ensure_client(self) -> None:
        if self._client is None:
            import anthropic  # lazy

            self._client = anthropic.Anthropic()

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        kwargs: dict[str, Any] = dict(
            model=self.spec.model,
            max_tokens=self.spec.max_tokens,
            system=system,
            messages=messages,
        )
        if self.spec.temperature is not None:
            kwargs["temperature"] = self.spec.temperature
        kwargs.update(self.spec.extra)

        resp = self._client.messages.create(**kwargs)
        blocks = [b.type for b in resp.content]
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        meta = {
            "stop_reason": getattr(resp, "stop_reason", None),
            "block_types": blocks,
            "dropped_blocks": [b for b in blocks if b != "text"],
            "output_tokens": getattr(getattr(resp, "usage", None), "output_tokens", None),
        }
        return text, meta


class OpenAIAdapter(BaseAdapter):
    """OpenAI Chat Completions API. The system prompt becomes a system message.

    Setting ``base_url`` (and optionally ``api_key``) in the config points this
    adapter at any OpenAI-compatible server, which is how self-hosted models
    served by vLLM, TGI, Ollama or LM Studio are used.
    """

    _CLIENT_KEYS = ("base_url", "api_key", "organization", "timeout")

    def _ensure_client(self) -> None:
        if self._client is None:
            from openai import OpenAI  # lazy

            client_kwargs = {k: self.spec.extra[k] for k in self._CLIENT_KEYS
                             if k in self.spec.extra}
            self._client = OpenAI(**client_kwargs)

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        full = [{"role": "system", "content": system}] + messages
        # OpenAI deprecated max_tokens; max_completion_tokens works on all
        # current models and is required by the newer ones, so use it uniformly.
        kwargs: dict[str, Any] = dict(
            model=self.spec.model,
            messages=full,
            max_completion_tokens=self.spec.max_tokens,
        )
        if self.spec.temperature is not None:
            kwargs["temperature"] = self.spec.temperature
        # spec.extra may carry provider-specific options such as
        # service_tier="flex"; it is merged last so config can override
        # defaults. Client-construction keys are excluded from the request body.
        kwargs.update({k: v for k, v in self.spec.extra.items()
                       if k not in self._CLIENT_KEYS})

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        meta = {
            "stop_reason": getattr(choice, "finish_reason", None),
            "block_types": None,
            "dropped_blocks": [],
            "output_tokens": getattr(getattr(resp, "usage", None), "completion_tokens", None),
        }
        return text, meta


class GeminiAdapter(BaseAdapter):
    """Google Gemini via the google-genai SDK.

    The system prompt is passed through ``system_instruction``; the message list
    is converted to Gemini's ``contents`` format (assistant -> ``model``).
    """

    def _ensure_client(self) -> None:
        if self._client is None:
            from google import genai  # lazy

            self._client = genai.Client()

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        from google.genai import types  # lazy

        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))

        cfg: dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": self.spec.max_tokens,
        }
        if self.spec.temperature is not None:
            cfg["temperature"] = self.spec.temperature
        cfg.update(self.spec.extra)

        resp = self._client.models.generate_content(
            model=self.spec.model,
            contents=contents,
            config=types.GenerateContentConfig(**cfg),
        )
        text = (getattr(resp, "text", None) or "").strip()
        finish = None
        try:
            finish = str(resp.candidates[0].finish_reason)
        except (AttributeError, IndexError):
            pass
        meta = {
            "stop_reason": finish,
            "block_types": None,
            "dropped_blocks": [],
            "output_tokens": getattr(
                getattr(resp, "usage_metadata", None), "candidates_token_count", None
            ),
        }
        return text, meta


class BedrockAdapter(BaseAdapter):
    """Amazon Bedrock via the boto3 Converse API.

    The Converse API is model-agnostic, so the same adapter serves Claude,
    Llama, Titan, and other Bedrock-hosted models. ``model`` is the Bedrock
    model ID or inference profile ARN.
    """

    def _ensure_client(self) -> None:
        if self._client is None:
            import boto3  # lazy

            region = self.spec.region or "us-east-1"
            self._client = boto3.client("bedrock-runtime", region_name=region)

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        converse_messages = [
            {"role": m["role"], "content": [{"text": m["content"]}]} for m in messages
        ]
        inference_config: dict[str, Any] = {"maxTokens": self.spec.max_tokens}
        if self.spec.temperature is not None:
            inference_config["temperature"] = self.spec.temperature

        kwargs: dict[str, Any] = dict(
            modelId=self.spec.model,
            messages=converse_messages,
            system=[{"text": system}],
            inferenceConfig=inference_config,
        )
        kwargs.update(self.spec.extra)

        resp = self._client.converse(**kwargs)
        parts = resp["output"]["message"]["content"]
        text = "".join(p.get("text", "") for p in parts).strip()
        meta = {
            "stop_reason": resp.get("stopReason"),
            "block_types": None,
            "dropped_blocks": [],
            "output_tokens": resp.get("usage", {}).get("outputTokens"),
        }
        return text, meta


class HuggingFaceAdapter(BaseAdapter):
    """Local or Hub models loaded with transformers.

    ``model`` is either a Hub repo id (``google/gemma-3-4b-it``) or a local
    directory. The model and tokenizer are loaded once on first use and reused
    for the rest of the run.

    Messages are rendered with the tokenizer's chat template. If the tokenizer
    has no template, loading fails rather than falling back to an invented
    ``User:``/``Assistant:`` format: such a format both misrepresents the
    model's training distribution and invites role-leakage violations. Set
    ``chat_template`` explicitly, or ``use_chat_template: false`` to accept a
    plain concatenation.

    Recognised ``extra`` keys (all optional):
        device_map, torch_dtype, trust_remote_code, load_in_4bit, load_in_8bit,
        revision, attn_implementation, tokenizer_model, chat_template,
        use_chat_template, top_p, top_k, repetition_penalty, do_sample, seed.
    """

    # Defaults chosen to work on a single GPU or CPU without extra configuration.
    _DEFAULT_LOAD = {"device_map": "auto", "torch_dtype": "auto",
                     "trust_remote_code": False}
    _GEN_KEYS = ("top_p", "top_k", "repetition_penalty", "do_sample",
                 "num_beams", "min_new_tokens", "no_repeat_ngram_size")

    def _ensure_client(self) -> None:
        if self._client is not None:
            return

        import torch  # lazy
        from transformers import AutoModelForCausalLM, AutoTokenizer  # lazy

        extra = dict(self.spec.extra)
        tok_id = extra.pop("tokenizer_model", None) or self.spec.model
        revision = extra.get("revision")
        trust = bool(extra.get("trust_remote_code",
                               self._DEFAULT_LOAD["trust_remote_code"]))

        tok_kwargs: dict[str, Any] = {"trust_remote_code": trust}
        if revision:
            tok_kwargs["revision"] = revision
        tokenizer = AutoTokenizer.from_pretrained(tok_id, **tok_kwargs)

        chat_template = extra.get("chat_template")
        if chat_template:
            tokenizer.chat_template = chat_template
        use_template = bool(extra.get("use_chat_template", True))
        if use_template and not getattr(tokenizer, "chat_template", None):
            raise ProviderError(
                f"Tokenizer for '{tok_id}' has no chat template. Set "
                f"'chat_template' in the config, point 'tokenizer_model' at an "
                f"instruction-tuned tokenizer, or set 'use_chat_template': false "
                f"to accept a plain concatenation."
            )

        load_kwargs: dict[str, Any] = {
            "device_map": extra.get("device_map", self._DEFAULT_LOAD["device_map"]),
            "trust_remote_code": trust,
        }
        dtype = extra.get("torch_dtype", self._DEFAULT_LOAD["torch_dtype"])
        if isinstance(dtype, str) and dtype != "auto":
            load_kwargs["torch_dtype"] = getattr(torch, dtype)
        else:
            load_kwargs["torch_dtype"] = dtype
        for key in ("revision", "attn_implementation", "load_in_4bit", "load_in_8bit"):
            if key in extra:
                load_kwargs[key] = extra[key]

        model = AutoModelForCausalLM.from_pretrained(self.spec.model, **load_kwargs)
        model.eval()

        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            tokenizer.pad_token = tokenizer.eos_token

        seed = extra.get("seed")
        if seed is not None:
            torch.manual_seed(int(seed))

        self._client = {"model": model, "tokenizer": tokenizer,
                        "torch": torch, "use_template": use_template}

    def _render(self, system: str, messages: list[dict]) -> str:
        tokenizer = self._client["tokenizer"]
        if self._client["use_template"]:
            chat = [{"role": "system", "content": system}] + messages
            try:
                return tokenizer.apply_chat_template(
                    chat, tokenize=False, add_generation_prompt=True)
            except Exception:
                # Some templates reject a system role; fold it into the first turn.
                merged = list(messages)
                if merged and merged[0]["role"] == "user":
                    merged = [{"role": "user",
                               "content": f"{system}\n\n{merged[0]['content']}"}] \
                             + merged[1:]
                else:
                    merged = [{"role": "user", "content": system}] + merged
                return tokenizer.apply_chat_template(
                    merged, tokenize=False, add_generation_prompt=True)
        parts = [system] + [m["content"] for m in messages]
        return "\n\n".join(parts)

    def _call(self, system: str, messages: list[dict]) -> tuple[str, dict]:
        model = self._client["model"]
        tokenizer = self._client["tokenizer"]
        torch = self._client["torch"]

        prompt = self._render(system, messages)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        prompt_len = inputs["input_ids"].shape[-1]

        gen_kwargs: dict[str, Any] = {
            "max_new_tokens": self.spec.max_tokens,
            "pad_token_id": tokenizer.pad_token_id,
        }
        # transformers has no temperature=0; greedy decoding is do_sample=False.
        if self.spec.temperature is None:
            gen_kwargs["do_sample"] = True
        elif self.spec.temperature <= 0:
            gen_kwargs["do_sample"] = False
        else:
            gen_kwargs["do_sample"] = True
            gen_kwargs["temperature"] = self.spec.temperature
        for key in self._GEN_KEYS:
            if key in self.spec.extra:
                gen_kwargs[key] = self.spec.extra[key]

        with torch.no_grad():
            out = model.generate(**inputs, **gen_kwargs)

        new_tokens = out[0][prompt_len:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
        n_new = int(new_tokens.shape[-1])
        meta = {
            "stop_reason": "max_tokens" if n_new >= self.spec.max_tokens else "stop",
            "block_types": None,
            "dropped_blocks": [],
            "output_tokens": n_new,
        }
        return text, meta


_ADAPTERS = {
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "bedrock": BedrockAdapter,
    "huggingface": HuggingFaceAdapter,
    "hf": HuggingFaceAdapter,
}


def build_adapter(spec: ModelSpec, max_retries: int = 4) -> BaseAdapter:
    """Instantiate the adapter for ``spec.provider``."""
    key = spec.provider.lower()
    if key not in _ADAPTERS:
        raise ValueError(
            f"Unknown provider '{spec.provider}'. "
            f"Supported: {', '.join(sorted(_ADAPTERS))}."
        )
    return _ADAPTERS[key](spec, max_retries=max_retries)
