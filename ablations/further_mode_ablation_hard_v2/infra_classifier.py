"""Campaign-specific but-for infrastructure classifier.

This wraps the benchmark-wide classifier to handle the deliberative contract
compiler, which runs before the first browser step.  A semantic/structured
compiler failure is model capability (score zero), never a zero-step redraw;
only a provider/transport failure that terminates compilation is infrastructure.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import _infra_classify as shared


VERSION = "further-mode-infra-classifier-v1"
COMPILER_SCAFFOLDS = {
    "browseruse-deliberative-contract-only",
    "browseruse-deliberative-feasibility",
    "browseruse-deliberative-no-coverage",
    "browseruse-deliberative-coverage-advisory",
    "browseruse-deliberative",
}
STARTUP_MARKERS = (
    "AGENTARENA_ENV_STARTUP_TIMEOUT",
    "AGENTARENA_ENV_SERVER_EXITED",
    "environment server exited before readiness",
    "Page.navigate() timed out",
    "Navigation failed:",
)
COMPILER_MARKER = "contract compilation failed after four attempts:"
PARTIAL_EXTERNAL_MARKERS = (
    "Out of memory", "OOMKilled", "Killed process", "SIGKILL",
    "AGENTARENA_RUN_PROCESS_SPAWN_FAILED", "TargetClosedError",
    "ConnectionResetError", "ConnectionRefusedError",
)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def classify_run(cell_dir: str) -> dict:
    root = Path(cell_dir)
    try:
        summary = json.loads((root / "summary.json").read_text())
    except Exception:  # noqa: BLE001
        return shared.classify_run(cell_dir)
    chosen = summary.get("chosen")
    outcome = summary.get("outcome")
    steps = int(summary.get("num_steps") or summary.get("steps") or 0)
    base = {"steps": steps, "outcome": outcome}

    # A genuine transaction is measured regardless of any earlier fault text.
    if chosen is not None:
        return {"class": shared.SCORED, "code": "transacted",
                "evidence": "", **base}

    log = _read(root / "run.log")
    error = str(summary.get("error") or "")
    combined = f"{log}\n{error}"

    # Compiler termination precedes browser steps, so decide it before the
    # shared zero-step rule. Inspect only the terminal compiler payload; an
    # earlier recovered provider fault elsewhere in the log is irrelevant.
    if summary.get("scaffold") in COMPILER_SCAFFOLDS and COMPILER_MARKER in combined:
        payload = combined.rsplit(COMPILER_MARKER, 1)[1][:2000]
        if shared._reason_class(payload) == "endpoint":
            return {
                "class": shared.INFRA,
                "code": "compiler_endpoint_termination",
                "evidence": payload[:300],
                **base,
            }
        return {
            "class": shared.CAPABILITY,
            "code": "compiler_semantic_or_output_failure",
            "evidence": payload[:300],
            **base,
        }

    # The shared classifier historically treats outcome=error as transacted.
    # Correct only exact terminal startup/navigation/evaluator/provider cases
    # with no chosen item. Arbitrary errors remain behavioral/ambiguous.
    if outcome in {"error", None}:
        if shared.EVALUATOR_GET_RETRIES_EXHAUSTED in error:
            return {
                "class": shared.INFRA,
                "code": "evaluator_get_exhausted",
                "evidence": error[:300],
                **base,
            }
        marker = next((value for value in STARTUP_MARKERS if value in combined), None)
        if steps == 0 and marker:
            return {
                "class": shared.INFRA,
                "code": "zero_step_startup_or_navigation",
                "evidence": marker,
                **base,
            }
        tail = "\n".join(combined.splitlines()[-shared.TAIL_LINES:])
        if steps == 0 and (
            shared._reason_class(tail) == "endpoint"
            or any(value in tail for value in shared.TRANSPORT_DEATH)
        ):
            return {
                "class": shared.INFRA,
                "code": "zero_step_endpoint_or_transport",
                "evidence": tail[-300:],
                **base,
            }
        return {
            "class": shared.AMBIGUOUS,
            "code": "nontransaction_error_not_proven_infrastructure",
            "evidence": error[:300],
            **base,
        }
    return shared.classify_run(cell_dir)


def is_infra_fail(cell_dir: str) -> bool:
    return classify_run(cell_dir)["class"] == shared.INFRA


def classify_partial_attempt(terminal: dict, launcher_text: str) -> dict:
    """Classify a definitely-ended attempt that produced no summary."""
    base = {"steps": 0, "outcome": None}
    spawn_type = terminal.get("spawn_exception_type")
    proven_spawn_failure = (
        terminal.get("process_pid") is None
        and spawn_type in {"OSError", "FileNotFoundError", "PermissionError"}
    )
    checkout_absence_proven = (
        terminal.get("database_order_count") == 0
        or (
            proven_spawn_failure
            and terminal.get("database_order_count") == -1
        )
    )
    if (
        terminal.get("process_ended") is not True
        or terminal.get("summary_exists") is not False
        or not checkout_absence_proven
    ):
        return {"class": shared.AMBIGUOUS, "code": "partial_not_proven_empty",
                "evidence": "terminal/order/summary proof incomplete", **base}
    capability_markers = (
        COMPILER_MARKER, *shared.OUTPUT_FAULT, *shared.CAP_TRUNCATION,
        "Result failed ", "Judge Verdict", "Final Result:",
    )
    marker = next((value for value in capability_markers
                   if value in launcher_text), None)
    if marker:
        return {"class": shared.CAPABILITY,
                "code": "partial_capability_evidence",
                "evidence": marker, **base}
    if terminal.get("logged_agent_step_evidence") is not False:
        return {"class": shared.AMBIGUOUS, "code": "partial_behavior_possible",
                "evidence": "agent/browser step evidence exists", **base}
    external = next((value for value in PARTIAL_EXTERNAL_MARKERS
                     if value in launcher_text), None)
    returncode = terminal.get("returncode")
    if external or returncode in {-9, -11} or spawn_type in {
        "OSError", "FileNotFoundError", "PermissionError"
    }:
        return {"class": shared.INFRA,
                "code": "partial_external_termination",
                "evidence": external or f"returncode={returncode}; spawn={spawn_type}",
                **base}
    return {"class": shared.AMBIGUOUS,
            "code": "partial_termination_not_proven_infrastructure",
            "evidence": f"returncode={returncode}; spawn={spawn_type}", **base}
