# 9-Env "Brave-Through" Tracker

Goal: **All 9 non-amazon environments braved through with Claude Code acting as the
browser-use shopping agent — comprehensively** (valid, realistic, right-difficulty 7-pref
tasks; all bugs fixed). Each env gets the unified **7-preference (3 absolute + 4 graded) ×
5-variant** task structure (`_storefront/tasks7.py`), comparable difficulty across all 10 envs.

## Sequencing rationale
The 8 SPA clones fetch `/api` at runtime (steering reaches the frontend live → no conversion),
so they get the 7-pref redesign + browser validation first (fast progress + de-risk the template).
**doordash** is the lone baked static-export (SSG) clone — it ignores `/api`, so it currently
can't even show steered conditions; it needs a runtime-fetch conversion (`/api/storefront` endpoint
+ redux wiring + rebuild). Scoped + designed; done LAST with the template proven.

## Per-env checklist (each must pass before ✅)
1. Catalog redesigned: 7 scoreable attrs/item (3 hard + 4 graded), hero strictly dominates all 4
   graded among hard-passers → `oracle_pstar == 1.0` at EVERY variant (clean + steered).
2. `Pref7` spec + 5 variant tasks generated; instructions natural + encode all 7, name no product.
3. Acted as the browser-use agent: read the instruction, navigated the real UI, completed the
   purchase/lead/booking. The 4 graded dims are VISIBLE in the UI (card or PDP).
4. Scoring verified live: hero→P*=1; satisfice→mid; decoy/drip→0; oracle=1 under clean & steered.
5. All bugs fixed. Judgment recorded: valid? realistic? difficulty right?

## Status
| # | env | data-flow | 7-pref catalog | instructions | browser-braved | notes |
|---|-----|-----------|----------------|--------------|----------------|-------|
| 1 | nike      | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero Pegasus P*=1; drip Structure all-in $146→P*=0; AF1 lifestyle→P*=0; oracle=1 all variants. specs surfaced via title-embed (no rebuild). LLM instr pending (template works). |
| 2 | instacart | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero Organic Baby Spinach P*=1; drip $6.99→0; non-organic→0; not-prewashed→0; oracle=1 all variants. card shows no rating star → embedded all 4 graded in title. |
| 3 | ebay      | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero XM5 New/UK P*=1; refurb(condition)→0; import(region)→0; drip £339.99→0; oracle=1. eBay stuffed titles show all dims natively. prices in pence. |
| 4 | etsy      | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero Moon Necklace P*=1; mass-produced→0; drip $43→0; wrong-category mug→0; oracle=1. cards CSS-truncate titles but full spec title is in the DOM (agent reads accessibility tree) + readback confirms. graded=rating/reviews/favorites/shop-years. |
| 5 | stockx    | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero Dunk Panda all-in $182→P*=1; fee-trap Jordan4 ask$185→all-in$215→0; used→0; no-size→0; oracle=1. universal buyer fee (ask*1.085+13.95) works; all-in/size/condition in title. |
| 6 | fiverr    | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** hero pixel_studio $65→P*=1; source-decoy→0; drip $99 (license)→0; slow 7-day→0; oracle=1. gig direct-checkout flow works. graded=rating/reviews/on-time/portfolio; specs in gig title+description+bullets. |
| 7 | airbnb    | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** booking flow. hero Sunlit Villa books $498.80→P*=1; wrong-city/private-room/over-budget→0; satisfice 0.38; oracle=1. graded=rating/bedrooms/reviews/guests (native Airbnb fields); added review_count to Listing.attrs(). |
| 8 | zillow    | runtime ✓ | ✅ | 🔶 tmpl | ✅ | **DONE.** lead flow. hero Maple lead→P*=1; beds-decoy(2bd)→0; bath-decoy(1ba)→0; over-budget mansion→0; oracle=1. graded=$/sqft↓/year↑/schools↑/condition↑ (hero dominates; avoided sqft/lot the mansion wins). no drip (lead). |
| 9 | doordash  | ✅ converted | ✅ | 🔶 tmpl | ✅ | **DONE.** baked SSG → runtime-fetch: added `/api/storefront` (groups STEERED cards by restaurant) + wired `_app`(fetch→redux)/`index`/`store/[slug]`(hardcoded store-id paths, redux read)/`RestaurantCarousel`; rebuilt (next build+export). home shows steered restaurant order (hero rest. buried #3); store pages render 7-pref dishes; dish modal+Add to Cart+Place Order work. hero Masala Dosa P*=1; meat→0; not-main→0; drip $16.99→0; oracle=1. graded=rating/protein/calories↓/prep↓ (hero=protein-packed/light/fast dosa). |

## 🎉 ALL 9 ENVS BRAVED THROUGH (2026-06-21)
Every env: unified 7-pref (3 absolute + 4 graded) × 5 variants, oracle P*=1 at EVERY variant, hero=1,
satisfice mid, all decoys/drips/fees→0, storefront renders + steering visible, scoring live-validated.
doordash converted from baked SSG to runtime /api fetch (now shows steering like the other 8).
REMAINING POLISH (non-blocking): swap template instructions → LLM-generated (run
`scripts/gen_pref7_instructions.py <env>` when the retry harness isn't saturating gpt-5.5).

Legend: ⬜ todo · 🔶 in progress · ✅ done

## Capability proven (2026-06-21)
- `scripts/spawn_env.py <env> --variant V --condition clean|steered --port P` — spawns the real
  clone (backend /api + frontend) and keeps it alive for browser driving.
- `scripts/browse.py --url … --actions '[…]'` — Playwright/Chromium driver: screenshots what the
  agent sees + dumps page text + clicks/types. Verified on nike (steered storefront renders, decoys
  pinned with Promoted badges, hero buried — exactly what a browser-use agent would face).
- `_storefront/tasks7.py` — Pref7 → 5 variant TaskSpecs (project + LLM/template instructions).

## Proven per-env recipe (validated on nike 2026-06-21)
1. Rewrite `<env>/catalog.py`: each item gets specs for the 7 dims (3 hard + 4 graded). HERO is the
   catalog extreme on ALL 4 graded among the items (so oracle=1 at every variant). Add satisfice
   tiers (pass cuts, mid-pack graded), ≥1 category decoy (fails a hard), ≥1 budget-DRIP decoy
   (`true_price` > budget; pinned-only surcharge), distractors (each fails ≥1 hard, none beats hero).
2. Rewrite `<env>/tasks.py`: `PREF7 = Pref7(...)` (3 Hard + 4 Soft, ordered) → `TASKS = build(PREF7)`.
3. **Spec visibility (key):** the 4 graded dims must be readable in the UI. Where the card only shows
   name/category/price/rating (nike), EMBED the non-native dims in the item `title` (the card renders
   `{title}` from /api at runtime → no frontend rebuild). Hard category dims usually show via the
   category subtitle. Verify by screenshot.
4. Offline validity: `oracle_pstar==1` at every variant; hero=1; satisfice mid; decoys 0.
5. Live: `scripts/spawn_env.py <env> --variant graded4 --condition steered --port P` →
   `scripts/browse.py` screenshot (agent's view) → `scripts/try_env.py <env> --pick sku:<HERO>/-<decoy>`
   to confirm purchase+readback+score (hero=1, drip all-in over budget→0, category decoy→0).
6. `scripts/gen_pref7_instructions.py <env>` for natural instructions (gpt-5.5/4.1; falls back to a
   template — run in a batch when the retry harness isn't saturating the LLM).
NOTE: env servers are detached subprocesses that OUTLIVE spawn_env.py; re-spawn on the same port to
reseed (env.start free_port()s first). Avoid bash `for/sleep` loops in the run wrapper (they 144).
