# Bonete B200 helper

This folder contains the reusable B200 launcher copied from the
`calibrated_influence` project. Run it from the `preference-fidelity` repository
root so that `.env`, `SOURCE_DIR`, and `.b200/manifests` resolve in this project:

```bash
cluster/b200/b200 setup .env
cluster/b200/b200 doctor

SOURCE_DIR=. cluster/b200/b200 submit my-job python3 your_script.py
```

The helper currently enforces the source project's operational safety policy:
Bonete namespace `bonete61`, workstream `socialreasoning`, P0/high priority,
immutable container-image digests, a maximum aggregate allocation of sixteen
B200s, clean scientific source trees, and Kubernetes-secret injection for the
Hugging Face token. Individual training jobs remain explicitly sized; the
harness-distillation campaign uses one coordinated eight-B200 node.

Fresh jobs receive a new output directory. Exact-output recovery is supported
through paired `B200_RESUME_JOB_OUT_DIR` and `B200_RESUME_IDENTITY_SHA256`
values, with identity verification before any PVC overlay; normally these are
computed and supplied by the scientific package launcher rather than by hand.

See [GUIDE.md](GUIDE.md) for the full cluster workflow. The later LAST-specific
sections document the source campaign that motivated some safeguards; they are
background rather than commands for this repository.

The helper relies on the Bonete tools installed in `~/.local/bin` and the
machine's existing Kubernetes/OIDC configuration. It does not copy credentials
between repositories: `setup` reads this repository's ignored `.env` and
updates the existing per-user Kubernetes Secret without printing its value.
