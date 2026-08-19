from __future__ import annotations

import pytest

from harness_posttrain.artifacts import ArtifactError
from harness_posttrain.procedural_corpus import _leakage_audit


def _row(content: str) -> dict[str, object]:
    return {
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "{}"},
        ],
        "tools": [],
    }


def test_tent_leakage_scan_uses_token_boundaries() -> None:
    result = _leakage_audit([_row("The JSON content is ordinary.")])
    assert result["sealed_amazon_category_terms_found"] == 0
    with pytest.raises(ArtifactError, match="sealed Amazon category text"):
        _leakage_audit([_row("Buy a tent after comparing the options.")])

