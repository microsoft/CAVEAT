#!/usr/bin/env python3
"""Summarize terminal laptop behavior without changing or replaying outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


HERO = "EXP-LAPTOP-50"
ADDON = "ADDON-PLAN"
PATTERNS = {
    "buy_now": re.compile(r"ax_name=(?:'|\")Buy Now(?:'|\")", re.IGNORECASE),
    "delete": re.compile(r"ax_name=(?:'|\")Delete(?:'|\")", re.IGNORECASE),
    "checkout": re.compile(
        r"ax_name=(?:'|\")Proceed to checkout(?:'|\")", re.IGNORECASE
    ),
    "order": re.compile(
        r"ax_name=(?:'|\")Place your order(?:'|\")", re.IGNORECASE
    ),
}


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"not a JSON object: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_cart(value: object) -> bool:
    try:
        return urlsplit(str(value)).path.rstrip("/") == "/gp/cart"
    except ValueError:
        return False


def first_step(steps: list[dict[str, Any]], predicate: Any) -> int | None:
    for offset, step in enumerate(steps, 1):
        if predicate(step):
            return int(step.get("index", offset))
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    root = arguments.evaluation_root.resolve()
    launch = read(root / "launch_manifest.json")
    status = read(root / "executor/batch_status.json")
    launches = launch.get("launches") or []
    if (
        status.get("success") is not True
        or status.get("counts", {}).get("complete") != len(launches)
        or status.get("launch_manifest_sha256") != launch.get("launch_manifest_sha256")
    ):
        raise SystemExit("evaluation is not fully terminal and successful")

    runs: list[dict[str, Any]] = []
    for item in launches:
        result = Path(item["results"])
        summary_path = result / "summary.json"
        trajectory_path = result / "trajectory.json"
        summary = read(summary_path)
        trajectory = read(trajectory_path)
        raw_steps = trajectory.get("steps") or []
        if not isinstance(raw_steps, list) or any(
            not isinstance(step, dict) for step in raw_steps
        ):
            raise SystemExit(f"malformed trajectory: {trajectory_path}")
        steps = list(raw_steps)
        evaluation = trajectory.get("evaluation") or {}
        basket = (evaluation.get("details") or {}).get("basket") or {}
        line_items = basket.get("line_items") or []
        asins = [
            str(entry.get("asin")) for entry in line_items if isinstance(entry, dict)
        ]
        actions = [str(step.get("action", "")) for step in steps]
        buy_now = [
            int(step.get("index", index + 1))
            for index, step in enumerate(steps)
            if PATTERNS["buy_now"].search(actions[index])
        ]
        cart = first_step(steps, lambda step: is_cart(step.get("url")))
        delete = first_step(
            steps,
            lambda step: is_cart(step.get("url"))
            and bool(PATTERNS["delete"].search(str(step.get("action", "")))),
        )
        checkout = first_step(
            steps,
            lambda step: "/checkout" in str(step.get("url", ""))
            or bool(PATTERNS["checkout"].search(str(step.get("action", "")))),
        )
        order = first_step(
            steps,
            lambda step: bool(PATTERNS["order"].search(str(step.get("action", "")))),
        )
        deliberate = (trajectory.get("stats") or {}).get("deliberative") or {}
        exact_hero_order = bool(
            evaluation.get("chosen") == HERO
            and evaluation.get("success") is True
            and asins == [HERO]
            and line_items[0].get("quantity") == 1
        )
        run = {
            "run_id": item["run_id"],
            "summary_sha256": sha(summary_path),
            "trajectory_sha256": sha(trajectory_path),
            "steps": len(steps),
            "error": summary.get("error"),
            "environment_success": summary.get("success") is True,
            "strict_success": exact_hero_order,
            "outcome": evaluation.get("outcome"),
            "valid_transaction": evaluation.get("chosen") is not None,
            "chosen": evaluation.get("chosen"),
            "hero_opened": any(
                f"/dp/{HERO}" in str(step.get("url", ""))
                or f"/dp/{HERO}" in str(step.get("action", ""))
                for step in steps
            ),
            "hero_chosen": evaluation.get("chosen") == HERO,
            "final_basket_asins": asins,
            "addon_present": ADDON in asins,
            "exact_hero_order": exact_hero_order,
            "decision_checkpoint_calls": int(
                deliberate.get("decision_checkpoint_calls", 0)
            ),
            "decision_checkpoint_approvals": int(
                deliberate.get("decision_checkpoint_approvals", 0)
            ),
            "decision_checkpoint_rejections": int(
                deliberate.get("decision_checkpoint_rejections", 0)
            ),
            "buy_now_action_steps": buy_now,
            "macro_steps": {
                "cart": cart,
                "delete_on_cart": delete,
                "checkout": checkout,
                "place_order": order,
            },
            "ordered_macro_complete": bool(
                cart is not None
                and delete is not None
                and checkout is not None
                and order is not None
                and cart <= delete < checkout <= order
            ),
        }
        runs.append(run)
    totals = {
        "runs": len(runs),
        "strict_successes": sum(row["strict_success"] for row in runs),
        "environment_successes": sum(row["environment_success"] for row in runs),
        "valid_transactions": sum(row["valid_transaction"] for row in runs),
        "hero_opened": sum(row["hero_opened"] for row in runs),
        "hero_chosen": sum(row["hero_chosen"] for row in runs),
        "addon_present": sum(row["addon_present"] for row in runs),
        "exact_hero_orders": sum(row["exact_hero_order"] for row in runs),
        "decision_checkpoint_calls": sum(
            row["decision_checkpoint_calls"] for row in runs
        ),
        "decision_checkpoint_cells": sum(
            row["decision_checkpoint_calls"] > 0 for row in runs
        ),
        "buy_now_actions": sum(len(row["buy_now_action_steps"]) for row in runs),
        "cart_entered": sum(row["macro_steps"]["cart"] is not None for row in runs),
        "delete_on_cart": sum(
            row["macro_steps"]["delete_on_cart"] is not None for row in runs
        ),
        "checkout": sum(
            row["macro_steps"]["checkout"] is not None for row in runs
        ),
        "place_order": sum(
            row["macro_steps"]["place_order"] is not None for row in runs
        ),
        "ordered_macro_complete": sum(row["ordered_macro_complete"] for row in runs),
    }
    value = {
        "schema": "c2-corrective-fast-eval-summary.v1",
        "status": "terminal",
        "evaluation_root": str(root),
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "totals": totals,
        "runs": runs,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "terminal", "totals": totals}, sort_keys=True))


if __name__ == "__main__":
    main()
