"""Exact sealed-r4 eight-cell replay for the focused step-26 candidate.

This module supplies only candidate lineage and reporting labels.  All config
cloning, projection checks, launching, conversion, and scoring execute through
``sol_dagger_laptop_eval`` so the model-facing harness remains the exact prior
r4 harness after the same narrow endpoint/output/nonce/port projection.
"""

from __future__ import annotations

import argparse

from . import sol_dagger_laptop_eval as exact_r4
from .sol_dagger_step26_candidate_serve import (
    validate_endpoint,
    validate_lineage_attestation,
)

PREREG_SCHEMA = (
    "caveat-27b-eval.sol-dagger-cleanup-step26-laptop-preregistration.v1"
)
BUNDLE_SCHEMA = "caveat-27b-eval.sol-dagger-cleanup-step26-laptop-bundle.v1"
LAUNCH_SCHEMA = "caveat-27b-eval.sol-dagger-cleanup-step26-laptop-launch.v1"
MATRIX_SCHEMA = "caveat-27b-eval.sol-dagger-cleanup-step26-laptop-matrix.v1"
REPORT_SCHEMA = "caveat-27b-eval.sol-dagger-cleanup-step26-laptop-report.v1"
INVARIANCE_SCHEMA = (
    "caveat-27b-eval.sol-dagger-cleanup-step26-r4-config-invariance.v1"
)
EXACT_LORA_SCHEMA = "caveat-27b-eval.sol-dagger-cleanup-step26-exact-lora.v1"
CANDIDATE_NAME = "step26-sol-dagger-cleanup-sft"
CANDIDATE_RESULT_KEY = "sol_dagger_cleanup_step26"
CANDIDATE_RUN_SLUG = "sol_dagger_cleanup_step26"
CANDIDATE_DISPLAY_NAME = "Sol-DAgger cleanup step26"
CANDIDATE_BINDING_KEY = "sol_dagger_cleanup_step26_exact_lora_binding"
SCIENTIFIC_LABEL = (
    "same_task_laptop_steered_sol_dagger_cleanup_step26_development_replay"
)
DIRECTIONAL_TARGET = {
    "strict_successes_at_least": 3,
    "hero_opened_at_least": 4,
    "hero_chosen_at_least": 4,
    "addon_present_at_most": 2,
    "valid_transaction_at_least": 7,
}


def _configure() -> None:
    """Select step-26 metadata without changing any r4 harness operation."""

    exact_r4.PREREG_SCHEMA = PREREG_SCHEMA
    exact_r4.BUNDLE_SCHEMA = BUNDLE_SCHEMA
    exact_r4.LAUNCH_SCHEMA = LAUNCH_SCHEMA
    exact_r4.MATRIX_SCHEMA = MATRIX_SCHEMA
    exact_r4.REPORT_SCHEMA = REPORT_SCHEMA
    exact_r4.INVARIANCE_SCHEMA = INVARIANCE_SCHEMA
    exact_r4.EXACT_LORA_SCHEMA = EXACT_LORA_SCHEMA
    exact_r4.CANDIDATE_NAME = CANDIDATE_NAME
    exact_r4.CANDIDATE_RESULT_KEY = CANDIDATE_RESULT_KEY
    exact_r4.CANDIDATE_RUN_SLUG = CANDIDATE_RUN_SLUG
    exact_r4.CANDIDATE_DISPLAY_NAME = CANDIDATE_DISPLAY_NAME
    exact_r4.CANDIDATE_BINDING_KEY = CANDIDATE_BINDING_KEY
    exact_r4.SCIENTIFIC_LABEL = SCIENTIFIC_LABEL
    exact_r4.SOURCE_STEP = 25
    exact_r4.FINAL_STEP = 26
    exact_r4.TREATMENT_DESCRIPTION = (
        "The replay differs from sealed r4 only in the receipt-bound focused "
        "Sol-DAgger cleanup step26 direct-LoRA endpoint and non-model-facing "
        "output/nonce/port provenance."
    )
    exact_r4.DIRECTIONAL_TARGET = DIRECTIONAL_TARGET
    exact_r4.validate_endpoint = validate_endpoint
    exact_r4.validate_lineage_attestation = validate_lineage_attestation


def preregister(arguments: argparse.Namespace) -> None:
    _configure()
    exact_r4.preregister(arguments)


def audit_preregistration(path):  # type: ignore[no-untyped-def]
    _configure()
    return exact_r4.audit_preregistration(path)


def render(arguments: argparse.Namespace) -> None:
    _configure()
    exact_r4.render(arguments)


def finalize(arguments: argparse.Namespace) -> None:
    _configure()
    exact_r4.finalize(arguments)


def audit_command(arguments: argparse.Namespace) -> None:
    _configure()
    exact_r4.audit_command(arguments)


# These are direct aliases to the sealed implementation and constants.  They
# make the byte-identical projection property easy to unit-test independently
# of a live step-26 receipt.
assert_r4_config_invariance = exact_r4.assert_r4_config_invariance
_harness_projection = exact_r4._harness_projection
_source_inputs = exact_r4._source_inputs
_source_rows = exact_r4._source_rows
EXPECTED_CELLS = exact_r4.EXPECTED_CELLS
ALLOWED_CONFIG_DIFFERENCES = exact_r4.ALLOWED_CONFIG_DIFFERENCES
VARIANTS = exact_r4.VARIANTS
REPETITIONS = exact_r4.REPETITIONS


def main() -> None:
    _configure()
    exact_r4.main()


if __name__ == "__main__":
    main()
