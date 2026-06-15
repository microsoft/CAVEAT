"""Generate the 6 figure configs (headline, scale, vintage, effort, xfamily, hidden) for each of the
5 benchmark products, plus a 6-figure AGGREGATE across all five — all on the canonical palette so
colours are consistent across every figure (same model = same hue; effort/scale/visibility = same hue
+ alpha; OpenAI=blues, Grok=red, DeepSeek=purple). Writes benchmark_data/reports/fig_configs/*.json.

Run-name scheme per product prefix P (laptop uses its own legacy names, mapped below):
  P_g55 (gpt-5.5)  P_g41 (gpt-4.1)  P_scale (5.4/-mini/-nano)  P_vint (5/5.1/5.2/5.4)
  P_eff (5.5 low/med/high)  P_xfam (grok/gpt-oss/DeepSeek)  P_hid (5.5/4.1 on PDP-only catalog)
"""
import json
from pathlib import Path

OUT = Path("benchmark_data/reports/fig_configs")
OUT.mkdir(parents=True, exist_ok=True)
VARIANTS = [["thresholded", "0"], ["mixed", "1"], ["graded", "2"], ["graded3", "3"], ["graded4", "4"]]
XLAB = "preference relativeness   (0 = absolute thresholds   →   4 = fully graded)"

# canonical per-model colours (identical across every figure)
C = {"gpt-5.5": "#1f4e79", "gpt-5.4": "#2f6da3", "gpt-5.2": "#5089bf", "gpt-5.1": "#7aa9d4",
     "gpt-5": "#a9c9e4", "gpt-4.1": "#6aa0d0", "gpt-oss": "#3d6f9e",
     "grok": "#c0392b", "deepseek": "#7d4fa0"}

# the 5 products: prefix -> (display title, glob-name map per sweep)
# laptop keeps its already-run legacy names; the 4 new products use the P_* scheme.
def newmap(P):
    # the 4 new products run ALL expansion models (vint/scale/effort/xfamily) in ONE P_exp run;
    # figures filter by model name within the glob, so every sweep points at P_exp.
    exp = [f"results/{P}_exp_r*"]
    return {"g55": [f"results/{P}_g55_r*"], "g41": [f"results/{P}_g41_r*"],
            "scale": exp, "vint": exp, "eff": exp, "xfam": exp,
            "hid": [f"results/{P}_hid_r*"]}

LAPMAP = {"g55": ["results/lap_gN_g55_r*", "results/lap_gN_g55b_r*"],
          "g41": ["results/lap_gN3_g41_r*"], "scale": ["results/lap_scale54_r*"],
          "vint": ["results/lap_vintage5_r*", "results/lap_vintage5b_r*"],
          "eff": ["results/lap_effort55_r*", "results/lap_effort55b_r*"],
          "xfam": ["results/lap_xfam_r*", "results/lap_xfam2_r*",
                   "results/lap_xfam_ds_r*", "results/lap_xfam_ds2_r*"],
          "hid": ["results/lap_hidden_r*"]}

PRODUCTS = {
    "laptop": ("Laptop", LAPMAP),
    "office_chair": ("Office chair", newmap("oc")),
    "mattress": ("Mattress", newmap("mat")),
    "backpack": ("Backpack", newmap("bp")),
    "tent": ("Tent", newmap("tent")),
}


def S(label, globs, model, color, alpha=1.0, gap=0.0, scaffold=None, scenario=None):
    d = {"label": label, "globs": globs, "model": model, "color": color, "alpha": alpha}
    if gap:
        d["gap_before"] = gap
    if scaffold:
        d["scaffold"] = scaffold
    if scenario:
        d["scenario"] = scenario
    return d


def figs_for(mp):
    """Return {figtype: series-list} for one product's glob-map (or aggregated map)."""
    g = mp
    return {
        "headline": [
            S("gpt-5.5", g["g55"], "gpt-5.5", C["gpt-5.5"]),
            S("gpt-4.1", g["g41"], "gpt-4.1", C["gpt-4.1"]),
        ],
        "scale": [
            S("gpt-5.4", g["scale"], "gpt-5.4", C["gpt-5.4"], 1.0),
            S("gpt-5.4-mini", g["scale"], "gpt-5.4-mini", C["gpt-5.4"], 0.6),
            S("gpt-5.4-nano", g["scale"], "gpt-5.4-nano", C["gpt-5.4"], 0.34),
        ],
        # gpt-5.2 dropped: on the non-electronic stores it completes CLEAN but fails COMBINED even at
        # low concurrency (capability/efficiency limit — ~197 steps for a clean buy), so it has no
        # steered data. Vintage line is gpt-5 → 5.1 → 5.4 → 5.5 (4 points still show the trend).
        "vintage": [
            S("gpt-5.5", g["g55"], "gpt-5.5", C["gpt-5.5"]),
            S("gpt-5.4", g["vint"], "gpt-5.4", C["gpt-5.4"]),
            S("gpt-5.1", g["vint"], "gpt-5.1", C["gpt-5.1"]),
            S("gpt-5", g["vint"], "gpt-5", C["gpt-5"]),
        ],
        "effort": [
            S("gpt-5.5 · high", g["eff"], "gpt-5.5-high", C["gpt-5.5"], 1.0),
            S("gpt-5.5 · medium", g["eff"], "gpt-5.5-medium", C["gpt-5.5"], 0.6),
            S("gpt-5.5 · low", g["eff"], "gpt-5.5-low", C["gpt-5.5"], 0.34),
        ],
        "xfamily": [
            S("gpt-5.5", g["g55"], "gpt-5.5", C["gpt-5.5"]),
            S("gpt-5.4", g["vint"], "gpt-5.4", C["gpt-5.4"]),
            S("gpt-5.1", g["vint"], "gpt-5.1", C["gpt-5.1"]),
            S("gpt-5", g["vint"], "gpt-5", C["gpt-5"]),
            S("gpt-4.1", g["g41"], "gpt-4.1", C["gpt-4.1"]),
            S("gpt-oss-120b", g["xfam"], "gpt-oss-120b", C["gpt-oss"]),
            S("grok-4.1", g["xfam"], "grok-4-1-fast-non-reasoning", C["grok"], 1.0, 13),
            S("DeepSeek-V4", g["xfam"], "DeepSeek-V4-Pro", C["deepseek"], 1.0, 13),
        ],
        "hidden": [
            S("gpt-5.5 · card", g["g55"], "gpt-5.5", C["gpt-5.5"], 1.0),
            S("gpt-5.5 · PDP", g["hid"], "gpt-5.5", C["gpt-5.5"], 0.42),
            S("gpt-4.1 · card", g["g41"], "gpt-4.1", C["gpt-4.1"], 1.0),
            S("gpt-4.1 · PDP", g["hid"], "gpt-4.1", C["gpt-4.1"], 0.42),
        ],
    }


XLAB_FIG = {
    "headline": XLAB, "vintage": XLAB + "   ·   bars L→R: newer → older vintage",
    "scale": "preference relativeness   (0 = thresholds  →  4 = fully graded)   ·   bars L→R: larger → smaller",
    "effort": "preference relativeness   (0 = thresholds  →  4 = fully graded)   ·   bars L→R: high → low effort",
    "hidden": XLAB + "   ·   solid = specs on card, faded = specs PDP-only",
    "xfamily": XLAB + "   ·   grouped by family: OpenAI · Grok · DeepSeek",
    "scaffold": XLAB + "   ·   each bar = an agent scaffold (gpt-4.1)",
}
# agent-scaffold comparison: distinct hues per harness
SCAFFOLD_COLOR = {"browseruse": "#2f6da3", "playwright-mcp": "#c0762a", "stagehand": "#3a8a4a"}
# scenario_id per product prefix (for filtering the combined playwright-mcp run by product)
PREFIX_SCENARIO = {"lap": "laptop", "oc": "office_chair", "mat": "mattress", "bp": "backpack", "tent": "tent"}
PWMCP_GLOB = ["results/pwmcp_g41_r*"]    # the playwright-mcp sweep (all 5 products, gpt-4.1)
SHAND_GLOB = ["results/shand_g41_r*"]    # the stagehand sweep (all 5 products, gpt-4.1)


def write_cfg(name, title, series, figtype):
    cfg = {"title": title, "xlabel": XLAB_FIG[figtype], "condition": "combined",
           "variants": VARIANTS, "series": series}
    (OUT / f"{name}.json").write_text(json.dumps(cfg, indent=2))
    print(f"wrote {name}.json ({len(series)} series)")


def main():
    # per-product configs (new prefix scheme; laptop already has hand-written configs but we
    # regenerate consistently here too so all 5 share one source of truth)
    prefix = {"laptop": "lap", "office_chair": "oc", "mattress": "mat", "backpack": "bp", "tent": "tent"}

    def g41_hidden(mp):
        # gpt-5.5 PDP-only (hidden) is BLOCKED for the new products (PhyAGI monthly cap + TRAPI
        # gpt-5.5 throttle) -> gpt-4.1-only hidden panel (card vs PDP). Laptop keeps the full 4-series.
        return [S("gpt-4.1 · card", mp["g41"], "gpt-4.1", C["gpt-4.1"], 1.0),
                S("gpt-4.1 · PDP", mp["hid"], "gpt-4.1", C["gpt-4.1"], 0.42)]

    def scaffold_fig(g41_globs, scenario):
        # clean/steered with each bar = an agent scaffold (browser-use vs playwright-mcp), both gpt-4.1.
        # (stagehand was trialed but excluded: it gives up ~40% of cells under concurrency — too flaky
        #  a completer to be a fair evaluation subject.)
        return [S("browser-use", g41_globs, "gpt-4.1", SCAFFOLD_COLOR["browseruse"], scaffold="browseruse"),
                S("playwright-mcp", PWMCP_GLOB, "gpt-4.1", SCAFFOLD_COLOR["playwright-mcp"],
                  scaffold="playwright-mcp", scenario=scenario)]

    for sid, (title, mp) in PRODUCTS.items():
        figs = figs_for(mp)
        if sid != "laptop":
            figs["hidden"] = g41_hidden(mp)
        figs["scaffold"] = scaffold_fig(mp["g41"], sid)
        for ft, series in figs.items():
            write_cfg(f"{prefix[sid]}_{ft}", "", series, ft)
    # aggregate across all 5 products: union the globs per sweep
    agg = {k: sum((PRODUCTS[s][1][k] for s in PRODUCTS), []) for k in LAPMAP}
    aggfigs = figs_for(agg)
    aggfigs["hidden"] = g41_hidden(agg)   # 5.5-PDP exists only for laptop -> agg hidden = gpt-4.1 card vs PDP
    aggfigs["scaffold"] = scaffold_fig(agg["g41"], None)   # all 5 products, no scenario filter
    for ft, series in aggfigs.items():
        write_cfg(f"agg_{ft}", "", series, ft)


if __name__ == "__main__":
    main()
