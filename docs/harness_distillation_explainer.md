# Explaining the harness-distillation training campaign

This document is for explaining the training campaign to someone who already
knows AgentArena, the standard and hard benchmarks, the failure-mode analysis,
and the improved deliberative browser harness. It therefore starts from the
question that followed that work: can we move the useful decision procedure
from an external harness into the model itself?

## Part I: how to explain it verbally

### One-sentence version

We run the model on new shopping tasks, let a frozen copy of the same model make
decisions with the improved harness's structured working state, and train the
ordinary unassisted model to reproduce those better decisions from raw browser
history alone.

### Short spoken version

> Our improved harness made agents substantially better by forcing five things:
> retain the literal instruction, cover the relevant option set, resolve
> important facts, compare feasible candidates without prominence bias, and
> verify the final transaction. The training question is whether those habits
> can become part of the model rather than remaining external scaffolding.
>
> We use Qwen3.6-27B as both student and teacher. The student receives exactly
> the ordinary browser-use context. A frozen copy of the same model receives
> that context plus a structured summary of the five harness phases, constructed
> only from storefront evidence already visible to the agent. This isolates the
> value of the harness: the teacher is not a larger model and receives no hero
> identity, evaluator result, hidden catalog, or future observation.
>
> We first use a small amount of supervised fine-tuning to give the student
> support for good search, bookkeeping, tool use, and stopping actions. We then
> use on-policy context distillation. The current student attempts fresh tasks;
> the scaffolded frozen teacher evaluates and corrects decisions at the states
> the student actually reaches; and we update the student on those signals. We
> repeat because every update changes the states the student will visit next.
>
> The training tasks are procedurally generated shopping problems from domain
> families disjoint from Amazon. They include clean and truthfully steered
> conditions, large catalogs, multiple equally weighted preferences,
> satisficing tasks, and no-qualified-product cases. This trains the abstract
> decision policy without training on Amazon products or benchmark answers.
>
> We select a checkpoint only on a sealed procedural suite. Once selection is
> immutable, we evaluate it on Amazon's five products using the unchanged
> baseline browser-use harness. Therefore, if it beats the frozen base model,
> the improvement must be in the model's policy rather than an inference-time
> harness advantage.

### The central intuition

The improved harness is an external state machine for a long-horizon,
partially observed decision problem. It prevents the agent from losing the
instruction, stopping before closing the option set, confusing unknown facts
with favorable facts, or committing without a final audit.

The training campaign treats that state machine as privileged *training-time
context*. The model is asked to amortize it: infer and maintain the equivalent
state internally from its ordinary interaction history. In compact form:

```text
raw browser history + external decision state  ->  scaffolded teacher decision
raw browser history                            ->  student learns that decision
```

"Privileged" here means available only to the teacher, not unavailable in the
environment. Every field must be derived from public observations at or before
the current decision.

### Why the process has both SFT and on-policy distillation

SFT supplies an initial repertoire of useful actions. It is efficient, but it
mostly covers states found in collected demonstrations. In a browser task, one
early student error changes all subsequent pages and decisions, so purely
offline imitation suffers from compounding distribution shift.

On-policy distillation addresses this by collecting trajectories from the
current student. The teacher then supervises the actual states produced by the
student's own mistakes. This is the DAgger idea applied as context
distillation: repeatedly train on the learner's state distribution rather than
only the demonstrator's state distribution.

### What claim the procedure supports

The sealed procedural suite is a leakage-safe checkpoint screen. It tests
whether the intended decision behavior transferred, but it is not proof of an
Amazon gain. The definitive claim comes only from the final, held-out Amazon
comparison under the unchanged baseline harness.

## Part II: technical details for follow-up questions

### 1. What exactly is being distilled?

At each model decision, the teacher may receive a structured state with five
sections:

1. **Contract:** literal hard constraints, requested stopping rule, and
   comparative objectives.
2. **Coverage:** the ranking-independent finite frontier, what has been checked,
   and what work remains.
3. **Fact resolution:** known, unknown, and semantically conflicting product
   facts, each tied to public evidence.
4. **Decision:** feasible and nondominated candidates plus explicit rejection
   reasons, with comparative objectives treated as coequal unless the user says
   otherwise.
5. **Precommit:** visible product identity, quantity, add-ons, and all-in total
   immediately before commitment.

These are the operational counterparts of the failure modes and successful
harness mechanisms established in the earlier trajectory analysis and
ablations.

The state is causal: it can cite only evidence observed at or before that turn.
The materialization code rejects future phase state, evaluator fields, oracle
labels, private storefront data, credentials, and unprovenanced claims.

### 2. Why Qwen3.6-27B?

The primary student is `Qwen/Qwen3.6-27B` at the pinned revision
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`.

The choice balances four requirements:

- it is recent and already supports reasoning, chat templates, and tool calls;
- it has enough capability to exhibit meaningful shopping behavior and enough
  headroom to improve;
- 27B is practical for repeated LoRA training and rollout generation on B200s;
- it can act as its own frozen teacher, isolating scaffolding transfer from
  stronger-model distillation.

Qwen3.5-27B is a predeclared fallback only if the primary model fails the
runtime or headroom gate. The vision tower is frozen because the reported
evaluation uses DOM-only observations; the multimodal wrapper and native chat
template are nevertheless preserved.

### 3. What data is generated?

The frozen procedural source bank contains 12,000 truthful shopping tasks:

| Split | Tasks | Domain/schema families | Role |
|---|---:|---:|---|
| Train | 9,600 | 24 | Rollouts and weight updates |
| Validation | 1,200 | 6 | Development and validation source |
| Procedural test | 1,200 | 6 | Sealed procedural evidence |

Split boundaries are by domain and schema family, not a random split of near-
duplicate products. The five Amazon categories, instructions, IDs, catalogs,
and outcomes are forbidden.

The task distribution contains:

- catalogs from 16 to 512 products during training and up to 768 in procedural
  testing;
- 50% clean and 50% truthfully steered conditions;
- 75% best-available, 15% satisficing, and 10% no-qualified-product intents;
- one to four comparative objectives;
- matched counterfactual pairs that reorder the objective wording while
  preserving the correct decision.

The order pairs discourage the common shortcut that treats the first-mentioned
preference as more important. Satisficing and no-qualified cases prevent the
opposite shortcut of always searching indefinitely and always buying something.
No deliberately false product specification is permitted.

The 12,000 tasks define the available universe; they are not all placed into a
single training epoch. The accelerated campaign uses audited compact subsets
for SFT and fresh, disjoint 512-episode pools for each on-policy round.

### 4. How are student and teacher inputs separated?

For a baseline context \(x\) and teacher-only phase state \(s(x)\):

- the **student** receives \(x\);
- the **teacher** receives \(x\) plus \(s(x)\);
- both use the same frozen tokenizer, chat template, model family, tool schema,
  and ordinary browser observations.

The phase state is stored as a separate audited sidecar. It is never serialized
into the student's prompt columns. Corrective SFT rows contain the baseline
messages and tools followed by one teacher-generated assistant decision; the
teacher's phase-state prefix is absent.

This is why the procedure is context distillation rather than prompt training:
the gradient carries the effect of the structured context, but the evaluated
model does not receive that context.

### 5. What happens in the SFT warm start?

The seed data combines two sources:

1. All causal assistant decisions from successful baseline trajectories. These
   preserve native browser actions, tool syntax, reasoning format, and valid
   stopping behavior.
2. Bounded corrective next actions from the frozen scaffolded teacher on usable
   trajectories. The label selection is causal and outcome-blind, with at most
   four corrections per session.

Clean satisficing successes are deliberately rehearsed so that coverage
training does not become "enumerate everything regardless of the instruction."

The nominal SFT configuration is:

- BF16 rank-64 LoRA, alpha 128, dropout 0;
- assistant-token-only loss;
- 65,536-token audited training sequences;
- learning rate \(10^{-5}\), 3% warmup, cosine schedule;
- approximately one token-equivalent pass;
- maximum gradient norm 1.0.

Only model-generated reasoning and browser-action tokens receive loss. User
instructions, storefront observations, and tool results are context rather
than prediction targets. A small LoRA and limited pass reduce catastrophic
forgetting and make every stage inexpensive to merge and inspect.

The accelerated run evaluated early SFT milestones rather than waiting for the
original maximum seed-data plan. Those milestones did not jointly satisfy the
sealed gain and regression guards, so SFT alone did not justify an Amazon
claim.

### 6. What is on-policy context distillation mathematically?

For a state \(x\), the current student samples an exact completion-token
sequence

\[
y \sim p_\theta(y\mid x).
\]

The same token IDs are scored by:

\[
p_\theta(y\mid x)
\quad\text{and}\quad
p_T(y\mid x,s(x)),
\]

where \(p_T\) is the immutable original checkpoint with the teacher-only phase
state. The OPD update minimizes the reverse-KL direction

\[
\mathrm{KL}\!\left(p_\theta(\cdot\mid x)\;\|\;
p_T(\cdot\mid x,s(x))\right)
\]

using a score-function estimate on student-sampled tokens. Intuitively, a
student action is reinforced when the scaffolded teacher assigns it more
probability than the student does and suppressed when the teacher regards it
as less plausible.

The exact sampled token IDs are retained throughout. Hidden reasoning is never
decoded and retokenized, avoiding alignment errors in reasoning and tool-call
tokens.

### 7. Why are corrective labels still needed after reverse KL?

Reverse KL learns primarily from actions that the student already samples. If
the required recovery or search action has almost no student probability, it
may never appear and therefore supplies little useful learning signal.

The frozen teacher consequently also generates explicit corrective next
actions. PRIME-RL v0.7 cannot combine its reverse-KL OPD objective and
corrective cross-entropy in one optimizer step, so each round is implemented
honestly as:

```text
on-policy rollout
    -> reverse-KL OPD
    -> merge
    -> corrective/rehearsal SFT
    -> merge
    -> procedural validation
```

The corrective pass uses an effective learning rate of \(10^{-6}\), derived
from the OPD learning rate \(2\times10^{-6}\) and the preregistered 0.5
corrective weight. Approximately 10% of its token mass is seed rehearsal. This
implements a stability-plasticity tradeoff: learn missing recovery actions
without erasing general browser competence, clean stopping, or tool validity.

### 8. Why use multiple on-policy rounds?

The frozen plan has three rounds, each using:

- 512 usable episodes from a fresh, disjoint task pool;
- outcome-blind episode selection based only on infrastructure completeness and
  a nonbinding safety audit;
- 100 OPD optimizer updates;
- corrective/rehearsal SFT after the OPD update.

After an update, the student takes different actions and reaches different
states. Recollecting trajectories is therefore part of the learning algorithm,
not merely more data generation. Three bounded rounds capture this shifting
distribution while avoiding open-ended benchmark-driven tuning.

Every LoRA is merged into its exact parent checkpoint before the next stage.
The pipeline verifies that each adapter is nonzero, merged logits match adapter
logits, saved-and-reloaded logits match, and the tokenizer and chat template did
not change. The next round therefore starts from the cumulative student rather
than accidentally restarting from the base model.

### 9. How are checkpoints selected?

Amazon outcomes are not used for checkpoint selection. In the accelerated
campaign, candidates are compared on a presealed 120-task procedural panel; 92
best-available tasks contribute to the optimal-product selection rate.

A candidate must show the predeclared material gain—at least five additional
optimal selections among those 92—and pass guards for:

- no binding safety backstop;
- valid basket and transaction behavior;
- no material tool-validity regression;
- no excessive slowdown on successful satisficing tasks;
- no unacceptable clean-task regression.

These guards make the selector deliberately high precision. Failure to pass
does not prove that a checkpoint could not help Amazon; it means the evidence
is insufficient to spend the sealed benchmark as a selection signal.

### 10. What is the definitive evaluation?

After procedural-only selection is frozen, the selected merged model and the
frozen base model are evaluated with identical inference conditions:

- unchanged baseline `browseruse` scaffold;
- no deliberative prompt or `decision_checkpoint`;
- DOM-only observations;
- preserved Qwen reasoning and chat template;
- temperature 1.0, top-p 0.95, and top-k 20;
- 4,000-step and 36,000-second nonbinding safety ceilings.

The Amazon matrix is:

```text
5 products x 4 non-absolute preference levels x 2 conditions x 3 repetitions
= 120 runs per arm
```

Laptop is development-exposed. Office chair, mattress, backpack, and tent form
the 96-run-per-arm confirmatory subset. The primary outcome is optimal-product
selection rate. A successful confirmatory result requires at least a
15-percentage-point combined-condition improvement, a positive lower bound from
the paired task-stratified 95% bootstrap, no more than five points of clean
optimal-selection or valid-purchase regression, and no binding backstop.

### 11. What would and would not count as success?

Success means that the trained weights, under the ordinary baseline harness,
behave more like the externally scaffolded policy on held-out Amazon tasks.

It would **not** be valid to attribute an improvement to internalization if the
evaluated model received:

- the deliberative prompt or phase-state sidecar;
- a special decision tool unavailable to the base arm;
- hero identities, evaluator feedback, or hidden catalog fields;
- Amazon outcomes during checkpoint selection;
- a different browser scaffold, sampling policy, or resource ceiling.

The scientific comparison is therefore not "better training system versus
baseline system." It is **frozen base weights versus trained weights inside the
same baseline system**.

## Useful answers to common questions

**Is the teacher stronger than the student?**  Not in base capability. It is a
frozen copy of the same checkpoint with better organized, public-derived
context. That is intentional so the experiment isolates harness transfer.

**Is the phase state a hidden oracle?**  No. It is a structured transformation
of evidence the agent has already observed. Provenance and causal-turn checks
reject hidden, future, private, or evaluator-derived fields.

**Why not simply fine-tune on successful harness trajectories?**  Offline SFT
does not cover the states created by the trained student's own errors. On-policy
rounds specifically repair that covariate shift.

**Why not evaluate Amazon after every checkpoint?**  That would turn the test
benchmark into iterative training feedback. Procedural validation selects the
checkpoint; Amazon is reserved for the final claim.

**Does procedural improvement guarantee Amazon improvement?**  No. It is a
mechanism-aligned screen. Only the final held-out Amazon comparison establishes
benchmark transfer.

**What, precisely, is being internalized?**  A policy for maintaining the user
contract, closing the relevant option set, resolving uncertainty, comparing
feasible alternatives, and auditing commitment—not a list of products or
answers.

## Implementation references

- Harness motivation and phases: [`harness_improvement_design.md`](harness_improvement_design.md)
- Training package overview: [`../training/harness_distill/README.md`](../training/harness_distill/README.md)
- Frozen campaign configuration: [`../training/harness_distill/configs/campaign.yaml`](../training/harness_distill/configs/campaign.yaml)
- Final evaluation protocol: [`../training/harness_distill/configs/evaluation.yaml`](../training/harness_distill/configs/evaluation.yaml)

