from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from agentarena.core import environment


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.read()


def test_http_get_json_retries_then_preserves_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[urllib.request.Request, float]] = []
    sleeps: list[float] = []

    def fake_urlopen(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _Response:
        requests.append((request, timeout))
        if len(requests) == 1:
            raise urllib.error.URLError("transient")
        return _Response(b'{"orders": [{"asin": "HERO"}]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(environment.time, "sleep", sleeps.append)

    result = environment.http_get_json(
        "http://127.0.0.1:9999/api/orders",
        retries=3,
        delay=0.25,
        timeout=7,
        headers={"X-Storefront-Ops": "secret"},
    )

    assert result == {"orders": [{"asin": "HERO"}]}
    assert sleeps == [0.25]
    assert len(requests) == 2
    assert all(timeout == 7 for _, timeout in requests)
    assert all(
        request.get_header("X-storefront-ops") == "secret"
        for request, _ in requests
    )


def test_http_get_json_retry_exhaustion_raises_explicit_infra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def failing_urlopen(
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _Response:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("server unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    monkeypatch.setattr(environment.time, "sleep", sleeps.append)

    with pytest.raises(
        RuntimeError,
        match=environment.EVALUATOR_GET_RETRIES_EXHAUSTED,
    ) as caught:
        environment.http_get_json(
            "http://127.0.0.1:9999/api/orders",
            retries=3,
            delay=0.25,
            timeout=7,
            headers={"X-Storefront-Ops": "must-not-leak"},
        )

    assert calls == 3
    assert sleeps == [0.25, 0.5, 0.75]
    assert "must-not-leak" not in str(caught.value)
    assert "failed after 3 attempts" in str(caught.value)
    assert isinstance(caught.value.__cause__, urllib.error.URLError)
