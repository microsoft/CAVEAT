# Eight-environment standard leaderboard protocol

## Scope

This protocol expands the standard leaderboard from Amazon to Airbnb,
DoorDash, eBay, Etsy, Fiverr, Instacart, Nike, and StockX. Zillow is excluded.
The measured matrix is fixed at 17 model configurations, five relativeness
variants, clean and steered conditions, and three independent runs per setting:
4,080 runs in total. The headline score is `preservation_strict`; the secondary
score is `strict_binary = 1[P*=1]`, with literal unique-hero rate reported as a
separate diagnostic.

All task/catalog/oracle inputs are frozen before launch. Every one of the 40
environment-by-variant oracles must have P*=1.0. The browser-use harness,
storefront behavior, task wording, scoring, model parameters, and step/time
backstops are shared across the matrix. Runs are launched at at most 30-way
browser concurrency with a global ten-second spawn stagger, balanced blocks,
and routes rotated across `gcr/shared`, `msraif/shared`, and
`redmond/interactive`. Availability is probed before launch, before each new
block, and whenever a route becomes unavailable.

The 3,600-second probe freshness rule is a hard launch-receipt bound. The
controller begins renewal with 60 seconds of headroom. It also checks the exact
probe record immediately before creating an attempt artifact or worker. If the
lease crosses this soft renewal edge during source/host preflight, only the
typed pre-`Popen` refresh event is retryable: the identical row and attempt are
requeued, no directory/receipt/process is created, all regions are reprobed,
and routing is recomputed. Freeze, host, port, route, process, and unknown
launch failures remain fail-closed. Block receipts bind the probe by SHA-256
and high-resolution open epoch. Launch receipts bind the exact probe and route
with a high-resolution process-start epoch; if `Popen` itself crosses the hard
lease edge, that new process group is terminated and the campaign fails closed.

## Exact extraction externalization taxonomy

Browser-use 0.13.6 routes an extract result of 10,000 or more characters to an
exact `extracted_content_<counter>.md` file. The complete result remains in the
current `ActionResult`, a durable memory pointer names the file, and `read_file`
retrieves the exact file content. This is fixed external-memory architecture,
not truncation. The raw eight-record `context_cap_audit` remains unchanged;
`extract_memory_chars` is cross-bound to
`fixed_architecture.extract_result_file_externalization` and reported as a
non-invalidating architecture touch.

The distinct, genuinely lossy limits remain invalidating: 60,000-character
read-state and action-result channels, 100,000-character extract page chunks,
100 `already_collected` items, and the other declared lossy context limits.
The exact boundary behavior and UTF-8 round trip are mechanically tested at
9,999, 10,000, and 10,119 characters. Both the upstream tools implementation
and filesystem implementation are source-hash guarded and included in the
frozen dependency inventory.

## Model-output validation feedback taxonomy

Browser-use renders an action-schema failure back to the model and caps that
feedback at 20,000 characters, retaining symmetric 10,000-character edges.
Pydantic can expand one malformed model-authored action across every member of
the action union, producing a long, repetitive `ValidationError[AgentOutput]`.
This carries no new browser, tool, storefront, or marketplace observation: the
model authored the invalid value and already received the frozen action schema.

For the successor campaign, only errors whose exception or explicit
`__cause__` chain contains a Pydantic `ValidationError` titled exactly
`AgentOutput` are classified as fixed validation-feedback rendering. The audit
wrapper observes provenance and binds the exact resulting `ActionResult` by
object identity; it does not alter the exception, result, prompt, tools, or any
agent-visible bytes. The raw eight-record context audit still counts every
action error. An independent detailed audit partitions it into
`agent_output_validation` and `other_or_unknown`, and the frozen limit audit
cross-binds both views exactly.

All unclassified, browser-, tool-, and environment-origin errors remain under
the lossy 20,000-character limit and invalidate a run if they cross it. The cap
is not raised and no recovery tool or benchmark-specific repair is added.

## Excluded pilots

`results/clone8_full_leaderboard_20260731_v2` is an excluded controller pilot.
`results/clone8_full_leaderboard_20260731_v3` is also excluded in full. V3's
frozen contract classified the 10,000-character extraction route as lossy and
therefore stopped prospectively when that event occurred. No V3 result is
reused or retroactively reinterpreted. The corrected taxonomy applies only to
a fresh source/contract freeze and a fresh 4,080-run campaign.

`results/clone8_full_leaderboard_20260731_v4` is excluded in full as well. Its
prospectively frozen contract treated every action-error rendering above 20,000
characters as loss of environment information. It stopped when a malformed
model action expanded into a 30,235-character Pydantic union diagnostic. An
audit of V4 and historical Amazon trajectories found the same source mechanism
across model families, while no oversized event contained a storefront/tool
response. V4 is not rescued or pooled; the provenance distinction is declared,
tested, and frozen before a fresh successor begins.

`results/clone8_full_leaderboard_20260731_v5` is excluded in full. Its sole
pre-launch probe remained healthy in every region, and all 217 workers it
actually launched had fresh probe receipts. At the next spawn, however, the
outer scheduler checked the probe only seconds before its one-hour expiry;
source and host attestation crossed the hard edge, and the inner pre-`Popen`
check was treated as a generic protocol failure. No stale worker or attempt
artifact was created. V5 is not resumed or pooled: renewable-lease semantics
are declared, tested, and frozen prospectively for a fresh 4,080-run successor.

`results/clone8_full_leaderboard_20260731_v6` is excluded in full. It launched
the first 255-run block with fresh, exact probe receipts and renewed the probe
successfully without overlapping a launch. One DeepSeek-V4-Pro Fiverr worker,
however, repeatedly used Browser Use's all-occurrence `replace_file` primitive
to replace a delimiter with text containing multiple copies of that same
delimiter. The replacement sites therefore doubled on each call. Its scratch
file reached 22.5 GB and its process used about 122 GB RSS before the exact
attested process group was terminated to protect the shared host. Three other
attested workers still running after the campaign was excluded were then
terminated and recorded as incomplete scientific-invalid tails. V6 stopped
new spawns, never opened block 2, and none of its 245 scored or six behavioral
results are reused.

`results/clone8_full_leaderboard_20260731_v7` is excluded in full. It launched
52 rows from block 1: 44 were scored, three were behavioral failures, two were
zero-step eBay server-start failures, and three still-running tails were
receipt-attested and terminated only after exclusion. The two startup failures
hit the shared 40-second health deadline at 44.3 and 48.1 seconds of total run
time while the host was near its frozen browser concurrency. They occurred on
different models and ports; other eBay rows started successfully, all three
model-service regions remained healthy, and an idle replay of the same backend
and database started normally. The controller correctly failed closed because
the old contract did not distinguish a still-alive server health timeout from
an environment/configuration error. V7 opened no later block and none of its
results are reused.

The next successor raises and freezes the shared server-start health deadline
well above observed healthy startup, records server output in the run directory,
and distinguishes an early child-process exit from a still-alive health
timeout. Only the latter's exact zero-step marker, bound to the frozen
environment and row port, is eligible for an infrastructure retry. Arbitrary
`outcome="error"`, early process exits, configuration failures, and mismatched
markers remain scientific-invalid and stop the campaign. This is launch
plumbing shared by every environment; it does not change a healthy storefront,
agent prompt, model action, task, or score.

The successor freezes two general, equal-arm infrastructure corrections before
any measured launch. First, the non-scoring Browser Use judge is explicitly
disabled. Upstream invokes it only after the agent has finished and logged its
actions; it does not override the agent result, and AgentArena scores the
storefront database independently. Historical Amazon evidence includes judge
failures on runs with P*=1.0, so this removes an auxiliary model request and
retry tail without changing decisions or scores.

Second, `replace_file` rejects only recursive all-site amplification: the
current file must contain the search text more than once *and* the replacement
text must itself contain that search text more than once. Ordinary zero-match,
single-site, multi-site, and linear self-containing replacements retain
upstream behavior. A rejection is an explicit tool error and leaves the file
unchanged; there is no hidden truncation or file-size cap. Mechanical replay of
all 5,396 `replace_file` calls in 2,100 completed Amazon leaderboard runs found
zero triggers. Replay of the V6 failure triggers on its second replacement,
before geometric growth. The exact predicate, upstream source hashes, run-local
observations, and tests are frozen as fixed architecture for the successor.

Historical Amazon results remain descriptive, protocol-qualified evidence.
They are not silently pooled into a same-protocol confirmatory denominator;
doing that would require a separate fresh 2,550-run Amazon campaign.

`results/clone8_full_leaderboard_20260731_v7` is excluded in full. It froze the
two corrections above and passed its prelaunch certification, but two eBay
workers in the first block received no observation because the shared
storefront health wait expired after 40 seconds. Both attempts ended with zero
agent steps. The old lifecycle discarded server output and used one generic
error for both a still-starting child and a child that had exited, so the
campaign correctly failed closed rather than guessing which failure occurred.
V7 stopped new spawns, drained its live workers, and none of its results are
reused.

The next successor freezes a 300-second storefront-start deadline as an
infrastructure-only backstop. The lifecycle now polls child liveness while it
waits and preserves server output in the run-local
`environment_server.log`. Only a zero-step `error` carrying the exact frozen
environment, port, deadline, and
`AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT` marker is eligible for an
infrastructure refill. That marker is emitted only when the child is still
alive at the deadline. An early child exit has the distinct
`AGENTARENA_ENVIRONMENT_SERVER_EXITED` marker and remains a fail-closed
scientific-invalid configuration/server failure. Generic zero-step worker
errors are never rescued. This shared pre-agent lifecycle does not change a
healthy storefront, any agent prompt/action, catalogs, tasks, scoring, or
Amazon behavior.

The first non-versioned canonical attempt, now archived at
`results/archive/eight_env_leaderboard_excluded_20260731_sigkill`, is excluded
in full. It reached 41 terminal rows: 38 scored outcomes, two behavioral
failures, and one scientific-invalid Airbnb GPT-4o attempt. The invalid worker
was killed by `SIGKILL` after roughly 14 agent steps while its environment and
browser descendants remained alive. It was far below every frozen step and
wall-clock backstop; host telemetry showed neither resource pressure nor an
OOM event, and the controller recorded no kill request. Because the original
contract did not prospectively classify this exact lifecycle failure, none of
the attempt's 40 otherwise usable outcomes are reused.

The fresh canonical successor is `results/eight_env_leaderboard`. Before any
measured launch it freezes one narrow, symmetric infrastructure rule: only a
controller-observed worker return code of `-SIGKILL` with both the trajectory
and summary absent is a candidate for a typed infrastructure refill. After the
worker scope is quiescent, a read-only SQLite audit must additionally prove that
no checkout committed: Airbnb must have no `booking` for the task guest, and
each of the seven shared storefronts must have no task-user `order` header.
Header-only orders veto a refill; cart-only state is diagnostic. Missing or
unreadable databases, schema drift, live file descriptors, journal/WAL/SHM
sidecars, unstable hashes, or a failed integrity check all fail closed.
Complete, corrupt, identity-mismatched, or otherwise invalid artifacts are
never overridden. Resume state may be reconstructed only from an immutable
validated completion receipt, and six unsuccessful typed attempts make the row
and campaign scientific-invalid.

After every worker exit, the controller attests and cleans only that worker's
exact process scope. Its `AGENTARENA_CACHE_NONCE` is bound to the fresh
campaign UUID, immutable manifest hash, run, and attempt. Process group/session
membership is detection-only: every signal is sent individually through a
pidfd after nonce and start-tick revalidation, and an uncertified numeric group
member fails closed. It uses a bounded TERM-then-KILL sequence. A receipt is not
complete until the exact scope is empty and its frozen port is released;
remaining processes, PID reuse, or a busy port fail closed. An independent
alert-only monitor checks the active launcher, status freshness, concurrency,
disk space, frozen port ownership, and unexpected worker/browser roots every
30 minutes. It cannot probe, launch, kill, refill, or modify scientific state;
those responsibilities remain exclusively with the campaign controller.
