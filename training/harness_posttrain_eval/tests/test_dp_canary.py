from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain_eval.common import IntegrityError
from harness_posttrain_eval.dp_canary import run_dp_canary, running_by_engine


def _metrics(values: tuple[int, int, int, int]) -> bytes:
    return "".join(
        (
            'vllm:num_requests_running{engine="%d",model_name="qwen"} %d.0\n'
            % (engine, value)
        )
        for engine, value in enumerate(values)
    ).encode()


def test_running_by_engine_requires_exact_nonnegative_four() -> None:
    assert running_by_engine(_metrics((2, 1, 3, 4)).decode()) == {
        0: 2,
        1: 1,
        2: 3,
        3: 4,
    }
    with pytest.raises(IntegrityError, match="expected"):
        running_by_engine(_metrics((2, 1, 3, 4)).decode().replace('engine="3"', 'engine="4"'))
    with pytest.raises(IntegrityError, match="nonnegative integer"):
        running_by_engine(_metrics((2, 1, 3, 4)).decode().replace("3.0", "3.5", 1))


def test_canary_publishes_only_after_all_engines_are_running(tmp_path: Path) -> None:
    samples = iter((_metrics((16, 0, 0, 0)), _metrics((4, 4, 4, 4))))
    posts: list[dict[str, Any]] = []

    def get(_url: str, **_kwargs: Any) -> bytes:
        return next(samples)

    def post(**kwargs: Any) -> dict[str, Any]:
        posts.append(kwargs)
        return {
            "request": kwargs["nonce"],
            "http_status": 200,
            "finish_reason": "length",
        }

    output = tmp_path / "dp-canary.json"
    receipt = run_dp_canary(
        base_url="http://127.0.0.1:18000/v1",
        model="qwen-exact-lora",
        api_key="secret",
        output_path=output,
        concurrency=8,
        max_tokens=256,
        get=get,
        post=post,
    )
    assert len(posts) == 8
    assert receipt["all_engines_observed_running"] is True
    assert receipt["samples"][-1] == {"0": 4, "1": 4, "2": 4, "3": 4}
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


def test_canary_failure_writes_no_receipt(tmp_path: Path) -> None:
    def post(**kwargs: Any) -> dict[str, Any]:
        return {
            "request": kwargs["nonce"],
            "http_status": 200,
            "finish_reason": "stop",
        }

    output = tmp_path / "dp-canary.json"
    with pytest.raises(IntegrityError, match="never observed"):
        run_dp_canary(
            base_url="http://127.0.0.1:18000/v1",
            model="qwen-exact-lora",
            api_key="secret",
            output_path=output,
            concurrency=4,
            max_tokens=256,
            observation_timeout_seconds=0.01,
            sample_interval_seconds=0.005,
            get=lambda *_args, **_kwargs: _metrics((4, 0, 0, 0)),
            post=post,
        )
    assert not output.exists()


def test_canary_is_create_only_before_network_work(tmp_path: Path) -> None:
    output = tmp_path / "dp-canary.json"
    output.write_text("{}", encoding="utf-8")
    calls = 0

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("network must not run")

    with pytest.raises(IntegrityError, match="refusing to overwrite"):
        run_dp_canary(
            base_url="http://127.0.0.1:18000/v1",
            model="qwen-exact-lora",
            api_key="secret",
            output_path=output,
            get=unexpected,
            post=unexpected,
        )
    assert calls == 0
