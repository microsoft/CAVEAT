# S33 training recipe: from raw Qwen3.5-27B to the served checkpoint

## Executive summary

The S33 checkpoint is **not** a model trained from scratch, and it is not the
result of one homogeneous algorithm. It is the endpoint of a staged,
harness-coupled post-training pipeline:

1. start from the pinned raw `Qwen/Qwen3.5-27B` BF16 checkpoint;
2. perform broad procedural **supervised fine-tuning (SFT)** with LoRA;
3. select and merge the best broad SFT adapter into the raw model;
4. perform a reward-filtered, on-policy **ReST/DAgger-style SFT** refinement;
5. continue the resulting LoRA through a receipt-linked laptop-focused branch,
   including fixed-v7, step-25 Sol/DAgger SFT, and steps 26–32, using a mixture
   of chosen-action cross-entropy, selectively bounded rejected-token
   unlikelihood, replay/retention examples, and fresh teacher/on-policy browser
   states;
6. train steps 33–35 with trajectory-level on-policy action distillation; and
7. publish the **step-33 microcheckpoint**, rather than step 35, as the model
   called S33.

The most accurate one-line classification is:

> **A LoRA model trained by a sequence of SFT and DAgger/on-policy-distillation
> stages; the S33 update itself is on-policy data collection followed by
> supervised action-token cross-entropy, not policy-gradient RL.**

In short, S33 is **OPD in how its newest data was collected, but SFT/behavior
cloning in how its weights were optimized**.

## 1. What exactly is “the S33 model”? 

The evaluated alias is:

```text
qwen35-browser-action-step35-trajectory-proximal-a-step33-8eb0b3bc397d-exact-lora
```

Its trainable artifact is the step-33 LoRA adapter:

```text
tree SHA-256: 8eb0b3bc397d8dde41a294acf067be51d5e14d8c7de315e6a696264e3c5cd4aa
files:         2
bytes:         933,975,509
rank:          64
alpha:         128
dropout:       0
bias:          none
task type:     causal language modeling
```

The adapter targets 12 projection families:

```text
q_proj, k_proj, v_proj, o_proj,
gate_proj, up_proj, down_proj,
in_proj_a, in_proj_b, in_proj_qkv, in_proj_z, out_proj
```

It is served dynamically on top of the campaign's selected merged parent—not
directly on top of untouched raw Qwen weights. Therefore these two expressions
are not equivalent:

```text
raw Qwen + S33 adapter                  # wrong composition
selected broad-SFT parent + S33 adapter # actual composition
```

The adapter configuration continues to name the selected merged parent, whose
tree is:

```text
5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca
```

## 2. End-to-end lineage

```text
Raw Qwen3.5-27B (fc05daec..., BF16)
  |
  |  broad procedural LoRA SFT: 3 candidate mixtures × 20 updates
  v
Selected broad SFT candidate
  |
  |  behavior-gated BF16 merge into raw weights
  v
Selected merged parent (tree 5939382f...)
  |
  |  20-update reward-filtered on-policy ReST/DAgger SFT
  v
Refinement step 20 LoRA
  |
  |  receipt-linked same-task laptop adaptation branch:
  |  fixed-v7 step 23 -> Sol/DAgger step 25 -> continued steps 26–29
  v
Step 28
  |
  |  step 29: paired Buy-Now/action preference update
  |  step 30: HERO50 paired semantic update
  |  step 31: strict checkout/cart update
  |  step 32: discovery/rebind/cleanup bridge update
  v
Step 32 adapter (tree a2c2fe90...)
  |
  |  steps 33–35: trajectory-level on-policy action distillation
  v
S33 = the step-33 microcheckpoint (tree 8eb0b3bc...)
```

The numbered “steps” after the broad parent are cumulative optimizer-update
labels in the continued LoRA/DCP lineage. They are not 33 epochs, and they are
not 33 independent end-to-end training runs.

## 3. Stage 0 — pinned raw foundation model

The original foundation checkpoint is:

```text
model:     Qwen/Qwen3.5-27B
revision:  fc05daec18b0a78c049392ed2e771dde82bdf654
tree:      4fb5dfc7f0e69f2812aa8ec112a888531227d76721ca20855a3852940e422c5a
precision: BF16
```

The campaign preserves Qwen's tokenizer, processor, chat template, reasoning
format, and tool-call format. It does not pretrain the language model again.

## 4. Stage 1 — broad procedural harness-coupled SFT

### Goal

The first post-training stage teaches the raw model to cooperate with the
`browseruse-deliberative` harness at interfaces where the raw model was weak:

- compile literal user requirements into a complete decision contract;
- recover from rejected or incomplete structured drafts;
- emit valid browser actions;
- produce complete decision-checkpoint calls.

The aim was not to memorize Amazon products or distill the whole harness into
the weights.

### Data generation and split

- 1,600 deterministic procedural training tasks were generated.
- Each task produced two literal-contract examples, two recovery examples, and
  one checkpoint rehearsal.
- This yielded an 8,000-row raw pool.
- Final candidate corpora contained 4,800 rows each.
- Procedural train, validation, and test families were kept separate.
- Amazon-five facts, private catalog fields, evaluator labels, and storefront
  operation tokens were excluded from this broad corpus.

### Candidate mixtures

Three independent LoRA candidates were trained for 20 updates:

| Candidate | Contract | Recovery | Checkpoint rehearsal |
|---|---:|---:|---:|
| balanced | 40% | 40% | 20% |
| protocol-heavy | 60% | 30% | 10% |
| recovery-heavy | 30% | 60% | 10% |

Checkpoints were predeclared at updates 5, 10, and 20, producing nine
candidates for procedural validation. Selection prioritized exact contract
semantics, then structured syntax validity, then checkpoint validity. No
Amazon evaluation outcome was used for this selection.

### Optimizer type

This was ordinary teacher-forced supervised next-token training of LoRA
parameters. It was not reinforcement learning.

## 5. Stage 2 — select and merge the broad SFT parent

The selected broad adapter was numerically checked and folded into the pinned
raw BF16 model. Publication required:

- a nonzero change from the raw model;
- close agreement between dynamic-LoRA and merged logits;
- successful standalone Hugging Face reload;
- preserved processor, tokenizer, and chat-template behavior.

This produced the 54.7 GB selected merged parent with tree `5939382f...`.
All later S33-family adapters are defined relative to this parent.

This distinction explains why the portable S33 package contains both the
selected parent and the adapter. The final S33 LoRA could not simply be merged
into BF16 while satisfying the frozen endpoint-equivalence gate; dynamic LoRA
remains the scientifically faithful representation.

## 6. Stage 3 — 20-update reward-filtered ReST/DAgger refinement

### Rollout collection

The selected broad SFT model was run on 64 fresh procedural contract prompts,
with four samples per prompt (256 samples total):

- 75% truthfully steered;
- 25% clean/unsteered.

Operationally correct successes were retained. Each failed sample received one
authored correction based only on the public contract. Equivalence ignored
irrelevant prose/internal IDs but required all literal feasibility and ranking
requirements to match.

### Training corpus

The refinement corpus was materialized with a fixed mixture:

- 60% corrections;
- 30% successful on-policy samples;
- 10% checkpoint rehearsals.

It trained a rank-64, alpha-128 LoRA for 20 updates at learning rate `5e-6`.

### Algorithm classification

This stage is best described as **reward-filtered ReST/DAgger-style SFT**:

- the current policy generates the states/actions (on-policy collection);
- a correctness filter and teacher corrections choose targets;
- optimization is supervised cross-entropy;
- there are no policy-gradient advantages or PPO/GRPO updates.

An optional 12-update reverse-KL OPD smoke existed in the campaign design but
was disabled and was not required for the resulting model.

## 7. Stage 4 — receipt-linked laptop-focused continuation

After broad procedural training, the campaign entered a same-task Amazon
laptop development/adaptation phase. This is an important scientific caveat:
S33 is no longer an Amazon-zero-shot model.

The exact parent chain—not merely similarly named experiments in the
repository—includes:

- a three-update fixed-v7 continuation from step 20 to step 23, at learning
  rate `2e-6` with a constant, no-warmup schedule;
- a receipt-linked step-25 `sol-dagger-sft` adapter;
- a step-25-to-step-29 paired Buy-Now/unlikelihood continuation containing
  eight chosen and eight rejected exact-laptop states per update for four
  updates.

The techniques in this linked branch shared three properties:

1. LoRA/DCP state was continued rather than restarting from raw weights;
2. training targets were structured browser decisions/actions;
3. optimization remained supervised, even where states or labels were produced
   by an on-policy rollout and teacher-correction process.

The adaptation used laptop development data, while office-chair and the other
non-laptop Amazon categories were kept out of checkpoint selection at this
point. Consequently, laptop results measure same-task transfer/adaptation;
non-laptop categories are the stronger generalization test.

The repository contains many candidate and repair experiments around this
lineage. They must **not** be inferred to be ancestors from their names or step
numbers. Only artifacts connected by the immutable `parent_candidate.path`,
checkpoint bridge, and training receipts belong to S33. The descriptions above
are limited to that connected chain; failed, retired, sibling, and
evaluation-only candidates are not part of the weights.

## 8. Stage 5 — the final semantic micro-update chain

### Step 29 — paired Buy-Now/action preference update

Step 29 resumed the exact step-25 Sol/DAgger adapter and trained for four
updates. Its immutable plan labels the source
`sealed_exact8_derived_collection_r2`: eight chosen and eight rejected states,
distributed two per each of `graded`, `graded3`, `graded4`, and `mixed`, reused
at each update. Direct corpus inspection finds laptop/Amazon markers in every
materialized row (16/16 serialized chosen/rejected rows). It used paired
chosen-action cross-entropy plus bounded rejected-token unlikelihood to prefer
the correct transaction/navigation action over a stable wrong relative target.

### Step 30 — HERO50 paired semantic update

Step 30 resumed step 29 and performed one update:

```text
learning rate: 5e-6
states:        12
chosen:        12
rejected:      12
on-policy:     true
policy grad:   false
```

The corpus combined fresh exact laptop-HERO states with sealed shortcut
retention examples across `graded`, `graded3`, `graded4`, and `mixed` variants.
Loss mass was:

- 30% chosen action cross-entropy;
- 40% chosen pre-action-tail cross-entropy;
- 30% rejected semantic-token unlikelihood.

### Step 31 — strict checkout/cart update

Step 31 resumed step 30 and performed one update at `3e-6`:

```text
states:       18
chosen:       18
rejected:     16
chosen-only:   2
on-policy:    false for the trainer-only materialized update
policy grad:  false
```

Its training groups covered:

- HERO discovery and checkpoint repair;
- HERO PDP add-to-cart behavior;
- deleting an unexpected add-on from a dirty cart;
- proceeding from a clean cart;
- placing the order;
- retention of useful shortcuts.

Loss mass was approximately 32.6% chosen-action CE, 43.4% chosen-tail CE, and
24% rejected semantic unlikelihood.

### Step 32 — discovery/rebind/cleanup bridge update

Step 32 resumed step 31 and performed one update at `1e-6` over 45 states:

```text
chosen states:       45
rejected targets:    25
chosen-only states:  20
fresh dynamic:       33
sealed retention:    12
on-policy flag:      false for the materialized trainer-only run
policy gradient:     false
```

The four logical groups and their designed mass were:

| Group | States | Loss mass |
|---|---:|---:|
| upstream visible-HERO discovery/rebind | 17 | 45% |
| post-HERO add → view-cart bridge | 11 | 30% |
| multiline dirty-cart cleanup | 15 | 20% |
| proceed/place retention | 2 | 5% |

The total objective coefficients were approximately:

- 35.34% chosen-action CE;
- 47.12% chosen pre-action-tail CE;
- 17.54% rejected semantic unlikelihood.

The rejected term used a bounded token-level loss:

```text
-log(1 - 0.95 * p_theta(rejected_token | rejected_prefix))
```

Only semantically relevant rejected values were masked into that term; prompt
tokens and generic JSON syntax were excluded. This is closer to targeted
unlikelihood/preference shaping than to DPO: no pairwise sequence-level DPO
log-ratio objective was used.

## 9. Stage 6 — steps 33–35 trajectory-proximal action distillation

This is the run from which S33 itself was extracted.

### Data

The corpus contained 73 trajectory states:

- 28 fresh sequential actions collected by rolling in step 32;
- 45 sealed step-32 action-replay states.

The intended source mass was 60% fresh trajectory actions and 40% replay.
Variants were represented as:

| Variant | States |
|---|---:|
| graded | 19 |
| graded3 | 20 |
| graded4 | 17 |
| mixed | 17 |

There were no held-out or evaluation states in this trainer corpus.

Direct inspection of the immutable source data confirms that the S33 corpus is
laptop/Amazon-focused—not merely named that way:

- all 28/28 `fresh_hero50_pairs.jsonl` rows contain laptop/Amazon storefront
  content;
- all 45/45 `shortcut_retention_pairs.jsonl` rows do as well;
- therefore all 73/73 states used at every S33–S35 update are laptop/Amazon
  browser-action states;
- the step-32 fresh file is likewise 33/33 laptop-marked.

Examples in the actual serialized supervision include laptop search results,
`EXP-LAPTOP` PDPs, Add to Cart, the Amazon-style cart, protection-plan removal,
checkout, and order placement. This is stronger evidence than directory or
experiment naming alone.

### Supervision target

For each state, supervision covered only the **entire structured BrowserUse
action JSON**. It did not supervise hidden rationale, memory, or the next-goal
prose. Trajectories were treated atomically, and weights were normalized across
trajectory, subgoal, state, and action-token levels so long actions or long
trajectories did not dominate merely by token count.

### Optimization

```text
source step:        32
candidate steps:    33, 34, 35
optimizer updates:  3
learning rate:      2e-7
fresh optimizer:    yes
fresh scheduler:    yes
fresh dataloader:   yes
LoRA:               rank 64, alpha 128
on-policy data:     yes
policy gradient:    no
```

Although the implementation retained the general paired/unlikelihood loss
machinery, the active coefficients for this run were:

```text
chosen action CE:       1.0
chosen tail CE:         0.0
rejected unlikelihood:  0.0
RL/advantage tokens:    0
```

Thus the actual S33 weight update is pure action-token behavior cloning on a
mixture of fresh on-policy and replay states. Calling it PPO, GRPO, DPO, or
policy-gradient RL would be incorrect.

### Why publish step 33?

The training job emitted microcheckpoints at steps 33, 34, and 35. S33 is the
first microcheckpoint, selected for development probing as profile A. It is not
the nominal step-35 endpoint, despite the run name containing “step35.” Its
identity is pinned by the step-33 inventory and adapter tree, so later weights
are not implicitly included.

## 10. What the loss functions mean

Across the full lineage, three supervised signals appear:

### Chosen-action cross-entropy

Standard teacher-forced negative log likelihood on the correct structured
browser action. This is ordinary SFT/behavior cloning.

### Chosen pre-action-tail cross-entropy

Some corrective stages additionally trained a bounded portion immediately
preceding the action. This helped preserve enough local decision context while
avoiding full rationale imitation.

### Rejected semantic-token unlikelihood

For paired states, selected wrong semantic values were discouraged with the
bounded loss above. It was not applied indiscriminately to all JSON tokens.
This term was active in steps 30–32 but exactly zero in the S33–S35 trajectory
run.

## 11. What this recipe is—and is not

| Label | Applies? | Explanation |
|---|---|---|
| SFT | **Yes** | All weight updates ultimately optimize supervised token losses. |
| Behavior cloning | **Yes** | Especially exact for steps 33–35: imitate chosen structured actions. |
| DAgger/ReST | **Yes, for data collection** | Current-policy states were sampled, filtered, and corrected before supervised updates. |
| OPD | **Reasonable shorthand** | Particularly for the on-policy trajectory-distillation stages, provided one notes the optimizer is CE. |
| DPO | **No** | No DPO log-ratio objective against a frozen reference policy. |
| PPO/GRPO | **No** | No policy-gradient advantage optimization in the S33 lineage described here. |
| RLHF | **No, in the conventional sense** | Rewards/filters influence example selection, not a policy-gradient update. |
| Continued pretraining | **No** | No unsupervised next-token corpus continuation of the full model. |
| Full-parameter tuning | **No** | The treatment is rank-64 LoRA on a fixed parent. |

## 12. Leakage and interpretation caveats

The original broad stage was explicitly leakage-resistant and procedural.
However, the later receipt-linked step-23-through-step-33 lineage deliberately used laptop
development trajectories and HERO-related states. Therefore:

- laptop evaluation is not a clean unseen-domain generalization test;
- non-laptop Amazon categories are a better transfer test;
- the model may learn workflow concepts—enumeration, rebind, cart inspection,
  add-on deletion—but may also acquire laptop-specific selection tendencies;
- evaluation harness improvements and model post-training must be separated in
  matched ablations, because the model was trained to cooperate with the
  deliberative harness interface.

## 13. Reproducibility and artifact provenance

Key locally available evidence:

- initial campaign description:
  `training/hpt-action-launch-6ab80e4/README.md`
- fixed-v7 continuation release:
  `results/harness_posttrain_fixed_v7_eval_20260813/orchestration/fixed_v7_training_release_descriptor.json`
- S33 handoff and exact adapter identity:
  `results/harness_posttrain_campaign2_20260814/orchestration/c2_step36_outcome_candidate_handoff_s33_w2.json`
- endpoint-faithful local package:
  `results/harness_posttrain_campaign2_20260814/profilea_s33_8eb0_endpoint_faithful_composite_local_r1/`

Key immutable cluster evidence:

```text
/data/harness-posttrain/.../refinement/training_receipt.json
/data/harness-posttrain/.../final/exact_lora_manifest.json
/data/runs/.../step32_training_run_r2/training/plan.json
/data/runs/.../step32_training_run_r2/training/training_receipt.json
/data/runs/.../step36_training_run_r1/training/plan.json
/data/runs/.../step36_training_run_r1/training/microcheckpoint_inventories/step_33.json
```

The exact S33 adapter is also preserved locally inside the endpoint-faithful
composite. Its receipt records `single_weight_merged=false`: the package is a
self-contained parent-plus-adapter model, matching how the evaluated endpoint
was served.

## Bottom line

S33 should be described as a **multi-stage LoRA post-training result**:

> broad procedural SFT → selected-parent merge → reward-filtered on-policy
> DAgger SFT → laptop-focused paired corrective SFT/unlikelihood updates →
> trajectory-level on-policy action distillation, with step 33 exported as the
> evaluated microcheckpoint.

If forced to choose between “SFT” and “OPD,” the technically precise answer is:

> **The optimizer is SFT; the later data-generation loop is OPD/DAgger.**
