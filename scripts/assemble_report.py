"""Assemble the final findings report: inject results tables (from report_tables) into the prose
template (five_product_findings.md) at each <!-- TABLE:group --> marker. Writes in place.

Usage: python scripts/assemble_report.py
"""
import io
import re
from contextlib import redirect_stdout

import report_tables as RT

REPORT = "benchmark_data/reports/five_product_findings.md"
OUT = "benchmark_data/reports/five_product_findings_FINAL.md"
PRETTY = {"laptop": "Laptop (electronics)", "office_chair": "Office chair (furniture)",
          "mattress": "Mattress (bedding)", "backpack": "Backpack (travel)",
          "tent": "Tent (outdoors)"}


def group_md(group):
    """Markdown tables for one figure group: each product + the aggregate."""
    out = []
    for sid in RT.PRODUCTS:
        buf = io.StringIO()
        with redirect_stdout(buf):
            RT.table(group, [RT.PREFIX[sid]], PRETTY.get(sid, sid))
        out.append(buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        RT.table(group, [RT.PREFIX[s] for s in RT.PRODUCTS], "AGGREGATE (all 5 products)")
    out.append(buf.getvalue())
    return "\n".join(out)


def main():
    text = open(REPORT).read()

    def repl(m):
        g = m.group(1)
        try:
            return group_md(g)
        except Exception as e:
            return f"_(table {g} unavailable: {e})_"

    text = re.sub(r"<!-- TABLE:(\w+) -->", repl, text)
    open(OUT, "w").write(text)
    print(f"assembled {OUT}")


if __name__ == "__main__":
    main()
