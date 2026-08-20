from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import write_json_create_only, write_text_create_only


def _percent(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    headline = report["headline"]
    held_out = report["held_out"]
    clean = report["clean_guards"]
    lines = [
        f"# {report['campaign_id']} final matched evaluation",
        "",
        f"Outcome: **{'SUCCESS' if report['success'] else 'NOT ESTABLISHED'}**",
        "",
        "## Headline combined condition",
        "",
        "| Measure | Base | Selected | Difference |",
        "|---|---:|---:|---:|",
        (
            f"| Optimal-selection rate | {_percent(headline['base_optimal_selection_rate'])} | "
            f"{_percent(headline['selected_optimal_selection_rate'])} | "
            f"{_percent(headline['optimal_selection_rate_difference'])} |"
        ),
        "",
        (
            "Optimal-selection-rate one-sided 95% lower bound: "
            f"{headline['optimal_selection_rate_one_sided_95_lower_bound']:.4f}; "
            f"exact McNemar p={headline['mcnemar']['pvalue_one_sided']:.6g}; "
            f"sign-randomization p={headline['sign_randomization_pvalue']:.6g}."
        ),
        "",
        "## Leakage-safe held-out transfer",
        "",
        f"Held-out optimal-selection-rate difference: "
        f"{_percent(held_out['optimal_selection_rate_difference'])}.",
        (
            "Held-out optimal-selection-rate one-sided 95% lower bound: "
            f"{held_out['optimal_selection_rate_one_sided_95_lower_bound']:.4f}."
        ),
        f"Held-out scenarios improving: {held_out['positive_scenarios']}/4.",
        "## Clean guards and confounds",
        "",
        f"Clean optimal-selection-rate difference: "
        f"{_percent(clean['optimal_selection_rate_difference'])}.",
        f"Clean valid-purchase difference: {_percent(clean['valid_purchase_difference'])}.",
        "",
    ]
    for name, value in sorted(report["confound_audit"].items()):
        lines.append(f"- {'PASS' if value else 'FAIL'}: {name.replace('_', ' ')}")
    lines.extend(["", "## Predeclared gates", ""])
    for name, value in sorted(report["gates"].items()):
        lines.append(f"- {'PASS' if value else 'FAIL'}: {name.replace('_', ' ')}")
    lines.extend(["", "## Frozen provenance", ""])
    for name, value in sorted(report["provenance"].items()):
        lines.append(f"- {name}: `{value}`")
    lines.extend(["", "## Compiler decomposition", ""])
    for arm, values in report["compiler_decomposition"].items():
        conditional = values["conditional_optimal_selection_rate"]
        conditional_text = "n/a" if conditional is None else _percent(conditional)
        lines.append(
            f"- {arm}: contract pass {_percent(values['pass_rate'])}; "
            f"conditional optimal-selection rate {conditional_text}."
        )
    if "post_sft_contribution" in report:
        contribution = report["post_sft_contribution"]
        lines.extend(
            [
                "",
                "## Post-SFT contribution",
                "",
                f"Paired runs: {contribution['pair_count']}.",
                "Optimal-selection-rate difference over SFT parent: "
                f"{_percent(contribution['optimal_selection_rate_difference'])}.",
                (
                    "One-sided 95% lower bound: "
                    f"{contribution['one_sided_95_lower_bound']:.4f}; "
                    f"p={contribution['sign_randomization_pvalue']:.6g}."
                ),
            ]
        )
    return "\n".join(lines) + "\n"


def write_report(json_path: Path, markdown_path: Path, report: dict[str, Any]) -> None:
    write_json_create_only(json_path, report)
    write_text_create_only(markdown_path, render_markdown(report))
