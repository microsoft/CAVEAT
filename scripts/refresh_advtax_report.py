#!/usr/bin/env python
"""Regenerate the taxonomy results table (in place, inside the findings report) + the figure.

Idempotent: rewrites everything between the RESULTS markers, so it can be re-run as the matrix
fills without touching any of the prose around it.

    python scripts/refresh_advtax_report.py [results/advtax_v1]
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "results/advtax_v1"
REPORT = ROOT / "benchmark_data" / "reports" / "adv_taxonomy_findings.md"
BEGIN, END = "<!-- BEGIN RESULTS -->", "<!-- END RESULTS -->"

NAME = {
    "clean": "clean storefront (baseline)", "adv-hidden": "visually-nulled text injection",
    "adv-apighost": "consumption-channel cloaking", "adv-subllm": "extraction-channel payload",
    "adv-suppress": "selective truth suppression", "adv-metrology": "metrological framing",
    "adv-flood": "observation-window flooding", "adv-promptfmt": "control-frame forgery",
    "adv-filter": "corrupted verification affordances", "adv-precomputed": "computation substitution",
    "adv-costblind": "budget-integrity attack", "adv-budget": "verification-cost asymmetry",
    "adv-principal": "forged principal state", "adv-policy": "automation-policy framing",
    "adv-consensus": "machine-directed social proof", "adv-all": "all deniable families stacked",
    "adv-exec": "transaction substitution (beyond deniable)",
}
GROUP = [
    ("", ["clean"]),
    ("**Layer 1 — channel asymmetry**", ["adv-hidden", "adv-apighost", "adv-subllm", "adv-suppress"]),
    ("**Layer 2 — comprehension**", ["adv-metrology", "adv-flood", "adv-promptfmt"]),
    ("**Layer 3 — delegated verification**",
     ["adv-filter", "adv-precomputed", "adv-costblind", "adv-budget"]),
    ("**Layer 4 — principal & authority**", ["adv-principal", "adv-policy", "adv-consensus"]),
    ("**Combined**", ["adv-all"]),
    ("**Layer 5 — beyond deniable**", ["adv-exec"]),
]
SC = ["backpack", "laptop", "mattress", "office_chair", "tent"]


def main() -> int:
    subprocess.run([sys.executable, "scripts/score_advtax.py", PREFIX], check=True,
                   capture_output=True, cwd=ROOT)
    t = json.load(open(ROOT / f"{PREFIX.rstrip('/')}_table.json"))

    rows = ["| family | " + " | ".join(s.replace("_", " ") for s in SC)
            + " | **pooled** | 95% CI | n |",
            "|---|" + "---:|" * len(SC) + "---:|:---:|---:|"]
    for g, fams in GROUP:
        if g:
            rows.append(f"| {g} |" + " |" * (len(SC) + 3))
        for f in fams:
            d = t.get(f)
            if not d or not d["n"]:
                continue
            cells = " | ".join(
                f'{d["by_scenario"].get(s):.2f}' if d["by_scenario"].get(s) is not None else "–"
                for s in SC)
            rows.append(f'| {NAME.get(f, f)} | {cells} | **{d["pooled"]:.3f}** | '
                        f'[{d["lo"]:.2f}, {d["hi"]:.2f}] | {d["n"]} |')
    total = sum(d["n"] for d in t.values())
    table = "\n".join(rows) + f"\n\n*{total} scored runs. Regenerate: `scripts/refresh_advtax_report.py`.*"

    s = REPORT.read_text()
    if BEGIN in s and END in s:
        head, rest = s.split(BEGIN, 1)
        _old, tail = rest.split(END, 1)
        s = f"{head}{BEGIN}\n{table}\n{END}{tail}"
    else:                                     # first run: wrap the existing table in markers
        import re
        m = re.search(r"\| family \| backpack.*?(?=\n\n\*\*Seven families)", s, re.S)
        if not m:
            print("could not locate the results table; leaving the report alone")
            return 1
        s = s[:m.start()] + f"{BEGIN}\n{table}\n{END}" + s[m.end():]
    REPORT.write_text(s)
    subprocess.run([sys.executable, "scripts/fig_advtax.py", PREFIX], check=True, cwd=ROOT)
    print(f"refreshed report table ({total} runs) + fig_advtax.png/.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
