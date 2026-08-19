"""Frozen runtime and source-inventory helpers for the CAVEAT truthful-hard campaign."""
from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from caveat.core.environment import (
    ENVIRONMENT_SERVER_EXITED,
    ENVIRONMENT_STARTUP_TIMEOUT,
    ENVIRONMENT_STARTUP_TIMEOUT_SECONDS,
)


ROOT = Path(__file__).resolve().parents[1]

# Complete browser-use 0.13.6 event timeout surface used by the campaign.
EVENT_TIMEOUT_NAMES = (
    "AgentEventBusStop",
    "AgentFocusChangedEvent",
    "BrowserConnectedEvent",
    "BrowserErrorEvent",
    "BrowserKillEvent",
    "BrowserLaunchEvent",
    "BrowserReconnectedEvent",
    "BrowserReconnectingEvent",
    "BrowserSessionEventBusStopOnAgentClose",
    "BrowserStartEvent",
    "BrowserStateRequestEvent",
    "BrowserStopEvent",
    "BrowserStoppedEvent",
    "CaptchaSolverFinishedEvent",
    "CaptchaSolverStartedEvent",
    "CaptchaSolverWait",
    "ClickCoordinateEvent",
    "ClickElementEvent",
    "CloseTabEvent",
    "DownloadProgressEvent",
    "DownloadStartedEvent",
    "FileDownloadedEvent",
    "GetDropdownOptionsEvent",
    "GoBackEvent",
    "GoForwardEvent",
    "LoadStorageStateEvent",
    "NavigateToUrlEvent",
    "NavigationCompleteEvent",
    "NavigationStartedEvent",
    "RefreshEvent",
    "SaveStorageStateEvent",
    "ScreenshotEvent",
    "ScrollEvent",
    "ScrollToTextEvent",
    "SelectDropdownOptionEvent",
    "SendKeysEvent",
    "StorageStateLoadedEvent",
    "StorageStateSavedEvent",
    "SwitchTabEvent",
    "TabClosedEvent",
    "TabCreatedEvent",
    "TargetCrashedEvent",
    "TypeTextEvent",
    "UploadFileEvent",
    "WaitEvent",
)

CAPS = {
    "max_steps": 12000,
    "cell_timeout_seconds": 172800,
    "llm_timeout_seconds": 7200,
    "llm_http_timeout_seconds": 7260,
    "step_timeout_seconds": 7500,
    "extract_llm_timeout_seconds": 7500,
    "cdp_request_timeout_seconds": 7500,
    "browser_action_timeout_seconds": 7500,
    "max_consecutive_failures": 1000,
    "max_completion_tokens": "off",
    "llm_sdk_max_retries": 16,
    "evaluate_result_single_chars": 64 * 1024**2,
    "evaluate_result_store_bytes": 8 * 1024**3,
    "evaluate_result_store_responses": 200_000,
    "event_timeouts_seconds": {
        name: 600 for name in EVENT_TIMEOUT_NAMES
    },
    "spawn_stagger_seconds": 10,
    "max_concurrent_browsers": 15,
}

EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION = {
    "inline_chars_max": 9999,
    "externalize_at_chars": 10000,
    "storage_operation": "FileSystem.save_extracted_content",
    "filename_pattern": "extracted_content_<counter>.md",
    "full_result_in_action_result": True,
    "one_shot_read_state_delivery_requested": True,
    "durable_memory_pointer": True,
    "recovery_tool": "read_file",
    "separate_read_state_limit_chars": 60000,
    "upstream_browser_use_version_guard": "0.13.6",
    "upstream_tools_service_sha256_guard": (
        "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
    ),
    "upstream_file_system_sha256_guard": (
        "ad49a50cdc019bba871bad85c97b1b6272cb8c44739f289d6010239be2069cd8"
    ),
}

AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION = {
    "trigger_chars": 20000,
    "comparison": ">",
    "retained_head_chars": 10000,
    "retained_tail_chars": 10000,
    "separator": "......",
    "classification": {
        "exception_type": "pydantic.ValidationError",
        "title": "AgentOutput",
        "chain": "exception_or_explicit___cause___only",
        "history_result_binding": "object_identity",
    },
    "raw_context_audit": "unchanged_all_action_errors",
    "agent_visible_behavior": "unchanged",
    "unknown_errors_fail_closed": True,
    "upstream_browser_use_version_guard": "0.13.6",
    "upstream_agent_service_sha256_guard": (
        "b2d833432500d03f2edfddffb6e8682388702cd25714f2426468b66f7aa2ab33"
    ),
    "upstream_message_manager_service_sha256_guard": (
        "54959a44f45adaae357d52d67d14146553a8a3176b928b70f2a1a8b1f2d75906"
    ),
}

POST_TASK_AUXILIARY_JUDGE_CONFIGURATION = {
    "enabled": False,
    "agent_constructor_kwarg": "use_judge",
    "upstream_default": True,
    "authoritative_evaluator": (
        "caveat.core.experiment.run_cell:env.evaluate"
    ),
    "model_calls_after_agent_done": 0,
    "agent_action_behavior": "unchanged_before_done",
    "browser_shutdown": (
        "unchanged_Agent.close_then_scaffold_finally_BrowserSession.kill"
    ),
    "cleanup_bounds": (
        "existing_frozen_whole_run_and_browser_event_timeouts"
    ),
    "new_cleanup_mechanism": False,
    "upstream_browser_use_version_guard": "0.13.6",
    "upstream_agent_service_sha256_guard": (
        "b2d833432500d03f2edfddffb6e8682388702cd25714f2426468b66f7aa2ab33"
    ),
    "upstream_browser_session_sha256_guard": (
        "36169b6b024aef3d977279ce783fef6798b11e158fb5b40f32a4cd684a089b59"
    ),
}

REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION = {
    "trigger_predicate": (
        "content.count(old_str) > 1 and new_str.count(old_str) > 1"
    ),
    "match_semantics": "str_count_non_overlapping",
    "applies_to": "all_replace_file_calls_both_arms",
    "install_point": (
        "after_optional_extension_prepare_before_context_audit"
    ),
    "trigger_behavior": "ActionResult.error_and_file_unchanged",
    "silent_truncation": False,
    "upstream_browser_use_version_guard": "0.13.6",
    "upstream_tools_service_sha256_guard": (
        "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
    ),
    "upstream_file_system_sha256_guard": (
        "ad49a50cdc019bba871bad85c97b1b6272cb8c44739f289d6010239be2069cd8"
    ),
}

RUNTIME_SANITIZE_PREFIXES = (
    "AMAZON_",
    "STOREFRONT_",
    "SF_",
    "CAVEAT_",
    "BROWSER_USE_",
    "TIMEOUT_",
)
RUNTIME_SANITIZE_EXACT = (
    "ANONYMIZED_TELEMETRY",
    "ALL_PROXY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "CURL_CA_BUNDLE",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NODE_EXTRA_CA_CERTS",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_LOG",
    "OPENAI_ORGANIZATION",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_WEBHOOK_SECRET",
    "PHYAGI_GATEWAY_URL",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PYTHON_DOTENV_DISABLED",
    "PYTHONHTTPSVERIFY",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SSLKEYLOGFILE",
    "TRAPI_APIPATH",
    "TRAPI_ENDPOINT",
    "TRAPI_MODEL",
    "TRAPI_REGIONS_OVERRIDE",
    "TRAPI_SCOPE",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "NO_PROXY",
)

DEPENDENCY_CAP_SOURCE_PATHS = {
    "browser-use": (
        "browser_use/agent/message_manager/service.py",
        "browser_use/agent/service.py",
        "browser_use/agent/views.py",
        "browser_use/browser/_cdp_timeout.py",
        "browser_use/browser/events.py",
        "browser_use/browser/session.py",
        "browser_use/browser/session_manager.py",
        "browser_use/browser/watchdog_base.py",
        "browser_use/browser/watchdogs/captcha_watchdog.py",
        "browser_use/browser/watchdogs/local_browser_watchdog.py",
        "browser_use/filesystem/file_system.py",
        "browser_use/logging_config.py",
        "browser_use/tools/service.py",
    ),
    "openai": (
        "openai/_base_client.py",
        "openai/_constants.py",
    ),
    "httpx": (
        "httpx/_client.py",
        "httpx/_config.py",
    ),
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


LIMIT_AUDIT_SCHEMA_VERSION = 1


def runtime_limit_contract(
    *,
    near_fraction: float = 0.25,
) -> dict:
    """Return the exact arm-aware limit contract for measured harness runs.

    The categories intentionally separate outcome-censoring safety backstops
    from fixed harness architecture and from launch/score plumbing.  A run must
    emit one audit record for every entry applicable to its arm; the reporter
    validates the inventory exactly rather than inferring completeness from a
    small hand-picked subset.
    """

    def record(
        configured: object,
        applicability: tuple[str, ...],
        source: str,
        *,
        observation_basis: str | None = None,
        direct_maximum_observed: bool | None = None,
    ) -> dict:
        value = {
            "applicability": list(applicability),
            "configured": configured,
            "source": source,
        }
        if observation_basis is not None:
            value["observation_basis"] = observation_basis
        if direct_maximum_observed is not None:
            value["direct_maximum_observed"] = direct_maximum_observed
        return value

    both = ("baseline", "caveat_harness")
    caveat_harness = ("caveat_harness",)
    payload = {
        "schema_version": LIMIT_AUDIT_SCHEMA_VERSION,
        "categories": {
            "safety_backstops": {
                "max_steps": record(
                    CAPS["max_steps"], both,
                    "CAVEAT benchmark CLI / Agent.run",
                ),
                "whole_run_timeout_seconds": record(
                    CAPS["cell_timeout_seconds"], both,
                    "caveat/scaffolds/browseruse.py:_run",
                ),
                "llm_timeout_seconds": record(
                    CAPS["llm_timeout_seconds"], both,
                    "browser_use.agent.service.Agent",
                ),
                "llm_http_timeout_seconds": record(
                    CAPS["llm_http_timeout_seconds"], both,
                    "openai/httpx client",
                ),
                "step_timeout_seconds": record(
                    CAPS["step_timeout_seconds"], both,
                    "browser_use.agent.service.Agent._execute_step",
                ),
                "extract_llm_timeout_seconds": record(
                    CAPS["extract_llm_timeout_seconds"], both,
                    "browser_use.tools.service extract patch",
                ),
                "cdp_request_timeout_seconds": record(
                    CAPS["cdp_request_timeout_seconds"], both,
                    "browser_use.browser._cdp_timeout",
                ),
                "browser_action_timeout_seconds": record(
                    CAPS["browser_action_timeout_seconds"], both,
                    "browser_use.tools.service.Tools.act",
                ),
                "max_consecutive_failures": record(
                    CAPS["max_consecutive_failures"], both,
                    "browser_use.agent.service.Agent",
                ),
                "llm_sdk_max_retries": record(
                    {
                        "retries": CAPS["llm_sdk_max_retries"],
                        "effective_attempts":
                            CAPS["llm_sdk_max_retries"] + 1,
                        "retry_statuses": [408, 409, 429, ">=500"],
                        "retry_after_max_seconds": 60,
                        "exponential_backoff_max_seconds": 8,
                    },
                    both,
                    "openai._base_client",
                ),
                "event_timeouts_seconds": record(
                    dict(CAPS["event_timeouts_seconds"]), both,
                    "browser_use event bus TIMEOUT_* environment",
                ),
                "dependency_inner_timeouts": record(
                    {
                        "local_browser_launch_attempts": 3,
                        "local_browser_cdp_ready_seconds": 30,
                        "cdp_connect_seconds": 15,
                        "page_navigate_seconds": 20,
                        "page_ready_same_domain_seconds": 3,
                        "page_ready_cross_domain_seconds": 8,
                        "reconnect_attempts": 3,
                        "reconnect_attempt_seconds": 15,
                        "reconnect_wait_seconds": 54,
                        "dom_wait_seconds": [1, 2, 10],
                        "click_type_wait_seconds": [2, 3, 5, 10, 15],
                        "tool_wait_seconds": [2, 3, 30],
                        "download_wait_seconds": [3, 5, 10, 15, 20],
                        "popup_wait_seconds": 0.5,
                        "websocket_frame_bytes": 200 * 1024**2,
                    },
                    both,
                    "browser_use browser/session/watchdogs/tools",
                ),
                "evaluate_result_single_chars": record(
                    CAPS["evaluate_result_single_chars"],
                    both,
                    "caveat.scaffolds.browseruse common evaluate spill",
                ),
                "evaluate_result_store_bytes": record(
                    CAPS["evaluate_result_store_bytes"],
                    both,
                    "caveat.scaffolds.browseruse._EvaluateResultStore",
                ),
                "evaluate_result_store_responses": record(
                    CAPS["evaluate_result_store_responses"],
                    both,
                    "caveat.scaffolds.browseruse._EvaluateResultStore",
                ),
                "structured_response_attempts": record(
                    4,
                    caveat_harness,
                    "caveat_harness._structured",
                ),
            },
            "lossy_context_limits": {
                name: record(value, both, "browser_use prompt/tool context")
                for name, value in _agent_behavior_limits()[
                    "context_limits"
                ].items()
                if name != "extract_memory_chars"
            },
            "fixed_architecture": {
                "max_completion_tokens": record("off", both, "ChatOpenAI"),
                "max_actions_per_step": record(
                    5, both, "browser_use.agent.service.Agent",
                ),
                "empty_action_retries": record(
                    1, both, "Agent._get_model_output_with_retry",
                ),
                "fallback_llm_depth": record(
                    1, both, "Agent._try_switch_to_fallback_llm",
                ),
                "max_history_items": record(
                    None, both, "browser_use.agent.views.AgentSettings",
                ),
                "message_compaction": record(
                    {
                        "enabled": True,
                        "compact_every_n_steps": 25,
                        "trigger_char_count": 40_000,
                        "keep_last_items": 6,
                        "summary_max_chars": 6_000,
                        "include_read_state": False,
                    },
                    both,
                    "browser_use.agent.message_manager.service",
                ),
                "planning_and_loop_nudges": record(
                    {
                        "planning_enabled": True,
                        "replan_on_stall": 3,
                        "exploration_limit": 5,
                        "loop_detection_enabled": True,
                        "loop_window": 20,
                        "repetition_thresholds": [5, 8, 12],
                        "stagnation_threshold": 5,
                        "page_fingerprint_memory": 5,
                        "budget_warning_fraction": 0.75,
                        "last_step_done_only": True,
                    },
                    both,
                    "browser_use.agent.service/views",
                ),
                "url_shortening": record(
                    {"query_fragment_chars": 25, "hash_chars": 7},
                    both,
                    "browser_use.agent.service.Agent",
                ),
                "post_task_auxiliary_judge": record(
                    POST_TASK_AUXILIARY_JUDGE_CONFIGURATION,
                    both,
                    "browser_use.agent.service.Agent",
                ),
                "browser_profile": record(
                    {
                        "max_iframes": 100,
                        "max_iframe_depth": 5,
                        "minimum_page_load_wait_seconds": 0.25,
                        "network_idle_wait_seconds": 0.5,
                        "between_actions_wait_seconds": 0.1,
                    },
                    both,
                    "browser_use.browser.profile.BrowserProfile",
                ),
                "evaluate_result_spill": record(
                    {
                        "install_point": (
                            "after_optional_extension_prepare_before_"
                            "context_audit"
                        ),
                        "inline_chars": 9_999,
                        "single_bound_signal_chars": 67_108_865,
                        "addressing": "eval-sha256",
                        "operations": [
                            "stat", "list", "search", "read",
                        ],
                        "read_default_chars": 12_000,
                        "read_max_chars": 50_000,
                        "search_default_hits": 20,
                        "search_max_hits": 200,
                        "list_default": 50,
                        "list_max": 100,
                        "receipt_head_preview_chars": 500,
                        "receipt_tail_preview_chars": 500,
                        "terminates_sequence": True,
                        "store_directory_mode": "0700",
                        "store_file_mode": "0600",
                        "upstream_browser_use_version_guard": "0.13.6",
                        "upstream_tools_service_sha256_guard": (
                            "a33038d9baa18ac2306c443ba5329c4d95c"
                            "31e5ed2da0361948dfe390e4dfe3a"
                        ),
                    },
                    both,
                    "caveat.scaffolds.browseruse common evaluate spill",
                ),
                "extract_result_file_externalization": record(
                    EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION,
                    both,
                    "browser_use.tools.service.Tools.extract",
                ),
                "agent_output_validation_feedback_rendering": record(
                    AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION,
                    both,
                    (
                        "browser_use.agent.service.Agent._handle_step_error / "
                        "browser_use.agent.message_manager.service.MessageManager"
                    ),
                ),
                "replace_file_recursive_amplification_guard": record(
                    REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION,
                    both,
                    (
                        "caveat.scaffolds.browseruse common "
                        "replace_file guard"
                    ),
                ),
                "upstream_sequence_terminators": record(
                    [
                        "search_google", "navigate", "go_back",
                        "switch_tab", "evaluate",
                        "done", "error", "url_or_focus_change",
                    ],
                    both,
                    "browser_use.agent.service.Agent.multi_act",
                ),
                "compiler_and_checkpoint_shape": record(
                    {
                        "contract_compile_calls": 1,
                        # The checkpoint is agent-invoked, so zero calls is an
                        # observed behavioral result rather than an invalid
                        # runtime configuration.
                        "decision_checkpoint": "agent_invoked_optional",
                    },
                    caveat_harness,
                    "caveat_harness compiler/checkpoint",
                ),
                "caveat_harness_sequence_terminators": record(
                    ["decision_checkpoint"],
                    caveat_harness,
                    "caveat_harness._install_tools",
                ),
            },
            "diagnostic_truncations": {},
            "infrastructure": {
                "environment_startup": record(
                    {
                        "health_total_seconds": (
                            ENVIRONMENT_STARTUP_TIMEOUT_SECONDS
                        ),
                        "health_request_seconds": 5,
                        "poll_seconds": 0.4,
                        "seed_subprocess_timeout": None,
                        "process_early_exit_detection": True,
                        "server_output_artifact": "environment_server.log",
                        "timeout_marker": ENVIRONMENT_STARTUP_TIMEOUT,
                        "early_exit_marker": ENVIRONMENT_SERVER_EXITED,
                    },
                    ("score",),
                    "caveat.core.environment",
                ),
                "environment_evaluator_get": record(
                    {
                        "retries": 5,
                        "request_timeout_seconds": 15,
                        "linear_delay_seconds": 0.5,
                        "exhaustion_result": (
                            "raise:"
                            "CAVEAT_EVALUATOR_GET_RETRIES_EXHAUSTED"
                        ),
                    },
                    ("score",),
                    "caveat.core.environment.http_get_json",
                ),
                "runner_poll": record(
                    {"poll_seconds": 1.5, "worker_timeout": None},
                    ("score",),
                    "caveat.core.experiment",
                ),
            },
            "launch_only": {
                "campaign_launch": record(
                    {
                        "block_runs": 10,
                        "max_parallel_runs": 4,
                        "pair_atomic_replenishment": True,
                        "max_concurrent_browsers": 15,
                        "spawn_stagger_seconds": 10,
                        "probe_freshness_seconds": 3_600,
                        "probe_process_timeout": None,
                        "run_process_timeout": None,
                        "inventory_subprocess_timeout_seconds": 20,
                    },
                    ("launch",),
                    "scripts/caveat_harness_eval_campaign.py",
                ),
            },
        },
        "near_policy": {
            "evaluate_result_single_fraction": near_fraction,
            "evaluate_result_store_fraction": near_fraction,
            "whole_run_fraction": 0.99,
        },
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def applicable_limit_inventory(contract: dict, arm: str) -> dict[str, dict]:
    """Return the exact category/name records applicable to one measured arm."""

    categories = contract.get("categories")
    if not isinstance(categories, dict):
        return {}
    out = {}
    for category, records in categories.items():
        if not isinstance(records, dict):
            continue
        applicable = {
            name: record
            for name, record in records.items()
            if isinstance(record, dict)
            and arm in (record.get("applicability") or ())
        }
        if applicable:
            out[category] = applicable
    return out


def validate_limit_audit(
    audit: object,
    contract: object,
    arm: str,
) -> list[str]:
    """Validate a per-run limit audit against the frozen exact inventory."""

    errors = []
    if not isinstance(contract, dict):
        return ["frozen limit contract is absent or malformed"]
    payload = {key: value for key, value in contract.items() if key != "sha256"}
    if (
        contract.get("schema_version") != LIMIT_AUDIT_SCHEMA_VERSION
        or contract.get("sha256") != _sha_bytes(_json_bytes(payload))
    ):
        return ["frozen limit contract is malformed or hash-invalid"]
    if not isinstance(audit, dict):
        return ["trajectory lacks common limit_audit instrumentation"]
    expected_top = {
        "schema_version",
        "contract_sha256",
        "arm",
        "complete",
        "error",
        "categories",
    }
    if set(audit) != expected_top:
        errors.append("limit_audit top-level schema is not exact")
    if audit.get("schema_version") != LIMIT_AUDIT_SCHEMA_VERSION:
        errors.append("limit_audit schema_version differs from the freeze")
    if audit.get("contract_sha256") != contract.get("sha256"):
        errors.append("limit_audit contract hash differs from the freeze")
    if audit.get("arm") != arm:
        errors.append("limit_audit arm differs from the scheduled arm")
    if audit.get("complete") is not True:
        errors.append(
            "limit_audit is incomplete"
            + (
                f": {audit.get('error')}"
                if audit.get("error") else ""
            )
        )
    if audit.get("error") is not None:
        errors.append("limit_audit carries a runtime audit error")
    expected = applicable_limit_inventory(contract, arm)
    observed = audit.get("categories")
    if not isinstance(observed, dict) or set(observed) != set(expected):
        errors.append("limit_audit category inventory is not exact")
        return errors
    for category, expected_records in expected.items():
        records = observed.get(category)
        if not isinstance(records, dict) or set(records) != set(
            expected_records
        ):
            errors.append(
                f"limit_audit {category} name inventory is not exact"
            )
            continue
        for name, frozen in expected_records.items():
            record = records[name]
            if not isinstance(record, dict) or set(record) != {
                "configured", "touched_count", "observations"
            }:
                errors.append(
                    f"limit_audit {category}.{name} record is malformed"
                )
                continue
            if record.get("configured") != frozen.get("configured"):
                errors.append(
                    f"limit_audit {category}.{name} configuration drifted"
                )
            touched = record.get("touched_count")
            if type(touched) is not int or touched < 0:
                errors.append(
                    f"limit_audit {category}.{name} touched_count is malformed"
                )
            if not isinstance(record.get("observations"), dict):
                errors.append(
                    f"limit_audit {category}.{name} observations are malformed"
                )
                continue
            frozen_basis = frozen.get("observation_basis")
            if (
                frozen_basis is not None
                and record["observations"].get("observation_basis")
                != frozen_basis
            ):
                errors.append(
                    f"limit_audit {category}.{name} observation basis drifted"
                )
            frozen_direct = frozen.get("direct_maximum_observed")
            if (
                frozen_direct is not None
                and record["observations"].get(
                    "direct_maximum_observed"
                ) is not frozen_direct
            ):
                errors.append(
                    f"limit_audit {category}.{name} direct-maximum "
                    "attestation drifted"
                )
    return errors


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_browser_executable() -> Path:
    configured = os.environ.get("CAVEAT_CHROME")
    if configured:
        candidates = [Path(configured)]
    else:
        roots = [
            Path.home() / ".cache/ms-playwright",
            Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent")),
        ]
        candidates: list[Path] = []
        for root in roots:
            for pattern in (
                "chromium-*/chrome-linux*/chrome",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-win/chrome.exe",
            ):
                candidates.extend(sorted(root.glob(pattern)))
        if not candidates:
            for name in (
                "chromium", "chromium-browser", "google-chrome", "chrome"
            ):
                resolved = shutil.which(name)
                if resolved:
                    candidates.append(Path(resolved))
                    break
    executable = candidates[-1].expanduser().resolve() if candidates else None
    if executable is None or not executable.is_file():
        raise SystemExit(
            "no runnable Chromium executable found for runtime dependency freeze"
        )
    return executable


def _dependency_cap_source_manifest() -> dict:
    records = {}
    for distribution_name, relative_paths in (
        DEPENDENCY_CAP_SOURCE_PATHS.items()
    ):
        try:
            distribution = importlib_metadata.distribution(distribution_name)
        except importlib_metadata.PackageNotFoundError as exc:
            raise SystemExit(
                f"required runtime distribution is missing: {distribution_name}"
            ) from exc
        for relative_path in relative_paths:
            path = Path(distribution.locate_file(relative_path)).resolve()
            if not path.is_file():
                raise SystemExit(
                    "cap-bearing dependency source is missing: "
                    f"{distribution_name}:{relative_path}"
                )
            records[f"{distribution_name}:{relative_path}"] = {
                "distribution": distribution_name,
                "version": str(distribution.version),
                "path": str(path),
                "sha256": _sha_file(path),
                "size": path.stat().st_size,
            }
    return {
        "files": dict(sorted(records.items())),
        "sha256": _sha_bytes(_json_bytes(records)),
    }


def _browser_use_package_tree_manifest() -> dict:
    """Hash the complete installed browser_use package, excluding bytecode caches."""

    try:
        distribution = importlib_metadata.distribution("browser-use")
    except importlib_metadata.PackageNotFoundError as exc:
        raise SystemExit("required runtime distribution is missing: browser-use") from exc
    package = Path(distribution.locate_file("browser_use")).resolve()
    if not package.is_dir():
        raise SystemExit(f"installed browser_use package is missing: {package}")
    records = {}
    for path in sorted(package.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = str(path.relative_to(package))
        records[relative] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    if not records:
        raise SystemExit("installed browser_use package-tree inventory is empty")
    return {
        "distribution": "browser-use",
        "version": str(distribution.version),
        "package": str(package),
        "files": records,
        "file_count": len(records),
        "sha256": _sha_bytes(_json_bytes(records)),
    }


def _agent_behavior_limits() -> dict:
    payload = {
        "browser_use_version": "0.13.6",
        "max_actions_per_step": 5,
        "empty_action_retries": 1,
        "fallback_llm_depth": 1,
        "max_failures": CAPS["max_consecutive_failures"],
        "final_response_after_failure": True,
        "effective_failure_calls": CAPS["max_consecutive_failures"] + 1,
        "use_judge": False,
        "post_task_auxiliary_judge": (
            POST_TASK_AUXILIARY_JUDGE_CONFIGURATION
        ),
        "message_compaction": {
            "enabled": True,
            "compact_every_n_steps": 25,
            "trigger_char_count": 40000,
            "keep_last_items": 6,
            "summary_max_chars": 6000,
            "include_read_state": False,
        },
        "planning": {
            "enabled": True,
            "replan_on_stall": 3,
            "exploration_limit": 5,
            "loop_detection_enabled": True,
            "loop_detection_window": 20,
        },
        "context_limits": {
            "max_clickable_elements_chars": 40000,
            "read_state_chars": 60000,
            "action_results_chars": 60000,
            "action_error_chars": 20000,
            "evaluate_memory_chars": 10000,
            "extract_page_chunk_chars": 100000,
            "extract_already_collected_items": 100,
            "extract_memory_chars": 10000,
        },
        "extract_result_file_externalization": (
            EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION
        ),
        "agent_output_validation_feedback_rendering": (
            AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION
        ),
        "replace_file_recursive_amplification_guard": (
            REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION
        ),
        "action_error_prompt_cap_patch": {
            "upstream_chars": 200,
            "effective_chars": 20000,
            "effective_edge_chars": 10000,
            "upstream_browser_use_version_guard": "0.13.6",
            "upstream_message_manager_service_sha256_guard": (
                "54959a44f45adaae357d52d67d14146553a8a3176b928b70f2a1a8b1f2d75906"
            ),
        },
        "extract_timeout_patch": {
            "upstream_seconds": 120,
            "effective_seconds": CAPS["extract_llm_timeout_seconds"],
            "minimum_seconds": 7500,
            "upstream_browser_use_version_guard": "0.13.6",
            "upstream_tools_service_sha256_guard": (
                "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
            ),
        },
        "evaluate_result_spill": {
            "install_point": (
                "after_optional_extension_prepare_before_context_audit"
            ),
            "inline_chars": 9999,
            "single_bound_signal_chars": 67108865,
            "single_result_safety_backstop_chars": (
                CAPS["evaluate_result_single_chars"]
            ),
            "store_max_bytes": CAPS["evaluate_result_store_bytes"],
            "store_max_responses": (
                CAPS["evaluate_result_store_responses"]
            ),
            "opaque_id": "eval-sha256",
            "operations": ["stat", "list", "search", "read"],
            "read_default_chars": 12000,
            "read_max_chars": 50000,
            "search_default_hits": 20,
            "search_max_hits": 200,
            "list_default": 50,
            "list_max": 100,
            "receipt_head_preview_chars": 500,
            "receipt_tail_preview_chars": 500,
            "terminates_sequence": True,
            "store_directory_mode": "0700",
            "store_file_mode": "0600",
            "upstream_browser_use_version_guard": "0.13.6",
            "upstream_tools_service_sha256_guard": (
                "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
            ),
        },
        "openai_sdk_retries": {
            "configured_retries": CAPS["llm_sdk_max_retries"],
            "effective_attempts": CAPS["llm_sdk_max_retries"] + 1,
            "retry_statuses": [408, 409, 429, ">=500"],
            "retry_after_max_seconds": 60,
            "exponential_backoff_max_seconds": 8,
        },
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def runtime_dependency_manifest() -> dict:
    """Return an exact, reproducible manifest of the measured runtime."""
    executable = _find_browser_executable()
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(
            f"cannot identify frozen Chromium executable {executable}: {exc}"
        ) from exc
    browser_version = (completed.stdout or completed.stderr).strip()
    if not browser_version:
        raise SystemExit(
            f"Chromium executable returned no version: {executable}"
        )
    distributions = sorted(
        (
            {
                "name": str(dist.metadata.get("Name") or "").strip(),
                "version": str(dist.version),
            }
            for dist in importlib_metadata.distributions()
        ),
        key=lambda row: (row["name"].casefold(), row["name"], row["version"]),
    )
    if not distributions or any(not row["name"] for row in distributions):
        raise SystemExit("installed Python distribution manifest is incomplete")
    payload = {
        "python": {
            "executable": str(Path(sys.executable).resolve()),
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "version_detail": sys.version,
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "browser": {
            "executable": str(executable),
            "version": browser_version,
            "library_path": os.environ.get("CAVEAT_CHROME_LIBS") or None,
        },
        "installed_distributions": distributions,
        "installed_distribution_count": len(distributions),
        "installed_distributions_sha256": _sha_bytes(
            _json_bytes(distributions)
        ),
        "cap_bearing_dependency_sources": (
            _dependency_cap_source_manifest()
        ),
        "browser_use_package_tree": _browser_use_package_tree_manifest(),
        "agent_behavior_limits": _agent_behavior_limits(),
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def _runtime_environment_policy(runtime: dict) -> dict:
    """Closed non-secret environment inherited by certifiers and measured runs."""
    values = {
        "AMAZON_API_GATE": "1",
        "AMAZON_SPEC_BUDGET": "0",
        "AMAZON_SSR": "0",
        "STOREFRONT_API_GATE": "1",
        "SF_RATE_ENABLED": "0",
        "SF_COUNT_MODE": "request",
        "SF_RATE_SHORT_WINDOW": "10",
        "SF_RATE_SHORT_MAX": "12",
        "SF_RATE_LONG_WINDOW": "60",
        "SF_RATE_LONG_MAX": "60",
        "SF_RATE_SUSTAINED_WINDOW": "300",
        "SF_RATE_SUSTAINED_MAX": "80",
        "SF_CHALLENGE_MIN_DELAY": "2",
        "SF_CHALLENGE_TTL": "45",
        "CAVEAT_CELL_TIMEOUT": str(CAPS["cell_timeout_seconds"]),
        "CAVEAT_LLM_TIMEOUT": str(CAPS["llm_timeout_seconds"]),
        "CAVEAT_MAX_FAILURES": str(CAPS["max_consecutive_failures"]),
        "CAVEAT_MAX_COMPLETION_TOKENS": str(
            CAPS["max_completion_tokens"]
        ),
        "CAVEAT_LLM_CACHE": "0",
        "CAVEAT_NO_SHOT_PERSIST": "1",
        "CAVEAT_SPAWN_STAGGER": str(CAPS["spawn_stagger_seconds"]),
        "PYTHON_DOTENV_DISABLED": "1",
        "ANONYMIZED_TELEMETRY": "false",
        "BROWSER_USE_CLOUD_SYNC": "false",
        "BROWSER_USE_CDP_TIMEOUT_S": str(
            CAPS["cdp_request_timeout_seconds"]
        ),
        "BROWSER_USE_ACTION_TIMEOUT_S": str(
            CAPS["browser_action_timeout_seconds"]
        ),
        "BROWSER_USE_EXTRACT_TIMEOUT_S": str(
            CAPS["extract_llm_timeout_seconds"]
        ),
        "CAVEAT_CHROME": runtime["browser"]["executable"],
    }
    library_path = runtime["browser"].get("library_path")
    if library_path:
        values["CAVEAT_CHROME_LIBS"] = str(library_path)
    values.update({
        f"TIMEOUT_{name}": str(seconds)
        for name, seconds in CAPS["event_timeouts_seconds"].items()
    })
    required_absent = (
        "CAVEAT_CACHE_NONCE",
        "CAVEAT_NO_VISION",
        "AMAZON_EXPERIMENT",
        "AMAZON_EXPERIMENT_CATALOG",
        "AMAZON_PIN_ASINS",
        "AMAZON_STEERING",
        "BROWSER_USE_ALLOWED_DOMAINS",
        "BROWSER_USE_BROWSER_MODE",
        "BROWSER_USE_CONFIG_DIR",
        "BROWSER_USE_CONFIG_FILE",
        "BROWSER_USE_CONFIG_PATH",
        "BROWSER_USE_DEFAULT_USER_DATA_DIR",
        "BROWSER_USE_DISABLE_EXTENSIONS",
        "BROWSER_USE_EXTENSIONS_DIR",
        "BROWSER_USE_HEADLESS",
        "BROWSER_USE_LLM_MODEL",
        "BROWSER_USE_LLM_URL",
        "BROWSER_USE_PROXY_PASSWORD",
        "BROWSER_USE_PROXY_URL",
        "BROWSER_USE_PROXY_USERNAME",
        "BROWSER_USE_RUST_BROWSER_MODE",
        "BROWSER_USE_RUST_MODEL",
        "ALL_PROXY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "CURL_CA_BUNDLE",
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "OPENAI_LOG",
        "OPENAI_ORGANIZATION",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENAI_WEBHOOK_SECRET",
        "PHYAGI_GATEWAY_URL",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHONHTTPSVERIFY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SSLKEYLOGFILE",
        "STOREFRONT_CLIENT_TOKEN",
        "STOREFRONT_OPS_TOKEN",
        "TRAPI_APIPATH",
        "TRAPI_ENDPOINT",
        "TRAPI_MODEL",
        "TRAPI_REGIONS_OVERRIDE",
        "TRAPI_SCOPE",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "NO_PROXY",
    )
    payload = {
        "sanitize_prefixes": list(RUNTIME_SANITIZE_PREFIXES),
        "sanitize_exact": list(RUNTIME_SANITIZE_EXACT),
        "set": dict(sorted(values.items())),
        "required_absent_after_apply": list(required_absent),
        "per_run_set": {
            "TRAPI_REGIONS_OVERRIDE": "schedule.region_order"
        },
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def _installed_event_timeout_names() -> set[str]:
    try:
        distribution = importlib_metadata.distribution("browser-use")
    except importlib_metadata.PackageNotFoundError as exc:
        raise SystemExit("browser-use distribution is not installed") from exc
    package = Path(distribution.locate_file("browser_use"))
    paths = (
        package / "browser/events.py",
        package / "browser/watchdogs/captcha_watchdog.py",
        package / "agent/service.py",
    )
    if any(not path.is_file() for path in paths):
        raise SystemExit("browser-use timeout source inventory is incomplete")
    return set(re.findall(
        r"TIMEOUT_([A-Za-z0-9_]+)",
        "\n".join(path.read_text(errors="replace") for path in paths),
    ))


def _validate_caps(caps: dict) -> None:
    if caps != CAPS:
        raise SystemExit("campaign cap inventory differs from frozen code")
    errors = []
    if caps.get("max_steps", 0) < 12000:
        errors.append("max_steps is below 12,000")
    if caps.get("cell_timeout_seconds", 0) < 172800:
        errors.append("run timeout is below 48 hours")
    if caps.get("llm_timeout_seconds", 0) < 7200:
        errors.append("LLM timeout is below two hours")
    if caps.get("llm_http_timeout_seconds") != (
        caps.get("llm_timeout_seconds", 0) + 60
    ):
        errors.append("LLM HTTP timeout is not LLM timeout + 60 seconds")
    if caps.get("step_timeout_seconds") != (
        caps.get("llm_timeout_seconds", 0) + 300
    ):
        errors.append("step timeout is not LLM timeout + 300 seconds")
    if caps.get("extract_llm_timeout_seconds", 0) < 7500:
        errors.append("extract LLM timeout is below 7,500 seconds")
    if caps.get("cdp_request_timeout_seconds", 0) < caps.get(
        "step_timeout_seconds", 0
    ):
        errors.append("CDP request timeout is below the whole-step timeout")
    if caps.get("browser_action_timeout_seconds", 0) < caps.get(
        "step_timeout_seconds", 0
    ):
        errors.append("browser action timeout is below the whole-step timeout")
    if caps.get("max_consecutive_failures", 0) < 1000:
        errors.append("consecutive-failure backstop is below 1,000")
    if caps.get("max_completion_tokens") != "off":
        errors.append("completion-token ceiling is not disabled")
    if caps.get("llm_sdk_max_retries") != 16:
        errors.append("LLM SDK retry inventory is not 16")
    events = caps.get("event_timeouts_seconds") or {}
    if set(events) != set(EVENT_TIMEOUT_NAMES):
        errors.append("browser-use event timeout inventory is not exact")
    installed_events = _installed_event_timeout_names()
    if set(events) != installed_events:
        errors.append(
            "frozen event timeout inventory differs from installed browser-use: "
            f"missing={sorted(installed_events - set(events))} "
            f"extra={sorted(set(events) - installed_events)}"
        )
    if any(
        type(value) is not int or value < 600 for value in events.values()
    ):
        errors.append("one or more browser-use event ceilings are below 600s")
    if errors:
        raise SystemExit("invalid frozen cap inventory:\n  " + "\n  ".join(errors))


def _iter_code_paths() -> Iterable[Path]:
    for base, suffixes in (
        (ROOT / "caveat", {".py"}),
        (ROOT / "scripts", {".py", ".sh"}),
    ):
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix in suffixes
                and "__pycache__" not in path.parts
            ):
                yield path
    frontend = ROOT / "caveat/envs/amazon/server/frontend/dist"
    if frontend.exists():
        yield from (path for path in frontend.rglob("*") if path.is_file())
    for name in ("pyproject.toml", "uv.lock", "pytest.ini"):
        path = ROOT / name
        if path.exists():
            yield path
    for path in ROOT.glob("requirements*.txt"):
        if path.is_file():
            yield path


def code_inventory() -> dict[str, dict]:
    out = {}
    for path in sorted(set(item.resolve() for item in _iter_code_paths())):
        relative = str(path.relative_to(ROOT.resolve()))
        out[relative] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    return out
