from __future__ import annotations

import copy
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import browser_action_selection as selection
from harness_posttrain.artifacts import ArtifactError, canonical_json
from harness_posttrain.browser_action_curriculum import _output_stack
from harness_posttrain.browser_action_selection import (
    _ACTION_MAX_TOKENS,
    _COMPLETION_BUDGET,
    _SELECTOR_MAX_MODEL_LEN,
    _SELECTOR_REQUEST_TIMEOUT_SECONDS,
    _SELECTOR_TRANSPORT_POLICY,
    _SINGLE_ATTEMPT_EXECUTION,
    EVIDENCE_SCHEMA,
    _choose,
    _metrics_from_evidence,
    _score_action_content,
    _selection_rank,
)
from harness_posttrain.selection import MultiLoraServer


def _agent_content(action: dict) -> str:
    _system, model = _output_stack()
    value = {
        "thinking": "Check the state transition.",
        "evaluation_previous_goal": "The previous action is complete.",
        "memory": "The procedural option state is tracked.",
        "next_goal": "Take the one authorized action.",
        "action": [action],
    }
    return canonical_json(model.model_validate(value).model_dump(mode="json"))


def _checkpoint(candidate: str = "opt_a") -> dict:
    return {
        "decision_checkpoint": {
            "frontier": {
                "inspected_count": 2,
                "advertised_count": 2,
                "coverage_mode": "advertised_total",
                "advertised_page_count": None,
                "enumerated_page_count": None,
                "excluded_count": 1,
                "unresolved_count": 0,
                "exhausted": True,
                "basis": "2 results",
            },
            "candidates": [
                {
                    "id": candidate,
                    "label": "Alpha",
                    "source_url": f"https://shop.local/products/{candidate}",
                    "facts": [
                        {
                            "criterion_id": "objective_score",
                            "state": "known",
                            "value": 9,
                            "unit": "points",
                        }
                    ],
                }
            ],
            "proposed_candidate_id": candidate,
        }
    }


@pytest.mark.parametrize(
    ("transition", "action"),
    [
        ("continue-incomplete", {"click": {"index": 900}}),
        ("checkpoint-complete", _checkpoint()),
        ("repair-rejected", _checkpoint()),
        ("act-after-approval", {"click": {"index": 901}}),
    ],
)
def test_real_agent_output_scores_exact_transition(transition: str, action: dict) -> None:
    _system, output_model = _output_stack()
    scored = _score_action_content(
        _agent_content(action),
        output_model=output_model,
        transition=transition,
        expected_action=action,
        approved_action={"click": {"index": 901}},
    )
    assert scored["agent_output_schema_valid"] is True
    assert scored["transition_exact"] is True
    assert scored["premature_checkpoint_or_purchase"] is False


def test_incomplete_rejects_checkpoint_and_approved_purchase() -> None:
    _system, output_model = _output_stack()
    for action in (_checkpoint(), {"click": {"index": 901}}):
        scored = _score_action_content(
            _agent_content(action),
            output_model=output_model,
            transition="continue-incomplete",
            expected_action={"click": {"index": 900}},
            approved_action={"click": {"index": 901}},
        )
        assert scored["transition_exact"] is False
        assert scored["premature_checkpoint_or_purchase"] is True


def test_checkpoint_requires_exact_arguments_and_action_alone() -> None:
    _system, output_model = _output_stack()
    expected = _checkpoint()
    wrong = _checkpoint("opt_b")
    wrong["decision_checkpoint"]["candidates"][0]["label"] = "Beta"
    for content in (
        _agent_content(wrong),
        "not json",
        canonical_json(
            {
                "thinking": "x",
                "evaluation_previous_goal": "x",
                "memory": "x",
                "next_goal": "x",
                "action": [expected, {"click": {"index": 901}}],
            }
        ),
    ):
        scored = _score_action_content(
            content,
            output_model=output_model,
            transition="checkpoint-complete",
            expected_action=expected,
            approved_action={"click": {"index": 901}},
        )
        assert scored["transition_exact"] is False


def _contract_row(candidate: str, task: int, *, syntax: bool, semantic: bool) -> dict:
    return {
        "schema": EVIDENCE_SCHEMA,
        "candidate": candidate,
        "kind": "contract",
        "task_id": f"contract-{task}",
        "scoring": {"syntax_valid": syntax, "semantic_exact": semantic},
    }


def _action_row(
    candidate: str,
    task: int,
    transition: str,
    *,
    exact: bool,
    premature: bool = False,
) -> dict:
    return {
        "schema": EVIDENCE_SCHEMA,
        "candidate": candidate,
        "kind": "browser_action",
        "task_id": f"action-{task}",
        "transition": transition,
        "scoring": {
            "transition_exact": exact,
            "premature_checkpoint_or_purchase": premature,
        },
    }


def _evidence(candidate: str, misses: dict[str, int] | None = None) -> list[dict]:
    misses = misses or {}
    rows = [
        _contract_row(
            candidate,
            index,
            syntax=True,
            semantic=index >= misses.get("contract", 0),
        )
        for index in range(64)
    ]
    for transition in (
        "continue-incomplete",
        "checkpoint-complete",
        "repair-rejected",
        "act-after-approval",
    ):
        for index in range(8):
            rows.append(
                _action_row(
                    candidate,
                    index,
                    transition,
                    exact=index >= misses.get(transition, 0),
                    premature=(
                        transition == "continue-incomplete" and index < misses.get("premature", 0)
                    ),
                )
            )
    return rows


def test_metrics_enforce_every_hard_gate_and_denominator() -> None:
    metrics = _metrics_from_evidence(_evidence("step22"), {"step22"})["step22"]
    assert metrics["passes_hard_gates"] is True
    assert metrics["contract_semantic_exact_rate"] == 1.0
    assert metrics["action_transition_exact_rate"] == 1.0

    one_complete_miss = _metrics_from_evidence(
        _evidence("step22", {"checkpoint-complete": 1}), {"step22"}
    )["step22"]
    assert one_complete_miss["passes_hard_gates"] is False
    assert one_complete_miss["gate_checks"]["complete_checkpoint"] is False

    with pytest.raises(ArtifactError, match="denominator"):
        _metrics_from_evidence(_evidence("step22")[:-1], {"step22"})


def test_selection_uses_action_first_rank_and_earliest_tie_break() -> None:
    evidence = []
    for candidate in ("step20", "step22", "step24"):
        evidence.extend(_evidence(candidate))
    metrics = _metrics_from_evidence(evidence, {"step20", "step22", "step24"})
    selected, reason = _choose(metrics)
    assert selected == "step20"
    assert reason == "highest_ranked_hard_gate_pass"

    metrics["step20"] = copy.deepcopy(metrics["step20"])
    metrics["step20"]["passes_hard_gates"] = False
    assert _choose(metrics)[0] == "step22"

    # More exact actions outrank a better contract, as required by the selector.
    left = copy.deepcopy(metrics["step22"])
    right = copy.deepcopy(metrics["step24"])
    left["action_transition_exact_count"] = 31
    left["contract_semantic_exact_count"] = 64
    right["action_transition_exact_count"] = 32
    right["contract_semantic_exact_count"] = 61
    assert _selection_rank(24, right) > _selection_rank(22, left)


def test_no_passing_continuation_falls_back_to_step20() -> None:
    metrics = {
        f"step{step}": {
            "passes_hard_gates": False,
            "action_transition_exact_count": 0,
            "minimum_action_transition_exact_count": 0,
            "repair_checkpoint_exact_count": 0,
            "postapproval_action_exact_count": 0,
            "complete_checkpoint_exact_count": 0,
            "incomplete_continue_exact_count": 0,
            "contract_semantic_exact_count": 0,
            "contract_syntax_valid_count": 0,
        }
        for step in (20, 22, 24, 26, 28)
    }
    assert _choose(metrics) == (
        "step20",
        "fallback_step20_no_candidate_passed_all_hard_gates",
    )


def test_selector_module_contains_no_amazon_task_dependency() -> None:
    source = Path(__file__).parents[1] / "src/harness_posttrain/browser_action_selection.py"
    text = source.read_text(encoding="utf-8")
    assert "agentarena.benchmark" not in text
    assert "results/harness_posttrain_final_eval" not in text
    assert '"source": "procedural"' in text


def test_action_requests_use_a_nonbinding_context_safe_ceiling(monkeypatch) -> None:
    rows = [
        (
            transition,
            {
                "messages": [
                    {"role": "system", "content": "frozen"},
                    {
                        "role": "assistant",
                        "content": canonical_json({"action": [{"click": {"index": 1}}]}),
                    },
                ]
            },
        )
        for transition in (
            "continue-incomplete",
            "checkpoint-complete",
            "repair-rejected",
            "act-after-approval",
        )
    ]
    monkeypatch.setattr(selection, "_system_with_contract", lambda system, _contract: system)
    monkeypatch.setattr(selection, "_action_rows", lambda *_args: rows)
    specs = selection._action_specs(  # noqa: SLF001
        model="step20",
        task={
            "task_id": "sealed-1",
            "source": "procedural",
            "scenario": "procedural_selection",
            "rendered_basis": "2 results",
            "public_state": {},
            "expected_arguments": {},
        },
        contract={"gold_contract": {}},
        system="frozen",
        output_model=object,
    )
    assert {spec["request"]["max_tokens"] for spec in specs} == {_ACTION_MAX_TOKENS}
    assert _COMPLETION_BUDGET == {
        "selector_max_model_len": 131072,
        "action_max_tokens": 65536,
        "contract_max_tokens": 4096,
        "frozen_max_prompt_tokens": 19568,
        "worst_case_prompt_plus_completion_tokens": 85104,
        "context_reserve_tokens": 45968,
        "request_transport": {
            "request_timeout_seconds": 7200,
            "maximum_attempts_per_request": 1,
            "retry_count_per_request": 0,
            "client_resubmit_on_transport_failure": False,
            "generation_reset_policy": "forbid_client_resubmission",
        },
    }
    server = MultiLoraServer(
        base_model=Path("/base"),
        adapters={"step20": Path("/adapter")},
        output_dir=Path("/output"),
        gpu_ids=(0, 1, 2, 3),
        max_model_len=_SELECTOR_MAX_MODEL_LEN,
    )
    command = server.command()
    assert command[command.index("--max-model-len") + 1] == "131072"


def test_selector_transport_is_one_7200_second_attempt_without_hidden_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float | None] = []

    def urlopen(*_args: Any, timeout: float | None = None, **_kwargs: Any) -> None:
        calls.append(timeout)
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8000/v1/chat/completions",
            503,
            "unavailable",
            {},
            io.BytesIO(),
        )

    monkeypatch.setattr(selection.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        selection._selector_post("http://127.0.0.1:8000/v1", {})  # noqa: SLF001
    assert calls == [_SELECTOR_REQUEST_TIMEOUT_SECONDS]
    assert _SELECTOR_TRANSPORT_POLICY == {
        "request_timeout_seconds": 7200,
        "maximum_attempts_per_request": 1,
        "retry_count_per_request": 0,
        "client_resubmit_on_transport_failure": False,
        "generation_reset_policy": "forbid_client_resubmission",
    }


def test_successful_selector_evidence_attests_one_attempt_and_zero_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
            ).encode()

    monkeypatch.setattr(selection.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    row = selection._execute_spec(  # noqa: SLF001
        "http://127.0.0.1:8000/v1",
        {
            "candidate": "step20",
            "kind": "contract",
            "task_id": "sealed-1",
            "source": "procedural",
            "scenario": "procedural_selection",
            "instruction": "frozen",
            "gold_contract": {},
            "request": {"model": "step20", "max_tokens": 4096},
        },
        object,
    )
    assert row["request_execution"] == _SINGLE_ATTEMPT_EXECUTION
    assert row["selector_process_attempt_id"] == 1
    assert row["completion_budget"]["request_transport"] == _SELECTOR_TRANSPORT_POLICY


def test_length_finish_reason_is_rejected_before_scoring(monkeypatch) -> None:
    monkeypatch.setattr(
        selection,
        "_selector_post",
        lambda *_args: {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "partial output must never be scored"},
                }
            ]
        },
    )
    with pytest.raises(ArtifactError, match="completion ceiling bound"):
        selection._execute_spec(  # noqa: SLF001
            "http://127.0.0.1:8000/v1",
            {
                "candidate": "step20",
                "kind": "contract",
                "task_id": "sealed-1",
                "source": "procedural",
                "scenario": "procedural_selection",
                "instruction": "frozen",
                "gold_contract": {},
                "request": {"model": "step20", "max_tokens": 65536},
            },
            object,
        )


def _cache_spec(task_id: str) -> dict[str, Any]:
    return {
        "candidate": "step20",
        "kind": "contract",
        "task_id": task_id,
        "source": "procedural",
        "scenario": "procedural_selection",
        "instruction": "frozen",
        "gold_contract": {},
        "request": {"model": "step20", "max_tokens": 4096, "messages": []},
    }


def _cache_row(spec: dict[str, Any], attempt_id: int) -> dict[str, Any]:
    response = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
    request = spec["request"]
    return {
        "schema": EVIDENCE_SCHEMA,
        **{key: value for key, value in spec.items() if key != "request"},
        "request": request,
        "request_sha256": selection.sha256_bytes(canonical_json(request).encode()),
        "response": response,
        "response_sha256": selection.sha256_bytes(canonical_json(response).encode()),
        "assistant_content": None,
        "finish_reason": "stop",
        "completion_ceiling_bound": False,
        "completion_budget": _COMPLETION_BUDGET,
        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
        "selector_process_attempt_id": attempt_id,
        "scoring": {
            "syntax_valid": False,
            "semantic_exact": False,
            "normalized_contract": None,
            "error": "empty assistant content",
        },
    }


def test_interrupted_attempt_preserves_rows_and_resume_selects_only_absent_specs(
    tmp_path: Path,
) -> None:
    output = tmp_path / "selection_nonbinding_v3"
    cache_dir = output / "response_cache"
    cache_dir.mkdir(parents=True)
    specs = [_cache_spec("sealed-1"), _cache_spec("sealed-2")]

    attempt1, _attempt_dir1, _receipt1 = selection._next_attempt(  # noqa: SLF001
        output, cache_before=0, expected=2
    )
    row1 = _cache_row(specs[0], attempt1)
    selection.publish_json(cache_dir / selection._cache_name(row1), row1)  # noqa: SLF001

    cached = selection._cache_rows(cache_dir, specs, object)  # noqa: SLF001
    assert list(cached) == [selection._evidence_key(specs[0])]  # noqa: SLF001
    selection._prepare_attempts(output, cached_rows=1, expected=2)  # noqa: SLF001
    first_receipt = selection.read_json(output / "attempts/attempt-0001/receipt.json")
    assert first_receipt["status"] == "externally_interrupted"
    assert first_receipt["completed_in_attempt"] == 1

    missing = selection._uncached_specs(specs, cached)  # noqa: SLF001
    assert [row["task_id"] for row in missing] == ["sealed-2"]
    attempt2, _attempt_dir2, receipt2 = selection._next_attempt(  # noqa: SLF001
        output, cache_before=1, expected=2
    )
    row2 = _cache_row(missing[0], attempt2)
    selection.publish_json(cache_dir / selection._cache_name(row2), row2)  # noqa: SLF001
    selection.publish_json(
        receipt2,
        selection._attempt_receipt(  # noqa: SLF001
            attempt_id=attempt2,
            status="complete",
            cache_before=1,
            completed=1,
            expected=2,
            failure=None,
        ),
    )

    resumed = selection._cache_rows(cache_dir, specs, object)  # noqa: SLF001
    evidence = sorted(resumed.values(), key=selection._evidence_key)  # noqa: SLF001
    attempts = selection._attempt_inventory(output, evidence)  # noqa: SLF001
    summary = selection._request_execution_summary(evidence, attempts)  # noqa: SLF001
    assert [row["status"] for row in attempts] == ["externally_interrupted", "complete"]
    assert summary["all_accepted_responses_completed_single_http_attempt"] is True
    assert summary["uncached_inflight_requests_may_have_been_reexecuted"] is True
    assert summary["aggregate_zero_generation_resets_claimed"] is False
    assert summary["aggregate_client_generation_reset_count"] is None
    assert selection._cache_inventory(cache_dir, evidence)["rows"] == 2  # noqa: SLF001


@pytest.mark.parametrize("mode", ["partial", "symlink", "request-mismatch"])
def test_response_cache_tamper_and_partial_entries_fail_closed(tmp_path: Path, mode: str) -> None:
    cache_dir = tmp_path / "response_cache"
    cache_dir.mkdir()
    spec = _cache_spec("sealed-1")
    row = _cache_row(spec, 1)
    path = cache_dir / selection._cache_name(row)  # noqa: SLF001
    if mode == "partial":
        path.write_text('{"schema":', encoding="utf-8")
    elif mode == "symlink":
        target = tmp_path / "outside.json"
        target.write_text(json.dumps(row), encoding="utf-8")
        path.symlink_to(target)
    else:
        row["request"] = {"model": "different"}
        path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ArtifactError):
        selection._cache_rows(cache_dir, [spec], object)  # noqa: SLF001


def test_failed_same_process_attempt_cannot_resume(tmp_path: Path) -> None:
    output = tmp_path / "selection_nonbinding_v3"
    attempt, _attempt_dir, receipt = selection._next_attempt(  # noqa: SLF001
        output, cache_before=0, expected=1
    )
    selection.publish_json(
        receipt,
        selection._attempt_receipt(  # noqa: SLF001
            attempt_id=attempt,
            status="failed",
            cache_before=0,
            completed=0,
            expected=1,
            failure=TimeoutError("bound transport failed"),
        ),
    )
    with pytest.raises(ArtifactError, match="failed"):
        selection._prepare_attempts(output, cached_rows=0, expected=1)  # noqa: SLF001


def test_selector_output_lock_rejects_concurrent_owner_and_symlink(tmp_path: Path) -> None:
    output = tmp_path / "selection_nonbinding_v3"
    with selection._selection_lock(output):  # noqa: SLF001
        with pytest.raises(ArtifactError, match="another process"):
            with selection._selection_lock(output):  # noqa: SLF001
                pytest.fail("a second selector acquired the same output lock")

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked-selection"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ArtifactError, match="symlink"):
        with selection._selection_lock(linked):  # noqa: SLF001
            pytest.fail("a selector accepted a symlink output")
