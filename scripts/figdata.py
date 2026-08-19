"""Shared helpers for the results figure (scripts/final_fig.py).

Just the small stats utilities + the canonical relativeness order; the figure data
itself is read from benchmark_data/reports/figure_data.json by final_fig.py.
"""
import random

random.seed(7)
VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]   # relativeness 0..4


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def boot_ci(vals, B=4000):
    if not vals:
        return (float("nan"),) * 3
    m = mean(vals)
    if max(vals) - min(vals) < 1e-9:
        return (m, m, m)
    bs = sorted(sum(random.choice(vals) for _ in vals) / len(vals) for _ in range(B))
    return m, bs[int(0.025 * B)], bs[int(0.975 * B)]
