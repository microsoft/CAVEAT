from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import stat
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import browser_use
import pytest

from . import browseruse as browseruse_scaffold
from .browseruse import (
    _ActionErrorProvenanceCollector,
    _ContextCapToolCollector,
    _EvaluateResultStore,
    _EvaluateResultStoreCapacityError,
    _EvaluateResultStoreIntegrityError,
    _InspectEvaluateResult,
    _LosslessReadStateRecoveryAudit,
    _ReplaceFileSafetyCollector,
    _WholeRunTimeoutError,
    _action_error_audit,
    _action_error_audit_before_agent_construction,
    _await_whole_run,
    _context_cap_audit,
    _context_cap_audit_before_agent_construction,
    _explicit_frequency_penalty,
    _finalize_limit_audit,
    _history_to_steps,
    _install_action_error_provenance_audit,
    _install_context_cap_tool_audit,
    _install_evaluate_result_spill,
    _install_lossless_read_state_recovery,
    _install_replace_file_recursive_amplification_guard,
    _lift_action_error_prompt_cap,
    _lift_evaluate_serialization_backstop,
    _lossless_read_state_recovery_authorization_from_environment,
    _is_agent_output_validation_error,
    _new_limit_audit,
    _run,
)


class _Action:
    def __init__(self, value: dict):
        self.value = value

    def model_dump(self, **_kwargs):
        return dict(self.value)


def test_frequency_penalty_override_is_explicit_and_fail_closed():
    absent = SimpleNamespace(extra={})
    omitted = SimpleNamespace(extra={"frequency_penalty": None})
    numeric = SimpleNamespace(extra={"frequency_penalty": 0})

    assert _explicit_frequency_penalty(absent) == (False, None)
    assert _explicit_frequency_penalty(omitted) == (True, None)
    assert _explicit_frequency_penalty(numeric) == (True, 0.0)
    with pytest.raises(ValueError, match="numeric or null"):
        _explicit_frequency_penalty(
            SimpleNamespace(extra={"frequency_penalty": "0"})
        )
    with pytest.raises(ValueError, match=r"\[-2, 2\]"):
        _explicit_frequency_penalty(
            SimpleNamespace(extra={"frequency_penalty": 2.1})
        )


class _State:
    def __init__(self, url: str, interacted=()):
        self.url = url
        self.interacted_element = list(interacted)

    def get_screenshot(self):
        return None


def _item(url: str, actions: list[dict] | None):
    output = None
    if actions is not None:
        output = SimpleNamespace(
            action=[_Action(action) for action in actions],
            current_state=SimpleNamespace(memory=f"memory for {url}"),
        )
    return SimpleNamespace(
        state=_State(url, [{"tag": "a"}] if actions else []),
        model_output=output,
    )


def test_history_to_steps_preserves_model_turn_boundaries():
    history = SimpleNamespace(
        history=[
            _item("http://example.test/one", [{"click": {"index": 1}}, {"wait": {"seconds": 1}}]),
            _item("http://example.test/two", [{"done": {"text": "ok"}}]),
            _item("http://example.test/failure", None),
        ]
    )

    steps, tool_actions = _history_to_steps(history)

    assert len(steps) == 3
    assert tool_actions == 3
    assert len(json.loads(steps[0].action)) == 2
    assert len(json.loads(steps[1].action)) == 1
    assert json.loads(steps[2].action) == []
    assert steps[0].url.endswith("/one")
    assert steps[1].reasoning


def test_whole_run_timeout_covers_and_cancels_pre_agent_work():
    entered_prepare = asyncio.Event()
    cancelled_prepare = asyncio.Event()

    async def browser_launch_navigation_and_prepare():
        # This represents work that occurs before Agent construction.  The
        # outer deadline must cover it just as it covers Agent.run.
        entered_prepare.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled_prepare.set()

    async def exercise():
        with pytest.raises(
            _WholeRunTimeoutError,
            match="whole run exceeded",
        ):
            await _await_whole_run(
                browser_launch_navigation_and_prepare(),
                0.01,
            )

    asyncio.run(exercise())

    assert entered_prepare.is_set()
    assert cancelled_prepare.is_set()


def test_whole_run_timeout_does_not_mislabel_an_inner_timeout():
    async def inner_operation():
        raise TimeoutError("inner browser timeout")

    async def exercise():
        with pytest.raises(TimeoutError, match="inner browser timeout") as exc:
            await _await_whole_run(inner_operation(), 1.0)
        assert type(exc.value) is TimeoutError

    asyncio.run(exercise())


def test_post_task_auxiliary_judge_contract_is_disabled_and_source_guarded():
    from browser_use import Agent, BrowserSession
    from scripts.hard_campaign_runtime import (
        POST_TASK_AUXILIARY_JUDGE_CONFIGURATION,
        _agent_behavior_limits,
        runtime_limit_contract,
    )

    configured = POST_TASK_AUXILIARY_JUDGE_CONFIGURATION
    assert configured == (
        browseruse_scaffold._POST_TASK_AUXILIARY_JUDGE_CONFIGURATION
    )
    assert configured["enabled"] is False
    assert configured["model_calls_after_agent_done"] == 0
    assert configured["new_cleanup_mechanism"] is False
    assert _agent_behavior_limits()["use_judge"] is False
    assert inspect.signature(Agent.__init__).parameters[
        "use_judge"
    ].default is True

    for implementation, key in (
        (Agent, "upstream_agent_service_sha256_guard"),
        (BrowserSession, "upstream_browser_session_sha256_guard"),
    ):
        source = Path(inspect.getsourcefile(implementation) or "")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == configured[key]

    record = runtime_limit_contract()["categories"][
        "fixed_architecture"
    ]["post_task_auxiliary_judge"]
    assert record["configured"] == configured
    assert record["applicability"] == ["baseline", "deliberative"]


def _audit_item(actions=(), results=(), state_message=""):
    return SimpleNamespace(
        model_output=SimpleNamespace(
            action=[_Action(action) for action in actions],
        ),
        result=list(results),
        state_message=state_message,
    )


def test_context_cap_audit_records_all_eight_caps_and_exact_boundaries():
    history = SimpleNamespace(
        history=[
            _audit_item(
                actions=[
                    {"evaluate": {"code": "return 1"}},
                    {
                        "extract": {
                            "query": "products",
                            "already_collected": [f"id-{i}" for i in range(101)],
                        }
                    },
                ],
                results=[
                    SimpleNamespace(
                        error=None,
                        extracted_content="v" * 9999,
                    ),
                    SimpleNamespace(
                        error=None,
                        extracted_content="x" * 10000,
                        metadata={
                            "extraction_result": {
                                "is_partial": True,
                                "content_stats": {
                                    "final_filtered_chars": 100001,
                                },
                            }
                        },
                    ),
                ],
                state_message=(
                    "Interactive elements (truncated to 40000 characters):"
                ),
            ),
            _audit_item(
                actions=[
                    {"evaluate": {"code": "return 2"}},
                    {
                        "extract": {
                            "query": "more products",
                            "already_collected": [f"id-{i}" for i in range(100)],
                        }
                    },
                ],
                results=[
                    SimpleNamespace(
                        error="e" * 20000,
                        extracted_content="v" * 10000,
                    ),
                    SimpleNamespace(
                        error="e" * 20001,
                        extracted_content="x" * 10001,
                        metadata={
                            "extraction_result": {
                                "is_partial": False,
                                "content_stats": {
                                    "final_filtered_chars": 100000,
                                },
                            }
                        },
                    ),
                ],
            ),
        ]
    )

    audit = _context_cap_audit(history)

    assert audit == {
        "schema_version": 1,
        "complete": True,
        "limits": {
            "action_error_chars": {
                "configured": 20000,
                "touched_count": 1,
                "max_observed": 20001,
            },
            "action_results_chars": {
                "configured": 60000,
                "touched_count": 0,
                "max_observed": 20007,
            },
            "evaluate_memory_chars": {
                "configured": 10000,
                "touched_count": 1,
                "max_observed": 10000,
            },
            "extract_already_collected_items": {
                "configured": 100,
                "touched_count": 1,
                "max_observed": 101,
            },
            "extract_memory_chars": {
                "configured": 10000,
                "touched_count": 2,
                "max_observed": 10001,
            },
            "extract_page_chunk_chars": {
                "configured": 100000,
                "touched_count": 1,
                "max_observed": 100001,
            },
            "max_clickable_elements_chars": {
                "configured": 40000,
                "touched_count": 1,
                "max_observed": 40001,
                "observation_kind": "lower_bound_if_touched",
                "max_observed_lower_bound": 40001,
            },
            "read_state_chars": {
                "configured": 60000,
                "touched_count": 0,
                "max_observed": 0,
            },
        },
        "history_items": 2,
    }


def test_context_cap_audit_is_complete_for_empty_available_history():
    audit = _context_cap_audit(SimpleNamespace(history=[]))

    assert audit["complete"] is True
    assert audit["history_items"] == 0
    assert "error" not in audit
    assert all(
        record["touched_count"] == 0 and record["max_observed"] == 0
        for record in audit["limits"].values()
    )


def test_pre_agent_context_cap_audit_is_explicitly_known_empty():
    audit = _context_cap_audit_before_agent_construction()

    assert audit["schema_version"] == 1
    assert audit["complete"] is True
    assert audit["history_state"] == "not_created"
    assert (
        audit["measurement_basis"]
        == "failure_before_agent_construction"
    )
    assert audit["history_items"] == 0
    assert "error" not in audit
    assert all(
        record["configured"] > 0
        and record["touched_count"] == 0
        and record["max_observed"] == 0
        for record in audit["limits"].values()
    )


def test_context_cap_audit_fails_closed_when_history_is_unavailable():
    audit = _context_cap_audit(None)

    assert audit["schema_version"] == 1
    assert audit["complete"] is False
    assert audit["error"] == "AgentHistory unavailable"
    assert audit["history_items"] == 0
    assert set(audit["limits"]) == {
        "action_error_chars",
        "action_results_chars",
        "evaluate_memory_chars",
        "extract_already_collected_items",
        "extract_memory_chars",
        "extract_page_chunk_chars",
        "max_clickable_elements_chars",
        "read_state_chars",
    }
    assert all(
        record["touched_count"] == 0 and record["max_observed"] == 0
        for record in audit["limits"].values()
    )


def test_action_error_prompt_cap_is_actually_lifted_and_idempotent():
    from browser_use.agent.message_manager.service import MessageManager

    implementation = MessageManager._update_agent_history_description
    original_code = implementation.__code__
    try:
        first = _lift_action_error_prompt_cap()
        second = _lift_action_error_prompt_cap()
        assert first == second == {
            "browser_use_version": "0.13.6",
            "source_sha256": (
                "54959a44f45adaae357d52d67d14146553a8a3176b928b70f2a1a8b1f2d75906"
            ),
            "upstream_chars": 200,
            "effective_chars": 20000,
            "effective_edge_chars": 10000,
        }

        def rendered_error(error: str) -> str:
            state = SimpleNamespace(
                read_state_description="",
                read_state_images=[],
                agent_history_items=[],
            )
            action_result = SimpleNamespace(
                include_extracted_content_only_once=False,
                extracted_content=None,
                images=[],
                long_term_memory=None,
                error=error,
            )
            implementation(
                SimpleNamespace(state=state),
                result=[action_result],
                step_info=SimpleNamespace(step_number=0),
            )
            assert len(state.agent_history_items) == 1
            return state.agent_history_items[0].action_results

        at_boundary = "x" * 20000
        assert rendered_error(at_boundary) == f"Result\n{at_boundary}"
        over_boundary = "a" * 10000 + "b" + "c" * 10000
        assert rendered_error(over_boundary) == (
            "Result\n" + "a" * 10000 + "......" + "c" * 10000
        )
    finally:
        implementation.__code__ = original_code


def _lossless_recovery_authorization(
    monkeypatch,
    *,
    attempt: int = 2,
    amendment_sha256: str = "a" * 64,
) -> dict:
    run_id = browseruse_scaffold._LOSSLESS_READ_STATE_RECOVERY_RUN_ID
    campaign_uuid = (
        browseruse_scaffold._LOSSLESS_READ_STATE_RECOVERY_CAMPAIGN_UUID
    )
    manifest_sha256 = (
        browseruse_scaffold._LOSSLESS_READ_STATE_RECOVERY_MANIFEST_SHA256
    )
    nonce = (
        f"eight_env_leaderboard/{campaign_uuid}/{manifest_sha256}/"
        f"{run_id}/attempt_{attempt}/continuation-{amendment_sha256}"
    )
    authorization = {
        "schema": (
            browseruse_scaffold._LOSSLESS_READ_STATE_RECOVERY_SCHEMA
        ),
        "mode": browseruse_scaffold._LOSSLESS_READ_STATE_RECOVERY_MODE,
        "campaign_uuid": campaign_uuid,
        "manifest_sha256": manifest_sha256,
        "amendment_sha256": amendment_sha256,
        "scheduler_sha256": "b" * 64,
        "run_index": 598,
        "run_id": run_id,
        "prior_attempt": 1,
        "attempt": attempt,
        "prior_completion_sha256": (
            browseruse_scaffold
            ._LOSSLESS_READ_STATE_RECOVERY_PRIOR_COMPLETION_SHA256
        ),
        "prior_trajectory_sha256": (
            browseruse_scaffold
            ._LOSSLESS_READ_STATE_RECOVERY_PRIOR_TRAJECTORY_SHA256
        ),
        "cache_nonce_sha256": hashlib.sha256(
            nonce.encode("utf-8")
        ).hexdigest(),
    }
    monkeypatch.setenv("AGENTARENA_CACHE_NONCE", nonce)
    monkeypatch.setenv(
        "AGENTARENA_LOSSLESS_READ_STATE_RECOVERY_JSON",
        json.dumps(authorization, sort_keys=True),
    )
    return authorization


def test_lossless_read_state_authorization_is_absent_or_exact(
    monkeypatch,
):
    monkeypatch.delenv(
        "AGENTARENA_LOSSLESS_READ_STATE_RECOVERY_JSON", raising=False
    )
    assert (
        _lossless_read_state_recovery_authorization_from_environment()
        is None
    )

    expected = _lossless_recovery_authorization(monkeypatch)
    assert (
        _lossless_read_state_recovery_authorization_from_environment()
        == expected
    )

    monkeypatch.setenv(
        "AGENTARENA_LOSSLESS_READ_STATE_RECOVERY_JSON",
        '{"schema":"one","schema":"two"}',
    )
    with pytest.raises(RuntimeError, match="malformed JSON"):
        _lossless_read_state_recovery_authorization_from_environment()

    _lossless_recovery_authorization(monkeypatch)
    monkeypatch.setenv("AGENTARENA_CACHE_NONCE", "copied-worker")
    with pytest.raises(RuntimeError, match="cache nonce is not bound"):
        _lossless_read_state_recovery_authorization_from_environment()


def test_lossless_read_state_wrapper_is_instance_only_and_preserves_neighbors(
    monkeypatch,
):
    from browser_use.agent.message_manager.service import MessageManager
    from browser_use.agent.views import ActionResult

    authorization = _lossless_recovery_authorization(monkeypatch)
    manager = object.__new__(MessageManager)
    manager.state = SimpleNamespace(
        read_state_description="before",
        read_state_images=[],
        agent_history_items=[],
    )
    sibling = object.__new__(MessageManager)
    sibling.state = SimpleNamespace(
        read_state_description="",
        read_state_images=[],
        agent_history_items=[],
    )
    implementation = MessageManager._update_agent_history_description
    original_code = implementation.__code__
    try:
        _lift_action_error_prompt_cap()
        collector = _LosslessReadStateRecoveryAudit(authorization)
        _install_lossless_read_state_recovery(
            SimpleNamespace(_message_manager=manager), collector
        )

        assert (
            manager.__dict__["_update_agent_history_description"]
            is not implementation
        )
        assert (
            sibling._update_agent_history_description.__func__
            is implementation
        )
        assert MessageManager._update_agent_history_description is implementation

        image = {"url": "data:image/png;base64,AA=="}
        result = ActionResult(
            extracted_content="r" * 60010,
            include_extracted_content_only_once=True,
            long_term_memory="a" * 60010,
            images=[image],
        )
        manager._update_agent_history_description(
            result=[result],
            step_info=SimpleNamespace(step_number=0),
        )

        raw = (
            f"<read_state_0>\n{result.extracted_content}\n"
            "</read_state_0>\n"
        )
        assert manager.state.read_state_description == raw.strip("\n")
        assert manager.state.read_state_images == result.images
        assert len(manager.state.agent_history_items) == 1
        history_item = manager.state.agent_history_items[0]
        assert type(history_item).__name__ == "HistoryItem"
        expected_action_results = "Result\n" + "a" * 60010
        assert history_item.action_results == (
            expected_action_results[:60000]
            + "\n... [Content truncated at 60k characters]"
        )

        snapshot = collector.snapshot()
        assert snapshot["complete"] is True
        assert snapshot["call_count"] == 1
        assert snapshot["raw_crossing_count"] == 1
        assert snapshot["restored_crossing_count"] == 1
        assert snapshot["effective_loss_touched_count"] == 0
        record = snapshot["records"][0]
        assert record["raw_chars"] == len(raw)
        assert record["raw_sha256"] == hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()
        assert record["upstream_sha256"] == (
            record["expected_upstream_sha256"]
        )
        assert record["effective_sha256"] == hashlib.sha256(
            raw.strip("\n").encode("utf-8")
        ).hexdigest()
        assert record["effective_loss"] is False
    finally:
        implementation.__code__ = original_code


@pytest.mark.parametrize(
    "guard",
    ("version", "file_source", "method_source", "code", "signature", "constants"),
)
def test_lossless_read_state_wrapper_guards_fail_closed(
    monkeypatch,
    guard: str,
):
    from browser_use.agent.message_manager.service import MessageManager

    authorization = _lossless_recovery_authorization(monkeypatch)
    manager = object.__new__(MessageManager)
    manager.state = SimpleNamespace(
        read_state_description="",
        read_state_images=[],
        agent_history_items=[],
    )
    implementation = MessageManager._update_agent_history_description
    original_code = implementation.__code__
    try:
        _lift_action_error_prompt_cap()
        if guard == "version":
            monkeypatch.setattr(
                browseruse_scaffold.importlib_metadata,
                "version",
                lambda _name: "changed",
            )
        elif guard == "file_source":
            monkeypatch.setattr(
                browseruse_scaffold,
                "_BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256",
                "0" * 64,
            )
        elif guard == "method_source":
            monkeypatch.setattr(
                browseruse_scaffold,
                "_BROWSER_USE_MESSAGE_MANAGER_METHOD_SOURCE_SHA256",
                "0" * 64,
            )
        elif guard == "code":
            monkeypatch.setattr(
                browseruse_scaffold,
                "_BROWSER_USE_MESSAGE_MANAGER_METHOD_CODE_SHA256",
                "0" * 64,
            )
        elif guard == "signature":
            monkeypatch.setattr(
                browseruse_scaffold,
                "_BROWSER_USE_MESSAGE_MANAGER_METHOD_SIGNATURE",
                "changed",
            )
        else:
            monkeypatch.setattr(
                browseruse_scaffold,
                "_EFFECTIVE_MESSAGE_MANAGER_METHOD_CONSTANTS",
                (*implementation.__code__.co_consts, "changed"),
            )
        collector = _LosslessReadStateRecoveryAudit(authorization)
        with pytest.raises(RuntimeError, match="guard|constants"):
            _install_lossless_read_state_recovery(
                SimpleNamespace(_message_manager=manager), collector
            )
        assert "_update_agent_history_description" not in manager.__dict__
    finally:
        implementation.__code__ = original_code


def _pydantic_validation_error(title: str):
    from pydantic import ValidationError, create_model

    model = create_model(title, required_value=(int, ...))
    try:
        model.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("fixture did not raise ValidationError")


def _explicit_cause(error: BaseException) -> RuntimeError:
    outer = RuntimeError("provider wrapper")
    outer.__cause__ = error
    return outer


def test_agent_output_validation_classifier_uses_only_exact_explicit_cause():
    agent_output = _pydantic_validation_error("AgentOutput")
    tool_output = _pydantic_validation_error("ClickActionModel")

    assert _is_agent_output_validation_error(agent_output) is True
    assert _is_agent_output_validation_error(
        _explicit_cause(agent_output)
    ) is True
    assert _is_agent_output_validation_error(tool_output) is False
    assert _is_agent_output_validation_error(
        _explicit_cause(tool_output)
    ) is False
    assert _is_agent_output_validation_error(
        RuntimeError(str(agent_output))
    ) is False

    try:
        raise agent_output
    except Exception:
        try:
            raise RuntimeError("context only")
        except RuntimeError as context_only:
            assert context_only.__cause__ is None
            assert context_only.__context__ is agent_output
            assert (
                _is_agent_output_validation_error(context_only) is False
            )


def test_action_error_provenance_wrapper_is_observability_only():
    from browser_use.agent.views import ActionResult

    class FakeAgent:
        def __init__(self):
            self.state = SimpleNamespace(last_result=[])
            self.calls = []

        async def _handle_step_error(self, error):
            self.calls.append(error)
            result = ActionResult(error=str(error))
            self.state.last_result = [result]
            return "upstream-return"

    agent = FakeAgent()
    original_handler = agent._handle_step_error
    collector = _ActionErrorProvenanceCollector()
    _install_action_error_provenance_audit(agent, collector)
    _install_action_error_provenance_audit(agent, collector)
    error = _explicit_cause(_pydantic_validation_error("AgentOutput"))

    returned = asyncio.run(agent._handle_step_error(error))
    result = agent.state.last_result[0]

    assert returned == "upstream-return"
    assert agent.calls == [error]
    assert result.error == str(error)
    assert result.model_dump(exclude_none=True) == {
        "is_done": False,
        "error": str(error),
        "include_extracted_content_only_once": False,
        "include_in_memory": False,
    }
    assert collector.is_agent_output_validation(result) is True
    assert inspect.unwrap(agent._handle_step_error) == original_handler

    audit = _action_error_audit(
        SimpleNamespace(history=[_audit_item(results=[result])]),
        collector,
    )
    assert audit == {
        "schema_version": 1,
        "complete": True,
        "error": None,
        "all": {"count": 1, "over_cap_count": 0, "max_chars": 16},
        "agent_output_validation": {
            "count": 1,
            "over_cap_count": 0,
            "max_chars": 16,
        },
        "other_or_unknown": {
            "count": 0,
            "over_cap_count": 0,
            "max_chars": 0,
        },
    }


def test_action_error_identity_label_is_byte_invisible_to_message_manager():
    from browser_use.agent.message_manager.service import MessageManager
    from browser_use.agent.views import ActionResult

    implementation = MessageManager._update_agent_history_description
    original_code = implementation.__code__
    marked = ActionResult(error="e" * 20001)
    unmarked = ActionResult(error="e" * 20001)
    collector = _ActionErrorProvenanceCollector()
    collector.mark_agent_output_validation(marked)

    def rendered(result):
        state = SimpleNamespace(
            read_state_description="",
            read_state_images=[],
            agent_history_items=[],
        )
        implementation(
            SimpleNamespace(state=state),
            result=[result],
            step_info=SimpleNamespace(step_number=0),
        )
        return state.agent_history_items[0].to_string().encode("utf-8")

    try:
        _lift_action_error_prompt_cap()
        assert collector.is_agent_output_validation(marked) is True
        assert collector.is_agent_output_validation(unmarked) is False
        assert rendered(marked) == rendered(unmarked)
    finally:
        implementation.__code__ = original_code


def test_action_error_audit_exact_boundary_mixed_and_spoof_fail_closed():
    known_at = SimpleNamespace(error="k" * 20000)
    known_over = SimpleNamespace(error="v" * 20001)
    unknown_over = SimpleNamespace(error="u" * 20003)
    spoof_over = SimpleNamespace(
        error=("94 validation errors for AgentOutput\n" + "s" * 20010)
    )
    collector = _ActionErrorProvenanceCollector()
    collector.mark_agent_output_validation(known_at)
    collector.mark_agent_output_validation(known_over)
    history = SimpleNamespace(history=[_audit_item(results=[
        known_at, known_over, unknown_over, spoof_over,
    ])])

    audit = _action_error_audit(history, collector)

    assert audit == {
        "schema_version": 1,
        "complete": True,
        "error": None,
        "all": {
            "count": 4,
            "over_cap_count": 3,
            "max_chars": len(spoof_over.error),
        },
        "agent_output_validation": {
            "count": 2,
            "over_cap_count": 1,
            "max_chars": 20001,
        },
        "other_or_unknown": {
            "count": 2,
            "over_cap_count": 2,
            "max_chars": len(spoof_over.error),
        },
    }
    raw = _context_cap_audit(history)
    assert raw["schema_version"] == 1
    assert raw["limits"]["action_error_chars"] == {
        "configured": 20000,
        "touched_count": 3,
        "max_observed": len(spoof_over.error),
    }


def test_action_error_audit_requires_exact_marked_result_identity():
    original = SimpleNamespace(error="v" * 20001)
    copied = SimpleNamespace(error=original.error)
    collector = _ActionErrorProvenanceCollector()
    collector.mark_agent_output_validation(original)

    audit = _action_error_audit(
        SimpleNamespace(history=[_audit_item(results=[copied])]),
        collector,
    )

    assert audit["complete"] is False
    assert "absent from AgentHistory" in audit["error"]
    assert audit["agent_output_validation"]["count"] == 0
    assert audit["other_or_unknown"] == {
        "count": 1,
        "over_cap_count": 1,
        "max_chars": 20001,
    }
    assert _action_error_audit_before_agent_construction() == {
        "schema_version": 1,
        "complete": True,
        "error": None,
        "all": {"count": 0, "over_cap_count": 0, "max_chars": 0},
        "agent_output_validation": {
            "count": 0, "over_cap_count": 0, "max_chars": 0,
        },
        "other_or_unknown": {
            "count": 0, "over_cap_count": 0, "max_chars": 0,
        },
    }


def test_evaluate_spill_preserves_inline_results_and_round_trips_large(
    tmp_path: Path,
):
    from browser_use import Tools
    from browser_use.agent.views import ActionResult

    tools = Tools()
    registration = tools.registry.registry.actions["evaluate"]
    upstream_wrapper = registration.function
    current_result = ActionResult()

    @wraps(upstream_wrapper)
    async def prepared_final_evaluate(**_kwargs):
        return current_result

    registration.function = prepared_final_evaluate
    store = _EvaluateResultStore(tmp_path / "evaluate_results")
    attestation = _install_evaluate_result_spill(tools, store)
    assert _lift_evaluate_serialization_backstop(tools) == attestation
    assert attestation == {
        "browser_use_version": "0.13.6",
        "source_sha256": (
            "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
        ),
        "upstream_chars": 20000,
        "inline_chars": 9999,
        "single_max_chars": 67108864,
        "memory_chars": 10000,
    }
    implementation = inspect.unwrap(registration.function)
    constants = implementation.__code__.co_consts
    assert constants.count(67108864) == 1
    assert constants.count(67108823) == 1
    assert constants.count(
        "\n... [Truncated after 67108864 characters]"
    ) == 1
    assert constants.count(10000) == 1
    assert 67108823 + len(
        "\n... [Truncated after 67108864 characters]"
    ) == 67108865

    async def returned(payload: str) -> tuple[ActionResult, bytes]:
        nonlocal current_result
        current_result = ActionResult(
            extracted_content=payload,
            long_term_memory="original memory",
            include_extracted_content_only_once=True,
            metadata={"original": True},
        )
        before = current_result.model_dump_json().encode("utf-8")
        observed = await registration.function()
        return observed, before

    exactly_inline = "i" * 9999
    observed, before = asyncio.run(returned(exactly_inline))
    assert observed is current_result
    assert observed.model_dump_json().encode("utf-8") == before

    marker_literal = (
        "ordinary value "
        "\n... [Truncated after 67108864 characters]"
    )
    observed, before = asyncio.run(returned(marker_literal))
    assert observed is current_result
    assert observed.model_dump_json().encode("utf-8") == before

    exactly_memory_boundary = "b" * 10000
    observed, _before = asyncio.run(returned(exactly_memory_boundary))
    boundary_receipt = json.loads(observed.extracted_content)
    assert observed is not current_result
    assert observed.long_term_memory == observed.extracted_content
    assert observed.include_extracted_content_only_once is False
    assert store.read_text(
        boundary_receipt["result_id"]
    ) == exactly_memory_boundary

    large = (
        "HEAD-SENTINEL|"
        + "x" * 19980
        + "|NEEDLE|"
        + "y" * 600
        + "|TAIL-SENTINEL"
    )
    observed, _before = asyncio.run(returned(large))
    assert observed is not current_result
    receipt = json.loads(observed.extracted_content)
    result_id = receipt["result_id"]
    assert len(observed.extracted_content) < 10000
    assert observed.long_term_memory == observed.extracted_content
    assert observed.include_extracted_content_only_once is False
    assert receipt["head_preview"].startswith("HEAD-SENTINEL|")
    assert receipt["tail_preview"].endswith("|TAIL-SENTINEL")
    assert receipt["head_preview_chars"] == 500
    assert receipt["tail_preview_chars"] == 500
    assert store.read_text(result_id) == large
    assert store.stat(result_id)["chars"] == len(large)
    assert str(tmp_path) not in observed.extracted_content

    inspector = tools.registry.registry.actions[
        "inspect_evaluate_result"
    ]
    assert inspector.terminates_sequence is True

    async def inspect_result(params: _InspectEvaluateResult):
        return await inspector.function(params=params)

    stat_result = asyncio.run(inspect_result(_InspectEvaluateResult(
        operation="stat",
        result_id=result_id,
    )))
    assert stat_result.include_extracted_content_only_once is True
    assert json.loads(stat_result.extracted_content)["result_id"] == result_id

    list_result = asyncio.run(inspect_result(_InspectEvaluateResult(
        operation="list",
    )))
    assert list_result.include_extracted_content_only_once is True
    assert json.loads(list_result.extracted_content)["records"][0][
        "result_id"
    ] == result_id

    search_result = asyncio.run(inspect_result(_InspectEvaluateResult(
        operation="search",
        result_id=result_id,
        needle="NEEDLE",
    )))
    assert search_result.include_extracted_content_only_once is True
    offsets = json.loads(search_result.extracted_content)["offsets"]
    assert offsets == [large.index("NEEDLE")]

    read_start = len(large) - 20
    read_result = asyncio.run(inspect_result(_InspectEvaluateResult(
        operation="read",
        result_id=result_id,
        start=read_start,
        length=20,
    )))
    header, chunk = read_result.extracted_content.split("\n", 1)
    assert json.loads(header)["sha256"] == receipt["sha256"]
    assert chunk == large[read_start:]
    assert read_result.include_extracted_content_only_once is True

    snapshot = store.snapshot()
    assert snapshot["responses"] == 2
    assert snapshot["records"] == 2
    assert snapshot["bytes"] == (
        len(exactly_memory_boundary.encode("utf-8"))
        + len(large.encode("utf-8"))
    )
    assert snapshot["single_bound_touched_count"] == 0
    assert snapshot["integrity_failure_count"] == 0
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert {
        stat.S_IMODE(path.stat().st_mode)
        for path in store.root.iterdir()
    } == {0o600}


def test_common_evaluate_transport_is_arm_identical_except_checkpoint(
    tmp_path: Path,
):
    from browser_use import Tools

    from .browseruse_deliberative import _DeliberativeExtension

    baseline = Tools()
    deliberative = Tools()
    extension = _DeliberativeExtension(SimpleNamespace(
        task=SimpleNamespace(instruction="Choose one item."),
        start_url="https://store.example/",
    ))
    extension.tools = deliberative
    extension._install_tools()

    _install_evaluate_result_spill(
        baseline,
        _EvaluateResultStore(tmp_path / "baseline_results"),
    )
    _install_evaluate_result_spill(
        deliberative,
        _EvaluateResultStore(tmp_path / "deliberative_results"),
    )

    baseline_registry = baseline.registry.registry.actions
    deliberative_registry = deliberative.registry.registry.actions
    assert (
        set(deliberative_registry) - set(baseline_registry)
    ) == {"decision_checkpoint"}
    assert set(baseline_registry) - set(deliberative_registry) == set()

    for action_name in ("evaluate", "inspect_evaluate_result"):
        baseline_action = baseline_registry[action_name]
        deliberative_action = deliberative_registry[action_name]
        assert (
            baseline_action.description,
            baseline_action.terminates_sequence,
            baseline_action.domains,
        ) == (
            deliberative_action.description,
            deliberative_action.terminates_sequence,
            deliberative_action.domains,
        )
        baseline_code = inspect.unwrap(baseline_action.function).__code__
        deliberative_code = inspect.unwrap(
            deliberative_action.function
        ).__code__
        assert baseline_code.co_code == deliberative_code.co_code
        assert baseline_code.co_consts == deliberative_code.co_consts


def test_evaluate_result_store_caps_and_path_safety(
    tmp_path: Path,
):
    byte_store = _EvaluateResultStore(
        tmp_path / "byte_store",
        max_bytes=4,
        max_responses=10,
    )
    with pytest.raises(
        _EvaluateResultStoreCapacityError,
        match="byte ceiling",
    ):
        byte_store.add("12345")
    assert byte_store.snapshot()["byte_bound_touched_count"] == 1

    response_store = _EvaluateResultStore(
        tmp_path / "response_store",
        max_bytes=100,
        max_responses=1,
    )
    response_store.add("first")
    with pytest.raises(
        _EvaluateResultStoreCapacityError,
        match="response ceiling",
    ):
        response_store.add("second")
    assert (
        response_store.snapshot()["response_bound_touched_count"] == 1
    )

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked_store"
    os.symlink(target, link)
    with pytest.raises(
        _EvaluateResultStoreIntegrityError,
        match="may not be a symlink",
    ):
        _EvaluateResultStore(link)

    secure_store = _EvaluateResultStore(tmp_path / "secure_store")
    record = secure_store.add("uncorrupted")
    data_path = (
        secure_store.root / f"{record['result_id']}.data"
    )
    outside = tmp_path / "outside"
    outside.write_text("uncorrupted")
    outside.chmod(0o600)
    data_path.unlink()
    os.symlink(outside, data_path)
    with pytest.raises(
        _EvaluateResultStoreIntegrityError,
        match="unsafe file type",
    ):
        secure_store.read_text(record["result_id"])


def test_evaluate_result_store_requires_fresh_root_and_never_imports_records(
    tmp_path: Path,
):
    root = tmp_path / "one_run_only"
    first_store = _EvaluateResultStore(root)
    record = first_store.add("prior-run result")

    with pytest.raises(
        _EvaluateResultStoreIntegrityError,
        match="root already exists",
    ):
        _EvaluateResultStore(root)

    assert first_store.read_text(record["result_id"]) == "prior-run result"
    assert first_store.snapshot()["records"] == 1


def test_evaluate_result_store_reuses_identical_payload_only_within_one_store(
    tmp_path: Path,
    monkeypatch,
):
    store = _EvaluateResultStore(tmp_path / "same_run")
    first = store.add("identical payload")
    data_path = store.root / f"{first['result_id']}.data"
    metadata_path = store.root / f"{first['result_id']}.json"
    before = {
        path.name: (path.stat().st_ino, path.read_bytes())
        for path in (data_path, metadata_path)
    }

    def unexpected_write(_path, _content):
        raise AssertionError("identical payload must not be republished")

    monkeypatch.setattr(store, "_atomic_write", unexpected_write)
    second = store.add("identical payload")

    assert second == first
    assert store.snapshot()["responses"] == 2
    assert store.snapshot()["records"] == 1
    assert {
        path.name: (path.stat().st_ino, path.read_bytes())
        for path in (data_path, metadata_path)
    } == before


def test_evaluate_result_store_atomic_publish_never_overwrites_race_winner(
    tmp_path: Path,
):
    store = _EvaluateResultStore(tmp_path / "race_store")
    destination = store.root / ("eval-" + "a" * 64 + ".data")
    destination.write_bytes(b"race winner")
    destination.chmod(0o600)
    before = (destination.stat().st_ino, destination.read_bytes())

    with pytest.raises(
        _EvaluateResultStoreIntegrityError,
        match="appeared concurrently",
    ):
        store._atomic_write(destination, b"losing write")

    assert (destination.stat().st_ino, destination.read_bytes()) == before
    assert not any(
        path.name.startswith(".pending-evaluate-")
        for path in store.root.iterdir()
    )


def test_evaluate_spill_audits_unexpected_storage_failure(
    tmp_path: Path,
    monkeypatch,
):
    from browser_use import Tools
    from browser_use.agent.views import ActionResult

    tools = Tools()
    registration = tools.registry.registry.actions["evaluate"]
    upstream_wrapper = registration.function

    @wraps(upstream_wrapper)
    async def prepared_final_evaluate(**_kwargs):
        return ActionResult(extracted_content="x" * 10000)

    registration.function = prepared_final_evaluate
    store = _EvaluateResultStore(tmp_path / "evaluate_results")
    _install_evaluate_result_spill(tools, store)

    def broken_add(_text):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(store, "add", broken_add)
    result = asyncio.run(registration.function())
    assert "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE" in result.error
    assert str(tmp_path) not in result.error
    assert store.snapshot()["integrity_failure_count"] == 1


def test_context_cap_audit_fails_closed_on_unreadable_action():
    history = SimpleNamespace(
        history=[
            SimpleNamespace(
                model_output=SimpleNamespace(action=[object()]),
                result=[],
                state_message="",
            )
        ]
    )

    audit = _context_cap_audit(history)

    assert audit["complete"] is False
    assert audit["history_items"] == 1
    assert audit["error"].startswith("AgentHistory audit failed: TypeError:")


def test_context_cap_audit_reconstructs_two_60k_prompt_channels():
    history = SimpleNamespace(
        history=[
            _audit_item(
                results=[
                    SimpleNamespace(
                        extracted_content="r" * 60000,
                        include_extracted_content_only_once=True,
                        long_term_memory="a" * 60000,
                        error=None,
                    )
                ]
            ),
            _audit_item(),
        ]
    )

    audit = _context_cap_audit(history)

    assert audit["complete"] is True
    assert audit["limits"]["read_state_chars"] == {
        "configured": 60000,
        "touched_count": 1,
        "max_observed": 60032,
    }
    assert audit["limits"]["action_results_chars"] == {
        "configured": 60000,
        "touched_count": 1,
        "max_observed": 60007,
    }


@pytest.mark.parametrize("complete_recovery", (True, False))
def test_read_state_finalizer_preserves_raw_and_subtracts_only_exact_recovery(
    tmp_path: Path,
    monkeypatch,
    complete_recovery: bool,
):
    from browser_use.agent.message_manager.service import MessageManager
    from browser_use.agent.views import ActionResult
    from scripts.hard_campaign_runtime import runtime_limit_contract

    authorization = _lossless_recovery_authorization(monkeypatch)
    manager = object.__new__(MessageManager)
    manager.state = SimpleNamespace(
        read_state_description="",
        read_state_images=[],
        agent_history_items=[],
    )
    implementation = MessageManager._update_agent_history_description
    original_code = implementation.__code__
    try:
        _lift_action_error_prompt_cap()
        collector = _LosslessReadStateRecoveryAudit(authorization)
        _install_lossless_read_state_recovery(
            SimpleNamespace(_message_manager=manager), collector
        )
        result = ActionResult(
            extracted_content="r" * 60010,
            include_extracted_content_only_once=True,
        )
        manager._update_agent_history_description(
            result=[result],
            step_info=SimpleNamespace(step_number=0),
        )
        history = SimpleNamespace(
            history=[_audit_item(results=[result]), _audit_item()]
        )
        raw = _context_cap_audit(history)
        assert raw["limits"]["read_state_chars"] == {
            "configured": 60000,
            "touched_count": 1,
            "max_observed": 60042,
        }
        recovery = collector.snapshot()
        if not complete_recovery:
            recovery["complete"] = False
            recovery["error"] = "test-incomplete"

        audit = _new_limit_audit(
            runtime_limit_contract(near_fraction=0.9), "baseline"
        )
        _finalize_limit_audit(
            audit,
            steps=[],
            elapsed_seconds=1.0,
            context_cap_audit=raw,
            history_error_text="",
            run_error=None,
            total_timeout_touched=False,
            evaluate_result_store=(
                _EvaluateResultStore(tmp_path / "evaluate").snapshot()
            ),
            extension_stats=None,
            agent=None,
            action_error_audit=_action_error_audit(
                history, _ActionErrorProvenanceCollector()
            ),
            replace_file_safety_audit=(
                _ReplaceFileSafetyCollector().snapshot()
            ),
            lossless_read_state_recovery_audit=recovery,
        )

        record = audit["categories"]["lossy_context_limits"][
            "read_state_chars"
        ]
        assert record["observations"]["touched_count"] == 1
        assert record["observations"]["max_observed"] == 60042
        assert record["observations"]["raw_touched_count"] == 1
        assert record["observations"]["raw_max_observed"] == 60042
        if complete_recovery:
            assert audit["complete"] is True
            assert record["touched_count"] == 0
            assert record["observations"]["recovery_complete"] is True
            assert record["observations"]["restored_touched_count"] == 1
            assert record["observations"]["effective_touched_count"] == 0
        else:
            assert audit["complete"] is False
            assert "recovery audit is incomplete" in audit["error"]
            assert record["touched_count"] == 1
            assert record["observations"]["recovery_complete"] is False
            assert record["observations"]["effective_touched_count"] == 1
    finally:
        implementation.__code__ = original_code


def test_replace_file_guard_stops_only_recursive_all_site_amplification(
    tmp_path: Path,
):
    from browser_use import Tools
    from browser_use.filesystem.file_system import FileSystem

    tools = Tools()
    collector = _ReplaceFileSafetyCollector()
    attestation = _install_replace_file_recursive_amplification_guard(
        tools, collector
    )
    file_system = FileSystem(tmp_path, create_default_files=False)
    asyncio.run(file_system.write_file("notes.md", "left TARGET right"))
    registered = tools.registry.registry.actions["replace_file"]

    def replace(old_str: str, new_str: str):
        return asyncio.run(registered.function(
            params=registered.param_model(
                file_name="notes.md",
                old_str=old_str,
                new_str=new_str,
            ),
            file_system=file_system,
        ))

    # A single current site passes through even when replacement contains two
    # sites.  This preserves ordinary upstream behavior and creates the exact
    # state from which repeating the action would amplify geometrically.
    first = replace("TARGET", "TARGET and TARGET")
    expanded = "left TARGET and TARGET right"
    assert first.error is None
    assert file_system.display_file("notes.md") == expanded
    assert (file_system.get_dir() / "notes.md").read_text() == expanded

    # Repeating it has multiple current sites and multiple replacement sites,
    # so the recursive-amplification predicate rejects before mutation.
    second = replace("TARGET", "TARGET and TARGET")
    assert second.error == (
        "replace_file blocked recursive amplification: old_str matches 2 "
        "locations in notes.md and occurs 2 times inside new_str; replacing "
        "every site would multiply the same search text across all sites. "
        "The file was not changed. Use more surrounding context in old_str "
        "to identify one target, or use replacement text that does not "
        "repeat old_str."
    )
    assert second.metadata == {
        "agentarena_replace_file_guard": {
            "trigger_index": 1,
            "reason": "recursive_all_sites_amplification",
            "resolved_file": "notes.md",
            "current_chars": len(expanded),
            "old_str_chars": len("TARGET"),
            "current_non_overlapping_matches": 2,
            "replacement_non_overlapping_matches": 2,
            "file_unchanged": True,
            "agent_result": "error",
        }
    }
    assert file_system.display_file("notes.md") == expanded
    assert (file_system.get_dir() / "notes.md").read_text() == expanded

    # A more specific old_str remains the general recovery path.
    recovered = replace("TARGET and TARGET", "complete")
    assert recovered.error is None
    assert file_system.display_file("notes.md") == "left complete right"

    snapshot = collector.snapshot()
    assert snapshot["invocations"] == 3
    assert snapshot["checked_invocations"] == 3
    assert snapshot["passed_invocations"] == 2
    assert snapshot["rejected_invocations"] == 1
    assert snapshot["errors"] == []
    assert attestation["trigger_predicate"] == (
        "content.count(old_str) > 1 and new_str.count(old_str) > 1"
    )
    assert attestation["silent_truncation"] is False


def test_replace_file_guard_preserves_ordinary_upstream_semantics(
    tmp_path: Path,
):
    from browser_use import Tools
    from browser_use.filesystem.file_system import FileSystem

    tools = Tools()
    collector = _ReplaceFileSafetyCollector()
    _install_replace_file_recursive_amplification_guard(tools, collector)
    file_system = FileSystem(tmp_path, create_default_files=False)
    registered = tools.registry.registry.actions["replace_file"]

    def replace(old_str: str, new_str: str):
        return asyncio.run(registered.function(
            params=registered.param_model(
                file_name="ordinary.md",
                old_str=old_str,
                new_str=new_str,
            ),
            file_system=file_system,
        ))

    initial = "café TOKEN 🙂 TOKEN 漢字"
    asyncio.run(file_system.write_file("ordinary.md", initial))

    # Zero matches keeps Browser Use's original success/no-op behavior.
    zero = replace("absent", "replacement")
    assert zero.error is None
    assert file_system.display_file("ordinary.md") == initial

    # Multiple sites with an ordinary replacement remain all-site replace.
    multi = replace("TOKEN", "done")
    assert multi.error is None
    assert file_system.display_file("ordinary.md") == (
        "café done 🙂 done 漢字"
    )

    # Multiple sites with one self-copy are linear, not amplifying, and pass.
    asyncio.run(file_system.write_file("ordinary.md", initial))
    self_once = replace("TOKEN", "prefix TOKEN suffix")
    assert self_once.error is None
    assert file_system.display_file("ordinary.md") == (
        "café prefix TOKEN suffix 🙂 prefix TOKEN suffix 漢字"
    )

    snapshot = collector.snapshot()
    assert snapshot["invocations"] == 3
    assert snapshot["passed_invocations"] == 3
    assert snapshot["rejected_invocations"] == 0
    assert snapshot["max_current_matches"] == 2
    assert snapshot["max_replacement_matches"] == 1
    assert snapshot["trigger_records"] == []


def test_replace_file_guard_finalizes_as_exact_fixed_architecture(
    tmp_path: Path,
):
    from scripts.hard_campaign_runtime import (
        runtime_limit_contract,
        validate_limit_audit,
    )

    collector = _ReplaceFileSafetyCollector()
    collector.installed()
    collector.invoked()
    trigger = collector.checked(
        resolved_file="scratch.md",
        current_chars=31,
        old_str_chars=4,
        current_matches=3,
        replacement_matches=2,
    )
    assert trigger is not None
    snapshot = collector.snapshot()
    contract = runtime_limit_contract(near_fraction=0.9)
    audit = _new_limit_audit(contract, "baseline")
    _finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=_context_cap_audit(
            SimpleNamespace(history=[])
        ),
        action_error_audit=_action_error_audit(
            SimpleNamespace(history=[]),
            _ActionErrorProvenanceCollector(),
        ),
        replace_file_safety_audit=snapshot,
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=_EvaluateResultStore(
            tmp_path / "evaluate-replace-guard"
        ).snapshot(),
        extension_stats=None,
        agent=None,
    )

    assert audit["complete"] is True
    assert validate_limit_audit(audit, contract, "baseline") == []
    assert all(
        record["touched_count"] == 0
        for record in audit["categories"][
            "lossy_context_limits"
        ].values()
    )
    fixed = audit["categories"]["fixed_architecture"][
        "replace_file_recursive_amplification_guard"
    ]
    assert fixed["touched_count"] == 1
    assert fixed["observations"]["audit_complete"] is True
    assert fixed["observations"]["trigger_records"] == [trigger]
    assert fixed["observations"]["rejected_invocations"] == 1


@pytest.mark.parametrize(
    ("target_chars", "externalized"),
    ((9999, False), (10000, True), (10119, True)),
)
def test_extract_file_externalization_round_trips_exactly(
    tmp_path: Path,
    monkeypatch,
    target_chars: int,
    externalized: bool,
):
    from browser_use import Tools
    from browser_use.agent.message_manager.service import MessageManager
    from browser_use.dom import markdown_extractor
    from browser_use.filesystem.file_system import FileSystem

    tools = Tools()
    extract = tools.registry.registry.actions["extract"]
    read_file = tools.registry.registry.actions["read_file"]
    file_system = FileSystem(tmp_path, create_default_files=False)

    async def clean_markdown(**_kwargs):
        return "page", {
            "original_html_chars": 4,
            "initial_markdown_chars": 4,
            "final_filtered_chars": 4,
            "filtered_chars_removed": 0,
        }

    monkeypatch.setattr(
        markdown_extractor,
        "extract_clean_markdown",
        clean_markdown,
    )

    class Browser:
        @staticmethod
        async def get_current_page_url():
            return "https://example.test"

    class ExtractionLLM:
        def __init__(self, completion: str):
            self.completion = completion

        async def ainvoke(self, *_args, **_kwargs):
            return SimpleNamespace(completion=self.completion)

    empty_result = (
        "<url>\nhttps://example.test\n</url>\n<query>\nq\n</query>\n"
        "<result>\n\n</result>"
    )
    unicode_sentinel = "\nTAIL-é🙂漢字\n" if target_chars == 10119 else ""
    completion = (
        "x" * (target_chars - len(empty_result) - len(unicode_sentinel))
        + unicode_sentinel
    )
    expected = empty_result.replace(
        "\n</result>", completion + "\n</result>"
    )
    assert len(expected) == target_chars

    params = extract.param_model(
        query="q",
        extract_links=False,
        extract_images=False,
        start_from_char=0,
        already_collected=[],
    )
    result = asyncio.run(extract.function(
        params=params,
        browser_session=Browser(),
        page_extraction_llm=ExtractionLLM(completion),
        file_system=file_system,
    ))

    assert result.extracted_content == expected
    assert result.include_extracted_content_only_once is externalized
    if not externalized:
        assert result.long_term_memory == expected
        assert file_system.list_files() == []
        return

    filename = "extracted_content_0.md"
    assert result.long_term_memory == (
        "Query: q\nContent in extracted_content_0.md and once in "
        "<read_state>."
    )
    assert file_system.list_files() == [filename]
    assert filename in file_system.describe()
    assert file_system.display_file(filename) == expected
    assert (file_system.get_dir() / filename).read_bytes() == (
        expected.encode("utf-8")
    )

    read_result = asyncio.run(read_file.function(
        params=read_file.param_model(file_name=filename),
        available_file_paths=[],
        file_system=file_system,
    ))
    read_envelope = (
        f"Read from file {filename}.\n<content>\n{expected}\n</content>"
    )
    assert read_result.extracted_content == read_envelope
    assert read_result.include_extracted_content_only_once is True

    state = SimpleNamespace(
        read_state_description="",
        read_state_images=[],
        agent_history_items=[],
    )
    MessageManager._update_agent_history_description(
        SimpleNamespace(state=state),
        result=[result],
        step_info=SimpleNamespace(step_number=0),
    )
    assert state.read_state_description == (
        f"<read_state_0>\n{expected}\n</read_state_0>"
    )
    assert filename in state.agent_history_items[0].action_results


def test_extract_externalization_finalizes_as_fixed_architecture(
    tmp_path: Path,
):
    from scripts.hard_campaign_runtime import runtime_limit_contract

    contract = runtime_limit_contract(near_fraction=0.9)
    audit = _new_limit_audit(contract, "baseline")
    collector = _ContextCapToolCollector()
    collector.observe("extract_memory_chars", 10119)
    context_audit = _context_cap_audit(
        SimpleNamespace(history=[]),
        tool_collector=collector,
    )
    store = _EvaluateResultStore(tmp_path / "evaluate").snapshot()

    _finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=context_audit,
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=store,
        extension_stats=None,
        agent=None,
        action_error_audit=_action_error_audit(
            SimpleNamespace(history=[]),
            _ActionErrorProvenanceCollector(),
        ),
        replace_file_safety_audit=(
            _ReplaceFileSafetyCollector().snapshot()
        ),
    )

    lossy = audit["categories"]["lossy_context_limits"]
    assert "extract_memory_chars" not in lossy
    assert all(record["touched_count"] == 0 for record in lossy.values())
    fixed = audit["categories"]["fixed_architecture"][
        "extract_result_file_externalization"
    ]
    assert fixed["touched_count"] == 1
    assert fixed["observations"] == {
        "raw_context_audit_name": "extract_memory_chars",
        "externalized_results": 1,
        "max_result_chars": 10119,
        "threshold_chars": 10000,
    }


def test_action_error_finalizer_splits_only_for_new_fixed_contract(
    tmp_path: Path,
):
    from scripts.hard_campaign_runtime import runtime_limit_contract

    known = SimpleNamespace(error="v" * 20001)
    unknown = SimpleNamespace(error="u" * 20003)
    history = SimpleNamespace(
        history=[_audit_item(results=[known, unknown])]
    )
    collector = _ActionErrorProvenanceCollector()
    collector.mark_agent_output_validation(known)
    provenance = _action_error_audit(history, collector)
    raw = _context_cap_audit(history)
    store = _EvaluateResultStore(tmp_path / "evaluate-split").snapshot()

    contract = runtime_limit_contract(near_fraction=0.9)
    audit = _new_limit_audit(contract, "baseline")
    _finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=raw,
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=store,
        extension_stats=None,
        agent=None,
        action_error_audit=provenance,
        replace_file_safety_audit=(
            _ReplaceFileSafetyCollector().snapshot()
        ),
    )

    assert audit["complete"] is True
    lossy = audit["categories"]["lossy_context_limits"][
        "action_error_chars"
    ]
    assert lossy["touched_count"] == 1
    assert lossy["observations"] == {
        "classification_complete": True,
        "raw_touched_count": 2,
        "raw_max_observed": 20003,
        "other_or_unknown_count": 1,
        "other_or_unknown_touched_count": 1,
        "max_observed": 20003,
        "classified_agent_output_validation_count": 1,
        "classified_agent_output_validation_touched_count": 1,
        "classified_agent_output_validation_max_observed": 20001,
    }
    fixed = audit["categories"]["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]
    assert fixed["configured"] == (
        browseruse_scaffold
        ._AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION
    )
    assert fixed["touched_count"] == 1
    assert fixed["observations"] == {
        "classification_complete": True,
        "raw_context_audit_name": "action_error_chars",
        "all_error_count": 2,
        "all_over_cap_count": 2,
        "all_max_chars": 20003,
        "agent_output_validation_count": 1,
        "agent_output_validation_over_cap_count": 1,
        "agent_output_validation_max_chars": 20001,
        "other_or_unknown_count": 1,
        "other_or_unknown_over_cap_count": 1,
        "other_or_unknown_max_chars": 20003,
    }

    legacy_contract = json.loads(json.dumps(contract))
    del legacy_contract["categories"]["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]
    legacy = _new_limit_audit(legacy_contract, "baseline")
    _finalize_limit_audit(
        legacy,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=raw,
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=store,
        extension_stats=None,
        agent=None,
        action_error_audit=provenance,
        replace_file_safety_audit=(
            _ReplaceFileSafetyCollector().snapshot()
        ),
    )
    assert legacy["complete"] is True
    assert legacy["categories"]["lossy_context_limits"][
        "action_error_chars"
    ] == {
        "configured": 20000,
        "touched_count": 2,
        "observations": {
            "touched_count": 2,
            "max_observed": 20003,
        },
    }


def test_limit_audit_records_effective_auxiliary_judge_setting(
    tmp_path: Path,
):
    from scripts.hard_campaign_runtime import runtime_limit_contract

    history = SimpleNamespace(history=[])
    audit = _new_limit_audit(runtime_limit_contract(), "baseline")
    agent = SimpleNamespace(
        settings=SimpleNamespace(use_judge=False),
        is_using_fallback_llm=False,
        _message_manager=None,
    )
    _finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=_context_cap_audit(history),
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=_EvaluateResultStore(
            tmp_path / "evaluate-judge-disabled"
        ).snapshot(),
        extension_stats=None,
        agent=agent,
        action_error_audit=_action_error_audit(
            history,
            _ActionErrorProvenanceCollector(),
        ),
        replace_file_safety_audit=(
            _ReplaceFileSafetyCollector().snapshot()
        ),
    )

    assert audit["complete"] is True
    judge = audit["categories"]["fixed_architecture"][
        "post_task_auxiliary_judge"
    ]
    assert judge["touched_count"] == 0
    assert judge["observations"] == {
        "agent_constructed": True,
        "effective_use_judge": False,
        "authoritative_evaluator": (
            "agentarena.core.experiment.run_cell:env.evaluate"
        ),
    }


def test_action_error_finalizer_rejects_broken_cross_binding(
    tmp_path: Path,
):
    from scripts.hard_campaign_runtime import runtime_limit_contract

    result = SimpleNamespace(error="v" * 20001)
    history = SimpleNamespace(history=[_audit_item(results=[result])])
    collector = _ActionErrorProvenanceCollector()
    collector.mark_agent_output_validation(result)
    provenance = _action_error_audit(history, collector)
    provenance["all"]["max_chars"] = 20002
    audit = _new_limit_audit(
        runtime_limit_contract(near_fraction=0.9), "baseline"
    )

    _finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=_context_cap_audit(history),
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=_EvaluateResultStore(
            tmp_path / "evaluate-broken"
        ).snapshot(),
        extension_stats=None,
        agent=None,
        action_error_audit=provenance,
        replace_file_safety_audit=(
            _ReplaceFileSafetyCollector().snapshot()
        ),
    )

    assert audit["complete"] is False
    assert "maxima do not partition" in audit["error"]
    assert audit["categories"]["lossy_context_limits"][
        "action_error_chars"
    ]["touched_count"] == 1
    assert audit["categories"]["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]["touched_count"] == 0


def test_context_cap_tool_wrapper_audits_only_returned_evaluate_receipt():
    evaluate_result = SimpleNamespace(
        extracted_content=(
            "x" * 19950
            + "\n... [Truncated after 20000 characters]"
        )
    )

    async def evaluate(**_kwargs):
        return evaluate_result

    async def extract(**_kwargs):
        return SimpleNamespace(extracted_content=None)

    registry = {
        "evaluate": SimpleNamespace(function=evaluate),
        "extract": SimpleNamespace(function=extract),
    }
    tools = SimpleNamespace(
        registry=SimpleNamespace(
            registry=SimpleNamespace(actions=registry),
        )
    )
    collector = _ContextCapToolCollector()

    _install_context_cap_tool_audit(tools, collector)
    returned = asyncio.run(registry["evaluate"].function(params={}))

    assert returned is evaluate_result
    assert collector.errors == []
    assert collector.observations["evaluate_memory_chars"] == [
        (19989, True, False)
    ]


def test_context_cap_tool_wrapper_captures_extract_input_chunk_and_memory():
    from browser_use.dom import markdown_extractor

    real_clean = markdown_extractor.extract_clean_markdown
    real_chunk = markdown_extractor.chunk_markdown_by_structure

    async def clean_markdown(**_kwargs):
        return "p" * 100001, {"final_filtered_chars": 100001}

    def chunk_markdown(
        _content,
        *,
        max_chunk_chars,
        start_from_char,
    ):
        assert max_chunk_chars == 100000
        assert start_from_char == 0
        return [SimpleNamespace(has_more=True)]

    async def extract(**kwargs):
        content, _stats = await markdown_extractor.extract_clean_markdown(
            browser_session=kwargs["browser_session"],
            extract_links=False,
            extract_images=False,
        )
        assert len(content) == 100001
        return SimpleNamespace(extracted_content="m" * 10000)

    async def evaluate(**_kwargs):
        return SimpleNamespace(extracted_content=None)

    registry = {
        "evaluate": SimpleNamespace(function=evaluate),
        "extract": SimpleNamespace(function=extract),
    }
    tools = SimpleNamespace(
        registry=SimpleNamespace(
            registry=SimpleNamespace(actions=registry),
        )
    )
    collector = _ContextCapToolCollector()
    markdown_extractor.extract_clean_markdown = clean_markdown
    markdown_extractor.chunk_markdown_by_structure = chunk_markdown
    try:
        _install_context_cap_tool_audit(tools, collector)
        asyncio.run(
            registry["extract"].function(
                params={
                    "start_from_char": 0,
                    "already_collected": [str(i) for i in range(101)],
                },
                browser_session=object(),
            )
        )
    finally:
        markdown_extractor.extract_clean_markdown = real_clean
        markdown_extractor.chunk_markdown_by_structure = real_chunk

    assert collector.errors == []
    assert collector.observations["extract_already_collected_items"] == [
        (101, True, False)
    ]
    assert collector.observations["extract_page_chunk_chars"] == [
        (100001, True, False)
    ]
    assert collector.observations["extract_memory_chars"] == [
        (10000, True, False)
    ]


def test_run_certifies_zero_context_cap_use_when_prepare_fails_pre_agent(
    tmp_path: Path,
    monkeypatch,
):
    constructed = []
    llm_construction = []

    class FakeAgent:
        def __init__(self, **_kwargs):
            constructed.append(True)

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            llm_construction.append(kwargs)

    class FakeBrowserProfile:
        def __init__(self, **_kwargs):
            pass

    class FakeBrowserSession:
        def __init__(self, *, browser_profile):
            self.browser_profile = browser_profile
            self.killed = False

        async def start(self):
            return None

        async def navigate_to(self, _url):
            return None

        async def kill(self):
            self.killed = True

    class BrokenExtension:
        async def prepare(self, **_kwargs):
            raise RuntimeError("contract compiler semantic verification failed")

        def stats_snapshot(self):
            return {"contract_compile_passes": 1}

    browser_config = SimpleNamespace(
        executable="/fake/chromium",
        headless=True,
        args=[],
        lib_path=None,
        width=1280,
        height=720,
        child_env=lambda: {},
    )
    endpoint = SimpleNamespace(
        model="fake-model",
        base_url="http://fake.invalid",
        api_key="fake",
        reasoning=False,
        reasoning_effort=None,
    )
    model = SimpleNamespace(
        openai_endpoint=lambda: endpoint,
        provider="phyagi",
        deployment="fake-model",
        name="fake-model",
        vision=False,
        extra={"frequency_penalty": None},
        has_vision=False,
    )
    ctx = SimpleNamespace(
        model=model,
        headless=True,
        work_dir=tmp_path,
        start_url="http://storefront.invalid",
        task=SimpleNamespace(instruction="Buy the lightest item."),
        max_steps=100,
    )

    monkeypatch.setattr(browser_use, "Agent", FakeAgent)
    monkeypatch.setattr(browser_use, "BrowserProfile", FakeBrowserProfile)
    monkeypatch.setattr(browser_use, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(browser_use, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(browser_use, "Tools", object)
    monkeypatch.setattr(
        browseruse_scaffold,
        "_patch_fence_tolerance",
        lambda _chat_openai: None,
    )
    monkeypatch.setattr(
        browseruse_scaffold,
        "_tools_with_lifted_extract_timeout",
        lambda _tools: SimpleNamespace(),
    )
    monkeypatch.setattr(
        browseruse_scaffold.BrowserConfig,
        "from_env",
        classmethod(lambda _cls, *, headless: browser_config),
    )

    trajectory = asyncio.run(_run(ctx, extension=BrokenExtension()))

    assert constructed == []
    assert len(llm_construction) == 1
    assert llm_construction[0]["frequency_penalty"] is None
    assert "reasoning_models" not in llm_construction[0]
    assert trajectory.steps == []
    assert trajectory.stats["decision_steps"] == 0
    assert trajectory.stats["tool_actions"] == 0
    assert trajectory.stats["error"].startswith("RuntimeError: contract compiler")
    audit = trajectory.stats["context_cap_audit"]
    assert audit["complete"] is True
    assert audit["history_state"] == "not_created"
    assert (
        audit["measurement_basis"]
        == "failure_before_agent_construction"
    )
    assert audit["history_items"] == 0
    assert all(
        record["touched_count"] == 0 and record["max_observed"] == 0
        for record in audit["limits"].values()
    )


def test_run_installs_common_spill_after_prepare_before_context_audit(
    tmp_path: Path,
    monkeypatch,
):
    events = []
    observed_agent_kwargs = {}
    initial_tools = SimpleNamespace(label="initial")
    prepared_tools = SimpleNamespace(label="prepared")

    class FakeHistory:
        history = []

        @staticmethod
        def final_result():
            return "done"

    class FakeAgent:
        def __init__(self, **kwargs):
            observed_agent_kwargs.update(kwargs)
            events.append(("agent", kwargs["tools"]))
            self.history = FakeHistory()
            self.is_using_fallback_llm = False
            self._message_manager = None
            self.settings = SimpleNamespace(
                use_judge=kwargs.get("use_judge")
            )

        async def run(self, *, max_steps):
            events.append(("run", max_steps))
            if self.settings.use_judge:
                events.append(("judge", "unexpected"))

    class FakeChatOpenAI:
        def __init__(self, **_kwargs):
            pass

    class FakeBrowserProfile:
        def __init__(self, **_kwargs):
            pass

    class FakeBrowserSession:
        def __init__(self, *, browser_profile):
            self.browser_profile = browser_profile

        async def start(self):
            return None

        async def navigate_to(self, _url):
            return None

        async def kill(self):
            return None

    class Extension:
        async def prepare(self, **kwargs):
            events.append(("prepare", kwargs["tools"]))
            # Even an extension kwarg cannot silently restore the upstream
            # default; the environment evaluator remains authoritative.
            return kwargs["task_text"], prepared_tools, {"use_judge": True}

        @staticmethod
        def stats_snapshot():
            return {}

    endpoint = SimpleNamespace(
        model="fake-model",
        base_url="http://fake.invalid",
        api_key="fake",
        reasoning=False,
        reasoning_effort=None,
    )
    model = SimpleNamespace(
        openai_endpoint=lambda: endpoint,
        provider="phyagi",
        deployment="fake-model",
        name="fake-model",
        vision=False,
        extra={},
        has_vision=False,
    )
    ctx = SimpleNamespace(
        model=model,
        headless=True,
        work_dir=tmp_path,
        start_url="http://storefront.invalid",
        task=SimpleNamespace(instruction="Buy one item."),
        max_steps=123,
    )
    browser_config = SimpleNamespace(
        executable="/fake/chromium",
        headless=True,
        args=[],
        lib_path=None,
        width=1280,
        height=720,
        child_env=lambda: {},
    )

    monkeypatch.setattr(browser_use, "Agent", FakeAgent)
    monkeypatch.setattr(browser_use, "BrowserProfile", FakeBrowserProfile)
    monkeypatch.setattr(browser_use, "BrowserSession", FakeBrowserSession)
    monkeypatch.setattr(browser_use, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(browser_use, "Tools", object)
    monkeypatch.setattr(
        browseruse_scaffold,
        "_patch_fence_tolerance",
        lambda _chat_openai: None,
    )

    def tools_with_lift(_tools):
        events.append(("tools", initial_tools))
        return initial_tools

    def install_spill(tools, store):
        assert isinstance(store, _EvaluateResultStore)
        events.append(("spill", tools))

    def install_replace_guard(tools, collector):
        collector.installed()
        events.append(("replace_guard", tools))

    def install_context(tools, _collector):
        events.append(("context", tools))

    monkeypatch.setattr(
        browseruse_scaffold,
        "_tools_with_lifted_extract_timeout",
        tools_with_lift,
    )
    monkeypatch.setattr(
        browseruse_scaffold,
        "_install_evaluate_result_spill",
        install_spill,
    )
    monkeypatch.setattr(
        browseruse_scaffold,
        "_install_replace_file_recursive_amplification_guard",
        install_replace_guard,
    )
    monkeypatch.setattr(
        browseruse_scaffold,
        "_install_context_cap_tool_audit",
        install_context,
    )
    monkeypatch.setattr(
        browseruse_scaffold,
        "_lift_action_error_prompt_cap",
        lambda: {},
    )
    monkeypatch.setattr(
        browseruse_scaffold.BrowserConfig,
        "from_env",
        classmethod(lambda _cls, *, headless: browser_config),
    )

    trajectory = asyncio.run(_run(ctx, extension=Extension()))

    assert events == [
        ("tools", initial_tools),
        ("prepare", initial_tools),
        ("replace_guard", prepared_tools),
        ("spill", prepared_tools),
        ("context", prepared_tools),
        ("agent", prepared_tools),
        ("run", 123),
    ]
    assert trajectory.answer == "done"
    assert observed_agent_kwargs["use_judge"] is False
    assert trajectory.stats["evaluate_result_store"]["responses"] == 0
