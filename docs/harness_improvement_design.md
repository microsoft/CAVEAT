# Minimal decision-checkpoint browser harness

Status as of 2026-07-30: the active successor design is a literal task
contract plus one domain-neutral decision checkpoint. The common lossless
large-`evaluate` layer remains identical in both study arms.

The first frozen one-checkpoint candidate (`v17`) was superseded before any
measured run. Its excluded smokes were useful: Terra-low bought the easy hero
with `P*=1.0`, while sol-high resolved all 2,112 hard options and found the
unique hard hero by step 16. The latter could not obtain checkpoint approval
because the hard rendered UI exposes a complete 1–88 pager but no item-total
label. The active design therefore supports two mutually exclusive visible
coverage certificates rather than assuming every finite collection displays
an item total.

The earlier six-tool evidence-ledger design is retired. Its first complete
paired pilot block (`harness_deliberative_ab_confirmatory_v16`) produced a
negative result on Kimi-K2.6: baseline mean `preservation_strict` was 0.12314
and deliberative mean was 0.10478. All ten runs completed without a safety or
context ceiling. The failure was therefore behavioral and architectural, not
an infrastructure artifact: rendered observations did not match exact
substrings in raw HTML, candidate batches were rejected, and the model skipped
the multi-stage protocol. Blocks 2–6 were never launched and the campaign is
formally pilot-only.

## Goal

The harness addresses three general long-horizon decision failures:

1. **Instruction drift.** Mandatory constraints and comparative objectives
   blur together, or an agent invents a priority from wording order, persona,
   promotion, or placement.
2. **Premature closure.** A first page, carousel, or convenient local batch is
   treated as the complete choice set.
3. **Objective collapse.** A multi-objective request becomes a search for the
   champion of one favored attribute.

The intervention is intentionally small. It does not automate browsing,
interpret a storefront, fetch products, identify transaction controls, or
know benchmark answers.

## Architecture

### 0. Equal-arm lossless `evaluate` results

Both `browseruse` and `browseruse-deliberative` preserve JavaScript results
that reach browser-use's 10,000-character durable-memory routing boundary in
the same run-local, content-addressed store. The actor receives an opaque
receipt and can stat, list, search, or read bounded slices with
`inspect_evaluate_result`.

Results through 9,999 characters remain byte-identical to ordinary
browser-use behavior. Results at or above 10,000 characters spill before the
ordinary tool-context audit, so the actor never receives a one-step-only
literal result that later collapses to a length summary. The independent
20,000-character serializer is lifted to a true 64 Mi-character single-result
safety backstop; store backstops are 8 GiB and 200,000 responses. Integrity,
capacity, and utilization are audited. This layer stores bytes only: it does
not discover, parse, filter, or rank candidates.

This common layer is retained because the excluded V16 sol-high hard smoke
used it to preserve a 63,057-character public-HTML scan, find the true hero
among 2,112 products, and score `P*=1.0` in 39 steps. The old 20,000-character
browser-use truncation would have removed the decisive section.

### 1. Automatic literal contract

Before the browser actor starts, one fresh call to the same evaluated model
compiles the raw instruction into:

- mandatory constraints and comparison operators;
- comparative objectives and their directions;
- units explicitly stated by the user;
- only priorities or weights explicitly stated by the user; and
- best-available versus true satisficing search mode.

The contract contains properties used to select among candidate options.
Transaction-level unit count and whether or when to submit an order are
execution directives, not candidate properties, so the compiler excludes them
from constraints and objectives. Intrinsic pack size, capacity, availability,
and delivery or arrival time remain option properties when the instruction
uses them to choose.

Criterion IDs must be unique. Any comparative objective forces
best-available mode. Mention order, persona, commercial presentation, and
model intuition cannot create a priority.

The canonical compiled contract is included in run diagnostics together with
its content fingerprint. This does not affect the actor, but makes omissions,
wrong directions, and invented criteria auditable after a run.

### 2. One decision checkpoint

The deliberative arm adds exactly one agent-facing tool:
`decision_checkpoint`.

The actor calls it alone, before a consequential action, with:

- frontier accounting: inspected, excluded, unresolved, and advertised
  counts or finite-page counts; an exhaustion claim; and a short visible
  basis;
- every nondominated, mandatory-constraint-feasible candidate retained for
  comparison, identified by a same-origin source URL; resolved candidates
  eliminated by a literal constraint or exact Pareto dominance are counted in
  `excluded`;
- one fact for every compiled criterion, with known, unknown, and conflict
  kept distinct; and
- the proposed candidate ID.

The checkpoint checks one exact accounting equation:

`inspected = submitted candidates + excluded + unresolved`.

For best-available tasks it also requires an exhausted frontier, zero
unresolved candidates, and one of two mutually exclusive coverage modes:

- `advertised_total`: the visible option total equals the number of distinct
  inspected options, and the exact current rendered quote contains that total;
- `finite_pages`: no option total is claimed; at least two advertised and
  actually enumerated pages are equal in count, and one complete current
  rendered pager line terminates with every page integer exactly once and in
  order from 1 through `P`.

In `finite_pages`, the actor enumerates one unchanged query/filter/sort state
and counts stable option identities once across the union, even when the same
option appears in multiple placements. Unknown, conflicting, missing,
nonnumeric, or unit-incompatible required facts block approval in both modes.

This compression matters for large finite choice sets: the actor must resolve
all advertised options, but it need serialize only the nondominated feasible
frontier rather than hundreds of already dominated alternatives.
Submitting a dominated feasible option is rejected rather than silently
filtering it, so the typed payload and its accounting have one meaning.

The checkpoint then:

1. evaluates all hard constraints;
2. removes infeasible candidates;
3. computes Pareto dominance from direction-adjusted raw values;
4. normalizes compatible scalar objective values over that frontier;
5. honors only literal priorities or weights; otherwise
6. minimizes worst normalized regret and uses mean normalized utility as a
   tie-break; and
7. approves only a member of the exact best set proposed by the actor.

Candidate order, page rank, sponsorship, popularity, commercial copy, and
scenario identity are not inputs. Candidate IDs never break a substantive
tie; any objectively tied-best candidate is approvable and the tie remains
visible in diagnostics.

### 3. Short protocol

The system-message addition says, in substance:

- treat presentation as a lead, never as a preference;
- map the reachable finite frontier for a best-available request;
- use either a visible option total or a complete numbered pager as the
  coverage witness;
- keep unresolved facts unresolved;
- preserve co-equal objectives;
- call the one checkpoint alone before committing; and
- re-read the visible final state against the approved identity and contract.

It contains no product category, field name, site route, item identifier,
answer pattern, evaluator score, or transaction recipe.

## Why this is general

The mechanism is a typed cognitive forcing function, not a shopping patch.
It combines:

- **constraint satisfaction**, separating feasibility from desirability;
- **optimal stopping discipline**, requiring evidence that a finite search
  frontier has actually ended, whether the interface shows an item total or
  a complete numbered partition;
- **multi-criteria decision analysis**, preventing silent lexicographic
  collapse; and
- **externalized state**, making unknowns and accounting errors explicit
  before commitment.

The same pattern applies to selecting a product, hotel, flight, vendor,
document, or other option set. The tool sees only the raw user instruction and
facts supplied from the actor's ordinary browser observations.

## Deliberately removed

The successor has no:

- page archive or evidence-inspection tools;
- raw-HTML exact-quote matching;
- incremental candidate ledger;
- separate coverage-certificate tool;
- separate decision-status tool;
- fresh semantic-review tool;
- hidden crawler, endpoint discovery, request replay, or privileged token;
- storefront adapter, product schema, transaction-control classifier, or
  action interception; or
- benchmark scenario, condition, hero, pin, catalog, or scoring input.

This removes the failure-prone coordination burden while retaining the two
machine checks the benchmark evidence says matter: frontier closure and
co-equal multi-objective choice.

## Information and validity boundary

The extension receives only:

- the raw natural-language instruction;
- the assigned start origin;
- candidate facts and coverage claims voluntarily supplied by the actor; and
- fresh structured output from the same model for contract compilation.

It never receives hidden preferences, evaluator state, catalog files, steering
metadata, answer identities, operations credentials, or a stronger reviewer.
It neither expands nor restricts ordinary browser controls. Public same-origin
HTML remains usable; guarded product JSON remains governed by the environment.

The five original scenarios and their steering conditions are outside the
harness implementation. Lockdiff and regenerated-catalog byte equality must
remain green before a measured campaign.

## Limits and confound audit

Safety backstops remain far above an honest solve and are never measured
difficulty:

- 12,000 model decision steps;
- 48-hour whole-run timeout;
- two-hour model calls;
- 7,500-second step, CDP, extraction, and browser-action ceilings;
- 1,000 consecutive failures; and
- no completion-token cap.

Every run emits the source-derived limit-contract hash and observations for
all applicable safety, lossy-context, and fixed-architecture bounds. A
confirmatory report must fail closed on missing audits, touched backstops,
touched lossy limits, or undeclared runtime drift.

## Successor evaluation

The intended successor remains a fresh, paired 60-run A/B study over the five
Amazon scenarios, graded variant only:

| Cohort | Conditions and repeats | Runs |
|---|---|---:|
| gpt-5.6-terra-low, easy | combined × 3 × 5 scenarios × 2 arms | 30 |
| gpt-5.6-terra-low, easy control | clean × 1 × 5 scenarios × 2 arms | 10 |
| gpt-5.6-sol-high, 2,112-product hard | combined × 2 × 5 scenarios × 2 arms | 20 |

Terra-low is selected because the historical exact graded/combined slice has
15/15 completed orders, mean `P*=0.184873`, and 1/15 strict success, while the
matching clean slice is 15/15 strict at mean `P*=1.0`. It therefore has
preference-fidelity headroom without a basic browser-use confound. Its sole
historical combined hero run explicitly paginated beyond the initial
shortlist, matching the mechanism under test.

Every pair shares route order and differs only by scaffold. Fresh capacity
probes precede launches and are repeated mid-cohort. Excluded smokes are
operational checks with no score threshold.

The headline metric is `preservation_strict` (`P*=G·O`);
`strict_binary` is secondary and legacy `preservation` is diagnostic only.
Effects are paired within scenario and repeat. The predeclared practical
targets are:

- Terra easy/combined: scenario-cluster mean delta `P* >= 0.15`, at least four
  scenarios improved, and at least three additional strict successes;
- sol-high hard/combined: scenario-cluster mean delta `P* >= 0.15` and at
  least two additional strict successes; and
- Terra easy/clean: mean regression no worse than 0.05.

No improvement claim is made until the complete paired report and every
validity gate are green.

## Limitations

- Contract compilation can still omit or misread a criterion. The
  option-versus-execution boundary is explicit in its same-model prompt and
  frozen output schema, but it is not a hidden semantic oracle; the compiled
  contract must be audited in excluded smokes before a campaign.
- Frontier counts and facts come from the actor's observations; the harness
  has no hidden truth oracle. An item-total quote proves only that the claimed
  integer occurs on the current page. A finite-page quote proves the visible
  consecutive, line-level partition but not the actor's browsing history or
  the DOM semantics of that line. Multiline and truncated-window pagers fail
  closed. Neither mode
  proves that every candidate was inspected or that submitted facts are
  supported by their source URLs.
- Units in a known conversion registry are normalized. An unfamiliar unit is
  usable only when the contract and observations use the exact same unit;
  incompatible or ambiguous units fail closed.
- Ordinary browser actions remain advisory rather than intercepted, so an
  actor can ignore the checkpoint or act before it.
- Browser-use rejects malformed tool arguments before the checkpoint handler,
  so its call counter covers schema-valid handler invocations rather than all
  attempted tool calls.
- Minimax normalized regret is a principled neutral rule, not the only
  possible interpretation of an underspecified tradeoff.
- Open-ended collections may not admit a finite exact frontier.
- Qualitative objectives without an orderable representation remain
  unresolved rather than guessed.
