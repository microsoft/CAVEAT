"""Benchmark generation CLI.

    python -m agentarena.benchmark.cli generate --scenario laptop --seed 7
    python -m agentarena.benchmark.cli generate --all
    python -m agentarena.benchmark.cli generate --scenario monitor --no-images
    python -m agentarena.benchmark.cli list
"""

from __future__ import annotations

import argparse
import asyncio

from .build import build_scenario
from .scenarios import SCENARIOS, THIS_PASS


def main() -> int:
    ap = argparse.ArgumentParser(prog="agentarena.benchmark.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate scenario artifact(s)")
    g.add_argument("--scenario", help="scenario id (default: this-pass set)")
    g.add_argument("--all", action="store_true", help="generate all this-pass scenarios")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--no-images", action="store_true", help="use stock images (fast)")
    g.add_argument("--force-images", action="store_true")

    sub.add_parser("list", help="list scenarios")

    args = ap.parse_args()
    if args.cmd == "list":
        for sid, s in SCENARIOS.items():
            tag = " (this pass)" if sid in THIS_PASS else ""
            print(f"  {sid:14s} {s.noun:22s} cat={s.category_slug}{tag}")
        return 0

    if args.cmd == "generate":
        ids = THIS_PASS if (args.all or not args.scenario) else [args.scenario]

        async def run():
            for sid in ids:
                await build_scenario(sid, seed=args.seed, with_images=not args.no_images,
                                     force_images=args.force_images)

        asyncio.run(run())
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
