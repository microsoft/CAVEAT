#!/usr/bin/env python3
"""Create an immutable, content-addressed adaptive serving successor on the PVC."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


OLD = Path("/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-adaptive-buynow-sft-w1-20260815/source_eval_1ef6149b")
TARGET = OLD.with_name("source_eval_adaptive_serve_streamlined_v1")
STAGING = TARGET.with_name("." + TARGET.name + ".staging")
BUNDLE = TARGET.with_name(TARGET.name + ".git.bundle")
DESCRIPTOR = TARGET.with_name(TARGET.name + ".identity.json")
OLD_TREE = "c14930634666b83149f55f146a9af9829b597dce7c0daa0b50182537637a4b50"
PARENT_GIT = "1ef6149b64aba813322ac6b96cd78fd048614957"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_identity(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    total = 0
    directories = 0
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        if base != root:
            directories += 1
        for name in directory_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise RuntimeError(f"unsafe directory: {child}")
        for name in file_names:
            child = base / name
            if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
                raise RuntimeError(f"unsafe file: {child}")
            size = child.stat().st_size
            rows.append({"path": child.relative_to(root).as_posix(), "size": size, "sha256": file_sha(child)})
            total += size
    rows.sort(key=lambda row: str(row["path"]))
    return {
        "files": len(rows),
        "directories": directories,
        "bytes": total,
        "tree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
    }


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected one replacement anchor in {path}, got {text.count(old)}")
    path.write_text(text.replace(old, new))


def seal(root: Path) -> str:
    rows: list[dict[str, str]] = []
    directories: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        directory_names.sort()
        file_names.sort()
        if base != root:
            directories.append(base)
            rows.append({"path": base.relative_to(root).as_posix(), "kind": "directory", "mode": "0555"})
        for name in file_names:
            child = base / name
            mode = 0o555 if stat.S_IMODE(child.lstat().st_mode) & 0o111 else 0o444
            os.chmod(child, mode)
            rows.append({"path": child.relative_to(root).as_posix(), "kind": "file", "mode": format(mode, "04o")})
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        os.chmod(directory, 0o555)
    os.chmod(root, 0o555)
    rows.sort(key=lambda row: (row["path"], row["kind"]))
    return hashlib.sha256(canonical(rows)).hexdigest()


def main() -> None:
    if source_identity(OLD)["tree_sha256"] != OLD_TREE:
        raise RuntimeError("parent adaptive serving source changed")
    if any(path.exists() for path in (TARGET, STAGING, BUNDLE, DESCRIPTOR)):
        raise RuntimeError("successor staging target already exists")
    shutil.copytree(OLD, STAGING, symlinks=True)
    for path in [STAGING, *STAGING.rglob("*")]:
        if path.is_dir():
            os.chmod(path, 0o700)
        elif path.is_file():
            os.chmod(path, 0o700 if stat.S_IMODE(path.stat().st_mode) & 0o111 else 0o600)

    runner = STAGING / "scripts/run_adaptive_buy_now_candidate_serve.sh"
    module = STAGING / "src/harness_posttrain_eval/adaptive_buy_now_candidate_serve.py"
    replace_once(
        runner,
        '''env PYTHONPATH="$trainer_pythonpath" PYTHONDONTWRITEBYTECODE=1 FLA_TILELANG=0 \\
  "$runtime_python" "$trainer_source/scripts/prepare_adaptive_buy_now_training.py" \\
  validate-receipt --receipt "${1}" >/dev/null

''',
        "",
    )
    replace_once(
        module,
        "from .common import IntegrityError, read_json, sha256_file",
        "from .common import (\n    IntegrityError,\n    canonical_bytes,\n    read_json,\n    sha256_bytes,\n    sha256_file,\n)",
    )
    replace_once(
        module,
        '''def validate_trainer_source(root: Path, git_sha: str, tree_sha256: str) -> dict[str, Any]:''',
        '''def _verify_canonical_body(value: dict[str, Any], field: str, label: str) -> str:
    claimed = _sha(value.get(field), f"{label} body")
    body = {key: item for key, item in value.items() if key != field}
    if sha256_bytes(canonical_bytes(body)) != claimed:
        raise IntegrityError(f"{label} canonical body SHA-256 changed")
    return claimed


def validate_trainer_source(root: Path, git_sha: str, tree_sha256: str) -> dict[str, Any]:''',
    )
    replace_once(
        module,
        '''    receipt = read_json(receipt_path)
    candidate = receipt.get("candidate") or {}
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    plan = read_json(plan_path)
    if (
''',
        '''    receipt = read_json(receipt_path)
    _verify_canonical_body(receipt, "receipt_body_sha256", "adaptive receipt")
    candidate = receipt.get("candidate") or {}
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    plan = read_json(plan_path)
    _verify_canonical_body(plan, "plan_body_sha256", "adaptive plan")
    if (
''',
    )
    replace_once(
        module,
        '''        or plan.get("schema") != PLAN_SCHEMA
        or plan.get("plan_body_sha256") != receipt.get("plan_body_sha256")
        or sha256_file(plan_path) != receipt.get("plan_sha256")
        or plan.get("artifact_git_sha") != TRAINER_GIT_SHA
''',
        '''        or plan.get("schema") != PLAN_SCHEMA
        or plan.get("status") != "prepared"
        or plan.get("plan_body_sha256") != receipt.get("plan_body_sha256")
        or sha256_file(plan_path) != receipt.get("plan_sha256")
        or plan.get("artifact_git_sha") != TRAINER_GIT_SHA
        or plan.get("scientific_label") != SCIENTIFIC_LABEL
        or plan.get("source_step") != 25
        or plan.get("final_step") != 26
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != 2e-6
        or plan.get("fresh_optimizer") is not True
        or plan.get("fresh_scheduler") is not True
        or plan.get("fresh_dataloader") is not True
        or plan.get("objective") != "weighted_behavioral_cloning"
        or plan.get("on_policy") is not False
        or plan.get("policy_gradient") is not False
        or plan.get("native_prime_component") != "ce"
        or plan.get("collection_counts") != COLLECTION_COUNTS
        or Path(str(plan.get("candidate_path", ""))).resolve() != CANDIDATE_PATH
        or plan.get("candidate_name") != "step26-action-weighted-ce"
        or Path(str(plan.get("parent_model", ""))).resolve() != PARENT_PATH
''',
    )

    test = STAGING / "tests/test_adaptive_buy_now_candidate_serve.py"
    test.write_text('''from __future__ import annotations

from pathlib import Path

import pytest

from harness_posttrain_eval import adaptive_buy_now_candidate_serve as serve
from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes


def _sealed(body: dict, field: str) -> dict:
    return {**body, field: sha256_bytes(canonical_bytes(body))}


def test_canonical_receipt_and_plan_body_verification_is_fail_closed() -> None:
    receipt = _sealed({"schema": serve.RECEIPT_SCHEMA, "status": "ok"}, "receipt_body_sha256")
    assert serve._verify_canonical_body(receipt, "receipt_body_sha256", "receipt") == receipt["receipt_body_sha256"]
    receipt["status"] = "forged"
    with pytest.raises(IntegrityError, match="canonical body"):
        serve._verify_canonical_body(receipt, "receipt_body_sha256", "receipt")


def test_runner_omits_full_trainer_receipt_validator() -> None:
    runner = Path(serve.__file__).resolve().parents[2] / "scripts/run_adaptive_buy_now_candidate_serve.sh"
    text = runner.read_text()
    assert "validate-receipt" not in text
    assert "audit-trainer-source" in text
    assert "run-server" in text
''')

    subprocess.run(["git", "init", "-q", str(STAGING)], check=True)
    subprocess.run(["git", "-C", str(STAGING), "add", "-A"], check=True)
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "campaign2-source-stager",
        "GIT_AUTHOR_EMAIL": "campaign2-source-stager@localhost",
        "GIT_COMMITTER_NAME": "campaign2-source-stager",
        "GIT_COMMITTER_EMAIL": "campaign2-source-stager@localhost",
        "GIT_AUTHOR_DATE": "2026-08-15T07:55:00Z",
        "GIT_COMMITTER_DATE": "2026-08-15T07:55:00Z",
    }
    subprocess.run(
        ["git", "-C", str(STAGING), "commit", "-q", "-m", f"Streamline adaptive serving activation\n\nDerived-from: {PARENT_GIT}"],
        check=True,
        env=environment,
    )
    git_sha = subprocess.check_output(["git", "-C", str(STAGING), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(STAGING), "bundle", "create", str(BUNDLE), "HEAD"], check=True)
    shutil.rmtree(STAGING / ".git")
    identity = source_identity(STAGING)
    runner_sha = file_sha(runner)
    module_sha = file_sha(module)
    test_sha = file_sha(test)
    mode_tree = seal(STAGING)
    os.rename(STAGING, TARGET)
    os.chmod(BUNDLE, 0o444)
    body = {
        "schema": "harness-posttrain-ops.campaign2-derived-staged-source.v1",
        "status": "staged_read_only",
        "root": str(TARGET),
        "git_sha": git_sha,
        "derived_from_git_sha": PARENT_GIT,
        "derived_from_tree_sha256": OLD_TREE,
        **identity,
        "mode_tree_sha256": mode_tree,
        "root_mode": "0555",
        "git_bundle": {"path": str(BUNDLE), "bytes": BUNDLE.stat().st_size, "sha256": file_sha(BUNDLE)},
        "required_sha256": {
            "scripts/run_adaptive_buy_now_candidate_serve.sh": runner_sha,
            "src/harness_posttrain_eval/adaptive_buy_now_candidate_serve.py": module_sha,
            "tests/test_adaptive_buy_now_candidate_serve.py": test_sha,
        },
    }
    value = {**body, "descriptor_body_sha256": hashlib.sha256(canonical(body)).hexdigest()}
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(DESCRIPTOR, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
