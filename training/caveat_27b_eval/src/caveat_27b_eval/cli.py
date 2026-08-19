from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .batch import load_and_run
from .common import IntegrityError, read_json, sha256_file
from .dp_canary import run_dp_canary
from .launcher import render_run_bundle
from .manifest import benchmark_lock, freeze_manifest, verify_manifest
from .matrix import (
    audit_matrix,
    diagnostic_matrix,
    final_matrix,
    shadow_gate_matrix,
    shadow_selection_matrix,
    write_matrix,
)
from .observations import convert_results
from .prepare_final import (
    BASE_DEPLOYMENT,
    TRAINED_DEPLOYMENT,
    prepare_final_bundle,
)
from .refill import prepare_refill
from .report import write_report
from .split import audit_split, generate_to_path
from .statistics import analyze_final, load_observations

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs/campaign.json"


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="caveat-27b-eval")
    root.add_argument("--config", type=_path, default=DEFAULT_CONFIG)
    subcommands = root.add_subparsers(dest="command", required=True)

    split = subcommands.add_parser("generate-split")
    split.add_argument("--output", type=_path, required=True)

    split_audit = subcommands.add_parser("audit-split")
    split_audit.add_argument("--split", type=_path, required=True)
    split_audit.add_argument("--require-materialized", action="store_true")
    split_audit.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)

    matrix = subcommands.add_parser("build-matrix")
    matrix.add_argument(
        "--kind", choices=("diagnostic", "shadow-selection", "shadow-gate", "final"), required=True
    )
    matrix.add_argument("--split", type=_path)
    matrix.add_argument("--candidate-arms", default="balanced,recovery-heavy,protocol-heavy")
    matrix.add_argument("--selected-arm", default="trained")
    matrix.add_argument("--include-sft-parent", action="store_true")
    matrix.add_argument("--scenario-scope", choices=("all", "development", "heldout"), default="all")
    matrix.add_argument("--output", type=_path, required=True)

    matrix_audit = subcommands.add_parser("audit-matrix")
    matrix_audit.add_argument("--matrix", type=_path, required=True)

    render = subcommands.add_parser("render-run-bundle")
    render.add_argument("--matrix", type=_path, required=True)
    render.add_argument("--model-specs", type=_path, required=True)
    render.add_argument("--frozen-manifest", type=_path, required=True)
    render.add_argument("--endpoint-manifest", type=_path)
    render.add_argument("--output-dir", type=_path, required=True)
    render.add_argument("--results-root", type=_path, required=True)
    render.add_argument("--base-port", type=int, required=True)
    render.add_argument("--python-executable", default=sys.executable)

    execute = subcommands.add_parser("run-bundle")
    execute.add_argument("--launch-manifest", type=_path, required=True)
    execute.add_argument("--state-dir", type=_path, required=True)
    execute.add_argument("--working-directory", type=_path, default=REPOSITORY_ROOT)
    execute.add_argument("--jobs", type=int, default=32)
    execute.add_argument("--spawn-stagger-seconds", type=float, default=0.0)

    refill = subcommands.add_parser("prepare-refill")
    refill.add_argument("--launch-manifest", type=_path, required=True)
    refill.add_argument("--executor-state-dir", type=_path, required=True)
    refill.add_argument("--archive-root", type=_path, required=True)
    refill.add_argument("--retry-manifest", type=_path, required=True)
    refill.add_argument("--arm", action="append", required=True)
    refill.add_argument("--run-id", action="append")

    convert = subcommands.add_parser("convert-results")
    convert.add_argument("--launch-manifest", type=_path, required=True)
    convert.add_argument("--frozen-manifest", type=_path, required=True)
    convert.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)
    convert.add_argument("--observations-output", type=_path, required=True)
    convert.add_argument("--audit-output", type=_path, required=True)

    canary = subcommands.add_parser("dp-canary")
    canary.add_argument("--base-url", required=True)
    canary.add_argument("--model", required=True)
    canary.add_argument("--output", type=_path, required=True)
    canary.add_argument("--api-key-environment", default="CAVEAT_27B_API_KEY")
    canary.add_argument("--concurrency", type=int, default=16)
    canary.add_argument("--max-tokens", type=int, default=4096)
    canary.add_argument("--request-timeout-seconds", type=float, default=900.0)
    canary.add_argument("--observation-timeout-seconds", type=float, default=120.0)

    prepare = subcommands.add_parser("prepare-final")
    prepare.add_argument(
        "--split", type=_path, default=PACKAGE_ROOT / "manifests/shadow_split.json"
    )
    prepare.add_argument(
        "--matrix", type=_path, default=PACKAGE_ROOT / "manifests/final_matrix.json"
    )
    prepare.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)
    prepare.add_argument("--output-dir", type=_path, required=True)
    prepare.add_argument("--results-root", type=_path, required=True)
    prepare.add_argument("--storefront-base-port", type=int, required=True)
    prepare.add_argument("--python-executable", default=sys.executable)
    prepare.add_argument("--base-weight-sha256", required=True)
    prepare.add_argument("--trained-weight-sha256", required=True)
    prepare.add_argument("--tokenizer-sha256", required=True)
    prepare.add_argument("--chat-template-sha256", required=True)
    prepare.add_argument("--container-image-digest", required=True)
    prepare.add_argument("--serving-stack-json", type=_path, required=True)
    prepare.add_argument("--base-name", default="qwen35-27b-base-local")
    prepare.add_argument("--trained-name", default="caveat-27b-local")
    prepare.add_argument("--base-url", required=True)
    prepare.add_argument("--trained-url", required=True)
    prepare.add_argument("--base-deployment", default=BASE_DEPLOYMENT)
    prepare.add_argument("--trained-deployment", default=TRAINED_DEPLOYMENT)
    prepare.add_argument("--base-server-record", type=_path, required=True)
    prepare.add_argument("--trained-server-record", type=_path, required=True)
    prepare.add_argument("--base-tunnel-record", type=_path, required=True)
    prepare.add_argument("--trained-tunnel-record", type=_path, required=True)
    prepare.add_argument(
        "--api-key-environment", default="CAVEAT_27B_API_KEY"
    )
    prepare.add_argument("--probe-timeout-seconds", type=float, default=30.0)

    freeze = subcommands.add_parser("freeze-manifest")
    freeze.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)
    freeze.add_argument("--split", type=_path, required=True)
    freeze.add_argument("--output", type=_path, required=True)
    freeze.add_argument("--base-weight-sha256", required=True)
    freeze.add_argument("--trained-weight-sha256", required=True)
    freeze.add_argument("--tokenizer-sha256", required=True)
    freeze.add_argument("--chat-template-sha256", required=True)
    freeze.add_argument("--container-image-digest", required=True)
    freeze.add_argument("--serving-stack-json", type=_path, required=True)

    verify = subcommands.add_parser("verify-manifest")
    verify.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)
    verify.add_argument("--manifest", type=_path, required=True)

    analyze = subcommands.add_parser("analyze-final")
    analyze.add_argument("--matrix", type=_path, required=True)
    analyze.add_argument("--observations", type=_path, required=True)
    analyze.add_argument("--frozen-manifest", type=_path, required=True)
    analyze.add_argument("--launch-manifest", type=_path, required=True)
    analyze.add_argument("--conversion-audit", type=_path, required=True)
    analyze.add_argument("--repo-root", type=_path, default=REPOSITORY_ROOT)
    analyze.add_argument("--selected-arm", default="trained")
    analyze.add_argument("--json-output", type=_path, required=True)
    analyze.add_argument("--markdown-output", type=_path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    config = read_json(arguments.config)
    try:
        if arguments.command == "generate-split":
            split = generate_to_path(arguments.config, arguments.output)
            print(json.dumps({"tasks": len(split["tasks"]), "sha256": split["split_sha256"]}))
        elif arguments.command == "audit-split":
            print(
                json.dumps(
                    audit_split(
                        read_json(arguments.split),
                        config,
                        require_materialized=arguments.require_materialized,
                        original_benchmark_lock=(
                            benchmark_lock(arguments.repo_root)
                            if arguments.require_materialized
                            else None
                        ),
                    ),
                    sort_keys=True,
                )
            )
        elif arguments.command == "build-matrix":
            if arguments.kind == "diagnostic":
                result = diagnostic_matrix(config)
            elif arguments.kind == "shadow-selection":
                if arguments.split is None:
                    raise IntegrityError("shadow-selection requires --split")
                result = shadow_selection_matrix(
                    config,
                    read_json(arguments.split),
                    candidate_arms=[value for value in arguments.candidate_arms.split(",") if value],
                )
            elif arguments.kind == "shadow-gate":
                if arguments.split is None:
                    raise IntegrityError("shadow-gate requires --split")
                result = shadow_gate_matrix(
                    config, read_json(arguments.split), selected_arm=arguments.selected_arm
                )
            else:
                result = final_matrix(
                    config,
                    selected_arm=arguments.selected_arm,
                    include_sft_parent=arguments.include_sft_parent,
                    scenario_scope=arguments.scenario_scope,
                )
            write_matrix(arguments.output, result)
            print(json.dumps(audit_matrix(result), sort_keys=True))
        elif arguments.command == "audit-matrix":
            print(json.dumps(audit_matrix(read_json(arguments.matrix)), sort_keys=True))
        elif arguments.command == "render-run-bundle":
            result = render_run_bundle(
                matrix=read_json(arguments.matrix),
                config=config,
                frozen_manifest=read_json(arguments.frozen_manifest),
                endpoint_manifest=(
                    read_json(arguments.endpoint_manifest)
                    if arguments.endpoint_manifest is not None
                    else None
                ),
                model_specs=read_json(arguments.model_specs),
                output_dir=arguments.output_dir,
                results_root=arguments.results_root,
                base_port=arguments.base_port,
                python_executable=arguments.python_executable,
            )
            print(
                json.dumps(
                    {
                        "runs": len(result["launches"]),
                        "sha256": result["launch_manifest_sha256"],
                    }
                )
            )
        elif arguments.command == "run-bundle":
            result = load_and_run(
                launch_manifest=arguments.launch_manifest,
                state_dir=arguments.state_dir,
                working_directory=arguments.working_directory,
                jobs=arguments.jobs,
                spawn_stagger_seconds=arguments.spawn_stagger_seconds,
            )
            print(json.dumps({"success": result["success"], "counts": result["counts"]}))
            if not result["success"]:
                return 2
        elif arguments.command == "prepare-refill":
            result = prepare_refill(
                launch_manifest_path=arguments.launch_manifest,
                executor_state_dir=arguments.executor_state_dir,
                archive_root=arguments.archive_root,
                retry_manifest_path=arguments.retry_manifest,
                arms=arguments.arm,
                run_ids=arguments.run_id,
            )
            print(
                json.dumps(
                    {
                        "archived": len(result["archived"]),
                        "preserved": len(result["complete_results_preserved"]),
                        "receipt_sha256": result["receipt_sha256"],
                        "retry_manifest_sha256": result["retry_manifest_sha256"],
                        "runs_to_retry": len(result["runs_to_retry"]),
                    },
                    sort_keys=True,
                )
            )
        elif arguments.command == "convert-results":
            result = convert_results(
                launch_manifest=read_json(arguments.launch_manifest),
                frozen_manifest=read_json(arguments.frozen_manifest),
                repository_root=arguments.repo_root,
                observations_output=arguments.observations_output,
                audit_output=arguments.audit_output,
            )
            print(
                json.dumps(
                    {
                        "complete": result["complete"],
                        "observations": result["observation_rows"],
                        "infrastructure_invalid": result[
                            "infrastructure_invalid_runs"
                        ],
                    }
                )
            )
            if not result["complete"]:
                return 2
        elif arguments.command == "dp-canary":
            api_key = os.environ.get(arguments.api_key_environment)
            if not api_key:
                raise IntegrityError(
                    f"API key environment is absent: {arguments.api_key_environment}"
                )
            result = run_dp_canary(
                base_url=arguments.base_url,
                model=arguments.model,
                api_key=api_key,
                output_path=arguments.output,
                concurrency=arguments.concurrency,
                max_tokens=arguments.max_tokens,
                request_timeout_seconds=arguments.request_timeout_seconds,
                observation_timeout_seconds=arguments.observation_timeout_seconds,
            )
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "all_engines_observed_running": result[
                            "all_engines_observed_running"
                        ],
                        "receipt_sha256": result["receipt_sha256"],
                    },
                    sort_keys=True,
                )
            )
        elif arguments.command == "prepare-final":
            result = prepare_final_bundle(
                repository_root=arguments.repo_root,
                config_path=arguments.config,
                split_path=arguments.split,
                matrix_path=arguments.matrix,
                output_dir=arguments.output_dir,
                results_root=arguments.results_root,
                storefront_base_port=arguments.storefront_base_port,
                python_executable=arguments.python_executable,
                base_weight_sha256=arguments.base_weight_sha256,
                trained_weight_sha256=arguments.trained_weight_sha256,
                tokenizer_sha256=arguments.tokenizer_sha256,
                chat_template_sha256=arguments.chat_template_sha256,
                container_image_digest=arguments.container_image_digest,
                serving_stack=read_json(arguments.serving_stack_json),
                base_name=arguments.base_name,
                trained_name=arguments.trained_name,
                base_url=arguments.base_url,
                trained_url=arguments.trained_url,
                base_deployment=arguments.base_deployment,
                trained_deployment=arguments.trained_deployment,
                base_server_record_path=arguments.base_server_record,
                trained_server_record_path=arguments.trained_server_record,
                base_tunnel_record_path=arguments.base_tunnel_record,
                trained_tunnel_record_path=arguments.trained_tunnel_record,
                api_key_environment=arguments.api_key_environment,
                probe_timeout_seconds=arguments.probe_timeout_seconds,
            )
            print(json.dumps(result, sort_keys=True))
        elif arguments.command == "freeze-manifest":
            result = freeze_manifest(
                root=arguments.repo_root,
                config_path=arguments.config,
                split_path=arguments.split,
                output_path=arguments.output,
                base_weight_sha256=arguments.base_weight_sha256,
                trained_weight_sha256=arguments.trained_weight_sha256,
                tokenizer_sha256=arguments.tokenizer_sha256,
                chat_template_sha256=arguments.chat_template_sha256,
                container_image_digest=arguments.container_image_digest,
                serving_stack=read_json(arguments.serving_stack_json),
            )
            print(result["manifest_sha256"])
        elif arguments.command == "verify-manifest":
            verify_manifest(read_json(arguments.manifest), root=arguments.repo_root)
            print("valid")
        elif arguments.command == "analyze-final":
            frozen_manifest = read_json(arguments.frozen_manifest)
            verify_manifest(frozen_manifest, root=arguments.repo_root)
            result = analyze_final(
                read_json(arguments.matrix),
                load_observations(arguments.observations),
                config,
                selected_arm=arguments.selected_arm,
                frozen_manifest=frozen_manifest,
                launch_manifest=read_json(arguments.launch_manifest),
                conversion_audit=read_json(arguments.conversion_audit),
                observations_sha256=sha256_file(arguments.observations),
            )
            write_report(arguments.json_output, arguments.markdown_output, result)
            print(json.dumps({"success": result["success"], "gates": result["gates"]}))
        else:  # pragma: no cover
            raise AssertionError(arguments.command)
    except IntegrityError as exc:
        raise SystemExit(f"integrity error: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
