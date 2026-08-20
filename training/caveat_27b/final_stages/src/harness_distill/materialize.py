"""Materialize browser-use proxy turns into audited decision-replay rows.

One row is one causal model decision.  Its student request is the exact
provider-visible ``effective_request`` sampled during rollout, with only
model/sampling fields removed (PRIME owns those).  Both the original
browser-use request and effective request are hash-bound into the replay.  Its
``privileged_context`` is a separately audited shadow phase state from the same
or an earlier turn.  The two are never merged here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .shadow import (
    ClaimDerivation,
    ClaimStatus,
    EvidenceRef,
    PrivilegeAuditError,
    PublicEvidence,
    PublicEvidenceSource,
    ShadowPhase,
    ShadowPhaseStateBuilder,
    TeacherPrivilege,
)

MATERIALIZED_SCHEMA = "harness-distill.decision-replay.v1"
PRIVILEGE_SIDECAR_SCHEMA = "harness-distill.teacher-privilege.v1"
PROXY_TRACE_SCHEMA = "harness-distill.proxy-trace.v1"

# The interception server imposes the campaign's model and SamplingConfig.  A
# replay program must not smuggle the original run's decoding configuration
# around that authority.  Everything else, including messages, existing tool
# schemas/tool_choice, response format, and provider metadata, is retained.
PRIME_CONTROLLED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "typical_p",
        "presence_penalty",
        "frequency_penalty",
        "repetition_penalty",
        "max_tokens",
        "max_completion_tokens",
        "min_tokens",
        "seed",
        "n",
        "best_of",
        "stop",
        "stream",
        "stream_options",
        "logprobs",
        "top_logprobs",
        "logit_bias",
        "reasoning",
        "reasoning_effort",
        "extra_body",
    }
)


class MaterializationError(ValueError):
    """A proxy turn and its privilege sidecar cannot form a safe replay row."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_content(content: Any, path: str) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise MaterializationError(f"{path} must be text or supported content parts")
    for index, part in enumerate(content):
        item_path = f"{path}[{index}]"
        if not isinstance(part, Mapping):
            raise MaterializationError(f"{item_path} must be an object")
        if part.get("type") == "text":
            if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                raise MaterializationError(f"{item_path} is not a canonical text part")
        elif part.get("type") == "image_url":
            image = part.get("image_url")
            if (
                set(part) != {"type", "image_url"}
                or not isinstance(image, Mapping)
                or set(image) != {"url"}
                or not isinstance(image["url"], str)
            ):
                raise MaterializationError(f"{item_path} is not a canonical image_url part")
        else:
            raise MaterializationError(f"{item_path} has an unsupported content-part type")


def _validate_replayable_message(message: Mapping[str, Any], index: int) -> None:
    """Fail if Verifiers' pinned chat renderer would normalize this message.

    Raw requests are stored for the one-call program, but PRIME training parses
    and renders them.  Restricting records to the pinned dialect's lossless
    subset makes "exact baseline messages" true on both paths.
    """

    path = f"messages[{index}]"
    role = message.get("role")
    if role in {"system", "user"}:
        unknown = set(message) - {"role", "content"}
        if unknown:
            raise MaterializationError(f"{path} has non-round-trippable fields: {sorted(unknown)}")
        _validate_content(message.get("content"), f"{path}.content")
        return
    if role == "tool":
        unknown = set(message) - {"role", "tool_call_id", "content", "name"}
        if unknown:
            raise MaterializationError(f"{path} has non-round-trippable fields: {sorted(unknown)}")
        if not isinstance(message.get("tool_call_id"), str):
            raise MaterializationError(f"{path}.tool_call_id must be text")
        if "name" in message and (not isinstance(message["name"], str) or not message["name"]):
            raise MaterializationError(f"{path}.name must be non-empty text")
        _validate_content(message.get("content"), f"{path}.content")
        return
    if role == "assistant":
        unknown = set(message) - {"role", "content", "reasoning_content", "tool_calls"}
        if unknown:
            raise MaterializationError(f"{path} has non-round-trippable fields: {sorted(unknown)}")
        if message.get("content") is not None and (
            not isinstance(message.get("content"), str) or not message["content"]
        ):
            raise MaterializationError(f"{path}.content must be text or null")
        if "reasoning_content" in message and (
            not isinstance(message["reasoning_content"], str) or not message["reasoning_content"]
        ):
            raise MaterializationError(f"{path}.reasoning_content must be non-empty text")
        calls = message.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or not calls:
                raise MaterializationError(f"{path}.tool_calls must be a non-empty list")
            for call_index, call in enumerate(calls):
                call_path = f"{path}.tool_calls[{call_index}]"
                if not isinstance(call, Mapping) or set(call) != {"id", "type", "function"}:
                    raise MaterializationError(f"{call_path} is not a canonical function call")
                function = call.get("function")
                if (
                    call.get("type") != "function"
                    or not isinstance(call.get("id"), str)
                    or not isinstance(function, Mapping)
                    or set(function) != {"name", "arguments"}
                    or not isinstance(function.get("name"), str)
                    or not isinstance(function.get("arguments"), str)
                ):
                    raise MaterializationError(f"{call_path} is not a canonical function call")
        return
    raise MaterializationError(f"{path} has unsupported role {role!r}")


def infer_turn_index(messages: Sequence[Mapping[str, Any]]) -> int:
    """Infer the impending model-turn index from preceding assistant messages."""

    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise MaterializationError("request messages must be a sequence")
    count = 0
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise MaterializationError(f"messages[{index}] must be an object")
        _validate_replayable_message(message, index)
        role = message.get("role")
        if role == "assistant":
            count += 1
    return count


def replay_request(original_request: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a baseline request while removing only PRIME-owned fields."""

    if not isinstance(original_request, Mapping):
        raise MaterializationError("proxy request must be an object")
    try:
        copied = json.loads(json.dumps(original_request, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise MaterializationError("proxy request must contain finite JSON data") from exc
    messages = copied.get("messages")
    if not isinstance(messages, list) or not messages:
        raise MaterializationError("proxy request requires a non-empty messages list")
    infer_turn_index(messages)
    if "privileged_context" in copied:
        raise MaterializationError("student request must not contain privileged_context")
    return {
        key: value for key, value in copied.items() if key not in PRIME_CONTROLLED_REQUEST_FIELDS
    }


def _public_evidence_from_dict(value: Mapping[str, Any]) -> PublicEvidence:
    required = {"evidence_id", "source", "turn_index", "payload"}
    missing = sorted(required - set(value))
    if missing:
        raise MaterializationError(f"privilege evidence is missing fields: {missing}")
    try:
        return PublicEvidence(
            evidence_id=value["evidence_id"],
            source=PublicEvidenceSource(value["source"]),
            turn_index=value["turn_index"],
            payload=value["payload"],
            page_url=value.get("page_url"),
        )
    except (TypeError, ValueError, PrivilegeAuditError) as exc:
        raise MaterializationError(f"invalid public evidence: {exc}") from exc


def _evidence_ref_from_dict(value: Mapping[str, Any]) -> EvidenceRef:
    try:
        return EvidenceRef(
            evidence_id=value["evidence_id"],
            path=tuple(value.get("path", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterializationError(f"invalid evidence reference: {exc}") from exc


def rebuild_teacher_privilege(payload: Mapping[str, Any]) -> TeacherPrivilege:
    """Re-run the provenance audit instead of trusting serialized audit booleans."""

    if not isinstance(payload, Mapping):
        raise MaterializationError("privilege payload must be an object")
    phase_state = payload.get("phase_state")
    evidence_rows = payload.get("public_evidence")
    if not isinstance(phase_state, Mapping) or not isinstance(evidence_rows, list):
        raise MaterializationError("privilege requires phase_state and public_evidence")
    turn_index = phase_state.get("turn_index")
    evidence = [_public_evidence_from_dict(row) for row in evidence_rows]
    try:
        builder = ShadowPhaseStateBuilder(turn_index, evidence)
    except (TypeError, ValueError, PrivilegeAuditError) as exc:
        raise MaterializationError(f"invalid privilege turn/evidence: {exc}") from exc

    phases = phase_state.get("phases")
    if not isinstance(phases, Mapping):
        raise MaterializationError("privilege phase_state.phases must be an object")
    try:
        for raw_phase, claims in phases.items():
            phase = ShadowPhase(raw_phase)
            if not isinstance(claims, list):
                raise MaterializationError(f"phase {raw_phase!r} must be a list")
            for claim in claims:
                if not isinstance(claim, Mapping):
                    raise MaterializationError(f"phase {raw_phase!r} contains a non-object")
                references = claim.get("evidence_refs")
                if not isinstance(references, list):
                    raise MaterializationError("phase claim requires evidence_refs")
                builder.claim(
                    phase=phase,
                    key=claim["key"],
                    value=claim.get("value"),
                    evidence_refs=[_evidence_ref_from_dict(ref) for ref in references],
                    status=ClaimStatus(claim.get("status", "known")),
                    derivation=ClaimDerivation(claim.get("derivation", "direct")),
                )
        privilege = builder.build()
    except (KeyError, TypeError, ValueError, PrivilegeAuditError) as exc:
        if isinstance(exc, MaterializationError):
            raise
        raise MaterializationError(f"privilege failed provenance audit: {exc}") from exc

    # This equality detects removed evidence, altered claims, fake audit counts,
    # and any future schema field that the strict builder does not understand.
    if canonical_json(privilege.to_prompt_payload()) != canonical_json(payload):
        raise MaterializationError(
            "serialized privilege differs from its independently rebuilt public state"
        )
    return privilege


def _privilege_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = record.get("privilege", record.get("privileged_context"))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MaterializationError("privileged_context is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise MaterializationError("sidecar requires privilege or privileged_context")
    return raw


def _index_sidecars(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise MaterializationError(f"sidecar row {index} must be an object")
        schema = record.get("schema", PRIVILEGE_SIDECAR_SCHEMA)
        if schema != PRIVILEGE_SIDECAR_SCHEMA:
            raise MaterializationError(f"sidecar row {index} has unsupported schema {schema!r}")
        session = record.get("session_sha256")
        if not isinstance(session, str) or not session:
            raise MaterializationError(f"sidecar row {index} requires session_sha256")
        privilege = rebuild_teacher_privilege(_privilege_payload(record))
        declared_turn = record.get("turn_index", privilege.state.turn_index)
        if declared_turn != privilege.state.turn_index:
            raise MaterializationError(f"sidecar row {index} turn_index disagrees with phase state")
        indexed[session].append(
            {
                "turn_index": declared_turn,
                "request_sha256": record.get("request_sha256"),
                "split": record.get("split", "train"),
                "privilege": privilege,
            }
        )
    return indexed


def _select_sidecar(
    candidates: Sequence[dict[str, Any]],
    *,
    request_sha256: str,
    request_turn: int,
) -> dict[str, Any]:
    exact = [candidate for candidate in candidates if candidate["request_sha256"] == request_sha256]
    pool = exact or [
        candidate
        for candidate in candidates
        if candidate["request_sha256"] in {None, ""} and candidate["turn_index"] <= request_turn
    ]
    if exact and any(candidate["turn_index"] > request_turn for candidate in exact):
        raise MaterializationError("request-matched privilege comes from a future turn")
    if not pool:
        raise MaterializationError("no same-or-past privilege sidecar matches request")
    latest_turn = max(candidate["turn_index"] for candidate in pool)
    latest = [candidate for candidate in pool if candidate["turn_index"] == latest_turn]
    signatures = {candidate["privilege"].prompt_json() for candidate in latest}
    if len(signatures) != 1:
        raise MaterializationError("ambiguous privilege sidecars at the same latest turn")
    return latest[0]


def materialize_decision_replays(
    proxy_records: Iterable[Mapping[str, Any]],
    privilege_records: Iterable[Mapping[str, Any]],
    *,
    roles: Sequence[str] = ("student",),
) -> list[dict[str, Any]]:
    """Pair successful proxy turns with audited same-or-past privilege state."""

    sidecars = _index_sidecars(privilege_records)
    allowed_roles = set(roles)
    rows: list[dict[str, Any]] = []
    for proxy_index, record in enumerate(proxy_records):
        if not isinstance(record, Mapping):
            raise MaterializationError(f"proxy row {proxy_index} must be an object")
        if record.get("schema") != PROXY_TRACE_SCHEMA:
            raise MaterializationError(
                f"proxy row {proxy_index} has unsupported schema {record.get('schema')!r}"
            )
        if record.get("role", "student") not in allowed_roles:
            continue
        status = record.get("status_code")
        if type(status) is not int or not 200 <= status < 300:
            raise MaterializationError(f"proxy row {proxy_index} is not a successful model call")
        original = record.get("request")
        effective = record.get("effective_request")
        if not isinstance(original, Mapping):
            raise MaterializationError(f"proxy row {proxy_index} request must be an object")
        if not isinstance(effective, Mapping):
            raise MaterializationError(
                f"proxy row {proxy_index} effective_request must be an object"
            )
        # Validate both request dialects independently.  The original request
        # remains the identity used by the public-evidence sidecar; the
        # effective request is what the provider actually sampled and therefore
        # what PRIME must replay and the teacher must label.
        original_replay = replay_request(original)
        request = replay_request(effective)
        original_request_hash = sha256_json(original)
        effective_request_hash = sha256_json(effective)
        declared_hash = record.get("request_sha256")
        if declared_hash != original_request_hash:
            raise MaterializationError(f"proxy row {proxy_index} request hash mismatch")
        if record.get("effective_request_sha256") != effective_request_hash:
            raise MaterializationError(f"proxy row {proxy_index} effective request hash mismatch")
        session = record.get("session_sha256")
        if not isinstance(session, str) or session not in sidecars:
            raise MaterializationError(f"proxy row {proxy_index} has no privilege session")
        original_turn_index = infer_turn_index(original_replay["messages"])
        turn_index = infer_turn_index(request["messages"])
        if turn_index != original_turn_index:
            raise MaterializationError("effective request changes the causal assistant-turn index")
        selected = _select_sidecar(
            sidecars[session],
            request_sha256=original_request_hash,
            request_turn=turn_index,
        )
        privilege: TeacherPrivilege = selected["privilege"]
        if privilege.state.turn_index > turn_index:
            raise MaterializationError("future privilege cannot supervise an earlier decision")

        response = record.get("response")
        reference_completion = None
        if isinstance(response, Mapping):
            choices = response.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                message = choices[0].get("message")
                if isinstance(message, Mapping):
                    reference_completion = deepcopy(dict(message))

        replay_id = hashlib.sha256(
            (
                f"{session}:{original_request_hash}:{effective_request_hash}:"
                f"{privilege.audit.evidence_digest}"
            ).encode()
        ).hexdigest()
        rows.append(
            {
                "schema": MATERIALIZED_SCHEMA,
                "idx": len(rows),
                "replay_id": replay_id,
                "split": selected["split"],
                "session_sha256": session,
                "source_sequence": record.get("sequence"),
                "source_turn_index": turn_index,
                # ``request_sha256`` remains the original public-request key
                # for compatibility with the sidecar join.  The explicit
                # fields below remove all ambiguity for downstream audits.
                "request_sha256": original_request_hash,
                "original_request_sha256": original_request_hash,
                "effective_request_sha256": effective_request_hash,
                "replay_request_sha256": sha256_json(request),
                "request": request,
                "privileged_context": privilege.prompt_json(),
                "privilege_audit": privilege.audit.to_dict(),
                "reference_completion": reference_completion,
            }
        )
    return rows


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MaterializationError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise MaterializationError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def materialize_files(
    proxy_path: str | Path,
    privilege_path: str | Path,
    output_path: str | Path,
) -> int:
    rows = materialize_decision_replays(
        read_jsonl(proxy_path),
        read_jsonl(privilege_path),
    )
    return write_jsonl(output_path, rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", required=True, type=Path)
    parser.add_argument("--privilege", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    count = materialize_files(args.proxy, args.privilege, args.output)
    print(canonical_json({"output": str(args.output), "rows": count}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
