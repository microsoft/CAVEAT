# B200 cluster workflow

This is the repository-local, live-tested workflow for Bonete. It supersedes
the removed onboarding PDF, whose operational values were stale and internally
inconsistent.

## Verified configuration

- Kubernetes context: `oidc@msr02`
- Namespace / Volcano queue: `bonete61`
- Vast PVC: `pvc-vast-bonete61`, mounted in jobs at `/data`
- Current PVC capacity: 1000 TiB
- Workstream: `socialreasoning`
- Required priority: `p0` (`high`); lower-priority values are rejected
- Priority mapping used by the scheduler: `p0` = `high`, `p1` = `low`
- Per-user output base: `/data/runs/<alias>/`

Both scheduler classes were connectivity-tested on 2026-08-07. The current
helper deliberately enforces the stricter operating policy: every scientific,
upload, download, and internal source-transfer pod uses P0.

## One-time setup

The machine already has `kubectl`, `az`, `azcopy`, `vsubmit.sh`, and the Bonete
kubeconfig. Run:

```bash
cluster/b200/b200 setup .env
cluster/b200/b200 doctor
```

`setup` creates or updates the namespaced Secret
`<alias>-hf-token`. The helper injects `HF_TOKEN` into jobs with a Kubernetes
`secretKeyRef`; it never passes the token through `TRANSFER_VARS`. The setup
path extracts only the `HF_TOKEN` entry into a mode-0600 temporary input, so no
other dotenv entries are copied into Kubernetes or displayed.

Do not remove `.env` from `.gitignore`. The stock `vsubmit.sh` copies the source
tree, and its `TRANSFER_VARS` implementation prints values and writes them into
rendered YAML. Never put `HF_TOKEN` in `TRANSFER_VARS`.

If cluster authentication expires, refresh it with the OIDC/browser flow and
rerun `cluster/b200/b200 doctor`. The helper deliberately does not automate
interactive identity login.

## Submit a job

The helper wraps the installed Volcano tools, stages `SOURCE_DIR` on Vast,
injects the Hugging Face secret safely, labels the job, submits it, streams
logs, and returns nonzero on pod failure.

`submit` is a foreground, terminal-state operation. The repository wrapper
captures the generated scientific job ID immediately after `kubectl create`,
then takes over supervision from the installed helper. It does not return just
because a `kubectl logs -f` stream ends, and it has no wall-clock timeout while
the Volcano job remains queued or running. Log streams are reconnected while a
separate 30-second status poll continues. Each individual API check has a
15-second request timeout; transport failures are retried indefinitely. If the
created object is genuinely absent for three successful consecutive checks,
the foreground command fails instead of claiming completion. These intervals
can be changed for engineering tests with `B200_MONITOR_INTERVAL_SECONDS` and
`B200_MONITOR_MISSING_LIMIT`; scientific runs should use the defaults.

A zero return code requires a terminal `Completed`/`Succeeded` Volcano state,
at least one inspectable terminal pod, every pod in `Succeeded`, and a zero
exit code from every reported application container. Failed, aborted,
terminated, missing, or nonzero-exit jobs return nonzero. The submission
manifest is written only after this check and records the terminal transition
time, observation time, pod phases, container exit codes/reasons/finish times,
and the exact remote output path. Thus the manifest's `finished_at` and
`return_code` describe the cluster job, not the lifetime of a log connection.

```bash
SOURCE_DIR=. cluster/b200/b200 submit experiment-01 python train.py --config config.yaml
```

P0 is the default and the only accepted priority:

```bash
SOURCE_DIR=. cluster/b200/b200 submit urgent-smoke python3 train.py
```

Common tuning variables accepted by the installed submitter include:

```bash
NODES=1 \
GPUS_PER_NODE=8 \
NPROC_PER_NODE=8 \
CONTAINER_IMAGE_PATH=docker.io/sytelus/gpu-devbox@sha256:24e4632c1fb555ecb7bf57064313b7a784ae43286defa167534fc2d798711a39 \
SOURCE_DIR=. \
cluster/b200/b200 submit training-run python train.py
```

The wrapper rejects mutable image tags. Set `CONTAINER_IMAGE_PATH` to the exact
image used by a frozen experiment, in `repository@sha256:<digest>` form. The
default is the digest-resolved image from the repository's successful B200
smoke test. Transfer pods use an independently pinned BusyBox image.

For scientific jobs, the wrapper injects that validated immutable value as
`LAST_CONTAINER_IMAGE_DIGEST` inside every pod. Experiment code must bind this
value into its provenance and fail closed when it is absent; the launcher-side
`CONTAINER_IMAGE_PATH` is not otherwise available in the container.

The aggregate allocation has a hard cap of sixteen B200s. Before staging and
again immediately before creation, the wrapper sums GPU requests across every
nonterminal job owned by the current user, including queued jobs. An atomic
namespaced lock prevents two local submissions from both passing the check.
`B200_MAX_GPUS` may lower the cap but cannot raise it. Prefer coordinated
single-node jobs when the workload requires eight-way training; the aggregate
guard permits at most two full-node allocations, or an equivalent mix.

Scientific submissions require a clean source tree. For a clearly labeled
engineering-only diagnostic, `B200_ALLOW_DIRTY_SOURCE=1` permits a dirty tree;
its exact staged-tree digest and dirty flag are still recorded. Do not use that
override for pilot or confirmatory runs.

For a non-distributed command, the helper defaults to `USE_TORCHRUN=0` and
`INSTALL_PACKAGE=0`. Set either to `1` when needed. The installed submitter's
full-node resource defaults are large; override `CPU_REQUESTS`,
`MEMORY_REQUESTS`, `RDMA_REQUESTS`, and `MEMORY_SIZE_LIMIT` only when the job's
requirements are understood.

Job code can write to `$REMOTE_JOB_OUT_DIR`, which resolves beneath
`/data/runs/<alias>/`. The submitter prints the exact path at startup. Preserve
that path for downloading results.

### Resume one exact scientific output

Fresh submission remains the default: the helper creates a new timestamped
directory. A recovery submission may instead set both
`B200_RESUME_JOB_OUT_DIR=/data/runs/<alias>/...` and
`B200_RESUME_IDENTITY_SHA256=<64-lowercase-hex>`. The target must stay beneath
the submitting user's output root and contain only safe path components. Do not
guess the digest; use the scientific package's launcher, which derives it from
the exact clean source Git SHA, immutable container-image digest, campaign
path, and hashes of every campaign config.

Recovery is fail-closed in two places. Before the source archive is uploaded,
the private transfer helper reads the existing root
`campaign_identity.json` from the PVC and requires its file SHA-256 to equal
the supplied digest. The transfer is a tar overlay and does not delete prior
checkpoints or results. Once the pod starts, the scientific entrypoint derives
the identity again and requires byte-for-byte equality before writing config
snapshots or provenance. A missing or changed identity aborts without an
overlay. The installed tools in `~/.local/bin` are never modified; these guards
are applied only to the submission's private helper copies.

Each invocation writes an ignored local record beneath `.b200/manifests/`.
Scientific records include the exact command argv and hash, job ID, frozen
image digest, allocation and P0 class, Git SHA/dirty flag, staged-tree hash,
hashes of command-line config files, source-transfer job ID, rendered Volcano
manifest, API-retrieved Volcano object, timestamps, and return code. Upload and
download job IDs are appended to `transfers.jsonl`. Recovery records also carry
the exact reused output path and campaign-identity digest. Copy these records into the
redacted experiment artifact directory before reporting a run.

## Move data

Upload a local directory to a PVC-relative path:

```bash
cluster/b200/b200 upload ./dataset datasets/my-dataset
```

The data is then visible to jobs at `/data/datasets/my-dataset`.

Download a run directory:

```bash
cluster/b200/b200 download \
  runs/t-yuxuanli/JOB-RUN-DIRECTORY \
  .b200/results/JOB-RUN-DIRECTORY
```

Uploads and downloads use pinned, P0, short-lived CPU transfer jobs and clean
them up.
Archive or shard collections of many small files before transfer when possible.
Vast is shared working storage, not durable archival storage; copy important
outputs back promptly.

## Observe and control jobs

```bash
cluster/b200/b200 status
cluster/b200/b200 status FULL_JOB_NAME
cluster/b200/b200 logs FULL_JOB_NAME
cluster/b200/b200 cancel FULL_JOB_NAME
```

`cancel` requires one exact job name. Delete the Volcano job rather than an
individual pod, because the controller may recreate deleted pods.

## Smoke test

The test fixture is in `cluster/b200/examples/`:

```bash
SOURCE_DIR=cluster/b200/examples \
GPUS_PER_NODE=1 NPROC_PER_NODE=1 \
CPU_REQUESTS=8 MEMORY_REQUESTS=64Gi RDMA_REQUESTS=1 MEMORY_SIZE_LIMIT=8Gi \
cluster/b200/b200 submit connectivity-smoke python smoke.py
```

It checks CUDA through PyTorch, authenticates to the Hugging Face `whoami`
endpoint, reads `input/message.txt`, and writes `result.json` to the run root.

## Corrections incorporated from the old onboarding guide

- Use `bonete61` and `pvc-vast-bonete61`; older examples using
  `msraif-shared` belong to older or different infrastructure.
- The live PVC reports 1000 TiB, not the old approximation of 500 TB.
- `kubectl get vcjob` is valid, but the discovered API resource is plural
  `jobs` in `batch.volcano.sh/v1alpha1`. RBAC checks should use
  `jobs.batch.volcano.sh`, not `vcjobs.batch.volcano.sh`.
- The valid `kubectl` troubleshooting command is `kubectl describe pod NAME`,
  not `kubectl get describe NAME`.
- The installed scripts are under `~/.local/bin`, not `/usr/local/bin`.
- The installed submitter requires `PROJECT_NAME`; this helper supplies the
  verified workstream automatically.
