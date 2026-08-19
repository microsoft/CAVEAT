"""Exact sealed-r4 replay for the campaign-2 action-weighted candidate.

This module changes only candidate lineage, reporting labels, and the
preregistered directional gate.  Config cloning, projection auditing,
launching, conversion, and scoring continue to execute through
``sol_dagger_laptop_eval`` so the model-facing evaluation harness remains the
exact prior r4 harness after its already-sealed narrow projection.
"""

from __future__ import annotations

import argparse

from . import sol_dagger_laptop_eval as exact_r4

PREREG_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-r1-laptop-preregistration.v1"
)
BUNDLE_SCHEMA = "harness-posttrain-eval.interactive-sol-dagger-r1-laptop-bundle.v1"
LAUNCH_SCHEMA = "harness-posttrain-eval.interactive-sol-dagger-r1-laptop-launch.v1"
MATRIX_SCHEMA = "harness-posttrain-eval.interactive-sol-dagger-r1-laptop-matrix.v1"
REPORT_SCHEMA = "harness-posttrain-eval.interactive-sol-dagger-r1-laptop-report.v1"
INVARIANCE_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-r1-r4-config-invariance.v1"
)
EXACT_LORA_SCHEMA = "harness-posttrain-eval.interactive-sol-dagger-r1-exact-lora.v1"

# The trainer resumes sealed step 25 and executes optimizer update 26.  The
# external run slug describes the campaign; the candidate name binds the exact
# receipt identity chosen by the trainer.
CANDIDATE_NAME = "step26-action-weighted-ce"
CANDIDATE_RESULT_KEY = "interactive_sol_dagger_r1"
CANDIDATE_RUN_SLUG = "interactive_sol_dagger_r1"
CANDIDATE_DISPLAY_NAME = "Interactive Sol DAgger action-weighted CE step26"
CANDIDATE_BINDING_KEY = "interactive_sol_dagger_r1_exact_lora_binding"
SCIENTIFIC_LABEL = (
    "same_task_laptop_steered_interactive_sol_dagger_r1_development_replay"
)
DIRECTIONAL_TARGET = {
    "strict_successes_at_least": 4,
    "hero_opened_at_least": 6,
    "hero_chosen_at_least": 6,
    "addon_present_at_most": 2,
    "valid_transaction_at_least": 8,
}

# These post-hoc trajectory requirements are intentionally separate from the
# five-field legacy directional-target object consumed by the sealed r4 core.
# They are mandatory promotion gates and will be audited after all eight cells
# are terminal: never BuyNow, enter cart in at least six cells, and issue a
# Delete action for every dirty-cart state encountered.
ACTION_PROMOTION_GATE = {
    "buy_now_actions_at_most": 0,
    "cart_entered_at_least": 6,
    "delete_for_every_dirty_cart": True,
}


def _configure() -> None:
    """Select campaign-2 metadata without changing any r4 harness operation."""

    # Import lazily so the sealed projection wrapper can be reviewed and
    # source-bound before the receipt-specific serving module is finalized.
    # Candidate execution still fails closed if that module is absent.
    from .interactive_sol_dagger_candidate_serve import (
        validate_endpoint,
        validate_lineage_attestation,
    )

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
        "The replay differs from sealed r4 only in the receipt-bound step26 "
        "action-weighted-CE direct-LoRA endpoint and non-model-facing "
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


# Direct aliases make it testable that campaign-2 did not fork or relax the
# sealed r4 projection contract.
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
