from __future__ import annotations

import json
from pathlib import Path

from agentarena.core.models import ModelSpec
from harness_posttrain.contract_refinement import _draft_types, contract_request_body
from harness_posttrain_eval.prepare_final import _model_specs


def test_qwen_eval_request_matches_training_selection_generation_semantics():
    package_root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (package_root / "configs/campaign.json").read_text(encoding="utf-8")
    )
    inference = config["inference"]

    # Qwen thinking is supplied by the frozen tokenizer template.  Treating
    # the endpoint as an OpenAI reasoning model would inject reasoning_effort,
    # which neither training nor sealed checkpoint selection used.
    assert inference["qwen_is_reasoning_model"] is False
    assert inference["qwen_thinking_mode"] == "tokenizer_native"
    assert inference["request_parameters"]["temperature"] == 0.0
    assert inference["request_parameters"]["frequency_penalty"] is None

    specs = _model_specs(
        base_name="Qwen base",
        trained_name="Qwen trained",
        base_url="http://127.0.0.1:18000/v1",
        trained_url="http://127.0.0.1:18100/v1",
        base_deployment="qwen35-base",
        trained_deployment="qwen35-trained",
        api_key_environment="TEST_API_KEY",
        frequency_penalty=inference["request_parameters"]["frequency_penalty"],
    )
    for spec in specs.values():
        assert spec["extra"] == {"frequency_penalty": None}
        assert ModelSpec.parse(spec).reasoning is False

    # The sealed non-Amazon selection route is the source request contract:
    # tokenizer-native thinking remains enabled and frequency_penalty is absent.
    draft, _extension = _draft_types()
    body = contract_request_body(
        model="qwen35-trained",
        instruction="Choose the least expensive qualifying option.",
        output_format=draft,
        temperature=0.0,
        max_tokens=2048,
    )
    assert body["temperature"] == 0.0
    assert "frequency_penalty" not in body
    assert "reasoning_effort" not in body
