from __future__ import annotations

import json
from typing import Any

import pytest

from harness_posttrain import browser_action_fixed_v5_gate as gate
from harness_posttrain.artifacts import ArtifactError


def _metrics(**overrides: int) -> dict[str, Any]:
    value: dict[str, Any] = {
        "action_transition_exact_count": 15,
        "complete_checkpoint_exact_count": 0,
        "repair_checkpoint_exact_count": 0,
        "incomplete_continue_exact_count": 7,
        "postapproval_action_exact_count": 8,
        "contract_syntax_valid_count": 64,
        "contract_semantic_exact_count": 0,
        "premature_checkpoint_or_purchase_count": 0,
    }
    value.update(overrides)
    return value


def test_fixed_gate_criteria_require_targeted_improvement_and_preservation() -> None:
    baseline = _metrics()
    passing = _metrics(
        action_transition_exact_count=18,
        complete_checkpoint_exact_count=1,
        repair_checkpoint_exact_count=1,
        postapproval_action_exact_count=7,
        contract_syntax_valid_count=62,
    )
    checks = gate._gate_checks(baseline, passing, distinct=True)  # noqa: SLF001
    assert checks and all(checks.values())

    no_target_gain = _metrics(action_transition_exact_count=18)
    checks = gate._gate_checks(baseline, no_target_gain, distinct=True)  # noqa: SLF001
    assert checks["action_transition_improves"] is True
    assert checks["complete_plus_repair_improves"] is False

    regressed = dict(passing, premature_checkpoint_or_purchase_count=1)
    checks = gate._gate_checks(baseline, regressed, distinct=True)  # noqa: SLF001
    assert checks["no_extra_premature_checkpoint_or_purchase"] is False
    assert gate._gate_checks(baseline, passing, distinct=False)[  # noqa: SLF001
        "candidate_adapter_is_distinct"
    ] is False


def test_fixed_specs_use_exact_native_headroom_for_browser_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        {
            "candidate": gate._CANDIDATE,  # noqa: SLF001
            "kind": "contract",
            "task_id": "contract",
            "transition": "",
            "request": {"messages": [{"role": "user", "content": "contract"}]},
        },
        {
            "candidate": gate._CANDIDATE,  # noqa: SLF001
            "kind": "browser_action",
            "task_id": "action",
            "transition": "repair-rejected",
            "request": {"messages": [{"role": "user", "content": "action"}]},
        },
    ]
    monkeypatch.setattr(gate, "_expected_specs", lambda **_kwargs: specs)
    monkeypatch.setattr(gate, "_rendered_prompt_tokens", lambda _tokenizer, _request: 19_568)
    result = gate._fixed_specs(  # noqa: SLF001
        contracts=[], checkpoints=[], system="system", output_model=object, tokenizer=object()
    )
    assert result[0]["request"]["max_tokens"] == 4096
    assert result[1]["request"]["max_tokens"] == 262_144 - 19_568 - 1
    for spec in result:
        audit = spec["prompt_token_audit"]
        assert audit["rendered_prompt_tokens"] + audit["max_tokens"] <= 262_144


def test_fixed_specs_reject_nonpositive_native_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = [
        {
            "candidate": gate._CANDIDATE,  # noqa: SLF001
            "kind": "browser_action",
            "task_id": "action",
            "transition": "repair-rejected",
            "request": {"messages": [{"role": "user", "content": "action"}]},
        }
    ]
    monkeypatch.setattr(gate, "_expected_specs", lambda **_kwargs: specs)
    monkeypatch.setattr(gate, "_rendered_prompt_tokens", lambda _tokenizer, _request: 262_144)
    with pytest.raises(ArtifactError, match="native context"):
        gate._fixed_specs(  # noqa: SLF001
            contracts=[],
            checkpoints=[],
            system="system",
            output_model=object,
            tokenizer=object(),
        )


def test_candidate_execution_rejects_length_and_prompt_token_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = {
        "candidate": gate._CANDIDATE,  # noqa: SLF001
        "kind": "contract",
        "task_id": "task",
        "source": "procedural",
        "scenario": "shadow",
        "transition": "",
        "instruction": "Choose one",
        "gold_contract": {},
        "request": {"messages": [], "max_tokens": 4096},
        "prompt_token_audit": {
            "rendered_prompt_tokens": 37,
            "max_tokens": 4096,
            "native_max_model_len": 262_144,
            "native_context_reserve_tokens": 258_011,
        },
    }
    response = {
        "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 37},
    }
    monkeypatch.setattr(gate, "_post_once", lambda _base, _request: response)
    with pytest.raises(ArtifactError, match="did not stop naturally"):
        gate._execute_candidate_spec("http://local/v1", spec, object, 1)  # noqa: SLF001

    response["choices"][0]["finish_reason"] = "stop"
    response["usage"]["prompt_tokens"] = 36
    with pytest.raises(ArtifactError, match="prompt token count"):
        gate._execute_candidate_spec("http://local/v1", spec, object, 1)  # noqa: SLF001


def test_gate_policy_is_fixed_candidate_only_and_contains_no_amazon() -> None:
    assert gate._GATE_CRITERIA == {  # noqa: SLF001
        "minimum_action_transition_exact_count_delta": 2,
        "minimum_complete_plus_repair_exact_count_delta": 2,
        "minimum_strictly_improved_target_transition_count": 1,
        "maximum_incomplete_continue_exact_count_regression": 1,
        "maximum_postapproval_action_exact_count_regression": 1,
        "maximum_contract_syntax_valid_count_regression": 2,
        "maximum_contract_semantic_exact_count_regression": 0,
        "maximum_premature_checkpoint_or_purchase_count_delta": 0,
        "require_distinct_candidate_adapter": True,
        "require_all_candidate_responses_natural_stop": True,
    }
    source = gate.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()  # noqa: PTH123
    assert '"selection_performed": False' in text
    assert '"amazon_data_used": False' in text
    assert "run_fixed_v5_gate" in text


class _ParsedOutput:
    def __init__(self, value: dict[str, Any]):
        self.value = value

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self.value


class _OutputModel:
    @classmethod
    def model_validate_json(cls, content: str) -> _ParsedOutput:
        value = json.loads(content)
        assert isinstance(value, dict)
        return _ParsedOutput(value)


def _checkpoint_action(source_url: str, *, label: str = "Product P") -> dict[str, Any]:
    return {
        "decision_checkpoint": {
            "frontier": {
                "inspected_count": 1,
                "advertised_count": 1,
                "coverage_mode": "advertised_total",
                "advertised_page_count": None,
                "enumerated_page_count": None,
                "excluded_count": 0,
                "unresolved_count": 0,
                "exhausted": True,
                "basis": "1 result",
            },
            "candidates": [
                {
                    "id": "p",
                    "label": label,
                    "source_url": source_url,
                    "facts": [],
                }
            ],
            "proposed_candidate_id": "p",
        }
    }


def _action_spec(user: str) -> dict[str, Any]:
    expected = _checkpoint_action("https://shop.local/products/p")
    return {
        "kind": "browser_action",
        "transition": "checkpoint-complete",
        "expected_action": expected,
        "approved_action": expected,
        "request": {"messages": [{"role": "user", "content": user}]},
    }


def test_observable_scoring_ignores_only_a_hidden_source_url() -> None:
    spec = _action_spec("Public item p is complete; no URL is displayed.")
    wrong_url = _checkpoint_action("https://invented.invalid/p")
    content = json.dumps({"action": [wrong_url]})
    score = gate._response_score(spec, content, _OutputModel)  # noqa: SLF001
    assert score["transition_exact"] is True
    assert score["hidden_source_url_values_substituted_for_scoring"] == 1
    entries = score["observable_field_policy"]["checkpoint_candidates"]
    assert entries == [
        {
            "candidate_id": "p",
            "source_url_expected_sha256": gate.sha256_bytes(
                b"https://shop.local/products/p"
            ),
            "source_url_present_in_user_messages": False,
            "compare_source_url": False,
        }
    ]

    wrong_label = _checkpoint_action(
        "https://invented.invalid/p", label="A different product"
    )
    score = gate._response_score(  # noqa: SLF001
        spec, json.dumps({"action": [wrong_label]}), _OutputModel
    )
    assert score["transition_exact"] is False


def test_observable_scoring_requires_a_visible_source_url_exactly() -> None:
    expected_url = "https://shop.local/products/p"
    spec = _action_spec(f"Public item p has source_url {expected_url}.")
    wrong_url = _checkpoint_action("https://invented.invalid/p")
    score = gate._response_score(  # noqa: SLF001
        spec, json.dumps({"action": [wrong_url]}), _OutputModel
    )
    assert score["transition_exact"] is False
    assert score["hidden_source_url_values_substituted_for_scoring"] == 0
    entry = score["observable_field_policy"]["checkpoint_candidates"][0]
    assert entry["source_url_present_in_user_messages"] is True
    assert entry["compare_source_url"] is True
