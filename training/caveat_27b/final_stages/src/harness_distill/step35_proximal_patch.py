"""Fail-closed PRIME patch for the Step35 Step32-anchored trust region.

PRIME's transport can carry sampled reference-token log probabilities, but the
trainer does not own a frozen reference model and therefore cannot compute a
full-vocabulary reference KL by itself.  Step35 uses the feasible exact
fallback: snapshot every local shard of the DCP-loaded Step32 LoRA, add a
proximal gradient on later micro-updates, and project every completed update
back into a small RMS ball around that snapshot.

The patch is deliberately narrow and only accepts the reviewed Step34 trainer
source.  It is applied in an isolated training reservation before torchrun.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .sol_dagger_training import sha256_file

PRIME_TRAIN_RELATIVE = Path("src/prime_rl/trainer/rl/train.py")
INPUT_SHA256 = "90c57221bbfd2331f6e80266ee66a17f4099b2ad19da4fd61be59747f710498b"
OUTPUT_SHA256 = "58976e6e4eb596deafbb415db1cc75f7eacb3ea2c1b20991ccb833c5a67399aa"
SOURCE_STEP = 32
PROFILES = {
    "A": {
        "final_step": 35,
        "optimizer_updates": 3,
        "learning_rate": 2.0e-7,
        "proximal_lambda": 1.0e-2,
        "max_delta_rms": 2.0e-6,
        "strength": "strong",
    },
    "B": {
        "final_step": 34,
        "optimizer_updates": 2,
        "learning_rate": 5.0e-7,
        "proximal_lambda": 5.0e-3,
        "max_delta_rms": 4.0e-6,
        "strength": "medium",
    },
}
# Compatibility aliases identify the conservative A profile. New plans bind
# one explicit profile and the patched trainer rejects an absent profile.
FINAL_STEP = int(PROFILES["A"]["final_step"])
PROXIMAL_LAMBDA = float(PROFILES["A"]["proximal_lambda"])
MAX_DELTA_RMS = float(PROFILES["A"]["max_delta_rms"])
EXPECTED_UPDATES = int(PROFILES["A"]["optimizer_updates"])


class Step35ProximalPatchError(RuntimeError):
    """The reviewed PRIME patch could not be applied exactly."""


_HELPERS = r'''

import os

# Step35: exact Step32-parent proximal trust region.  Anchor tensors are local
# shards, so the extra memory is proportional to the locally-held LoRA only.
_STEP35_SOURCE_STEP = 32
_STEP35_PROFILES = {
    "A": {
        "final_step": 35,
        "optimizer_updates": 3,
        "learning_rate": 2.0e-7,
        "proximal_lambda": 1.0e-2,
        "max_delta_rms": 2.0e-6,
    },
    "B": {
        "final_step": 34,
        "optimizer_updates": 2,
        "learning_rate": 5.0e-7,
        "proximal_lambda": 5.0e-3,
        "max_delta_rms": 4.0e-6,
    },
}


def _step35_local(parameter):
    value = parameter.detach()
    return value.to_local() if hasattr(value, "to_local") else value


def _step35_snapshot_parent(multi_run_manager):
    anchors = []
    for name, parameter in multi_run_manager.get_named_parameters_for_run(0):
        if not parameter.requires_grad:
            continue
        local = _step35_local(parameter)
        anchors.append((name, parameter, local.clone()))
    if not anchors:
        raise RuntimeError("STEP35_PROXIMAL_ANCHOR has no trainable LoRA shards")
    local_elements = sum(anchor.numel() for _, _, anchor in anchors)
    counts = [None] * dist.get_world_size()
    dist.all_gather_object(counts, {"rank": dist.get_rank(), "elements": local_elements})
    logger_payload = json.dumps(counts, sort_keys=True, separators=(",", ":"))
    return anchors, hashlib.sha256(logger_payload.encode()).hexdigest(), local_elements


def _step35_delta_stats(anchors):
    device = _step35_local(anchors[0][1]).device
    local_sumsq = torch.zeros((), dtype=torch.float64, device=device)
    local_count = 0
    for _, parameter, anchor in anchors:
        delta = _step35_local(parameter).float() - anchor.float()
        local_sumsq += delta.double().square().sum()
        local_count += delta.numel()
    totals = torch.stack(
        [local_sumsq, torch.tensor(float(local_count), dtype=torch.float64, device=device)]
    )
    dist.all_reduce(totals, op=dist.ReduceOp.SUM)
    rms = torch.sqrt(totals[0] / totals[1].clamp_min(1.0))
    return float(rms.item()), int(totals[1].item())


def _step35_add_proximal_gradient(anchors, proximal_lambda):
    for _, parameter, anchor in anchors:
        if parameter.grad is None:
            continue
        gradient = (
            parameter.grad.to_local()
            if hasattr(parameter.grad, "to_local")
            else parameter.grad
        )
        delta = (_step35_local(parameter).float() - anchor.float()).to(dtype=gradient.dtype)
        gradient.add_(delta, alpha=2.0 * proximal_lambda)


def _step35_project_and_audit(anchors, max_delta_rms):
    before_rms, global_elements = _step35_delta_stats(anchors)
    scale = min(1.0, max_delta_rms / max(before_rms, 1.0e-30))
    if scale < 1.0:
        with torch.no_grad():
            for _, parameter, anchor in anchors:
                local = _step35_local(parameter)
                projected = anchor.float() + (local.float() - anchor.float()) * scale
                local.copy_(projected.to(dtype=local.dtype))
    after_rms, observed_elements = _step35_delta_stats(anchors)
    if observed_elements != global_elements or after_rms > max_delta_rms * 1.001:
        raise RuntimeError("STEP35_PROXIMAL_AUDIT projection failed")
    return before_rms, after_rms, scale, global_elements
'''


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise Step35ProximalPatchError(
            f"Step35 PRIME anchor {label!r} occurs {text.count(old)} times"
        )
    return text.replace(old, new, 1)


def patched_text(source: str) -> str:
    """Return the exact Step35 successor of the reviewed PRIME train source."""

    start_log = (
        "    logger.info(\n"
        '        f"Starting from step {progress.step} '
        '(total_tokens={progress.total_tokens}, total_samples={progress.total_samples})"\n'
        "    )\n"
    )
    source = _replace_once(
        source,
        "\n\n@clean_exit\ndef train(config: TrainerConfig):",
        _HELPERS + "\n\n@clean_exit\ndef train(config: TrainerConfig):",
        "helper insertion",
    )
    source = _replace_once(
        source,
        start_log,
        """    step35_profile_name = os.environ.get("STEP35_PROXIMAL_PROFILE", "")
    step35_profile = _STEP35_PROFILES.get(step35_profile_name)
    if (
        step35_profile is None
        or config.max_concurrent_runs != 1
        or config.model.lora is None
        or checkpoint_step != _STEP35_SOURCE_STEP
        or config.max_steps != step35_profile["final_step"]
        or abs(float(config.optim.lr) - step35_profile["learning_rate"]) > 1.0e-15
    ):
        raise RuntimeError("STEP35_PROXIMAL_ANCHOR profile/config binding failed")
    step35_parent_anchor, step35_anchor_digest, step35_local_elements = _step35_snapshot_parent(
        multi_run_manager
    )
    logger.info(
        "STEP35_PROXIMAL_ANCHOR "
        f"profile={step35_profile_name} source_step={checkpoint_step} "
        f"final_step={config.max_steps} lambda={step35_profile['proximal_lambda']:.8g} "
        f"max_delta_rms={step35_profile['max_delta_rms']:.8g} "
        f"local_elements={step35_local_elements} digest={step35_anchor_digest} status=ok"
    )

    logger.info(
        f"Starting from step {progress.step} "
        f"(total_tokens={progress.total_tokens}, total_samples={progress.total_samples})"
    )
""",
        "parent snapshot",
    )
    source = _replace_once(
        source,
        """        # Optionally, clip the gradients
        grad_norm: torch.Tensor | None = None
""",
        """        # Add the exact proximal gradient before the ordinary global clip.
        _step35_add_proximal_gradient(
            step35_parent_anchor, step35_profile["proximal_lambda"]
        )

        # Optionally, clip the gradients
        grad_norm: torch.Tensor | None = None
""",
        "proximal gradient",
    )
    source = _replace_once(
        source,
        """        optimizer.step()
        optimizer.zero_grad()
""",
        """        optimizer.step()
        (
            step35_delta_before_rms,
            step35_delta_after_rms,
            step35_projection_scale,
            step35_global_elements,
        ) = _step35_project_and_audit(
            step35_parent_anchor, step35_profile["max_delta_rms"]
        )
        logger.info(
            "STEP35_PROXIMAL_AUDIT "
            f"step={progress.step} before_rms={step35_delta_before_rms:.12g} "
            f"after_rms={step35_delta_after_rms:.12g} scale={step35_projection_scale:.12g} "
            f"global_elements={step35_global_elements} status=ok"
        )
        optimizer.zero_grad()
""",
        "post-update projection",
    )
    return source


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def apply_patch(prime_root: str | Path, evidence_path: str | Path) -> dict[str, Any]:
    """Apply the patch atomically after verifying the exact reviewed input."""

    root = Path(prime_root).resolve()
    target = root / PRIME_TRAIN_RELATIVE
    evidence = Path(evidence_path).resolve()
    if not target.is_file() or target.is_symlink() or sha256_file(target) != INPUT_SHA256:
        raise Step35ProximalPatchError("reviewed Step34 PRIME trainer source drifted")
    before = target.read_text(encoding="utf-8")
    after = patched_text(before)
    payload = after.encode()
    if hashlib.sha256(payload).hexdigest() != OUTPUT_SHA256:
        raise Step35ProximalPatchError("Step35 PRIME patch output identity drifted")
    temporary = target.with_name(f".{target.name}.step35-{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise Step35ProximalPatchError("unsafe pre-existing PRIME patch temporary")
    temporary.write_bytes(payload)
    os.chmod(temporary, target.stat().st_mode & 0o777)
    os.replace(temporary, target)
    body: dict[str, Any] = {
        "schema": "harness-distill.step35-proximal-prime-patch.v1",
        "status": "ok",
        "target": str(PRIME_TRAIN_RELATIVE),
        "input_sha256": INPUT_SHA256,
        "output_sha256": OUTPUT_SHA256,
        "source_step": SOURCE_STEP,
        "profiles": PROFILES,
        "snapshot": "all_local_shards_of_registered_run0_trainable_lora_after_step32_dcp_load",
        "projection": "distributed_global_rms_ball_after_every_optimizer_step",
    }
    body_sha = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    value = {**body, "patch_body_sha256": body_sha}
    _write_new(
        evidence,
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        + b"\n",
    )
    return value


def validate_evidence(path: str | Path, *, prime_root: str | Path) -> dict[str, Any]:
    evidence_path = Path(path).resolve()
    value = json.loads(evidence_path.read_text(encoding="utf-8"))
    body = {key: item for key, item in value.items() if key != "patch_body_sha256"}
    expected_body = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    target = Path(prime_root).resolve() / PRIME_TRAIN_RELATIVE
    if (
        value.get("schema") != "harness-distill.step35-proximal-prime-patch.v1"
        or value.get("status") != "ok"
        or value.get("input_sha256") != INPUT_SHA256
        or value.get("output_sha256") != OUTPUT_SHA256
        or value.get("source_step") != SOURCE_STEP
        or value.get("profiles") != PROFILES
        or value.get("patch_body_sha256") != expected_body
        or value.get("output_sha256") != sha256_file(target)
    ):
        raise Step35ProximalPatchError("Step35 proximal patch evidence drifted")
    text = target.read_text(encoding="utf-8")
    required = (
        "STEP35_PROXIMAL_ANCHOR",
        "STEP35_PROXIMAL_AUDIT",
        'step35_profile_name = os.environ.get("STEP35_PROXIMAL_PROFILE", "")',
        'step35_profile["proximal_lambda"]',
        'step35_profile["max_delta_rms"]',
    )
    if any(text.count(item) < 1 for item in required):
        raise Step35ProximalPatchError("Step35 proximal PRIME semantics are absent")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--prime-root", type=Path, required=True)
    apply.add_argument("--evidence", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--prime-root", type=Path, required=True)
    check.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    value = (
        apply_patch(args.prime_root, args.evidence)
        if args.command == "apply"
        else validate_evidence(args.evidence, prime_root=args.prime_root)
    )
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "EXPECTED_UPDATES",
    "FINAL_STEP",
    "INPUT_SHA256",
    "MAX_DELTA_RMS",
    "OUTPUT_SHA256",
    "PROXIMAL_LAMBDA",
    "PROFILES",
    "SOURCE_STEP",
    "Step35ProximalPatchError",
    "apply_patch",
    "patched_text",
    "validate_evidence",
]
