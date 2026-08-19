"""browser-use scaffold (https://github.com/browser-use/browser-use).

A popular CDP-based web agent. We point its ``ChatOpenAI`` at the model's
OpenAI-compatible endpoint, launch the configured Chromium, run the agent on the
task, and normalize ``agent.history`` into the shared trajectory format.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata as importlib_metadata
import inspect
import json
import logging
import os
import re
import stat
import tempfile
import time
from functools import wraps
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError

from ..core.models import ModelSpec
from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ..core.trajectory import Step
from ._browser import BrowserConfig


_LOG = logging.getLogger(__name__)
_BROWSER_USE_VERSION = "0.13.6"
_BROWSER_USE_TOOLS_SERVICE_SHA256 = (
    "a33038d9baa18ac2306c443ba5329c4d95c31e5ed2da0361948dfe390e4dfe3a"
)
_BROWSER_USE_FILE_SYSTEM_SHA256 = (
    "ad49a50cdc019bba871bad85c97b1b6272cb8c44739f289d6010239be2069cd8"
)
_BROWSER_USE_AGENT_SERVICE_SHA256 = (
    "b2d833432500d03f2edfddffb6e8682388702cd25714f2426468b66f7aa2ab33"
)
_BROWSER_USE_BROWSER_SESSION_SHA256 = (
    "36169b6b024aef3d977279ce783fef6798b11e158fb5b40f32a4cd684a089b59"
)
_BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256 = (
    "54959a44f45adaae357d52d67d14146553a8a3176b928b70f2a1a8b1f2d75906"
)
_BROWSER_USE_MESSAGE_MANAGER_METHOD_SOURCE_SHA256 = (
    "fc30cbba7fd7f90fbd6754b34af25911c4fb2d3d56b6c975091cf2cd82548765"
)
_BROWSER_USE_MESSAGE_MANAGER_METHOD_CODE_SHA256 = (
    "48832725d2cefc6479ec6a978f7c6d9b034a2d7df45f81fd3834e1398cd201f7"
)
_BROWSER_USE_MESSAGE_MANAGER_METHOD_SIGNATURE = (
    "(self, model_output: 'AgentOutput | None' = None, "
    "result: 'list[ActionResult] | None' = None, "
    "step_info: 'AgentStepInfo | None' = None) -> 'None'"
)
_LOSSLESS_READ_STATE_RECOVERY_ENV = (
    "AGENTARENA_LOSSLESS_READ_STATE_RECOVERY_JSON"
)
_LOSSLESS_READ_STATE_RECOVERY_SCHEMA = (
    "agentarena.lossless-read-state-recovery-authorization.v1"
)
_LOSSLESS_READ_STATE_RECOVERY_MODE = "hash_bound_singleton_retry"
_LOSSLESS_READ_STATE_RECOVERY_CAMPAIGN_UUID = (
    "c38400ed-ccb1-427a-a749-a8e895a0dea8"
)
_LOSSLESS_READ_STATE_RECOVERY_MANIFEST_SHA256 = (
    "d7e1046483fea0f42ce9b20c961b4e31382414c85479042064d2686b0108fa91"
)
_LOSSLESS_READ_STATE_RECOVERY_RUN_INDEX = 598
_LOSSLESS_READ_STATE_RECOVERY_RUN_ID = (
    "instacart_r1/"
    "instacart__browseruse__Kimi-K2.6__greens-graded4__clean"
)
_LOSSLESS_READ_STATE_RECOVERY_PRIOR_ATTEMPT = 1
_LOSSLESS_READ_STATE_RECOVERY_PRIOR_COMPLETION_SHA256 = (
    "769c73b6a4deb79170f1e252c35f2400ec274ede361109761a55a8b280ac36d7"
)
_LOSSLESS_READ_STATE_RECOVERY_PRIOR_TRAJECTORY_SHA256 = (
    "608fb22554db6eb662d16ebe5955cfccfa291a279336b121a376c5d5c1c339b4"
)
_READ_STATE_CONTENT_LIMIT_CHARS = 60000
_READ_STATE_TRUNCATION_MARKER = (
    "\n... [Content truncated at 60k characters]"
)
_UPSTREAM_EXTRACT_TIMEOUT_S = 120.0
_UPSTREAM_ACTION_ERROR_PROMPT_CHARS = 200
_EFFECTIVE_ACTION_ERROR_PROMPT_CHARS = 20000
_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS = (
    _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS // 2
)
_EFFECTIVE_MESSAGE_MANAGER_METHOD_CONSTANTS = (
    "Update the agent history description",
    None,
    "",
    0,
    "<read_state_",
    ">\n",
    "\n</read_state_",
    1,
    "Added extracted_content to read_state_description: ",
    "Added ",
    " image(s) to read_state_images",
    "\n",
    "Added long_term_memory to action_results: ",
    "Added extracted_content to action_results: ",
    _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS,
    _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
    "......",
    -_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
    "Added error to action_results: ",
    _READ_STATE_CONTENT_LIMIT_CHARS,
    _READ_STATE_TRUNCATION_MARKER,
    "Truncated read_state_description to ",
    " characters",
    "Result\n",
    "Truncated action_results to ",
    ("step_number", "action_results"),
    "Agent failed to output in the right format.",
    ("step_number", "error"),
    (
        "step_number",
        "evaluation_previous_goal",
        "memory",
        "next_goal",
        "action_results",
    ),
)
_UPSTREAM_EVALUATE_OUTPUT_CHARS = 20000
_UPSTREAM_EVALUATE_OUTPUT_PREFIX_CHARS = 19950
_UPSTREAM_EVALUATE_OUTPUT_MARKER = (
    "\n... [Truncated after 20000 characters]"
)
_UPSTREAM_EVALUATE_MEMORY_CHARS = 10000
# Browser Use stops retaining the literal result in long-term memory at this
# boundary.  Keep every value below it byte-for-byte inline, and spill every
# value at or above it so no result crosses the lossy memory-routing branch.
_EVALUATE_INLINE_CHARS = _UPSTREAM_EVALUATE_MEMORY_CHARS - 1
_EVALUATE_SINGLE_MAX_CHARS = 64 * 1024**2
_EVALUATE_SINGLE_MARKER = (
    "\n... [Truncated after 67108864 characters]"
)
_EVALUATE_SINGLE_PREFIX_CHARS = (
    _EVALUATE_SINGLE_MAX_CHARS - len(_EVALUATE_SINGLE_MARKER) + 1
)
_EVALUATE_STORE_MAX_BYTES = 8 * 1024**3
_EVALUATE_STORE_MAX_RESPONSES = 200_000
_EVALUATE_INSPECT_DEFAULT_CHARS = 12_000
_EVALUATE_INSPECT_MAX_CHARS = 50_000
_EVALUATE_INSPECT_DEFAULT_HITS = 20
_EVALUATE_INSPECT_MAX_HITS = 200
_EVALUATE_INSPECT_DEFAULT_LIST = 50
_EVALUATE_INSPECT_MAX_LIST = 100
_EVALUATE_RECEIPT_PREVIEW_CHARS = 500
_EVALUATE_RESULT_SPILL_CONFIGURATION = {
    "install_point": (
        "after_optional_extension_prepare_before_context_audit"
    ),
    "inline_chars": _EVALUATE_INLINE_CHARS,
    "single_bound_signal_chars": _EVALUATE_SINGLE_MAX_CHARS + 1,
    "addressing": "eval-sha256",
    "operations": ["stat", "list", "search", "read"],
    "read_default_chars": _EVALUATE_INSPECT_DEFAULT_CHARS,
    "read_max_chars": _EVALUATE_INSPECT_MAX_CHARS,
    "search_default_hits": _EVALUATE_INSPECT_DEFAULT_HITS,
    "search_max_hits": _EVALUATE_INSPECT_MAX_HITS,
    "list_default": _EVALUATE_INSPECT_DEFAULT_LIST,
    "list_max": _EVALUATE_INSPECT_MAX_LIST,
    "receipt_head_preview_chars": _EVALUATE_RECEIPT_PREVIEW_CHARS,
    "receipt_tail_preview_chars": _EVALUATE_RECEIPT_PREVIEW_CHARS,
    "terminates_sequence": True,
    "store_directory_mode": "0700",
    "store_file_mode": "0600",
    "upstream_browser_use_version_guard": _BROWSER_USE_VERSION,
    "upstream_tools_service_sha256_guard":
        _BROWSER_USE_TOOLS_SERVICE_SHA256,
}
_EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION = {
    "inline_chars_max": 9999,
    "externalize_at_chars": 10000,
    "storage_operation": "FileSystem.save_extracted_content",
    "filename_pattern": "extracted_content_<counter>.md",
    "full_result_in_action_result": True,
    "one_shot_read_state_delivery_requested": True,
    "durable_memory_pointer": True,
    "recovery_tool": "read_file",
    "separate_read_state_limit_chars": 60000,
    "upstream_browser_use_version_guard": _BROWSER_USE_VERSION,
    "upstream_tools_service_sha256_guard":
        _BROWSER_USE_TOOLS_SERVICE_SHA256,
    "upstream_file_system_sha256_guard": _BROWSER_USE_FILE_SYSTEM_SHA256,
}
_AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION = {
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
    "upstream_browser_use_version_guard": _BROWSER_USE_VERSION,
    "upstream_agent_service_sha256_guard": (
        _BROWSER_USE_AGENT_SERVICE_SHA256
    ),
    "upstream_message_manager_service_sha256_guard": (
        _BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256
    ),
}
_POST_TASK_AUXILIARY_JUDGE_CONFIGURATION = {
    "enabled": False,
    "agent_constructor_kwarg": "use_judge",
    "upstream_default": True,
    "authoritative_evaluator": (
        "agentarena.core.experiment.run_cell:env.evaluate"
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
    "upstream_browser_use_version_guard": _BROWSER_USE_VERSION,
    "upstream_agent_service_sha256_guard": (
        _BROWSER_USE_AGENT_SERVICE_SHA256
    ),
    "upstream_browser_session_sha256_guard": (
        _BROWSER_USE_BROWSER_SESSION_SHA256
    ),
}
_REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION = {
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
    "upstream_browser_use_version_guard": _BROWSER_USE_VERSION,
    "upstream_tools_service_sha256_guard":
        _BROWSER_USE_TOOLS_SERVICE_SHA256,
    "upstream_file_system_sha256_guard":
        _BROWSER_USE_FILE_SYSTEM_SHA256,
}
_CONTEXT_CAPS = {
    "action_error_chars": (_EFFECTIVE_ACTION_ERROR_PROMPT_CHARS, ">"),
    "action_results_chars": (60000, ">"),
    "evaluate_memory_chars": (_UPSTREAM_EVALUATE_MEMORY_CHARS, ">="),
    "extract_already_collected_items": (100, ">"),
    "extract_memory_chars": (10000, ">="),
    "extract_page_chunk_chars": (100000, ">"),
    "max_clickable_elements_chars": (40000, ">"),
    "read_state_chars": (60000, ">"),
}
_LOSSY_CONTEXT_CAPS = {
    name: value for name, value in _CONTEXT_CAPS.items()
    if name != "extract_memory_chars"
}
_CLICKABLE_ELEMENTS_MARKER = (
    "Interactive elements (truncated to 40000 characters):"
)


class _InspectEvaluateResult(BaseModel):
    operation: Literal["stat", "list", "search", "read"]
    result_id: str = Field(default="", max_length=69)
    start: int = Field(default=0, ge=0)
    length: int = Field(
        default=_EVALUATE_INSPECT_DEFAULT_CHARS,
        ge=1,
        le=_EVALUATE_INSPECT_MAX_CHARS,
    )
    needle: str = Field(default="", max_length=10_000)
    max_hits: int = Field(
        default=_EVALUATE_INSPECT_DEFAULT_HITS,
        ge=1,
        le=_EVALUATE_INSPECT_MAX_HITS,
    )
    limit: int = Field(
        default=_EVALUATE_INSPECT_DEFAULT_LIST,
        ge=1,
        le=_EVALUATE_INSPECT_MAX_LIST,
    )


class _EvaluateResultStoreCapacityError(RuntimeError):
    """A declared evaluate-result storage backstop would be exceeded."""


class _EvaluateResultStoreIntegrityError(RuntimeError):
    """Run-local evaluate-result storage is unsafe or internally inconsistent."""


class _EvaluateResultStore:
    """Lossless, content-addressed storage for large evaluate results."""

    _ID_RE = re.compile(r"eval-[0-9a-f]{64}")

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = _EVALUATE_STORE_MAX_BYTES,
        max_responses: int = _EVALUATE_STORE_MAX_RESPONSES,
    ) -> None:
        if max_bytes <= 0 or max_responses <= 0:
            raise ValueError("evaluate-result store caps must be positive")
        self.root = Path(root)
        if self.root.is_symlink():
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result store root may not be a symlink"
            )
        try:
            # A store belongs to exactly one run.  In particular, a forced
            # rerun must never adopt records left in a reused work directory.
            # mkdir without exist_ok is the atomic freshness check.
            self.root.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result store root already exists; refusing to "
                "import prior-run records"
            ) from exc
        except OSError as exc:
            raise _EvaluateResultStoreIntegrityError(
                "cannot create a fresh evaluate-result store root"
            ) from exc
        self._assert_secure_path(
            self.root,
            kind="directory",
            expected_mode=0o700,
        )
        self.max_bytes = int(max_bytes)
        self.max_responses = int(max_responses)
        self._responses = 0
        self._response_bound_touched = 0
        self._byte_bound_touched = 0
        self._single_bound_touched = 0
        self._integrity_failures = 0
        self._max_serialized_chars = 0
        self._records, self._bytes = self._scan_usage()

    @staticmethod
    def _assert_secure_path(
        path: Path,
        *,
        kind: Literal["directory", "file"],
        expected_mode: int,
    ) -> os.stat_result:
        try:
            observed = path.lstat()
        except OSError as exc:
            raise _EvaluateResultStoreIntegrityError(
                f"evaluate-result {kind} is unavailable: {path.name}"
            ) from exc
        correct_type = (
            stat.S_ISDIR(observed.st_mode)
            if kind == "directory"
            else stat.S_ISREG(observed.st_mode)
        )
        if stat.S_ISLNK(observed.st_mode) or not correct_type:
            raise _EvaluateResultStoreIntegrityError(
                f"evaluate-result {kind} has an unsafe file type: "
                f"{path.name}"
            )
        if stat.S_IMODE(observed.st_mode) != expected_mode:
            raise _EvaluateResultStoreIntegrityError(
                f"evaluate-result {kind} mode is not "
                f"{expected_mode:04o}: {path.name}"
            )
        if hasattr(os, "getuid") and observed.st_uid != os.getuid():
            raise _EvaluateResultStoreIntegrityError(
                f"evaluate-result {kind} is not owned by this process user: "
                f"{path.name}"
            )
        return observed

    def _assert_secure_root(self) -> None:
        self._assert_secure_path(
            self.root,
            kind="directory",
            expected_mode=0o700,
        )

    def _assert_secure_file(self, path: Path) -> os.stat_result:
        return self._assert_secure_path(
            path,
            kind="file",
            expected_mode=0o600,
        )

    def _paths(self, result_id: str) -> tuple[Path, Path]:
        self._assert_secure_root()
        if not self._ID_RE.fullmatch(str(result_id)):
            raise ValueError("invalid evaluate-result ID")
        return (
            self.root / f"{result_id}.data",
            self.root / f"{result_id}.json",
        )

    def _scan_usage(self) -> tuple[int, int]:
        self._assert_secure_root()
        allowed_names: set[str] = set()
        for path in self.root.iterdir():
            if path.name.startswith(".pending-evaluate-"):
                raise _EvaluateResultStoreIntegrityError(
                    "incomplete evaluate-result atomic write is present"
                )
            if re.fullmatch(
                r"eval-[0-9a-f]{64}\.(?:data|json)",
                path.name,
            ) is None:
                raise _EvaluateResultStoreIntegrityError(
                    f"unexpected evaluate-result store entry: {path.name}"
                )
            self._assert_secure_file(path)
            allowed_names.add(path.name)
        record_ids = {
            name.rsplit(".", 1)[0] for name in allowed_names
        }
        for result_id in record_ids:
            if {
                f"{result_id}.data",
                f"{result_id}.json",
            } - allowed_names:
                raise _EvaluateResultStoreIntegrityError(
                    f"incomplete evaluate-result record {result_id}"
                )
        records = 0
        byte_count = 0
        for path in sorted(self.root.glob("eval-*.json")):
            try:
                metadata = json.loads(path.read_text("utf-8"))
                result_id = str(metadata["result_id"])
                data_path, metadata_path = self._paths(result_id)
                if path.name != f"{result_id}.json":
                    raise ValueError("metadata filename mismatch")
                if {
                    data_path.name,
                    metadata_path.name,
                } - allowed_names:
                    raise ValueError("evaluate-result record is incomplete")
                observed = self.stat(result_id)
                self.read_text(result_id)
                byte_count += int(observed["bytes"])
                records += 1
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise _EvaluateResultStoreIntegrityError(
                    f"corrupt evaluate-result metadata: {path.name}"
                ) from exc
        return records, byte_count

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=".pending-evaluate-",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            try:
                # Linking a same-directory temporary file publishes it
                # atomically only when the destination is absent.  Unlike
                # os.replace, a concurrent winner is never overwritten.
                os.link(
                    temporary,
                    path,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise _EvaluateResultStoreIntegrityError(
                    f"evaluate-result destination appeared concurrently: "
                    f"{path.name}"
                ) from exc
            except OSError as exc:
                raise _EvaluateResultStoreIntegrityError(
                    f"cannot publish evaluate-result file: {path.name}"
                ) from exc
            _EvaluateResultStore._assert_secure_path(
                path,
                kind="file",
                expected_mode=0o600,
            )
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def note_serialized(self, chars: int, *, bound_touched: bool) -> None:
        chars = max(0, int(chars))
        self._max_serialized_chars = max(
            self._max_serialized_chars, chars
        )
        if bound_touched:
            self._single_bound_touched += 1

    def note_integrity_failure(self) -> None:
        self._integrity_failures += 1

    def add(self, text: str) -> dict[str, Any]:
        if self._responses + 1 > self.max_responses:
            self._response_bound_touched += 1
            raise _EvaluateResultStoreCapacityError(
                "evaluate-result response ceiling reached"
            )
        content = str(text).encode("utf-8", errors="surrogatepass")
        content_sha256 = hashlib.sha256(content).hexdigest()
        result_id = f"eval-{content_sha256}"
        data_path, metadata_path = self._paths(result_id)
        metadata = {
            "schema_version": 1,
            "result_id": result_id,
            "sha256": content_sha256,
            "chars": len(str(text)),
            "bytes": len(content),
        }
        if data_path.is_symlink() or metadata_path.is_symlink():
            raise _EvaluateResultStoreIntegrityError(
                f"evaluate-result record is symlinked {result_id}"
            )
        if data_path.exists() or metadata_path.exists():
            if not (data_path.exists() and metadata_path.exists()):
                raise _EvaluateResultStoreIntegrityError(
                    f"incomplete evaluate-result record {result_id}"
                )
            observed = self.stat(result_id)
            if observed != metadata or self.read_text(result_id) != text:
                raise _EvaluateResultStoreIntegrityError(
                    f"evaluate-result hash collision {result_id}"
                )
        else:
            if self._bytes + len(content) > self.max_bytes:
                self._byte_bound_touched += 1
                raise _EvaluateResultStoreCapacityError(
                    "evaluate-result byte ceiling reached"
                )
            self._atomic_write(data_path, content)
            self._atomic_write(
                metadata_path,
                (
                    json.dumps(
                        metadata,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self._records += 1
            self._bytes += len(content)
        self._responses += 1
        return metadata

    def stat(self, result_id: str) -> dict[str, Any]:
        data_path, metadata_path = self._paths(result_id)
        presence = []
        for path in (data_path, metadata_path):
            try:
                path.lstat()
                presence.append(True)
            except FileNotFoundError:
                presence.append(False)
            except OSError as exc:
                raise _EvaluateResultStoreIntegrityError(
                    f"cannot inspect evaluate-result record {result_id}"
                ) from exc
        if presence == [False, False]:
            raise ValueError(f"unknown evaluate-result ID {result_id}")
        if presence != [True, True]:
            raise _EvaluateResultStoreIntegrityError(
                f"incomplete evaluate-result record {result_id}"
            )
        self._assert_secure_file(data_path)
        self._assert_secure_file(metadata_path)
        try:
            metadata = json.loads(metadata_path.read_text("utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise _EvaluateResultStoreIntegrityError(
                f"cannot read evaluate-result metadata {result_id}"
            ) from exc
        expected = {
            "schema_version": 1,
            "result_id": result_id,
            "sha256": result_id.removeprefix("eval-"),
            "chars": metadata.get("chars"),
            "bytes": metadata.get("bytes"),
        }
        if metadata != expected:
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result metadata is malformed"
            )
        if (
            type(metadata["chars"]) is not int
            or metadata["chars"] < 0
            or type(metadata["bytes"]) is not int
            or metadata["bytes"] < 0
            or self._assert_secure_file(data_path).st_size
            != metadata["bytes"]
        ):
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result metadata/data disagree"
            )
        return metadata

    def read_text(self, result_id: str) -> str:
        data_path, _metadata_path = self._paths(result_id)
        metadata = self.stat(result_id)
        try:
            content = data_path.read_bytes()
        except OSError as exc:
            raise _EvaluateResultStoreIntegrityError(
                f"cannot read evaluate-result data {result_id}"
            ) from exc
        if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result content hash mismatch"
            )
        try:
            text = content.decode("utf-8", errors="surrogatepass")
        except UnicodeDecodeError as exc:
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result content is not valid UTF-8"
            ) from exc
        if len(text) != metadata["chars"]:
            raise _EvaluateResultStoreIntegrityError(
                "evaluate-result character count mismatch"
            )
        return text

    def list_records(self, start: int, limit: int) -> dict[str, Any]:
        self._assert_secure_root()
        ids = sorted(path.stem for path in self.root.glob("eval-*.json"))
        page = [self.stat(result_id) for result_id in ids[start:start + limit]]
        return {
            "total": len(ids),
            "start": start,
            "records": page,
        }

    def search(
        self,
        result_id: str,
        needle: str,
        *,
        start: int,
        max_hits: int,
    ) -> dict[str, Any]:
        if not needle:
            raise ValueError("search requires a non-empty needle")
        text = self.read_text(result_id)
        offsets: list[int] = []
        cursor = min(start, len(text))
        while len(offsets) < max_hits:
            found = text.find(needle, cursor)
            if found < 0:
                break
            offsets.append(found)
            cursor = found + max(1, len(needle))
        return {
            "result_id": result_id,
            "start": start,
            "offsets": offsets,
            "next_start": cursor if len(offsets) == max_hits else None,
            "chars": len(text),
        }

    def snapshot(self) -> dict[str, Any]:
        self._assert_secure_root()
        return {
            "schema_version": 1,
            "inline_chars": _EVALUATE_INLINE_CHARS,
            "single_max_chars": _EVALUATE_SINGLE_MAX_CHARS,
            "max_serialized_chars": self._max_serialized_chars,
            "single_bound_touched_count": self._single_bound_touched,
            "integrity_failure_count": self._integrity_failures,
            "max_bytes": self.max_bytes,
            "bytes": self._bytes,
            "byte_bound_touched_count": self._byte_bound_touched,
            "max_responses": self.max_responses,
            "responses": self._responses,
            "response_bound_touched_count":
                self._response_bound_touched,
            "records": self._records,
        }


def _lift_action_error_prompt_cap() -> dict[str, Any]:
    """Lift browser-use's lossy 200-character action-error prompt cap.

    The upstream method retains only the first and last 100 characters of an
    action error longer than 200 characters.  Patch all three literals so the
    effective boundary is 20,000 characters with symmetric 10,000-character
    edges.  Exact distribution, source, and bytecode-shape guards make this
    fail closed if browser-use changes underneath the benchmark.
    """
    actual_version = importlib_metadata.version("browser-use")
    if actual_version != _BROWSER_USE_VERSION:
        raise RuntimeError(
            "browser-use version changed under action-error cap guard: "
            f"{actual_version!r} != {_BROWSER_USE_VERSION!r}"
        )

    from browser_use.agent.message_manager.service import MessageManager

    implementation = MessageManager._update_agent_history_description
    if (
        getattr(implementation, "__qualname__", "")
        != "MessageManager._update_agent_history_description"
    ):
        raise RuntimeError(
            "browser-use action-error formatter changed under cap guard"
        )
    source_path = Path(
        inspect.getsourcefile(implementation) or ""
    ).resolve()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if source_sha256 != _BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256:
        raise RuntimeError(
            "browser-use agent/message_manager/service.py changed under "
            f"action-error cap guard: {source_sha256}"
        )

    constants = implementation.__code__.co_consts
    upstream_shape = (
        constants.count(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS) == 1
        and constants.count(
            _UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2
        ) == 1
        and constants.count(
            -(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2)
        ) == 1
        and constants.count(_EFFECTIVE_ACTION_ERROR_PROMPT_CHARS) == 0
        and constants.count(
            _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
        ) == 0
        and constants.count(
            -_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
        ) == 0
    )
    effective_shape = (
        constants.count(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS) == 0
        and constants.count(
            _UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2
        ) == 0
        and constants.count(
            -(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2)
        ) == 0
        and constants.count(_EFFECTIVE_ACTION_ERROR_PROMPT_CHARS) == 1
        and constants.count(
            _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
        ) == 1
        and constants.count(
            -_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
        ) == 1
    )
    if upstream_shape:
        replacements = {
            _UPSTREAM_ACTION_ERROR_PROMPT_CHARS:
                _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS,
            _UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2:
                _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
            -(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2):
                -_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
        }
        implementation.__code__ = implementation.__code__.replace(
            co_consts=tuple(
                replacements.get(value, value) for value in constants
            )
        )
        constants = implementation.__code__.co_consts
        effective_shape = (
            constants.count(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS) == 0
            and constants.count(
                _UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2
            ) == 0
            and constants.count(
                -(_UPSTREAM_ACTION_ERROR_PROMPT_CHARS // 2)
            ) == 0
            and constants.count(
                _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS
            ) == 1
            and constants.count(
                _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
            ) == 1
            and constants.count(
                -_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS
            ) == 1
        )
    if not effective_shape:
        raise RuntimeError(
            "browser-use action-error cap literals do not match either the "
            "guarded upstream or effective bytecode shape"
        )

    attestation = {
        "browser_use_version": actual_version,
        "source_sha256": source_sha256,
        "upstream_chars": _UPSTREAM_ACTION_ERROR_PROMPT_CHARS,
        "effective_chars": _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS,
        "effective_edge_chars": _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
    }
    _LOG.info(
        "AGENTARENA_ACTION_ERROR_CAP_PATCH version=%s source_sha256=%s "
        "upstream_chars=%s effective_chars=%s effective_edge_chars=%s",
        actual_version,
        source_sha256,
        _UPSTREAM_ACTION_ERROR_PROMPT_CHARS,
        _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS,
        _EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS,
    )
    return attestation


def _lossless_read_state_recovery_authorization_from_environment(
) -> dict[str, Any] | None:
    """Parse the create-only, singleton recovery authority.

    Absence is the only inactive state.  An empty, malformed, copied, or
    nonce-mismatched value fails closed so this recovery cannot silently
    become a general harness mode.
    """

    raw = os.environ.get(_LOSSLESS_READ_STATE_RECOVERY_ENV)
    if raw is None:
        return None

    def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if not isinstance(key, str) or key in value:
                raise ValueError("authorization has duplicate/non-string key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"authorization contains non-finite value {value}")

    try:
        authorization = json.loads(
            raw,
            object_pairs_hook=exact_object,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: malformed JSON"
        ) from exc

    exact_keys = {
        "schema",
        "mode",
        "campaign_uuid",
        "manifest_sha256",
        "amendment_sha256",
        "scheduler_sha256",
        "run_index",
        "run_id",
        "prior_attempt",
        "attempt",
        "prior_completion_sha256",
        "prior_trajectory_sha256",
        "cache_nonce_sha256",
    }
    if not isinstance(authorization, dict) or set(authorization) != exact_keys:
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: schema is not exact"
        )

    fixed = {
        "schema": _LOSSLESS_READ_STATE_RECOVERY_SCHEMA,
        "mode": _LOSSLESS_READ_STATE_RECOVERY_MODE,
        "campaign_uuid": _LOSSLESS_READ_STATE_RECOVERY_CAMPAIGN_UUID,
        "manifest_sha256": _LOSSLESS_READ_STATE_RECOVERY_MANIFEST_SHA256,
        "run_index": _LOSSLESS_READ_STATE_RECOVERY_RUN_INDEX,
        "run_id": _LOSSLESS_READ_STATE_RECOVERY_RUN_ID,
        "prior_attempt": _LOSSLESS_READ_STATE_RECOVERY_PRIOR_ATTEMPT,
        "prior_completion_sha256": (
            _LOSSLESS_READ_STATE_RECOVERY_PRIOR_COMPLETION_SHA256
        ),
        "prior_trajectory_sha256": (
            _LOSSLESS_READ_STATE_RECOVERY_PRIOR_TRAJECTORY_SHA256
        ),
    }
    if any(authorization.get(key) != value for key, value in fixed.items()):
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: "
            "authorization is not the frozen run-598 retry"
        )
    attempt = authorization.get("attempt")
    if type(attempt) is not int or attempt < 2:
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: "
            "attempt must be an integer >= 2"
        )
    sha_names = (
        "amendment_sha256",
        "scheduler_sha256",
        "cache_nonce_sha256",
    )
    if any(
        not isinstance(authorization.get(name), str)
        or re.fullmatch(r"[0-9a-f]{64}", authorization[name]) is None
        for name in sha_names
    ):
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: hash is malformed"
        )

    nonce = os.environ.get("AGENTARENA_CACHE_NONCE")
    expected_nonce = (
        "eight_env_leaderboard/"
        f"{_LOSSLESS_READ_STATE_RECOVERY_CAMPAIGN_UUID}/"
        f"{_LOSSLESS_READ_STATE_RECOVERY_MANIFEST_SHA256}/"
        f"{_LOSSLESS_READ_STATE_RECOVERY_RUN_ID}/attempt_{attempt}/"
        f"continuation-{authorization['amendment_sha256']}"
    )
    if nonce != expected_nonce:
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: "
            "cache nonce is not bound to the exact retry and amendment"
        )
    nonce_sha256 = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    if authorization["cache_nonce_sha256"] != nonce_sha256:
        raise RuntimeError(
            f"invalid {_LOSSLESS_READ_STATE_RECOVERY_ENV}: "
            "cache nonce digest differs"
        )
    return dict(authorization)


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class _LosslessReadStateRecoveryAudit:
    """Run-local proof that raw read-state crossings were restored exactly."""

    def __init__(self, authorization: Mapping[str, Any]):
        self.authorization = dict(authorization)
        self.complete = False
        self.error: str | None = "instance wrapper was not installed"
        self.installation: dict[str, Any] | None = None
        self.records: list[dict[str, Any]] = []

    def installed(self, attestation: Mapping[str, Any]) -> None:
        if self.installation is not None:
            raise RuntimeError("lossless read-state wrapper installed twice")
        self.installation = dict(attestation)
        self.complete = True
        self.error = None

    def fail(self, message: str) -> None:
        self.complete = False
        self.error = (
            f"{self.error}; {message}" if self.error else message
        )

    def observe(self, record: Mapping[str, Any]) -> None:
        if not self.complete:
            raise RuntimeError("cannot observe an incomplete recovery audit")
        self.records.append(dict(record))

    def snapshot(self) -> dict[str, Any]:
        raw_crossings = sum(
            int(record.get("raw_crossed") is True)
            for record in self.records
        )
        restored = sum(
            int(record.get("restored") is True)
            for record in self.records
        )
        return {
            "schema_version": 1,
            "active": True,
            "complete": self.complete,
            "error": self.error,
            "authorization": dict(self.authorization),
            "authorization_sha256": _json_sha256(self.authorization),
            "installation": (
                dict(self.installation)
                if self.installation is not None else None
            ),
            "call_count": len(self.records),
            "raw_crossing_count": raw_crossings,
            "restored_crossing_count": restored,
            "raw_max_chars": max(
                (int(record["raw_chars"]) for record in self.records),
                default=0,
            ),
            "effective_loss_touched_count": raw_crossings - restored,
            "records": [dict(record) for record in self.records],
        }


def _read_state_raw_rendering(result: list[Any] | None) -> str:
    rendered = ""
    read_state_index = 0
    for action_result in result or []:
        extracted = getattr(action_result, "extracted_content", None)
        if (
            getattr(
                action_result,
                "include_extracted_content_only_once",
                False,
            )
            and extracted
        ):
            rendered += (
                f"<read_state_{read_state_index}>\n{extracted}\n"
                f"</read_state_{read_state_index}>\n"
            )
            read_state_index += 1
    return rendered


def _read_state_non_target_snapshot(manager: Any) -> tuple[Any, ...]:
    """Identity/value snapshot of every neighbor the recovery must not edit."""

    state = manager.state
    images = state.read_state_images
    history = state.agent_history_items
    return (
        id(manager),
        type(manager),
        id(state),
        type(state),
        id(images),
        tuple((id(value), type(value)) for value in images),
        id(history),
        tuple(
            (
                id(item),
                type(item),
                getattr(item, "action_results", None),
            )
            for item in history
        ),
    )


def _install_lossless_read_state_recovery(
    agent: Any,
    collector: _LosslessReadStateRecoveryAudit,
) -> dict[str, Any]:
    """Install the run-598 recovery on one constructed manager instance."""

    actual_version = importlib_metadata.version("browser-use")
    if actual_version != _BROWSER_USE_VERSION:
        raise RuntimeError(
            "browser-use version changed under lossless read-state guard: "
            f"{actual_version!r} != {_BROWSER_USE_VERSION!r}"
        )

    from browser_use.agent.message_manager.service import MessageManager

    implementation = MessageManager._update_agent_history_description
    source_path = Path(inspect.getsourcefile(implementation) or "").resolve()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    method_source_sha256 = hashlib.sha256(
        inspect.getsource(implementation).encode("utf-8")
    ).hexdigest()
    code_sha256 = hashlib.sha256(implementation.__code__.co_code).hexdigest()
    signature = str(inspect.signature(implementation))
    if source_sha256 != _BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256:
        raise RuntimeError(
            "browser-use message-manager source changed under lossless "
            f"read-state guard: {source_sha256}"
        )
    if (
        method_source_sha256
        != _BROWSER_USE_MESSAGE_MANAGER_METHOD_SOURCE_SHA256
    ):
        raise RuntimeError(
            "browser-use read-state method source changed under guard: "
            f"{method_source_sha256}"
        )
    if code_sha256 != _BROWSER_USE_MESSAGE_MANAGER_METHOD_CODE_SHA256:
        raise RuntimeError(
            "browser-use read-state method bytecode changed under guard: "
            f"{code_sha256}"
        )
    if (
        getattr(implementation, "__qualname__", "")
        != "MessageManager._update_agent_history_description"
    ):
        raise RuntimeError(
            "browser-use read-state method qualname changed under guard"
        )
    if signature != _BROWSER_USE_MESSAGE_MANAGER_METHOD_SIGNATURE:
        raise RuntimeError(
            "browser-use read-state method signature changed under guard: "
            f"{signature}"
        )
    if implementation.__code__.co_consts != (
        _EFFECTIVE_MESSAGE_MANAGER_METHOD_CONSTANTS
    ):
        raise RuntimeError(
            "browser-use read-state method constants are not the exact "
            "post-action-error-lift shape"
        )

    manager = getattr(agent, "_message_manager", None)
    if type(manager) is not MessageManager:
        raise RuntimeError(
            "agent message manager is not the guarded browser-use class"
        )
    if "_update_agent_history_description" in manager.__dict__:
        raise RuntimeError(
            "agent message manager already has an instance method override"
        )
    upstream = manager._update_agent_history_description
    if getattr(upstream, "__self__", None) is not manager or getattr(
        upstream, "__func__", None
    ) is not implementation:
        raise RuntimeError(
            "cannot bind exact upstream read-state method to agent instance"
        )

    attestation = {
        "browser_use_version": actual_version,
        "source_sha256": source_sha256,
        "method_source_sha256": method_source_sha256,
        "method_code_sha256": code_sha256,
        "method_qualname": implementation.__qualname__,
        "method_signature": signature,
        "method_constants_sha256": _json_sha256(
            list(implementation.__code__.co_consts)
        ),
        "installation_scope": "single_message_manager_instance",
        "read_state_limit_chars": _READ_STATE_CONTENT_LIMIT_CHARS,
        "action_results_limit_behavior": "unchanged_upstream",
    }

    @wraps(upstream)
    def lossless_update(
        model_output: Any = None,
        result: list[Any] | None = None,
        step_info: Any = None,
    ) -> None:
        try:
            upstream(
                model_output=model_output,
                result=result,
                step_info=step_info,
            )
            raw = _read_state_raw_rendering(result)
            full = raw.strip("\n")
            raw_crossed = len(raw) > _READ_STATE_CONTENT_LIMIT_CHARS
            expected_upstream = (
                raw[:_READ_STATE_CONTENT_LIMIT_CHARS]
                + _READ_STATE_TRUNCATION_MARKER
                if raw_crossed else raw
            ).strip("\n")
            state = manager.state
            observed_upstream = state.read_state_description
            if observed_upstream != expected_upstream:
                raise RuntimeError(
                    "upstream read-state rendering differs from guarded "
                    "60k semantics"
                )

            non_target = _read_state_non_target_snapshot(manager)
            upstream_sha256 = hashlib.sha256(
                observed_upstream.encode("utf-8")
            ).hexdigest()
            restored = False
            if raw_crossed:
                state.read_state_description = full
                restored = True
            if _read_state_non_target_snapshot(manager) != non_target:
                raise RuntimeError(
                    "lossless read-state recovery changed action results, "
                    "images, history, or object classes"
                )
            effective = state.read_state_description
            if effective != full:
                raise RuntimeError(
                    "lossless read-state recovery did not restore exact text"
                )
            collector.observe({
                "call_index": len(collector.records) + 1,
                "step_number": (
                    getattr(step_info, "step_number", None)
                    if step_info is not None else None
                ),
                "raw_chars": len(raw),
                "raw_sha256": hashlib.sha256(
                    raw.encode("utf-8")
                ).hexdigest(),
                "raw_crossed": raw_crossed,
                "upstream_chars": len(observed_upstream),
                "upstream_sha256": upstream_sha256,
                "expected_upstream_sha256": hashlib.sha256(
                    expected_upstream.encode("utf-8")
                ).hexdigest(),
                "restored": restored,
                "effective_chars": len(effective),
                "effective_sha256": hashlib.sha256(
                    effective.encode("utf-8")
                ).hexdigest(),
                "effective_loss": effective != full,
                "history_items": len(state.agent_history_items),
                "read_state_images": len(state.read_state_images),
                "action_results_sha256": _json_sha256([
                    getattr(item, "action_results", None)
                    for item in state.agent_history_items
                ]),
            })
        except Exception as exc:
            collector.fail(f"{type(exc).__name__}: {exc}")
            raise

    manager._update_agent_history_description = lossless_update
    if (
        manager.__dict__.get("_update_agent_history_description")
        is not lossless_update
        or MessageManager._update_agent_history_description
        is not implementation
    ):
        del manager.__dict__["_update_agent_history_description"]
        raise RuntimeError(
            "lossless read-state wrapper did not remain instance-local"
        )
    collector.installed(attestation)
    _LOG.info(
        "AGENTARENA_LOSSLESS_READ_STATE_RECOVERY_INSTALLED "
        "run_index=%s run_id=%s attempt=%s amendment_sha256=%s",
        collector.authorization["run_index"],
        collector.authorization["run_id"],
        collector.authorization["attempt"],
        collector.authorization["amendment_sha256"],
    )
    return attestation


def _tools_with_lifted_extract_timeout(Tools):
    """Lift browser-use's hidden extract-LLM cap with exact upstream guards."""
    raw_timeout = os.environ.get("BROWSER_USE_EXTRACT_TIMEOUT_S", "7500")
    try:
        timeout_s = float(raw_timeout)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid BROWSER_USE_EXTRACT_TIMEOUT_S={raw_timeout!r}"
        ) from exc
    if timeout_s < 7500:
        raise RuntimeError(
            "BROWSER_USE_EXTRACT_TIMEOUT_S must be at least 7500 seconds"
        )
    actual_version = importlib_metadata.version("browser-use")
    if actual_version != _BROWSER_USE_VERSION:
        raise RuntimeError(
            "browser-use version changed under extract-timeout guard: "
            f"{actual_version!r} != {_BROWSER_USE_VERSION!r}"
        )

    tools = Tools()
    registered = tools.registry.registry.actions.get("extract")
    if registered is None:
        raise RuntimeError("browser-use extract action is missing")
    wrapper = registered.function
    implementations = [
        cell.cell_contents
        for cell in (wrapper.__closure__ or ())
        if inspect.iscoroutinefunction(cell.cell_contents)
        and getattr(cell.cell_contents, "__qualname__", "").endswith(
            "Tools.__init__.<locals>.extract"
        )
    ]
    if len(implementations) != 1:
        raise RuntimeError(
            "cannot uniquely resolve browser-use extract implementation"
        )
    implementation = implementations[0]
    source_path = Path(inspect.getsourcefile(implementation) or "").resolve()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if source_sha256 != _BROWSER_USE_TOOLS_SERVICE_SHA256:
        raise RuntimeError(
            "browser-use tools/service.py changed under extract-timeout guard: "
            f"{source_sha256}"
        )
    constants = implementation.__code__.co_consts
    if constants.count(_UPSTREAM_EXTRACT_TIMEOUT_S) != 1:
        raise RuntimeError(
            "browser-use extract timeout literal is not the expected single 120s "
            "constant"
        )
    implementation.__code__ = implementation.__code__.replace(
        co_consts=tuple(
            timeout_s if value == _UPSTREAM_EXTRACT_TIMEOUT_S else value
            for value in constants
        )
    )

    @wraps(wrapper)
    async def audited_extract(*args, **kwargs):
        started = time.monotonic()
        try:
            return await wrapper(*args, **kwargs)
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= timeout_s * 0.99:
                _LOG.error(
                    "AGENTARENA_EXTRACT_LLM_TIMEOUT_BOUND "
                    "elapsed_seconds=%.3f configured_seconds=%.3f",
                    elapsed,
                    timeout_s,
                )

    registered.function = audited_extract
    _LOG.info(
        "AGENTARENA_EXTRACT_TIMEOUT_PATCH version=%s source_sha256=%s "
        "upstream_seconds=%.1f configured_seconds=%.1f",
        actual_version,
        source_sha256,
        _UPSTREAM_EXTRACT_TIMEOUT_S,
        timeout_s,
    )
    return tools


def _wrapped_function_graph(root: Any) -> list[Any]:
    """Return all function objects reachable through normal wrapper links."""

    pending = [root]
    seen: set[int] = set()
    functions = []
    while pending:
        candidate = pending.pop()
        if not inspect.isfunction(candidate):
            continue
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        functions.append(candidate)
        wrapped = getattr(candidate, "__wrapped__", None)
        if inspect.isfunction(wrapped):
            pending.append(wrapped)
        for cell in candidate.__closure__ or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if inspect.isfunction(value):
                pending.append(value)
    return functions


def _lift_evaluate_serialization_backstop(tools: Any) -> dict[str, Any]:
    """Replace only browser-use's 20k serialization loss with a 64 MiB backstop.

    The final registry may have been wrapped by an optional scaffold extension.
    Resolve the guarded upstream implementation through wrapper links after
    that extension has prepared the tools, then patch its three exact
    serialization literals.  The independent 10k memory-routing threshold
    remains bytecode-identical; the common wrapper replaces every result that
    reaches it with a compact durable receipt before the context-cap audit
    observes the result.
    """

    actual_version = importlib_metadata.version("browser-use")
    if actual_version != _BROWSER_USE_VERSION:
        raise RuntimeError(
            "browser-use version changed under evaluate serialization guard: "
            f"{actual_version!r} != {_BROWSER_USE_VERSION!r}"
        )
    try:
        registered = tools.registry.registry.actions["evaluate"]
    except Exception as exc:
        raise RuntimeError(
            "browser-use evaluate action is missing after tool preparation"
        ) from exc

    implementations = []
    for candidate in _wrapped_function_graph(registered.function):
        if not getattr(candidate, "__qualname__", "").endswith(
            "Tools.__init__.<locals>.evaluate"
        ):
            continue
        source_name = inspect.getsourcefile(candidate)
        if not source_name:
            continue
        source_path = Path(source_name).resolve()
        try:
            source_sha256 = hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
        if source_sha256 == _BROWSER_USE_TOOLS_SERVICE_SHA256:
            implementations.append((candidate, source_sha256))
    if len(implementations) != 1:
        raise RuntimeError(
            "cannot uniquely resolve guarded browser-use evaluate "
            "implementation after tool preparation"
        )
    implementation, source_sha256 = implementations[0]

    def shapes(constants: tuple[Any, ...]) -> tuple[bool, bool]:
        memory_is_unchanged = (
            constants.count(_UPSTREAM_EVALUATE_MEMORY_CHARS) == 1
        )
        upstream = (
            memory_is_unchanged
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_CHARS) == 1
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_PREFIX_CHARS) == 1
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_MARKER) == 1
            and constants.count(_EVALUATE_SINGLE_MAX_CHARS) == 0
            and constants.count(_EVALUATE_SINGLE_PREFIX_CHARS) == 0
            and constants.count(_EVALUATE_SINGLE_MARKER) == 0
        )
        effective = (
            memory_is_unchanged
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_CHARS) == 0
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_PREFIX_CHARS) == 0
            and constants.count(_UPSTREAM_EVALUATE_OUTPUT_MARKER) == 0
            and constants.count(_EVALUATE_SINGLE_MAX_CHARS) == 1
            and constants.count(_EVALUATE_SINGLE_PREFIX_CHARS) == 1
            and constants.count(_EVALUATE_SINGLE_MARKER) == 1
        )
        return upstream, effective

    constants = implementation.__code__.co_consts
    upstream_shape, effective_shape = shapes(constants)
    if upstream_shape:
        replacements = {
            _UPSTREAM_EVALUATE_OUTPUT_CHARS:
                _EVALUATE_SINGLE_MAX_CHARS,
            _UPSTREAM_EVALUATE_OUTPUT_PREFIX_CHARS:
                _EVALUATE_SINGLE_PREFIX_CHARS,
            _UPSTREAM_EVALUATE_OUTPUT_MARKER:
                _EVALUATE_SINGLE_MARKER,
        }
        implementation.__code__ = implementation.__code__.replace(
            co_consts=tuple(
                replacements.get(value, value) for value in constants
            )
        )
        upstream_shape, effective_shape = shapes(
            implementation.__code__.co_consts
        )
    if upstream_shape or not effective_shape:
        raise RuntimeError(
            "browser-use evaluate serialization literals do not match "
            "either the guarded upstream or effective bytecode shape"
        )

    attestation = {
        "browser_use_version": actual_version,
        "source_sha256": source_sha256,
        "upstream_chars": _UPSTREAM_EVALUATE_OUTPUT_CHARS,
        "inline_chars": _EVALUATE_INLINE_CHARS,
        "single_max_chars": _EVALUATE_SINGLE_MAX_CHARS,
        "memory_chars": _UPSTREAM_EVALUATE_MEMORY_CHARS,
    }
    _LOG.info(
        "AGENTARENA_EVALUATE_SERIALIZATION_PATCH version=%s "
        "source_sha256=%s upstream_chars=%s single_max_chars=%s "
        "memory_chars=%s",
        actual_version,
        source_sha256,
        _UPSTREAM_EVALUATE_OUTPUT_CHARS,
        _EVALUATE_SINGLE_MAX_CHARS,
        _UPSTREAM_EVALUATE_MEMORY_CHARS,
    )
    return attestation


def _updated_action_result(
    result: Any,
    *,
    update: dict[str, Any],
) -> Any:
    model_copy = getattr(result, "model_copy", None)
    if not callable(model_copy):
        raise RuntimeError(
            "evaluate returned a non-ActionResult large payload"
        )
    return model_copy(update=update)


def _install_evaluate_result_spill(
    tools: Any,
    store: _EvaluateResultStore,
) -> dict[str, Any]:
    """Install equal-arm lossless large-evaluate handling and one inspector."""

    from browser_use.agent.views import ActionResult

    attestation = _lift_evaluate_serialization_backstop(tools)
    registry = tools.registry.registry.actions
    if "inspect_evaluate_result" in registry:
        raise RuntimeError(
            "inspect_evaluate_result action already exists after preparation"
        )
    registered = registry.get("evaluate")
    if registered is None:
        raise RuntimeError("evaluate action disappeared during preparation")
    original_evaluate = registered.function

    def metadata_with(
        result: Any,
        audit_record: dict[str, Any],
    ) -> dict[str, Any]:
        raw = _field(result, "metadata") or {}
        if not isinstance(raw, Mapping):
            raise RuntimeError("evaluate metadata is not a mapping")
        return {
            **dict(raw),
            "agentarena_evaluate_result": audit_record,
        }

    def compact_failure(
        result: Any,
        *,
        kind: str,
        message: str,
        audit_record: dict[str, Any],
    ) -> Any:
        receipt = json.dumps(
            {
                "kind": kind,
                "message": message,
                **audit_record,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return _updated_action_result(
            result,
            update={
                "error": message,
                "extracted_content": receipt,
                "long_term_memory": receipt,
                "include_extracted_content_only_once": False,
                "metadata": metadata_with(result, audit_record),
            },
        )

    @wraps(original_evaluate)
    async def spilled_evaluate(*args, **kwargs):
        result = await original_evaluate(*args, **kwargs)
        extracted = _field(result, "extracted_content")
        if extracted is None:
            return result
        text = str(extracted)
        if len(text) <= _EVALUATE_INLINE_CHARS:
            store.note_serialized(
                len(text),
                bound_touched=False,
            )
            return result
        # The lifted upstream prefix plus marker is deliberately max+1
        # characters, so length alone is a collision-free bound signal.
        single_bound_touched = len(text) > _EVALUATE_SINGLE_MAX_CHARS
        store.note_serialized(
            (
                max(len(text), _EVALUATE_SINGLE_MAX_CHARS + 1)
                if single_bound_touched else len(text)
            ),
            bound_touched=single_bound_touched,
        )
        if single_bound_touched:
            audit_record = {
                "single_max_chars": _EVALUATE_SINGLE_MAX_CHARS,
                "minimum_original_chars":
                    _EVALUATE_SINGLE_MAX_CHARS + 1,
            }
            return compact_failure(
                result,
                kind="agentarena_evaluate_result_unavailable",
                message=(
                    "AGENTARENA_EVALUATE_SINGLE_BOUND: JavaScript result "
                    "exceeded the 67108864-character safety backstop; the "
                    "run cannot certify a lossless result."
                ),
                audit_record=audit_record,
            )
        try:
            record = store.add(text)
        except _EvaluateResultStoreCapacityError as exc:
            return compact_failure(
                result,
                kind="agentarena_evaluate_result_unavailable",
                message=(
                    "AGENTARENA_EVALUATE_STORE_BOUND: lossless JavaScript "
                    f"result storage failed: {exc}"
                ),
                audit_record={
                    "chars": len(text),
                    "store_max_bytes": store.max_bytes,
                    "store_max_responses": store.max_responses,
                },
            )
        except Exception as exc:
            store.note_integrity_failure()
            _LOG.exception(
                "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE"
            )
            return compact_failure(
                result,
                kind="agentarena_evaluate_result_unavailable",
                message=(
                    "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE: "
                    "lossless JavaScript result storage could not be "
                    "certified."
                ),
                audit_record={
                    "chars": len(text),
                    "exception_type": type(exc).__name__,
                },
            )
        receipt_record = {
            "kind": "agentarena_evaluate_result",
            **record,
            "inspect_action": "inspect_evaluate_result",
            "operations": ["stat", "list", "search", "read"],
            "head_preview": text[:_EVALUATE_RECEIPT_PREVIEW_CHARS],
            "tail_preview": text[-_EVALUATE_RECEIPT_PREVIEW_CHARS:],
            "head_preview_chars": min(
                len(text), _EVALUATE_RECEIPT_PREVIEW_CHARS
            ),
            "tail_preview_chars": min(
                len(text), _EVALUATE_RECEIPT_PREVIEW_CHARS
            ),
        }
        receipt = json.dumps(
            receipt_record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(receipt) >= _UPSTREAM_EVALUATE_MEMORY_CHARS:
            raise RuntimeError(
                "evaluate-result receipt unexpectedly exceeds memory routing"
            )
        return _updated_action_result(
            result,
            update={
                "extracted_content": receipt,
                "long_term_memory": receipt,
                "include_extracted_content_only_once": False,
                "metadata": metadata_with(result, receipt_record),
            },
        )

    registered.function = spilled_evaluate

    @tools.action(
        "Inspect a losslessly preserved JavaScript result by opaque result_id. "
        "Operations: stat metadata, list IDs, search exact text offsets, or "
        "read a character slice.",
        param_model=_InspectEvaluateResult,
        terminates_sequence=True,
    )
    async def inspect_evaluate_result(params: _InspectEvaluateResult):
        try:
            if params.operation == "stat":
                if not params.result_id:
                    raise ValueError("stat requires result_id")
                payload = store.stat(params.result_id)
                memory = f"Evaluate result metadata: {params.result_id}"
            elif params.operation == "list":
                payload = store.list_records(params.start, params.limit)
                memory = (
                    "Listed preserved evaluate-result IDs at "
                    f"offset {params.start}"
                )
            elif params.operation == "search":
                if not params.result_id:
                    raise ValueError("search requires result_id")
                payload = store.search(
                    params.result_id,
                    params.needle,
                    start=params.start,
                    max_hits=params.max_hits,
                )
                memory = (
                    f"Searched preserved evaluate result "
                    f"{params.result_id}"
                )
            else:
                if not params.result_id:
                    raise ValueError("read requires result_id")
                metadata = store.stat(params.result_id)
                text = store.read_text(params.result_id)
                chunk = text[
                    params.start:params.start + params.length
                ]
                header = json.dumps(
                    {
                        "result_id": params.result_id,
                        "start": params.start,
                        "end": params.start + len(chunk),
                        "chars": metadata["chars"],
                        "sha256": metadata["sha256"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                return ActionResult(
                    extracted_content=f"{header}\n{chunk}",
                    long_term_memory=(
                        "Read preserved evaluate result "
                        f"{params.result_id} characters "
                        f"{params.start}:{params.start + len(chunk)}"
                    ),
                    include_extracted_content_only_once=True,
                )
            return ActionResult(
                extracted_content=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                long_term_memory=memory,
                include_extracted_content_only_once=True,
            )
        except (_EvaluateResultStoreIntegrityError, OSError) as exc:
            store.note_integrity_failure()
            _LOG.exception(
                "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE"
            )
            return ActionResult(
                error=(
                    "evaluate-result inspection failed: "
                    "run-local storage integrity could not be certified "
                    f"({type(exc).__name__})"
                )
            )
        except Exception as exc:
            return ActionResult(
                error=(
                    "evaluate-result inspection failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            )

    return attestation


@SCAFFOLDS.register("browseruse")
class BrowserUseScaffold(Scaffold):
    name = "browseruse"

    def supports(self, model: ModelSpec) -> tuple[bool, str]:
        return True, ""

    def run(self, ctx: RunContext) -> RawTrajectory:
        return asyncio.run(_run(ctx))


def _img_bytes(shot) -> Optional[bytes]:
    if not shot:
        return None
    if isinstance(shot, (bytes, bytearray)):
        return bytes(shot)
    if isinstance(shot, str):
        s = shot.split(",", 1)[1] if shot.startswith("data:") else shot
        try:
            return base64.b64decode(s)
        except Exception:
            p = Path(shot)
            return p.read_bytes() if p.exists() else None
    return None


def _history_to_steps(history) -> tuple[list[Step], int]:
    """Preserve browser-use's decision-step boundaries in the shared trajectory.

    ``AgentHistoryList.model_actions()`` deliberately flattens the action lists
    emitted by each model turn.  Indexing that flattened list against
    ``model_thoughts()``, ``urls()``, and ``screenshots()`` (which are all
    turn-level) misaligns the trace and inflates ``num_steps``.  Keep one shared
    ``Step`` per browser-use history item and serialize that turn's packed action
    list into the existing string field.  The separate returned count records
    how many atomic tool actions were executed.
    """
    steps: list[Step] = []
    tool_actions = 0
    for index, item in enumerate(getattr(history, "history", ()) or (), start=1):
        output = getattr(item, "model_output", None)
        actions: list[dict] = []
        if output is not None:
            raw_actions = list(getattr(output, "action", ()) or ())
            interacted = list(getattr(item.state, "interacted_element", ()) or ())
            if len(interacted) < len(raw_actions):
                interacted.extend([None] * (len(raw_actions) - len(interacted)))
            for action, element in zip(raw_actions, interacted):
                value = action.model_dump(exclude_none=True, mode="json")
                if element is not None:
                    value["interacted_element"] = element
                actions.append(value)
            tool_actions += len(raw_actions)
            brain = getattr(output, "current_state", None)
            reasoning = str(brain) if brain is not None else str(output)
        else:
            reasoning = ""
        shot = None
        try:
            shot = item.state.get_screenshot()
        except Exception:
            pass
        steps.append(
            Step(
                index=index,
                action=json.dumps(actions, ensure_ascii=False, default=str),
                reasoning=reasoning,
                url=str(getattr(item.state, "url", "") or ""),
                screenshot=_img_bytes(shot),
            )
        )
    return steps, tool_actions


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


_REPLACE_FILE_SAFETY_AUDIT_SCHEMA_VERSION = 1


class _ReplaceFileSafetyCollector:
    """Exact observations from the recursive-amplification guard."""

    def __init__(self) -> None:
        self.installation_count = 0
        self.invocations = 0
        self.checked_invocations = 0
        self.passthrough_invocations = 0
        self.rejected_invocations = 0
        self.passed_invocations = 0
        self.max_current_chars = 0
        self.max_current_matches = 0
        self.max_replacement_matches = 0
        self.trigger_records: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def installed(self) -> None:
        self.installation_count += 1
        if self.installation_count != 1:
            self.fail(
                "replace_file recursive-amplification guard installed twice"
            )

    def invoked(self) -> None:
        self.invocations += 1

    def passthrough(self) -> None:
        self.passthrough_invocations += 1

    def checked(
        self,
        *,
        resolved_file: str,
        current_chars: int,
        old_str_chars: int,
        current_matches: int,
        replacement_matches: int,
    ) -> dict[str, Any] | None:
        self.checked_invocations += 1
        self.max_current_chars = max(
            self.max_current_chars, current_chars
        )
        self.max_current_matches = max(
            self.max_current_matches,
            current_matches,
        )
        self.max_replacement_matches = max(
            self.max_replacement_matches,
            replacement_matches,
        )
        if not (current_matches > 1 and replacement_matches > 1):
            self.passed_invocations += 1
            return None
        self.rejected_invocations += 1
        record = {
            "trigger_index": self.rejected_invocations,
            "reason": "recursive_all_sites_amplification",
            "resolved_file": resolved_file,
            "current_chars": current_chars,
            "old_str_chars": old_str_chars,
            "current_non_overlapping_matches": current_matches,
            "replacement_non_overlapping_matches": replacement_matches,
            "file_unchanged": True,
            "agent_result": "error",
        }
        self.trigger_records.append(record)
        return record

    def fail(self, message: str) -> None:
        self.errors.append(str(message))

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": _REPLACE_FILE_SAFETY_AUDIT_SCHEMA_VERSION,
            "installation_count": self.installation_count,
            "invocations": self.invocations,
            "checked_invocations": self.checked_invocations,
            "passthrough_invocations": self.passthrough_invocations,
            "rejected_invocations": self.rejected_invocations,
            "passed_invocations": self.passed_invocations,
            "max_current_chars": self.max_current_chars,
            "max_current_matches": self.max_current_matches,
            "max_replacement_matches": self.max_replacement_matches,
            "trigger_records": [
                dict(record) for record in self.trigger_records
            ],
            "errors": list(self.errors),
        }


def _install_replace_file_recursive_amplification_guard(
    tools: Any,
    collector: _ReplaceFileSafetyCollector,
) -> dict[str, Any]:
    """Reject only recursive all-sites amplification before allocation.

    ``str.count`` and ``str.replace`` share non-overlapping match semantics.
    Ordinary zero-match, multi-site, and self-containing replacements pass
    through byte-for-byte.  The guard triggers only when the current file has
    multiple replacement sites and the replacement itself contains multiple
    copies of the search text: the exact recursive pattern that multiplies
    sites geometrically.  A rejection is visible and leaves the file intact.
    """

    from browser_use.agent.views import ActionResult

    actual_version = importlib_metadata.version("browser-use")
    if actual_version != _BROWSER_USE_VERSION:
        raise RuntimeError(
            "browser-use version changed under replace_file recursive-"
            "amplification "
            "guard: "
            f"{actual_version!r} != {_BROWSER_USE_VERSION!r}"
        )
    source_hashes = {}
    for module_name, expected in (
        (
            "browser_use.tools.service",
            _BROWSER_USE_TOOLS_SERVICE_SHA256,
        ),
        (
            "browser_use.filesystem.file_system",
            _BROWSER_USE_FILE_SYSTEM_SHA256,
        ),
    ):
        module = __import__(module_name, fromlist=["__file__"])
        source_path = Path(module.__file__).resolve()
        observed = hashlib.sha256(source_path.read_bytes()).hexdigest()
        source_hashes[module_name] = observed
        if observed != expected:
            raise RuntimeError(
                f"{module_name} changed under replace_file recursive-"
                "amplification "
                "guard: "
                f"{observed} != {expected}"
            )

    try:
        registered = tools.registry.registry.actions["replace_file"]
    except Exception as exc:
        raise RuntimeError(
            "browser-use replace_file action is missing after tool preparation"
        ) from exc
    original_replace = registered.function

    @wraps(original_replace)
    async def guarded_replace(*args, **kwargs):
        collector.invoked()
        params = kwargs.get("params")
        file_system = kwargs.get("file_system")
        old_str = _field(params, "old_str")
        new_str = _field(params, "new_str")
        file_name = _field(params, "file_name")

        # Preserve upstream validation/not-found/empty-search behavior.  None
        # here means the existing-file targeted-edit path cannot be reached.
        if file_system is None or not old_str:
            collector.passthrough()
            return await original_replace(*args, **kwargs)
        if not all(
            type(value) is str
            for value in (file_name, old_str, new_str)
        ):
            collector.fail(
                "replace_file received non-string validated parameters"
            )
            raise RuntimeError(
                "replace_file safety preflight could not certify parameters"
            )

        try:
            resolved, _was_sanitized = file_system._resolve_filename(
                file_name
            )
            if not file_system._is_valid_filename(resolved):
                collector.passthrough()
                return await original_replace(*args, **kwargs)
            file_obj = file_system.files.get(resolved)
            if file_obj is None:
                collector.passthrough()
                return await original_replace(*args, **kwargs)
            content = file_obj.read()
            if type(content) is not str:
                raise TypeError("file content is not a string")
            current_matches = content.count(old_str)
            replacement_matches = new_str.count(old_str)
        except Exception as exc:
            collector.fail(
                "replace_file safety preflight failed: "
                f"{type(exc).__name__}: {exc}"
            )
            raise RuntimeError(
                "replace_file safety preflight could not certify the "
                "match count; file was not changed"
            ) from exc

        trigger = collector.checked(
            resolved_file=resolved,
            current_chars=len(content),
            old_str_chars=len(old_str),
            current_matches=current_matches,
            replacement_matches=replacement_matches,
        )
        if trigger is None:
            return await original_replace(*args, **kwargs)

        message = (
            "replace_file blocked recursive amplification: old_str matches "
            f"{current_matches} locations in {resolved} and occurs "
            f"{replacement_matches} times inside new_str; replacing every "
            "site would multiply the same search text across all sites. The "
            "file was not changed. Use more surrounding context in old_str "
            "to identify one target, or use replacement text that does not "
            "repeat old_str."
        )
        _LOG.warning(
            "AGENTARENA_REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD "
            "resolved_file=%s current_chars=%s current_matches=%s "
            "replacement_matches=%s",
            resolved,
            len(content),
            current_matches,
            replacement_matches,
        )
        return ActionResult(
            error=message,
            metadata={"agentarena_replace_file_guard": dict(trigger)},
        )

    registered.function = guarded_replace
    collector.installed()
    attestation = {
        **_REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION,
        "observed_tools_service_sha256": source_hashes[
            "browser_use.tools.service"
        ],
        "observed_file_system_sha256": source_hashes[
            "browser_use.filesystem.file_system"
        ],
    }
    _LOG.info(
        "AGENTARENA_REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_INSTALLED "
        "version=%s tools_sha256=%s "
        "file_system_sha256=%s",
        actual_version,
        source_hashes["browser_use.tools.service"],
        source_hashes["browser_use.filesystem.file_system"],
    )
    return attestation


def _action_mapping(action: Any) -> Mapping[str, Any]:
    if isinstance(action, Mapping):
        return action
    model_dump = getattr(action, "model_dump", None)
    if not callable(model_dump):
        raise TypeError(
            f"action {type(action).__name__} is neither a mapping nor dumpable"
        )
    dumped = model_dump(exclude_none=True, mode="json")
    if not isinstance(dumped, Mapping):
        raise TypeError(
            f"action {type(action).__name__} dumped to "
            f"{type(dumped).__name__}, not a mapping"
        )
    return dumped


class _ContextCapToolCollector:
    """Run-local observations captured before upstream tools discard detail."""

    def __init__(self) -> None:
        self.observations: dict[str, list[tuple[int, bool, bool]]] = {
            name: [] for name in _CONTEXT_CAPS
        }
        self.errors: list[str] = []

    def observe(
        self,
        name: str,
        observed: int,
        *,
        touched: bool | None = None,
        lower_bound: bool = False,
    ) -> None:
        configured, comparison = _CONTEXT_CAPS[name]
        if touched is None:
            touched = (
                observed > configured
                if comparison == ">"
                else observed >= configured
            )
        self.observations[name].append(
            (max(0, int(observed)), bool(touched), bool(lower_bound))
        )

    def fail(self, message: str) -> None:
        self.errors.append(str(message))


_ACTION_ERROR_AUDIT_SCHEMA_VERSION = 1
_ACTION_ERROR_PROVENANCE_ATTRIBUTE = (
    "_agentarena_action_error_provenance_collector"
)


def _is_agent_output_validation_error(error: BaseException) -> bool:
    """Identify model-output validation by exception provenance only.

    Provider adapters wrap Pydantic validation failures with ``raise ... from
    error``.  Following only that explicit ``__cause__`` chain is important:
    ``__context__`` may instead point at an earlier primary-model failure while
    the fallback's final exception is an unrelated provider or infrastructure
    error.  Exact title matching also keeps action-parameter/tool validation in
    the conservative unknown bucket.
    """

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, ValidationError)
            and current.title == "AgentOutput"
        ):
            return True
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return False


class _ActionErrorProvenanceCollector:
    """Run-local identity labels for errors created by ``_handle_step_error``.

    Labels never enter ``ActionResult`` or an agent-facing message.  Holding the
    exact result object also makes a copied, reconstructed, or text-spoofed
    result fail closed rather than inherit a model-validation classification.
    """

    def __init__(self) -> None:
        self.agent_output_validation_results: dict[int, Any] = {}
        self.errors: list[str] = []

    def mark_agent_output_validation(self, result: Any) -> None:
        error = _field(result, "error")
        if error is None:
            self.fail(
                "agent-output validation handler produced no error result"
            )
            return
        identity = id(result)
        existing = self.agent_output_validation_results.get(identity)
        if existing is not None and existing is not result:
            self.fail("action-error result identity collision")
            return
        if existing is result:
            self.fail("action-error result was provenance-marked twice")
            return
        self.agent_output_validation_results[identity] = result

    def is_agent_output_validation(self, result: Any) -> bool:
        return (
            self.agent_output_validation_results.get(id(result)) is result
        )

    def fail(self, message: str) -> None:
        self.errors.append(str(message))


def _install_action_error_provenance_audit(
    agent: Any,
    collector: _ActionErrorProvenanceCollector,
) -> None:
    """Wrap one agent instance without changing its error handling or output."""

    installed = getattr(agent, _ACTION_ERROR_PROVENANCE_ATTRIBUTE, None)
    if installed is collector:
        return
    if installed is not None:
        collector.fail(
            "agent already has a different action-error provenance collector"
        )
        return
    original = getattr(agent, "_handle_step_error", None)
    if not callable(original):
        collector.fail("Agent._handle_step_error is unavailable")
        return

    @wraps(original)
    async def audited_handle_step_error(error: BaseException):
        model_validation = _is_agent_output_validation_error(error)
        try:
            returned = await original(error)
        except BaseException:
            if model_validation:
                collector.fail(
                    "Agent._handle_step_error raised before provenance could "
                    "be bound"
                )
            raise
        if model_validation:
            results = list(
                getattr(getattr(agent, "state", None), "last_result", ())
                or ()
            )
            if len(results) != 1:
                collector.fail(
                    "agent-output validation handler did not produce exactly "
                    "one ActionResult"
                )
            else:
                collector.mark_agent_output_validation(results[0])
        return returned

    setattr(agent, "_handle_step_error", audited_handle_step_error)
    setattr(agent, _ACTION_ERROR_PROVENANCE_ATTRIBUTE, collector)


def _empty_action_error_bucket() -> dict[str, int]:
    return {"count": 0, "over_cap_count": 0, "max_chars": 0}


def _empty_action_error_audit(
    *,
    complete: bool,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": _ACTION_ERROR_AUDIT_SCHEMA_VERSION,
        "complete": bool(complete),
        "error": error,
        "all": _empty_action_error_bucket(),
        "agent_output_validation": _empty_action_error_bucket(),
        "other_or_unknown": _empty_action_error_bucket(),
    }


def _action_error_audit_before_agent_construction() -> dict[str, Any]:
    """Certify the action-error surface as known-empty before ``Agent(...)``."""

    return _empty_action_error_audit(complete=True)


def _action_error_audit(
    history: Any,
    collector: _ActionErrorProvenanceCollector | None,
) -> dict[str, Any]:
    """Inventory action errors and split only exact provenance-marked results."""

    audit = _empty_action_error_audit(complete=True)
    errors: list[str] = []
    if collector is None:
        errors.append("action-error provenance collector is unavailable")
    else:
        errors.extend(collector.errors)
    if history is None:
        errors.append("AgentHistory unavailable")
        audit["complete"] = False
        audit["error"] = "; ".join(dict.fromkeys(errors))
        return audit

    try:
        raw_items = getattr(history, "history")
        if raw_items is None:
            raise AttributeError("AgentHistory.history unavailable")
        items = list(raw_items)
    except Exception as exc:  # noqa: BLE001 - audit must fail closed
        errors.append(
            f"AgentHistory unreadable: {type(exc).__name__}: {exc}"
        )
        audit["complete"] = False
        audit["error"] = "; ".join(dict.fromkeys(errors))
        return audit

    marked_occurrences: dict[int, int] = {}

    def observe(bucket_name: str, chars: int) -> None:
        bucket = audit[bucket_name]
        bucket["count"] += 1
        bucket["max_chars"] = max(bucket["max_chars"], chars)
        if chars > _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS:
            bucket["over_cap_count"] += 1

    try:
        for item in items:
            for result in list(_field(item, "result", ()) or ()):
                raw_error = _field(result, "error")
                if raw_error is None:
                    continue
                chars = len(str(raw_error))
                observe("all", chars)
                known = bool(
                    collector is not None
                    and collector.is_agent_output_validation(result)
                )
                bucket_name = (
                    "agent_output_validation"
                    if known else "other_or_unknown"
                )
                observe(bucket_name, chars)
                if known:
                    identity = id(result)
                    marked_occurrences[identity] = (
                        marked_occurrences.get(identity, 0) + 1
                    )
    except Exception as exc:  # noqa: BLE001 - audit must fail closed
        errors.append(
            f"AgentHistory action errors unreadable: "
            f"{type(exc).__name__}: {exc}"
        )

    if collector is not None:
        expected = set(collector.agent_output_validation_results)
        observed = set(marked_occurrences)
        missing = expected - observed
        unexpected = observed - expected
        duplicated = sorted(
            identity for identity, count in marked_occurrences.items()
            if count != 1
        )
        if missing:
            errors.append(
                f"{len(missing)} provenance-marked ActionResult(s) absent "
                "from AgentHistory"
            )
        if unexpected:
            errors.append(
                f"{len(unexpected)} unexpected provenance identity/identities"
            )
        if duplicated:
            errors.append(
                f"{len(duplicated)} provenance-marked ActionResult(s) "
                "occurred more than once"
            )

    if errors:
        audit["complete"] = False
        audit["error"] = "; ".join(dict.fromkeys(errors))
    return audit


def _empty_context_cap_audit(
    *,
    complete: bool,
    error: str | None = None,
    history_state: str | None = None,
    measurement_basis: str | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "schema_version": 1,
        "complete": complete,
        "limits": {
            name: {
                "configured": configured,
                "touched_count": 0,
                "max_observed": 0,
            }
            for name, (configured, _comparison) in _CONTEXT_CAPS.items()
        },
        "history_items": 0,
    }
    if error is not None:
        audit["error"] = error
    if history_state is not None:
        audit["history_state"] = history_state
    if measurement_basis is not None:
        audit["measurement_basis"] = measurement_basis
    return audit


def _context_cap_audit_before_agent_construction() -> dict[str, Any]:
    """Certify the known-empty context-cap surface before ``Agent(...)``.

    No Browser Use message manager, history item, model step, or agent tool
    execution exists before construction starts.  This is distinct from an
    unavailable history after construction, which remains fail-closed.
    """
    return _empty_context_cap_audit(
        complete=True,
        history_state="not_created",
        measurement_basis="failure_before_agent_construction",
    )


def _install_context_cap_tool_audit(
    tools: Any,
    collector: _ContextCapToolCollector,
) -> None:
    """Wrap the final tool registry used by either browser-use scaffold arm."""

    try:
        registry = tools.registry.registry.actions
        extract_registration = registry["extract"]
        evaluate_registration = registry["evaluate"]
    except Exception as exc:  # noqa: BLE001 - absence must be reported, not hidden
        collector.fail(
            f"context-cap tool registration unavailable: "
            f"{type(exc).__name__}: {exc}"
        )
        return

    original_evaluate = evaluate_registration.function

    @wraps(original_evaluate)
    async def audited_evaluate(*args, **kwargs):
        result = await original_evaluate(*args, **kwargs)
        try:
            extracted = _field(result, "extracted_content")
            if extracted is not None:
                collector.observe(
                    "evaluate_memory_chars",
                    len(str(extracted)),
                )
        except Exception as exc:  # noqa: BLE001 - never perturb the agent
            collector.fail(
                f"evaluate cap audit failed: {type(exc).__name__}: {exc}"
            )
        return result

    evaluate_registration.function = audited_evaluate

    original_extract = extract_registration.function

    @wraps(original_extract)
    async def audited_extract(*args, **kwargs):
        params = kwargs.get("params")
        try:
            already_collected = _field(
                params,
                "already_collected",
                (),
            ) or ()
            collector.observe(
                "extract_already_collected_items",
                len(already_collected),
            )
        except Exception as exc:  # noqa: BLE001 - never perturb the agent
            collector.fail(
                f"extract input cap audit failed: {type(exc).__name__}: {exc}"
            )

        # The free-text extract result omits content_stats.  Observe the exact
        # markdown/chunk used by this invocation by temporarily wrapping the
        # local function that the upstream implementation imports at call time.
        markdown_module = None
        original_clean_markdown = None
        observed_clean_markdown = None
        try:
            from browser_use.dom import markdown_extractor as markdown_module

            original_clean_markdown = markdown_module.extract_clean_markdown

            async def observed_clean_markdown(*clean_args, **clean_kwargs):
                content, content_stats = await original_clean_markdown(
                    *clean_args,
                    **clean_kwargs,
                )
                try:
                    start_from_char = int(
                        _field(params, "start_from_char", 0) or 0
                    )
                    chunks = markdown_module.chunk_markdown_by_structure(
                        content,
                        max_chunk_chars=100000,
                        start_from_char=start_from_char,
                    )
                    if chunks:
                        remaining_chars = max(
                            0,
                            int(content_stats["final_filtered_chars"])
                            - start_from_char,
                        )
                        collector.observe(
                            "extract_page_chunk_chars",
                            remaining_chars,
                            touched=bool(chunks[0].has_more),
                        )
                    else:
                        collector.observe(
                            "extract_page_chunk_chars",
                            0,
                            touched=False,
                        )
                except Exception as exc:  # noqa: BLE001 - preserve tool result
                    collector.fail(
                        "extract page-chunk cap audit failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                return content, content_stats

            markdown_module.extract_clean_markdown = observed_clean_markdown
        except Exception as exc:  # noqa: BLE001 - wrapper remains transparent
            collector.fail(
                f"extract markdown audit unavailable: "
                f"{type(exc).__name__}: {exc}"
            )

        try:
            result = await original_extract(*args, **kwargs)
        finally:
            if (
                markdown_module is not None
                and original_clean_markdown is not None
                and observed_clean_markdown is not None
            ):
                if (
                    markdown_module.extract_clean_markdown
                    is observed_clean_markdown
                ):
                    markdown_module.extract_clean_markdown = (
                        original_clean_markdown
                    )
                else:
                    collector.fail(
                        "extract markdown audit wrapper changed concurrently"
                    )

        try:
            extracted = _field(result, "extracted_content")
            if extracted is not None:
                collector.observe("extract_memory_chars", len(str(extracted)))
        except Exception as exc:  # noqa: BLE001 - never perturb the agent
            collector.fail(
                f"extract result cap audit failed: {type(exc).__name__}: {exc}"
            )
        return result

    extract_registration.function = audited_extract


def _context_cap_audit(
    history: Any,
    *,
    tool_collector: _ContextCapToolCollector | None = None,
) -> dict[str, Any]:
    """Return a fail-closed, exact inventory of all browser-use context caps.

    The helper remains pure: callers may supply a run-local collector containing
    detail captured immediately before upstream tools discard it.  With no
    collector, it derives the same observations from an ``AgentHistory`` where
    possible, which keeps the contract unit-testable with simple fakes.
    """

    if history is None:
        return _empty_context_cap_audit(
            complete=False,
            error="AgentHistory unavailable",
        )

    try:
        raw_items = getattr(history, "history")
        if raw_items is None:
            return _empty_context_cap_audit(
                complete=False,
                error="AgentHistory.history unavailable",
            )
        items = list(raw_items)
    except Exception as exc:  # noqa: BLE001 - diagnostic must fail closed
        return _empty_context_cap_audit(
            complete=False,
            error=f"AgentHistory unreadable: {type(exc).__name__}: {exc}",
        )

    audit = _empty_context_cap_audit(complete=True)
    audit["history_items"] = len(items)
    incomplete_reasons: list[str] = []

    def observe(
        name: str,
        observed: int,
        *,
        touched: bool | None = None,
        lower_bound: bool = False,
    ) -> None:
        record = audit["limits"][name]
        observed = max(0, int(observed))
        record["max_observed"] = max(record["max_observed"], observed)
        configured, comparison = _CONTEXT_CAPS[name]
        if touched is None:
            touched = (
                observed > configured
                if comparison == ">"
                else observed >= configured
            )
        if touched:
            record["touched_count"] += 1
        if lower_bound:
            record["observation_kind"] = "lower_bound_if_touched"
            record["max_observed_lower_bound"] = max(
                record.get("max_observed_lower_bound", 0),
                observed,
            )

    def result_context_lengths(results: list[Any]) -> tuple[int, int]:
        read_state = ""
        action_results = ""
        read_index = 0
        for result in results:
            extracted = _field(result, "extracted_content")
            include_once = bool(
                _field(result, "include_extracted_content_only_once", False)
            )
            long_term = _field(result, "long_term_memory")
            error = _field(result, "error")
            if include_once and extracted:
                read_state += (
                    f"<read_state_{read_index}>\n{extracted}\n"
                    f"</read_state_{read_index}>\n"
                )
                read_index += 1
            if long_term:
                action_results += f"{long_term}\n"
            elif extracted and not include_once:
                action_results += f"{extracted}\n"
            if error:
                error = str(error)
                error_text = (
                    error[:_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS]
                    + "......"
                    + error[-_EFFECTIVE_ACTION_ERROR_PROMPT_EDGE_CHARS:]
                    if len(error) > _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS
                    else error
                )
                action_results += f"{error_text}\n"
        if action_results:
            action_results = f"Result\n{action_results}".strip("\n")
        return len(read_state), len(action_results)

    try:
        # Each stored state message proves that the preceding item's raw
        # results were transformed into the two capped prompt channels.
        for previous_item in items[:-1]:
            previous_results = list(
                _field(previous_item, "result", ()) or ()
            )
            read_chars, action_chars = result_context_lengths(
                previous_results
            )
            observe("read_state_chars", read_chars)
            observe("action_results_chars", action_chars)

        for item in items:
            state_message = _field(item, "state_message")
            if state_message is None:
                incomplete_reasons.append(
                    "AgentHistory item lacks state_message"
                )
            elif _CLICKABLE_ELEMENTS_MARKER in str(state_message):
                observe(
                    "max_clickable_elements_chars",
                    40001,
                    touched=True,
                    lower_bound=True,
                )

            results = list(_field(item, "result", ()) or ())
            for result in results:
                error = _field(result, "error")
                if error is not None:
                    observe("action_error_chars", len(str(error)))

            output = _field(item, "model_output")
            actions = list(_field(output, "action", ()) or ()) if output else []
            for action_index, action in enumerate(actions):
                dumped = _action_mapping(action)
                if len(dumped) != 1:
                    raise ValueError(
                        "action dump must contain exactly one non-null action"
                    )
                action_name, params = next(iter(dumped.items()))
                if (
                    action_name not in {"evaluate", "extract"}
                    or action_index >= len(results)
                ):
                    continue

                # Runtime wrappers are the authoritative source for these
                # lossy tool caps.  History fallback supports offline/unit use.
                if tool_collector is not None:
                    continue
                result = results[action_index]
                extracted = _field(result, "extracted_content")
                if action_name == "evaluate":
                    if extracted is not None:
                        observe(
                            "evaluate_memory_chars",
                            len(str(extracted)),
                        )
                else:
                    already_collected = _field(
                        params,
                        "already_collected",
                        (),
                    ) or ()
                    observe(
                        "extract_already_collected_items",
                        len(already_collected),
                    )
                    if extracted is not None:
                        observe(
                            "extract_memory_chars",
                            len(str(extracted)),
                        )
                    metadata = _field(result, "metadata") or {}
                    extraction_result = _field(
                        metadata,
                        "extraction_result",
                    )
                    content_stats = _field(
                        extraction_result,
                        "content_stats",
                    )
                    is_partial = _field(extraction_result, "is_partial")
                    if content_stats is None or is_partial is None:
                        incomplete_reasons.append(
                            "executed extract lacks page-chunk audit metadata"
                        )
                    else:
                        final_chars = int(
                            _field(
                                content_stats,
                                "final_filtered_chars",
                                0,
                            )
                            or 0
                        )
                        start_chars = int(
                            _field(
                                content_stats,
                                "started_from_char",
                                _field(params, "start_from_char", 0),
                            )
                            or 0
                        )
                        observe(
                            "extract_page_chunk_chars",
                            max(0, final_chars - start_chars),
                            touched=bool(is_partial),
                        )
    except Exception as exc:  # noqa: BLE001 - incomplete audits must be explicit
        incomplete_reasons.append(
            f"AgentHistory audit failed: {type(exc).__name__}: {exc}"
        )

    if tool_collector is not None:
        for name, observations in tool_collector.observations.items():
            for observed, touched, lower_bound in observations:
                observe(
                    name,
                    observed,
                    touched=touched,
                    lower_bound=lower_bound,
                )
        incomplete_reasons.extend(tool_collector.errors)

    if incomplete_reasons:
        audit["complete"] = False
        audit["error"] = "; ".join(dict.fromkeys(incomplete_reasons))
    return audit


# --- Burst-resilient event timeouts (2026-07 concurrency fix) --------------------------------
# browser-use 0.13.6 reads per-event handler timeouts from env vars (events._get_timeout). The
# stock 30s NavigateToUrlEvent ceiling turns TRANSIENT launch-burst latency (N chromiums
# cold-starting on one host) into dead 0-step cells: the empirical ceiling was ~20 concurrent
# browsers, with mass first-navigation timeouts above it — while the machine sat near-idle.
# These are ceilings, not added latency: healthy cells are unaffected; burst-hit cells now wait
# out the spike instead of dying. setdefault -> operators can still override per run.
for _ev, _secs in (("TIMEOUT_NavigateToUrlEvent", "120"),
                   ("TIMEOUT_BrowserStateRequestEvent", "90"),
                   ("TIMEOUT_ScreenshotEvent", "60"),
                   ("TIMEOUT_ClickElementEvent", "45"),
                   ("TIMEOUT_ClickCoordinateEvent", "45"),
                   ("TIMEOUT_ScrollEvent", "30")):
    os.environ.setdefault(_ev, _secs)

# --- Completion-token ceiling (2026-07 validity fix) ----------------------------------------
# browser-use's ChatOpenAI defaults max_completion_tokens=4096, and for reasoning models that
# budget covers HIDDEN REASONING as well as the emitted action, so finish_reason='length' lands
# with content=None -> ModelOutputTruncatedError -> step retry -> "none". Measured over the 2100-run
# leaderboard the cap bound in 269 runs and was heavily model-dependent (Qwen3.5-122B 123/150,
# Kimi-K2.6 120/150, GPT-5.5-med 14/150, most models 0-3), i.e. it penalised verbose/reasoning-heavy
# models specifically. That makes it a measured constraint on output length rather than a safety
# backstop, and length is not what this benchmark measures.
#
# Default: None -> the parameter is omitted from the request entirely and each model uses its own
# provider-side output limit (browser-use handles the None case; per-attempt timeout=180s still
# bounds a runaway). Set AGENTARENA_MAX_COMPLETION_TOKENS=<int> to re-impose an explicit ceiling.
@lru_cache(maxsize=1)
def _trapi_token_provider_cached():
    """azure.identity's bearer-token provider, built once per process. The provider itself caches
    the token and refreshes it when it nears expiry, so calling it per request is cheap and always
    returns a live token."""
    from ..core.models import _trapi_token_provider
    return _trapi_token_provider()


def _llm_timeout_s() -> int:
    """Per-LLM-call ceiling, shared by the HTTP client and browser-use's Agent (see the long note
    at the Agent(...) call site). One value for every model — a speed-dependent ceiling ranks models
    by latency, not by preference fidelity."""
    try:
        return int(os.environ.get("AGENTARENA_LLM_TIMEOUT", "1200"))
    except ValueError:
        return 1200


def _completion_cap() -> Optional[int]:
    raw = (os.environ.get("AGENTARENA_MAX_COMPLETION_TOKENS") or "").strip().lower()
    if raw in ("", "0", "none", "off", "unset"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _explicit_frequency_penalty(model: ModelSpec) -> tuple[bool, float | None]:
    """Return a model-spec frequency penalty only when it was explicitly set.

    ``browser-use`` otherwise supplies its own provider-agnostic default of
    ``0.3``.  Local checkpoints must be able to preserve the request contract
    used during their training and selection without changing that default for
    every other benchmark model.  An explicit ``None`` means "omit the OpenAI
    request field" and is intentionally distinct from an absent override.
    """

    extra = getattr(model, "extra", None)
    if not isinstance(extra, Mapping) or "frequency_penalty" not in extra:
        return False, None
    value = extra["frequency_penalty"]
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("model extra.frequency_penalty must be numeric or null")
    normalized = float(value)
    if not -2.0 <= normalized <= 2.0:
        raise ValueError("model extra.frequency_penalty must be in [-2, 2]")
    return True, normalized


_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_FENCE_PATCHED = False


def _first_json_object(text: str) -> str:
    """If the text begins with a JSON object, return just that first balanced object (dropping any
    trailing characters the model appended after the closing brace). gpt-5.5#high sometimes emits a
    valid action object followed by extra text / a second object -> pydantic 'trailing characters'
    json_invalid. No-op unless the stripped text starts with '{' and a balanced object is found, so it
    never touches genuine prose."""
    s = text.lstrip()
    if not s.startswith("{"):
        return text
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                # only trim if there really are trailing non-space chars after the object
                return s[:end] if s[end:].strip() else text
    return text


def _strip_fences(text: str) -> str:
    """Unwrap a whole-string markdown code fence (```json ... ```) so models that wrap their action
    JSON in fences (e.g. gpt-5.2) parse cleanly, then drop any trailing characters after the first
    balanced JSON object (gpt-5.5#high 'trailing characters' case). No-op unless the ENTIRE content is
    one fenced block / a leading JSON object, so it never alters genuine prose / inline code."""
    if not isinstance(text, str):
        return text
    m = _FENCE.match(text)
    out = m.group(1).strip() if m else text
    return _first_json_object(out)


def _patch_fence_tolerance(ChatOpenAI) -> None:
    """One-time: wrap ChatOpenAI.get_client so each response's content has a surrounding markdown
    code fence stripped before browser-use's model_validate_json runs. Harmless for models that
    don't fence (most); fixes the ones that do."""
    global _FENCE_PATCHED
    if _FENCE_PATCHED:
        return
    _orig_get_client = ChatOpenAI.get_client

    def _get_client(self):
        client = _orig_get_client(self)
        _orig_create = client.chat.completions.create
        # TRAPI bearer tokens live ~1h, but ModelSpec.openai_endpoint() resolves ONE token string at
        # cell start and browser-use holds it for the life of the client. That was invisible while
        # runs finished inside the hour; once the harness ceilings were lifted (10h cell timeout,
        # 2000 steps) runs began outliving their token and dying in a cascade of 401s -> consecutive
        # failures. agentarena's own router already avoids this by passing a CALLABLE api_key
        # ("tokens are minted fresh per request and never go stale", llm_client.create_trapi_client);
        # browser-use types api_key as str, so mint per request here instead. Only for TRAPI hosts —
        # PhyAGI and OpenAI keys are static and must pass through untouched.
        _is_trapi = "trapi" in str(getattr(client, "base_url", "")).lower()
        _token = _trapi_token_provider_cached() if _is_trapi else None

        async def _create(*a, **k):
            if _token is not None:
                hdrs = dict(k.get("extra_headers") or {})
                hdrs["Authorization"] = f"Bearer {_token()}"
                k["extra_headers"] = hdrs
            try:
                resp = await _orig_create(*a, **k)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = (
                    type(exc).__name__ in {
                        "APIConnectionError",
                        "APITimeoutError",
                        "RateLimitError",
                    }
                    or status in {408, 409, 429}
                    or (isinstance(status, int) and status >= 500)
                )
                if retryable:
                    _LOG.error(
                        "AGENTARENA_LLM_SDK_RETRIES_EXHAUSTED "
                        "exception_type=%s status=%s configured_retries=%s "
                        "effective_attempts=%s",
                        type(exc).__name__,
                        status,
                        self.max_retries,
                        self.max_retries + 1,
                    )
                raise
            try:
                for ch in (resp.choices or []):
                    c = getattr(ch.message, "content", None)
                    if c:
                        ch.message.content = _strip_fences(c)
            except Exception:
                pass
            return resp

        client.chat.completions.create = _create
        return client

    ChatOpenAI.get_client = _get_client
    _FENCE_PATCHED = True


_LIMIT_AUDIT_SCHEMA_VERSION = 1


def _limit_contract_from_environment() -> tuple[dict | None, str | None]:
    raw = os.environ.get("AGENTARENA_LIMIT_CONTRACT_JSON")
    if not raw:
        return None, "AGENTARENA_LIMIT_CONTRACT_JSON is absent"
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"limit contract is not JSON: {exc}"
    if not isinstance(contract, dict):
        return None, "limit contract is not an object"
    if contract.get("schema_version") != _LIMIT_AUDIT_SCHEMA_VERSION:
        return None, "limit contract schema_version is not 1"
    categories = contract.get("categories")
    if not isinstance(categories, dict):
        return None, "limit contract categories are absent"
    expected_hash = contract.get("sha256")
    payload = {key: value for key, value in contract.items() if key != "sha256"}
    actual_hash = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if expected_hash != actual_hash:
        return None, "limit contract hash is invalid"
    return contract, None


def _new_limit_audit(
    contract: dict | None,
    arm: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    categories: dict[str, dict[str, dict[str, Any]]] = {}
    if contract is not None:
        for category, records in contract["categories"].items():
            applicable = {}
            for name, record in records.items():
                if arm not in record.get("applicability", ()):
                    continue
                observations = {}
                if record.get("observation_basis") is not None:
                    observations["observation_basis"] = record[
                        "observation_basis"
                    ]
                if record.get("direct_maximum_observed") is not None:
                    observations["direct_maximum_observed"] = record[
                        "direct_maximum_observed"
                    ]
                applicable[name] = {
                    "configured": record.get("configured"),
                    "touched_count": 0,
                    "observations": observations,
                }
            if applicable:
                categories[category] = applicable
    return {
        "schema_version": _LIMIT_AUDIT_SCHEMA_VERSION,
        "contract_sha256": (
            contract.get("sha256") if contract is not None else None
        ),
        "arm": arm,
        "complete": contract is not None and error is None,
        "error": error,
        "categories": categories,
    }


def _limit_record(
    audit: dict[str, Any],
    category: str,
    name: str,
) -> dict[str, Any] | None:
    return (
        (audit.get("categories") or {}).get(category, {}).get(name)
    )


def _observe_limit(
    audit: dict[str, Any],
    category: str,
    name: str,
    *,
    touched: int = 0,
    **observations: Any,
) -> None:
    record = _limit_record(audit, category, name)
    if record is None:
        audit["complete"] = False
        audit["error"] = (
            f"runtime attempted to observe undeclared limit "
            f"{category}.{name}"
        )
        return
    record["touched_count"] += max(0, int(touched))
    record["observations"].update(observations)


def _validate_runtime_limit_configuration(
    audit: dict[str, Any],
    *,
    ctx: RunContext,
    arm: str,
) -> None:
    """Fail the audit closed if live values differ from the frozen contract."""

    extract_externalization_actual = dict(
        _EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION
    )
    dependency_hash_errors = []
    for key, module_name in (
        (
            "upstream_tools_service_sha256_guard",
            "browser_use.tools.service",
        ),
        (
            "upstream_file_system_sha256_guard",
            "browser_use.filesystem.file_system",
        ),
    ):
        try:
            module = __import__(module_name, fromlist=["__file__"])
            source_path = Path(module.__file__).resolve()
            extract_externalization_actual[key] = hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest()
        except Exception as exc:  # noqa: BLE001 - audit must fail closed
            dependency_hash_errors.append(
                f"{module_name} source hash unavailable: "
                f"{type(exc).__name__}: {exc}"
            )

    replace_guard_declared = _limit_record(
        audit,
        "fixed_architecture",
        "replace_file_recursive_amplification_guard",
    ) is not None
    replace_guard_actual = dict(
        _REPLACE_FILE_RECURSIVE_AMPLIFICATION_GUARD_CONFIGURATION
    )
    if replace_guard_declared:
        try:
            replace_guard_actual[
                "upstream_browser_use_version_guard"
            ] = importlib_metadata.version("browser-use")
        except Exception as exc:  # noqa: BLE001 - audit must fail closed
            dependency_hash_errors.append(
                "browser-use version unavailable: "
                f"{type(exc).__name__}: {exc}"
            )
        for key, module_name in (
            (
                "upstream_tools_service_sha256_guard",
                "browser_use.tools.service",
            ),
            (
                "upstream_file_system_sha256_guard",
                "browser_use.filesystem.file_system",
            ),
        ):
            try:
                module = __import__(module_name, fromlist=["__file__"])
                source_path = Path(module.__file__).resolve()
                replace_guard_actual[key] = hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest()
            except Exception as exc:  # noqa: BLE001 - fail closed
                dependency_hash_errors.append(
                    f"{module_name} source hash unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )

    auxiliary_judge_declared = _limit_record(
        audit,
        "fixed_architecture",
        "post_task_auxiliary_judge",
    ) is not None
    auxiliary_judge_actual = dict(
        _POST_TASK_AUXILIARY_JUDGE_CONFIGURATION
    )
    if auxiliary_judge_declared:
        try:
            auxiliary_judge_actual[
                "upstream_browser_use_version_guard"
            ] = importlib_metadata.version("browser-use")
            from browser_use.agent.service import Agent as UpstreamAgent

            parameter = inspect.signature(
                UpstreamAgent.__init__
            ).parameters.get("use_judge")
            auxiliary_judge_actual["upstream_default"] = (
                parameter.default if parameter is not None else None
            )
        except Exception as exc:  # noqa: BLE001 - audit must fail closed
            dependency_hash_errors.append(
                "browser-use auxiliary-judge surface unavailable: "
                f"{type(exc).__name__}: {exc}"
            )
        for key, module_name in (
            (
                "upstream_agent_service_sha256_guard",
                "browser_use.agent.service",
            ),
            (
                "upstream_browser_session_sha256_guard",
                "browser_use.browser.session",
            ),
        ):
            try:
                module = __import__(module_name, fromlist=["__file__"])
                source_path = Path(module.__file__).resolve()
                auxiliary_judge_actual[key] = hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest()
            except Exception as exc:  # noqa: BLE001 - fail closed
                dependency_hash_errors.append(
                    f"{module_name} source hash unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )

    validation_feedback_declared = _limit_record(
        audit,
        "fixed_architecture",
        "agent_output_validation_feedback_rendering",
    ) is not None
    validation_feedback_actual = dict(
        _AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION
    )
    if validation_feedback_declared:
        validation_feedback_actual[
            "upstream_browser_use_version_guard"
        ] = importlib_metadata.version("browser-use")
        for key, module_name in (
            (
                "upstream_agent_service_sha256_guard",
                "browser_use.agent.service",
            ),
            (
                "upstream_message_manager_service_sha256_guard",
                "browser_use.agent.message_manager.service",
            ),
        ):
            try:
                module = __import__(module_name, fromlist=["__file__"])
                source_path = Path(module.__file__).resolve()
                validation_feedback_actual[key] = hashlib.sha256(
                    source_path.read_bytes()
                ).hexdigest()
            except Exception as exc:  # noqa: BLE001 - audit must fail closed
                dependency_hash_errors.append(
                    f"{module_name} source hash unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )

    actual = {
        ("safety_backstops", "max_steps"): ctx.max_steps,
        ("safety_backstops", "whole_run_timeout_seconds"): float(
            os.environ.get("AGENTARENA_CELL_TIMEOUT", "10800")
        ),
        ("safety_backstops", "llm_timeout_seconds"): _llm_timeout_s(),
        ("safety_backstops", "llm_http_timeout_seconds"):
            _llm_timeout_s() + 60,
        ("safety_backstops", "step_timeout_seconds"):
            _llm_timeout_s() + 300,
        ("safety_backstops", "extract_llm_timeout_seconds"): float(
            os.environ.get("BROWSER_USE_EXTRACT_TIMEOUT_S", "7500")
        ),
        ("safety_backstops", "cdp_request_timeout_seconds"): float(
            os.environ.get("BROWSER_USE_CDP_TIMEOUT_S", "15")
        ),
        ("safety_backstops", "browser_action_timeout_seconds"): float(
            os.environ.get("BROWSER_USE_ACTION_TIMEOUT_S", "180")
        ),
        ("safety_backstops", "max_consecutive_failures"): int(
            os.environ.get("AGENTARENA_MAX_FAILURES", "60")
        ),
        ("safety_backstops", "llm_sdk_max_retries"): {
            "retries": 16,
            "effective_attempts": 17,
            "retry_statuses": [408, 409, 429, ">=500"],
            "retry_after_max_seconds": 60,
            "exponential_backoff_max_seconds": 8,
        },
        ("safety_backstops", "evaluate_result_single_chars"):
            _EVALUATE_SINGLE_MAX_CHARS,
        ("safety_backstops", "evaluate_result_store_bytes"):
            _EVALUATE_STORE_MAX_BYTES,
        ("safety_backstops", "evaluate_result_store_responses"):
            _EVALUATE_STORE_MAX_RESPONSES,
        ("fixed_architecture", "max_completion_tokens"): (
            "off" if _completion_cap() is None else _completion_cap()
        ),
        ("fixed_architecture", "evaluate_result_spill"):
            _EVALUATE_RESULT_SPILL_CONFIGURATION,
        ("fixed_architecture", "extract_result_file_externalization"):
            extract_externalization_actual,
    }
    if replace_guard_declared:
        actual[(
            "fixed_architecture",
            "replace_file_recursive_amplification_guard",
        )] = replace_guard_actual
    if auxiliary_judge_declared:
        actual[(
            "fixed_architecture",
            "post_task_auxiliary_judge",
        )] = auxiliary_judge_actual
    if validation_feedback_declared:
        actual[(
            "fixed_architecture",
            "agent_output_validation_feedback_rendering",
        )] = validation_feedback_actual
    event_record = _limit_record(
        audit, "safety_backstops", "event_timeouts_seconds"
    )
    if event_record is not None and isinstance(
        event_record.get("configured"), dict
    ):
        actual[("safety_backstops", "event_timeouts_seconds")] = {
            name: float(os.environ[f"TIMEOUT_{name}"])
            for name in event_record["configured"]
        }
    errors = list(dependency_hash_errors)
    for (category, name), observed in actual.items():
        record = _limit_record(audit, category, name)
        if record is None:
            errors.append(f"missing {category}.{name}")
            continue
        configured = record["configured"]
        if isinstance(configured, (int, float)) and isinstance(
            observed, (int, float)
        ):
            matches = float(configured) == float(observed)
        else:
            matches = configured == observed
        record["observations"]["runtime_configured"] = observed
        if not matches:
            errors.append(
                f"{category}.{name}={observed!r}, expected {configured!r}"
            )
    context = (audit.get("categories") or {}).get(
        "lossy_context_limits", {}
    )
    if set(context) != set(_LOSSY_CONTEXT_CAPS):
        errors.append("lossy context-limit inventory differs from runtime")
    else:
        for name, (configured, _comparison) in _LOSSY_CONTEXT_CAPS.items():
            if context[name]["configured"] != configured:
                errors.append(
                    f"lossy_context_limits.{name} differs from runtime"
                )
    if errors:
        audit["complete"] = False
        audit["error"] = "; ".join(errors)


def _history_errors(history: Any) -> str:
    values = []
    for item in getattr(history, "history", ()) or ():
        for result in getattr(item, "result", ()) or ():
            error = getattr(result, "error", None)
            if error:
                values.append(str(error))
    return "\n".join(values)


def _action_error_audit_errors(
    value: Any,
    raw_context_record: Any,
) -> list[str]:
    """Validate the exact provenance schema and bind it to the raw audit."""

    errors: list[str] = []
    expected_top = {
        "schema_version", "complete", "error", "all",
        "agent_output_validation", "other_or_unknown",
    }
    expected_bucket = {"count", "over_cap_count", "max_chars"}
    if not isinstance(value, dict) or set(value) != expected_top:
        return ["action_error_audit top-level schema is not exact"]
    if value.get("schema_version") != _ACTION_ERROR_AUDIT_SCHEMA_VERSION:
        errors.append("action_error_audit schema_version differs")
    if value.get("complete") is not True or value.get("error") is not None:
        errors.append("action_error_audit is incomplete")

    buckets: dict[str, dict[str, int]] = {}
    for name in ("all", "agent_output_validation", "other_or_unknown"):
        bucket = value.get(name)
        if not isinstance(bucket, dict) or set(bucket) != expected_bucket:
            errors.append(f"action_error_audit.{name} is malformed")
            continue
        if any(type(bucket.get(key)) is not int or bucket[key] < 0
               for key in expected_bucket):
            errors.append(
                f"action_error_audit.{name} counters are malformed"
            )
            continue
        if bucket["over_cap_count"] > bucket["count"]:
            errors.append(
                f"action_error_audit.{name} over-cap count exceeds count"
            )
        if bucket["count"] == 0 and bucket["max_chars"] != 0:
            errors.append(
                f"action_error_audit.{name} empty bucket has nonzero max"
            )
        if (
            bucket["over_cap_count"] == 0
            and bucket["max_chars"]
            > _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS
        ) or (
            bucket["over_cap_count"] > 0
            and bucket["max_chars"]
            <= _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS
        ):
            errors.append(
                f"action_error_audit.{name} boundary counters disagree"
            )
        buckets[name] = bucket

    if len(buckets) == 3:
        all_bucket = buckets["all"]
        known = buckets["agent_output_validation"]
        unknown = buckets["other_or_unknown"]
        if all_bucket["count"] != known["count"] + unknown["count"]:
            errors.append("action_error_audit counts do not partition all")
        if (
            all_bucket["over_cap_count"]
            != known["over_cap_count"] + unknown["over_cap_count"]
        ):
            errors.append(
                "action_error_audit over-cap counts do not partition all"
            )
        if all_bucket["max_chars"] != max(
            known["max_chars"], unknown["max_chars"]
        ):
            errors.append("action_error_audit maxima do not partition all")

    if not isinstance(raw_context_record, dict):
        errors.append("raw action_error_chars context record is absent")
    elif len(buckets) == 3:
        if raw_context_record.get("configured") != (
            _EFFECTIVE_ACTION_ERROR_PROMPT_CHARS
        ):
            errors.append("raw action-error cap configuration differs")
        if raw_context_record.get("touched_count") != buckets["all"][
            "over_cap_count"
        ]:
            errors.append(
                "raw and provenance action-error touched counts disagree"
            )
        if raw_context_record.get("max_observed") != buckets["all"][
            "max_chars"
        ]:
            errors.append("raw and provenance action-error maxima disagree")
    return errors


def _replace_file_safety_audit_errors(value: Any) -> list[str]:
    """Validate the exact recursive-amplification guard observation."""

    expected_top = {
        "schema_version",
        "installation_count",
        "invocations",
        "checked_invocations",
        "passthrough_invocations",
        "rejected_invocations",
        "passed_invocations",
        "max_current_chars",
        "max_current_matches",
        "max_replacement_matches",
        "trigger_records",
        "errors",
    }
    if not isinstance(value, dict) or set(value) != expected_top:
        return ["replace_file safety audit top-level schema is not exact"]
    errors: list[str] = []
    if value.get("schema_version") != (
        _REPLACE_FILE_SAFETY_AUDIT_SCHEMA_VERSION
    ):
        errors.append("replace_file safety audit schema_version differs")
    numeric_names = expected_top - {
        "schema_version", "trigger_records", "errors"
    }
    if any(
        type(value.get(name)) is not int or value[name] < 0
        for name in numeric_names
    ):
        errors.append("replace_file safety audit counters are malformed")
        return errors
    if value["installation_count"] not in (0, 1):
        errors.append("replace_file guard installation count is not zero/one")
    if (
        value["installation_count"] == 0
        and value["invocations"] != 0
    ):
        errors.append("replace_file guard ran before installation")
    if value["invocations"] != (
        value["checked_invocations"]
        + value["passthrough_invocations"]
    ):
        errors.append("replace_file invocation partition disagrees")
    if value["checked_invocations"] != (
        value["passed_invocations"]
        + value["rejected_invocations"]
    ):
        errors.append("replace_file checked-call partition disagrees")
    if value["checked_invocations"] == 0 and (
        value["max_current_chars"] != 0
        or value["max_current_matches"] != 0
        or value["max_replacement_matches"] != 0
    ):
        errors.append("replace_file empty audit has nonzero maxima")
    raw_errors = value.get("errors")
    if not isinstance(raw_errors, list) or any(
        not isinstance(item, str) for item in raw_errors
    ):
        errors.append("replace_file safety audit errors are malformed")
    elif raw_errors:
        errors.append(
            "replace_file safety collector failed: "
            + "; ".join(raw_errors)
        )

    trigger_records = value.get("trigger_records")
    if not isinstance(trigger_records, list):
        errors.append("replace_file trigger records are malformed")
        return errors
    if len(trigger_records) != value["rejected_invocations"]:
        errors.append("replace_file trigger count disagrees")
    expected_trigger = {
        "trigger_index",
        "reason",
        "resolved_file",
        "current_chars",
        "old_str_chars",
        "current_non_overlapping_matches",
        "replacement_non_overlapping_matches",
        "file_unchanged",
        "agent_result",
    }
    for index, trigger in enumerate(trigger_records, start=1):
        if not isinstance(trigger, dict) or set(trigger) != expected_trigger:
            errors.append(
                f"replace_file trigger record {index} schema is not exact"
            )
            continue
        current_matches = trigger.get(
            "current_non_overlapping_matches"
        )
        replacement_matches = trigger.get(
            "replacement_non_overlapping_matches"
        )
        reason = trigger.get("reason")
        if (
            trigger.get("trigger_index") != index
            or not isinstance(trigger.get("resolved_file"), str)
            or not trigger["resolved_file"]
            or type(trigger.get("current_chars")) is not int
            or trigger["current_chars"] < 0
            or type(trigger.get("old_str_chars")) is not int
            or trigger["old_str_chars"] < 1
            or type(current_matches) is not int
            or current_matches <= 1
            or type(replacement_matches) is not int
            or replacement_matches <= 1
            or trigger.get("file_unchanged") is not True
            or trigger.get("agent_result") != "error"
            or reason != "recursive_all_sites_amplification"
        ):
            errors.append(
                f"replace_file trigger record {index} is malformed"
            )
            continue
        if (
            current_matches > value["max_current_matches"]
            or replacement_matches
            > value["max_replacement_matches"]
        ):
            errors.append(
                f"replace_file trigger record {index} exceeds maxima"
            )
    return errors


def _lossless_read_state_recovery_audit_errors(
    value: dict[str, Any] | None,
    raw_read_state: Mapping[str, Any] | None,
) -> list[str]:
    """Validate the complete proof before subtracting restored crossings."""

    prefix = "lossless read-state recovery audit"
    if not isinstance(value, dict):
        return [f"{prefix} is absent or malformed"]
    exact = {
        "schema_version", "active", "complete", "error",
        "authorization", "authorization_sha256", "installation",
        "call_count", "raw_crossing_count", "restored_crossing_count",
        "raw_max_chars", "effective_loss_touched_count", "records",
    }
    errors: list[str] = []
    if set(value) != exact:
        return [f"{prefix} schema is not exact"]
    if (
        value.get("schema_version") != 1
        or value.get("active") is not True
        or value.get("complete") is not True
        or value.get("error") is not None
    ):
        errors.append(f"{prefix} is incomplete")

    authorization = value.get("authorization")
    authorization_keys = {
        "schema", "mode", "campaign_uuid", "manifest_sha256",
        "amendment_sha256", "scheduler_sha256", "run_index", "run_id",
        "prior_attempt", "attempt", "prior_completion_sha256",
        "prior_trajectory_sha256", "cache_nonce_sha256",
    }
    fixed = {
        "schema": _LOSSLESS_READ_STATE_RECOVERY_SCHEMA,
        "mode": _LOSSLESS_READ_STATE_RECOVERY_MODE,
        "campaign_uuid": _LOSSLESS_READ_STATE_RECOVERY_CAMPAIGN_UUID,
        "manifest_sha256": _LOSSLESS_READ_STATE_RECOVERY_MANIFEST_SHA256,
        "run_index": _LOSSLESS_READ_STATE_RECOVERY_RUN_INDEX,
        "run_id": _LOSSLESS_READ_STATE_RECOVERY_RUN_ID,
        "prior_attempt": _LOSSLESS_READ_STATE_RECOVERY_PRIOR_ATTEMPT,
        "prior_completion_sha256": (
            _LOSSLESS_READ_STATE_RECOVERY_PRIOR_COMPLETION_SHA256
        ),
        "prior_trajectory_sha256": (
            _LOSSLESS_READ_STATE_RECOVERY_PRIOR_TRAJECTORY_SHA256
        ),
    }
    if (
        not isinstance(authorization, dict)
        or set(authorization) != authorization_keys
        or any(authorization.get(key) != item for key, item in fixed.items())
        or type(authorization.get("attempt")) is not int
        or authorization.get("attempt", 0) < 2
        or any(
            not isinstance(authorization.get(name), str)
            or re.fullmatch(r"[0-9a-f]{64}", authorization[name]) is None
            for name in (
                "amendment_sha256", "scheduler_sha256",
                "cache_nonce_sha256",
            )
        )
    ):
        errors.append(f"{prefix} authorization is not exact")
    elif value.get("authorization_sha256") != _json_sha256(authorization):
        errors.append(f"{prefix} authorization digest differs")

    installation = value.get("installation")
    expected_installation = {
        "browser_use_version": _BROWSER_USE_VERSION,
        "source_sha256": _BROWSER_USE_MESSAGE_MANAGER_SERVICE_SHA256,
        "method_source_sha256": (
            _BROWSER_USE_MESSAGE_MANAGER_METHOD_SOURCE_SHA256
        ),
        "method_code_sha256": _BROWSER_USE_MESSAGE_MANAGER_METHOD_CODE_SHA256,
        "method_qualname": "MessageManager._update_agent_history_description",
        "method_signature": _BROWSER_USE_MESSAGE_MANAGER_METHOD_SIGNATURE,
        "method_constants_sha256": _json_sha256(
            list(_EFFECTIVE_MESSAGE_MANAGER_METHOD_CONSTANTS)
        ),
        "installation_scope": "single_message_manager_instance",
        "read_state_limit_chars": _READ_STATE_CONTENT_LIMIT_CHARS,
        "action_results_limit_behavior": "unchanged_upstream",
    }
    if installation != expected_installation:
        errors.append(f"{prefix} installation attestation differs")

    records = value.get("records")
    record_keys = {
        "call_index", "step_number", "raw_chars", "raw_sha256",
        "raw_crossed", "upstream_chars", "upstream_sha256",
        "expected_upstream_sha256", "restored", "effective_chars",
        "effective_sha256", "effective_loss", "history_items",
        "read_state_images", "action_results_sha256",
    }
    valid_records: list[dict[str, Any]] = []
    if not isinstance(records, list):
        errors.append(f"{prefix} records are malformed")
        records = []
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict) or set(record) != record_keys:
            errors.append(f"{prefix} record {position} schema is not exact")
            continue
        hash_names = (
            "raw_sha256", "upstream_sha256", "expected_upstream_sha256",
            "effective_sha256", "action_results_sha256",
        )
        integer_names = (
            "raw_chars", "upstream_chars", "effective_chars",
            "history_items", "read_state_images",
        )
        if (
            record.get("call_index") != position
            or any(
                type(record.get(name)) is not int or record[name] < 0
                for name in integer_names
            )
            or (
                record.get("step_number") is not None
                and type(record.get("step_number")) is not int
            )
            or any(
                not isinstance(record.get(name), str)
                or re.fullmatch(r"[0-9a-f]{64}", record[name]) is None
                for name in hash_names
            )
            or type(record.get("raw_crossed")) is not bool
            or type(record.get("restored")) is not bool
            or type(record.get("effective_loss")) is not bool
        ):
            errors.append(f"{prefix} record {position} is malformed")
            continue
        crossed = record["raw_chars"] > _READ_STATE_CONTENT_LIMIT_CHARS
        expected_effective_chars = (
            record["raw_chars"] - 1 if record["raw_chars"] else 0
        )
        if (
            record["raw_crossed"] is not crossed
            or record["restored"] is not crossed
            or record["effective_loss"] is not False
            or record["effective_chars"] != expected_effective_chars
            or record["upstream_sha256"]
            != record["expected_upstream_sha256"]
            or (
                crossed and record["upstream_chars"]
                != _READ_STATE_CONTENT_LIMIT_CHARS
                + len(_READ_STATE_TRUNCATION_MARKER)
            )
            or (
                not crossed
                and record["upstream_chars"] != expected_effective_chars
            )
        ):
            errors.append(
                f"{prefix} record {position} does not prove exact restoration"
            )
            continue
        valid_records.append(record)

    raw_crossings = sum(
        int(record["raw_crossed"] is True) for record in valid_records
    )
    restored = sum(
        int(record["restored"] is True) for record in valid_records
    )
    raw_max = max(
        (record["raw_chars"] for record in valid_records), default=0
    )
    aggregates_match = (
        type(value.get("call_count")) is int
        and value.get("call_count") == len(records)
        and type(value.get("raw_crossing_count")) is int
        and value.get("raw_crossing_count") == raw_crossings
        and type(value.get("restored_crossing_count")) is int
        and value.get("restored_crossing_count") == restored
        and type(value.get("raw_max_chars")) is int
        and value.get("raw_max_chars") == raw_max
        and type(value.get("effective_loss_touched_count")) is int
        and value.get("effective_loss_touched_count")
        == raw_crossings - restored
    )
    if not aggregates_match or len(valid_records) != len(records):
        errors.append(f"{prefix} aggregate counters differ")
    if (
        not isinstance(raw_read_state, Mapping)
        or type(raw_read_state.get("touched_count")) is not int
        or type(raw_read_state.get("max_observed")) is not int
        or raw_read_state.get("touched_count") != raw_crossings
        or raw_read_state.get("max_observed") != raw_max
    ):
        errors.append(f"{prefix} differs from raw context audit")
    return errors


def _finalize_limit_audit(
    audit: dict[str, Any],
    *,
    steps: list[Step],
    elapsed_seconds: float,
    context_cap_audit: dict[str, Any],
    history_error_text: str,
    run_error: str | None,
    total_timeout_touched: bool,
    evaluate_result_store: dict[str, Any] | None,
    extension_stats: dict[str, Any] | None,
    agent: Any,
    action_error_audit: dict[str, Any] | None = None,
    replace_file_safety_audit: dict[str, Any] | None = None,
    lossless_read_state_recovery_audit: dict[str, Any] | None = None,
) -> None:
    safety_text = "\n".join(
        value for value in (history_error_text, run_error or "")
        if value
    )
    max_steps = _limit_record(audit, "safety_backstops", "max_steps")
    if max_steps is not None:
        configured = int(max_steps["configured"])
        _observe_limit(
            audit,
            "safety_backstops",
            "max_steps",
            touched=int(len(steps) >= configured),
            observed_steps=len(steps),
        )
    _observe_limit(
        audit,
        "safety_backstops",
        "whole_run_timeout_seconds",
        touched=int(total_timeout_touched),
        observed_seconds=round(elapsed_seconds, 3),
    )
    step_timeout_record = _limit_record(
        audit, "safety_backstops", "step_timeout_seconds"
    )
    step_timeout_value = (
        step_timeout_record.get("configured")
        if step_timeout_record is not None else None
    )
    step_timeout_markers = ()
    if isinstance(step_timeout_value, (int, float)):
        rendered = f"{float(step_timeout_value):g}"
        step_timeout_markers = (
            f" timed out after {rendered} seconds",
        )
    failures_record = _limit_record(
        audit, "safety_backstops", "max_consecutive_failures"
    )
    failures_value = (
        failures_record.get("configured")
        if failures_record is not None else None
    )
    failures_markers = ()
    if isinstance(failures_value, int):
        failures_markers = (
            f"Stopping due to {failures_value} consecutive failures",
            f"failed {failures_value} times",
        )
    marker_map = {
        "llm_timeout_seconds": (
            "llm call timed out", "llm timed out", "TimeoutError: LLM",
        ),
        "llm_http_timeout_seconds": (
            "APITimeoutError", "httpx.ReadTimeout",
            "httpx.ConnectTimeout", "httpx.WriteTimeout",
            "httpx.PoolTimeout",
        ),
        "step_timeout_seconds": step_timeout_markers,
        "extract_llm_timeout_seconds": (
            "AGENTARENA_EXTRACT_LLM_TIMEOUT_BOUND",
        ),
        "cdp_request_timeout_seconds": ("CDP request timed out",),
        "browser_action_timeout_seconds": (
            "Browser action timed out", "Action timed out",
        ),
        "max_consecutive_failures": failures_markers,
        "llm_sdk_max_retries": (
            "AGENTARENA_LLM_SDK_RETRIES_EXHAUSTED",
            "max retries exceeded",
        ),
        "event_timeouts_seconds": (
            "Error in event handler", "EventBus",
        ),
        "dependency_inner_timeouts": (
            "Page.navigate() timed out",
            "reconnection attempts failed",
            "Browser did not start within",
        ),
    }
    for name, markers in marker_map.items():
        _observe_limit(
            audit,
            "safety_backstops",
            name,
            touched=int(any(marker in safety_text for marker in markers)),
        )
    limits = context_cap_audit.get("limits")
    if context_cap_audit.get("complete") is not True:
        audit["complete"] = False
        audit["error"] = (
            "common context-limit audit is incomplete"
            + (
                f": {context_cap_audit.get('error')}"
                if context_cap_audit.get("error") else ""
            )
        )
    validation_feedback_record = _limit_record(
        audit,
        "fixed_architecture",
        "agent_output_validation_feedback_rendering",
    )
    validation_split_errors: list[str] = []
    if validation_feedback_record is not None:
        raw_action_error = (
            limits.get("action_error_chars")
            if isinstance(limits, dict) else None
        )
        validation_split_errors = _action_error_audit_errors(
            action_error_audit,
            raw_action_error,
        )
        if validation_split_errors:
            audit["complete"] = False
            message = "; ".join(validation_split_errors)
            audit["error"] = (
                f"{audit['error']}; {message}"
                if audit.get("error") else message
            )

    if isinstance(limits, dict):
        for name, source in limits.items():
            if name == "extract_memory_chars":
                record = _limit_record(
                    audit,
                    "fixed_architecture",
                    "extract_result_file_externalization",
                )
                if record is None:
                    audit["complete"] = False
                    audit["error"] = (
                        "missing fixed extraction externalization audit"
                    )
                    continue
                externalized = int(source.get("touched_count", 0))
                record["touched_count"] = externalized
                record["observations"].update({
                    "raw_context_audit_name": name,
                    "externalized_results": externalized,
                    "max_result_chars": int(
                        source.get("max_observed", 0)
                    ),
                    "threshold_chars": int(
                        source.get("configured", 0)
                    ),
                })
                continue
            if (
                name == "read_state_chars"
                and lossless_read_state_recovery_audit is not None
            ):
                record = _limit_record(
                    audit, "lossy_context_limits", name
                )
                if record is None:
                    audit["complete"] = False
                    audit["error"] = (
                        "missing lossy read-state audit record"
                    )
                    continue
                raw_touched = int(source.get("touched_count", 0))
                raw_max = int(source.get("max_observed", 0))
                recovery_errors = (
                    _lossless_read_state_recovery_audit_errors(
                        lossless_read_state_recovery_audit,
                        source,
                    )
                )
                # Preserve the untouched raw context observations regardless
                # of proof status.  Only a complete, exact proof is permitted
                # to subtract restored crossings from the effective lossy
                # count; every other state retains the conservative raw count.
                record["observations"] = {
                    key: value for key, value in source.items()
                    if key != "configured"
                }
                record["observations"].update({
                    "raw_touched_count": raw_touched,
                    "raw_max_observed": raw_max,
                    "lossless_recovery_audit": (
                        lossless_read_state_recovery_audit
                    ),
                })
                if recovery_errors:
                    record["touched_count"] = raw_touched
                    record["observations"].update({
                        "recovery_complete": False,
                        "effective_touched_count": raw_touched,
                    })
                    audit["complete"] = False
                    message = "; ".join(recovery_errors)
                    audit["error"] = (
                        f"{audit['error']}; {message}"
                        if audit.get("error") else message
                    )
                    continue
                restored = int(
                    lossless_read_state_recovery_audit[
                        "restored_crossing_count"
                    ]
                )
                effective = raw_touched - restored
                record["touched_count"] = effective
                record["observations"].update({
                    "recovery_complete": True,
                    "restored_touched_count": restored,
                    "effective_touched_count": effective,
                })
                continue
            if (
                name == "action_error_chars"
                and validation_feedback_record is not None
            ):
                record = _limit_record(
                    audit, "lossy_context_limits", name
                )
                if record is None:
                    audit["complete"] = False
                    audit["error"] = (
                        "missing lossy action-error audit record"
                    )
                    continue
                if validation_split_errors:
                    # Conservative fallback is diagnostic only because the
                    # incomplete audit already invalidates the run.
                    record["touched_count"] = int(
                        source.get("touched_count", 0)
                    )
                    record["observations"].update({
                        "classification_complete": False,
                        "raw_touched_count": int(
                            source.get("touched_count", 0)
                        ),
                        "raw_max_observed": int(
                            source.get("max_observed", 0)
                        ),
                    })
                    validation_feedback_record["observations"].update({
                        "classification_complete": False,
                    })
                    continue

                assert action_error_audit is not None
                all_bucket = action_error_audit["all"]
                known = action_error_audit[
                    "agent_output_validation"
                ]
                unknown = action_error_audit["other_or_unknown"]
                record["touched_count"] = unknown["over_cap_count"]
                record["observations"].update({
                    "classification_complete": True,
                    "raw_touched_count": all_bucket["over_cap_count"],
                    "raw_max_observed": all_bucket["max_chars"],
                    "other_or_unknown_count": unknown["count"],
                    "other_or_unknown_touched_count": unknown[
                        "over_cap_count"
                    ],
                    "max_observed": unknown["max_chars"],
                    "classified_agent_output_validation_count": known[
                        "count"
                    ],
                    "classified_agent_output_validation_touched_count": (
                        known["over_cap_count"]
                    ),
                    "classified_agent_output_validation_max_observed": (
                        known["max_chars"]
                    ),
                })
                validation_feedback_record["touched_count"] = known[
                    "over_cap_count"
                ]
                validation_feedback_record["observations"].update({
                    "classification_complete": True,
                    "raw_context_audit_name": "action_error_chars",
                    "all_error_count": all_bucket["count"],
                    "all_over_cap_count": all_bucket["over_cap_count"],
                    "all_max_chars": all_bucket["max_chars"],
                    "agent_output_validation_count": known["count"],
                    "agent_output_validation_over_cap_count": known[
                        "over_cap_count"
                    ],
                    "agent_output_validation_max_chars": known[
                        "max_chars"
                    ],
                    "other_or_unknown_count": unknown["count"],
                    "other_or_unknown_over_cap_count": unknown[
                        "over_cap_count"
                    ],
                    "other_or_unknown_max_chars": unknown["max_chars"],
                })
                continue
            record = _limit_record(
                audit, "lossy_context_limits", name
            )
            if record is None:
                audit["complete"] = False
                audit["error"] = f"unexpected context-limit audit: {name}"
                continue
            record["touched_count"] = int(
                source.get("touched_count", 0)
            )
            record["observations"] = {
                key: value for key, value in source.items()
                if key != "configured"
            }
    else:
        audit["complete"] = False
        audit["error"] = "common context-limit audit is absent"

    replace_record = _limit_record(
        audit,
        "fixed_architecture",
        "replace_file_recursive_amplification_guard",
    )
    if replace_record is not None:
        replace_errors = _replace_file_safety_audit_errors(
            replace_file_safety_audit
        )
        if replace_errors:
            audit["complete"] = False
            message = "; ".join(replace_errors)
            audit["error"] = (
                f"{audit['error']}; {message}"
                if audit.get("error") else message
            )
            replace_record["observations"].update({
                "audit_complete": False,
            })
        else:
            assert replace_file_safety_audit is not None
            _observe_limit(
                audit,
                "fixed_architecture",
                "replace_file_recursive_amplification_guard",
                touched=replace_file_safety_audit[
                    "rejected_invocations"
                ],
                audit_complete=True,
                **{
                    key: value
                    for key, value in replace_file_safety_audit.items()
                    if key not in {"schema_version", "errors"}
                },
            )

    store_fields = {
        "schema_version",
        "inline_chars",
        "single_max_chars",
        "max_serialized_chars",
        "single_bound_touched_count",
        "integrity_failure_count",
        "max_bytes",
        "bytes",
        "byte_bound_touched_count",
        "max_responses",
        "responses",
        "response_bound_touched_count",
        "records",
    }
    if (
        not isinstance(evaluate_result_store, dict)
        or set(evaluate_result_store) != store_fields
        or evaluate_result_store.get("schema_version") != 1
    ):
        audit["complete"] = False
        audit["error"] = (
            "common evaluate-result store audit is absent or malformed"
        )
    else:
        numeric_names = store_fields - {"schema_version"}
        malformed = [
            name for name in numeric_names
            if type(evaluate_result_store.get(name)) is not int
            or evaluate_result_store[name] < 0
        ]
        if malformed:
            audit["complete"] = False
            audit["error"] = (
                "common evaluate-result store counters are malformed: "
                + ", ".join(sorted(malformed))
            )
        elif (
            evaluate_result_store["inline_chars"]
            != _EVALUATE_INLINE_CHARS
            or evaluate_result_store["single_max_chars"]
            != _EVALUATE_SINGLE_MAX_CHARS
            or evaluate_result_store["max_bytes"]
            != _EVALUATE_STORE_MAX_BYTES
            or evaluate_result_store["max_responses"]
            != _EVALUATE_STORE_MAX_RESPONSES
            or evaluate_result_store["bytes"]
            > evaluate_result_store["max_bytes"]
            or evaluate_result_store["responses"]
            > evaluate_result_store["max_responses"]
            or evaluate_result_store["records"]
            > evaluate_result_store["responses"]
            or (
                evaluate_result_store["responses"] == 0
                and (
                    evaluate_result_store["records"] != 0
                    or evaluate_result_store["bytes"] != 0
                )
            )
            or (
                evaluate_result_store["responses"] > 0
                and (
                    evaluate_result_store["records"] == 0
                    or evaluate_result_store["max_serialized_chars"]
                    <= evaluate_result_store["inline_chars"]
                    or evaluate_result_store["bytes"]
                    < evaluate_result_store["records"]
                    * (evaluate_result_store["inline_chars"] + 1)
                )
            )
            or (
                evaluate_result_store["max_serialized_chars"]
                > evaluate_result_store["single_max_chars"]
                and evaluate_result_store[
                    "single_bound_touched_count"
                ] == 0
            )
        ):
            audit["complete"] = False
            audit["error"] = (
                "common evaluate-result store differs from the runtime "
                "contract"
            )
        else:
            _observe_limit(
                audit,
                "safety_backstops",
                "evaluate_result_single_chars",
                touched=evaluate_result_store[
                    "single_bound_touched_count"
                ],
                max_serialized_chars=evaluate_result_store[
                    "max_serialized_chars"
                ],
                maximum=evaluate_result_store["single_max_chars"],
            )
            byte_touched = max(
                evaluate_result_store["byte_bound_touched_count"],
                int(
                    evaluate_result_store["bytes"]
                    >= evaluate_result_store["max_bytes"]
                ),
            )
            _observe_limit(
                audit,
                "safety_backstops",
                "evaluate_result_store_bytes",
                touched=byte_touched,
                used=evaluate_result_store["bytes"],
                maximum=evaluate_result_store["max_bytes"],
                utilization=(
                    evaluate_result_store["bytes"]
                    / evaluate_result_store["max_bytes"]
                ),
                records=evaluate_result_store["records"],
            )
            response_touched = max(
                evaluate_result_store["response_bound_touched_count"],
                int(
                    evaluate_result_store["responses"]
                    >= evaluate_result_store["max_responses"]
                ),
            )
            _observe_limit(
                audit,
                "safety_backstops",
                "evaluate_result_store_responses",
                touched=response_touched,
                used=evaluate_result_store["responses"],
                maximum=evaluate_result_store["max_responses"],
                utilization=(
                    evaluate_result_store["responses"]
                    / evaluate_result_store["max_responses"]
                ),
                records=evaluate_result_store["records"],
            )
            _observe_limit(
                audit,
                "fixed_architecture",
                "evaluate_result_spill",
                touched=evaluate_result_store["responses"],
                spilled_responses=evaluate_result_store["responses"],
                unique_records=evaluate_result_store["records"],
                stored_bytes=evaluate_result_store["bytes"],
                max_serialized_chars=evaluate_result_store[
                    "max_serialized_chars"
                ],
            )
            if evaluate_result_store["integrity_failure_count"] > 0:
                audit["complete"] = False
                audit["error"] = (
                    "common evaluate-result storage integrity failed "
                    f"{evaluate_result_store['integrity_failure_count']} "
                    "time(s)"
                )

    action_counts = []
    for step in steps:
        try:
            actions = json.loads(step.action)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(actions, list):
            action_counts.append(len(actions))
    max_actions_observed = max(action_counts, default=0)
    max_actions_record = _limit_record(
        audit, "fixed_architecture", "max_actions_per_step"
    )
    max_actions_configured = (
        max_actions_record.get("configured")
        if max_actions_record is not None else None
    )
    _observe_limit(
        audit,
        "fixed_architecture",
        "max_actions_per_step",
        touched=int(
            isinstance(max_actions_configured, int)
            and max_actions_observed >= max_actions_configured
        ),
        max_executed_actions=max_actions_observed,
    )
    auxiliary_judge_record = _limit_record(
        audit,
        "fixed_architecture",
        "post_task_auxiliary_judge",
    )
    if auxiliary_judge_record is not None:
        effective_use_judge = (
            getattr(getattr(agent, "settings", None), "use_judge", None)
            if agent is not None else None
        )
        _observe_limit(
            audit,
            "fixed_architecture",
            "post_task_auxiliary_judge",
            touched=0,
            agent_constructed=agent is not None,
            effective_use_judge=effective_use_judge,
            authoritative_evaluator=(
                "agentarena.core.experiment.run_cell:env.evaluate"
            ),
        )
        if agent is not None and effective_use_judge is not False:
            audit["complete"] = False
            message = "post-task auxiliary judge is not disabled"
            audit["error"] = (
                f"{audit['error']}; {message}"
                if audit.get("error") else message
            )
    if agent is not None:
        _observe_limit(
            audit,
            "fixed_architecture",
            "fallback_llm_depth",
            touched=int(bool(
                getattr(agent, "is_using_fallback_llm", False)
            )),
            fallback_was_used=bool(
                getattr(agent, "is_using_fallback_llm", False)
            ),
        )
        manager = getattr(agent, "_message_manager", None)
        manager_state = getattr(manager, "state", None)
        compaction_count = int(
            getattr(manager_state, "compaction_count", 0) or 0
        )
        _observe_limit(
            audit,
            "fixed_architecture",
            "message_compaction",
            touched=compaction_count,
            compaction_count=compaction_count,
            last_compaction_step=getattr(
                manager_state, "last_compaction_step", None
            ),
        )

    if not isinstance(extension_stats, dict):
        if audit.get("arm") == "deliberative":
            audit["complete"] = False
            message = "deliberative extension stats are absent"
            audit["error"] = (
                f"{audit['error']}; {message}"
                if audit.get("error") else message
            )
        return
    supplied = extension_stats.get("limit_observations")
    compiler = _limit_record(
        audit, "fixed_architecture", "compiler_and_checkpoint_shape"
    )
    if compiler is not None:
        compiler["observations"].update({
            "contract_compile_calls":
                extension_stats.get("contract_compile_calls"),
            "decision_checkpoint_calls":
                extension_stats.get("decision_checkpoint_calls"),
            "decision_checkpoint_rejections":
                extension_stats.get("decision_checkpoint_rejections"),
        })
    if supplied is not None:
        by_name = {
            name: record
            for records in audit["categories"].values()
            for name, record in records.items()
        }
        if not isinstance(supplied, dict):
            audit["complete"] = False
            audit["error"] = "deliberative limit_observations is malformed"
        else:
            if set(supplied) != {"structured_response_attempts"}:
                audit["complete"] = False
                audit["error"] = (
                    "deliberative limit_observations inventory is not exact"
                )
            for name, observation in supplied.items():
                record = by_name.get(name)
                if record is None or not isinstance(observation, dict):
                    audit["complete"] = False
                    audit["error"] = (
                        f"unknown/malformed deliberative limit observation: "
                        f"{name}"
                    )
                    continue
                touched = observation.get("touched_count", 0)
                details = observation.get("observations", {})
                if not isinstance(touched, int) or touched < 0 or not isinstance(
                    details, dict
                ):
                    audit["complete"] = False
                    audit["error"] = (
                        f"malformed deliberative limit observation: {name}"
                    )
                    continue
                record["touched_count"] += touched
                record["observations"].update(details)
    else:
        audit["complete"] = False
        audit["error"] = "deliberative limit_observations is absent"


class _WholeRunTimeoutError(TimeoutError):
    """Raised only when the scaffold's outer, whole-lifecycle deadline binds."""


async def _await_whole_run(awaitable: Any, timeout_seconds: float) -> Any:
    """Await one browser lifecycle without masking an inner ``TimeoutError``.

    ``asyncio.wait_for`` raises ``TimeoutError`` both when its own deadline
    expires and when the wrapped coroutine raises that exception.  The
    campaign needs to attribute the outer safety backstop exactly, so use
    ``asyncio.wait`` and a private exception type for only that deadline.
    Cancellation is awaited before returning so browser/preflight tasks cannot
    leak past the measured run.
    """
    task = asyncio.create_task(awaitable)
    try:
        done, _pending = await asyncio.wait(
            {task}, timeout=timeout_seconds
        )
        if done:
            return await task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise _WholeRunTimeoutError(
            f"whole run exceeded {timeout_seconds} seconds"
        )
    except BaseException:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


async def _run(ctx: RunContext, *, extension=None) -> RawTrajectory:
    from browser_use import (
        Agent,
        BrowserProfile,
        BrowserSession,
        ChatOpenAI,
        Tools,
    )
    _patch_fence_tolerance(ChatOpenAI)

    ep = ctx.model.openai_endpoint()
    bc = BrowserConfig.from_env(headless=ctx.headless)
    if bc.lib_path:
        os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")

    # max_retries: the OpenAI SDK retries 429/503 with exponential backoff (respecting the
    # Retry-After header) transparently inside each call, so transient TRAPI rate-limits /
    # backend-health blips are absorbed before the agent's own step-retry kicks in. Without this
    # (SDK default 2) a big concurrent run loses cells to "none" when TRAPI throttles. timeout
    # caps a single attempt so a hung reasoning call can't stall a cell forever.
    llm_kwargs = dict(model=ep.model, base_url=ep.base_url, api_key=ep.api_key,
                      dont_force_structured_output=True, add_schema_to_system_prompt=True,
                      max_retries=16, timeout=float(_llm_timeout_s() + 60),
                      max_completion_tokens=_completion_cap())
    has_frequency_penalty, frequency_penalty = _explicit_frequency_penalty(ctx.model)
    if has_frequency_penalty:
        # ChatOpenAI's default is 0.3.  Preserve an explicitly frozen model
        # request contract, including None (which omits the wire parameter).
        llm_kwargs["frequency_penalty"] = frequency_penalty
    if ep.reasoning:
        llm_kwargs["reasoning_models"] = [ep.model]   # max_completion_tokens, no temperature
        if ep.reasoning_effort:                       # reasoning-effort sweep (low/medium/high)
            llm_kwargs["reasoning_effort"] = ep.reasoning_effort
    else:
        llm_kwargs["temperature"] = 0.0
    llm = ChatOpenAI(**llm_kwargs)

    # Reliability: TRAPI regions flap 503 ("all-backends-unhealthy") and this scaffold drives a SINGLE
    # endpoint, so a persistent region outage kills the cell (the SDK's retries can't revive a down
    # backend). If browser-use's Agent supports a fallback LLM and PhyAGI serves this model, build a
    # PhyAGI-routed twin and pass it as fallback_llm — TRAPI stays primary (max TRAPI), PhyAGI is the
    # safety net so a flap fails over instead of losing the cell.
    # Reliability + MAX TRAPI: a transient single-region TRAPI 502/timeout should fail over to a SECOND
    # TRAPI region (stays on TRAPI) rather than the slow/flaky PhyAGI. So the fallback_llm is a TRAPI twin
    # pointed at pinned-region[1] (e.g. gpt-5.5 redmond->msraif, gpt-4.1 msraif->gcr). PhyAGI is only used
    # if TRAPI has no 2nd region for the model. Pure routing (which server answers), not agent behavior.
    fallback_llm = None
    try:
        import inspect
        from ..llm_client import (TRAPI_MODEL_REGIONS, _trapi_base_url, _logical,
                                   PHYAGI_MODELS, _phyagi_key)
        logical = _logical(ctx.model.deployment or ctx.model.name)
        regions = TRAPI_MODEL_REGIONS.get(logical, [])
        if "fallback_llm" in inspect.signature(Agent.__init__).parameters and ctx.model.provider != "phyagi":
            if len(regions) >= 2:                                   # 2nd TRAPI region (keep traffic on TRAPI)
                fb_kwargs = dict(llm_kwargs)
                fb_kwargs.update(base_url=_trapi_base_url(regions[1]))   # same TRAPI model + key, diff region
                fallback_llm = ChatOpenAI(**fb_kwargs)
            elif _phyagi_key() and logical in PHYAGI_MODELS:        # no 2nd TRAPI region -> PhyAGI net
                fb = ModelSpec(name=ctx.model.name, provider="phyagi", deployment=logical,
                               vision=ctx.model.vision, extra=dict(ctx.model.extra)).openai_endpoint()
                fb_kwargs = dict(llm_kwargs)
                fb_kwargs.update(model=fb.model, base_url=fb.base_url, api_key=fb.api_key)
                if "reasoning_models" in fb_kwargs:
                    fb_kwargs["reasoning_models"] = [fb.model]
                fallback_llm = ChatOpenAI(**fb_kwargs)
    except Exception:
        fallback_llm = None

    # The assigned site is the complete browser scope.  Deriving this boundary
    # from the task URL keeps navigation isolation independent of any vendor or
    # benchmark-specific domain list; subresources may still load normally.
    start_host = urlsplit(ctx.start_url).hostname
    if not start_host:
        raise ValueError("start_url must contain a hostname")
    # keep the browser's user-data-dir on the cell's (disk-backed) work dir — browser_use
    # otherwise drops a ~75MB profile in /tmp per run, which fills a tmpfs /tmp during big runs.
    udd = None
    try:
        udd = ctx.work_dir / "udd"
        udd.mkdir(parents=True, exist_ok=True)
        udd = str(udd)
    except Exception:
        udd = None
    profile = BrowserProfile(executable_path=bc.executable, headless=bc.headless,
                             args=bc.args, env=bc.child_env(),
                             allowed_domains=[start_host], user_data_dir=udd,
                             window_size={"width": bc.width, "height": bc.height})
    bs = BrowserSession(browser_profile=profile)
    steps: list[Step] = []
    answer = ""
    t0 = time.time()
    error = None
    agent = None
    history_error_text = ""
    total_timeout_touched = False
    context_cap_collector = _ContextCapToolCollector()
    context_cap_audit = _context_cap_audit(None)
    action_error_collector = _ActionErrorProvenanceCollector()
    replace_file_safety_collector = _ReplaceFileSafetyCollector()
    action_error_audit = _empty_action_error_audit(
        complete=False,
        error="AgentHistory unavailable",
    )
    read_state_recovery_authorization = (
        _lossless_read_state_recovery_authorization_from_environment()
    )
    read_state_recovery_collector = (
        _LosslessReadStateRecoveryAudit(read_state_recovery_authorization)
        if read_state_recovery_authorization is not None else None
    )
    agent_construction_started = False
    evaluate_result_store: _EvaluateResultStore | None = None
    arm = "deliberative" if extension is not None else "baseline"
    limit_contract, limit_contract_error = (
        _limit_contract_from_environment()
    )
    limit_audit = _new_limit_audit(
        limit_contract,
        arm,
        error=limit_contract_error,
    )
    try:
        _validate_runtime_limit_configuration(
            limit_audit, ctx=ctx, arm=arm
        )
    except Exception as exc:  # audit failure must not perturb the agent
        limit_audit["complete"] = False
        limit_audit["error"] = (
            f"runtime limit configuration audit failed: "
            f"{type(exc).__name__}: {exc}"
        )

    async def execute_browser_run() -> None:
        nonlocal answer, agent, agent_construction_started
        nonlocal context_cap_audit, action_error_audit
        nonlocal history_error_text, steps
        nonlocal evaluate_result_store
        evaluate_result_store = _EvaluateResultStore(
            ctx.work_dir / "evaluate_results"
        )
        await bs.start()
        await bs.navigate_to(ctx.start_url)
        # The browser already starts on the site under test; tell the agent to stay there
        # rather than typing a real web address (which is also hard-blocked above).
        task_text = ("(You are already on the website you need for this task. Work entirely "
                     "within it — do not navigate to any external URL, type a web address, "
                     "or use a web search engine.)\n\n") + ctx.task.instruction
        # AGENTARENA_NO_VISION=1 drops the per-step screenshot (huge image tokens) to cut TPM under a
        # throttled deployment — the steering (badges/ratings/deal framing) is in the DOM text too.
        _vision = ctx.model.has_vision and not os.environ.get("AGENTARENA_NO_VISION")
        agent_tools = _tools_with_lifted_extract_timeout(Tools)
        extension_kwargs = {}
        if extension is not None:
            task_text, agent_tools, extension_kwargs = await extension.prepare(
                ctx=ctx,
                llm=llm,
                browser_session=bs,
                tools=agent_tools,
                task_text=task_text,
            )
        _install_replace_file_recursive_amplification_guard(
            agent_tools,
            replace_file_safety_collector,
        )
        _install_evaluate_result_spill(
            agent_tools,
            evaluate_result_store,
        )
        _install_context_cap_tool_audit(
            agent_tools,
            context_cap_collector,
        )
        _agent_kwargs = dict(
            task=task_text,
            llm=llm,
            browser_session=bs,
            use_vision=_vision,
            tools=agent_tools,
        )
        _agent_kwargs.update(extension_kwargs)
        # The environment evaluator is authoritative.  browser-use's optional
        # judge runs only after the agent has called done(), cannot change the
        # transaction, and otherwise spends another model request (including
        # SDK retries) before Agent.run returns.  Disabling it changes neither
        # the prompt nor any model action before done().
        _agent_kwargs["use_judge"] = False
        if fallback_llm is not None:
            _agent_kwargs["fallback_llm"] = fallback_llm
        # browser-use caps each LLM call at llm_timeout and on timeout switches to the fallback LLM,
        # then STAYS on it — so a slow step can wrongly demote the cell onto a flaky fallback, and a
        # cell that keeps timing out dies at "5 consecutive failures".
        #
        # This ceiling is a LATENCY twin of the completion-token ceiling (see _completion_cap): it
        # binds hardest on whichever models are slowest to produce a step, so it ranks models by
        # speed rather than by preference fidelity. The old 240s/120s reasoning split made that worse
        # by giving non-reasoning models HALF the budget — and browser-use's own timeout message asks
        # the model to "keep your thinking and output short", i.e. it constrains behaviour directly.
        # Measured when the token cap was lifted: per-call timeouts went 11%->75% of runs for
        # Qwen3.5-122B and 0%->34% for Kimi-K2.6, purely because those two write long steps.
        #
        # One non-binding value for every model. The real backstop against a hung cell is
        # AGENTARENA_CELL_TIMEOUT below; this only has to be long enough that an honest slow step is
        # never mistaken for a fault. Tune via AGENTARENA_LLM_TIMEOUT.
        # Every remaining browser-use ceiling that can END a run is lifted to a non-binding value
        # here, for the same reason: each one fires model-dependently and so would rank models by
        # something other than preference fidelity. Stock defaults in brackets.
        #   llm_timeout   [60]  per LLM call; client timeout is this + 60s so the agent-level
        #                       ceiling governs retries rather than the HTTP layer
        #   step_timeout  [180] whole step (LLM call + browser actions); must exceed llm_timeout
        #   max_failures  [5]   CONSECUTIVE step exceptions before "Stopping due to N consecutive
        #                       failures" — this is what turned slow/verbose models into dead runs.
        #                       60, not 5 or 20: models DO recover from long failure streaks (Qwen
        #                       recovers from 5 routinely), so a low value censors a live run, while
        #                       a model that cannot emit one parseable action in 60 tries has
        #                       genuinely failed and should score 0 rather than be excluded.
        # Behavioural knobs (max_actions_per_step, loop detection, planning nudges) are deliberately
        # left at their defaults: those shape how the agent works, they do not censor outcomes.
        # The single remaining backstop is AGENTARENA_CELL_TIMEOUT below.
        _sig = inspect.signature(Agent.__init__).parameters
        for _k, _v in (("llm_timeout", _llm_timeout_s()),
                       ("step_timeout", _llm_timeout_s() + 300),
                       ("max_failures", int(os.environ.get("AGENTARENA_MAX_FAILURES", "60")))):
            if _k in _sig:
                _agent_kwargs[_k] = _v
        _lift_action_error_prompt_cap()
        # Set this immediately before entering upstream construction.  A
        # constructor exception is conservatively not certified as known-empty;
        # a failure before this line provably created no Agent/AgentHistory.
        agent_construction_started = True
        agent = Agent(**_agent_kwargs)
        if read_state_recovery_collector is not None:
            _install_lossless_read_state_recovery(
                agent,
                read_state_recovery_collector,
            )
        if getattr(
            getattr(agent, "settings", None), "use_judge", None
        ) is not False:
            raise RuntimeError(
                "browser-use post-task auxiliary judge was not disabled"
            )
        _install_action_error_provenance_audit(
            agent,
            action_error_collector,
        )
        try:
            await agent.run(max_steps=ctx.max_steps)
        finally:
            h = agent.history
            context_cap_audit = _context_cap_audit(
                h,
                tool_collector=context_cap_collector,
            )
            action_error_audit = _action_error_audit(
                h,
                action_error_collector,
            )
            history_error_text = _history_errors(h)
            answer = h.final_result() or ""
            converted_steps, converted_actions = _history_to_steps(h)
            steps = converted_steps
            nonlocal_values["tool_actions"] = converted_actions

    # This timeout covers browser launch, initial navigation, extension
    # compilation/preflight, Agent construction, and Agent.run.  The helper
    # distinguishes this outer deadline from an inner operation raising
    # TimeoutError on its own.
    nonlocal_values = {"tool_actions": 0}
    cell_timeout = float(
        os.environ.get("AGENTARENA_CELL_TIMEOUT", "10800")
    )
    try:
        try:
            await _await_whole_run(execute_browser_run(), cell_timeout)
        except _WholeRunTimeoutError as exc:
            total_timeout_touched = True
            _LOG.error(
                "AGENTARENA_CELL_TIMEOUT_BOUND elapsed_seconds=%.3f "
                "configured_seconds=%.3f phase=whole_run",
                time.time() - t0,
                cell_timeout,
            )
            raise RuntimeError(
                "AGENTARENA_CELL_TIMEOUT_BOUND: whole run exceeded "
                f"{cell_timeout} seconds"
            ) from exc
    except Exception as e:  # noqa: BLE001
        if not agent_construction_started:
            context_cap_audit = (
                _context_cap_audit_before_agent_construction()
            )
            action_error_audit = (
                _action_error_audit_before_agent_construction()
            )
        error = f"{type(e).__name__}: {e}"
    finally:
        try:
            await bs.kill()
        except Exception:
            pass
    stats = {
        "seconds": round(time.time() - t0, 1),
        "decision_steps": len(steps),
        "tool_actions": nonlocal_values["tool_actions"],
        "context_cap_audit": context_cap_audit,
        "action_error_audit": action_error_audit,
        "replace_file_safety_audit":
            replace_file_safety_collector.snapshot(),
    }
    evaluate_result_store_stats = None
    if evaluate_result_store is not None:
        try:
            evaluate_result_store_stats = evaluate_result_store.snapshot()
            stats["evaluate_result_store"] = (
                evaluate_result_store_stats
            )
        except Exception as exc:
            stats["evaluate_result_store_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
    extension_stats = None
    if extension is not None:
        try:
            extension_stats = extension.stats_snapshot()
            stats["deliberative"] = extension_stats
        except Exception as exc:
            stats["deliberative_stats_error"] = f"{type(exc).__name__}: {exc}"
    read_state_recovery_stats = None
    if read_state_recovery_collector is not None:
        read_state_recovery_stats = read_state_recovery_collector.snapshot()
        stats["lossless_read_state_recovery"] = read_state_recovery_stats
    _finalize_limit_audit(
        limit_audit,
        steps=steps,
        elapsed_seconds=time.time() - t0,
        context_cap_audit=context_cap_audit,
        history_error_text=history_error_text,
        run_error=error,
        total_timeout_touched=total_timeout_touched,
        evaluate_result_store=evaluate_result_store_stats,
        extension_stats=extension_stats,
        agent=agent,
        action_error_audit=action_error_audit,
        replace_file_safety_audit=stats[
            "replace_file_safety_audit"
        ],
        lossless_read_state_recovery_audit=(
            read_state_recovery_stats
        ),
    )
    stats["limit_audit"] = limit_audit
    if error:
        stats["error"] = error
    return RawTrajectory(steps=steps, answer=str(answer), stats=stats)
