# Mode-evidence ablation campaign

This isolated package freezes the 120-run prospective matrix described by the
mode-evidence protocol. It does not edit production tasks, catalogs, scaffold
registration, or harness code. Objective reversal comes from a hashed sidecar;
the prompt-only and no-coverage names are registered only through
`ablations/harness_components` on the launched command's `PYTHONPATH`.

The launcher is deliberately block-oriented and create-only. The 120 rows are
packed into five sequential blocks of 30, 20, 30, 20, and 20 rows. It fills but
never exceeds V19's 15-browser machine backstop, so a 30-row block executes in
two waves. A fresh probe is required before every block, a completed block is
bound into the next probe, and partial evidence blocks any silent relaunch.

## Preflight and freeze

```bash
.venv/bin/python ablations/mode_evidence_campaign/campaign.py verify-static

.venv/bin/python ablations/mode_evidence_campaign/campaign.py prepare \
  --campaign-dir results/mode_evidence_ablation_v1 \
  --campaign-id mode_evidence_ablation_v1 \
  --base-port 17000 \
  --cert-report /absolute/path/to/hard_certification.json \
  --lockdiff-report /absolute/path/to/lockdiff_after.json \
  --sol-regions gcr/shared,msraif/shared,redmond/interactive \
  --terra-regions gcr/shared,msraif/shared,redmond/interactive

.venv/bin/python ablations/mode_evidence_campaign/campaign.py verify \
  --campaign-dir results/mode_evidence_ablation_v1
```

`prepare` freezes benchmark artifacts, source hashes, certification and
lockdiff evidence, routing, the V19 runtime/cap contract, the analysis protocol,
and the exact schedule. It is idempotent only for an already valid freeze; a
partial directory must be preserved and replaced with a new campaign path.

## Probe and launch one block

The frozen stages are `sol_b01`, `sol_b02`, `sol_b03`, `terra_b04`, and
`terra_b05`.

```bash
.venv/bin/python ablations/mode_evidence_campaign/campaign.py probe \
  --campaign-dir results/mode_evidence_ablation_v1 \
  --stage sol_b01 --label sol_b01_$(date -u +%Y%m%dT%H%M%S)

.venv/bin/python ablations/mode_evidence_campaign/campaign.py launch-block \
  --campaign-dir results/mode_evidence_ablation_v1 \
  --block 1 --checkpoint '<the label above>' \
  --confirm LAUNCH-FROZEN-MODE-EVIDENCE-V1
```

The explicit confirmation protects against accidental launch. The current
package intentionally does not auto-launch during preparation or verification.

## Monitor and report

```bash
.venv/bin/python ablations/mode_evidence_campaign/campaign.py status \
  --campaign-dir results/mode_evidence_ablation_v1

.venv/bin/python ablations/mode_evidence_campaign/campaign.py report \
  --campaign-dir results/mode_evidence_ablation_v1
```

Reporting reads only manifest-named paths. It fails closed unless all 120 runs
have strict rescoring, valid create-only receipts, exact task/scaffold/model
identity, complete V19 limit audits, and zero safety/lossy-limit touches.

Operational smokes remain a preregistered prelaunch gate. They should be run
and archived before the first measured block; this launcher does not silently
reinterpret a measured run as a smoke or replacement.
