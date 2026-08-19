"""Four-task non-Amazon semantic gate for CAVEAT-27B exact-LoRA evaluation routes."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import IntegrityError

SCHEMA = "caveat-27b-eval.procedural-route-gate.v1"
SEALED_TASKS_SHA256 = "dc62538d655689ab49acc32760d9420bb31b168874ac34bdc054dcd283ccae77"
SEALED_TASK_IDS = (
    "task_2451413f7409dc3f7513",  # simple objective
    "task_77f60cc3612f61396753",  # four coequal objectives + directive
    "task_f51d40486ca687484e67",  # ordinal priorities
    "task_070850362cb8906cf6eb",  # numeric weights
)
_AMAZON = {"laptop", "office_chair", "mattress", "backpack", "tent"}
_RETRYABLE = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ContractStack:
    draft: type[Any]
    request_body: Callable[..., dict[str, Any]]
    decode: Callable[[str], Any]
    score: Callable[[str, str, Mapping[str, Any]], tuple[bool, bool, str | None, str]]
    source: Path
    source_sha256: str


class EndpointError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_contract_stack() -> ContractStack:
    """Import the campaign implementation, never a separately installed copy."""

    source_root = Path(__file__).resolve().parents[3] / "caveat_27b/src"
    expected = (source_root / "caveat_27b/contract_refinement.py").resolve()
    if not expected.is_file():
        raise IntegrityError(f"contract implementation is absent: {expected}")
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    try:
        module = importlib.import_module("caveat_27b.contract_refinement")
    except ImportError as exc:
        raise IntegrityError("load the route gate with the benchmark .venv") from exc
    source = Path(module.__file__).resolve()
    if source != expected:
        raise IntegrityError(
            f"contract implementation resolved to {source}, expected {expected}"
        )
    draft, _extension = module._draft_types()  # noqa: SLF001
    return ContractStack(
        draft=draft,
        request_body=module.contract_request_body,
        decode=module._decode_content,  # noqa: SLF001
        score=module._score_operational,  # noqa: SLF001
        source=source,
        source_sha256=_sha256(source.read_bytes()),
    )


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise IntegrityError("sealed task source must be a regular non-symlink file")
    data = path.read_bytes()
    observed = _sha256(data)
    if observed != SEALED_TASKS_SHA256:
        raise IntegrityError(
            f"sealed task bytes drifted: {observed} != {SEALED_TASKS_SHA256}"
        )
    try:
        rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("sealed task source is not valid JSONL") from exc
    by_id = {row.get("task_id"): row for row in rows if isinstance(row, dict)}
    if len(by_id) != len(rows):
        raise IntegrityError(
            "sealed task source contains a non-object or duplicate task ID"
        )
    selected = []
    for task_id in SEALED_TASK_IDS:
        row = by_id.get(task_id)
        if row is None:
            raise IntegrityError(f"sealed route-gate task is absent: {task_id}")
        if (
            row.get("schema") != "caveat-27b.contract-shadow-task.v1"
            or row.get("source") != "procedural"
            or row.get("scenario") in _AMAZON
        ):
            raise IntegrityError(
                f"route-gate task is not non-Amazon procedural data: {task_id}"
            )
        if not isinstance(row.get("instruction"), str) or not isinstance(
            row.get("gold_contract"), dict
        ):
            raise IntegrityError(f"route-gate task is incomplete: {task_id}")
        selected.append(row)
    return selected


def _endpoint(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise IntegrityError(
            "base URL must be a credential-free HTTP(S) URL ending in /v1"
        )
    return base_url.rstrip("/") + "/chat/completions"


def _post(
    *, url: str, body: Mapping[str, Any], api_key: str, timeout: float
) -> tuple[dict[str, Any], float]:
    encoded = json.dumps(body).encode()
    started = time.monotonic()
    last_error = "unknown endpoint failure"
    for attempt in range(1, 4):
        request = urllib.request.Request(
            url,
            method="POST",
            data=encoded,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                value = json.loads(response.read().decode())
            if not isinstance(value, dict):
                raise EndpointError("response is not an object")
            return value, time.monotonic() - started
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in _RETRYABLE or attempt == 3:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 3:
                break
        except json.JSONDecodeError as exc:
            raise EndpointError("response is not JSON") from exc
        time.sleep(attempt)
    raise EndpointError(f"request failed after three attempts: {last_error}")


def _content(response: Mapping[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise EndpointError("response has no assistant message") from exc
    # OpenAI-compatible reasoning servers legitimately return ``content=null``
    # when the completion budget is exhausted before the answer channel.  The
    # request reached the intended model and produced a behavioral failure; it
    # is not an endpoint/transport failure.
    if content is None:
        return ""
    if not isinstance(content, str):
        raise EndpointError("assistant content is not text")
    return content


def _evaluate(
    task: Mapping[str, Any],
    *,
    stack: ContractStack,
    endpoint: str,
    model: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    instruction = str(task["instruction"])
    body = stack.request_body(
        model=model,
        instruction=instruction,
        output_format=stack.draft,
        temperature=0.0,
        max_tokens=2048,
    )
    core = {
        "task_id": task["task_id"],
        "scenario": task["scenario"],
        "condition": task["condition"],
    }
    try:
        response, latency = _post(
            url=endpoint, body=body, api_key=api_key, timeout=timeout
        )
        content = _content(response)
    except EndpointError as exc:
        return {
            **core,
            "syntax_valid": False,
            "semantic_valid": False,
            "infrastructure_error": str(exc),
        }
    try:
        stack.draft.model_validate(stack.decode(content))
        parser_valid = True
    except (TypeError, ValueError, json.JSONDecodeError):
        parser_valid = False
    try:
        syntax, semantic, _normalized, error = stack.score(
            instruction, content, task["gold_contract"]
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        syntax, semantic, error = False, False, "assistant produced no parseable answer"
    if syntax != parser_valid:
        raise IntegrityError("exact parser and scorer disagree on syntax validity")
    return {
        **core,
        "syntax_valid": syntax,
        "semantic_valid": semantic,
        "infrastructure_error": None,
        "error": error,
        "assistant_content_sha256": _sha256(content.encode()),
        "latency_seconds": round(latency, 6),
    }


def run_route_gate(
    *,
    arm: str,
    base_url: str,
    model: str,
    tasks_path: Path,
    api_key: str,
    timeout: float = 600.0,
    concurrency: int = 4,
) -> dict[str, Any]:
    if arm not in {"base", "trained"} or not model or not api_key:
        raise IntegrityError("arm, model, and API key must be valid and nonempty")
    if timeout <= 0 or not 1 <= concurrency <= 4:
        raise IntegrityError(
            "timeout must be positive and concurrency must be in [1, 4]"
        )
    tasks = _load_tasks(tasks_path)
    stack = _load_contract_stack()
    endpoint = _endpoint(base_url)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(
            pool.map(
                lambda task: _evaluate(
                    task,
                    stack=stack,
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                ),
                tasks,
            )
        )
    semantic = sum(row["semantic_valid"] for row in results)
    infrastructure = sum(bool(row["infrastructure_error"]) for row in results)
    return {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "amazon_tasks_used": [],
        "arm": arm,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "task_source_sha256": SEALED_TASKS_SHA256,
        "contract_implementation": str(stack.source),
        "contract_implementation_sha256": stack.source_sha256,
        "semantic_pass_fraction": f"{semantic}/4",
        "infrastructure_failures": infrastructure,
        "passed": infrastructure == 0 and semantic == 4,
        "tasks": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("base", "trained"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--api-key-environment", default="CAVEAT_27B_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    api_key = os.environ.get(args.api_key_environment, "")
    if not api_key:
        parser.error(f"environment variable {args.api_key_environment} is unset")
    try:
        result = run_route_gate(
            arm=args.arm,
            base_url=args.base_url,
            model=args.model,
            tasks_path=args.tasks.resolve(),
            api_key=api_key,
            timeout=args.timeout_seconds,
            concurrency=args.concurrency,
        )
    except IntegrityError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 3 if result["infrastructure_failures"] else (0 if result["passed"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
