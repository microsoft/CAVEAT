from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from scripts import prepare_qualitative_mode_packets as qual


SPEC = json.loads(qual.DEFAULT_SPEC.read_text())
CODEBOOK = json.loads(qual.DEFAULT_CODEBOOK.read_text())
SCHEMA = json.loads(qual.DEFAULT_SCHEMA.read_text())


def _candidate(
    tmp_path: Path,
    *,
    env: str,
    model: str,
    scenario: str,
    variant: str,
    condition: str,
    repetition: str = "r1",
    stratum: str | None = None,
) -> qual.Candidate:
    directory = tmp_path / repetition / f"{env}__browseruse__{model}__{scenario}-{variant}__{condition}"
    path = directory / "trajectory.json"
    return qual.Candidate(
        trajectory_path=path,
        source_root=tmp_path,
        relative_path=path.relative_to(tmp_path).as_posix(),
        env=env,
        scaffold="browseruse",
        model=model,
        task_id=f"{scenario}-{variant}",
        condition=condition,
        scenario=scenario,
        variant=variant,
        repetition=repetition,
        stratum=stratum,
    )


def test_amazon_sampler_is_deterministic_exact_and_balanced(tmp_path: Path):
    candidates = []
    for stratum, models in SPEC["amazon"]["model_strata"].items():
        for model in models:
            for condition in SPEC["amazon"]["conditions"]:
                for variant in SPEC["amazon"]["variants"]:
                    for scenario in SPEC["amazon"]["scenarios"]:
                        for repetition in ("r1", "r2", "r3"):
                            candidates.append(
                                _candidate(
                                    tmp_path,
                                    env="amazon",
                                    model=model,
                                    scenario=scenario,
                                    variant=variant,
                                    condition=condition,
                                    repetition=repetition,
                                    stratum=stratum,
                                )
                            )
    selected = qual.select_amazon_holdout(candidates, SPEC)
    selected_reversed = qual.select_amazon_holdout(list(reversed(candidates)), SPEC)
    assert [row.stable_key for row in selected] == [row.stable_key for row in selected_reversed]
    assert len(selected) == len({row.stable_key for row in selected}) == 64
    assert Counter(row.condition for row in selected) == {"clean": 32, "combined": 32}
    assert Counter((row.condition, row.variant) for row in selected) == {
        (condition, variant): 8
        for condition in ("clean", "combined")
        for variant in ("mixed", "graded", "graded3", "graded4")
    }
    for condition in ("clean", "combined"):
        scenario_counts = Counter(row.scenario for row in selected if row.condition == condition)
        stratum_counts = Counter(row.stratum for row in selected if row.condition == condition)
        assert sorted(scenario_counts.values()) == [6, 6, 6, 7, 7]
        assert sorted(stratum_counts.values()) == [10, 11, 11]


def test_external_sampler_is_exact_balanced_and_distinct(tmp_path: Path):
    candidates = []
    models = ["m1", "m2", "m3", "m4", "m5"]
    variants = ["mixed", "graded", "graded3", "graded4"]
    for env in SPEC["external_transfer"]["environments"]:
        for condition in ("clean", "steered"):
            for model in models:
                for variant in variants:
                    for repetition in ("r1", "r2", "r3"):
                        candidates.append(
                            _candidate(
                                tmp_path,
                                env=env,
                                model=model,
                                scenario=f"{env}_task",
                                variant=variant,
                                condition=condition,
                                repetition=repetition,
                            )
                        )
    selected = qual.select_external_transfer(candidates, SPEC)
    assert len(selected) == 32
    for env in SPEC["external_transfer"]["environments"]:
        rows = [row for row in selected if row.env == env]
        assert len(rows) == 4
        assert Counter(row.condition for row in rows) == {"clean": 2, "steered": 2}
        assert len({row.model for row in rows}) == 4
        assert len({row.variant for row in rows}) == 4
        assert len({(row.model, row.variant) for row in rows}) == 4


def test_discovery_and_selection_do_not_open_trajectory_json(tmp_path: Path):
    models = SPEC["amazon"]["model_strata"]
    for stratum_models in models.values():
        for model in stratum_models:
            for condition in ("clean", "combined"):
                for variant in ("mixed", "graded", "graded3", "graded4"):
                    for scenario in ("laptop", "office_chair", "mattress", "backpack", "tent"):
                        directory = (
                            tmp_path
                            / "r1"
                            / f"amazon__browseruse__{model}__{scenario}-{variant}__{condition}"
                        )
                        directory.mkdir(parents=True)
                        # Invalid JSON proves discovery/selection only uses the path.
                        (directory / "trajectory.json").write_text("THIS IS NOT JSON")
    candidates = qual.discover_candidates(
        tmp_path,
        allowed_variants={"mixed", "graded", "graded3", "graded4"},
        allowed_conditions={"clean", "combined"},
        allowed_envs={"amazon"},
        model_strata=models,
    )
    assert len(qual.select_amazon_holdout(candidates, SPEC)) == 64


def test_coder_packet_omits_outcomes_and_top_level_identities():
    trajectory = {
        "instruction": "Buy the lightest qualifying item.",
        "answer": "I bought HERO-1",
        "evaluation": {"outcome": "compliant", "chosen": "HERO-1", "strict_binary": 1},
        "model": "model-secret",
        "condition": "combined",
        "steps": [
            {
                "index": 1,
                "action": "{'click': 1}",
                "reasoning": "I will inspect HERO-1.",
                "url": "http://shop/dp/HERO-1",
                "note": "visible action evidence",
                "has_image": False,
                "private_extra": "drop me",
            }
        ],
    }
    packet = qual._packet_from_trajectory("Qopaque", "amazon_holdout", trajectory)
    serialized = json.dumps(packet)
    for forbidden_key in ("evaluation", "answer", "model", "condition", "strict_binary"):
        assert forbidden_key not in packet
    for forbidden_value in ("model-secret", "combined", "compliant"):
        assert forbidden_value not in serialized
    # Product identities that naturally occur in the raw trace are retained,
    # rather than retrospectively rewriting evidence.
    assert packet["trace"][0]["reasoning"] == "I will inspect HERO-1."
    assert "private_extra" not in packet["trace"][0]


def _record(packet_id: str, role: str, *, disagreement: bool = False) -> dict:
    codes = []
    for index, definition in enumerate(CODEBOOK["codes"]):
        present = bool(disagreement and role == "skeptical" and index == 0)
        codes.append(
            {
                "code_id": definition["id"],
                "present": present,
                "episode": definition.get("episodes", ["run"])[0],
                "evidence_level": "behavior" if present else "justification_only",
                "step_indices": [1] if present else [],
                "excerpt": "grounded visible excerpt" if present else "",
                "interpretation": "present" if present else "not observed",
                "alternative_explanation": "none",
                "confidence": "high",
            }
        )
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "coder_role": role,
        "codes": codes,
        "candidate_new_codes": [],
        "negative_cases": ["A deviant case worth checking"] if role == "skeptical" else [],
        "memo": f"{role} pass",
    }


def test_schema_and_frozen_codebook_validation():
    valid = _record("Qopaque", "deductive")
    assert qual.validate_coding_record(valid, SCHEMA, CODEBOOK) == []
    invalid = json.loads(json.dumps(valid))
    invalid["unexpected"] = True
    invalid["codes"].pop()
    errors = qual.validate_coding_record(invalid, SCHEMA, CODEBOOK)
    assert any("unexpected property" in error for error in errors)
    assert any("missing frozen code decisions" in error for error in errors)


def test_merge_emits_adjudication_repeatability_and_seeded_review(tmp_path: Path):
    packets_dir = tmp_path / "packets"
    records_dir = tmp_path / "records"
    packets_dir.mkdir()
    records_dir.mkdir()
    packet_id = "Qopaque"
    (packets_dir / f"{packet_id}.json").write_text(
        json.dumps(
            {
                "packet_schema_version": 1,
                "packet_id": packet_id,
                "instruction": "Buy one item.",
                "trace": [{"index": 1, "action": "click", "reasoning": "because"}],
            }
        )
    )
    for role in qual.PASS_ROLES:
        (records_dir / f"{role}.json").write_text(
            json.dumps(_record(packet_id, role, disagreement=True))
        )
    output = tmp_path / "merged"
    result = qual.merge_coding_passes(records_dir, output, packets_dir=packets_dir)
    assert result["disagreements"] == 1
    assert result["agreement_review"] == math.ceil(0.20 * (len(CODEBOOK["codes"]) - 1))
    assert result["repeatability"]["krippendorff_alpha_nominal"] < 1.0
    assert "not human inter-rater reliability" in result["repeatability"]["label"]
    adjudication = json.loads((output / "adjudication_inputs" / f"{packet_id}.json").read_text())
    assert sum(item["requires_adjudication"] for item in adjudication["codes"]) == 1
    review = json.loads((output / "researcher_review_packet.json").read_text())
    assert len(review["all_disagreements"]) == 1
    assert review["all_explicit_negative_cases"]
    assert packet_id in review["blinded_packets"]


def test_nominal_alpha_perfect_and_nontrivial():
    assert qual._krippendorff_nominal_binary([[False, False, False], [True, True, True]]) == 1.0
    alpha = qual._krippendorff_nominal_binary([[False, False, True], [True, True, False]])
    assert alpha is not None and alpha < 1.0
