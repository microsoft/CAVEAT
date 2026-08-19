# Qwen3.5-27B harness-assisted post-training evaluation

This package owns the scientific boundary around the revised campaign. It does
not change AgentArena, Amazon, `browseruse-deliberative`, or model-training
code. It deterministically assigns shadow tasks, locks canonical source bytes,
builds paired run matrices, renders directly runnable AgentArena configs, and
fails closed when computing the final matched comparison.

The confirmatory treatment is deliberately narrow: raw and trained
`Qwen/Qwen3.5-27B` use the same improved harness, browser environment,
inference stack, sampling contract, and safety backstops. Only model weight
identity may differ.

## Diagnostic exact-LoRA route gate

Before preparing or launching the Amazon matrix, each live exact-LoRA endpoint
can be checked with four fixed non-Amazon procedural tasks. This is only a route
and contract-semantics diagnostic: it does not use Amazon tasks, write benchmark
results, or change the frozen final matrix. The runner imports the exact schema
prompt, JSON decoder, Pydantic draft type, and operational scorer used by the
improved browser-use harness. It exits 0 only for a semantic `4/4`, exits 2 for
a model-semantic failure, and exits 3 for an endpoint/transport failure.

```bash
export PYTHONPATH="$PWD/training/harness_posttrain_eval/src:$PWD/training/harness_posttrain/src:$PWD"
export HARNESS_POSTTRAIN_API_KEY='<the key shared by both endpoints>'
TASKS="$PWD/results/harness_posttrain_training/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/corpus/raw/selection_contract_tasks.jsonl"

$PWD/.venv/bin/python -m harness_posttrain_eval.route_gate \
  --arm base \
  --base-url http://127.0.0.1:18000/v1 \
  --model qwen35-27b-base-exact-lora \
  --tasks "$TASKS"

$PWD/.venv/bin/python -m harness_posttrain_eval.route_gate \
  --arm trained \
  --base-url http://127.0.0.1:18100/v1 \
  --model qwen35-harness-posttrained-exact-lora \
  --tasks "$TASKS"
```

Each invocation prints one JSON report with all four task outcomes and the
aggregate `semantic_pass_fraction`. It writes no artifact; redirect stdout to a
new diagnostics path if a persistent operator receipt is desired.

The local Qwen inference contract deliberately keeps tokenizer-native thinking
enabled. It does not label the endpoint as an OpenAI reasoning model, because
that would add a `reasoning_effort` field that training and sealed checkpoint
selection never used. It also explicitly omits `frequency_penalty`: browser-use
otherwise contributes its own `0.3` default, while the training and selection
requests had no such field. The per-model `extra.frequency_penalty: null`
override applies identically to the raw and trained arms and leaves all other
benchmark models on browser-use's unchanged default.

Before this semantic gate, prove the serving topology under a concurrent burst.
The canary is create-only and fails unless the live vLLM metrics simultaneously
show at least one running request on each of engines 0 through 3:

```bash
python -m harness_posttrain_eval.cli dp-canary \
  --base-url http://127.0.0.1:18000/v1 \
  --model qwen35-27b-base-exact-lora \
  --output /path/to/new/diagnostics/base-dp-canary.json

python -m harness_posttrain_eval.cli dp-canary \
  --base-url http://127.0.0.1:18100/v1 \
  --model qwen35-harness-posttrained-exact-lora \
  --output /path/to/new/diagnostics/trained-dp-canary.json
```

Both exact-LoRA endpoints must be launched with `vllm serve`,
`--data-parallel-size 4`, and `--api-server-count 4`. The live capture rejects
the legacy single-frontend module entrypoint and a missing frontend count.

## Frozen design

- Laptop is development-exposed. Office chair, mattress, backpack, and tent
  remain held out until the checkpoint is frozen.
- The shadow split has 768 train tasks from 12 category/schema families and 192
  validation tasks from four disjoint families. Validation contains 32
  selection, 64 sealed-gate, and 96 reserve tasks.
- The final base-versus-trained matrix has 200 combined runs and 120 clean
  guard runs. An optional 80-run SFT-parent arm isolates a later on-policy
  stage.
- Optimal-product selection rate (`strict_binary`) is the headline.
  `preservation_strict` is a mandatory secondary endpoint.

The campaign configuration binds these exact harness bytes:

| File | SHA-256 |
|---|---|
| `browseruse.py` | `1db5dad9bf8950aca2602c0ef5c33673f415ad1662c5c8e5de853b338082d52e` |
| `browseruse_deliberative.py` | `6336e052bd94617dabea9ea0a7fb4b3c23923728b90f09abab4040d5146ad3ed` |
| `_deliberative_core.py` | `9800f3a408c5c966b5bd01b3453ea7ab60e41c80b7420531aa1cf68211885cbb` |

## Prospective browser-action-corrected evaluation API

The corrected-model workflow is intentionally exposed as a Python API first;
the shared CLI has not been changed.  It accepts the finalized
`exact_lora_manifest.json` itself and delegates component, tokenizer, receipt,
and composite validation to
`harness_posttrain.browser_action_finalization.validate_browser_action_serving_manifest`.
It never infers a campaign-specific model path.

When the component trees exist only on the serving PVC, construct the injected
validator with `corrected_capture.remote_manifest_validator(...)`.  It runs the
same authoritative validator in the serving pod on every preparation/audit
pass and accepts local manifest/receipt copies only when their hashes equal the
remote validated files.

`prepare_corrected_evaluations(...)` in
`harness_posttrain_eval.corrected_eval` freezes two outcome-blind designs:

- a 24-cell laptop gate comparing the existing step-20 model with the corrected
  model across the four nonabsolute variants (combined n=2 and clean n=1 per
  arm and variant); and
- a 320-cell Amazon-five confirmation comparing raw zero-control Qwen with the
  corrected model (combined n=5 and clean n=3 per arm and variant).

The preparation reads only the prior preparation receipt, frozen manifest,
endpoint/model records, canonical matrix, and launch configs.  It does not read
results or observations.  Its create-only reuse manifests select exactly 12
existing step-20 laptop cells and 160 existing raw cells.  Every target cell is
bound to its benchmark-task hash, harness hash, causal launch-config hash,
limit-contract hash, block seed, source launch-config hash, and exact model
weight identity.  `raw` always means the unmodified Qwen parent plus its
exact-shape zero adapter; it never means step 20.

After the corrected endpoint and its local port-forward are live,
`harness_posttrain_eval.corrected_capture.capture_corrected_endpoint(...)`
produces all endpoint records without trusting hand-written server JSON.  It
runs the authoritative finalizer validator inside the serving pod, captures
PID 1's real argv and mutation-sensitive environment, verifies the exact local
port-forward process by PID before and after a live `/v1/models` probe, and
publishes the model spec, server, tunnel, probe, endpoint binding, and capture
receipt atomically and create-only.  The API key is used only for the probe and
is not persisted.  `qualify_corrected_endpoint(...)` remains available to
audit separately captured records without making a request.  The server record
uses `browser_action_correction_<selected-checkpoint-name>` as `adapter_kind`,
where the checkpoint name comes from the validated manifest.

`render_corrected_completion_bundle(...)` then creates only the missing
corrected runs: 12 for `laptop_development_gate`, or 160 for
`amazon_five_final`.  The existing `run-bundle` command can execute these
single-arm completion manifests.  Reuse-only manifests are auditable evidence
inputs and the executor refuses to launch them.  Final configs cannot be
rendered until a matching successful development report is supplied.

Use the existing `convert-results` command separately on the reuse-only subset
with the original frozen manifest, and on the corrected-only subset with its
derived frozen manifest.  Then use these functions from
`harness_posttrain_eval.corrected_report`:

```python
bind_evaluation_observations(...)  # joins reused and new evidence cell by cell
analyze_development_gate(...)      # returns the preregistered selection report
analyze_corrected_final(...)       # gated raw-versus-corrected confirmation
write_corrected_report(...)        # create-only JSON and Markdown
```

The development model is selected only when combined binary-hero delta is at
least 0.25, clean binary-hero regression is at most 0.10, at least three of four
variant deltas are positive, and no run binds a safety backstop.  There is no
fallback selection.  Held-out Amazon scenarios are not accepted by the gate
report and cannot influence selection.

## Use

From the repository root:

```bash
export PYTHONPATH=training/harness_posttrain_eval/src
python -m harness_posttrain_eval.cli audit-split \
  --split training/harness_posttrain_eval/manifests/shadow_split.json
python -m harness_posttrain_eval.cli audit-matrix \
  --matrix training/harness_posttrain_eval/manifests/final_matrix.json
```

The frozen 320-run final matrix is also partitioned into a 64-run
development-laptop matrix and a 256-run four-scenario held-out matrix. Their
run-ID union is exactly the full matrix, so they can be scheduled independently
without changing the analysis denominator.

### Prepare the final evaluation without launching it

Use `prepare-final` once both vLLM servers and their local Kubernetes port
forwards are already running. It checks
the canonical matrix, freezes and re-verifies the benchmark, writes matched
model specs, probes each live `/v1/models` response, validates the endpoint and
server equivalence contract, and renders all 320 run configs. It does not start
any storefront, browser, model server, or benchmark run.

Each `--*-server-record` must be an operator-captured record of the server that
is actually running, with exactly these keys:

```json
{
  "model_path": "/the/exact/model/path",
  "served_model_name": "the-exact-wire-name",
  "port": 8000,
  "argv": ["the", "complete", "vllm", "launch", "argv"],
  "config": {
    "model_path": "/the/exact/model/path",
    "served_model_name": "the-exact-wire-name",
    "port": 8000,
    "all_other_resolved_server_options": "included here"
  }
}
```

Here, `port` is the actual port on which vLLM listens inside its pod, not a
workstation port. The two complete `argv` and `config` records must be identical
after only model path and served model name are normalized; their actual server
ports must match exactly. Do not reconstruct or guess these records after
launch.

Each `--*-tunnel-record` separately captures the live local transport, with
exactly these keys:

```json
{
  "transport": "kubectl-port-forward",
  "listen_host": "127.0.0.1",
  "listen_port": 18000,
  "target_pod": "the-exact-running-pod-name",
  "target_port": 8000,
  "argv": [
    "kubectl", "--namespace", "eval", "port-forward", "--address",
    "127.0.0.1", "pod/the-exact-running-pod-name", "18000:8000"
  ],
  "config": {
    "context": "the-exact-cluster-context",
    "namespace": "eval",
    "listen_host": "127.0.0.1",
    "listen_port": 18000,
    "target_pod": "the-exact-running-pod-name",
    "target_port": 8000
  }
}
```

The tunnel listener must exactly match the corresponding model-spec URL, and
its target port must exactly match that arm's vLLM server port. The two complete
tunnel records must match after only the pod identity and local listen port are
normalized. Thus `127.0.0.1:18000 -> base-pod:8000` and
`127.0.0.1:18100 -> trained-pod:8000` are an honest matched deployment, while a
different target port, cluster context, namespace, transport, or other option
fails closed. The serving-stack JSON records the actual container/engine stack.
The API secret is read once for the live probes and only its shared `env:...`
reference is written to model specs.

`--base-weight-sha256` and `--trained-weight-sha256` are supplied model-tree
identities; this preparation command deliberately does not traverse either
model path. Compute each on the machine holding the checkpoint as the SHA-256 of
the canonical, sorted per-file `{size, sha256}` manifest, following file contents
when the Hugging Face snapshot uses symlinks and excluding
`merge_provenance.json`. For the final model, pass the published
`model_tree_sha256`. The fixed wire deployments are `qwen35-27b-base` and
`qwen35-harness-posttrained-final`.

```bash
export PYTHONPATH="$PWD/training/harness_posttrain_eval/src"
export HARNESS_POSTTRAIN_API_KEY='<the key already used by both vLLM servers>'

python -m harness_posttrain_eval.cli \
  --config "$PWD/training/harness_posttrain_eval/configs/campaign.json" \
  prepare-final \
  --repo-root "$PWD" \
  --split "$PWD/training/harness_posttrain_eval/manifests/shadow_split.json" \
  --matrix "$PWD/training/harness_posttrain_eval/manifests/final_matrix.json" \
  --output-dir /path/to/new/final-preparation \
  --results-root /path/to/new/final-results \
  --storefront-base-port 22000 \
  --python-executable "$PWD/.venv/bin/python" \
  --base-weight-sha256 "$BASE_WEIGHT_SHA256" \
  --trained-weight-sha256 "$TRAINED_WEIGHT_SHA256" \
  --tokenizer-sha256 "$TOKENIZER_SHA256" \
  --chat-template-sha256 "$CHAT_TEMPLATE_SHA256" \
  --container-image-digest "$CONTAINER_IMAGE_DIGEST" \
  --serving-stack-json /path/to/actual-serving-stack.json \
  --base-name qwen35-27b-base-local \
  --trained-name qwen35-27b-trained-local \
  --base-url http://127.0.0.1:18000/v1 \
  --trained-url http://127.0.0.1:18100/v1 \
  --base-server-record /path/to/actual-base-server-record.json \
  --trained-server-record /path/to/actual-trained-server-record.json \
  --base-tunnel-record /path/to/actual-base-tunnel-record.json \
  --trained-tunnel-record /path/to/actual-trained-tunnel-record.json
```

The output directory is create-only. `preparation.json` binds the manifest,
matrix, model specs, endpoint attestation, launch manifest, both source server
records, both source tunnel records, results root, and storefront port band.
Check that the selected port band is free before later launching the bundle.

After base and trained OpenAI-compatible endpoints are live, copy
`configs/model_specs.example.json`, replace endpoint/deployment values, and
render create-only per-run AgentArena configs:

The endpoint manifest must attest both arms' exact weight hashes, container
image, model-spec hash, successful `/v1/models` identity probe, and server
launch record (`model_path`, served name, port, complete `argv`, and complete
parsed `config`), and Kubernetes tunnel record (transport, listener, target pod
and port, complete `argv`, and complete parsed `config`). Model specs must be
identical except for display name, deployment, and base URL. Server
arguments/config must be identical after only model path and served name are
normalized. Tunnel arguments/config must be identical after only target pod and
local listen port are normalized.

```bash
python -m harness_posttrain_eval.cli render-run-bundle \
  --matrix training/harness_posttrain_eval/manifests/final_matrix.json \
  --frozen-manifest /path/to/frozen-inference-manifest.json \
  --endpoint-manifest /path/to/frozen-endpoint-manifest.json \
  --model-specs /path/to/frozen-model-specs.json \
  --output-dir /path/to/final-run-bundle \
  --results-root /path/to/results \
  --base-port 20000 \
  --python-executable "$PWD/.venv/bin/python"
```

Every `launch_manifest.json` entry contains an executable `argv` for
`agentarena.run_cell`, the fully materialized canonical `TaskSpec`, environment
contract, unique port, result directory, matched-pair ID, and source matrix
hash. The renderer also binds the frozen harness and inference hashes, a unique
per-run cache nonce, and AgentArena's complete runtime limit audit.

Run or resume the bundle with bounded, arm-balanced concurrency:

```bash
python -m harness_posttrain_eval.cli run-bundle \
  --launch-manifest /path/to/final-run-bundle/launch_manifest.json \
  --state-dir /path/to/final-run-state \
  --working-directory "$PWD" \
  --jobs 32 \
  --spawn-stagger-seconds 10
```

A resume never launches an already complete behavioral run. Partial, malformed,
errored, and skipped result directories are reported in `batch_status.json` and
left byte-for-byte untouched; preserve and deliberately reissue those attempts
instead of silently overwriting evidence.

### Outcome-blind interrupted-run refill

After an executor has stopped, build a retry manifest containing only selected
absent or invalid cells with `prepare-refill`. The command acquires the original
executor's lock non-blockingly and refuses to proceed if any recorded child PID
is still live. It calls the batch executor's existing `_load_result` classifier;
it does not inspect scores, purchases, or other outcomes to decide what to retry.

```bash
python -m harness_posttrain_eval.cli prepare-refill \
  --launch-manifest /path/to/original-bundle/launch_manifest.json \
  --executor-state-dir /path/to/original-executor-state \
  --archive-root /path/on-same-filesystem/refill-archive \
  --retry-manifest /path/to/refill-launch-manifest.json \
  --arm base \
  --arm trained
```

Repeat `--arm` to select multiple arms. Repeat `--run-id` to further restrict
the selected arms to explicit run IDs. Without `--run-id`, every launch in the
selected arms is classified. Classifier-complete results are omitted from the
retry and remain byte-for-byte in place. An absent path is added directly to the
retry. Every selected non-complete directory that does exist, including an empty
directory, is fully inventoried and atomically renamed into
`refill-archive/results/` before its original launch object is reissued.

The operation rejects path escape, symlinks, special files, an existing archive
or retry output, and a cross-filesystem archive move. It writes create-only,
self-hashed `refill_plan.json` and `refill_receipt.json` records; the receipt
contains every archived tree's full inventory and SHA-256. The retry manifest is
also self-hashed and its `launches` entries are exact objects from the original
manifest. Run it with a fresh executor state directory because its manifest hash
intentionally differs from the original subset source:

```bash
python -m harness_posttrain_eval.cli run-bundle \
  --launch-manifest /path/to/refill-launch-manifest.json \
  --state-dir /path/to/new-refill-executor-state \
  --working-directory "$PWD" \
  --jobs 32 \
  --spawn-stagger-seconds 10
```

`block_seed` is a deterministic scheduling/block identity, not a model-sampling
seed. The production harness exposes no provider-seed control; inference draws
are therefore independent within matched task/repetition blocks, and the
analysis does not claim common-random-number pairing.

After every launch is complete, perform fresh strict rescoring and build the
analysis observations. Behavioral no-purchase runs remain in the denominator at
zero. So does a zero-step failure to compile the required contract after the
fixed four parse/semantic repair attempts: those attempts are part of the
measured harness protocol, not a resource backstop. Provider authentication,
rate, deployment, timeout, navigation, server, and browser failures remain
infrastructure-invalid. Missing or infrastructure-invalid runs are listed in a
separate create-only conversion audit and make the command exit nonzero.

```bash
python -m harness_posttrain_eval.cli convert-results \
  --launch-manifest /path/to/final-run-bundle/launch_manifest.json \
  --frozen-manifest /path/to/frozen-inference-manifest.json \
  --repo-root "$PWD" \
  --observations-output /path/to/observations.jsonl \
  --audit-output /path/to/observation_conversion_audit.json
```

Then run the matched analysis:

```bash
python -m harness_posttrain_eval.cli analyze-final \
  --matrix training/harness_posttrain_eval/manifests/final_matrix.json \
  --observations /path/to/observations.jsonl \
  --frozen-manifest /path/to/frozen-inference-manifest.json \
  --launch-manifest /path/to/final-run-bundle/launch_manifest.json \
  --conversion-audit /path/to/observation_conversion_audit.json \
  --repo-root "$PWD" \
  --selected-arm trained \
  --json-output /path/to/final_report.json \
  --markdown-output /path/to/final_report.md
```

The report is create-only and records the exact frozen campaign, matrix,
endpoint, launch, observation, and conversion-audit hashes. Behavioral failures
stay in the denominator. Missing runs, unresolved infrastructure failures,
source/inference drift, bound safety limits, lossy context, or absent strict
scores prevent a success claim.
