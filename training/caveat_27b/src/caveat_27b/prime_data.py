"""Exact PRIME rendering audit and deterministic local Parquet materialization."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_file,
)
from .config import Campaign

PRIME_DATASET_SCHEMA = "caveat-27b.prime-sft-dataset.v1"


def _prime_python(prime_root: str | Path | None) -> tuple[Path, Path | None]:
    root = Path(prime_root).resolve() if prime_root is not None else None
    configured = os.environ.get("PRIME_RL_PYTHON")
    candidates = [Path(configured).resolve()] if configured else []
    if root is not None:
        candidates.extend([root / ".venv/bin/python", root / "venv/bin/python"])
    candidates.append(Path("/opt/prime-rl/.venv/bin/python"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate, root
    raise ArtifactError("pinned PRIME interpreter is absent; set PRIME_RL_PYTHON or --prime-root")


def _smoke(path: str | Path, campaign: Campaign) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise ArtifactError("model smoke report is not successful")
    snapshot = Path(str(value.get("resolved_snapshot", ""))).resolve()
    if not snapshot.is_dir():
        raise ArtifactError("smoke-tested model snapshot is absent")
    if value.get("snapshot", {}).get("model_id") != campaign.model["model_id"]:
        raise ArtifactError("smoke report model differs from the campaign model")
    targets = value.get("target_selection", {}).get("targets")
    if not isinstance(targets, list) or not targets or not all(
        isinstance(item, str) and "." in item for item in targets
    ):
        raise ArtifactError("smoke report has no exact LoRA target names")
    return value


def _render_audit(
    *,
    source: Path,
    snapshot: Path,
    seq_len: int,
    global_batch_size: int,
    prime_root: str | Path | None,
) -> dict[str, Any]:
    python, root = _prime_python(prime_root)
    script = r'''
import json
import sys
from pathlib import Path

from renderers.base import build_training_sample, create_renderer, load_tokenizer
from renderers.configs import Qwen35RendererConfig
from prime_rl.utils.chat_template import (
    deserialize_tool_calls,
    normalize_messages,
    strip_message_content,
)

source = Path(sys.argv[1])
snapshot = sys.argv[2]
seq_len = int(sys.argv[3])
global_batch_size = int(sys.argv[4])
tokenizer = load_tokenizer(snapshot)
renderer = create_renderer(tokenizer, Qwen35RendererConfig(enable_thinking=True))
rows = []
rendered_total = 0
trainable_total = 0
maximum = 0
with source.open(encoding="utf-8") as stream:
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        row = json.loads(line)
        messages = normalize_messages(row["messages"], default_role="assistant")
        messages = strip_message_content(deserialize_tool_calls(messages))
        raw_tools = row.get("tools", row.get("tool_defs")) or []
        if isinstance(raw_tools, str):
            raw_tools = json.loads(raw_tools)
        tools = [
            tool if isinstance(tool, dict) and tool.get("type") == "function" and "function" in tool
            else {"type": "function", "function": {
                "name": tool.get("name"), "description": tool.get("description"),
                "parameters": tool.get("parameters"),
                **({} if tool.get("strict") is None else {"strict": tool["strict"]}),
            }}
            for tool in raw_tools
        ]
        sample = build_training_sample(
            renderer, messages,
            role_to_mask=lambda message: message["role"] == "assistant",
            tools=tools,
        )
        token_ids = list(sample.token_ids)
        loss_mask = list(sample.loss_mask)
        if tokenizer.eos_token_id not in token_ids:
            token_ids.append(int(tokenizer.eos_token_id))
            loss_mask.append(True)
        shifted = loss_mask[1:]
        trainable = [index for index, enabled in enumerate(shifted) if enabled]
        rendered = len(token_ids) - 1
        if not trainable:
            raise RuntimeError(f"row {line_number} has no assistant target")
        if rendered > seq_len or trainable[-1] >= seq_len:
            raise RuntimeError(
                f"row {line_number} would be truncated: rendered={rendered} "
                f"last_target={trainable[-1]} seq_len={seq_len}"
            )
        rows.append({
            "line": line_number, "rendered_tokens": rendered,
            "trainable_tokens": len(trainable), "first_trainable_index": trainable[0],
            "last_trainable_index": trainable[-1],
        })
        rendered_total += rendered
        trainable_total += len(trainable)
        maximum = max(maximum, rendered)
if not rows:
    raise RuntimeError("no rendered rows")
print(json.dumps({
    "rows": rows, "row_count": len(rows), "rendered_tokens": rendered_total,
    "trainable_tokens": trainable_total, "max_rendered_tokens": maximum,
    "tokens_per_update": seq_len * global_batch_size,
    "seq_len": seq_len, "global_batch_size": global_batch_size,
    "renderer": "qwen3.5",
}, sort_keys=True))
'''
    environment = os.environ.copy()
    if root is not None:
        additions = [
            root / "packages/prime-rl-configs/src",
            root / "src",
            root / "deps/renderers",
        ]
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(path) for path in additions]
            + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
        )
    completed = subprocess.run(
        [
            str(python),
            "-c",
            script,
            str(source),
            str(snapshot),
            str(seq_len),
            str(global_batch_size),
        ],
        text=True,
        capture_output=True,
        env=environment,
        timeout=3600,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise ArtifactError(f"exact PRIME rendering audit failed: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ArtifactError("exact PRIME rendering audit returned malformed output") from exc
    if result.get("row_count", 0) < 1:
        raise ArtifactError("exact PRIME rendering audit returned no rows")
    return result


def materialize_prime_dataset(
    campaign: Campaign,
    *,
    source_jsonl: str | Path,
    source_manifest: str | Path,
    smoke_report: str | Path,
    stage: str,
    candidate: str | None = None,
    output_dir: str | Path,
    prime_root: str | Path | None = None,
) -> dict[str, Any]:
    if stage not in {"targeted_sft", "refinement"}:
        raise ArtifactError("PRIME dataset stage must be targeted_sft or refinement")
    source = Path(source_jsonl).resolve()
    rows = read_jsonl(source)
    if not rows:
        raise ArtifactError("SFT source JSONL is empty")
    upstream = read_json(source_manifest)
    if not isinstance(upstream, dict) or upstream.get("campaign_digest") != campaign.digest:
        raise ArtifactError("source manifest belongs to a different campaign")
    if upstream.get("output", {}).get("sha256") != sha256_file(source):
        raise ArtifactError("source manifest does not bind the supplied JSONL")
    if stage == "targeted_sft":
        if candidate is None or upstream.get("candidate") != candidate:
            raise ArtifactError("targeted SFT source manifest candidate differs")
    elif candidate is not None:
        raise ArtifactError("refinement PRIME data cannot name a targeted SFT candidate")
    report = _smoke(smoke_report, campaign)
    config = (
        campaign.targeted_sft_candidate(candidate)
        if stage == "targeted_sft" and candidate is not None
        else campaign.campaign["refinement"]
    )
    seq_len = int(config["sequence_length"])
    global_batch_size = int(config["global_batch_size"])
    audit = _render_audit(
        source=source,
        snapshot=Path(report["resolved_snapshot"]).resolve(),
        seq_len=seq_len,
        global_batch_size=global_batch_size,
        prime_root=prime_root,
    )
    output = Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        manifest_path = output / "manifest.json"
        parquet_path = output / "train.parquet"
        if manifest_path.is_file() and parquet_path.is_file():
            prior = read_json(manifest_path)
            if (
                prior.get("source_sha256") == sha256_file(source)
                and prior.get("parquet_sha256") == sha256_file(parquet_path)
            ):
                return prior
        raise ArtifactError(f"PRIME dataset output is nonempty: {output}")
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - available in the training image
        raise ArtifactError("pyarrow is required in the training image") from exc
    output.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    temporary = output / ".train.parquet.tmp"
    pq.write_table(
        table,
        temporary,
        compression="zstd",
        compression_level=9,
        use_dictionary=True,
        write_statistics=True,
        version="2.6",
        data_page_version="2.0",
        row_group_size=1024,
    )
    parquet = output / "train.parquet"
    temporary.replace(parquet)
    audit_rows = publish_jsonl(output / "token_audit_rows.jsonl", audit.pop("rows"))
    manifest = {
        "schema": PRIME_DATASET_SCHEMA,
        "stage": stage,
        "candidate": candidate,
        "campaign_digest": campaign.digest,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "source_manifest_sha256": sha256_file(source_manifest),
        "smoke_report_sha256": sha256_file(smoke_report),
        "model_snapshot": str(Path(report["resolved_snapshot"]).resolve()),
        "model_revision": campaign.model["revision"],
        "row_count": len(rows),
        "parquet_sha256": sha256_file(parquet),
        "token_audit": audit,
        "token_audit_rows_sha256": sha256_file(audit_rows),
        "columns": table.column_names,
        "pyarrow_version": pa.__version__,
    }
    publish_json(output / "manifest.json", manifest)
    return manifest
