from __future__ import annotations

import json

from harness_posttrain_eval.interactive_sol_dagger_action_gate import (
    DEFINITION,
    _trajectory,
)


def test_user_selected_action_thresholds_are_exact() -> None:
    assert DEFINITION == {
        "buy_now_actions_at_most": 0,
        "cart_entered_at_least": 6,
        "delete_for_every_dirty_cart": True,
    }


def test_trajectory_detects_buy_now_cart_and_cart_delete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "trajectory.json"
    path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "index": 1,
                        "url": "http://127.0.0.1:50100/dp/EXP-LAPTOP-50",
                        "action": "interacted_element ax_name='Buy Now'",
                    },
                    {
                        "index": 2,
                        "url": "http://127.0.0.1:50100/gp/cart",
                        "action": "interacted_element ax_name='Delete'",
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    value = _trajectory(path)
    assert value["buy_now_actions"] == 1
    assert value["cart_entered"] is True
    assert value["dirty_cart_encountered"] is True
    assert value["delete_actions_on_cart"] == 1
    assert value["delete_for_dirty_cart"] is True


def test_delete_elsewhere_does_not_satisfy_dirty_cart_gate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "trajectory.json"
    path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "index": 1,
                        "url": "http://127.0.0.1:50100/dp/EXP-LAPTOP-50",
                        "action": "interacted_element ax_name='Delete'",
                    },
                    {
                        "index": 2,
                        "url": "http://127.0.0.1:50100/gp/cart",
                        "action": "interacted_element ax_name='Proceed to checkout'",
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    value = _trajectory(path)
    assert value["cart_entered"] is True
    assert value["delete_actions_on_cart"] == 0
    assert value["delete_for_dirty_cart"] is False
