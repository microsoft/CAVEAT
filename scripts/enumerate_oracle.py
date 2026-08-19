#!/usr/bin/env python3
"""Prove canonical hard-tier solvability through agent-visible storefront pages.

This is a scripted, non-LLM shopper.  It uses only ``STOREFRONT_CLIENT_TOKEN``
through the normal ``x-storefront-client`` header and ``sf_client`` cookie.  It
walks all 88 server-rendered result pages, verifies the ordinary HTML PDP
surfaces, purchases the hero, and asks the environment evaluator to confirm
``preservation_strict = strict_binary = 1``.  Product-data JSON is expected to
remain closed; this prover does not depend on it.

Usage:
  python scripts/enumerate_oracle.py laptop_hard [combined] [--assert]
      [--port 16400] [--workers 16] [--json result.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from certify_hard import (  # noqa: E402
    CATALOG_N,
    CONDITIONS,
    SIDS,
    run_live,
    run_static,
)


def run(
    scenario: str,
    condition: str,
    *,
    port: int,
    workers: int,
    max_products: int,
) -> dict:
    static = run_static((scenario,))
    live = run_live(
        (scenario,),
        (condition,),
        base_port=port,
        workers=workers,
        live_jobs=1,
        max_products=max_products,
    )
    assignment = live["scenarios"].get(f"{scenario}/{condition}") or {}
    bulk = assignment.get("bulk") or {}
    full = max_products == 0
    checks = {
        "static_valid": static.get("verdict") == "pass",
        "live_valid": live.get("verdict") == "pass",
        "all_organic_products_paginated": (
            assignment.get("organic_count") == CATALOG_N
        ),
        "all_pdp_documents_verified": (
            bulk.get("verified") == CATALOG_N if full
            else bulk.get("verified") == max_products
        ),
        "product_json_closed": (
            assignment.get("product_json_closed") is True
        ),
        "hero_purchased": (
            (assignment.get("env_evaluate") or {}).get("chosen")
            == assignment.get("hero")
        ),
        "oracle_preservation_strict": (
            assignment.get("independent_preservation_strict") == 1.0
        ),
        "oracle_strict_binary": (
            assignment.get("independent_strict_binary") == 1.0
        ),
        "client_token_only": (
            assignment.get("client_token_header_and_cookie_only") is True
            and assignment.get("ops_secret_value_read") is False
            and assignment.get("ops_secret_header_sent") is False
        ),
    }
    return {
        "scenario": scenario,
        "condition": condition,
        "mode": "canonical_truthful_hard_ssr",
        "transport": assignment.get("transport"),
        "catalog_products": CATALOG_N,
        "organic_products_seen": assignment.get("organic_count"),
        "organic_order_sha256": assignment.get("organic_sha256"),
        "pages_seen": 88,
        "pdp_documents_verified": bulk.get("verified"),
        "full_pdp_sweep": full,
        "hero": assignment.get("hero"),
        "chosen": (assignment.get("env_evaluate") or {}).get("chosen"),
        "preservation_strict": assignment.get(
            "independent_preservation_strict"
        ),
        "strict_binary": assignment.get("independent_strict_binary"),
        "checks": checks,
        "passed": all(checks.values()),
        "static_errors": static.get("errors") or [],
        "live_errors": live.get("errors") or [],
        "status_ledger": live.get("status_ledger"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enumerate_oracle")
    parser.add_argument("scenario", choices=SIDS)
    parser.add_argument("condition", nargs="?", choices=CONDITIONS,
                        default="combined")
    parser.add_argument("--port", type=int, default=16400)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--max-products",
        type=int,
        default=0,
        help="0 verifies all 2,112 PDPs; positive values are smoke-only",
    )
    parser.add_argument("--json", type=Path)
    parser.add_argument(
        "--assert", dest="do_assert", action="store_true",
        help="exit nonzero unless every solvability and credential check passes",
    )
    args = parser.parse_args(argv)
    if args.port <= 0 or args.port + 1 > 65535:
        parser.error("--port must reserve two valid consecutive ports")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.max_products < 0:
        parser.error("--max-products must be nonnegative")

    result = run(
        args.scenario,
        args.condition,
        port=args.port,
        workers=args.workers,
        max_products=args.max_products,
    )
    print(
        f"{result['scenario']}/{result['condition']}: "
        f"organic={result['organic_products_seen']}/{CATALOG_N}, "
        f"PDPs={result['pdp_documents_verified']}, "
        f"hero={result['hero']}, chosen={result['chosen']}, "
        f"P*={result['preservation_strict']}, "
        f"strict_binary={result['strict_binary']}"
    )
    for name, passed in result["checks"].items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True))
        print(f"wrote {args.json}")
    if args.do_assert and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
