"""Model layer: one ``ModelSpec`` that any scaffold can consume two ways.

Why two faces? External scaffolds (browser-use, Stagehand, WebVoyager) drive their
*own* LLM client and just want an OpenAI-compatible ``(base_url, api_key, model)``
triple. Scaffolds you write yourself want to call a model directly — for those the
spec exposes an async ``chat()`` that routes through the unified ``llm_client``
(multi-region TRAPI + PhyAGI, with caching/failover/accounting).

Providers
---------
* ``trapi``  (default) — logical name (``gpt-5.5``, ``Kimi-K2.6``, ...) resolved to a
  TRAPI deployment; auth via ``az login``. The OpenAI face mints a fresh bearer token.
* ``openai`` — bring your own endpoint: give ``base_url`` + ``api_key`` (+ ``deployment``).
  Works for real OpenAI, vLLM, Ollama's OpenAI shim, a gateway, anything compatible.

Specify models as a bare string (``"gpt-5.5"`` → trapi) or a dict::

    {name: my-vllm, provider: openai, base_url: http://localhost:8000/v1,
     api_key: env:VLLM_KEY, deployment: Qwen2.5-7B, vision: false}
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from ..llm_client import TRAPI_DEPLOY, LLMClient, _logical

# Best-effort vision capability for known logical models (override with vision=).
_KNOWN_NO_VISION = {"llama-3.3-70b-instruct", "deepseek-r1", "deepseek-v3.2",
                    "mistral-large-3", "gpt-oss-120b", "gcr-fara-7b"}


def _is_reasoning(name: str) -> bool:
    m = _logical(name).lower()
    if "chat" in m:
        return False
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _resolve_key(api_key: Optional[str]) -> Optional[str]:
    """Allow ``env:VARNAME`` indirection so secrets stay out of config files."""
    if api_key and api_key.startswith("env:"):
        return os.environ.get(api_key[4:], "")
    return api_key


@lru_cache(maxsize=1)
def _trapi_token_provider():
    from azure.identity import AzureCliCredential, get_bearer_token_provider
    scope = os.environ.get("TRAPI_SCOPE", "api://trapi/.default")
    return get_bearer_token_provider(AzureCliCredential(), scope)


@lru_cache(maxsize=1)
def _shared_llm_client() -> LLMClient:
    # One router per process: shared routing health, cooldowns and accounting.
    return LLMClient(cache_enabled=os.environ.get("AGENTARENA_LLM_CACHE", "1") == "1")


@dataclass
class OpenAIEndpoint:
    """An OpenAI-compatible endpoint an external scaffold can be pointed at."""

    base_url: str
    api_key: str
    model: str
    vision: bool = True
    reasoning: bool = False


@dataclass
class ModelSpec:
    name: str                                # logical/display name
    provider: str = "trapi"                  # "trapi" | "openai"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    deployment: Optional[str] = None         # model id sent on the wire
    vision: Optional[bool] = None            # None → infer
    region: str = "gcr/shared"               # trapi region for the OpenAI face
    extra: dict[str, Any] = field(default_factory=dict)

    # -- capabilities ------------------------------------------------------- #
    @property
    def reasoning(self) -> bool:
        return _is_reasoning(self.deployment or self.name)

    @property
    def has_vision(self) -> bool:
        if self.vision is not None:
            return self.vision
        return _logical(self.name).lower() not in _KNOWN_NO_VISION

    def wire_model(self) -> str:
        """The model id actually sent on the wire."""
        if self.deployment:
            return self.deployment
        if self.provider == "trapi":
            return TRAPI_DEPLOY.get(self.name, self.name)
        return self.name

    # -- face 1: OpenAI-compatible endpoint (for external scaffolds) --------- #
    def openai_endpoint(self) -> OpenAIEndpoint:
        if self.provider == "openai":
            base = self.base_url or os.environ.get("OPENAI_BASE_URL")
            key = _resolve_key(self.api_key) or os.environ.get("OPENAI_API_KEY") or ""
            if not base:
                raise ValueError(f"model {self.name!r}: provider 'openai' needs base_url")
            return OpenAIEndpoint(base, key, self.wire_model(), self.has_vision, self.reasoning)
        if self.provider == "trapi":
            base = self.base_url or f"https://trapi.research.microsoft.com/{self.region}/openai/v1/"
            token = _trapi_token_provider()()        # fresh bearer token (valid ~1h)
            return OpenAIEndpoint(base, token, self.wire_model(), self.has_vision, self.reasoning)
        raise ValueError(f"unknown provider {self.provider!r}")

    # -- face 2: native async chat (for scaffolds you write) ---------------- #
    async def achat(self, messages, *, system: str | None = None,
                    max_tokens: int = 4096, **kw) -> str:
        if self.provider == "trapi":
            res = await _shared_llm_client().chat(
                messages, model=self.wire_model(), system=system,
                max_completion_tokens=max_tokens, **kw)
            return res.content
        # BYO: a plain AsyncOpenAI call
        from openai import AsyncOpenAI
        ep = self.openai_endpoint()
        client = _byo_client(ep.base_url, ep.api_key)
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        if system:
            messages = [{"role": "system", "content": system}, *messages]
        params = {"max_completion_tokens": max_tokens}
        params.update({k: v for k, v in kw.items() if v is not None})
        r = await client.chat.completions.create(model=ep.model, messages=messages, **params)
        return r.choices[0].message.content or ""

    # -- construction ------------------------------------------------------- #
    @classmethod
    def parse(cls, spec: "str | dict | ModelSpec") -> "ModelSpec":
        if isinstance(spec, ModelSpec):
            return spec
        if isinstance(spec, str):
            return cls(name=spec)
        d = dict(spec)
        return cls(name=d["name"], provider=d.get("provider", "trapi"),
                   base_url=d.get("base_url"), api_key=d.get("api_key"),
                   deployment=d.get("deployment"), vision=d.get("vision"),
                   region=d.get("region", "gcr/shared"), extra=d.get("extra", {}))

    @property
    def label(self) -> str:
        return self.name


@lru_cache(maxsize=8)
def _byo_client(base_url: str, api_key: str):
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=4)
