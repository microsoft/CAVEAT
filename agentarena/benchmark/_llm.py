"""Thin helpers over ``LLMClient`` for the generation pipeline: a shared client, robust
JSON extraction, and small async text/JSON call wrappers. All calls default to gpt-5.5
and are cached by content+seed inside ``LLMClient``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..llm_client import LLMClient

_client: Optional[LLMClient] = None


def client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def chat_text(prompt: str, *, system: Optional[str] = None, model: str = "gpt-5.5",
                    seed: int = 0, max_tokens: int = 1400) -> str:
    res = await client().chat(prompt, model=model, system=system,
                              max_completion_tokens=max_tokens, seed=seed)
    return res.content or ""


def extract_json(text: str) -> Any:
    """Parse a JSON object/array from a model reply (tolerates code fences + prose)."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # fall back to the outermost {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    raise ValueError(f"no JSON found in reply: {text[:200]!r}")


async def chat_json(prompt: str, *, system: Optional[str] = None, model: str = "gpt-5.5",
                    seed: int = 0, max_tokens: int = 1400, retries: int = 2) -> Any:
    last = None
    for attempt in range(retries + 1):
        suffix = "" if attempt == 0 else "\n\nReturn ONLY a single valid JSON value, no prose."
        txt = await chat_text(prompt + suffix, system=system, model=model,
                              seed=seed + attempt, max_tokens=max_tokens)
        try:
            return extract_json(txt)
        except Exception as e:  # noqa: BLE001
            last = e
    raise ValueError(f"chat_json failed after {retries + 1} tries: {last}")
