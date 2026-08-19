#!/usr/bin/env python3
"""Queue the frozen step29 pair-collection bundle without changing its rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
COLLECTION_ROOT = (
    WORKSPACE
    / "results/harness_posttrain_campaign2_20260814/step29_next_on_policy_buy_now_dagger_r1"
)
BUNDLE_PATH = COLLECTION_ROOT / "bundle.json"
MATERIALIZED_ROOT = (
    WORKSPACE
    / "results/harness_posttrain_campaign2_20260814/step29_next_on_policy_buy_now_exact16_r1"
)
SUPERVISOR_ROOT = COLLECTION_ROOT / "queued_runtime"
EXPECTED_BUNDLE_FILE_SHA256 = "cf4af109c01480e02e2f04754591e4ad7bb491d30bce953ddd01d1be0ef1a3d7"
EXPECTED_BUNDLE_BODY_SHA256 = "e696fa3e9950072ea14c7b5d7796db5d6cb3661a1fe95524a96c7f6f4cdac622"
EXPECTED_RULE_SHA256 = "29f535191246bfce572ad6e191abc2aea0f9119d0dfa110022230ab0bc01fd54"
EXPECTED_ALIAS = "qwen35-browser-action-step29-paired-buynow-9a73894a3055-exact-lora"
VARIANTS = ("graded", "graded3", "graded4", "mixed")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _trace_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    intervention = False
    successor = False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            intervention |= (
                row.get("route") == "teacher_intervention"
                and row.get("trigger_kind") == "visible_pdp_buy_now_click"
            )
            successor |= (
                row.get("route") == "successor"
                and row.get("successor_validated") is True
                and row.get("direct_checkout_avoided") is True
                and row.get("order_not_placed") is True
            )
    except (OSError, json.JSONDecodeError):
        return False
    return intervention and successor


def _frozen_bundle() -> dict[str, Any]:
    rule_path = (
        WORKSPACE
        / "training/harness_distill/src/harness_distill/adaptive_buy_now_dagger.py"
    )
    if _sha256(BUNDLE_PATH) != EXPECTED_BUNDLE_FILE_SHA256:
        raise RuntimeError("frozen bundle file changed")
    if _sha256(rule_path) != EXPECTED_RULE_SHA256:
        raise RuntimeError("frozen intervention rule changed")
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    if (
        bundle.get("bundle_sha256") != EXPECTED_BUNDLE_BODY_SHA256
        or bundle.get("student_alias") != EXPECTED_ALIAS
        or (bundle.get("matrix") or {}).get("split") != "train"
        or (bundle.get("matrix") or {}).get("evaluation_rows") != 0
        or len(bundle.get("rows", [])) != 24
    ):
        raise RuntimeError("frozen bundle contract changed")
    return bundle


def _launch_order(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    rows = bundle["rows"]
    return sorted(
        rows,
        key=lambda row: (int(row["replica"]), VARIANTS.index(str(row["variant"]))),
    )


def _shard_bundle(bundle: dict[str, Any], row: dict[str, Any], shard: Path) -> Path:
    body = {
        key: value
        for key, value in bundle.items()
        if key not in {"bundle_sha256", "rows", "matrix"}
    }
    body["matrix"] = {
        **bundle["matrix"],
        "variants": [row["variant"]],
        "replicas": [row["replica"]],
        "runs": 1,
    }
    body["rows"] = [row]
    value = {**body, "bundle_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    path = shard / "bundle.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise RuntimeError(f"launch shard changed: {path}")
    else:
        _write_atomic(path, value)
    (shard / "runtime").mkdir(exist_ok=True)
    return path


def _counts(valid_indices: set[int], rows_by_index: dict[int, dict[str, Any]]) -> dict[str, int]:
    return {
        variant: sum(
            rows_by_index[index]["variant"] == variant for index in valid_indices
        )
        for variant in VARIANTS
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-workers", type=int, default=3, choices=(1, 2, 3, 4))
    parser.add_argument("--quota-per-variant", type=int, default=4, choices=(4,))
    parser.add_argument("--upstream-base-url", default="http://127.0.0.1:18546")
    parser.add_argument("--cell-timeout-seconds", type=int, default=2700)
    args = parser.parse_args(argv)
    if not args.upstream_base_url.startswith("http://127.0.0.1:"):
        raise RuntimeError("only the authorized loopback endpoint is allowed")

    bundle = _frozen_bundle()
    order = _launch_order(bundle)
    rows_by_index = {int(row["index"]): row for row in order}
    SUPERVISOR_ROOT.mkdir(parents=True, exist_ok=True)

    attempted: set[int] = set()
    valid: set[int] = set()
    for row in order:
        index = int(row["index"])
        shard = SUPERVISOR_ROOT / f"cell_{index:02d}"
        if (shard / "exit.json").is_file() or Path(row["trace_path"]).is_file():
            attempted.add(index)
            if _trace_valid(Path(row["trace_path"])):
                valid.add(index)

    python_bin = WORKSPACE / ".venv/bin/python"
    environment = os.environ.copy()
    source_paths = [
        str(WORKSPACE / "training/harness_distill/src"),
        str(WORKSPACE / "training/harness_posttrain_eval/src"),
    ]
    if environment.get("PYTHONPATH"):
        source_paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = ":".join(source_paths)
    active: dict[int, tuple[subprocess.Popen[bytes], Any, Any]] = {}

    while True:
        for index, (process, stdout, stderr) in list(active.items()):
            return_code = process.poll()
            if return_code is None:
                continue
            stdout.close()
            stderr.close()
            attempted.add(index)
            row = rows_by_index[index]
            trace_valid = _trace_valid(Path(row["trace_path"]))
            if trace_valid:
                valid.add(index)
            _write_atomic(
                SUPERVISOR_ROOT / f"cell_{index:02d}" / "exit.json",
                {"index": index, "return_code": return_code, "valid_pair": trace_valid},
            )
            del active[index]

        counts = _counts(valid, rows_by_index)
        active_counts = {
            variant: sum(
                rows_by_index[index]["variant"] == variant for index in active
            )
            for variant in VARIANTS
        }
        _write_atomic(
            SUPERVISOR_ROOT / "structural_status.json",
            {
                "timestamp": time.time(),
                "max_workers": args.max_workers,
                "active": [
                    {"index": index, "variant": rows_by_index[index]["variant"]}
                    for index in sorted(active)
                ],
                "attempted": len(attempted),
                "valid_pairs": len(valid),
                "variant_valid_pairs": counts,
                "target": {
                    "rows": args.quota_per_variant * len(VARIANTS),
                    "per_variant": args.quota_per_variant,
                },
            },
        )
        if all(counts[variant] >= args.quota_per_variant for variant in VARIANTS):
            if active:
                time.sleep(0.5)
                continue
            break

        launched = False
        for row in order:
            if len(active) >= args.max_workers:
                break
            index = int(row["index"])
            variant = str(row["variant"])
            if index in attempted or index in active:
                continue
            if counts[variant] + active_counts[variant] >= args.quota_per_variant:
                continue
            shard = SUPERVISOR_ROOT / f"cell_{index:02d}"
            shard.mkdir(parents=True, exist_ok=True)
            shard_bundle = _shard_bundle(bundle, row, shard)
            stdout = (shard / "run.stdout").open("xb")
            stderr = (shard / "run.stderr").open("xb")
            process = subprocess.Popen(
                [
                    str(python_bin),
                    "-m",
                    "harness_distill.adaptive_buy_now_dagger_ops",
                    "run",
                    "--bundle",
                    str(shard_bundle),
                    "--upstream-base-url",
                    args.upstream_base_url,
                    "--python",
                    str(python_bin),
                    "--timeout-seconds",
                    str(args.cell_timeout_seconds),
                ],
                cwd=WORKSPACE,
                env=environment,
                stdout=stdout,
                stderr=stderr,
            )
            active[index] = (process, stdout, stderr)
            active_counts[variant] += 1
            launched = True

        if not active and not launched:
            _write_atomic(
                SUPERVISOR_ROOT / "exhausted.json",
                {
                    "status": "quota_not_reached",
                    "attempted": len(attempted),
                    "valid_pairs": len(valid),
                    "variant_valid_pairs": counts,
                },
            )
            return 2
        time.sleep(1)

    from harness_distill.adaptive_buy_now_dagger_ops import materialize_pairs

    if MATERIALIZED_ROOT.exists():
        manifest = json.loads((MATERIALIZED_ROOT / "manifest.json").read_text())
    else:
        manifest = materialize_pairs(
            bundle_paths=[BUNDLE_PATH],
            output_root=MATERIALIZED_ROOT,
            quota_per_variant=args.quota_per_variant,
        )
    _write_atomic(
        SUPERVISOR_ROOT / "complete.json",
        {
            "status": "exact_quota_materialized",
            "rows": manifest["rows"],
            "variant_counts": manifest["variant_counts"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
