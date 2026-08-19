# Architecture

A run is one **cell** = (environment × scaffold × model × task × condition). The
runner fans cells out across worker processes; each worker does:

```
env.start(port, task)            # seed a fresh DB w/ the catalog → launch server → wait healthy
  → scaffold.run(ctx)            # drive the browser; return RawTrajectory(steps, answer)
  → env.evaluate(handle, task)   # read back what was bought/booked → Evaluation
  → Trajectory(...).save(dir)    # step_*.png + trajectory.json + summary.json
  → handle.stop()
```

Everything downstream (the viewer, aggregation, comparison) reads the normalized
`Trajectory` format, so a new scaffold or environment is immediately comparable
with every other.

## Core types (`agentarena/core/`)

| file | type | responsibility |
|---|---|---|
| `trajectory.py` | `Trajectory`, `Step`, `Evaluation` | normalized on-disk run format + I/O |
| `environment.py` | `Environment` (+ `ENVIRONMENTS`) | server lifecycle: seed/launch/health/reset; adapters implement `seed_db` + `evaluate` |
| `scaffold.py` | `Scaffold` (+ `SCAFFOLDS`), `RunContext`, `RawTrajectory` | the plug-in seam: implement `run(ctx)` |
| `models.py` | `ModelSpec`, `OpenAIEndpoint` | two faces: `openai_endpoint()` (external scaffolds) + `achat()` (native, routed) |
| `task.py` | `TaskSpec`, `check_constraints` | instruction + preference DSL + compliance check |
| `experiment.py` | `Experiment`, `Runner`, `run_cell` | matrix expansion + parallel subprocess orchestration |

Registries are decorator-based and populated on import (`agentarena.envs` /
`agentarena.scaffolds`).

## Extending

**Add a scaffold** — implement `Scaffold.run(ctx) -> RawTrajectory` and
`@SCAFFOLDS.register("name")`. Use `ctx.model.openai_endpoint()` to point a
third-party agent at the model, or `await ctx.model.achat(...)` to call it
yourself. Capture `Step(screenshot=…, action=…, reasoning=…)` per step.
Reference: `scaffolds/simple.py`.

**Add an environment** — vendor the web app under `envs/<name>/server/`, then
implement `Environment.seed_db` (write a custom catalog into a fresh DB) and
`Environment.evaluate` (read back the outcome via the API, score with
`check_constraints`). Register a catalog + example task. Reference: `envs/amazon/`.

**Customize a catalog** — author `Product`/`Listing` objects (see
`envs/*/catalog.py`); the adapter serializes them to the JSON the env seeds from
and uses the same objects to score. The website's layout/logic is never touched —
only the data and (in the steered condition) the result ordering.

## Conditions & steering

`clean` seeds an honest catalog and a fair search. `steered` additionally pins the
`advertised` decoys to the top of results (with badges) and buries the `compliant`
picks below the fold — implemented entirely in the env's experiment hook
(`experiment_laptops.py` / `experiment_listings.py`), gated by environment
variables the adapter sets. An optional drip price (`display_price`/`true_price`)
surfaces only at checkout. What the API exposes never changes between conditions:
list/search rows are a card whitelist with an identical field-set clean vs
steered (airbnb excepted — it retains a steered-only card spec-strip), so the
only clean↔steered difference is the manipulation itself.

Condition suffixes ride on top: `-scrape-easy/hard/hardest` select an anti-bot
rate preset only (steering byte-identical), `-ssr` (Amazon) switches to the
server-rendered transport control, and `adv-*` conditions disable the serving
gate (`AMAZON_API_GATE=0`) to preserve the legacy surface the adversarial
cloaking machinery keys on.

## Serving gate & anti-bot (`envs/_storefront/gate.py`)

Every storefront installs the shared session gate: the adapter's `server_env()`
mints two per-cell secrets — `STOREFRONT_CLIENT_TOKEN`, injected into the served
page (SPA meta tag / boot script / SSR cookie) and required by every data/write
endpoint, and `STOREFRONT_OPS_TOKEN`, never served, used by the evaluator's
read-back (gate- and rate-exempt). Tokenless requests get a WAF-style 403 (a
full-page "Robot Check" for document navigations); `/docs`/`/openapi.json` are
dead. A rolling-window rate limiter (defaults 12/10 s, 60/60 s) covers content
reads: on breach, API reads answer 503 + Retry-After and page loads render a
solvable robot-check interstitial (`/verify-human`, min 2 s delay, 45 s TTL
auto-recovery). The database resets once per server process, so a page refresh
keeps the cart. `scripts/audit_lockdown.py` boots every env per condition and
asserts this endpoint matrix over live HTTP.

## Models & routing

`llm_client.py` is a self-contained multi-endpoint client: a logical name maps to
every TRAPI region (+ PhyAGI) that serves it; it load-balances, cools down
throttled endpoints, retries/fails over, and caches. Native scaffolds get this via
`ModelSpec.achat`. External scaffolds get a freshly-minted bearer token + the
region URL via `ModelSpec.openai_endpoint`. BYO endpoints bypass routing entirely.
