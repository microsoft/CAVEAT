"""Fail-closed concurrent canary for a four-replica vLLM endpoint."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .common import IntegrityError, canonical_bytes, sha256_bytes, write_json_create_only

CANARY_SCHEMA = "harness-posttrain-eval.dp-canary.v1"
ENGINE_COUNT = 4
_METRIC = re.compile(
    r'^vllm:num_requests_running\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.eE+-]+)$'
)
_ENGINE = re.compile(r'(?:^|,)engine="(?P<engine>[0-9]+)"(?:,|$)')


def running_by_engine(metrics: str) -> dict[int, int]:
    """Parse the current running-request gauge for exactly four engines."""

    running: dict[int, int] = {}
    for line in metrics.splitlines():
        match = _METRIC.fullmatch(line.strip())
        if match is None:
            continue
        engine_match = _ENGINE.search(match.group("labels"))
        if engine_match is None:
            raise IntegrityError("vLLM running-request metric has no engine label")
        engine = int(engine_match.group("engine"))
        if engine in running:
            raise IntegrityError(f"vLLM metrics duplicate engine {engine}")
        value = float(match.group("value"))
        if value < 0 or not value.is_integer():
            raise IntegrityError("vLLM running-request gauge is not a nonnegative integer")
        running[engine] = int(value)
    expected = set(range(ENGINE_COUNT))
    if set(running) != expected:
        raise IntegrityError(
            f"vLLM metrics expose engines {sorted(running)}, expected {sorted(expected)}"
        )
    return running


def _get(url: str, *, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            if response.status != 200:
                raise IntegrityError(f"{url} returned HTTP {response.status}")
            body = response.read(16 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise IntegrityError(f"{url} returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise IntegrityError(f"{url} failed: {type(exc).__name__}: {exc}") from exc
    if len(body) > 16 * 1024 * 1024:
        raise IntegrityError(f"{url} response exceeds 16 MiB")
    return body


def _post(
    *,
    base_url: str,
    model: str,
    api_key: str,
    nonce: int,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    # Long deterministic completions keep a request resident long enough for a
    # simultaneous metrics sample. The content has no benchmark semantics.
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Infrastructure canary {nonce}. Emit the word ping separated by "
                    "spaces until the output limit. Do not stop early."
                ),
            }
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        method="POST",
        data=json.dumps(payload, sort_keys=True).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read(16 * 1024 * 1024 + 1)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raise IntegrityError(f"canary request {nonce} returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise IntegrityError(
            f"canary request {nonce} failed: {type(exc).__name__}: {exc}"
        ) from exc
    if status != 200 or len(body) > 16 * 1024 * 1024:
        raise IntegrityError(f"canary request {nonce} returned an invalid response")
    try:
        response = json.loads(body)
        choice = response["choices"][0]
        finish_reason = choice["finish_reason"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise IntegrityError(f"canary request {nonce} returned malformed JSON") from exc
    if finish_reason not in {"length", "stop"}:
        raise IntegrityError(
            f"canary request {nonce} has unexpected finish_reason={finish_reason!r}"
        )
    return {"request": nonce, "http_status": status, "finish_reason": finish_reason}


def run_dp_canary(
    *,
    base_url: str,
    model: str,
    api_key: str,
    output_path: Path,
    concurrency: int = 16,
    max_tokens: int = 4096,
    request_timeout_seconds: float = 900.0,
    sample_interval_seconds: float = 0.25,
    observation_timeout_seconds: float = 120.0,
    get: Callable[..., bytes] = _get,
    post: Callable[..., dict[str, Any]] = _post,
) -> dict[str, Any]:
    """Prove that one concurrent burst occupies every DP engine.

    The receipt is create-only. A failed canary writes nothing, so an operator
    cannot mistake partial evidence for a passing topology attestation.
    """

    if output_path.exists() or output_path.is_symlink():
        raise IntegrityError(f"refusing to overwrite frozen artifact: {output_path}")
    if concurrency < ENGINE_COUNT:
        raise IntegrityError("DP canary concurrency must be at least four")
    if max_tokens < 256 or request_timeout_seconds <= 0:
        raise IntegrityError("DP canary token and request bounds must be positive")
    if sample_interval_seconds <= 0 or observation_timeout_seconds <= 0:
        raise IntegrityError("DP canary observation bounds must be positive")

    import time

    samples: list[dict[str, Any]] = []
    observed_all = False
    futures = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for nonce in range(concurrency):
            futures.append(
                pool.submit(
                    post,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    nonce=nonce,
                    max_tokens=max_tokens,
                    timeout_seconds=request_timeout_seconds,
                )
            )
        deadline = time.monotonic() + observation_timeout_seconds
        while time.monotonic() < deadline:
            body = get(
                base_url.rstrip("/").removesuffix("/v1") + "/metrics",
                timeout_seconds=min(10.0, observation_timeout_seconds),
            )
            try:
                counts = running_by_engine(body.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise IntegrityError("vLLM metrics are not UTF-8") from exc
            samples.append({str(engine): counts[engine] for engine in range(ENGINE_COUNT)})
            if all(counts[engine] > 0 for engine in range(ENGINE_COUNT)):
                observed_all = True
                break
            time.sleep(sample_interval_seconds)
        responses = [future.result() for future in as_completed(futures)]

    if not observed_all:
        raise IntegrityError(
            "concurrent canary never observed a running request on every DP engine"
        )
    receipt_core = {
        "schema": CANARY_SCHEMA,
        "status": "ok",
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "base_url": base_url.rstrip("/"),
        "model": model,
        "engine_count": ENGINE_COUNT,
        "concurrency": concurrency,
        "max_tokens": max_tokens,
        "samples": samples,
        "responses": sorted(responses, key=lambda row: row["request"]),
        "all_engines_observed_running": True,
    }
    receipt = {
        **receipt_core,
        "receipt_sha256": sha256_bytes(canonical_bytes(receipt_core)),
    }
    write_json_create_only(output_path, receipt)
    return receipt
