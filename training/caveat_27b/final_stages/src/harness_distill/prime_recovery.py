"""Crash-safe checkpoint reconciliation for the pinned PRIME-RL runtime.

PRIME-RL 0.7 resolves ``resume_step = -1`` by taking the numerically largest
``checkpoints/step_*`` directory.  Directory creation precedes distributed
checkpoint completion, and the single-run RL trainer and orchestrator write
to independent checkpoint roots.  A preemption can therefore leave a partial
latest trainer checkpoint or two valid-but-different latest steps.

This module runs under the harness launch lock before PRIME starts.  SFT may
resume only when its complete trainer and weight-export contracts agree.  OPD
mid-round resume is scientifically unsafe because PRIME omits sampler and
in-flight state, so an incomplete OPD round is quarantined and restarted; an
exact terminal trainer/export pair is adopted.  Every quarantine move uses a
same-filesystem atomic rename, is journaled before it starts, and is
idempotently finished after a kill at any rename boundary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import atomic_json

RECOVERY_SCHEMA = "harness-distill.prime-recovery.v1"
_STEP_RE = re.compile(r"step_(\d+)")
_ADAPTER_WEIGHT_NAMES = ("adapter_model.safetensors", "adapter_model.bin")


class PrimeRecoveryError(RuntimeError):
    """A checkpoint tree cannot be reconciled without guessing."""


@dataclass(frozen=True)
class PrimeRecoveryReport:
    kind: Literal["sft", "opd"]
    selected_step: int | None
    compatible_steps: tuple[int, ...]
    final_complete: bool
    resumed: bool
    archived_directories: int
    manifest_path: Path
    inventory: dict[str, Any]
    continuation_semantics: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["manifest_path"] = str(self.manifest_path)
        payload["schema"] = RECOVERY_SCHEMA
        return payload


def _scan_steps(root: Path) -> dict[int, Path]:
    """Return strict step directories; malformed step-ish entries fail closed."""

    if not root.exists():
        return {}
    if not root.is_dir() or root.is_symlink():
        raise PrimeRecoveryError(f"checkpoint root is not a real directory: {root}")
    found: dict[int, Path] = {}
    for entry in sorted(root.iterdir(), key=lambda path: path.name):
        if not entry.name.startswith("step_"):
            continue
        match = _STEP_RE.fullmatch(entry.name)
        if match is None:
            raise PrimeRecoveryError(f"malformed PRIME step entry: {entry}")
        if not entry.is_dir() or entry.is_symlink():
            raise PrimeRecoveryError(f"PRIME step entry is not a real directory: {entry}")
        step = int(match.group(1))
        if step in found:
            raise PrimeRecoveryError(f"duplicate PRIME step {step} under {root}")
        found[step] = entry
    return found


def _regular_nonempty(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def _trainer_checkpoint_status(
    step_dir: Path,
    *,
    sft_world_size: int | None,
) -> tuple[bool, list[str]]:
    """Validate a DCP commit, including every shard referenced by metadata."""

    trainer = step_dir / "trainer"
    metadata_path = trainer / ".metadata"
    reasons: list[str] = []
    if not _regular_nonempty(metadata_path):
        return False, ["missing-or-empty-dcp-metadata"]
    try:
        # Torch is supplied by the pinned PRIME environment.  Import lazily so
        # config-only users of the harness do not acquire a new dependency.
        from torch.distributed.checkpoint import FileSystemReader

        metadata = FileSystemReader(trainer).read_metadata()
    except Exception as exc:  # noqa: BLE001 - corruption/type drift is evidence
        return False, [f"unreadable-dcp-metadata:{type(exc).__name__}"]

    storage_data = getattr(metadata, "storage_data", None)
    if not isinstance(storage_data, dict) or not storage_data:
        reasons.append("dcp-metadata-has-no-storage-data")
        return False, reasons
    referenced: set[str] = set()
    metadata_mtime = metadata_path.stat().st_mtime_ns
    for info in storage_data.values():
        raw_relative = getattr(info, "relative_path", None)
        offset = getattr(info, "offset", None)
        length = getattr(info, "length", None)
        if (
            not isinstance(raw_relative, str)
            or not isinstance(offset, int)
            or not isinstance(length, int)
        ):
            reasons.append("malformed-dcp-storage-record")
            continue
        relative = Path(raw_relative)
        if relative.is_absolute() or ".." in relative.parts:
            reasons.append("unsafe-dcp-storage-path")
            continue
        shard = trainer / relative
        referenced.add(relative.as_posix())
        if not _regular_nonempty(shard):
            reasons.append(f"missing-dcp-shard:{relative.as_posix()}")
            continue
        if offset < 0 or length <= 0 or shard.stat().st_size < offset + length:
            reasons.append(f"truncated-dcp-shard:{relative.as_posix()}")
        if shard.stat().st_mtime_ns > metadata_mtime:
            reasons.append(f"dcp-shard-newer-than-metadata:{relative.as_posix()}")

    actual = {
        path.relative_to(trainer).as_posix() for path in trainer.rglob("*.distcp") if path.is_file()
    }
    if actual != referenced:
        reasons.append("dcp-shard-set-differs-from-metadata")

    if sft_world_size is not None:
        dataloader = trainer / "dataloader"
        expected = {f"rank_{rank}.pt" for rank in range(sft_world_size)}
        actual_dataloader = (
            {path.name for path in dataloader.glob("rank_*.pt") if path.is_file()}
            if dataloader.is_dir() and not dataloader.is_symlink()
            else set()
        )
        if actual_dataloader != expected:
            reasons.append("sft-dataloader-rank-set-incomplete")
        for name in sorted(expected & actual_dataloader):
            rank_state = dataloader / name
            if not _regular_nonempty(rank_state):
                reasons.append(f"empty-sft-dataloader-state:{name}")
                continue
            if rank_state.stat().st_mtime_ns > metadata_mtime:
                reasons.append(f"sft-dataloader-newer-than-metadata:{name}")
            try:
                import torch

                with rank_state.open("rb") as stream:
                    loaded = torch.load(stream, weights_only=False, map_location="cpu")
                if not isinstance(loaded, dict):
                    reasons.append(f"malformed-sft-dataloader-state:{name}")
            except Exception as exc:  # noqa: BLE001 - corruption/type drift is evidence
                reasons.append(f"unreadable-sft-dataloader-state:{name}:{type(exc).__name__}")
    return not reasons, reasons


def _adapter_status(step_dir: Path, *, nested: bool) -> tuple[bool, list[str]]:
    """Validate the LoRA export that PRIME attests with ``STABLE``."""

    stable = step_dir / "STABLE"
    adapter = step_dir / "lora_adapters" if nested else step_dir
    reasons: list[str] = []
    if not stable.is_file() or stable.is_symlink():
        return False, ["missing-stable-marker"]
    config_path = adapter / "adapter_config.json"
    if not _regular_nonempty(config_path):
        reasons.append("missing-adapter-config")
        config: dict[str, Any] = {}
    else:
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            config = {}
        if not config:
            reasons.append("invalid-adapter-config")
    if config:
        try:
            expected_shape = config.get("r") == 64 and float(config.get("lora_alpha")) == 128.0
        except (TypeError, ValueError):
            expected_shape = False
        if not expected_shape:
            reasons.append("adapter-rank-or-alpha-mismatch")
        targets = config.get("target_modules")
        if (
            not isinstance(targets, list)
            or not targets
            or not all(isinstance(target, str) and target for target in targets)
        ):
            reasons.append("adapter-has-no-target-modules")
    weights = [adapter / name for name in _ADAPTER_WEIGHT_NAMES if (adapter / name).exists()]
    if len(weights) != 1 or not _regular_nonempty(weights[0]):
        reasons.append("missing-or-ambiguous-adapter-weights")
    stable_mtime = stable.stat().st_mtime_ns
    for artifact in [config_path, *weights]:
        if artifact.is_file() and artifact.stat().st_mtime_ns > stable_mtime:
            reasons.append(f"adapter-artifact-newer-than-stable:{artifact.name}")
    return not reasons, reasons


def _orchestrator_status(step_dir: Path, *, step: int) -> tuple[bool, list[str]]:
    """Validate PRIME's atomic progress payload for operational alignment."""

    orchestrator = step_dir / "orchestrator"
    state_file = orchestrator / "progress.pt"
    reasons: list[str] = []
    if not _regular_nonempty(state_file):
        return False, ["missing-or-empty-orchestrator-progress"]
    if any(orchestrator.glob("progress.pt.*.tmp")):
        reasons.append("orchestrator-progress-has-partial-temp")
    try:
        import torch

        with state_file.open("rb") as stream:
            state = torch.load(stream, weights_only=False, map_location="cpu")
    except Exception as exc:  # noqa: BLE001 - corrupt/incompatible pickle is evidence
        return False, [*reasons, f"unreadable-orchestrator-progress:{type(exc).__name__}"]
    if not isinstance(state, dict) or "progress" not in state:
        reasons.append("orchestrator-progress-payload-malformed")
        return False, reasons
    progress = state["progress"]
    progress_step = (
        progress.get("step") if isinstance(progress, dict) else getattr(progress, "step", None)
    )
    # Interval checkpoints serialize the already-incremented next step; the
    # final teardown checkpoint serializes the just-finished step.
    if progress_step not in {step, step + 1}:
        reasons.append("orchestrator-progress-step-mismatch")
    return not reasons, reasons


def _identity_payload(
    *,
    kind: str,
    output_dir: Path,
    config_sha256: str,
    prime_max_steps: int,
    sft_world_size: int | None,
) -> dict[str, Any]:
    return {
        "schema": RECOVERY_SCHEMA,
        "kind": kind,
        "output_dir": str(output_dir),
        "config_sha256": config_sha256,
        "prime_max_steps": prime_max_steps,
        "sft_world_size": sft_world_size,
    }


def _validate_move(
    move: dict[str, str], *, roots: dict[str, Path], attempt: Path
) -> tuple[Path, Path]:
    label = move.get("root")
    if label not in roots:
        raise PrimeRecoveryError("recovery journal names an unknown checkpoint root")
    source = Path(move.get("source", "")).resolve()
    destination = Path(move.get("destination", "")).resolve()
    expected_parent = roots[label].resolve()
    expected_destination_parent = (attempt / "archived" / label).resolve()
    if source.parent != expected_parent or destination.parent != expected_destination_parent:
        raise PrimeRecoveryError("recovery journal path escaped its attested root")
    if not _STEP_RE.fullmatch(source.name) or destination.name != source.name:
        raise PrimeRecoveryError("recovery journal contains a malformed step path")
    return source, destination


def _finish_attempt(attempt: Path, *, roots: dict[str, Path]) -> None:
    plan_path = attempt / "plan.json"
    complete_path = attempt / "complete.json"
    if complete_path.is_file():
        return
    if not plan_path.is_file():
        raise PrimeRecoveryError(f"recovery attempt has no atomic plan: {attempt}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    moves = plan.get("moves")
    if plan.get("schema") != RECOVERY_SCHEMA or not isinstance(moves, list):
        raise PrimeRecoveryError(f"malformed recovery plan: {plan_path}")
    for move in moves:
        if not isinstance(move, dict):
            raise PrimeRecoveryError(f"malformed move in recovery plan: {plan_path}")
        source, destination = _validate_move(move, roots=roots, attempt=attempt)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_exists = source.exists()
        destination_exists = destination.exists()
        if source_exists and destination_exists:
            raise PrimeRecoveryError(f"both recovery source and destination exist: {source}")
        if not source_exists and not destination_exists:
            raise PrimeRecoveryError(f"both recovery source and destination are absent: {source}")
        if source_exists:
            os.replace(source, destination)
    atomic_json(
        complete_path,
        {
            "schema": RECOVERY_SCHEMA,
            "status": "complete",
            "move_count": len(moves),
        },
    )


def _recover_pending(recovery_dir: Path, *, roots: dict[str, Path]) -> None:
    if not recovery_dir.exists():
        return
    for staging in sorted(recovery_dir.glob(".quarantine-*.planning")):
        if not staging.is_dir() or staging.is_symlink():
            raise PrimeRecoveryError(f"malformed staged recovery attempt: {staging}")
        match = re.fullmatch(r"\.(quarantine-\d+)\.planning", staging.name)
        if match is None:
            raise PrimeRecoveryError(f"malformed staged recovery attempt name: {staging}")
        attempt = recovery_dir / match.group(1)
        if attempt.exists():
            raise PrimeRecoveryError(f"both staged and active recovery attempts exist: {attempt}")
        if (staging / "plan.json").is_file():
            os.replace(staging, attempt)
        else:
            # No journal means no rename could have started. Preserve the empty
            # staging directory as evidence and permit a new planned attempt.
            suffix = 1
            abandoned = recovery_dir / f".abandoned-{match.group(1)}-{suffix:06d}"
            while abandoned.exists():
                suffix += 1
                abandoned = recovery_dir / f".abandoned-{match.group(1)}-{suffix:06d}"
            os.replace(staging, abandoned)
    for attempt in sorted(recovery_dir.glob("quarantine-*")):
        if not attempt.is_dir() or attempt.is_symlink():
            raise PrimeRecoveryError(f"malformed recovery attempt: {attempt}")
        _finish_attempt(attempt, roots=roots)


def _new_attempt(recovery_dir: Path) -> Path:
    numbers: list[int] = []
    for path in recovery_dir.glob("quarantine-*"):
        match = re.fullmatch(r"quarantine-(\d+)", path.name)
        if match is None:
            raise PrimeRecoveryError(f"malformed recovery attempt name: {path}")
        numbers.append(int(match.group(1)))
    return recovery_dir / f"quarantine-{max(numbers, default=0) + 1:06d}"


def reconcile_prime_output(
    *,
    kind: Literal["sft", "opd"],
    output_dir: str | Path,
    phase_dir: str | Path,
    config_sha256: str,
    prime_max_steps: int,
    sft_world_size: int | None = None,
) -> PrimeRecoveryReport:
    """Select a safe SFT resume or terminal OPD adoption and quarantine the rest."""

    output = Path(output_dir).resolve()
    phase = Path(phase_dir).resolve()
    if kind == "sft":
        if sft_world_size is None or sft_world_size < 1:
            raise PrimeRecoveryError("SFT recovery requires the audited trainer world size")
    elif sft_world_size is not None:
        raise PrimeRecoveryError("OPD recovery must not declare an SFT world size")
    if prime_max_steps < 1:
        raise PrimeRecoveryError("PRIME terminal step must be positive")

    roots: dict[str, Path] = {
        "trainer_checkpoints": output / "checkpoints",
        "weights": output / "weights",
    }
    if kind == "opd":
        roots.update(
            {
                "orchestrator_checkpoints": output / "run_default" / "checkpoints",
                "broadcasts": output / "run_default" / "broadcasts",
                "rollouts": output / "run_default" / "rollouts",
                "top_level_rollouts": output / "rollouts",
            }
        )

    recovery_dir = phase / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    identity = _identity_payload(
        kind=kind,
        output_dir=output,
        config_sha256=config_sha256,
        prime_max_steps=prime_max_steps,
        sft_world_size=sft_world_size,
    )
    identity_path = recovery_dir / "identity.json"
    if identity_path.is_file():
        if json.loads(identity_path.read_text(encoding="utf-8")) != identity:
            raise PrimeRecoveryError("recovery identity differs from this PRIME artifact")
    else:
        atomic_json(identity_path, identity)

    _recover_pending(recovery_dir, roots=roots)
    scanned = {label: _scan_steps(root) for label, root in roots.items()}
    all_steps = sorted({step for steps in scanned.values() for step in steps})
    inventory: dict[str, Any] = {
        label: {str(step): {"path": str(path)} for step, path in steps.items()}
        for label, steps in scanned.items()
    }
    compatible: list[int] = []
    trainer_weight_committed: list[int] = []
    for step in all_steps:
        checks: dict[str, dict[str, Any]] = {}
        trainer_path = scanned["trainer_checkpoints"].get(step)
        trainer_ok, trainer_reasons = (
            _trainer_checkpoint_status(trainer_path, sft_world_size=sft_world_size)
            if trainer_path is not None
            else (False, ["missing-trainer-step"])
        )
        checks["trainer"] = {"complete": trainer_ok, "reasons": trainer_reasons}
        weight_path = scanned["weights"].get(step)
        weights_ok, weights_reasons = (
            _adapter_status(weight_path, nested=True)
            if weight_path is not None
            else (False, ["missing-weight-step"])
        )
        checks["weights"] = {"complete": weights_ok, "reasons": weights_reasons}
        step_ok = trainer_ok and weights_ok
        if step_ok:
            trainer_weight_committed.append(step)
        if kind == "opd":
            orch_path = scanned["orchestrator_checkpoints"].get(step)
            orch_ok, orch_reasons = (
                _orchestrator_status(orch_path, step=step)
                if orch_path is not None
                else (False, ["missing-orchestrator-step"])
            )
            checks["orchestrator"] = {"complete": orch_ok, "reasons": orch_reasons}
            broadcast_path = scanned["broadcasts"].get(step)
            broadcast_ok, broadcast_reasons = (
                _adapter_status(broadcast_path, nested=False)
                if broadcast_path is not None
                else (False, ["missing-broadcast-step"])
            )
            checks["broadcast"] = {
                "complete": broadcast_ok,
                "reasons": broadcast_reasons,
            }
            step_ok = step_ok and orch_ok and broadcast_ok
        inventory.setdefault("checks", {})[str(step)] = checks
        if step_ok:
            compatible.append(step)

    if any(step > prime_max_steps for step in trainer_weight_committed):
        raise PrimeRecoveryError("compatible checkpoint lies beyond the configured final step")
    # SFT's DCP includes the StatefulDataLoader and is safe to resume.  PRIME's
    # OPD orchestrator checkpoint does not include TrainSource, dispatcher,
    # TrainSink, UUID, or inference RNG state.  A mid-round OPD resume would
    # silently duplicate/omit exposure, so an incomplete OPD round restarts
    # from its immutable parent.  A terminal trainer checkpoint plus STABLE
    # final adapter is already the desired output and can be adopted directly.
    if kind == "sft":
        selected = max(compatible, default=None)
    else:
        selected = prime_max_steps if prime_max_steps in trainer_weight_committed else None
    compatible_set = set(compatible)
    trainer_weight_set = set(trainer_weight_committed)
    moves: list[dict[str, str]] = []
    checkpoint_labels = {"trainer_checkpoints", "weights", "orchestrator_checkpoints"}
    for label, steps in scanned.items():
        for step, source in steps.items():
            if kind == "opd" and selected is None:
                keep = False
            elif kind == "opd" and label in {"trainer_checkpoints", "weights"}:
                keep = step in trainer_weight_set and step <= selected
            elif label in checkpoint_labels:
                keep = step in compatible_set and (selected is not None and step <= selected)
            else:
                keep = selected is not None and step <= selected
            if keep:
                continue
            moves.append({"root": label, "source": str(source), "reason": "not-common-committed"})

    if moves:
        attempt = _new_attempt(recovery_dir)
        staging = recovery_dir / f".{attempt.name}.planning"
        staging.mkdir(parents=False, exist_ok=False)
        planned: list[dict[str, str]] = []
        for move in moves:
            source = Path(move["source"])
            destination = attempt / "archived" / move["root"] / source.name
            planned.append({**move, "destination": str(destination)})
        atomic_json(
            staging / "plan.json",
            {
                "schema": RECOVERY_SCHEMA,
                "status": "planned",
                "identity": identity,
                "selected_step": selected,
                "moves": planned,
            },
        )
        os.replace(staging, attempt)
        _finish_attempt(attempt, roots=roots)

    continuation = (
        "SFT resumes only from a complete DCP plus stable LoRA export; the DCP-restored "
        "StatefulDataLoader preserves its stochastic cursor state."
        if kind == "sft"
        else "OPD mid-round resume is forbidden because PRIME omits sampler, in-flight, sink, "
        "UUID, and inference RNG state. An incomplete round is quarantined and restarted from "
        "its immutable parent; an exact terminal DCP plus STABLE adapter is verified and adopted."
    )
    latest_path = recovery_dir / "latest.json"
    report_payload = {
        "schema": RECOVERY_SCHEMA,
        "kind": kind,
        "selected_step": selected,
        "compatible_steps": compatible,
        "final_complete": selected == prime_max_steps,
        "resumed": selected is not None,
        "archived_directories": len(moves),
        "inventory": inventory,
        "continuation_semantics": continuation,
    }
    atomic_json(latest_path, report_payload)
    return PrimeRecoveryReport(
        kind=kind,
        selected_step=selected,
        compatible_steps=tuple(compatible),
        final_complete=selected == prime_max_steps,
        resumed=selected is not None,
        archived_directories=len(moves),
        manifest_path=latest_path,
        inventory=inventory,
        continuation_semantics=continuation,
    )
