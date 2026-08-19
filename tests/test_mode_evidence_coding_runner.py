from __future__ import annotations

import json
from pathlib import Path

from scripts import prepare_qualitative_mode_packets as qual
from scripts import run_mode_evidence_coding as runner


SCHEMA = json.loads(runner.DEFAULT_SCHEMA.read_text())
CODEBOOK = json.loads(runner.DEFAULT_CODEBOOK.read_text())


def _packet() -> dict:
    return {
        "packet_schema_version": 1,
        "packet_id": "Qtest",
        "status": "outcome-blinded",
        "instruction": "Buy the best qualifying item.",
        "trace": [
            {
                "index": 7,
                "action": "{'click': {'index': 42}}",
                "reasoning": "The first item seems acceptable, but I should compare more.",
                "url": "http://shop/search?page=2",
                "note": "",
                "has_image": False,
            }
        ],
    }


def _record(*, present: bool = False, evidence: str = "justification_only") -> dict:
    codes = []
    for index, definition in enumerate(CODEBOOK["codes"]):
        selected = present and index == 0
        codes.append(
            {
                "code_id": definition["id"],
                "present": selected,
                "episode": definition.get("episodes", ["run"])[0] if selected else "run",
                "evidence_level": evidence if selected else "justification_only",
                "step_indices": [7] if selected else [],
                "excerpt": "The first item seems acceptable" if selected else "",
                "interpretation": "Possible early exposure effect." if selected else "Not evidenced.",
                "alternative_explanation": "The trace says comparison continues.",
                "confidence": "medium",
            }
        )
    return {
        "schema_version": 1,
        "packet_id": "Qtest",
        "coder_role": "deductive",
        "codes": codes,
        "candidate_new_codes": [],
        "negative_cases": [],
        "memo": "Blinded coding memo.",
    }


def test_wire_schema_preserves_local_constraints_but_uses_strict_subset():
    wire = runner._strict_wire_schema(SCHEMA)
    assert wire["properties"]["schema_version"] == {"const": 1, "type": "integer"}
    step_schema = wire["properties"]["codes"]["items"]["properties"]["step_indices"]
    assert "uniqueItems" not in step_schema
    assert set(wire["required"]) == set(wire["properties"])
    # The canonical local schema remains untouched and enforces uniqueness.
    assert SCHEMA["properties"]["codes"]["items"]["properties"]["step_indices"]["uniqueItems"]


def test_inductive_request_does_not_receive_codebook_but_other_passes_do():
    inductive = runner.build_model_messages(
        "inductive", _packet(), SCHEMA, CODEBOOK, "INDUCTIVE ROLE"
    )
    inductive_user = json.loads(inductive[1]["content"].split("\n\n", 1)[1])
    assert set(inductive_user) == {"coding_schema", "blinded_packet"}
    assert "shortlist_capture" in inductive[0]["content"]
    assert CODEBOOK["codes"][0]["definition"] not in inductive[0]["content"]

    deductive = runner.build_model_messages(
        "deductive", _packet(), SCHEMA, CODEBOOK, "DEDUCTIVE ROLE"
    )
    deductive_user = json.loads(deductive[1]["content"].split("\n\n", 1)[1])
    assert deductive_user["frozen_codebook"] == CODEBOOK


def test_request_clarifies_decoded_exact_excerpt_serialization():
    packet = _packet()
    packet["trace"][0]["reasoning"] = "literal \\n marker and actual\nline break"
    messages = runner.build_model_messages(
        "deductive", packet, SCHEMA, CODEBOOK, "DEDUCTIVE ROLE"
    )
    request = json.loads(messages[1]["content"].split("\n\n", 1)[1])
    system = messages[0]["content"]

    assert request["blinded_packet"]["trace"][0]["reasoning"] == packet["trace"][0]["reasoning"]
    assert r"backslash+n (`\n` as text)" in system
    assert r"(`\\n` in JSON source)" in system
    assert "exact-contiguous-substring rule is unchanged" in system

    record = _record(present=True)
    record["codes"][0]["excerpt"] = "literal \\n marker"
    serialized = json.dumps(record)
    assert r"literal \\n marker" in serialized
    parsed = runner.parse_record_response(serialized)
    assert parsed["codes"][0]["excerpt"] == "literal \\n marker"
    assert runner.grounding_errors(parsed, packet, CODEBOOK) == []


def test_grounding_requires_exact_excerpt_and_correct_evidence_channel():
    record = _record(present=True)
    assert runner.grounding_errors(record, _packet(), CODEBOOK) == []

    mislabeled = json.loads(json.dumps(record))
    mislabeled["codes"][0]["evidence_level"] = "behavior"
    errors = runner.grounding_errors(mislabeled, _packet(), CODEBOOK)
    assert any("occurs only in model reasoning" in error for error in errors)

    paraphrased = json.loads(json.dumps(record))
    paraphrased["codes"][0]["excerpt"] = "The item looked acceptable"
    errors = runner.grounding_errors(paraphrased, _packet(), CODEBOOK)
    assert any("not contiguous" in error for error in errors)


def test_run_episode_is_only_a_grounded_multi_episode_rollup():
    packet = _packet()
    packet["trace"].append(
        {
            "index": 8,
            "action": "{'click': {'index': 77}}",
            "reasoning": "At choice time I revisited the earlier discovery shortlist.",
            "url": "http://shop/cart",
            "note": "",
            "has_image": False,
        }
    )
    record = _record(present=True)
    decision = record["codes"][0]
    decision["episode"] = "run"
    decision["step_indices"] = [7]
    assert any(
        "grounded multi-step" in error
        for error in runner.grounding_errors(record, packet, CODEBOOK)
    )
    decision["step_indices"] = [7, 8]
    decision["interpretation"] = (
        "The pattern spans discovery of the shortlist and the later choice episode."
    )
    assert runner.grounding_errors(record, packet, CODEBOOK) == []


def test_normal_record_passes_canonical_and_grounding_validation():
    record = _record(present=True)
    errors = runner.validate_model_record(
        record,
        role="deductive",
        packet=_packet(),
        schema=SCHEMA,
        codebook=CODEBOOK,
    )
    assert errors == []
    wrong_role = dict(record, coder_role="skeptical")
    assert any("coder_role" in error for error in runner.validate_model_record(
        wrong_role,
        role="deductive",
        packet=_packet(),
        schema=SCHEMA,
        codebook=CODEBOOK,
    ))


def test_atomic_record_creation_is_create_only(tmp_path: Path):
    path = tmp_path / "deductive" / "Qtest.json"
    first = _record()
    second = dict(first, memo="must not replace")
    assert runner._atomic_create_json(path, first)
    assert not runner._atomic_create_json(path, second)
    assert json.loads(path.read_text())["memo"] == first["memo"]


def test_response_parser_accepts_fence_and_drops_trailing_prose():
    assert runner.parse_record_response('```json\n{"schema_version": 1}\n```') == {
        "schema_version": 1
    }
    assert runner.parse_record_response('{"schema_version": 1}\nextra') == {
        "schema_version": 1
    }


def test_retry_after_parses_trapi_message_without_consuming_a_coding_rule():
    error = RuntimeError("Token limit is exceeded. Try again in 26 seconds.")
    assert runner._retry_after_seconds(error) == 26.0


def _jobs(count: int) -> list[runner.Job]:
    return [
        runner.Job(
            role=runner.ROLES[index % len(runner.ROLES)],
            packet_id=f"Q{index:04d}",
            packet_path=Path(f"/packets/Q{index:04d}.json"),
            output_path=Path(f"/records/Q{index:04d}.json"),
        )
        for index in range(count)
    ]


def test_job_key_shards_are_stable_disjoint_and_complete():
    jobs = _jobs(97)
    partitions = [runner.partition_jobs(jobs, 7, index) for index in range(7)]
    keys_by_shard = [{job.key for job in partition} for partition in partitions]

    assert set().union(*keys_by_shard) == {job.key for job in jobs}
    assert sum(len(keys) for keys in keys_by_shard) == len(jobs)
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(keys_by_shard)
        for right in keys_by_shard[index + 1 :]
    )
    assert [job.key for job in runner.partition_jobs(list(reversed(jobs)), 7, 3)] == [
        job.key for job in reversed(partitions[3])
    ]
    assert all(
        runner.shard_index_for_job_key(job.key, 7) == shard_index
        for shard_index, partition in enumerate(partitions)
        for job in partition
    )


def test_shard_validation_rejects_invalid_count_and_index():
    for count, index in ((0, 0), (-1, 0), (2, -1), (2, 2)):
        try:
            runner.partition_jobs([], count, index)
        except runner.CodingRunnerError:
            pass
        else:
            raise AssertionError(f"accepted invalid shard selection {index}/{count}")


def test_run_cli_exposes_unsharded_defaults_and_explicit_shards():
    parser = runner._parser()
    defaults = parser.parse_args(["run"])
    assert (defaults.shard_count, defaults.shard_index) == (1, 0)

    selected = parser.parse_args(["run", "--shard-count", "9", "--shard-index", "4"])
    assert (selected.shard_count, selected.shard_index) == (9, 4)
