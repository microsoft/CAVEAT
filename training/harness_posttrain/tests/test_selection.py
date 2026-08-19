from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import selection
from harness_posttrain.artifacts import ArtifactError, sha256_file
from harness_posttrain.config import Campaign


@pytest.mark.parametrize("content", [None, "", "   "])
def test_contract_query_scores_empty_assistant_content_as_failure(
    monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    monkeypatch.setattr(selection, "contract_request_body", lambda **_kwargs: {})
    monkeypatch.setattr(
        selection,
        "_post",
        lambda *_args, **_kwargs: {"choices": [{"message": {"content": content}}]},
    )

    result = selection._contract_query(  # noqa: SLF001
        "http://127.0.0.1:8000/v1",
        "candidate",
        {"task_id": "task-1", "instruction": "Choose.", "gold_contract": {}},
        object,
    )

    assert result == ("task-1", False, False)


def test_post_retries_only_transient_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": []}).encode()

    def urlopen(*_args: Any, **_kwargs: Any) -> Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "http://127.0.0.1:8000/v1/chat/completions",
                503,
                "unavailable",
                {},
                io.BytesIO(),
            )
        return Response()

    monkeypatch.setattr(selection.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(selection.time, "sleep", lambda _seconds: None)

    assert selection._post("http://127.0.0.1:8000/v1", {}) == {"choices": []}  # noqa: SLF001
    assert calls == 2


def test_post_does_not_retry_nontransient_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def urlopen(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8000/v1/chat/completions",
            400,
            "bad request",
            {},
            io.BytesIO(),
        )

    monkeypatch.setattr(selection.urllib.request, "urlopen", urlopen)

    with pytest.raises(urllib.error.HTTPError):
        selection._post("http://127.0.0.1:8000/v1", {})  # noqa: SLF001
    assert calls == 1


def _completed_selection(tmp_path: Path, campaign: Campaign) -> tuple[dict, dict[str, Path]]:
    adapters = {}
    metrics = {}
    for profile in selection.PROFILES:
        for update in selection.UPDATES:
            name = f"{profile}-u{update}"
            adapter = tmp_path / name
            adapter.mkdir(parents=True)
            (adapter / "adapter_config.json").write_text(
                json.dumps({"profile": profile, "update": update}), encoding="utf-8"
            )
            adapters[name] = adapter.resolve()
            syntax = 64 if name == "recovery-heavy-u20" else update
            metrics[name] = {
                "syntax_valid": syntax,
                "semantic_exact": 0,
                "checkpoint_tool_valid": 0,
                "contract_tasks": 64,
                "checkpoint_tool_tasks": 8,
                "syntax_valid_rate": syntax / 64,
                "semantic_exact_rate": 0.0,
                "checkpoint_tool_valid_rate": 0.0,
            }
    selected_name = "recovery-heavy-u20"
    selected_adapter = adapters[selected_name]
    manifest = {
        "schema": selection.SELECTION_SCHEMA,
        "campaign_digest": campaign.digest,
        "selection_split": "procedural_validation",
        "amazon_scenarios_used": [],
        "num_gpus": 4,
        "candidate_count": 9,
        "rank_order": campaign.campaign["selection_gate"]["rank_order"],
        "selected": {
            "name": selected_name,
            "profile": "recovery-heavy",
            "update": 20,
            "adapter": str(selected_adapter),
            "adapter_config_sha256": sha256_file(
                selected_adapter / "adapter_config.json"
            ),
            "metrics": metrics[selected_name],
        },
        "candidates": metrics,
    }
    return manifest, adapters


def test_completed_selection_can_be_reused_after_downstream_failure(
    tmp_path: Path, campaign: Campaign
) -> None:
    manifest, adapters = _completed_selection(tmp_path, campaign)
    assert (
        selection._validate_completed_selection(  # noqa: SLF001
            manifest, campaign=campaign, adapters=adapters, num_gpus=4
        )
        is manifest
    )


def test_completed_selection_rejects_metric_or_adapter_drift(
    tmp_path: Path, campaign: Campaign
) -> None:
    manifest, adapters = _completed_selection(tmp_path, campaign)
    manifest["candidates"]["recovery-heavy-u20"]["syntax_valid_rate"] = 0.5
    with pytest.raises(ArtifactError, match="metric is inconsistent"):
        selection._validate_completed_selection(  # noqa: SLF001
            manifest, campaign=campaign, adapters=adapters, num_gpus=4
        )

    manifest, adapters = _completed_selection(tmp_path / "second", campaign)
    (adapters["recovery-heavy-u20"] / "adapter_config.json").write_text(
        '{"drift":true}', encoding="utf-8"
    )
    with pytest.raises(ArtifactError, match="selected checkpoint"):
        selection._validate_completed_selection(  # noqa: SLF001
            manifest, campaign=campaign, adapters=adapters, num_gpus=4
        )
