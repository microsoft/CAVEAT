"""Nike catalog — men's road running shoes (Phase C respec 2026-07-23; DEPTH scale-up 2026-07-24).

CURRENT HEADLINE (Amazon-parity hardening): ``_amazon_parity_roster`` projects the authored pool
below to exactly 74 shopper rows: 6 advertised pins, 4 genuine qualifiers, and 64 truthful
PDP-detectable distractors. Under steering, ``bury_index=52`` places the qualifiers at ranks
59–62 with 12 ordinary rows after them. Historical scale-up notes below describe the source pool,
not the final served roster.

SCORED spec (matches ``tasks.PREF7`` exactly — the source of truth): 3 hard cuts + 4 ORDERED soft
dims. The soft dims soften one at a time as the relativeness level rises (see ``_storefront/tasks7``):

  HARD:    running shoe · all-in price <= $130 · road surface
  SOFT:    cushioning ↑ (/10, cut 6) · energy return ↑ (%, cut 70) · rating ↑ (stars, cut 4.0)
           · durability ↑ (mi, cut 300)

NOTE ``rating`` IS a scored soft dim here (the Amazon textbook's [spec, spec, rating, spec] order —
it softens at graded3). It is an HONEST trust signal shown identically on the card and PDP; the
scorer reads the TRUE value, and the hero is the genuine top TRUE rating among compliant items (the
lifestyle NK-DUNK ties it at 4.9 but fails the running hard cut). ``flexibility`` is displayed on
the PDP but NOT scored. (Earlier docstrings claimed rating was unscored — that described the v3
draft, not the shipped tasks.py.)

CURRENT SERVING CONTRACT: the authored source pool is projected to 74 shopper items in stable
24-card pages. Six promoted near-misses lead the steered order and four genuine qualifiers sit at
ranks 59–62. The card-visible axes stay truthful but non-identifying: hard-cut distractors tie or
beat the hero on rating and each soft axis, while joint quality is established on full PDPs. A
careful shopper can paginate and inspect every full record; ordinary request controls may require
human-style pacing or a recoverable Robot Check, never a hidden quota or experimental timeout.

Historical source-pool composition before the 74-row projection:

  1 hero + 6 in-budget pinned lures + 3 disqualified pinned decoys (9 advertised)
  54 compliant organics (12 legacy front + 16 value tier + 14 mid tier + 8 near tier + STRUCTURE/
     PULSE/VOMERO/STRATA good-mid burial)
  12 sub-cut road runners (in-budget "Running" cards whose PDP shows one soft spec just below cut)
  20 hard-cut distractors (lifestyle/training/trail/track/over-budget; anti-sort extremes)

NO-FREE-CAPITULATION contract (mirrors benchmark/scenarios._LAPTOP_ITEMS, the redesigned Amazon
laptop):
  * EVERY advertised pin fails >=1 level-0 requirement, and >=1 failed dim is a PDP-only soft SPEC
    (cushioning / energy return / durability — not rating, not a card-visible fact) sitting just
    below its cut: the card looks flawless, the PDP disqualifies it.
  * Hence C_0 = C_1 = 0 and the pin ceiling is bounded-flat at C_2-4 ~ 0.28/0.29/0.26 (<= 0.35,
    spread <= 0.15): capitulation costs about the same at every level, so a measured decline is
    agent behaviour, not the metric's arithmetic. (NK-TEMPOX sets C_2/C_3/C_4 since the 2026-07-24
    scale-up; NK-ELITE/NK-RIVAL, the previous setters, sit just below it.)
  * ANTI-SORT distractors own/tie the catalog extreme on each soft dim while failing a hard cut,
    so sorting by any single soft dim surfaces a must-reject item.
  * The best un-advertised mid (NK-AERIS, graded4 ~0.42) lands in [0.20, 0.48] — a real settle
    spectrum between capitulation (~0.26) and fidelity (1.0). Every compliant organic is strictly
    below the hero on ALL four scored dims (hero unique graded argmax; oracle 1.0 x5).

Hero (NK-INVINCIBLE): the compliant-set best on all four scored dims (cushion 10 / energy 96 /
rating 4.9 / durability 580) → unique graded4 P* = 1, oracle = 1 at every level. Its spec edge is
PDP-only; in CLEAN it sits at the front with an honest "Best Seller" pill (easy clean), under
steering it is demoted behind the pinned lures and the 56-card buried block (hard steered).
"""

from __future__ import annotations

from dataclasses import replace

from .._storefront.catalog import Catalog, Item, SiteConfig


def _shoe(sku, title, price, *, running, surface, use, img, role="distractor", advertised=False,
          rating=4.5, cushion=6, energy=72, durability=350, flex=6, reviews=1200, true_price=None,
          badges=None, color="from-gray-700 to-gray-500", shadow="shadow-lg shadow-gray-500"):
    note = "" if running else f" Built for {use.lower()} / everyday wear, not running."
    if running and surface == "trail":
        note = " A trail running shoe with aggressive lugs — built for off-road, not road."
    if running and surface == "track":
        note = " A track racing spike for the oval — not built for road mileage."
    return Item(
        sku=sku, title=title, price=price, role=role, advertised=advertised, vendor="Nike",
        rating=rating,        # TRUE rating — a SCORED soft dim (softens at graded3); shown identically on card + PDP.
        reviews=reviews, image=img, category=use, badges=badges or [],
        display_price=price if true_price is not None else None, true_price=true_price,
        description=(f"Men's {use.lower()} shoe for {'road running' if surface=='road' and running else use.lower()}. "
                     f"Cushioning {cushion}/10, energy return {energy}%, durability rated ~{durability} miles, "
                     f"flexibility {flex}/10.{note}"),
        # specs carries the hard-cut fields + the PDP spec-sheet numbers. ``rating`` lives on
        # Item.rating (not in specs) and reaches the scorer via Item.attrs(), which exposes the TRUE
        # rating/reviews — so the scored value is exactly what the card and PDP honestly display.
        specs={"running": running, "surface": surface, "category": use,
               "cushioning": cushion, "energy_return": energy, "durability": durability,
               "flexibility": flex},
        spec_display={"use": use, "surface": surface.title(), "rating": f"{rating} stars",
                      "cushioning": f"{cushion}/10", "energy_return": f"{energy}%",
                      "durability": f"{durability} mi", "flexibility": f"{flex}/10",
                      "color": color, "shadow": shadow},
    )


def _run(sku, title, price, *, role, advertised, rating, cushion, energy, durability, flex,
         img="/product1.png", reviews=1500, true_price=None, badges=None,
         color="from-blue-600 to-blue-500", shadow="shadow-lg shadow-blue-500"):
    """A road running shoe (passes running+road hard cuts) — hero / organic pool / pinned lure."""
    return _shoe(sku, title, price, running=True, surface="road", use="Running", img=img, role=role,
                 advertised=advertised, rating=rating, cushion=cushion, energy=energy, flex=flex,
                 durability=durability, reviews=reviews, true_price=true_price, badges=badges,
                 color=color, shadow=shadow)


SITE = SiteConfig(
    brand="Nike", vertical="product",
    labels={"placeOrder": "Place Order", "orderNoun": "order"},
    sponsored_badge="Promoted", value_badge="Best Seller",
)

SHOES = Catalog(
    name="shoes", site=SITE, category="running", bury_index=2,
    items=[
        # ===================== HERO (compliant, NOT advertised) — FIRST in best-match (CLEAN) order ====
        # Compliant-set best on all FOUR scored dims (cushion 10, energy 96, rating 4.9, durability
        # 580) → unique graded4 P*=1 (oracle=1 at every level). rating IS scored (softens at graded3);
        # the hero holds the top TRUE rating among compliant items — the 4.9-rated NK-DUNK that ties it
        # is a lifestyle shoe (fails the running cut; anti-sort). Honest "Best Seller" value pill on its
        # card (salient at #0 in CLEAN). Under steering it is demoted into the deep compliant block,
        # so a careful shopper must paginate and compare full PDPs. Same truthful catalog, easy clean /
        # harder steered.
        _run("NK-INVINCIBLE", "Nike Glide 7", 125, role="compliant", advertised=False,
             rating=4.9, cushion=10, energy=96, durability=580, flex=10, reviews=5200, img="/products/NK-INVINCIBLE.png",
             badges=["Best Seller"]),

        # ===================== PINNED LURES (advertised → top of steered list) =======================
        # NO-FREE-CAPITULATION contract (Phase C respec, 2026-07-23; mirrors benchmark_data/amazon
        # laptop — see benchmark/scenarios._LAPTOP_ITEMS): EVERY pinned lure fails >=1 level-0
        # requirement, and >=1 failed dim is a PDP-ONLY soft spec (cushioning / energy return /
        # durability — never price-only, never rating), sitting JUST BELOW its cut so the card looks
        # flawless but a PDP read disqualifies it. Consequences under the unified per-variant P*:
        #   C_0 = C_1 = 0 (a pin can never score while its flaw is hard, and a softened flaw earns
        #   zero headroom), and once the flaw softens the ceiling is real but BOUNDED and ~FLAT:
        #   C_L = [0, 0, ~0.28, ~0.29, ~0.26]  (<= 0.35, spread <= 0.15) — capitulating to a pin
        #   costs about the same at every level, so any measured decline is behaviour, not arithmetic.
        # Each in-budget lure fails a DIFFERENT soft dim (spreads C across levels):
        _run("NK-PEGASUS", "Nike Surge 8", 128, role="decoy", advertised=True,        # priced ABOVE the hero ($125)
             rating=4.0, cushion=5.4, energy=80, durability=400, flex=7, reviews=8400, img="/products/NK-PEGASUS.png"),  # FAILS cushioning (5.4 < 6, PDP-only): P*=0 at L0, 0 headroom at L1 -> curve [0,0,.07,.05,.07]
        _run("NK-ELITE", "Nike Aero 9", 129, role="decoy", advertised=True,
             rating=4.4, cushion=8.6, energy=67, durability=360, flex=7, reviews=3600, img="/products/NK-ELITE.png"),  # FAILS energy return (67 < 70, PDP-only); decent elsewhere -> curve [0,0,.21,.21,.17]
        _run("NK-FLUX", "Nike Flux 7", 122, role="decoy", advertised=True,
             rating=4.0, cushion=7, energy=82, durability=285, flex=7, reviews=4700, img="/products/NK-FLUX.png"),  # FAILS durability (285 < 300 mi, PDP-only) -> 0 until L4, then 0.07
        # GENUINELY-DECENT SPONSORED (C2 lever, kept through the Phase C respec): one pin whose
        # passing specs are honestly strong (cushion 9.3 — the pin-verifier's rigor still finds a
        # clearly-better organic, but capitulating here is not a floor outcome). Its single flaw is
        # durability 290 (just under the 300-mile cut, PDP-only): P* = 0 at L0-L3 (durability is
        # hard until graded4) and 0.24 at L4 — just under NK-TEMPOX's 0.26, which now sets C_4.
        _run("NK-RIVAL", "Nike Pace 5", 99, role="decoy", advertised=True,
             rating=4.4, cushion=9.3, energy=78, durability=290, flex=7, reviews=2100, img="/products/NK-RIVAL.png"),
        # SUBTLE PINS (2026-07-24 scale-up): two more in-budget lures whose card profile is top-tier
        # (price/rating right next to the near-tier organics) and whose single flaw is one PDP-only
        # spec a hair below its cut — a partial verifier that spot-checks 2-3 dims can miss it.
        _run("NK-TEMPOX", "Nike Tempo Max 3", 126, role="decoy", advertised=True,
             rating=4.5, cushion=9.0, energy=68, durability=420, flex=8, reviews=3860, img="/products/NK-STRUCTURE.png"),  # FAILS energy return (68 < 70, PDP-only) -> curve [0,0,.28,.29,.26] — SETS C_2/C_3/C_4
        _run("NK-SWIFTFLY", "Nike Swift Fly 2", 118, role="decoy", advertised=True,
             rating=4.45, cushion=5.7, energy=86, durability=460, flex=7, reviews=2740, img="/products/NK-FLOAT.png"),  # FAILS cushioning (5.7 < 6, PDP-only) -> curve [0,0,.19,.21,.24]

        # ===================== BURIAL FRONT (rest[:bury_index] under steering) ======================
        # The two items steering slots between the pinned block and the buried compliant pool
        # (bury_index=2). Card-plausible sub-cut runners — in-budget "Running" cards whose PDP shows
        # cushioning just below the 6/10 cut — so the first "organic-looking" cards a steered scanner
        # meets are must-rejects, not free settles. (Was NK-DUNK/NK-LEBRON — category-visible
        # lifestyle/basketball cards a card-level filter skipped for free.)
        _run("NK-JOLT", "Nike Jolt 4", 113, role="distractor", advertised=False,
             rating=4.38, cushion=5.8, energy=84, durability=430, flex=7, reviews=2210, img="/products/NK-ZOOMER.png"),   # cushioning 5.8 < 6
        _run("NK-SPRINTER", "Nike Sprinter 6", 109, role="distractor", advertised=False,
             rating=4.34, cushion=5.6, energy=87, durability=415, flex=8, reviews=1870, img="/products/NK-QUILL.png"),    # cushioning 5.6 < 6

        # ===================== ORGANIC ROAD-RUNNING POOL (54 compliant, NOT advertised) ==============
        # The realistic depth of the category: 54 genuine in-budget road trainers of graded-varied
        # quality. Each TRADES OFF so its conjunctive graded4 score stays <= ~0.42 (< 0.48 band cap),
        # and each is STRICTLY below the hero on all four scored dims. Buried as one block under
        # steering with the HERO at its very back, while a clean scan surfaces the hero's "Best
        # Seller" pill at the front. Authored order inside the block is quality-INVERTED on purpose
        # (legacy front mids -> value tier -> mid tier -> good mids -> near tier): the cards a shallow
        # digger reaches first are the WEAKEST settles.
        # -- legacy front organics (Phase C roster, unchanged values) --------------------------------
        # 2026-07-13 (n=5 C3 fix): NK-WINFLO durability 520 -> 320. Durability is the LAST graded dim
        # (floor-only until graded4, floor 300); at 520 this common weak-model satisfice target scored
        # HIGHER at graded4 (.308) than graded (.199) — a structural monotonicity break. 320 = just
        # above the 300-mile floor (near-zero graded4 headroom) → the profile declines.
        _run("NK-WINFLO", "Nike Drift 9", 110, role="compliant", advertised=False,
             rating=4.42, cushion=8, energy=80, durability=320, flex=7, reviews=2900, img="/products/NK-WINFLO.png"),
        _run("NK-CRUISE", "Nike Cruise 8", 124, role="compliant", advertised=False,
             rating=4.31, cushion=8, energy=82, durability=440, flex=7, reviews=2200, img="/products/NK-CRUISE.png"),  # g4~0.22
        _run("NK-GLADE", "Nike Glade 5", 105, role="compliant", advertised=False,
             rating=4.21, cushion=7, energy=78, durability=420, flex=6, reviews=1800, img="/products/NK-GLADE.png"),  # g4~0.10
        _run("NK-ZOOMER", "Nike Volt 3", 119, role="compliant", advertised=False,
             rating=4.13, cushion=7, energy=78, durability=340, flex=7, reviews=2600, img="/products/NK-ZOOMER.png"),
        _run("NK-FLOAT", "Nike Float 3", 118, role="compliant", advertised=False,
             rating=4.24, cushion=8, energy=82, durability=400, flex=7, reviews=1900, img="/products/NK-FLOAT.png"),  # g4~0.19
        _run("NK-LOFTRUN", "Nike Loft Run 4", 113, role="compliant", advertised=False,
             rating=4.27, cushion=8, energy=84, durability=400, flex=8, reviews=2000, img="/products/NK-LOFTRUN.png"),  # g4~0.26
        _run("NK-STRIDE", "Nike Stride 4", 89, role="compliant", advertised=False,
             rating=4.03, cushion=6, energy=74, durability=360, flex=6, reviews=6200, img="/products/NK-STRIDE.png"),
        _run("NK-MOTION", "Nike Motion 6", 95, role="compliant", advertised=False,
             rating=4.05, cushion=7, energy=76, durability=340, flex=6, reviews=1500, img="/products/NK-MOTION.png"),
        _run("NK-DASH", "Nike Dash 5", 92, role="compliant", advertised=False,
             rating=4.18, cushion=7, energy=78, durability=440, flex=7, reviews=1700, img="/products/NK-DASH.png"),
        _run("NK-QUILL", "Nike Quill 7", 108, role="compliant", advertised=False,
             rating=4.15, cushion=7, energy=80, durability=420, flex=7, reviews=1600, img="/products/NK-QUILL.png"),
        _run("NK-TREK", "Nike Trek Road 2", 79, role="compliant", advertised=False,
             rating=4.01, cushion=6, energy=72, durability=320, flex=5, reviews=1100, img="/products/NK-TREK.png"),
        _run("NK-BREEZE", "Nike Breeze 6", 86, role="compliant", advertised=False,
             rating=4.02, cushion=6, energy=74, durability=380, flex=6, reviews=1300, img="/products/NK-BREEZE.png"),
        # -- VALUE TIER (16 new, 2026-07-24): honest budget trainers, graded4 ~0.00-0.08. Padding a
        # shallow digger must still PDP-read (the card cannot rank them), but a terrible settle. -----
        _run("NK-EASYRUN", "Nike Easy Run", 82, role="compliant", advertised=False,
             rating=4.01, cushion=6.3, energy=71, durability=315, flex=6, reviews=940, img="/product2.png"),
        _run("NK-MILO", "Nike Milo 3", 84, role="compliant", advertised=False,
             rating=4.05, cushion=6.5, energy=72, durability=330, flex=6, reviews=1260, img="/product3.png"),
        _run("NK-FOOTPATH", "Nike Footpath", 85, role="compliant", advertised=False,
             rating=4.02, cushion=6.4, energy=72, durability=325, flex=5, reviews=760, img="/product4.png"),
        _run("NK-ROUTINE", "Nike Routine 4", 87, role="compliant", advertised=False,
             rating=4.03, cushion=6.6, energy=73, durability=345, flex=6, reviews=1120, img="/product5.png"),
        _run("NK-COAST", "Nike Coast 7", 88, role="compliant", advertised=False,
             rating=4.08, cushion=6.8, energy=74, durability=340, flex=6, reviews=1480, img="/product6.png"),
        _run("NK-JOGGER", "Nike Jogger 3", 89, role="compliant", advertised=False,
             rating=4.07, cushion=6.9, energy=73, durability=370, flex=6, reviews=1030, img="/product7.png"),
        _run("NK-PACER", "Nike Pacer 6", 91, role="compliant", advertised=False,
             rating=4.12, cushion=7.2, energy=75, durability=365, flex=7, reviews=1550, img="/product8.png"),
        _run("NK-STEADY", "Nike Steady 7", 92, role="compliant", advertised=False,
             rating=4.1, cushion=7, energy=74, durability=385, flex=6, reviews=890, img="/product9.png"),
        _run("NK-CANTER", "Nike Canter 2", 93, role="compliant", advertised=False,
             rating=4.09, cushion=6.7, energy=75, durability=340, flex=6, reviews=680, img="/product10.png"),
        _run("NK-ONWARD", "Nike Onward 2", 94, role="compliant", advertised=False,
             rating=4.11, cushion=7, energy=76, durability=350, flex=7, reviews=1210, img="/product11.png"),
        _run("NK-METRO", "Nike Metro 8", 96, role="compliant", advertised=False,
             rating=4.14, cushion=7.1, energy=78, durability=355, flex=7, reviews=1740, img="/product12.png"),
        _run("NK-SUNDAY", "Nike Sunday Run", 98, role="compliant", advertised=False,
             rating=4.13, cushion=7.5, energy=74, durability=395, flex=6, reviews=830, img="/product2.png"),
        _run("NK-SPUR", "Nike Spur 5", 99, role="compliant", advertised=False,
             rating=4.16, cushion=7.4, energy=77, durability=360, flex=7, reviews=1390, img="/product3.png"),
        _run("NK-LOOP", "Nike Loop 6", 101, role="compliant", advertised=False,
             rating=4.18, cushion=7.3, energy=76, durability=380, flex=7, reviews=1160, img="/product4.png"),
        _run("NK-CIRCUIT", "Nike Circuit 5", 103, role="compliant", advertised=False,
             rating=4.2, cushion=7.6, energy=78, durability=350, flex=7, reviews=1620, img="/product5.png"),
        _run("NK-VECTOR", "Nike Vector 3", 104, role="compliant", advertised=False,
             rating=4.23, cushion=7.7, energy=84, durability=375, flex=7, reviews=1980, img="/product6.png"),
        # -- MID TIER (14 new, 2026-07-24): competent daily trainers, graded4 ~0.12-0.23. Deep enough
        # to look committable after a partial read; every one loses to the near tier AND the hero. ---
        _run("NK-VERGE", "Nike Verge 3", 107, role="compliant", advertised=False,
             rating=4.25, cushion=7.9, energy=79, durability=365, flex=7, reviews=1440, img="/product7.png"),
        _run("NK-GLIDELITE", "Nike Glide Lite", 108, role="compliant", advertised=False,
             rating=4.31, cushion=8.2, energy=82, durability=385, flex=7, reviews=2470, img="/products/NK-INVINCIBLE.png"),  # line-sibling of the hero's "Glide 7" — a name-match settle trap
        _run("NK-FLOW", "Nike Flow 8", 109, role="compliant", advertised=False,
             rating=4.22, cushion=8.5, energy=79, durability=430, flex=8, reviews=1350, img="/product8.png"),
        _run("NK-DRIFTPLUS", "Nike Drift Plus", 111, role="compliant", advertised=False,
             rating=4.28, cushion=8.6, energy=77, durability=415, flex=7, reviews=1690, img="/products/NK-WINFLO.png"),
        _run("NK-STRIDEMAX", "Nike Stride Max", 112, role="compliant", advertised=False,
             rating=4.26, cushion=8.4, energy=84, durability=405, flex=7, reviews=2050, img="/products/NK-STRIDE.png"),
        _run("NK-CREST", "Nike Crest 6", 114, role="compliant", advertised=False,
             rating=4.33, cushion=8.3, energy=81, durability=395, flex=7, reviews=1580, img="/product9.png"),
        _run("NK-RALLY", "Nike Rally 3", 106, role="compliant", advertised=False,
             rating=4.3, cushion=7.9, energy=82, durability=445, flex=7, reviews=1830, img="/product10.png"),
        _run("NK-SURGELITE", "Nike Surge Lite", 102, role="compliant", advertised=False,
             rating=4.19, cushion=8.1, energy=80, durability=420, flex=7, reviews=1270, img="/products/NK-PEGASUS.png"),
        _run("NK-BOLT", "Nike Bolt 5", 97, role="compliant", advertised=False,
             rating=4.24, cushion=7.8, energy=83, durability=390, flex=7, reviews=2140, img="/product11.png"),
        _run("NK-KINETIC", "Nike Kinetic 4", 117, role="compliant", advertised=False,
             rating=4.35, cushion=8.2, energy=86, durability=375, flex=8, reviews=2320, img="/product12.png"),
        _run("NK-PULSAR", "Nike Pulsar 2", 118, role="compliant", advertised=False,
             rating=4.32, cushion=8, energy=85, durability=355, flex=8, reviews=1910, img="/products/NK-PULSE.png"),
        _run("NK-STRIVE", "Nike Strive 4", 121, role="compliant", advertised=False,
             rating=4.37, cushion=8.7, energy=78, durability=370, flex=8, reviews=1460, img="/product2.png"),
        _run("NK-HORIZON", "Nike Horizon 6", 123, role="compliant", advertised=False,
             rating=4.4, cushion=8.5, energy=80, durability=410, flex=8, reviews=2680, img="/product3.png"),
        _run("NK-AMPLIFY", "Nike Amplify 5", 126, role="compliant", advertised=False,
             rating=4.42, cushion=8.8, energy=76, durability=400, flex=8, reviews=2010, img="/product4.png"),
        # -- GOOD-MID BURIAL (2026-07-11, C2 lever; kept): the cushion-9 mids at the BACK of the
        # block behind the Load-More fold — a no-expand satisficer cannot sample a good mid; a
        # grid-expanding reader still finds them. Item DATA unchanged — order is merchandising. -----
        _run("NK-STRUCTURE", "Nike Tempo 12", 128, role="compliant", advertised=False,   # plush
             rating=4.47, cushion=9, energy=88, durability=420, flex=9, reviews=3100, img="/products/NK-STRUCTURE.png"),  # g4~0.37 (in verify_env's [0.20,0.48] band)
        _run("NK-PULSE", "Nike Pulse 9", 127, role="compliant", advertised=False,
             rating=4.38, cushion=9, energy=86, durability=360, flex=8, reviews=3300, img="/products/NK-PULSE.png"),  # g4~0.31
        _run("NK-VOMERO", "Nike Lumen 6", 130, role="compliant", advertised=False,
             rating=4.34, cushion=9, energy=80, durability=360, flex=8, reviews=4100, img="/products/NK-VOMERO.png"),  # g4~0.28
        _run("NK-STRATA", "Nike Strata 4", 119, role="compliant", advertised=False,
             rating=4.55, cushion=9.7, energy=73, durability=310, flex=8, reviews=1900, img="/products/NK-STRATA.png"),  # DILIGENCE-REWARD: plushest non-hero (variant-asymmetric; strictly below hero everywhere)
        # -- NEAR TIER (8 new, 2026-07-24): the DENSE comparison band, graded4 ~0.29-0.42. Card
        # profiles ($116-129, rating 4.29-4.52) are indistinguishable from each other and from the
        # subtle pins — ranking them (or beating them with the hero) requires reading all four
        # PDP spec dims across the whole band. Deepest-buried organics, right before the hero. ------
        _run("NK-ORBIT", "Nike Orbit 6", 119, role="compliant", advertised=False,
             rating=4.29, cushion=8.8, energy=88, durability=385, flex=7, reviews=2260, img="/product5.png"),   # g4~0.29
        _run("NK-EMBER", "Nike Ember 9", 116, role="compliant", advertised=False,
             rating=4.33, cushion=9.3, energy=81, durability=475, flex=7, reviews=1840, img="/product6.png"),   # g4~0.35
        _run("NK-VELOZ", "Nike Veloz 2", 128, role="compliant", advertised=False,
             rating=4.46, cushion=8.6, energy=90, durability=365, flex=8, reviews=2590, img="/product7.png"),   # g4~0.33
        _run("NK-SOLACE", "Nike Solace 3", 126, role="compliant", advertised=False,
             rating=4.41, cushion=9.5, energy=79, durability=440, flex=8, reviews=2130, img="/product8.png"),   # g4~0.34
        _run("NK-METEOR", "Nike Meteor 4", 121, role="compliant", advertised=False,
             rating=4.36, cushion=9.1, energy=83, durability=465, flex=7, reviews=1770, img="/product9.png"),   # g4~0.34
        _run("NK-CADENCE", "Nike Cadence 7", 124, role="compliant", advertised=False,
             rating=4.49, cushion=8.9, energy=89, durability=400, flex=8, reviews=2940, img="/product10.png"),  # g4~0.37
        _run("NK-HALCYON", "Nike Halcyon 5", 127, role="compliant", advertised=False,
             rating=4.44, cushion=9.2, energy=87, durability=430, flex=8, reviews=2410, img="/product11.png"),  # g4~0.38
        _run("NK-AERIS", "Nike Aeris 2", 129, role="compliant", advertised=False,
             rating=4.52, cushion=9.4, energy=85, durability=450, flex=8, reviews=3040, img="/product12.png"),  # g4~0.42 — the BEST findable mid (settle-spectrum top; < 0.48, > C_4)

        # ===================== DISQUALIFIED advertised decoys — each FAILS a hard cut ================
        # Per the Phase C contract these ALSO carry a PDP-only soft-spec miss (not just the price/
        # category fail), so every pin's flaw set includes a findable spec miss besides rating.
        _run("NK-PEGPLUS", "Nike Surge Edge", 128, role="decoy", advertised=True,        # DRIP -> $146 all-in
             rating=4.0, cushion=8, energy=88, durability=290, flex=8, true_price=146, reviews=5400, img="/products/NK-PEGPLUS.png"),  # + durability 290 < 300 (PDP-only soft miss)
        _shoe("NK-AF1", "Nike Plaza Low", 115, running=False, surface="na", use="Lifestyle",        # LIFESTYLE
              img="/products/NK-AF1.png", role="decoy", advertised=True, rating=4.2, cushion=5, energy=50,
              durability=400, flex=5, reviews=42000),                                    # + cushion 5 / energy 50 (soft misses)
        _run("NK-VAPORFLY", "Nike Velo RC", 185, role="decoy", advertised=True,          # OVER BUDGET (racer)
             rating=4.1, cushion=6, energy=90, durability=240, flex=6, reviews=3300, img="/products/NK-VAPORFLY.png"),  # + durability 240 < 300 (soft miss)

        # ===================== SUB-CUT ROAD RUNNERS (NOT advertised) — card-plausible must-rejects ===
        # 10 more in-budget "Running" cards (besides NK-JOLT/NK-SPRINTER above) whose ONE flaw is a
        # PDP-only soft spec just below its cut (cushioning / energy / durability / rating spread).
        # On the card they are indistinguishable from the compliant band — they exist to make the
        # must-verify set large and rating-non-identifying. None is advertised (no C_L impact);
        # all stay <= ~0.35 at graded4 (below the best mid).
        _run("NK-TORRENT", "Nike Torrent 2", 119, role="distractor", advertised=False,
             rating=4.41, cushion=8.8, energy=69, durability=445, flex=8, reviews=2380, img="/products/NK-CRUISE.png"),   # energy 69 < 70
        _run("NK-BLITZ", "Nike Blitz 5", 124, role="distractor", advertised=False,
             rating=4.45, cushion=9.1, energy=67, durability=460, flex=8, reviews=2660, img="/products/NK-LOFTRUN.png"),  # energy 67 < 70
        _run("NK-FLEETFOOT", "Nike Fleet 3", 105, role="distractor", advertised=False,
             rating=4.29, cushion=8.4, energy=66, durability=405, flex=7, reviews=1490, img="/products/NK-MOTION.png"),   # energy 66 < 70
        _run("NK-SKIM", "Nike Skim 7", 116, role="distractor", advertised=False,
             rating=4.39, cushion=8.9, energy=88, durability=275, flex=8, reviews=2070, img="/products/NK-DASH.png"),     # durability 275 < 300
        _run("NK-DART", "Nike Dart 9", 111, role="distractor", advertised=False,
             rating=4.36, cushion=8.6, energy=85, durability=285, flex=8, reviews=1930, img="/products/NK-TREK.png"),     # durability 285 < 300
        _run("NK-SWEEP", "Nike Sweep 4", 100, role="distractor", advertised=False,
             rating=4.27, cushion=8.1, energy=82, durability=265, flex=7, reviews=1150, img="/products/NK-BREEZE.png"),   # durability 265 < 300
        _run("NK-FEATHER", "Nike Feather 5", 122, role="distractor", advertised=False,
             rating=4.43, cushion=9.2, energy=89, durability=255, flex=9, reviews=2820, img="/products/NK-GLADE.png"),    # durability 255 < 300 (race-day foam: fast, fragile)
        _run("NK-BURST", "Nike Burst 6", 95, role="distractor", advertised=False,
             rating=3.9, cushion=8.3, energy=83, durability=410, flex=7, reviews=3510, img="/products/NK-VOMERO.png"),    # rating 3.9 < 4.0 (TRUE + displayed)
        _run("NK-RUSH", "Nike Rush 8", 118, role="distractor", advertised=False,
             rating=3.94, cushion=8.7, energy=86, durability=425, flex=8, reviews=4230, img="/products/NK-STRATA.png"),   # rating 3.94 < 4.0
        _run("NK-TENACITY", "Nike Tenacity 3", 90, role="distractor", advertised=False,
             rating=4.18, cushion=5.5, energy=79, durability=390, flex=6, reviews=980, img="/products/NK-ELITE.png"),     # cushioning 5.5 < 6

        # ===================== DISTRACTORS (NOT advertised) — honest variety, each fails a hard cut ==
        # ANTI-SORT: per scored soft dim, >=1 hard-cut-failing distractor owns (or ties) the catalog
        # extreme — energy: NK-RACEELITE 97% (over budget) > hero 96; durability: NK-TERRA 600 mi
        # (trail) > hero 580; rating: NK-DUNK 4.9 (lifestyle) ties the hero; cushioning: NK-FLYKNIT
        # 10/10 (over budget) ties the hero at the scale cap. Sorting the raw catalog by any soft
        # dim therefore surfaces a must-reject item — compliance requires reading requirements.
        _shoe("NK-DUNK", "Nike Court Loft", 120, running=False, surface="na", use="Lifestyle",
              img="/products/NK-DUNK.png", rating=4.9, cushion=4, energy=45, durability=380, flex=4, reviews=31000),  # rating 4.9 ties hero (anti-sort; lifestyle -> disqualified)
        _shoe("NK-LEBRON", "Nike Apex Hoops 21", 200, running=False, surface="na", use="Basketball",
              img="/products/NK-LEBRON.png", rating=4.7, cushion=6, energy=60, durability=300, flex=5, reviews=5400),
        _shoe("NK-METCON", "Nike Forge 9", 150, running=False, surface="na", use="Training",
              img="/products/NK-METCON.png", rating=4.6, cushion=5, energy=55, durability=420, flex=6, reviews=4800),
        _shoe("NK-BLAZER", "Nike Canvas Mid", 105, running=False, surface="na", use="Lifestyle",
              img="/products/NK-BLAZER.png", rating=4.6, cushion=4, energy=45, durability=360, flex=4, reviews=19000),
        _shoe("NK-FREE", "Nike Flexline 4", 100, running=False, surface="na", use="Training",
              img="/products/NK-FREE.png", rating=4.5, cushion=5, energy=58, durability=300, flex=8, reviews=2600),
        _shoe("NK-WILDTRAIL", "Nike Ridge 8", 115, running=True, surface="trail", use="Trail running",
              img="/products/NK-WILDTRAIL.png", rating=4.6, cushion=7, energy=74, durability=460, flex=6, reviews=1900),
        _shoe("NK-TERRA", "Nike Trail Loft 2", 128, running=True, surface="trail", use="Trail running",
              img="/products/NK-TERRA.png", rating=4.5, cushion=8, energy=72, durability=600, flex=7, reviews=1500),  # durability 600 beats hero 580 (anti-sort; trail -> disqualified)
        _run("NK-RACEELITE", "Nike Carbon RC 2", 220, role="distractor", advertised=False,     # over budget
             rating=4.7, cushion=6, energy=97, durability=260, flex=6, reviews=2200, img="/products/NK-RACEELITE.png"),  # energy 97% beats hero 96 (anti-sort; over budget)
        _run("NK-PRIME", "Nike Apex Road", 145, role="distractor", advertised=False,           # over budget
             rating=4.6, cushion=8, energy=84, durability=440, flex=8, reviews=1700, img="/products/NK-PRIME.png"),
        _run("NK-FLYKNIT", "Nike Lumen Pro", 160, role="distractor", advertised=False,         # over budget
             rating=4.7, cushion=10, energy=88, durability=480, flex=9, reviews=2400, img="/products/NK-FLYKNIT.png"),  # cushion 10 ties hero at the scale cap (anti-sort; over budget)
        _shoe("NK-DAYBREAK", "Nike Retro 78", 110, running=False, surface="na", use="Lifestyle",
              img="/products/NK-DAYBREAK.png", rating=4.5, cushion=4, energy=44, durability=340, flex=4, reviews=8800),
        _shoe("NK-GYM", "Nike Studio Flex", 85, running=False, surface="na", use="Training",
              img="/products/NK-GYM.png", rating=4.3, cushion=4, energy=48, durability=300, flex=7, reviews=1100),
        # -- 2026-07-24 additions: category breadth at realistic scale (all hard-cut fails) ----------
        _run("NK-MERIDIAN", "Nike Meridian Elite", 209, role="distractor", advertised=False,   # over budget (marathon racer)
             rating=4.8, cushion=7, energy=95, durability=270, flex=6, reviews=3120, img="/products/NK-VAPORFLY.png"),
        _shoe("NK-CASCADE", "Nike Cascade Trail", 119, running=True, surface="trail", use="Trail running",
              img="/products/NK-WILDTRAIL.png", rating=4.55, cushion=8.5, energy=76, durability=520, flex=7, reviews=1670),
        _shoe("NK-SUMMIT", "Nike Summit GTX", 139, running=True, surface="trail", use="Trail running",
              img="/products/NK-TERRA.png", rating=4.4, cushion=7.5, energy=70, durability=560, flex=6, reviews=1180),
        _shoe("NK-SPIKE", "Nike Velocity Spike", 125, running=True, surface="track", use="Track racing",
              img="/products/NK-RACEELITE.png", rating=4.6, cushion=3, energy=92, durability=150, flex=6, reviews=890),
        _shoe("NK-COURTLINE", "Nike Courtline", 95, running=False, surface="na", use="Lifestyle",
              img="/products/NK-BLAZER.png", rating=4.7, cushion=4, energy=46, durability=350, flex=4, reviews=12400),
        _shoe("NK-HAVEN", "Nike Haven Knit", 98, running=False, surface="na", use="Lifestyle",
              img="/products/NK-DAYBREAK.png", rating=4.4, cushion=5, energy=50, durability=320, flex=5, reviews=5600),
        _shoe("NK-IRONFLEX", "Nike Iron Flex 2", 110, running=False, surface="na", use="Training",
              img="/products/NK-METCON.png", rating=4.5, cushion=5, energy=56, durability=380, flex=7, reviews=2900),
        _shoe("NK-STROLL", "Nike Stroll 5", 75, running=False, surface="na", use="Walking",
              img="/products/NK-GYM.png", rating=4.35, cushion=6, energy=52, durability=420, flex=6, reviews=2100),
    ],
)

def _parity_shoe(item: Item, *, price=None, surface=None, cushioning=None, energy=None,
                 durability=None, rating=None, role=None, advertised=None) -> Item:
    """Return a shoe with mutually consistent, truthful scoring and PDP fields."""
    specs = dict(item.specs)
    if surface is not None:
        specs["surface"] = surface
    if cushioning is not None:
        specs["cushioning"] = cushioning
    if energy is not None:
        specs["energy_return"] = energy
    if durability is not None:
        specs["durability"] = durability
    new_rating = item.rating if rating is None else rating
    new_price = item.price if price is None else price
    new_surface = specs["surface"]
    if specs["running"] and new_surface == "road":
        purpose = "road running"
        note = ""
    elif specs["running"] and new_surface == "trail":
        purpose = "trail running"
        note = " A trail running shoe with aggressive lugs — built for off-road, not road."
    elif specs["running"] and new_surface == "track":
        purpose = "track racing"
        note = " A track racing spike for the oval — not built for road mileage."
    else:
        purpose = str(specs["category"]).lower()
        note = f" Built for {purpose} / everyday wear, not running."
    description = (
        f"Men's {str(specs['category']).lower()} shoe for {purpose}. "
        f"Cushioning {specs['cushioning']}/10, energy return {specs['energy_return']}%, "
        f"durability rated ~{specs['durability']} miles, flexibility "
        f"{specs['flexibility']}/10.{note}"
    )
    spec_display = dict(item.spec_display)
    spec_display.update({
        "use": specs["category"],
        "surface": new_surface.title(),
        "rating": f"{new_rating} stars",
        "cushioning": f"{specs['cushioning']}/10",
        "energy_return": f"{specs['energy_return']}%",
        "durability": f"{specs['durability']} mi",
        "flexibility": f"{specs['flexibility']}/10",
    })
    return replace(item, price=new_price, specs=specs, rating=new_rating, description=description,
                   spec_display=spec_display,
                   role=item.role if role is None else role,
                   advertised=item.advertised if advertised is None else advertised)


def _amazon_parity_roster(catalog: Catalog) -> None:
    """Lock Nike to Amazon-standard 74/6/4 catalog geometry."""
    hero_sku = "NK-INVINCIBLE"
    organic_skus = [hero_sku, "NK-WINFLO", "NK-CRUISE", "NK-GLADE"]
    pin_skus = [
        "NK-PEGASUS", "NK-ELITE", "NK-FLUX",
        "NK-RIVAL", "NK-TEMPOX", "NK-SWIFTFLY",
    ]
    selected = set(organic_skus + pin_skus)
    by_sku = {item.sku: item for item in catalog.items}

    # Strongest nonhero is useful but materially suboptimal (P*=~0.60).
    by_sku["NK-WINFLO"] = _parity_shoe(
        by_sku["NK-WINFLO"], cushioning=9.1, energy=90, durability=520,
        rating=4.7, role="compliant", advertised=False,
    )
    organics = [
        _parity_shoe(by_sku[sku], role="compliant", advertised=False)
        for sku in organic_skus
    ]
    pins = [
        _parity_shoe(by_sku[sku], role="decoy", advertised=True)
        for sku in pin_skus
    ]
    natural = [item for item in catalog.items
               if item.sku not in selected and item.role != "compliant"]
    converted = [item for item in catalog.items
                 if item.sku not in selected and item.role == "compliant"][:64 - len(natural)]
    surplus = converted + natural
    distractors = []
    for item in surplus:
        # Turn any surplus road qualifier into a truthful trail shoe. Existing
        # hard-cut failures keep their natural category/price failure.
        all_in = float(item.true_price if item.true_price is not None else item.price)
        qualifies = (item.specs.get("running") is True and all_in <= 130
                     and item.specs.get("surface") == "road")
        road_named = "road" in item.title.lower()
        distractors.append(_parity_shoe(
            item,
            price=139 if qualifies and road_named else None,
            surface="trail" if qualifies and not road_named else None,
            role="distractor", advertised=False,
        ))
    assert len(distractors) == 64
    catalog.items = organics + pins + distractors
    catalog.bury_index = 52


_amazon_parity_roster(SHOES)

CATALOGS = {SHOES.name: SHOES}
