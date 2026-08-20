"""Fail-closed PRIME-RL SFT and privileged-OPD orchestration.

The generated files target the reviewed PRIME-RL v0.7.0 schema.  Configuration
generation is deliberately independent of PRIME's heavyweight runtime, while
launching requires the installed PRIME schema to parse the exact TOML before a
GPU process is started.

One upstream limitation is kept visible instead of being papered over: OPD is
a pure reverse-KL algorithm.  A simultaneous corrective-CE weight is
  not representable in one OPD environment; corrective CE must remain a
  separate SFT/rehearsal phase between OPD rounds.

The separately reviewed ``prime_rl_v0.7_train_top_p.patch`` adds only a typed
``TrainSamplingConfig.top_p`` field and replaces PRIME's literal ``1.0`` with
that field.  Its default is still 1.0; this campaign explicitly sets 0.95.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import atomic_json
from .model_smoke import (
    DENSE_ATTENTION_SUFFIXES,
    GDN_SUFFIXES,
    MLP_SUFFIXES,
)
from .prime_recovery import RECOVERY_SCHEMA, PrimeRecoveryError, reconcile_prime_output

PRIME_VERSION = "0.7.0"
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
PRIVILEGED_CONTEXT_KEY = "privileged_context"
DECISION_REPLAY_PLUGIN = "decision-replay-v1"
TRAIN_TOP_P_PATCH_SHA256 = "ea6f955228a8cb5b650c2dabc1cb38e20574b23f847524764f4e7058fd4ec767"
CORRECTIVE_CE_LIMITATION = (
    "PRIME-RL 0.7 OPD routes one pure ref_kl loss; corrective CE weight 0.5 "
    "must be applied in a separate SFT/rehearsal phase"
)
_ADAPTER_WEIGHT_NAMES = ("adapter_model.safetensors", "adapter_model.bin")
CAMPAIGN_SEQ_LEN = 65_536
POLICY_SERVER_PORT = 8000
TEACHER_SERVER_PORT = 8001
INFERENCE_GPU_IDS = (0, 1, 2, 3)
TRAINER_GPU_IDS = (4, 5, 6, 7)
POLICY_GPU_MEMORY_UTILIZATION = 0.40
TEACHER_GPU_MEMORY_UTILIZATION = 0.40
SFT_MANIFEST_SCHEMA = "harness-distill.sft-parquet.v3"
PRIME_LAUNCH_SCHEMA = "harness-distill.prime-launch.v2"


class PrimeTrainingError(RuntimeError):
    """A generated config or PRIME subprocess failed an audited invariant."""


@dataclass(frozen=True)
class PrimeConfigArtifact:
    kind: Literal["sft", "opd"]
    path: Path
    output_dir: Path
    sha256: str
    model_snapshot: Path
    dataset_path: Path
    target_count: int
    optimizer_updates: int
    prime_max_steps: int
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("path", "output_dir", "model_snapshot", "dataset_path"):
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class PrimeRunResult:
    kind: Literal["sft", "opd"]
    status: Literal["complete"]
    config_path: Path
    output_dir: Path
    adapter_path: Path
    manifest_path: Path
    resumed: bool
    returncode: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("config_path", "output_dir", "adapter_path", "manifest_path"):
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class SFTDatasetArtifact:
    source_jsonl: Path
    train_parquet: Path
    manifest_path: Path
    row_count: int
    source_sha256: str
    parquet_sha256: str
    rendered_tokens: int
    trainable_tokens: int
    max_rendered_tokens: int
    optimizer_updates: int
    prime_max_steps: int
    seq_len: int
    renderer: str
    model_revision: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("source_jsonl", "train_parquet", "manifest_path"):
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class PrimeInferenceArtifact:
    """A separately launched frozen-teacher inference configuration."""

    path: Path
    output_dir: Path
    sha256: str
    model_snapshot: Path
    port: int
    gpu_memory_utilization: float
    gpu_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("path", "output_dir", "model_snapshot"):
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class FrozenTeacherHandle:
    """Metadata for a live teacher process owned by this launcher."""

    pid: int
    base_url: str
    model_name: str
    log_path: Path
    manifest_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prime_terminal_step(optimizer_updates: int) -> int:
    """Translate an update count to PRIME's one-indexed terminal step.

    Pinned PRIME-RL initializes ``Progress.step`` at one, executes that step,
    and then tests equality with ``max_steps``.  Thus ``max_steps=N`` performs
    exactly ``N`` optimizer updates.  In particular, zero is not a valid
    one-update setting: the equality can never be reached after step one.
    """

    if type(optimizer_updates) is not int or optimizer_updates < 1:
        raise PrimeTrainingError("optimizer_updates must be a positive integer")
    return optimizer_updates


def _quoted(value: str | Path) -> str:
    """JSON strings are a valid, deterministic subset of TOML basic strings."""

    return json.dumps(str(value), ensure_ascii=False)


def _array(values: Sequence[str], *, indent: str = "  ") -> str:
    if not values:
        return "[]"
    return "[\n" + "".join(f"{indent}{_quoted(value)},\n" for value in values) + "]"


def _write_toml(path: str | Path, lines: Sequence[str]) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    # Parse before publishing, so a partially composed config never escapes.
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PrimeTrainingError(f"generated invalid TOML for {target}: {exc}") from exc
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)
    return target


def _read_smoke_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    if not report_path.is_file():
        raise PrimeTrainingError(f"model smoke report does not exist: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PrimeTrainingError(f"cannot read model smoke report: {report_path}") from exc
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise PrimeTrainingError("model smoke report is not successful")
    snapshot = Path(str(report.get("resolved_snapshot", ""))).resolve()
    if not snapshot.is_dir():
        raise PrimeTrainingError(f"exact local model snapshot is absent: {snapshot}")
    raw_targets = report.get("target_selection", {}).get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise PrimeTrainingError("model smoke report has no audited LoRA target names")
    if any(not isinstance(name, str) or "." not in name for name in raw_targets):
        raise PrimeTrainingError("LoRA targets must be full module names")
    if len(set(raw_targets)) != len(raw_targets):
        raise PrimeTrainingError("model smoke report contains duplicate LoRA targets")

    leaves = {name.rsplit(".", 1)[-1] for name in raw_targets}
    if not leaves.intersection(DENSE_ATTENTION_SUFFIXES):
        raise PrimeTrainingError("audited targets omit dense attention")
    if not leaves.intersection(MLP_SUFFIXES):
        raise PrimeTrainingError("audited targets omit the MLP")
    if report.get("target_selection", {}).get("gdn_present") and not leaves.intersection(
        GDN_SUFFIXES
    ):
        raise PrimeTrainingError("audited targets omit present GatedDeltaNet projections")
    return report


def _renderer_name(report: Mapping[str, Any]) -> str:
    model_id = str(report.get("snapshot", {}).get("model_id", ""))
    if "Qwen3.6" in model_id:
        return "qwen3.6"
    if "Qwen3.5" in model_id:
        return "qwen3.5"
    raise PrimeTrainingError(f"no reviewed renderer mapping for {model_id!r}")


def _exact_target_patterns(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Anchor every audited full name; never rely on a suffix collision."""

    targets = report["target_selection"]["targets"]
    return tuple(f"^{re.escape(name)}$" for name in targets)


def _inference_target_suffixes(report: Mapping[str, Any]) -> tuple[str, ...]:
    # PRIME exports PEFT target_modules as leaf names, which is also the dialect
    # vLLM accepts.  Trainer targeting remains full-name and collision-proof.
    leaves = {name.rsplit(".", 1)[-1] for name in report["target_selection"]["targets"]}
    return tuple(sorted(leaves))


def _require_sft_dataset(path: str | Path) -> Path:
    target = Path(path).resolve()
    if not target.is_dir():
        raise PrimeTrainingError(f"SFT dataset directory does not exist: {target}")
    if not (target / "train.parquet").is_file():
        raise PrimeTrainingError(f"PRIME local-data SFT expects {target / 'train.parquet'}")
    return target


def _rendered_token_audit(
    *,
    source: Path,
    report: Mapping[str, Any],
    seq_len: int,
    global_batch_size: int,
    prime_root: str | Path | None,
) -> dict[str, Any]:
    """Audit exact PRIME rendering and derive a token-equivalent update budget."""

    root = Path(prime_root).resolve() if prime_root is not None else None
    python = _prime_python(root)
    if python is None:
        raise PrimeTrainingError(
            "rendered-token audit requires the pinned PRIME interpreter; set "
            "PRIME_RL_PYTHON or pass prime_root"
        )
    renderer = _renderer_name(report)
    snapshot = Path(str(report["resolved_snapshot"])).resolve()
    script = r"""
import json
import sys
from pathlib import Path

from renderers.base import build_training_sample, create_renderer, load_tokenizer
from renderers.configs import Qwen35RendererConfig, Qwen36RendererConfig
from prime_rl.utils.chat_template import (
    deserialize_tool_calls,
    normalize_messages,
    strip_message_content,
)

source = Path(sys.argv[1])
snapshot = sys.argv[2]
renderer_name = sys.argv[3]
seq_len = int(sys.argv[4])
global_batch_size = int(sys.argv[5])
tokenizer = load_tokenizer(snapshot)
if renderer_name == "qwen3.6":
    renderer_config = Qwen36RendererConfig(enable_thinking=True, preserve_thinking=True)
elif renderer_name == "qwen3.5":
    renderer_config = Qwen35RendererConfig(enable_thinking=True)
else:
    raise RuntimeError(f"unsupported audited renderer: {renderer_name}")
renderer = create_renderer(tokenizer, renderer_config)

rows = []
total_tokens = 0
total_trainable = 0
max_tokens = 0
with source.open("r", encoding="utf-8") as stream:
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
            tool
            if isinstance(tool, dict) and tool.get("type") == "function" and "function" in tool
            else {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "parameters": tool.get("parameters"),
                    **({} if tool.get("strict") is None else {"strict": tool["strict"]}),
                },
            }
            for tool in raw_tools
        ]
        sample = build_training_sample(
            renderer,
            messages,
            role_to_mask=lambda message: message["role"] == "assistant",
            tools=tools,
        )
        token_ids = list(sample.token_ids)
        loss_mask = list(sample.loss_mask)
        if tokenizer.eos_token_id not in token_ids:
            token_ids.append(int(tokenizer.eos_token_id))
            loss_mask.append(True)
        shifted_mask = loss_mask[1:]
        rendered_tokens = len(token_ids) - 1
        trainable_positions = [index for index, enabled in enumerate(shifted_mask) if enabled]
        if not trainable_positions:
            raise RuntimeError(f"row {line_number} has no assistant target tokens")
        first_target = trainable_positions[0]
        last_target = trainable_positions[-1]
        if last_target >= seq_len:
            raise RuntimeError(
                f"row {line_number} assistant target at index {last_target} lies beyond "
                f"seq_len={seq_len}"
            )
        if rendered_tokens > seq_len:
            raise RuntimeError(
                f"row {line_number} renders to {rendered_tokens} tokens and PRIME CatDataset "
                f"would silently truncate it at seq_len={seq_len}"
            )
        trainable_tokens = len(trainable_positions)
        total_tokens += rendered_tokens
        total_trainable += trainable_tokens
        max_tokens = max(max_tokens, rendered_tokens)
        rows.append({
            "line": line_number,
            "rendered_tokens": rendered_tokens,
            "trainable_tokens": trainable_tokens,
            "first_trainable_index": first_target,
            "last_trainable_index": last_target,
        })

if not rows:
    raise RuntimeError("no rendered SFT rows")
tokens_per_update = seq_len * global_batch_size
optimizer_updates = max(1, (total_tokens + tokens_per_update - 1) // tokens_per_update)
print(json.dumps({
    "row_count": len(rows),
    "rendered_tokens": total_tokens,
    "trainable_tokens": total_trainable,
    "max_rendered_tokens": max_tokens,
    "tokens_per_update": tokens_per_update,
    "computed_optimizer_updates": optimizer_updates,
    "prime_max_steps": optimizer_updates,
    "seq_len": seq_len,
    "global_batch_size": global_batch_size,
    "renderer": renderer_name,
    "rows": rows,
}, sort_keys=True))
"""
    environment = os.environ.copy()
    if root is not None:
        environment["PYTHONPATH"] = os.pathsep.join(
            [
                str(root / "packages" / "prime-rl-configs" / "src"),
                str(root / "src"),
                str(root / "deps" / "renderers"),
            ]
            + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
        )
    completed = subprocess.run(
        [
            str(python),
            "-c",
            script,
            str(source),
            str(snapshot),
            renderer,
            str(seq_len),
            str(global_batch_size),
        ],
        text=True,
        capture_output=True,
        env=environment,
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise PrimeTrainingError(f"exact rendered-token audit failed: {detail}")
    try:
        audit = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise PrimeTrainingError("rendered-token audit returned malformed output") from exc
    if audit.get("row_count", 0) < 1 or audit.get("computed_optimizer_updates", 0) < 1:
        raise PrimeTrainingError("rendered-token audit returned empty accounting")
    return audit


def materialize_sft_jsonl_to_parquet(
    source_jsonl: str | Path,
    output_dir: str | Path,
    *,
    smoke_report: str | Path,
    seq_len: int = CAMPAIGN_SEQ_LEN,
    required_seq_len: int = CAMPAIGN_SEQ_LEN,
    global_batch_size: int = 8,
    prime_root: str | Path | None = None,
) -> SFTDatasetArtifact:
    """Convert the production SFT JSONL to PRIME's verified local-data layout.

    PRIME v0.7 calls ``datasets.load_dataset(<directory>, split="train")``;
    its own ``scripts/export_sft.py`` documents a directory containing
    ``train.parquet`` as the supported local form.  This converter preserves
    source order, canonicalizes mapping key order, uses fixed Parquet settings,
    and refuses to overwrite bytes from a different source.
    """

    report = _read_smoke_report(smoke_report)
    source = Path(source_jsonl).resolve()
    destination = Path(output_dir).resolve()
    allowed_seq_lens = {32_768, CAMPAIGN_SEQ_LEN}
    if (
        type(seq_len) is not int
        or type(required_seq_len) is not int
        or seq_len not in allowed_seq_lens
        or required_seq_len not in allowed_seq_lens
    ):
        raise PrimeTrainingError(
            "SFT seq_len and required_seq_len must be exactly 32768 or 65536"
        )
    if seq_len != required_seq_len:
        raise PrimeTrainingError(
            f"campaign SFT seq_len must be exactly {required_seq_len}, got {seq_len}"
        )
    if global_batch_size < 1:
        raise PrimeTrainingError("SFT global batch size must be positive")
    if not source.is_file() or source.stat().st_size == 0:
        raise PrimeTrainingError(f"SFT JSONL is absent or empty: {source}")
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PrimeTrainingError(f"SFT JSONL line {line_number} is invalid JSON") from exc
            if not isinstance(row, dict):
                raise PrimeTrainingError(f"SFT JSONL line {line_number} is not an object")
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages:
                raise PrimeTrainingError(
                    f"SFT JSONL line {line_number} requires non-empty messages"
                )
            if not any(
                isinstance(message, dict) and message.get("role") == "assistant"
                for message in messages
            ):
                raise PrimeTrainingError(f"SFT JSONL line {line_number} has no assistant target")
            # This also rejects NaN/Infinity and recursively sorts mapping keys.
            canonical = json.dumps(
                row,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            rows.append(json.loads(canonical))
    if not rows:
        raise PrimeTrainingError("SFT JSONL contains no rows")

    source_hash = _sha256_file(source)
    audit = _rendered_token_audit(
        source=source,
        report=report,
        seq_len=seq_len,
        global_batch_size=global_batch_size,
        prime_root=prime_root,
    )
    model_revision = str(report.get("snapshot", {}).get("revision", ""))
    parquet_path = destination / "train.parquet"
    manifest_path = destination / "manifest.json"
    if manifest_path.is_file() and parquet_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema") == SFT_MANIFEST_SCHEMA
            and manifest.get("source_sha256") == source_hash
            and manifest.get("row_count") == len(rows)
            and manifest.get("model_revision") == model_revision
            and manifest.get("token_audit") == audit
            and manifest.get("parquet_sha256") == _sha256_file(parquet_path)
        ):
            return SFTDatasetArtifact(
                source_jsonl=source,
                train_parquet=parquet_path,
                manifest_path=manifest_path,
                row_count=len(rows),
                source_sha256=source_hash,
                parquet_sha256=manifest["parquet_sha256"],
                rendered_tokens=audit["rendered_tokens"],
                trainable_tokens=audit["trainable_tokens"],
                max_rendered_tokens=audit["max_rendered_tokens"],
                optimizer_updates=audit["computed_optimizer_updates"],
                prime_max_steps=audit["prime_max_steps"],
                seq_len=seq_len,
                renderer=audit["renderer"],
                model_revision=model_revision,
            )
        raise PrimeTrainingError(
            f"SFT Parquet output already exists for different bytes: {destination}"
        )
    if destination.exists() and any(destination.iterdir()):
        raise PrimeTrainingError(f"SFT Parquet output must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - present in the pinned PRIME image
        raise PrimeTrainingError("pyarrow is required to materialize PRIME SFT data") from exc
    table = pa.Table.from_pylist(rows)
    temporary = destination / ".train.parquet.tmp"
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
    temporary.replace(parquet_path)
    parquet_hash = _sha256_file(parquet_path)
    manifest = {
        "schema": SFT_MANIFEST_SCHEMA,
        "source": str(source),
        "source_sha256": source_hash,
        "train_parquet": str(parquet_path),
        "parquet_sha256": parquet_hash,
        "row_count": len(rows),
        "model_revision": model_revision,
        "model_snapshot": str(Path(report["resolved_snapshot"]).resolve()),
        "token_audit": audit,
        "columns": table.column_names,
        "pyarrow_version": pa.__version__,
        "settings": {
            "compression": "zstd",
            "compression_level": 9,
            "data_page_version": "2.0",
            "row_group_size": 1024,
            "version": "2.6",
        },
    }
    atomic_json(manifest_path, manifest)
    return SFTDatasetArtifact(
        source_jsonl=source,
        train_parquet=parquet_path,
        manifest_path=manifest_path,
        row_count=len(rows),
        source_sha256=source_hash,
        parquet_sha256=parquet_hash,
        rendered_tokens=audit["rendered_tokens"],
        trainable_tokens=audit["trainable_tokens"],
        max_rendered_tokens=audit["max_rendered_tokens"],
        optimizer_updates=audit["computed_optimizer_updates"],
        prime_max_steps=audit["prime_max_steps"],
        seq_len=seq_len,
        renderer=audit["renderer"],
        model_revision=model_revision,
    )


def _require_replay_dataset(path: str | Path) -> Path:
    target = Path(path).resolve()
    if not target.is_file():
        raise PrimeTrainingError(f"decision-replay JSONL does not exist: {target}")
    if target.stat().st_size == 0:
        raise PrimeTrainingError("decision-replay JSONL is empty")
    return target


def _load_sft_manifest(
    dataset_dir: Path,
    *,
    report: Mapping[str, Any],
    seq_len: int,
    global_batch_size: int,
) -> dict[str, Any]:
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PrimeTrainingError(
            f"SFT dataset lacks rendered-token manifest: {manifest_path}; use "
            "materialize_sft_jsonl_to_parquet"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimeTrainingError(f"invalid SFT dataset manifest: {manifest_path}") from exc
    audit = manifest.get("token_audit")
    expected_revision = str(report.get("snapshot", {}).get("revision", ""))
    checks = {
        "schema": manifest.get("schema") == SFT_MANIFEST_SCHEMA,
        "model revision": manifest.get("model_revision") == expected_revision,
        "renderer": isinstance(audit, dict) and audit.get("renderer") == _renderer_name(report),
        "sequence length": isinstance(audit, dict) and audit.get("seq_len") == seq_len,
        "global batch size": isinstance(audit, dict)
        and audit.get("global_batch_size") == global_batch_size,
        "computed updates": isinstance(audit, dict)
        and isinstance(audit.get("computed_optimizer_updates"), int)
        and audit["computed_optimizer_updates"] >= 1,
        "PRIME terminal step": isinstance(audit, dict)
        and isinstance(audit.get("computed_optimizer_updates"), int)
        and audit.get("computed_optimizer_updates", 0) >= 1
        and audit.get("prime_max_steps") == audit["computed_optimizer_updates"],
        "rows": isinstance(audit, dict) and audit.get("row_count") == manifest.get("row_count"),
        "parquet hash": manifest.get("parquet_sha256")
        == _sha256_file(dataset_dir / "train.parquet"),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise PrimeTrainingError("SFT dataset manifest failed audit: " + ", ".join(failed))
    if any(
        int(row.get("last_trainable_index", seq_len)) >= seq_len for row in audit.get("rows", [])
    ):
        raise PrimeTrainingError("SFT manifest contains a target beyond seq_len")
    return manifest


def generate_sft_toml(
    *,
    smoke_report: str | Path,
    dataset_dir: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    student_snapshot: str | Path | None = None,
    optimizer_updates: int | None = None,
    seq_len: int = CAMPAIGN_SEQ_LEN,
    global_batch_size: int = 8,
    num_gpus: int = 8,
    rank: int = 64,
    alpha: float = 128.0,
    dropout: float = 0.0,
    learning_rate: float = 1.0e-5,
    warmup_fraction: float = 0.03,
    checkpoint_interval: int = 50,
) -> PrimeConfigArtifact:
    """Generate token-equivalent-pass SFT for a local Parquet dataset.

    PRIME's packed iterable has no row-epoch knob, so the requested optimizer
    update count comes from the exact-renderer token manifest. A caller may
    repeat that count explicitly, but a mismatch fails rather than silently
    changing the exposure budget. PRIME's inclusive ``max_steps`` convention
    is translated only when the TOML is written.
    """

    report = _read_smoke_report(smoke_report)
    data = _require_sft_dataset(dataset_dir)
    destination = Path(output_dir).resolve()
    if seq_len != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError(
            f"campaign SFT seq_len must be exactly {CAMPAIGN_SEQ_LEN}, got {seq_len}"
        )
    if global_batch_size < 1 or num_gpus < 1:
        raise PrimeTrainingError("SFT batch size and GPUs must be positive")
    if global_batch_size % num_gpus != 0:
        raise PrimeTrainingError("SFT global batch size must be divisible by trainer GPU count")
    if rank != 64 or alpha != 128:
        raise PrimeTrainingError("campaign LoRA must remain rank 64 / alpha 128")
    targets = _exact_target_patterns(report)
    renderer = _renderer_name(report)
    manifest = _load_sft_manifest(
        data,
        report=report,
        seq_len=seq_len,
        global_batch_size=global_batch_size,
    )
    audited_updates = int(manifest["token_audit"]["computed_optimizer_updates"])
    if optimizer_updates is not None and optimizer_updates != audited_updates:
        raise PrimeTrainingError(
            "SFT optimizer_updates="
            f"{optimizer_updates} differs from rendered-token audit {audited_updates}"
        )
    optimizer_updates = audited_updates
    prime_max_steps = _prime_terminal_step(optimizer_updates)
    snapshot = Path(
        student_snapshot if student_snapshot is not None else report["resolved_snapshot"]
    ).resolve()
    if not snapshot.is_dir():
        raise PrimeTrainingError(f"SFT student snapshot is absent: {snapshot}")
    warmup_steps = min(
        max(0, prime_max_steps - 1),
        max(1, math.ceil(optimizer_updates * warmup_fraction)),
    )
    scheduler_lines = (
        ['type = "constant"']
        if optimizer_updates == 1
        else ['type = "cosine"', f"warmup_steps = {warmup_steps}", "min_lr = 0.0"]
    )
    lines = [
        f"# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT})",
        "# Generated from a successful full-model smoke; target patterns are anchored full names.",
        f"# requested_optimizer_updates = {optimizer_updates}",
        "# PRIME max_steps is the one-indexed terminal optimizer step.",
        f"max_steps = {prime_max_steps}",
        f"output_dir = {_quoted(destination)}",
        "clean_output_dir = false",
        'matmul_precision = "high"',
        'loss_impl = "liger_fused"',
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_gpus = {num_gpus}",
        "gpus_per_node = 8",
        "",
        "[model]",
        f"name = {_quoted(snapshot)}",
        f"seq_len = {seq_len}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[model.lora]",
        f"rank = {rank}",
        f"alpha = {alpha}",
        f"dropout = {dropout}",
        f"target_modules = {_array(targets)}",
        "modules_to_save = []",
        "",
        "[renderer]",
        f"name = {_quoted(renderer)}",
        "enable_thinking = true",
        *(["preserve_thinking = true"] if renderer == "qwen3.6" else []),
        "",
        "[data]",
        'type = "sft"',
        f"name = {_quoted(data)}",
        f"batch_size = {global_batch_size}",
        f"seq_len = {seq_len}",
        "micro_batch_size = 1",
        'pack_function = "cat"',
        "shuffle = true",
        "seed = 56036027",
        "",
        "[data.loss_mask]",
        "system = false",
        "user = false",
        "assistant = true",
        "tool = false",
        "",
        "[optim]",
        'type = "adamw"',
        f"lr = {learning_rate}",
        "weight_decay = 0.01",
        "max_norm = 1.0",
        "",
        "[scheduler]",
        *scheduler_lines,
        "",
        "[ckpt]",
        f"interval = {checkpoint_interval}",
        "resume_step = -1",
        "keep_last = 2",
        "",
        "[ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
    ]
    target = _write_toml(config_path, lines)
    _validate_static_sft(tomllib.loads(target.read_text(encoding="utf-8")))
    return PrimeConfigArtifact(
        kind="sft",
        path=target,
        output_dir=destination,
        sha256=_sha256_file(target),
        model_snapshot=snapshot,
        dataset_path=data,
        target_count=len(targets),
        optimizer_updates=optimizer_updates,
        prime_max_steps=prime_max_steps,
        limitations=(),
    )


def _audit_replay_dataset(
    *,
    replay_jsonl: Path,
    report: Mapping[str, Any],
    context_template: str,
    seq_len: int,
    max_completion_tokens: int,
    prime_root: str | Path | None,
) -> dict[str, Any]:
    """Bound both policy and privileged-teacher sequences before OPD launch."""

    root = Path(prime_root).resolve() if prime_root is not None else None
    python = _prime_python(root)
    if python is None:
        raise PrimeTrainingError(
            "OPD replay token audit requires the pinned PRIME interpreter; set "
            "PRIME_RL_PYTHON or pass prime_root"
        )
    renderer_name = _renderer_name(report)
    snapshot = Path(report["resolved_snapshot"]).resolve()
    script = r"""
import json
import sys
from pathlib import Path
from renderers.base import create_renderer, load_tokenizer
from renderers.configs import Qwen35RendererConfig, Qwen36RendererConfig
from prime_rl.utils.chat_template import (
    deserialize_tool_calls,
    normalize_messages,
    strip_message_content,
)

path = Path(sys.argv[1])
snapshot = sys.argv[2]
renderer_name = sys.argv[3]
context_template = sys.argv[4]
seq_len = int(sys.argv[5])
max_completion = int(sys.argv[6])
tokenizer = load_tokenizer(snapshot)
config = (
    Qwen36RendererConfig(enable_thinking=True, preserve_thinking=True)
    if renderer_name == "qwen3.6"
    else Qwen35RendererConfig(enable_thinking=True)
)
renderer = create_renderer(tokenizer, config)
audited = []
with path.open("r", encoding="utf-8") as stream:
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        row = json.loads(line)
        request = row.get("request")
        if not isinstance(request, dict):
            raise RuntimeError(f"row {line_number} has no request object")
        messages = strip_message_content(
            deserialize_tool_calls(normalize_messages(request.get("messages"), default_role="user"))
        )
        raw_tools = request.get("tools") or []
        tools = [
            tool if tool.get("type") == "function" and "function" in tool else {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "parameters": tool.get("parameters"),
                    **({} if tool.get("strict") is None else {"strict": tool["strict"]}),
                },
            }
            for tool in raw_tools
        ]
        policy_prompt = renderer.render_ids(messages, tools=tools, add_generation_prompt=True)
        context = row.get("privileged_context")
        if not isinstance(context, str) or not context.strip():
            raise RuntimeError(f"row {line_number} has no privileged_context")
        hint = context_template.format(context=context)
        hint_ids = renderer.render_ids(
            [{"role": "system", "content": hint}], add_generation_prompt=False
        )
        policy_bound = len(policy_prompt) + max_completion
        teacher_bound = len(hint_ids) + policy_bound
        if policy_bound > seq_len:
            raise RuntimeError(
                f"row {line_number} policy prompt+completion bound {policy_bound} exceeds {seq_len}"
            )
        if teacher_bound > seq_len:
            raise RuntimeError(
                f"row {line_number} privileged teacher bound {teacher_bound} exceeds {seq_len}"
            )
        audited.append({
            "line": line_number,
            "policy_prompt_tokens": len(policy_prompt),
            "privileged_prefix_tokens": len(hint_ids),
            "policy_sequence_bound": policy_bound,
            "teacher_sequence_bound": teacher_bound,
        })
if not audited:
    raise RuntimeError("decision replay contains no rows")
print(json.dumps({
    "schema": "harness-distill.opd-token-audit.v1",
    "renderer": renderer_name,
    "seq_len": seq_len,
    "max_completion_tokens": max_completion,
    "row_count": len(audited),
    "max_policy_sequence_bound": max(row["policy_sequence_bound"] for row in audited),
    "max_teacher_sequence_bound": max(row["teacher_sequence_bound"] for row in audited),
    "rows": audited,
}, sort_keys=True))
"""
    environment = os.environ.copy()
    if root is not None:
        environment["PYTHONPATH"] = os.pathsep.join(
            [
                str(root / "packages" / "prime-rl-configs" / "src"),
                str(root / "src"),
                str(root / "deps" / "renderers"),
            ]
            + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
        )
    completed = subprocess.run(
        [
            str(python),
            "-c",
            script,
            str(replay_jsonl),
            str(snapshot),
            renderer_name,
            context_template,
            str(seq_len),
            str(max_completion_tokens),
        ],
        text=True,
        capture_output=True,
        env=environment,
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise PrimeTrainingError(f"OPD replay token audit failed: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise PrimeTrainingError("OPD replay token audit returned malformed output") from exc
    result["replay_sha256"] = _sha256_file(replay_jsonl)
    result["model_revision"] = str(report.get("snapshot", {}).get("revision", ""))
    return result


def generate_opd_toml(
    *,
    smoke_report: str | Path,
    replay_jsonl: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    student_snapshot: str | Path,
    round_index: int,
    optimizer_updates: int = 100,
    seq_len: int = CAMPAIGN_SEQ_LEN,
    batch_size: int = 8,
    rank: int = 64,
    alpha: float = 128.0,
    dropout: float = 0.0,
    learning_rate: float = 2.0e-6,
    checkpoint_interval: int = 50,
    teacher_base_url: str = f"http://localhost:{TEACHER_SERVER_PORT}/v1",
    policy_gpu_memory_utilization: float = POLICY_GPU_MEMORY_UTILIZATION,
    prime_root: str | Path | None = None,
) -> PrimeConfigArtifact:
    """Generate a privileged-context OPD round on four infer/four train GPUs.

    ``student_snapshot`` must be the full HF snapshot obtained by merging the
    preceding SFT/OPD adapter.  The teacher always remains the exact original
    smoke-tested base on a separate port; this makes SFT -> OPD1 -> OPD2 ->
    OPD3 initialization explicit instead of silently restarting every round.
    """

    report = _read_smoke_report(smoke_report)
    data = _require_replay_dataset(replay_jsonl)
    destination = Path(output_dir).resolve()
    if round_index < 1:
        raise PrimeTrainingError("OPD round_index starts at one")
    prime_max_steps = _prime_terminal_step(optimizer_updates)
    if batch_size < 1:
        raise PrimeTrainingError("OPD sequence length and batch size must be positive")
    if seq_len != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError(
            f"campaign OPD seq_len must be exactly {CAMPAIGN_SEQ_LEN}, got {seq_len}"
        )
    if rank != 64 or alpha != 128:
        raise PrimeTrainingError("campaign LoRA must remain rank 64 / alpha 128")
    if teacher_base_url.rstrip("/") != f"http://localhost:{TEACHER_SERVER_PORT}/v1":
        raise PrimeTrainingError(
            f"frozen teacher must use its distinct port {TEACHER_SERVER_PORT} endpoint"
        )
    if policy_gpu_memory_utilization != POLICY_GPU_MEMORY_UTILIZATION:
        raise PrimeTrainingError(
            "co-resident policy inference must remain capped at gpu_memory_utilization=0.40"
        )
    targets = _exact_target_patterns(report)
    inference_targets = _inference_target_suffixes(report)
    renderer = _renderer_name(report)
    teacher_snapshot = Path(report["resolved_snapshot"]).resolve()
    snapshot = Path(student_snapshot).resolve()
    if not snapshot.is_dir():
        raise PrimeTrainingError(f"merged OPD student snapshot is absent: {snapshot}")
    if snapshot == teacher_snapshot:
        raise PrimeTrainingError(
            "OPD student must be a merged prior-stage snapshot, not the frozen teacher base"
        )
    context_template = (
        "Use this audited decision state, derived only from public evidence already visible "
        "to the agent:\n<decision_state>\n{context}\n</decision_state>"
    )
    replay_audit = _audit_replay_dataset(
        replay_jsonl=data,
        report=report,
        context_template=context_template,
        seq_len=seq_len,
        max_completion_tokens=4096,
        prime_root=prime_root,
    )
    audit_path = Path(config_path).resolve().with_suffix(".replay_audit.json")
    atomic_json(audit_path, replay_audit)
    replay_audit_sha256 = _sha256_file(audit_path)
    adapter_name = f"harness-opd-r{round_index}-r64-a128"
    lines = [
        f"# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT}) + reviewed privileged-OPD patch",
        f"# train-top-p patch: {TRAIN_TOP_P_PATCH_SHA256}",
        f"# replay-token-audit: {audit_path} sha256={replay_audit_sha256}",
        f"# {CORRECTIVE_CE_LIMITATION}",
        f"# requested_optimizer_updates = {optimizer_updates}",
        "# PRIME max_steps is the one-indexed terminal optimizer step.",
        f"max_steps = {prime_max_steps}",
        f"seq_len = {seq_len}",
        f"output_dir = {_quoted(destination)}",
        "clean_output_dir = false",
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        'VLLM_API_KEY = "EMPTY"',
        "",
        "[deployment]",
        'type = "single_node"',
        "num_train_gpus = 4",
        "num_infer_gpus = 4",
        "gpus_per_node = 8",
        "",
        "[weight_broadcast]",
        'type = "filesystem"',
        "",
        "[model]",
        f"name = {_quoted(snapshot)}",
        "",
        "[ckpt]",
        f"interval = {checkpoint_interval}",
        "resume_step = -1",
        "keep_last = 2",
        "",
        "[wandb]",
        'project = "harness-distill"',
        f"name = {_quoted(f'opd-round-{round_index}')}",
        "",
        "[trainer]",
        "",
        "[trainer.model]",
        f"seq_len = {seq_len}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[trainer.model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[trainer.model.lora]",
        f"rank = {rank}",
        f"alpha = {alpha}",
        f"dropout = {dropout}",
        f"target_modules = {_array(targets)}",
        "modules_to_save = []",
        "",
        "[trainer.optim]",
        'type = "adamw"',
        f"lr = {learning_rate}",
        "weight_decay = 0.01",
        "max_norm = 1.0",
        "",
        "[trainer.ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
        "",
        "[orchestrator]",
        f"batch_size = {batch_size}",
        "group_size = 1",
        f"max_inflight_rollouts = {max(32, batch_size * 4)}",
        "max_off_policy_steps = 2",
        "pool_size = 32",
        "",
        "[orchestrator.model.lora]",
        f"name = {_quoted(adapter_name)}",
        f"rank = {rank}",
        f"alpha = {alpha}",
        "",
        "[orchestrator.renderer]",
        f"name = {_quoted(renderer)}",
        "enable_thinking = true",
        *(["preserve_thinking = true"] if renderer == "qwen3.6" else []),
        "",
        "[orchestrator.algo]",
        'type = "opd"',
        f"context_key = {_quoted(PRIVILEGED_CONTEXT_KEY)}",
        f"context_template = {_quoted(context_template)}",
        "",
        "[orchestrator.algo.teacher]",
        f"name = {_quoted(teacher_snapshot)}",
        f"base_url = [{_quoted(teacher_base_url)}]",
        "",
        "[orchestrator.algo.renderer]",
        f"name = {_quoted(renderer)}",
        "enable_thinking = true",
        *(["preserve_thinking = true"] if renderer == "qwen3.6" else []),
        "",
        "[orchestrator.train.sampling]",
        "temperature = 1.0",
        "top_p = 0.95",
        "max_completion_tokens = 4096",
        "",
        "[orchestrator.train.sampling.extra_body]",
        "top_k = 20",
        "presence_penalty = 0.0",
        "",
        "[[orchestrator.train.env]]",
        f"name = {_quoted(f'decision-replay-round-{round_index}')}",
        (
            f"taskset = {{ id = {_quoted(DECISION_REPLAY_PLUGIN)}, "
            f'path = {_quoted(data)}, split = "train" }}'
        ),
        (
            f"harness = {{ id = {_quoted(DECISION_REPLAY_PLUGIN)}, "
            'runtime = { type = "subprocess" } }'
        ),
        "group_size = 1",
        "max_turns = 1",
        "timeout = { setup = 300.0, rollout = 1800.0, finalize = 300.0, scoring = 300.0 }",
        'pool = { type = "static", num_workers = 32 }',
        "",
        "[inference]",
        f"gpu_memory_utilization = {policy_gpu_memory_utilization}",
        "data_parallel_rpc_port = 13345",
        "enable_prefix_caching = false",
        f"lora_target_modules = {_array(inference_targets)}",
        "",
        "[inference.model]",
        f"max_model_len = {seq_len}",
        'dtype = "bfloat16"',
        'tool_call_parser = "qwen3_coder"',
        'reasoning_parser = "qwen3"',
        "",
        "[inference.server]",
        f"port = {POLICY_SERVER_PORT}",
        "",
        "[inference.parallel]",
        "dp = 4",
        "tp = 1",
        "",
        "[inference.vllm_extra]",
        "language_model_only = true",
    ]
    target = _write_toml(config_path, lines)
    _validate_static_opd(tomllib.loads(target.read_text(encoding="utf-8")))
    return PrimeConfigArtifact(
        kind="opd",
        path=target,
        output_dir=destination,
        sha256=_sha256_file(target),
        model_snapshot=snapshot,
        dataset_path=data,
        target_count=len(targets),
        optimizer_updates=optimizer_updates,
        prime_max_steps=prime_max_steps,
        limitations=(CORRECTIVE_CE_LIMITATION,),
    )


def generate_teacher_inference_toml(
    *,
    smoke_report: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    teacher_port: int = TEACHER_SERVER_PORT,
    gpu_memory_utilization: float = TEACHER_GPU_MEMORY_UTILIZATION,
    seq_len: int = CAMPAIGN_SEQ_LEN,
) -> PrimeInferenceArtifact:
    """Generate the frozen original-base teacher that co-resides on GPUs 0-3."""

    report = _read_smoke_report(smoke_report)
    snapshot = Path(report["resolved_snapshot"]).resolve()
    destination = Path(output_dir).resolve()
    if teacher_port != TEACHER_SERVER_PORT:
        raise PrimeTrainingError(f"teacher must use reviewed port {TEACHER_SERVER_PORT}")
    if gpu_memory_utilization != TEACHER_GPU_MEMORY_UTILIZATION:
        raise PrimeTrainingError(
            "co-resident teacher must remain capped at gpu_memory_utilization=0.40"
        )
    if seq_len != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError(f"campaign teacher seq_len must be exactly {CAMPAIGN_SEQ_LEN}")
    lines = [
        f"# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT}) frozen teacher",
        "# DP4 gives four independent one-call teacher replicas; each shares one B200",
        "# with one DP4 policy replica and both vLLM reservations are capped at 0.40.",
        f"output_dir = {_quoted(destination)}",
        f"gpu_memory_utilization = {gpu_memory_utilization}",
        "data_parallel_rpc_port = 14345",
        "enable_prefix_caching = false",
        "api_server_count = 1",
        "",
        "[env_vars]",
        'VLLM_API_KEY = "EMPTY"',
        "",
        "[server]",
        'host = "127.0.0.1"',
        f"port = {teacher_port}",
        "",
        "[model]",
        f"name = {_quoted(snapshot)}",
        f"max_model_len = {seq_len}",
        'dtype = "bfloat16"',
        'tool_call_parser = "qwen3_coder"',
        'reasoning_parser = "qwen3"',
        "",
        "[parallel]",
        "dp = 4",
        "tp = 1",
        "",
        "[vllm_extra]",
        "language_model_only = true",
    ]
    target = _write_toml(config_path, lines)
    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    _validate_static_teacher(parsed)
    return PrimeInferenceArtifact(
        path=target,
        output_dir=destination,
        sha256=_sha256_file(target),
        model_snapshot=snapshot,
        port=teacher_port,
        gpu_memory_utilization=gpu_memory_utilization,
        gpu_ids=INFERENCE_GPU_IDS,
    )


def _validate_static_teacher(config: Mapping[str, Any]) -> None:
    if config.get("gpu_memory_utilization") != TEACHER_GPU_MEMORY_UTILIZATION:
        raise PrimeTrainingError("teacher vLLM memory cap drifted from 0.40")
    if config.get("enable_prefix_caching") is not False:
        raise PrimeTrainingError("teacher prefix caching must remain disabled")
    if config.get("server") != {
        "host": "127.0.0.1",
        "port": TEACHER_SERVER_PORT,
    }:
        raise PrimeTrainingError("teacher host/port drifted")
    if config.get("parallel") != {"dp": 4, "tp": 1}:
        raise PrimeTrainingError("teacher topology must remain DP4 x TP1 on GPUs 0-3")
    if config.get("data_parallel_rpc_port") != 14345:
        raise PrimeTrainingError("teacher DP RPC port must remain distinct from policy")
    if config.get("model", {}).get("max_model_len") != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError("teacher context length drifted")


def _read_served_models(base_url: str, *, timeout: float = 5.0) -> set[str]:
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": "Bearer EMPTY"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost only
        payload = json.loads(response.read().decode("utf-8"))
    return {
        str(item.get("id"))
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    }


@contextmanager
def run_frozen_teacher(
    artifact: PrimeInferenceArtifact,
    *,
    phase_dir: str | Path,
    prime_root: str | Path | None = None,
    executable: str | Path | None = None,
    gpu_ids: Sequence[int] = INFERENCE_GPU_IDS,
    startup_timeout: float = 1800.0,
    extra_env: Mapping[str, str] | None = None,
) -> Iterator[FrozenTeacherHandle]:
    """Start, verify, yield, and terminate only the frozen teacher we own."""

    if _sha256_file(artifact.path) != artifact.sha256:
        raise PrimeTrainingError("teacher config bytes changed after audited generation")
    if tuple(gpu_ids) != INFERENCE_GPU_IDS:
        raise PrimeTrainingError(
            f"teacher must share inference GPUs {INFERENCE_GPU_IDS}; got {tuple(gpu_ids)}"
        )
    parsed = tomllib.loads(artifact.path.read_text(encoding="utf-8"))
    _validate_static_teacher(parsed)
    validation = validate_with_prime(
        artifact.path,
        "inference",
        prime_root=prime_root,
        require=True,
    )
    phase = Path(phase_dir).resolve()
    phase.mkdir(parents=True, exist_ok=True)
    log_path = phase / "frozen_teacher.log"
    manifest_path = phase / "frozen_teacher_manifest.json"
    base_url = f"http://127.0.0.1:{artifact.port}/v1"
    try:
        existing = _read_served_models(base_url, timeout=1.0)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        existing = set()
    if existing:
        raise PrimeTrainingError(
            f"teacher port {artifact.port} is already serving {sorted(existing)}; "
            "refusing to adopt an unowned process"
        )
    requested = str(executable) if executable is not None else "inference"
    command_path = shutil.which(requested)
    if command_path is None:
        raise PrimeTrainingError(f"PRIME inference executable not found: {requested}")
    command = [command_path, "@", str(artifact.path)]
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = ",".join(str(item) for item in gpu_ids)
    environment["VLLM_API_KEY"] = "EMPTY"
    if extra_env:
        environment.update({str(key): str(value) for key, value in extra_env.items()})
    started = time.time()
    with _exclusive_lock(phase / ".teacher.lock"):
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            running = {
                "schema": "harness-distill.frozen-teacher.v1",
                "status": "starting",
                "pid": process.pid,
                "command": command,
                "config": str(artifact.path),
                "config_sha256": artifact.sha256,
                "model": str(artifact.model_snapshot),
                "base_url": base_url,
                "gpu_ids": list(gpu_ids),
                "gpu_memory_utilization": artifact.gpu_memory_utilization,
                "runtime_validation": validation,
                "started_unix": started,
            }
            atomic_json(manifest_path, running)
            deadline = time.monotonic() + startup_timeout
            served: set[str] = set()
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
                    raise PrimeTrainingError(
                        f"frozen teacher exited {process.returncode} during startup: {tail}"
                    )
                try:
                    served = _read_served_models(base_url)
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                    time.sleep(2.0)
                    continue
                if str(artifact.model_snapshot) in served:
                    break
                time.sleep(2.0)
            else:
                raise PrimeTrainingError(
                    f"frozen teacher did not serve {artifact.model_snapshot} within "
                    f"{startup_timeout:.0f}s"
                )
            ready = {**running, "status": "ready", "served_models": sorted(served)}
            atomic_json(manifest_path, ready)
            handle = FrozenTeacherHandle(
                pid=process.pid,
                base_url=base_url,
                model_name=str(artifact.model_snapshot),
                log_path=log_path,
                manifest_path=manifest_path,
            )
            try:
                yield handle
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=30)
                atomic_json(
                    manifest_path,
                    {
                        **ready,
                        "status": "stopped",
                        "returncode": process.returncode,
                        "ended_unix": time.time(),
                    },
                )


def _validate_static_sft(config: Mapping[str, Any]) -> None:
    try:
        masks = config["data"]["loss_mask"]
        lora = config["model"]["lora"]
        renderer = config["renderer"]
    except (KeyError, TypeError) as exc:
        raise PrimeTrainingError(f"incomplete SFT config: {exc}") from exc
    if masks != {"system": False, "user": False, "assistant": True, "tool": False}:
        raise PrimeTrainingError("SFT loss must cover assistant messages only")
    if lora.get("rank") != 64 or lora.get("alpha") != 128.0:
        raise PrimeTrainingError("SFT LoRA rank/alpha drifted")
    if not all(
        str(value).startswith("^") and str(value).endswith("$") for value in lora["target_modules"]
    ):
        raise PrimeTrainingError("SFT LoRA targets are not anchored full-name patterns")
    if renderer.get("enable_thinking") is not True:
        raise PrimeTrainingError("SFT renderer must preserve thinking")
    if renderer.get("name") == "qwen3.6" and renderer.get("preserve_thinking") is not True:
        raise PrimeTrainingError("Qwen3.6 SFT must preserve historical thinking")
    if config.get("loss_impl") != "liger_fused":
        raise PrimeTrainingError("65K SFT must use fused CE rather than materialize full logits")
    if config["model"].get("seq_len") != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError("SFT sequence length drifted from 65,536")
    if config["model"].get("cp") != 2 or config["model"].get("cp_style") != "ulysses":
        raise PrimeTrainingError("65K SFT must use reviewed Ulysses CP2")
    if config.get("clean_output_dir") is not False or config["ckpt"].get("resume_step") != -1:
        raise PrimeTrainingError("SFT config is not safely resumable")


def _validate_static_opd(config: Mapping[str, Any]) -> None:
    try:
        deployment = config["deployment"]
        algo = config["orchestrator"]["algo"]
        train_env = config["orchestrator"]["train"]["env"][0]
        trainer_lora = config["trainer"]["model"]["lora"]
        inference = config["inference"]
    except (KeyError, TypeError, IndexError) as exc:
        raise PrimeTrainingError(f"incomplete OPD config: {exc}") from exc
    if deployment != {
        "type": "single_node",
        "num_train_gpus": 4,
        "num_infer_gpus": 4,
        "gpus_per_node": 8,
    }:
        raise PrimeTrainingError("OPD must use the reviewed four-infer/four-trainer split")
    if config["weight_broadcast"].get("type") != "filesystem":
        raise PrimeTrainingError("LoRA OPD requires filesystem weight broadcast")
    if algo.get("type") != "opd" or algo.get("context_key") != PRIVILEGED_CONTEXT_KEY:
        raise PrimeTrainingError("OPD privileged context is not enabled")
    if algo["teacher"].get("name") == config["model"].get("name"):
        raise PrimeTrainingError("OPD teacher must differ from the merged student snapshot")
    if algo["teacher"].get("base_url") != [f"http://localhost:{TEACHER_SERVER_PORT}/v1"]:
        raise PrimeTrainingError("OPD teacher must use the distinct frozen-teacher endpoint")
    if train_env["taskset"].get("id") != DECISION_REPLAY_PLUGIN:
        raise PrimeTrainingError("OPD is not using the audited replay taskset")
    if train_env["harness"].get("id") != DECISION_REPLAY_PLUGIN:
        raise PrimeTrainingError("OPD is not using the one-call replay harness")
    if train_env.get("max_turns") != 1:
        raise PrimeTrainingError("decision replay must stop after exactly one model turn")
    if trainer_lora.get("rank") != 64 or trainer_lora.get("alpha") != 128.0:
        raise PrimeTrainingError("OPD LoRA rank/alpha drifted")
    if inference.get("enable_prefix_caching") is not False:
        raise PrimeTrainingError("prefix caching must remain disabled")
    if inference.get("gpu_memory_utilization") != POLICY_GPU_MEMORY_UTILIZATION:
        raise PrimeTrainingError("policy vLLM memory cap drifted from 0.40")
    if inference.get("data_parallel_rpc_port") != 13345:
        raise PrimeTrainingError("policy DP RPC port drifted")
    if inference.get("server", {}).get("port") != POLICY_SERVER_PORT:
        raise PrimeTrainingError("policy endpoint must remain distinct on port 8000")
    if inference["parallel"] != {"dp": 4, "tp": 1}:
        raise PrimeTrainingError("OPD inference must data-parallelize across four GPUs")
    if config.get("seq_len") != CAMPAIGN_SEQ_LEN:
        raise PrimeTrainingError("OPD sequence length drifted from 65,536")
    trainer_model = config["trainer"]["model"]
    if trainer_model.get("cp") != 2 or trainer_model.get("cp_style") != "ulysses":
        raise PrimeTrainingError("65K OPD training must use reviewed Ulysses CP2")
    if config["orchestrator"]["train"]["sampling"].get("top_p") != 0.95:
        raise PrimeTrainingError("OPD must use the campaign's reviewed top_p=0.95")
    if config.get("clean_output_dir") is not False or config["ckpt"].get("resume_step") != -1:
        raise PrimeTrainingError("OPD config is not safely resumable")
    if set(INFERENCE_GPU_IDS) & set(TRAINER_GPU_IDS) or set(
        (*INFERENCE_GPU_IDS, *TRAINER_GPU_IDS)
    ) != set(range(8)):
        raise PrimeTrainingError("reviewed topology must use exactly eight physical GPUs")


def _prime_python(prime_root: Path | None = None) -> Path | None:
    configured = os.environ.get("PRIME_RL_PYTHON")
    if configured:
        # Never resolve a venv interpreter symlink: resolving it escapes the
        # venv and silently drops PRIME's installed dependencies.
        target = Path(configured).absolute()
        return target.absolute() if target.is_file() else None
    if prime_root is not None:
        target = prime_root / ".venv" / "bin" / "python"
        if target.is_file():
            return target.absolute()
    try:
        import prime_rl.configs.rl  # noqa: F401
        import prime_rl.configs.sft  # noqa: F401
    except ImportError:
        return None
    # Do not resolve a virtualenv's Python symlink: doing so escapes the venv
    # and silently drops PRIME's installed dependencies.
    return Path(sys.executable).absolute()


def validate_with_prime(
    config_path: str | Path,
    kind: Literal["sft", "opd", "inference"],
    *,
    prime_root: str | Path | None = None,
    require: bool = False,
) -> dict[str, Any]:
    """Parse with PRIME's actual Pydantic schema, including plugin narrowing."""

    target = Path(config_path).resolve()
    root = Path(prime_root).resolve() if prime_root is not None else None
    python = _prime_python(root)
    if python is None:
        result = {
            "status": "unavailable",
            "reason": "an importable pinned PRIME runtime was not found",
        }
        if require:
            raise PrimeTrainingError(result["reason"])
        return result
    module = {"sft": "sft", "opd": "rl", "inference": "inference"}[kind]
    class_name = {
        "sft": "SFTConfig",
        "opd": "RLConfig",
        "inference": "InferenceConfig",
    }[kind]
    script = (
        "import json,tomllib,sys\n"
        "from pathlib import Path\n"
        f"from prime_rl.configs.{module} import {class_name} as C\n"
        "p=Path(sys.argv[1]); c=C.model_validate(tomllib.loads(p.read_text()))\n"
        "print(json.dumps({'status':'ok','type':type(c).__name__}))\n"
    )
    env = os.environ.copy()
    if root is not None:
        source_paths = [
            root / "packages" / "prime-rl-configs" / "src",
            root / "src",
        ]
        env["PYTHONPATH"] = os.pathsep.join(
            [str(path) for path in source_paths]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
    completed = subprocess.run(
        [str(python), "-c", script, str(target)],
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        raise PrimeTrainingError(f"PRIME rejected {kind} config {target}: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise PrimeTrainingError("PRIME config validator returned malformed output") from exc
    result.update(
        {
            "python": str(python),
            "prime_commit": PRIME_COMMIT,
            "config_sha256": _sha256_file(target),
        }
    )
    return result


def locate_latest_adapter(output_dir: str | Path) -> Path:
    """Locate only a final/stable PRIME weight export, never a live broadcast."""

    root = Path(output_dir).resolve()
    candidates: list[tuple[int, Path]] = []
    for step_dir in (root / "weights").glob("step_*"):
        match = re.fullmatch(r"step_(\d+)", step_dir.name)
        adapter = step_dir / "lora_adapters"
        if not match or not (step_dir / "STABLE").is_file():
            continue
        if not (adapter / "adapter_config.json").is_file():
            continue
        if not any((adapter / name).is_file() for name in _ADAPTER_WEIGHT_NAMES):
            continue
        candidates.append((int(match.group(1)), adapter))
    if not candidates:
        raise PrimeTrainingError(f"no stable PRIME LoRA adapter found under {root / 'weights'}")
    return max(candidates, key=lambda item: item[0])[1].resolve()


def _adapter_manifest(adapter: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(item for item in adapter.rglob("*") if item.is_file()):
        files[path.relative_to(adapter).as_posix()] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    if config.get("r") != 64 or float(config.get("lora_alpha", -1)) != 128.0:
        raise PrimeTrainingError("saved adapter rank/alpha differs from campaign")
    if not config.get("target_modules"):
        raise PrimeTrainingError("saved adapter declares no target modules")
    return {"path": str(adapter), "files": files, "adapter_config": config}


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PrimeTrainingError(f"another launcher owns {path}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def launch_prime_training(
    artifact: PrimeConfigArtifact,
    *,
    phase_dir: str | Path,
    prime_root: str | Path | None = None,
    executable: str | Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> PrimeRunResult:
    """Validate and run ``sft @ config`` or ``rl @ config`` exactly once/resumably."""

    phase = Path(phase_dir).resolve()
    phase.mkdir(parents=True, exist_ok=True)
    manifest_path = phase / "launch_manifest.json"
    log_path = phase / "launcher.log"
    with _exclusive_lock(phase / ".launch.lock"):
        if _sha256_file(artifact.path) != artifact.sha256:
            raise PrimeTrainingError("PRIME config bytes changed after audited generation")
        prior: dict[str, Any] | None = None
        if manifest_path.is_file():
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                prior.get("schema") != PRIME_LAUNCH_SCHEMA
                or prior.get("kind") != artifact.kind
                or prior.get("config_sha256") != artifact.sha256
                or prior.get("output_dir") != str(artifact.output_dir)
            ):
                raise PrimeTrainingError(
                    "prior launch manifest differs from requested PRIME artifact"
                )
            if prior.get("status") == "complete":
                prior_recovery = prior.get("recovery")
                if (
                    not isinstance(prior_recovery, dict)
                    or prior_recovery.get("schema") != RECOVERY_SCHEMA
                    or prior_recovery.get("final_complete") is not True
                    or prior_recovery.get("selected_step") != artifact.prime_max_steps
                ):
                    raise PrimeTrainingError("completed phase lacks a final recovery attestation")
                adapter_value = prior.get("adapter", {}).get("path")
                if not isinstance(adapter_value, str) or not adapter_value:
                    raise PrimeTrainingError("completed phase manifest has no adapter path")
                adapter = Path(adapter_value)
                if not adapter.is_dir():
                    raise PrimeTrainingError("completed phase manifest points to a missing adapter")
                _adapter_manifest(adapter)
                return PrimeRunResult(
                    kind=artifact.kind,
                    status="complete",
                    config_path=artifact.path,
                    output_dir=artifact.output_dir,
                    adapter_path=adapter.resolve(),
                    manifest_path=manifest_path,
                    resumed=True,
                    returncode=0,
                )
        elif not (phase / "recovery" / "identity.json").is_file():
            dynamic_roots = [
                artifact.output_dir / "checkpoints",
                artifact.output_dir / "weights",
                artifact.output_dir / "run_default" / "checkpoints",
                artifact.output_dir / "run_default" / "broadcasts",
                artifact.output_dir / "run_default" / "rollouts",
            ]
            if any(any(root.glob("step_*")) for root in dynamic_roots):
                raise PrimeTrainingError(
                    "unbound PRIME checkpoints exist without a launch or recovery identity"
                )

        parsed = tomllib.loads(artifact.path.read_text(encoding="utf-8"))
        validator = _validate_static_sft if artifact.kind == "sft" else _validate_static_opd
        validator(parsed)
        validation = validate_with_prime(
            artifact.path,
            artifact.kind,
            prime_root=prime_root,
            require=True,
        )
        sft_world_size = int(parsed["deployment"]["num_gpus"]) if artifact.kind == "sft" else None
        try:
            recovery = reconcile_prime_output(
                kind=artifact.kind,
                output_dir=artifact.output_dir,
                phase_dir=phase,
                config_sha256=artifact.sha256,
                prime_max_steps=artifact.prime_max_steps,
                sft_world_size=sft_world_size,
            )
        except PrimeRecoveryError as exc:
            raise PrimeTrainingError(f"unsafe PRIME checkpoint tree: {exc}") from exc

        started = time.time()
        if recovery.final_complete:
            adapter = locate_latest_adapter(artifact.output_dir)
            adopted = {
                "schema": PRIME_LAUNCH_SCHEMA,
                "status": "complete",
                "kind": artifact.kind,
                "prime_version": PRIME_VERSION,
                "prime_commit": PRIME_COMMIT,
                "config": str(artifact.path),
                "config_sha256": artifact.sha256,
                "output_dir": str(artifact.output_dir),
                "command": prior.get("command") if prior else None,
                "resumed": True,
                "started_unix": prior.get("started_unix", started) if prior else started,
                "ended_unix": started,
                "duration_seconds": 0.0,
                "returncode": 0,
                "runtime_validation": validation,
                "limitations": list(artifact.limitations),
                "recovery": recovery.to_dict(),
                "recovered_final_without_relaunch": True,
                "adapter": _adapter_manifest(adapter),
            }
            atomic_json(manifest_path, adopted)
            return PrimeRunResult(
                kind=artifact.kind,
                status="complete",
                config_path=artifact.path,
                output_dir=artifact.output_dir,
                adapter_path=adapter,
                manifest_path=manifest_path,
                resumed=True,
                returncode=0,
            )

        command_name = "sft" if artifact.kind == "sft" else "rl"
        requested = str(executable) if executable is not None else command_name
        command_path = shutil.which(requested)
        if command_path is None:
            raise PrimeTrainingError(f"PRIME executable not found: {requested}")
        resumed = recovery.resumed
        command = [command_path, "@", str(artifact.path)]
        environment = os.environ.copy()
        if extra_env:
            environment.update({str(key): str(value) for key, value in extra_env.items()})
        environment["FLA_TILELANG"] = "0"
        environment["NEVER_CLEAN_OUTPUT_DIR"] = "1"
        environment.setdefault("WANDB_MODE", "disabled")
        running = {
            "schema": PRIME_LAUNCH_SCHEMA,
            "status": "running",
            "kind": artifact.kind,
            "prime_version": PRIME_VERSION,
            "prime_commit": PRIME_COMMIT,
            "config": str(artifact.path),
            "config_sha256": artifact.sha256,
            "output_dir": str(artifact.output_dir),
            "command": command,
            "resumed": resumed,
            "started_unix": started,
            "runtime_validation": validation,
            "recovery": recovery.to_dict(),
            "limitations": list(artifact.limitations),
        }
        atomic_json(manifest_path, running)
        with log_path.open("a", encoding="utf-8") as log:
            launched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
            log.write(f"\n=== {artifact.kind} launch {launched_at} ===\n")
            log.flush()
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
                check=False,
            )
        ended = time.time()
        if completed.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            failed = {
                **running,
                "status": "failed",
                "returncode": completed.returncode,
                "ended_unix": ended,
                "duration_seconds": ended - started,
                "log": str(log_path),
                "log_tail": tail,
            }
            atomic_json(manifest_path, failed)
            raise PrimeTrainingError(
                f"PRIME {artifact.kind} exited {completed.returncode}; see {log_path}"
            )
        try:
            completed_recovery = reconcile_prime_output(
                kind=artifact.kind,
                output_dir=artifact.output_dir,
                phase_dir=phase,
                config_sha256=artifact.sha256,
                prime_max_steps=artifact.prime_max_steps,
                sft_world_size=sft_world_size,
            )
        except PrimeRecoveryError as exc:
            failed = {
                **running,
                "status": "failed",
                "returncode": 0,
                "ended_unix": ended,
                "duration_seconds": ended - started,
                "log": str(log_path),
                "recovery_error": str(exc),
            }
            atomic_json(manifest_path, failed)
            raise PrimeTrainingError(
                f"PRIME {artifact.kind} returned success with unsafe checkpoints: {exc}"
            ) from exc
        if not completed_recovery.final_complete:
            failed = {
                **running,
                "status": "failed",
                "returncode": 0,
                "ended_unix": ended,
                "duration_seconds": ended - started,
                "log": str(log_path),
                "recovery": completed_recovery.to_dict(),
                "recovery_error": "PRIME returned success without an attested final checkpoint",
            }
            atomic_json(manifest_path, failed)
            raise PrimeTrainingError(
                f"PRIME {artifact.kind} returned success without an attested final checkpoint"
            )
        adapter = locate_latest_adapter(artifact.output_dir)
        complete = {
            **running,
            "status": "complete",
            "returncode": 0,
            "ended_unix": ended,
            "duration_seconds": ended - started,
            "log": str(log_path),
            "recovery": completed_recovery.to_dict(),
            "adapter": _adapter_manifest(adapter),
        }
        atomic_json(manifest_path, complete)
        return PrimeRunResult(
            kind=artifact.kind,
            status="complete",
            config_path=artifact.path,
            output_dir=artifact.output_dir,
            adapter_path=adapter,
            manifest_path=manifest_path,
            resumed=resumed,
            returncode=0,
        )
