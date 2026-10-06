# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""OpenAI-compatible model configuration shared by every CAVEAT harness.

Models may be supplied as a name or as a mapping.  Endpoint credentials support
``env:VARIABLE`` indirection so secrets do not need to appear in run configs::

    {name: local-model, deployment: org/model, base_url: http://localhost:8000/v1,
     api_key: env:MODEL_API_KEY, vision: true}

The API is intentionally provider-neutral: OpenAI, vLLM, SGLang, and gateways
work through the same chat-completions interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


def _model_family(name: str) -> str:
    """Normalize display-only effort suffixes for capability inference."""
    value = name.lower()
    for suffix in ("-minimal", "-low", "-medium", "-high"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _is_reasoning(name: str) -> bool:
    value = _model_family(name)
    return "chat" not in value and value.startswith(("gpt-5", "o1", "o3", "o4"))


def _resolve_key(api_key: str | None) -> str | None:
    """Resolve ``env:VARIABLE`` references and fail clearly when they are absent."""
    if api_key and api_key.startswith("env:"):
        variable = api_key[4:]
        value = os.environ.get(variable)
        if not value:
            raise ValueError(
                f"model API key environment variable {variable!r} is not set"
            )
        return value
    return api_key


@dataclass(frozen=True)
class OpenAIEndpoint:
    """The endpoint contract consumed by bundled and third-party harnesses."""

    base_url: str
    api_key: str
    model: str
    vision: bool = True
    reasoning: bool = False
    reasoning_effort: str | None = None


@dataclass
class ModelSpec:
    """A model served through an OpenAI-compatible chat-completions endpoint."""

    name: str
    provider: str = "openai"
    base_url: str | None = None
    api_key: str | None = None
    deployment: str | None = None
    vision: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def reasoning(self) -> bool:
        explicit = self.extra.get("reasoning")
        return (
            bool(explicit)
            if explicit is not None
            else _is_reasoning(self.deployment or self.name)
        )

    @property
    def reasoning_effort(self) -> str | None:
        return self.extra.get("reasoning_effort")

    @property
    def has_vision(self) -> bool:
        return True if self.vision is None else self.vision

    def wire_model(self) -> str:
        return self.deployment or self.name

    def openai_endpoint(self) -> OpenAIEndpoint:
        if self.provider != "openai":
            raise ValueError(
                f"model {self.name!r}: unsupported provider {self.provider!r}; "
                "CAVEAT accepts OpenAI-compatible endpoints"
            )
        base_url = (
            self.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = _resolve_key(self.api_key) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                f"model {self.name!r}: set api_key (preferably env:VARIABLE) or OPENAI_API_KEY"
            )
        return OpenAIEndpoint(
            base_url.rstrip("/"),
            api_key,
            self.wire_model(),
            self.has_vision,
            self.reasoning,
            self.reasoning_effort,
        )

    async def achat(
        self, messages, *, system: str | None = None, max_tokens: int = 4096, **kwargs
    ) -> str:
        """Call the configured endpoint directly for a custom harness."""
        endpoint = self.openai_endpoint()
        client = _openai_client(endpoint.base_url, endpoint.api_key)
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        if system:
            messages = [{"role": "system", "content": system}, *messages]
        params = {"max_completion_tokens": max_tokens}
        params.update(
            {key: value for key, value in kwargs.items() if value is not None}
        )
        response = await client.chat.completions.create(
            model=endpoint.model, messages=messages, **params
        )
        return response.choices[0].message.content or ""

    @classmethod
    def parse(cls, spec: str | dict | ModelSpec) -> ModelSpec:
        if isinstance(spec, ModelSpec):
            return spec
        if isinstance(spec, str):
            if "#" in spec:
                model, effort = spec.split("#", 1)
                if not model or not effort:
                    raise ValueError(f"invalid model effort syntax: {spec!r}")
                return cls(
                    name=f"{model}-{effort}",
                    deployment=model,
                    extra={"reasoning_effort": effort},
                )
            return cls(name=spec)
        data = dict(spec)
        if not data.get("name"):
            raise ValueError("model mapping needs a non-empty 'name'")
        return cls(
            name=data["name"],
            provider=data.get("provider", "openai"),
            base_url=data.get("base_url"),
            api_key=data.get("api_key"),
            deployment=data.get("deployment"),
            vision=data.get("vision"),
            extra=dict(data.get("extra") or {}),
        )

    @property
    def label(self) -> str:
        return self.name


@lru_cache(maxsize=16)
def _openai_client(base_url: str, api_key: str):
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=4)
