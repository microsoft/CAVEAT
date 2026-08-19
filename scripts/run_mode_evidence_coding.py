#!/usr/bin/env python3
"""Run outcome-blinded, isolated AI trajectory-coding passes over public packets.

The model-facing request is assembled from only: one coder packet, the coding
schema, the role prompt, and (for deductive/skeptical only) the frozen codebook.
The runner never reads the sealed mapping or any source trajectory, summary,
database, or log.  Outputs are create-only and resumable.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import prepare_qualitative_mode_packets as qual  # noqa: E402


PUBLIC_ROOT = ROOT / "results" / "mode_evidence_qualitative_v1"
DEFAULT_MANIFEST = PUBLIC_ROOT / "coder_manifest.json"
DEFAULT_PACKETS = PUBLIC_ROOT / "coder_packets"
DEFAULT_OUTPUT = PUBLIC_ROOT / "coding_records"
DEFAULT_SCHEMA = ROOT / "analysis" / "mode_evidence" / "coding_record.schema.json"
DEFAULT_CODEBOOK = ROOT / "analysis" / "mode_evidence" / "sensitizing_codebook.json"
PROMPT_ROOT = ROOT / "analysis" / "mode_evidence" / "coding_prompts"
DEFAULT_PAUSE_FILE = PUBLIC_ROOT / "PAUSE_CODING"

ROLES = ("inductive", "deductive", "skeptical")
REGIONS = ("gcr/shared", "msraif/shared", "redmond/interactive")
DEPLOYMENT = "gpt-5.6-sol_2026-07-09"
MODEL_LABEL = "gpt-5.6-sol"
FORBIDDEN_PATH_PARTS = {"sealed_researcher_only"}
JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
SHARD_HASH_DOMAIN = "mode-evidence-coding-shard-v1"
GROUNDING_SERIALIZATION_CLARIFICATION = (
    "Serialization clarification for exact excerpts: packet fields are decoded strings shown "
    "through a JSON serialization layer. Copy an exact contiguous substring of the decoded field, "
    "not a visually transformed or paraphrased form, and preserve every character when returning "
    "JSON. If the decoded field contains the two literal characters backslash+n (`\\n` as text), "
    "encode the backslash so output JSON parses back to those two characters (`\\\\n` in JSON "
    "source); if it contains an actual newline, use the JSON newline escape (`\\n` in JSON source). "
    "Apply the same distinction to tabs and other escapes. Validation compares the parsed excerpt "
    "with the decoded packet field; the exact-contiguous-substring rule is unchanged."
)


class CodingRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    role: str
    packet_id: str
    packet_path: Path
    output_path: Path

    @property
    def key(self) -> str:
        return f"{self.role}|{self.packet_id}"


def _read_json(path: Path) -> Any:
    _assert_public_path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CodingRunnerError(f"cannot read JSON {path}: {exc}") from exc


def _assert_public_path(path: Path) -> None:
    if FORBIDDEN_PATH_PARTS.intersection(path.resolve().parts):
        raise CodingRunnerError(f"refusing forbidden sealed path: {path}")


def _sha256(path: Path) -> str:
    _assert_public_path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rank(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest(), 16)


def _validate_shard(shard_count: int, shard_index: int) -> None:
    if shard_count < 1:
        raise CodingRunnerError("--shard-count must be positive")
    if shard_index < 0 or shard_index >= shard_count:
        raise CodingRunnerError(
            f"--shard-index must be in [0, {shard_count - 1}] for "
            f"--shard-count={shard_count}"
        )


def shard_index_for_job_key(job_key: str, shard_count: int) -> int:
    """Return a stable shard for an immutable ``role|packet_id`` job key."""

    _validate_shard(shard_count, 0)
    return _rank(SHARD_HASH_DOMAIN, job_key) % shard_count


def partition_jobs(jobs: Sequence[Job], shard_count: int, shard_index: int) -> list[Job]:
    """Select one deterministic, disjoint part of a complete job collection."""

    _validate_shard(shard_count, shard_index)
    return [
        job
        for job in jobs
        if shard_index_for_job_key(job.key, shard_count) == shard_index
    ]


def _atomic_create_json(path: Path, payload: Mapping[str, Any]) -> bool:
    """Atomically create ``path`` without ever replacing an existing record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    temp = path.parent / f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _strict_wire_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the repository schema to the strict structured-output subset."""

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        cleaned = {
            key: visit(value)
            for key, value in node.items()
            if key not in {"$schema", "$id", "title", "default", "uniqueItems"}
        }
        if "type" not in cleaned and "const" in cleaned:
            value = cleaned["const"]
            cleaned["type"] = (
                "boolean" if isinstance(value, bool)
                else "integer" if isinstance(value, int)
                else "number" if isinstance(value, float)
                else "string" if isinstance(value, str)
                else "null" if value is None
                else "object"
            )
        if "type" not in cleaned and isinstance(cleaned.get("enum"), list) and cleaned["enum"]:
            values = cleaned["enum"]
            if all(isinstance(value, str) for value in values):
                cleaned["type"] = "string"
            elif all(isinstance(value, bool) for value in values):
                cleaned["type"] = "boolean"
        if cleaned.get("type") == "object":
            properties = cleaned.get("properties", {})
            cleaned["required"] = list(properties)
            cleaned["additionalProperties"] = False
        return cleaned

    return visit(copy.deepcopy(dict(schema)))


def _first_json_object(text: str) -> str:
    stripped = text.strip()
    fence = JSON_FENCE.match(stripped)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    if start < 0:
        raise CodingRunnerError("model response contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    raise CodingRunnerError("model response has an unterminated JSON object")


def parse_record_response(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(_first_json_object(text))
    except json.JSONDecodeError as exc:
        raise CodingRunnerError(f"model response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CodingRunnerError("model response is not a JSON object")
    return payload


def _packet_step_map(packet: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    steps: dict[int, Mapping[str, Any]] = {}
    for raw in packet.get("trace", []):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("index"), int):
            raise CodingRunnerError(f"packet {packet.get('packet_id')} has a malformed trace step")
        index = int(raw["index"])
        if index in steps:
            raise CodingRunnerError(f"packet {packet.get('packet_id')} repeats step {index}")
        steps[index] = raw
    return steps


def grounding_errors(
    record: Mapping[str, Any],
    packet: Mapping[str, Any],
    codebook: Mapping[str, Any],
) -> list[str]:
    """Validate exact citations and conservative evidence-channel labels."""

    errors: list[str] = []
    steps = _packet_step_map(packet)
    definitions = {item["id"]: item for item in codebook["codes"]}
    for decision in record.get("codes", []):
        if not isinstance(decision, Mapping):
            continue
        code_id = str(decision.get("code_id"))
        present = bool(decision.get("present"))
        indices = decision.get("step_indices", [])
        excerpt = decision.get("excerpt", "")
        if not present:
            if indices:
                errors.append(f"{code_id}: absent decision must have no step_indices")
            if excerpt:
                errors.append(f"{code_id}: absent decision must have an empty excerpt")
            continue
        if not indices:
            errors.append(f"{code_id}: present decision needs a step index")
            continue
        unknown = [index for index in indices if index not in steps]
        if unknown:
            errors.append(f"{code_id}: unknown step indices {unknown}")
            continue
        if not isinstance(excerpt, str) or not excerpt:
            errors.append(f"{code_id}: present decision needs an exact excerpt")
            continue
        locations: set[str] = set()
        for index in indices:
            step = steps[index]
            for field in ("action", "reasoning", "url", "note"):
                value = step.get(field)
                if isinstance(value, str) and excerpt in value:
                    locations.add(field)
        if not locations:
            errors.append(f"{code_id}: excerpt is not contiguous text in any cited step")
            continue
        evidence = decision.get("evidence_level")
        if evidence == "state":
            errors.append(f"{code_id}: coder packet exposes no direct state evidence channel")
        elif evidence == "behavior" and not locations.intersection({"action", "url", "note"}):
            errors.append(f"{code_id}: behavior excerpt occurs only in model reasoning")
        elif evidence == "justification_only" and "reasoning" not in locations:
            errors.append(f"{code_id}: justification excerpt does not occur in reasoning")
        allowed_episodes = set(definitions.get(code_id, {}).get("episodes", []))
        coded_episode = decision.get("episode")
        if allowed_episodes and coded_episode == "run":
            # Frozen unit is episode_with_run_level_rollup.  A run rollup is
            # not a loophole for one incompatible event: require multiple
            # grounded steps and an interpretation explicitly naming at least
            # two of this code's permitted episode types.
            interpretation_lower = str(decision.get("interpretation", "")).lower()
            named_allowed = {
                episode
                for episode in allowed_episodes
                if episode.replace("_", " ") in interpretation_lower
            }
            if len(allowed_episodes) < 2 or len(set(indices)) < 2 or len(named_allowed) < 2:
                errors.append(
                    f"{code_id}: episode='run' needs grounded multi-step evidence explicitly spanning "
                    f"at least two allowed episodes {sorted(allowed_episodes)}"
                )
        elif allowed_episodes and coded_episode not in allowed_episodes:
            errors.append(
                f"{code_id}: present episode {coded_episode!r} is outside {sorted(allowed_episodes)}"
            )
        if not str(decision.get("interpretation", "")).strip():
            errors.append(f"{code_id}: interpretation is empty")
        if not str(decision.get("alternative_explanation", "")).strip():
            errors.append(f"{code_id}: alternative_explanation is empty")
    return errors


def validate_model_record(
    record: Mapping[str, Any],
    *,
    role: str,
    packet: Mapping[str, Any],
    schema: Mapping[str, Any],
    codebook: Mapping[str, Any],
) -> list[str]:
    errors = qual.validate_coding_record(
        record,
        schema,
        codebook,
        require_complete_codebook=True,
        allowed_packet_ids={str(packet["packet_id"])},
    )
    if record.get("coder_role") != role:
        errors.append(f"$.: coder_role must be {role!r}")
    errors.extend(grounding_errors(record, packet, codebook))
    return errors


def normalize_record(record: Mapping[str, Any], codebook: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only deterministic ordering; never change a coding decision."""

    output = dict(record)
    order = {item["id"]: index for index, item in enumerate(codebook["codes"])}
    output["codes"] = sorted(
        [dict(item) for item in record["codes"]], key=lambda item: order[item["code_id"]]
    )
    output.setdefault("candidate_new_codes", [])
    output.setdefault("negative_cases", [])
    return output


def build_model_messages(
    role: str,
    packet: Mapping[str, Any],
    schema: Mapping[str, Any],
    codebook: Mapping[str, Any],
    role_prompt: str,
) -> list[dict[str, str]]:
    """Build the audited model request from allowed public inputs only."""

    code_ids = [item["id"] for item in codebook["codes"]]
    definitions = {item["id"]: item for item in codebook["codes"]}
    episode_hints = "\n".join(
        f"- {code_id} (allowed present episodes: "
        f"{', '.join(definitions[code_id].get('episodes', []))})"
        for code_id in code_ids
    )
    system = (
        role_prompt.rstrip()
        + "\n\nRequired frozen code IDs, in output order, with allowed present episodes:\n"
        + episode_hints
        + "\n\nThe frozen unit is episode_with_run_level_rollup. Use episode='run' only when "
          "a code permits at least two episodes, cite at least two exact steps spanning two of those "
          "episodes, and explicitly name both episode types in the interpretation. Never use 'run' "
          "for a single event or a code restricted to one episode."
        + "\n\n"
        + GROUNDING_SERIALIZATION_CLARIFICATION
    )
    request: dict[str, Any] = {
        "coding_schema": schema,
        "blinded_packet": packet,
    }
    if role in {"deductive", "skeptical"}:
        request["frozen_codebook"] = codebook
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "Code this single blinded packet. Return only the JSON record.\n\n"
                + json.dumps(request, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def load_jobs(
    manifest_path: Path,
    packets_root: Path,
    output_root: Path,
    roles: Sequence[str],
) -> tuple[list[Job], dict[str, Mapping[str, Any]]]:
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("status") != "outcome-blinded":
        raise CodingRunnerError("coder manifest is missing or not outcome-blinded")
    packets_root = packets_root.resolve()
    packet_map: dict[str, Mapping[str, Any]] = {}
    jobs: list[Job] = []
    for row in manifest.get("packets", []):
        if not isinstance(row, Mapping):
            raise CodingRunnerError("malformed coder manifest row")
        packet_id = str(row["packet_id"])
        manifest_rel = Path(str(row["packet_path"]))
        # Manifest paths are rooted at PUBLIC_ROOT; accept only the coder_packets subtree.
        expected_prefix = Path("coder_packets")
        if not manifest_rel.parts or manifest_rel.parts[0] != expected_prefix.parts[0]:
            raise CodingRunnerError(f"manifest path escapes coder packet tree: {manifest_rel}")
        packet_path = (manifest_path.parent / manifest_rel).resolve()
        try:
            packet_path.relative_to(packets_root)
        except ValueError as exc:
            raise CodingRunnerError(f"packet path escapes packet root: {packet_path}") from exc
        if _sha256(packet_path) != row.get("packet_sha256"):
            raise CodingRunnerError(f"packet hash mismatch: {packet_id}")
        packet = _read_json(packet_path)
        if not isinstance(packet, Mapping) or packet.get("packet_id") != packet_id:
            raise CodingRunnerError(f"packet identity mismatch: {packet_id}")
        forbidden = {
            "answer",
            "evaluation",
            "model",
            "condition",
            "summary",
            "chosen",
            "hero_identity",
            "strict_binary",
            "preservation_strict",
        }
        if forbidden.intersection(packet):
            raise CodingRunnerError(f"outcome-bearing top-level field in public packet {packet_id}")
        packet_map[packet_id] = packet
        for role in roles:
            jobs.append(
                Job(
                    role=role,
                    packet_id=packet_id,
                    packet_path=packet_path,
                    output_path=output_root.resolve() / role / f"{packet_id}.json",
                )
            )
    expected = int(manifest.get("packet_count", -1)) * len(roles)
    if len(jobs) != expected or len(packet_map) != int(manifest.get("packet_count", -1)):
        raise CodingRunnerError("manifest packet count is inconsistent")
    return jobs, packet_map


class RegionClients:
    def __init__(self, regions: Sequence[str], per_region: int, timeout_s: float):
        self.regions = tuple(regions)
        self.semaphores = {region: asyncio.Semaphore(per_region) for region in regions}
        self.timeout_s = timeout_s
        self.clients: dict[str, Any] = {}

    def build(self) -> None:
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from openai import AsyncOpenAI

        token_provider = get_bearer_token_provider(
            AzureCliCredential(), os.environ.get("TRAPI_SCOPE", "api://trapi/.default")
        )

        async def api_key() -> str:
            return token_provider()

        self.clients = {
            region: AsyncOpenAI(
                api_key=api_key,
                base_url=f"https://trapi.research.microsoft.com/{region}/openai/v1/",
                max_retries=1,
                timeout=self.timeout_s,
            )
            for region in self.regions
        }

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self.clients.values()))


async def _call_one(
    clients: RegionClients,
    region: str,
    messages: list[dict[str, str]],
    wire_schema: Mapping[str, Any],
    *,
    reasoning_effort: str,
    max_completion_tokens: int,
) -> tuple[str, dict[str, Any]]:
    async with clients.semaphores[region]:
        started = time.monotonic()
        response = await clients.clients[region].chat.completions.create(
            model=DEPLOYMENT,
            messages=messages,
            reasoning_effort=reasoning_effort,
            max_completion_tokens=max_completion_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "mode_evidence_coding_record",
                    "strict": True,
                    "schema": wire_schema,
                },
            },
        )
        choice = response.choices[0]
        usage = response.usage
        receipt = {
            "region": region,
            "latency_s": round(time.monotonic() - started, 3),
            "finish_reason": choice.finish_reason,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        return choice.message.content or "", receipt


def _retry_after_seconds(exc: Exception) -> float:
    """Read TRAPI/OpenAI Retry-After, including its plain-text fallback."""

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    try:
        if headers.get("retry-after-ms"):
            return max(1.0, min(120.0, float(headers["retry-after-ms"]) / 1000.0))
        if headers.get("retry-after"):
            return max(1.0, min(120.0, float(headers["retry-after"])))
    except (TypeError, ValueError):
        pass
    match = re.search(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*seconds", str(exc), re.I)
    return max(1.0, min(120.0, float(match.group(1)))) if match else 30.0


class EventWriter:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(self, event: Mapping[str, Any]) -> None:
        line = json.dumps(dict(event), sort_keys=True, ensure_ascii=False) + "\n"
        async with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()


async def run_coding(
    *,
    manifest_path: Path,
    packets_root: Path,
    output_root: Path,
    schema_path: Path,
    codebook_path: Path,
    roles: Sequence[str],
    regions: Sequence[str],
    concurrency_per_region: int,
    max_attempts: int,
    reasoning_effort: str,
    max_completion_tokens: int,
    timeout_s: float,
    pause_file: Path,
    limit: int | None = None,
    max_rate_limits: int = 12,
    shard_count: int = 1,
    shard_index: int = 0,
) -> dict[str, Any]:
    if concurrency_per_region > 4:
        raise CodingRunnerError("coding cap is four concurrent calls per region")
    if len(regions) * concurrency_per_region > 12:
        raise CodingRunnerError("coding cap is 12 total concurrent calls")
    if any(role not in ROLES for role in roles):
        raise CodingRunnerError(f"unknown role in {roles}")
    if any(region not in REGIONS for region in regions):
        raise CodingRunnerError(f"unknown TRAPI region in {regions}")
    _validate_shard(shard_count, shard_index)
    schema = _read_json(schema_path)
    codebook = _read_json(codebook_path)
    jobs, packets = load_jobs(manifest_path, packets_root, output_root, roles)
    manifest_target_records = len(jobs)
    if limit is not None:
        if limit < 1:
            raise CodingRunnerError("--limit must be positive")
        jobs = sorted(jobs, key=lambda item: _rank("coding-v1", item.key))[:limit]
    unsharded_jobs = jobs
    jobs = partition_jobs(unsharded_jobs, shard_count, shard_index)
    prompts = {
        role: (PROMPT_ROOT / f"{role}.md").read_text(encoding="utf-8") for role in roles
    }
    wire_schema = _strict_wire_schema(schema)
    clients = RegionClients(regions, concurrency_per_region, timeout_s)
    clients.build()
    event_writer = EventWriter(output_root / ".coding_runner" / "events.jsonl")
    totals = CounterResult()

    # Validate existing records before treating them as resumable successes.
    pending: list[Job] = []
    for job in jobs:
        if not job.output_path.exists():
            pending.append(job)
            continue
        existing = _read_json(job.output_path)
        if not isinstance(existing, Mapping):
            raise CodingRunnerError(f"existing record is not an object: {job.output_path}")
        errors = validate_model_record(
            existing,
            role=job.role,
            packet=packets[job.packet_id],
            schema=schema,
            codebook=codebook,
        )
        if errors:
            raise CodingRunnerError(
                f"refusing to overwrite invalid existing record {job.output_path}: {errors[:5]}"
            )
        totals.skipped += 1

    queue: asyncio.Queue[Job] = asyncio.Queue()
    for job in sorted(pending, key=lambda item: _rank("coding-v1", item.key)):
        queue.put_nowait(job)

    async def worker(region: str, worker_index: int) -> None:
        while True:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if pause_file.exists():
                    totals.paused += 1
                    continue
                packet = packets[job.packet_id]
                messages = build_model_messages(
                    job.role, packet, schema, codebook, prompts[job.role]
                )
                start_offset = _rank("route", job.key) % len(regions)
                last_error = ""
                substantive_attempts = 0
                rate_limit_events = 0
                call_ordinal = 0
                finished = False
                paused_job = False
                while substantive_attempts < max_attempts:
                    if pause_file.exists():
                        totals.paused += 1
                        paused_job = True
                        break
                    attempt = substantive_attempts + 1
                    attempt_region = regions[(start_offset + call_ordinal) % len(regions)]
                    # A worker's nominal region keeps the configured active workers per
                    # route; retry rotation still acquires the destination cap.
                    if call_ordinal == 0:
                        attempt_region = region
                    call_ordinal += 1
                    try:
                        content, receipt = await _call_one(
                            clients,
                            attempt_region,
                            messages,
                            wire_schema,
                            reasoning_effort=reasoning_effort,
                            max_completion_tokens=max_completion_tokens,
                        )
                        record = parse_record_response(content)
                        errors = validate_model_record(
                            record,
                            role=job.role,
                            packet=packet,
                            schema=schema,
                            codebook=codebook,
                        )
                        if errors:
                            raise CodingRunnerError("; ".join(errors[:12]))
                        normalized = normalize_record(record, codebook)
                        created = _atomic_create_json(job.output_path, normalized)
                        if not created:
                            existing = _read_json(job.output_path)
                            collision_errors = validate_model_record(
                                existing,
                                role=job.role,
                                packet=packet,
                                schema=schema,
                                codebook=codebook,
                            )
                            if collision_errors:
                                raise CodingRunnerError(
                                    f"create collision with invalid record: {collision_errors[:3]}"
                                )
                        totals.completed += int(created)
                        totals.skipped += int(not created)
                        await event_writer.emit(
                            {
                                "event": "record_valid",
                                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "packet_id": job.packet_id,
                                "role": job.role,
                                "attempt": attempt,
                                "worker_region": region,
                                **receipt,
                            }
                        )
                        finished = True
                        break
                    except Exception as exc:  # isolated retry; never feed output back
                        last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                        if type(exc).__name__ == "RateLimitError":
                            rate_limit_events += 1
                            delay = _retry_after_seconds(exc)
                            await event_writer.emit(
                                {
                                    "event": "rate_limit_deferred",
                                    "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "packet_id": job.packet_id,
                                    "role": job.role,
                                    "coding_attempt": attempt,
                                    "rate_limit_event": rate_limit_events,
                                    "region": attempt_region,
                                    "retry_after_s": delay,
                                    "error": last_error,
                                }
                            )
                            if rate_limit_events >= max_rate_limits:
                                break
                            await asyncio.sleep(delay)
                            continue
                        substantive_attempts += 1
                        await event_writer.emit(
                            {
                                "event": "attempt_failed",
                                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "packet_id": job.packet_id,
                                "role": job.role,
                                "attempt": attempt,
                                "region": attempt_region,
                                "error": last_error,
                            }
                        )
                if not finished and not paused_job:
                    totals.failed += 1
                    await event_writer.emit(
                        {
                            "event": "record_failed",
                            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "packet_id": job.packet_id,
                            "role": job.role,
                            "error": last_error,
                        }
                    )
            finally:
                queue.task_done()

    try:
        workers = [
            asyncio.create_task(worker(region, index))
            for region in regions
            for index in range(concurrency_per_region)
        ]
        await asyncio.gather(*workers)
    finally:
        await clients.close()

    result = {
        "schema_version": 1,
        "label": "AI coding repeatability; not human inter-rater reliability",
        "model": MODEL_LABEL,
        "roles": list(roles),
        "regions": list(regions),
        "concurrency_per_region": concurrency_per_region,
        "max_total_concurrency": len(regions) * concurrency_per_region,
        "shard_count": shard_count,
        "shard_index": shard_index,
        "shard_hash_domain": SHARD_HASH_DOMAIN,
        "manifest_target_records": manifest_target_records,
        "unsharded_target_records": len(unsharded_jobs),
        "target_records": len(jobs),
        "valid_existing_at_start": totals.skipped,
        "created": totals.completed,
        "failed": totals.failed,
        "paused": totals.paused,
        "remaining": sum(
            not (output_root / role / f"{packet_id}.json").exists()
            for role in roles
            for packet_id in packets
        ),
        "unsharded_remaining": sum(not job.output_path.exists() for job in unsharded_jobs),
        "shard_remaining": sum(not job.output_path.exists() for job in jobs),
    }
    state_name = (
        "last_run.json"
        if shard_count == 1
        else f"last_run.shard-{shard_index:05d}-of-{shard_count:05d}.json"
    )
    state_path = output_root / ".coding_runner" / state_name
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


class CounterResult:
    def __init__(self) -> None:
        self.completed = 0
        self.skipped = 0
        self.failed = 0
        self.paused = 0


def validate_all_records(
    manifest_path: Path,
    packets_root: Path,
    output_root: Path,
    schema_path: Path,
    codebook_path: Path,
) -> dict[str, Any]:
    schema = _read_json(schema_path)
    codebook = _read_json(codebook_path)
    jobs, packets = load_jobs(manifest_path, packets_root, output_root, ROLES)
    missing: list[str] = []
    invalid: dict[str, list[str]] = {}
    counts: dict[str, int] = {role: 0 for role in ROLES}
    for job in jobs:
        if not job.output_path.exists():
            missing.append(job.key)
            continue
        payload = _read_json(job.output_path)
        if not isinstance(payload, Mapping):
            invalid[job.key] = ["record is not an object"]
            continue
        errors = validate_model_record(
            payload,
            role=job.role,
            packet=packets[job.packet_id],
            schema=schema,
            codebook=codebook,
        )
        if errors:
            invalid[job.key] = errors
        else:
            counts[job.role] += 1
    return {
        "valid": not missing and not invalid,
        "expected": len(jobs),
        "valid_records": sum(counts.values()),
        "by_role": counts,
        "missing": missing,
        "invalid": invalid,
        "label": "AI coding repeatability; not human inter-rater reliability",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--packets", type=Path, default=DEFAULT_PACKETS)
    run.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    run.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    run.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)
    run.add_argument("--roles", nargs="+", choices=ROLES, default=list(ROLES))
    run.add_argument("--regions", nargs="+", choices=REGIONS, default=list(REGIONS))
    run.add_argument("--concurrency-per-region", type=int, default=4)
    run.add_argument("--max-attempts", type=int, default=4)
    run.add_argument("--max-rate-limits", type=int, default=12)
    run.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="high")
    run.add_argument("--max-completion-tokens", type=int, default=12000)
    run.add_argument("--timeout", type=float, default=600.0)
    run.add_argument("--pause-file", type=Path, default=DEFAULT_PAUSE_FILE)
    run.add_argument("--limit", type=int, help="deterministic smoke/resume subset")
    run.add_argument(
        "--shard-count",
        type=int,
        default=1,
        help="number of stable disjoint job-key partitions (default: 1)",
    )
    run.add_argument(
        "--shard-index",
        type=int,
        default=0,
        help="zero-based partition to run (default: 0)",
    )

    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.add_argument("--packets", type=Path, default=DEFAULT_PACKETS)
    validate.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    validate.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    validate.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            result = asyncio.run(
                run_coding(
                    manifest_path=args.manifest,
                    packets_root=args.packets,
                    output_root=args.output,
                    schema_path=args.schema,
                    codebook_path=args.codebook,
                    roles=args.roles,
                    regions=args.regions,
                    concurrency_per_region=args.concurrency_per_region,
                    max_attempts=args.max_attempts,
                    reasoning_effort=args.reasoning_effort,
                    max_completion_tokens=args.max_completion_tokens,
                    timeout_s=args.timeout,
                    pause_file=args.pause_file,
                    limit=args.limit,
                    max_rate_limits=args.max_rate_limits,
                    shard_count=args.shard_count,
                    shard_index=args.shard_index,
                )
            )
        else:
            result = validate_all_records(
                args.manifest, args.packets, args.output, args.schema, args.codebook
            )
    except CodingRunnerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid", result.get("failed", 0) == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
