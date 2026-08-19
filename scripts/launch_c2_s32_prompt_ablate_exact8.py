#!/usr/bin/env python3
"""Launch the receipt-bound S32 Exact8 prompt-only ablation once."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
PROMPT_SOURCE = WORKSPACE / ".worktrees/c2_s32_precommit_prompt_w1"
EVALUATOR_SOURCE = WORKSPACE / "training/harness_posttrain_eval/src"
CAMPAIGN = WORKSPACE / "training/harness_posttrain_eval/configs/campaign.json"
ROOT = (
    WORKSPACE
    / "results/harness_posttrain_campaign2_20260814/s32_prompt_ablate_exact8_r1"
)
ENDPOINT_ROOT = (
    WORKSPACE
    / "results/harness_posttrain_campaign2_20260814/s32_prompt_ablate_endpoint_w3"
)
MANIFEST = ROOT / "launch_manifest.json"
INVARIANCE = ROOT / "config_invariance.json"
ACTIVATION = ENDPOINT_ROOT / "activation.json"
EXECUTOR_STATE = ROOT / "executor"
EXECUTOR_LOG = ROOT / "executor_driver.log"
LAUNCH_RECEIPT = ROOT / "launch_activation.json"

SOURCE_GIT_SHA = "2dc96be428b7d4025b28a77237414fc4e2e6ed79"
HARNESS_SHA256 = "8d7d4ca7b48442faf5adae963260ed57873f74ef22e4a3a695f21b74f73043ec"
DELIBERATIVE_SHA256 = (
    "3cf6c38ad3f6077ecf16a87e79e03fb923e122e3404d98576603fc8b9f935c02"
)
MANIFEST_FILE_SHA256 = (
    "ed2b279117cd9f1ad2aa4b9dd990377124c537c58c03b64c88759f68dedc797c"
)
MANIFEST_BODY_SHA256 = (
    "a0e1e9917044fc3cdd103226c8f465ddf9f6de134ca4aac66ee9e20e75e94009"
)
INVARIANCE_FILE_SHA256 = (
    "d8ddcb21617f22e60f5899a230698f50887e1b394e9adbf04879d58358ffbd05"
)
RELEASE_BODY_SHA256 = (
    "46438be5e013bf5a8c98f1f0e4a30bfcdf4ce1333cc4cc191ec572a6639e6b79"
)
ALIAS = (
    "qwen35-browser-action-step32-rebind-bridge-cleanup-paired-"
    "a2c2fe90c23b-exact-lora"
)
PROMPT_ORDER = [3, 4, 5, 6, 0, 1, 2, 7]
PRIMARY_INDICES = [3, 4, 5, 6]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"not a JSON object: {path}")
    return value


def write_new(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def process_start_ticks(pid: int) -> str:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1]
    return fields.split()[19]


def port_free(port: int) -> bool:
    with socket.socket() as stream:
        return stream.connect_ex(("127.0.0.1", port)) != 0


def endpoint_models() -> set[str]:
    with urllib.request.urlopen("http://127.0.0.1:19320/health", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"endpoint health returned HTTP {response.status}")
    with urllib.request.urlopen("http://127.0.0.1:19320/v1/models", timeout=5) as response:
        value = json.load(response)
    return {
        item["id"]
        for item in value.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def git_output(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(PROMPT_SOURCE), *arguments], text=True
    ).strip()


def main() -> None:
    if LAUNCH_RECEIPT.exists() or EXECUTOR_LOG.exists() or EXECUTOR_STATE.exists():
        raise RuntimeError("Exact8 launch state already exists; refusing a duplicate launch")
    if sha(MANIFEST) != MANIFEST_FILE_SHA256:
        raise RuntimeError("Exact8 launch manifest file changed")
    if sha(INVARIANCE) != INVARIANCE_FILE_SHA256:
        raise RuntimeError("Exact8 invariance receipt changed")
    manifest = read(MANIFEST)
    invariance = read(INVARIANCE)
    activation = read(ACTIVATION)
    if manifest.get("launch_manifest_sha256") != MANIFEST_BODY_SHA256:
        raise RuntimeError("Exact8 launch manifest body binding changed")
    if invariance.get("launch_manifest_body_sha256") != MANIFEST_BODY_SHA256:
        raise RuntimeError("Exact8 invariance/manifest binding changed")
    if invariance.get("source_git_sha") != SOURCE_GIT_SHA:
        raise RuntimeError("prompt source receipt changed")
    if invariance.get("harness_sha256") != HARNESS_SHA256:
        raise RuntimeError("prompt harness attestation changed")
    if invariance.get("deliberative_file_sha256") != DELIBERATIVE_SHA256:
        raise RuntimeError("prompt file attestation changed")
    if invariance.get("launch_order_source_indices") != PROMPT_ORDER:
        raise RuntimeError("predeclared primary-first order changed")
    if invariance.get("predeclared_primary_indices") != PRIMARY_INDICES:
        raise RuntimeError("predeclared primary cohort changed")
    if git_output("rev-parse", "HEAD") != SOURCE_GIT_SHA:
        raise RuntimeError("prompt worktree HEAD changed")
    if git_output("status", "--short", "--untracked-files=no"):
        raise RuntimeError("prompt worktree has tracked changes")
    if sha(PROMPT_SOURCE / "agentarena/scaffolds/browseruse_deliberative.py") != DELIBERATIVE_SHA256:
        raise RuntimeError("prompt implementation file changed")
    if (
        activation.get("status") != "ready_for_development_probe"
        or activation.get("release_body_sha256") != RELEASE_BODY_SHA256
        or activation.get("alias") != ALIAS
        or activation.get("tunnel", {}).get("local_port") != 19320
    ):
        raise RuntimeError("endpoint activation binding changed")
    if ALIAS not in endpoint_models():
        raise RuntimeError("exact S32 adapter alias is not served")
    launches = manifest.get("launches")
    if not isinstance(launches, list) or len(launches) != 8:
        raise RuntimeError("Exact8 manifest shape changed")
    if [launch.get("port") for launch in launches] != [52703, 52704, 52705, 52706, 52700, 52701, 52702, 52707]:
        raise RuntimeError("Exact8 launch ports/order changed")
    if not all(port_free(int(launch["port"])) for launch in launches):
        raise RuntimeError("one or more Exact8 environment ports are occupied")
    if any(Path(str(launch["results"])).exists() for launch in launches):
        raise RuntimeError("one or more Exact8 result directories already exist")

    prior_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{PROMPT_SOURCE}:{EVALUATOR_SOURCE}"
    if prior_pythonpath:
        pythonpath += f":{prior_pythonpath}"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = pythonpath
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    import_probe = subprocess.check_output(
        [
            str(PYTHON),
            "-B",
            "-c",
            (
                "import json,agentarena,harness_posttrain_eval; "
                "import agentarena.scaffolds.browseruse_deliberative as d; "
                "print(json.dumps({'agentarena':agentarena.__file__,"
                "'deliberative':d.__file__,'evaluator':harness_posttrain_eval.__file__},"
                "sort_keys=True))"
            ),
        ],
        cwd=PROMPT_SOURCE,
        env=environment,
        text=True,
    ).strip()
    imports = json.loads(import_probe)
    if not str(imports["agentarena"]).startswith(str(PROMPT_SOURCE)):
        raise RuntimeError("agentarena did not resolve from prompt worktree")
    if not str(imports["deliberative"]).startswith(str(PROMPT_SOURCE)):
        raise RuntimeError("deliberative scaffold did not resolve from prompt worktree")
    if not str(imports["evaluator"]).startswith(str(EVALUATOR_SOURCE)):
        raise RuntimeError("evaluator did not resolve from its sealed source")

    command = [
        str(PYTHON),
        "-B",
        "-m",
        "harness_posttrain_eval.cli",
        "--config",
        str(CAMPAIGN),
        "run-bundle",
        "--launch-manifest",
        str(MANIFEST),
        "--state-dir",
        str(EXECUTOR_STATE),
        "--working-directory",
        str(PROMPT_SOURCE),
        "--jobs",
        "4",
        "--spawn-stagger-seconds",
        "0",
    ]
    log = EXECUTOR_LOG.open("xb")
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    receipt = {
        "schema": "c2-s32-prompt-ablate-exact8-launch-activation.v1",
        "status": "primary_first_full_exact8_launched",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "executor_pid": process.pid,
        "executor_process_start_ticks": process_start_ticks(process.pid),
        "executor_log": str(EXECUTOR_LOG),
        "executor_state": str(EXECUTOR_STATE),
        "command": command,
        "working_directory": str(PROMPT_SOURCE),
        "effective_pythonpath": pythonpath,
        "import_probe": imports,
        "source_git_sha": SOURCE_GIT_SHA,
        "harness_sha256": HARNESS_SHA256,
        "deliberative_file_sha256": DELIBERATIVE_SHA256,
        "launch_manifest": str(MANIFEST),
        "launch_manifest_file_sha256": MANIFEST_FILE_SHA256,
        "launch_manifest_body_sha256": MANIFEST_BODY_SHA256,
        "config_invariance": str(INVARIANCE),
        "config_invariance_file_sha256": INVARIANCE_FILE_SHA256,
        "release_body_sha256": RELEASE_BODY_SHA256,
        "alias": ALIAS,
        "jobs": 4,
        "launch_order_source_indices": PROMPT_ORDER,
        "predeclared_primary_indices": PRIMARY_INDICES,
        "predeclared_secondary_indices": [0, 1, 2, 7],
    }
    try:
        write_new(LAUNCH_RECEIPT, receipt)
    except BaseException:
        os.killpg(process.pid, 15)
        raise
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
