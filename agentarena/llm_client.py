"""Unified async LLM client for TRAPI + PhyAGI.

One file, three layers (collapsed from trapi_client.py / router.py / llm.py):
  * create_client / create_phyagi_client : low-level AsyncOpenAI factories.
  * Router                               : multi-endpoint load-balance + failover.
  * LLMClient                            : caching, bounded concurrency, backoff,
                                           token/latency accounting. <-- use this.

Quick start
-----------
    import asyncio
    from llm_client import LLMClient

    async def main():
        llm = LLMClient()                          # default model gpt-5.5
        res = await llm.chat("Say hi in one word.")
        print(res.content, res.total_tokens, res.latency_s)

        vecs = await llm.embed(["hello", "world"])
        print(len(vecs), len(vecs[0]))

    asyncio.run(main())

Auth
----
TRAPI  : run `az login` first (AzureCliCredential, scope api://trapi/.default).
PhyAGI : optional. Put the key in a .env file next to this script (or in the cwd /
         a parent dir) as either `PHYAGI_API_KEY=...` or `phyagi_apikey=...`, or
         export PHYAGI_API_KEY. If no key is found, only TRAPI endpoints are used.

Routing
-------
A logical model name (e.g. "gpt-5.5") maps to every endpoint that serves it; the
router picks the least-in-flight healthy endpoint, cools down throttled ones (429),
and fails over on error. Chat runs on TRAPI {gcr, msraif, redmond} + PhyAGI;
embeddings are TRAPI-only and pinned to the region that actually serves them.

PhyAGI session pinning (responses API) is not part of the routed chat path; for that
use `create_phyagi_client(session_id=...)` directly (see bottom of this module).

Smoke test
----------
    python llm_client.py        # lists TRAPI deployments + a routed chat + an embed
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import AsyncOpenAI

__all__ = ["LLMClient", "ChatResult", "Accounting", "Router",
           "create_client", "create_phyagi_client"]

ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = Path(os.environ.get("LLM_CACHE_DIR") or (ROOT / "runs" / "llm_cache"))


# --------------------------------------------------------------------------- #
# .env / credentials
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE lines from a .env file into os.environ (no overwrite)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _find_dotenv() -> Path | None:
    seen: set[Path] = set()
    for d in (Path.cwd(), ROOT, *ROOT.parents):
        c = d / ".env"
        if c in seen:
            continue
        seen.add(c)
        if c.exists():
            return c
    return None


def _phyagi_key() -> str | None:
    dotenv = _find_dotenv()
    if dotenv:
        _load_dotenv(dotenv)
    for name in ("PHYAGI_API_KEY", "phyagi_apikey", "phyagi_api_key"):
        if os.environ.get(name):
            return os.environ[name]
    return None


# --------------------------------------------------------------------------- #
# Model registry  (logical name -> TRAPI deployment; updated from models.list())
# --------------------------------------------------------------------------- #
TRAPI_REGIONS = ["gcr/shared", "msraif/shared", "redmond/interactive"]

TRAPI_DEPLOY: dict[str, str] = {
    # --- GPT-4.x ---
    "gpt-4.1": "gpt-4.1_2025-04-14",
    "gpt-4.1-mini": "gpt-4.1-mini_2025-04-14",
    "gpt-4.1-nano": "gpt-4.1-nano_2025-04-14",
    "gpt-4o": "gpt-4o_2024-11-20",
    "gpt-4o-mini": "gpt-4o-mini_2024-07-18",
    "gpt-4-32k": "gpt-4-32k_0314",
    # --- GPT-5.x (reasoning) ---
    "gpt-5": "gpt-5_2025-08-07",
    "gpt-5-mini": "gpt-5-mini_2025-08-07",
    "gpt-5-nano": "gpt-5-nano_2025-08-07",
    "gpt-5.1": "gpt-5.1_2025-11-13",
    "gpt-5.2": "gpt-5.2_2025-12-11",
    "gpt-5.4-mini": "gpt-5.4-mini_2026-03-17",
    "gpt-5.4-nano": "gpt-5.4-nano_2026-03-17",
    "gpt-5.5": "gpt-5.5_2026-04-24",
    # --- chat (non-reasoning) variants ---
    "gpt-5-chat": "gpt-5-chat_2025-10-03",   # newer of the two deployments listed
    "gpt-5.1-chat": "gpt-5.1-chat_2025-11-13",
    "gpt-5.2-chat": "gpt-5.2-chat_2025-12-11",
    "gpt-5.3-chat": "gpt-5.3-chat_2026-03-03",
    "gpt-chat-latest": "gpt-chat-latest_2026-05-28",
    # --- codex ---
    "gpt-5-codex": "gpt-5-codex_2025-09-15",
    "gpt-5.1-codex": "gpt-5.1-codex_2025-11-13",
    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini_2025-11-13",
    "gpt-5.1-codex-max": "gpt-5.1-codex-max_2025-12-04",
    "gpt-5.2-codex": "gpt-5.2-codex_2026-01-14",
    "gpt-5.3-codex": "gpt-5.3-codex_2026-02-24",
    "codex-mini": "codex-mini_2025-05-16",
    # --- o-series ---
    "o1": "o1_2024-12-17",
    "o3": "o3_2025-04-16",
    "o3-mini": "o3-mini_2025-01-31",
    "o4-mini": "o4-mini_2025-04-16",
    # --- model router ---
    "model-router": "model-router_2025-11-18",   # newest of three deployments
    # --- third-party served via TRAPI (version-suffixed) ---
    "grok-4": "grok-4_1",
    "grok-4-1-fast-reasoning": "grok-4-1-fast-reasoning_1",
    "grok-4-1-fast-non-reasoning": "grok-4-1-fast-non-reasoning_1",
    "grok-4-20-reasoning": "grok-4-20-reasoning_1",
    "grok-4-20-non-reasoning": "grok-4-20-non-reasoning_1",
    "gpt-oss-120b": "gpt-oss-120b_1",
    "Llama-3.3-70B-Instruct": "Llama-3.3-70B-Instruct_5",
    "Mistral-Large-3": "Mistral-Large-3_1",
    "Kimi-K2.5": "Kimi-K2.5_1",
    "Kimi-K2.6": "Kimi-K2.6_2026-04-20",
    "DeepSeek-R1": "DeepSeek-R1_1",
    "DeepSeek-V3.2": "DeepSeek-V3.2_1",
    # --- OSS / hosted, deployment name == logical name (identity passthrough) ---
    "gcr-fara-7b": "gcr-fara-7b",
    "gcr-llama-31-8b-instruct": "gcr-llama-31-8b-instruct",
    "gcr-phi-4-shared": "gcr-phi-4-shared",
    "gcr-phi-4-reasoning": "gcr-phi-4-reasoning",
    "gcr-phi-4-mini-reasoning": "gcr-phi-4-mini-reasoning",
    "gcr-phi-reasoning-plus": "gcr-phi-reasoning-plus",
    "unsloth/gemma-3-27b-it": "unsloth/gemma-3-27b-it",
    "unsloth/gemma-3-4b-it": "unsloth/gemma-3-4b-it",
    "Qwen/Qwen2.5-VL-7B-Instruct": "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen3-VL-4B-Instruct": "Qwen/Qwen3-VL-4B-Instruct",
    "Qwen/Qwen3.5-9B": "Qwen/Qwen3.5-9B",
    "Qwen/Qwen3.5-27B": "Qwen/Qwen3.5-27B",
    "Qwen/Qwen3.5-122B-A10B": "Qwen/Qwen3.5-122B-A10B",
    "Qwen/Qwen3.5-397B-A17B-GPTQ-Int4": "Qwen/Qwen3.5-397B-A17B-GPTQ-Int4",
    # (audio / image / realtime / transcribe / video deployments omitted: not chat-completable)
}

# Logical models PhyAGI also serves (bare names). Update if your gateway changes;
# the TRAPI list above comes from models.list() and does NOT describe PhyAGI.
PHYAGI_MODELS = {"gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-5.1", "gpt-5.2",
                 "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o4-mini"}

# Embeddings are TRAPI-only and NOT mirrored across regions; pin each to a region
# that actually serves it (unknown embed models fall back to gcr/shared in pick()).
EMBED_REGIONS: dict[str, list[str]] = {
    "text-embedding-3-small_1": ["gcr/shared"],
    "text-embedding-3-large_1": ["gcr/shared"],
    "text-embedding-ada-002_2": ["gcr/shared", "redmond/interactive"],
}


def _logical(model: str) -> str:
    """Strip a TRAPI date suffix (_2025-04-14...) and/or a bare version suffix
    (_1, _5, _0314) so cache keys + routing are endpoint/region-agnostic.
    e.g. 'gpt-5.5_2026-04-24' -> 'gpt-5.5', 'grok-4_1' -> 'grok-4'."""
    m = re.sub(r"_\d{4}-\d{2}-\d{2}.*$", "", model)   # ISO date suffix
    m = re.sub(r"_\d+$", "", m)                        # bare version suffix
    return m


def _supports_effort(model: str) -> bool:
    """Reasoning models accept `reasoning_effort`; chat variants and gpt-4.x do not.
    Anything not matched here that DOES accept it still works once it's passed via
    `extra`; anything matched that rejects it is auto-stripped on BadRequest."""
    m = _logical(model).lower()
    if "chat" in m:
        return False
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _trapi_base_url(region: str) -> str:
    return f"https://trapi.research.microsoft.com/{region}/openai/v1/"


# --------------------------------------------------------------------------- #
# Low-level client factories
# --------------------------------------------------------------------------- #
def create_client(
    model: str | None = None,
    base_url: str | None = None,
) -> tuple[AsyncOpenAI, str]:
    """Return (AsyncOpenAI, model) talking to TRAPI via the az-cli credential.

    api_key is a *callable* (not a fetched string) so tokens are minted fresh per
    request and never go stale; AsyncOpenAI awaits it, hence the async wrapper.
    """
    model = model or os.environ.get("TRAPI_MODEL", "gpt-5.5_2026-04-24")
    if base_url is None:
        if os.environ.get("TRAPI_ENDPOINT"):
            base_url = os.environ["TRAPI_ENDPOINT"].rstrip("/") + "/openai/v1/"
        else:
            base_url = _trapi_base_url(os.environ.get("TRAPI_APIPATH", "gcr/shared"))
    scope = os.environ.get("TRAPI_SCOPE", "api://trapi/.default")

    token_provider = get_bearer_token_provider(AzureCliCredential(), scope)

    async def api_key() -> str:
        return token_provider()

    client = AsyncOpenAI(max_retries=5, base_url=base_url, api_key=api_key)  # type: ignore[arg-type]
    return client, model


def create_phyagi_client(
    session_id: str,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    strict_session: bool = False,
) -> tuple[AsyncOpenAI, str]:
    """Return (AsyncOpenAI, model) for the PhyAGI gateway with a static key.

    session_id (and strict_session) are injected into every responses.create call.
    Use this directly when you need PhyAGI session pinning via the responses API;
    the routed LLMClient.chat path uses chat.completions and does not inject these.
    """
    model = model or os.environ.get("PHYAGI_MODEL", "gpt-5.2")
    base_url = base_url or os.environ.get("PHYAGI_GATEWAY_URL", "http://gateway.phyagi.net/api")
    api_key = api_key or _phyagi_key()
    if not api_key:
        raise ValueError("PhyAGI requires an API key (api_key arg, PHYAGI_API_KEY, or .env).")

    client = AsyncOpenAI(max_retries=5, base_url=base_url, api_key=api_key)
    client.responses.create = partial(  # type: ignore[assignment]
        client.responses.create,
        extra_body={"session_id": session_id, "strict_session": bool(strict_session)},
    )
    return client, model


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
@dataclass
class Endpoint:
    eid: str
    kind: str                 # "trapi" | "phyagi"
    base_url: str | None
    region: str | None
    in_flight: int = 0
    cooldown_until: float = 0.0
    calls: int = 0
    errors: int = 0
    throttles: int = 0
    client: Any = None        # lazy AsyncOpenAI

    def healthy(self) -> bool:
        return time.time() >= self.cooldown_until

    def deploy_for(self, logical: str) -> str:
        # TRAPI uses date/version-suffixed deployments; PhyAGI uses bare names.
        return TRAPI_DEPLOY.get(logical, logical) if self.kind == "trapi" else logical


class Router:
    """Maps a logical model to all endpoints that serve it and balances across them."""

    def __init__(self) -> None:
        self.phyagi_key = _phyagi_key()
        self.endpoints: dict[str, Endpoint] = {}
        for r in TRAPI_REGIONS:
            self.endpoints[f"trapi:{r}"] = Endpoint(
                f"trapi:{r}", "trapi", _trapi_base_url(r), r)
        if self.phyagi_key:
            self.endpoints["phyagi"] = Endpoint(
                "phyagi", "phyagi",
                os.environ.get("PHYAGI_GATEWAY_URL", "http://gateway.phyagi.net/api"), None)

    def _client(self, ep: Endpoint) -> AsyncOpenAI:
        if ep.client is None:
            if ep.kind == "trapi":
                ep.client, _ = create_client(model="x", base_url=ep.base_url)
            else:
                ep.client = AsyncOpenAI(max_retries=2, base_url=ep.base_url, api_key=self.phyagi_key)
        return ep.client

    def endpoints_for(self, logical: str, embed: bool = False) -> list[Endpoint]:
        trapi = [self.endpoints[f"trapi:{r}"] for r in TRAPI_REGIONS if f"trapi:{r}" in self.endpoints]
        if embed:
            regions = EMBED_REGIONS.get(logical, ["gcr/shared"])
            eps = [self.endpoints[f"trapi:{r}"] for r in regions if f"trapi:{r}" in self.endpoints]
            return eps or trapi or list(self.endpoints.values())
        eps: list[Endpoint] = []
        if logical in TRAPI_DEPLOY:
            eps += trapi
        if logical in PHYAGI_MODELS and "phyagi" in self.endpoints:
            eps.append(self.endpoints["phyagi"])
        return eps or list(self.endpoints.values())

    def pick(self, logical: str, embed: bool = False) -> Endpoint:
        eps = self.endpoints_for(logical, embed=embed)
        healthy = [e for e in eps if e.healthy()]
        if not healthy:
            return min(eps, key=lambda e: e.cooldown_until)        # soonest-free
        return min(healthy, key=lambda e: (e.in_flight, e.calls))  # least loaded

    def mark_throttle(self, ep: Endpoint, base: float = 4.0) -> None:
        ep.throttles += 1
        ep.cooldown_until = time.time() + min(base * (1.6 ** min(ep.throttles, 6)), 60.0)

    def stats(self) -> dict[str, Any]:
        return {e.eid: {"calls": e.calls, "errors": e.errors, "throttles": e.throttles}
                for e in self.endpoints.values()}


# --------------------------------------------------------------------------- #
# Accounting / result
# --------------------------------------------------------------------------- #
@dataclass
class Accounting:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    embed_calls: int = 0
    embed_tokens: int = 0
    latency_s: float = 0.0    # sum of network latencies (excludes cache hits)
    errors: int = 0
    throttles: int = 0
    by_endpoint: dict = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int
    latency_s: float
    cached: bool
    finish_reason: str | None = None


# --------------------------------------------------------------------------- #
# LLMClient  (construct once per process, pass around)
# --------------------------------------------------------------------------- #
class LLMClient:
    def __init__(
        self,
        default_model: str = "gpt-5.5_2026-04-24",
        embed_model: str = "text-embedding-3-small_1",
        concurrency: int = 8,
        cache_enabled: bool = True,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        max_backoff_retries: int = 6,
    ) -> None:
        self.default_model = default_model
        self.embed_model = embed_model
        self.sem = asyncio.Semaphore(concurrency)
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(cache_dir)
        if cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_backoff_retries = max_backoff_retries
        self.acct = Accounting()
        self.router = Router()

    # -- cache -------------------------------------------------------------- #
    def _cache_key(self, payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_read(self, key: str) -> dict[str, Any] | None:
        if not self.cache_enabled:
            return None
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None

    def _cache_write(self, key: str, value: dict[str, Any]) -> None:
        if not self.cache_enabled:
            return
        try:
            (self.cache_dir / f"{key}.json").write_text(json.dumps(value, ensure_ascii=False))
        except Exception:
            pass

    # -- chat --------------------------------------------------------------- #
    async def chat(
        self,
        messages: str | list[dict[str, str]],
        model: str | None = None,
        system: str | None = None,
        max_completion_tokens: int = 4096,
        seed: int | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        # ergonomic input: accept a bare string and/or a system prompt
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        if system is not None:
            messages = [{"role": "system", "content": system}, *messages]

        model = model or self.default_model
        params: dict[str, Any] = {"max_completion_tokens": max_completion_tokens}
        if seed is not None:
            params["seed"] = seed
        if reasoning_effort is not None and _supports_effort(model):
            params["reasoning_effort"] = reasoning_effort
        if extra:
            params.update(extra)

        logical = _logical(model)
        key = self._cache_key({"model": logical, "messages": messages, "params": params})
        hit = self._cache_read(key)
        if hit is not None:
            self.acct.calls += 1
            self.acct.cache_hits += 1
            for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
                setattr(self.acct, k, getattr(self.acct, k) + hit.get(k, 0))
            return ChatResult(cached=True, **{k: hit[k] for k in (
                "content", "model", "prompt_tokens", "completion_tokens",
                "reasoning_tokens", "total_tokens", "latency_s", "finish_reason")})

        # route across endpoints with failover + throttle cooldown
        attempts = max(self.max_backoff_retries, len(self.router.endpoints_for(logical)) + 3)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            ep = self.router.pick(logical)
            deploy = ep.deploy_for(logical)
            client = self.router._client(ep)
            try:
                ep.in_flight += 1
                async with self.sem:
                    t0 = time.perf_counter()
                    r = await client.chat.completions.create(model=deploy, messages=messages, **params)
                    dt = time.perf_counter() - t0
                ep.in_flight = max(0, ep.in_flight - 1)
                ep.calls += 1

                choice = r.choices[0]
                content = choice.message.content or ""
                usage = r.usage
                ctd = getattr(usage, "completion_tokens_details", None)
                rt = (getattr(ctd, "reasoning_tokens", 0) if ctd else 0) or 0
                res = ChatResult(
                    content=content, model=logical,
                    prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                    reasoning_tokens=rt, total_tokens=usage.total_tokens,
                    latency_s=dt, cached=False, finish_reason=choice.finish_reason)

                self.acct.calls += 1
                self.acct.prompt_tokens += res.prompt_tokens
                self.acct.completion_tokens += res.completion_tokens
                self.acct.reasoning_tokens += res.reasoning_tokens
                self.acct.total_tokens += res.total_tokens
                self.acct.latency_s += dt
                self.acct.by_endpoint[ep.eid] = self.acct.by_endpoint.get(ep.eid, 0) + 1

                self._cache_write(key, {
                    "content": content, "model": logical,
                    "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                    "reasoning_tokens": res.reasoning_tokens, "total_tokens": res.total_tokens,
                    "latency_s": dt, "finish_reason": res.finish_reason})
                return res

            except Exception as e:  # noqa: BLE001
                ep.in_flight = max(0, ep.in_flight - 1)
                ep.errors += 1
                last_exc = e
                name, msg = type(e).__name__, str(e)
                if "RateLimit" in name or "429" in msg or "TooManyRequests" in name:
                    self.router.mark_throttle(ep)
                    self.acct.throttles += 1
                    continue                                       # failover
                if "BadRequest" in name and "reasoning_effort" in params:
                    params.pop("reasoning_effort", None)            # endpoint rejects it
                    continue
                if any(s in name for s in ("APIConnection", "APITimeout", "InternalServer",
                                            "APIError", "Timeout")):
                    await asyncio.sleep(min(0.5 * (attempt + 1) + random.uniform(0, 0.5), 8.0))
                    continue
                if "BadRequest" in name or "NotFound" in name or "Permission" in name:
                    self.acct.errors += 1
                    raise
                await asyncio.sleep(min(0.5 * (attempt + 1), 8.0))

        self.acct.errors += 1
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("router exhausted endpoints")

    # -- embeddings --------------------------------------------------------- #
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        model = model or self.embed_model
        key = self._cache_key({"embed_model": model, "texts": texts})
        hit = self._cache_read(key)
        if hit is not None:
            self.acct.embed_calls += 1
            return hit["embeddings"]

        last_exc: Exception | None = None
        retries = max(self.max_backoff_retries, 5)
        for attempt in range(retries):
            ep = self.router.pick(model, embed=True)
            client = self.router._client(ep)
            try:
                ep.in_flight += 1
                async with self.sem:
                    r = await client.embeddings.create(model=model, input=texts)
                ep.in_flight = max(0, ep.in_flight - 1)
                ep.calls += 1
                embs = [d.embedding for d in r.data]
                self.acct.embed_calls += 1
                self.acct.embed_tokens += getattr(r.usage, "total_tokens", 0)
                self._cache_write(key, {"embeddings": embs})
                return embs
            except Exception as e:  # noqa: BLE001
                ep.in_flight = max(0, ep.in_flight - 1)
                ep.errors += 1
                last_exc = e
                name = type(e).__name__
                if "RateLimit" in name or "429" in str(e):
                    self.router.mark_throttle(ep)
                    self.acct.throttles += 1
                    continue
                if "BadRequest" in name or attempt >= retries - 1:
                    raise
                await asyncio.sleep(min(0.5 * (attempt + 1), 8.0))
        if last_exc is not None:
            raise last_exc
        return []


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #
async def _smoke_test() -> int:
    llm = LLMClient(cache_enabled=False)
    print(f"default model : {llm.default_model}")
    print(f"phyagi        : {'configured' if llm.router.phyagi_key else 'not configured (TRAPI only)'}")

    print("\n→ TRAPI deployments (gcr/shared):")
    try:
        client, _ = create_client()
        page = await client.models.list()
        for n in sorted(m.id for m in page.data):
            print(f"     • {n}")
    except Exception as e:
        print(f"  (could not list models: {type(e).__name__}: {e})")

    print(f"\n→ routed chat on {llm.default_model} ...")
    try:
        res = await llm.chat("Reply with the single word: ok", max_completion_tokens=2000)
        print(f"✓ {res.content!r}  ({res.total_tokens} tok, {res.latency_s:.2f}s, model={res.model})")
    except Exception as e:
        print(f"⚠ chat failed: {type(e).__name__}: {e}")
        return 1

    print("\n→ embedding ...")
    try:
        vecs = await llm.embed(["hello world"])
        print(f"✓ dim={len(vecs[0])}")
    except Exception as e:
        print(f"⚠ embed failed: {type(e).__name__}: {e}")

    print("\n== accounting ==")
    print(json.dumps(llm.acct.snapshot(), indent=2))
    print("\n== router stats ==")
    print(json.dumps(llm.router.stats(), indent=2))
    print("\n✅ done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_smoke_test()))
