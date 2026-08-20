#!/usr/bin/env python3
"""Frozen post-hoc g0 specialization of the eight-cell GRPO laptop replay.

This module deliberately reuses the reviewed full-cohort evaluator implementation,
but seals every textual specialization and supplies g0-specific receipt/release
validators.  It is development-only and never selection eligible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import fixed_v7_grpo_fast_eval as _full
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)

if TYPE_CHECKING:
    from .fixed_v7_grpo_fast_eval import (
        ADAPTER_CONFIG,
        HEX64,
        IMAGE_DIGEST,
        PARENT_PATH,
        PARENT_TREE,
        ROLLOUT_SOURCE_GIT_SHA,
        SERVE_RELEASE_SCHEMA,
        _runtime_identity,
        _serve_release,
        _self_hash,
        main,
    )

BASE_SOURCE_SHA256 = "eaa3595f80eb66dad3efec0498b8316e97c837ba5b03d6e40ede3d983d0fcfaf"
G0_SOURCE = "3ba8fb01b042394eae1cf1b5af855c0a9ebc3b7d"
G0_TRAINER_SOURCE = "8857398f5b77153a246a3d072938096bcf221236"
G0_LABEL = "posthoc_same_task_laptop_graded_r00_single_group_grpo_exploratory"
G0_EVAL_LABEL = "posthoc_g0_same_task_laptop_steered_development_replay"
G0_RELEASE_FILE = "ca5dc5369e7763d39b1ff30c82eb6898218938c7cb5771945869e07ecfaa3e87"
G0_RELEASE_BODY = "ab9a47646a055981e02d83b204eb5793c44819e9d3eb22ffc1ae73f1a0a9066a"
STAMPED_MULTISET = "18b1c427a8e00c61790912c7bb497e471bfc49312b5a3203dcdf83dcf3b06885"
UNSTAMPED_MULTISET = "765754736fce6c8fde2393fcbfe5057cf84812f28e0ea514b8e6530585c0824f"
ACTUAL_RANKS = {
    "rank_0.bin": {
        "size": 174_214_651,
        "sha256": "15590ab5859337755d9e0f894b1aa2f9fe5312edcdac9a6ca52d6aa28840c651",
        "microbatches": 129,
        "padded_tokens": 2_788_720,
        "loss_mask_tokens": 110_081,
        "model_cost": 170_671_655_580_336_128,
    },
    "rank_1.bin": {
        "size": 173_770_565,
        "sha256": "e832f659599634192ef812c954a4e64486be9e8a286650b0ae04f0dd2ea374ca",
        "microbatches": 129,
        "padded_tokens": 2_781_640,
        "loss_mask_tokens": 120_631,
        "model_cost": 170_671_655_580_336_128,
    },
}
G0_ROOT = Path(
    "/data/caveat-27b/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_fixed_v7_caveat_shop_grpo_g0_exploratory_recovery/"
    f"{G0_TRAINER_SOURCE}/trainer_only_r1"
)
G0_RECEIPT = G0_ROOT / "caveat_shop_grpo_g0_exploratory_training_receipt_v2.json"
G0_ADAPTER = G0_ROOT / "weights/step_24/lora_adapters"


def _specialized_source() -> str:
    path = Path(_full.__file__).resolve()
    if sha256_file(path) != BASE_SOURCE_SHA256:
        raise IntegrityError("full-cohort evaluator source identity changed")
    source = path.read_text()
    replacements = (
        ("0e8e92057215fc93a1078d76a53b78e226617c4e", G0_SOURCE),
        (
            "2cfaadb29e5d9e8ab761b292d6b28d683204e482154282c53d26cdd288ad13e2",
            G0_RELEASE_FILE,
        ),
        (
            "8018eb64a9a903f4399c4e7d7b4a5398ba416b56530751134efb4641a74146b9",
            G0_RELEASE_BODY,
        ),
        (
            "caveat_shop_grpo_step24_trainer_resume_from_shipped_cohort_world8",
            "caveat_shop_grpo_g0_exploratory_step24_world4",
        ),
        ("full_frozen_cohort_grpo", "posthoc_single_group_g0_grpo_exploratory"),
        ("same_task_laptop_steered_development_replay", G0_EVAL_LABEL),
        ("same_task_laptop_r00_grpo_development_replay", G0_EVAL_LABEL),
        ("step24-caveat_shop-grpo-r00", "step24-caveat_shop-grpo-g0-exploratory"),
        (
            "browser_action_fixed_v7_caveat_shop_grpo_trainer_recovery",
            "browser_action_fixed_v7_caveat_shop_grpo_g0_exploratory_recovery",
        ),
        ("trainer_only_r7", "trainer_only_r1"),
        (
            "caveat_shop_grpo_training_receipt.json",
            "caveat_shop_grpo_g0_exploratory_training_receipt_v2.json",
        ),
        (
            "caveat-27b-v7-grpo-fast-serve-w2",
            "caveat-27b-v7-grpo-g0-fast-serve-w4",
        ),
        (
            "caveat-27b.browser-action-fixed-v7-grpo-fast-serve-release.v1",
            "caveat-27b.browser-action-fixed-v7-grpo-g0-fast-serve-release.v1",
        ),
        (
            "caveat-27b-fixed-v7-grpo-",
            "caveat-27b-fixed-v7-grpo-g0-",
        ),
        ("http://127.0.0.1:18510/v1", "http://127.0.0.1:18520/v1"),
        ('"18510:8000"', '"18520:8000"'),
        ("grpo_fast", "grpo_g0_fast"),
        ("GRPO fast", "GRPO g0 fast"),
    )
    for old, new in replacements:
        if old not in source:
            raise IntegrityError(f"specialization token absent: {old}")
        source = source.replace(old, new)
    source = source.replace(
        "browser_action_fixed_v7_caveat_shop_grpo_g0_exploratory_recovery/{SOURCE_GIT_SHA}/",
        "browser_action_fixed_v7_caveat_shop_grpo_g0_exploratory_recovery/{G0_TRAINER_SOURCE}/",
    )
    source = source.replace(
        'training_release.get("source", {}).get("git_sha") != SOURCE_GIT_SHA',
        'training_release.get("source", {}).get("git_sha") != G0_TRAINER_SOURCE',
    )
    source = source.replace(
        '"execution_source_git_sha": SOURCE_GIT_SHA,\n            "original_rollout_source_git_sha"',
        '"execution_source_git_sha": G0_TRAINER_SOURCE,\n'
        '            "receipt_recovery_source_git_sha": SOURCE_GIT_SHA,\n'
        '            "original_rollout_source_git_sha"',
        1,
    )
    source = source.replace(
        'value.get("candidate", {}).get("execution_source_git_sha") != SOURCE_GIT_SHA',
        'value.get("candidate", {}).get("execution_source_git_sha") != G0_TRAINER_SOURCE\n'
        '        or value.get("candidate", {}).get("receipt_recovery_source_git_sha") != SOURCE_GIT_SHA',
    )
    source = source.replace(
        '"training": {\n            "execution_source_git_sha": SOURCE_GIT_SHA,\n'
        '            "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,',
        '"training": {\n'
        '            "execution_source_git_sha": G0_TRAINER_SOURCE,\n'
        '            "receipt_recovery_source_git_sha": SOURCE_GIT_SHA,\n'
        '            "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,',
    )
    source = source.replace(
        'value.get("training", {}).get("execution_source_git_sha") != SOURCE_GIT_SHA',
        'value.get("training", {}).get("execution_source_git_sha") != G0_TRAINER_SOURCE\n'
        '        or value.get("training", {}).get("receipt_recovery_source_git_sha") != SOURCE_GIT_SHA',
    )
    source = source.replace(
        '"model_selection_eligible": False,\n        "candidate": {',
        '"model_selection_eligible": False,\n'
        '        "post_hoc_after_outcome_inspection": True,\n'
        '        "not_selection_eligible": True,\n'
        '        "development_eval_only": True,\n'
        '        "candidate": {',
    )
    source = source.replace(
        'or value.get("model_selection_eligible") is not False\n',
        'or value.get("model_selection_eligible") is not False\n'
        '        or value.get("post_hoc_after_outcome_inspection") is not True\n'
        '        or value.get("not_selection_eligible") is not True\n'
        '        or value.get("development_eval_only") is not True\n',
    )
    source = source.replace(
        '"actual_trainer_world_size": 8,', '"actual_trainer_world_size": 4,'
    )
    source = source.replace(
        '"selection_performed": False,\n        },\n        "runtime": {',
        '"selection_performed": True,\n            "selection_timing": "post_hoc_after_outcome_inspection",\n            "not_selection_eligible": True,\n            "development_eval_only": True,\n        },\n        "runtime": {',
    )
    source = source.replace(
        'get("selection_performed") is not False',
        'get("selection_performed") is not True',
    )
    source = source.replace(
        '"candidate_selection_eligible": False,',
        '"candidate_selection_eligible": False,\n'
        '        "post_hoc_after_outcome_inspection": True,\n'
        '        "development_eval_only": True,',
    )
    source = source.replace(
        '\nif __name__ == "__main__":\n    main()\n',
        "\n",
    )
    return source


exec(compile(_specialized_source(), __file__ + "<specialized>", "exec"), globals())
_generic_serve_release = _serve_release


def _training_receipt(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "receipt_body_sha256", "g0 exploratory training receipt")
    candidate = value.get("candidate") or {}
    disclosures = value.get("disclosures") or {}
    selected = value.get("selected_training_batch") or {}
    packed = value.get("packed_microbatches") or {}
    erratum = value.get("packed_microbatch_erratum") or {}
    stamps = {
        "run_id": "run_default",
        "run_step": 24,
        "exact_on_every_microbatch": True,
    }
    aggregate = {
        "microbatches": 258,
        "padded_tokens": 5_570_360,
        "loss_mask_tokens": 230_712,
        "model_cost": 341_343_311_160_672_256,
        "canonical_stamped_multiset_sha256": STAMPED_MULTISET,
        "transport_stamps_stripped_multiset_sha256": UNSTAMPED_MULTISET,
    }
    if (
        path.resolve() != G0_RECEIPT
        or value.get("schema")
        != "harness-distill.caveat_shop-grpo-g0-exploratory-training-receipt.v1"
        or value.get("status") != "ok"
        or value.get("scientific_label") != G0_LABEL
        or value.get("artifact_source_git_sha") != G0_SOURCE
        or value.get("execution_source_git_sha") != G0_SOURCE
        or value.get("original_rollout_source_git_sha") != ROLLOUT_SOURCE_GIT_SHA
        or value.get("source_step") != 23
        or value.get("final_step") != 24
        or value.get("optimizer_updates") != 1
        or value.get("learning_rate") != 5e-7
        or value.get("selection_performed") is not True
        or value.get("selection_timing") != "post_hoc_after_outcome_inspection"
        or value.get("not_selection_eligible") is not True
        or value.get("development_eval_only") is not True
        or value.get("new_rollouts_performed") is not False
        or value.get("source_on_policy_data") is not True
        or value.get("receipt_recovery_only") is not True
        or value.get("optimizer_rerun") is not False
        or value.get("optimizer_rerun_performed") is not False
        or value.get("training_artifacts_produced_by_original_trainer") is not True
        or value.get("original_trainer_source_git_sha") != G0_TRAINER_SOURCE
        or disclosures
        != {
            "selected_after_outcome_inspection": True,
            "single_variant": True,
            "not_preregistered_full_cohort": True,
            "not_selection_eligible": True,
            "development_eval_only": True,
        }
        or selected.get("samples") != 290
        or selected.get("token_ids") != 5_570_236
        or selected.get("loss_mask_tokens") != 230_712
        or selected.get("path")
        != str(G0_ROOT / "run_default/rollouts/step_24/train_rollouts.bin")
        or selected.get("size") != 169_846_624
        or selected.get("sha256")
        != "ef8eb63e8d14b22df869bc5553e7ad9527194bed5f8b69d947dbc746407ff091"
        or selected.get("sample_multiset_sha256")
        != "553b8b2791ef4c093cf500f86e07fec96568c8624e748276467826c9b95ce6cd"
        or selected.get("advantage_histogram")
        != {
            "-5.008173942565918": 29,
            "-2.031419515609741": 21,
            "-1.7322604656219482": 52,
            "-1.5616824626922607": 20,
            "-1.4616825580596924": 56,
            "-1.3116824626922607": 46,
            "14.568580627441406": 66,
        }
        or selected.get("step") != 24
        or selected.get("run_idx") is not None
        or packed.get("topology")
        != {
            "trainer_world_size": 4,
            "context_parallel_size": 2,
            "data_parallel_replicate": 2,
            "data_parallel_shard": 1,
            "data_parallel_size": 2,
        }
        or packed.get("erratum_evidence")
        != {
            "path": str(Path(str(value.get("erratum_path", ""))).resolve()),
            "size": 2413,
            "sha256": "274d371688e0e9ade1686bbe6aab675cde1e04eece41cf867baafbeacb6746bf",
        }
        or packed.get("rank_files")
        != {
            name: {
                "path": str(G0_ROOT / "rollouts/step_24" / name),
                **identity,
                "run_id": "run_default",
                "run_step": 24,
            }
            for name, identity in ACTUAL_RANKS.items()
        }
        or packed.get("aggregate") != aggregate
        or packed.get("transport_stamps") != stamps
        or packed.get("objective_content_exact") is not True
        or packed.get("optimizer_rerun") is not False
        or packed.get("exact_stamped_pack") is not True
        or erratum.get("reason")
        != "pinned PRIME SinglePacker stamps run_id and run_step after prepare_batch and before FileSystemMicroBatchSender serialization"
        or erratum.get("actual_stamped_output") != ACTUAL_RANKS
        or erratum.get("actual_aggregate") != aggregate
        or erratum.get("transport_stamps") != stamps
        or erratum.get("objective_content_exact") is not True
        or erratum.get("optimizer_rerun") is not False
        or erratum.get("erratum_body_sha256")
        != "2347c7bf341d1af54770f0b9ac90e8cb5c9c13258624b9e3898c3eb688096576"
        or erratum.get("original_unstamped_expectation")
        != {
            "canonical_multiset_sha256": UNSTAMPED_MULTISET,
            "rank_0.bin": {
                "size": 174_213_232,
                "sha256": "3c804885145186f2f5d6a38e2b218f7f96fcf3338025f762dffae9011c0353cb",
            },
            "rank_1.bin": {
                "size": 173_769_146,
                "sha256": "e8ff86dbfcbbe94b581e75862048033b2aa6b391c7b21fd894b02608fd685ab5",
            },
        }
        or candidate.get("name") != "step24-caveat_shop-grpo-g0-exploratory"
        or candidate.get("update") != 24
        or Path(str(candidate.get("path", ""))).resolve() != G0_ADAPTER
        or not all(type(candidate.get(key)) is int for key in ("files", "bytes"))
        or HEX64.fullmatch(str(candidate.get("tree_sha256", ""))) is None
    ):
        raise IntegrityError("g0 exploratory receipt semantics changed")
    return value


def _serve_release(path: Path) -> dict[str, Any]:
    value = _generic_serve_release(path)
    if (
        value.get("scientific_label") != G0_LABEL
        or value.get("evaluation_label") != G0_EVAL_LABEL
        or value.get("candidate_name") != "step24-caveat_shop-grpo-g0-exploratory"
        or value.get("source_git_sha") != G0_SOURCE
        or value.get("original_trainer_source_git_sha") != G0_TRAINER_SOURCE
        or value.get("selection_performed") is not True
        or value.get("selection_timing") != "post_hoc_after_outcome_inspection"
        or value.get("not_selection_eligible") is not True
        or value.get("development_eval_only") is not True
        or value.get("laptop_r01_training_data_used") is not False
        or value.get("office_chair_training_data_used") is not False
    ):
        raise IntegrityError("g0 serve release disclosure changed")
    return value


def render_serve_release(arguments: argparse.Namespace) -> None:
    receipt = _training_receipt(arguments.training_receipt)
    training_release = read_json(arguments.training_release.resolve())
    _self_hash(training_release, "release_sha256", "g0 training release")
    if (
        sha256_file(arguments.training_release.resolve()) != G0_RELEASE_FILE
        or training_release.get("release_sha256") != G0_RELEASE_BODY
    ):
        raise IntegrityError("g0 training release bytes changed")
    job, pod, _container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=False
    )
    candidate = receipt["candidate"]
    config = G0_ADAPTER / "adapter_config.json"
    if not config.is_file() or sha256_file(config) != ADAPTER_CONFIG:
        raise IntegrityError("g0 adapter config changed")
    alias = f"caveat-27b-fixed-v7-grpo-g0-3ba8fb0-step24-{candidate['tree_sha256'][:8]}"
    core = {
        "schema": SERVE_RELEASE_SCHEMA,
        "status": "released",
        "source_git_sha": G0_SOURCE,
        "original_trainer_source_git_sha": G0_TRAINER_SOURCE,
        "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,
        "candidate_name": candidate["name"],
        "candidate_update": 24,
        "scientific_label": G0_LABEL,
        "evaluation_label": G0_EVAL_LABEL,
        "selection_performed": True,
        "selection_timing": "post_hoc_after_outcome_inspection",
        "not_selection_eligible": True,
        "development_eval_only": True,
        "laptop_r01_training_data_used": False,
        "office_chair_training_data_used": False,
        "training_release_path": str(arguments.training_release.resolve()),
        "training_release_file_sha256": G0_RELEASE_FILE,
        "training_release_body_sha256": G0_RELEASE_BODY,
        "training_receipt_path": str(arguments.training_receipt.resolve()),
        "training_receipt_file_sha256": sha256_file(
            arguments.training_receipt.resolve()
        ),
        "training_receipt_body_sha256": receipt["receipt_body_sha256"],
        "parent_model_path": str(PARENT_PATH),
        "parent_component_tree_sha256": PARENT_TREE,
        "candidate_adapter_path": str(G0_ADAPTER),
        "candidate_component_files": candidate["files"],
        "candidate_component_bytes": candidate["bytes"],
        "candidate_component_tree_sha256": candidate["tree_sha256"],
        "adapter_config_sha256": ADAPTER_CONFIG,
        "served_model_name": alias,
        "dtype": "bfloat16",
        "max_model_len": 32768,
        "data_parallel_size": 4,
        "api_server_count": 4,
        "container_image_digest": IMAGE_DIGEST,
        "serve_job": job["metadata"]["name"],
        "serve_job_uid": job["metadata"]["uid"],
        "serve_job_spec_sha256": sha256_bytes(canonical_bytes(job["spec"])),
        "serve_job_labels_sha256": sha256_bytes(
            canonical_bytes(job["metadata"].get("labels", {}))
        ),
        "serve_pod": pod["metadata"]["name"],
        "serve_pod_uid": pod["metadata"]["uid"],
        "serve_node": pod.get("spec", {}).get("nodeName"),
    }
    release = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), release)
    print(
        json.dumps(
            {"status": "rendered", "sha256": release["release_sha256"], "alias": alias}
        )
    )


# The specialized functions resolve these globals at call time.
globals()["_training_receipt"] = _training_receipt
globals()["_serve_release"] = _serve_release
globals()["render_serve_release"] = render_serve_release


if __name__ == "__main__":
    main()
