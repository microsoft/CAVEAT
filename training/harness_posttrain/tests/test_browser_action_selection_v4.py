from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import browser_action_selection_v4 as adaptive
from harness_posttrain.artifacts import ArtifactError, canonical_json, publish_json
from harness_posttrain.browser_action_selection import (
    _COMPLETION_BUDGET,
    _SELECTOR_TRANSPORT_POLICY,
    _SINGLE_ATTEMPT_EXECUTION,
    _evidence_key,
)


def _spec(task: int, *, kind: str = "browser_action") -> dict[str, Any]:
    result: dict[str, Any] = {
        "candidate": "step20",
        "kind": kind,
        "task_id": f"sealed-{task}",
        "source": "procedural",
        "scenario": "selection",
        "request": {
            "model": "step20",
            "messages": [{"role": "user", "content": f"frozen {task}"}],
            "temperature": 0.0,
            "max_tokens": 65536 if kind == "browser_action" else 4096,
        },
    }
    if kind == "browser_action":
        result.update(
            {
                "transition": "checkpoint-complete",
                "expected_action": {"click": {"index": 1}},
                "approved_action": {"click": {"index": 1}},
            }
        )
    else:
        result.update({"instruction": "frozen", "gold_contract": {}})
    return result


def _v3_row(spec: dict[str, Any], *, scoring: Any = "must-not-be-read") -> dict[str, Any]:
    response = {
        "choices": [{"finish_reason": "stop", "message": {"content": None}}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 3},
    }
    request = spec["request"]
    return {
        **{key: value for key, value in spec.items() if key != "request"},
        "request": request,
        "request_sha256": adaptive.sha256_bytes(canonical_json(request).encode()),
        "response": response,
        "response_sha256": adaptive.sha256_bytes(canonical_json(response).encode()),
        "assistant_content": None,
        "finish_reason": "stop",
        "completion_ceiling_bound": False,
        "completion_budget": _COMPLETION_BUDGET,
        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
        "selector_process_attempt_id": 1,
        "scoring": scoring,
    }


class _Tokenizer:
    def apply_chat_template(self, *_args: Any, **_kwargs: Any) -> dict[str, list[int]]:
        return {"input_ids": list(range(17))}


def _record(path: Path, body: dict[str, Any], digest_name: str) -> None:
    publish_json(
        path,
        {**body, digest_name: adaptive.sha256_bytes(canonical_json(body).encode())},
    )


def _v3_tree(tmp_path: Path, specs: list[dict[str, Any]]) -> Path:
    v3 = tmp_path / "selection_nonbinding_v3"
    attempt = v3 / "attempts/attempt-0001"
    server = attempt / "server"
    cache = v3 / "response_cache"
    server.mkdir(parents=True)
    cache.mkdir()
    start = {
        "status": "started",
        "attempt_id": 1,
        "cached_before_attempt": 0,
        "expected_total": 480,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
    }
    receipt = {
        "status": "failed",
        "attempt_id": 1,
        "cached_before_attempt": 0,
        "completed_in_attempt": 470,
        "cached_after_attempt": 470,
        "expected_total": 480,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
        "automatic_request_retries": 0,
        "same_process_request_resubmissions": 0,
        "external_process_interruption": False,
        "failure": {
            "type": "ArtifactError",
            "message": "selection completion ceiling bound; increase it before selection",
        },
    }
    _record(attempt / "start.json", start, "start_body_sha256")
    _record(attempt / "receipt.json", receipt, "receipt_body_sha256")
    publish_json(server / "command.json", ["vllm", "--max-model-len", "131072"])
    (server / "vllm.log").write_text("480 POST 200; ten length\n", encoding="utf-8")
    for index, spec in enumerate(specs):
        row = _v3_row(spec, scoring={"poison": index})
        publish_json(cache / f"{index:04d}.json", row)
    return v3


def test_v3_import_ignores_cached_scoring_and_freezes_only_identity_complement(
    tmp_path: Path,
) -> None:
    specs = [_spec(index) for index in range(480)]
    v3 = _v3_tree(tmp_path, specs[:470])
    imported, provenance = adaptive._validate_v3_source(  # noqa: SLF001
        v3=v3, specs=specs, tokenizer=_Tokenizer()
    )
    escalated, allowlist = adaptive._allowlist(  # noqa: SLF001
        specs=specs, imported=imported, tokenizer=_Tokenizer()
    )
    assert len(imported) == provenance["imported_rows"] == 470
    assert [_evidence_key(row) for row in escalated] == [_evidence_key(row) for row in specs[470:]]
    assert allowlist["score_fields_read_before_freeze"] == []
    assert all(row["escalated_max_tokens"] == 262144 - 17 - 1 for row in allowlist["rows"])


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("receipt", "exact fail-closed"),
        ("finish", "identity/provenance"),
        ("request", "identity/provenance"),
    ],
)
def test_v3_import_tamper_fails_closed(tmp_path: Path, mutation: str, match: str) -> None:
    specs = [_spec(index) for index in range(480)]
    v3 = _v3_tree(tmp_path, specs[:470])
    if mutation == "receipt":
        path = v3 / "attempts/attempt-0001/receipt.json"
        receipt = json.loads(path.read_text())
        receipt["completed_in_attempt"] = 469
        path.write_text(json.dumps(receipt), encoding="utf-8")
    else:
        path = next((v3 / "response_cache").glob("*.json"))
        row = json.loads(path.read_text())
        if mutation == "finish":
            row["finish_reason"] = "length"
        else:
            row["request"]["max_tokens"] = 1
        path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ArtifactError, match=match):
        adaptive._validate_v3_source(  # noqa: SLF001
            v3=v3, specs=specs, tokenizer=_Tokenizer()
        )


def test_adaptive_transport_is_one_14400_second_attempt_without_retry(monkeypatch) -> None:
    calls: list[float | None] = []

    def urlopen(*_args: Any, timeout: float | None = None, **_kwargs: Any) -> None:
        calls.append(timeout)
        raise urllib.error.HTTPError("http://local", 503, "unavailable", {}, io.BytesIO())

    monkeypatch.setattr(adaptive.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        adaptive._adaptive_post("http://local/v1", {})  # noqa: SLF001
    assert calls == [14400]


def test_runtime_prompt_count_must_match_frozen_allowlist(monkeypatch) -> None:
    spec = _spec(1)
    spec["request"]["max_tokens"] = 262144 - 17 - 1
    monkeypatch.setattr(
        adaptive,
        "_adaptive_post",
        lambda *_args: {
            "choices": [{"finish_reason": "stop", "message": {"content": None}}],
            "usage": {"prompt_tokens": 16, "completion_tokens": 1},
        },
    )
    with pytest.raises(ArtifactError, match="prompt-token budget"):
        adaptive._execute_escalated("http://local/v1", spec, object, 17)  # noqa: SLF001


def test_interrupted_adaptive_attempt_preserves_rows_and_allows_finalize_without_rerun(
    tmp_path: Path,
) -> None:
    output = tmp_path / "selection_nonbinding_v4"
    (output / "escalation_attempts").mkdir(parents=True)
    specs = [_spec(index) for index in range(10)]
    attempt_id, server_dir, _receipt = adaptive._new_process_attempt(  # noqa: SLF001
        output, specs, []
    )
    server_dir.mkdir()
    publish_json(server_dir / "command.json", ["vllm", "--max-model-len", "262144"])
    (server_dir / "vllm.log").write_text("ten accepted responses\n", encoding="utf-8")
    completed = []
    for spec in specs:
        row = _v3_row(spec, scoring={})
        row.update(
            {
                "schema": adaptive.ADAPTIVE_EVIDENCE_SCHEMA,
                "provenance_tier": "native_context_escalation",
                "natural_request_sha256": row["request_sha256"],
                "adaptive_process_attempt_id": attempt_id,
            }
        )
        completed.append(row)

    allowed = {tuple(_evidence_key(spec)) for spec in specs}
    attempts = adaptive._prepare_process_attempts(  # noqa: SLF001
        output, completed, allowed, recover_interrupted=True
    )
    assert attempts[0]["status"] == "externally_interrupted"
    assert attempts[0]["accepted"] == attempts[0]["submitted"] == 10
    assert [
        spec for spec in specs if _evidence_key(spec) not in {_evidence_key(r) for r in completed}
    ] == []

    evidence = [
        *[{"provenance_tier": "v3_natural_stop_import"} for _index in range(470)],
        *completed,
    ]
    summary = adaptive._adaptive_summary(evidence, attempts=attempts)  # noqa: SLF001
    assert summary["accepted_responses"] == 480
    assert summary["total_generation_attempts"] is None
    assert summary["generation_attempts_lower_bound"] == 490
    assert summary["possible_abandoned_adaptive_generations"] is True
    assert summary["aggregate_zero_generation_resets_claimed"] is False
