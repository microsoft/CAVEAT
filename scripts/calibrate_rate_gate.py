#!/usr/bin/env python
"""Replay a trajectory's request stream against the storefront rate-gate thresholds.

Answers "would this run have tripped the Robot Check?" OFFLINE, without booting a
server — for calibrating SF_RATE_* before committing to a preset.

Inputs (auto-detected):
  * a benchmark trajectory.json (results tree): steps[].url + stats.seconds.
    Step records carry no per-step timestamps, so arrivals are spread uniformly
    across stats.seconds (documented approximation; override with --interval).
  * a synthetic trace file: JSON list of events, each either
        {"t": <seconds-offset>, "url": "/api/products?limit=1"}
    or a bare URL string (then --interval spaces them).

Counted requests mirror the caveat_shop gate exactly: /api/products (list),
/api/search, /api/products/asin/*, /api/products/{id}, and the SSR document GETs
/s and /dp/*. Everything else is ignored.

All three gate windows are replayed: short burst, long, and the SUSTAINED (5-min)
anti-enumeration window (SF_RATE_SUSTAINED_WINDOW=300 / SF_RATE_SUSTAINED_MAX=80 in
gate.py) that catches a paced ~1 req/s catalog sweep the short/long pair let through.

Usage:
  .venv/bin/python scripts/calibrate_rate_gate.py results/.../trajectory.json
  .venv/bin/python scripts/calibrate_rate_gate.py trace.json --short-max 6 --long-max 30
  .venv/bin/python scripts/calibrate_rate_gate.py trace.json --interval 0.5 --preset hardest
  .venv/bin/python scripts/calibrate_rate_gate.py trace.json --sustained-max 80 --sustained-window 300
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

COUNTED = [
    re.compile(r"^/api/products$"),
    re.compile(r"^/api/search$"),
    re.compile(r"^/api/products/asin/[^/]+$"),
    re.compile(r"^/api/products/\d+$"),
    re.compile(r"^/s$"),
    re.compile(r"^/dp/[^/]+$"),
]

PRESETS = {  # mirrors caveat/envs/caveat_shop/__init__.py _SCRAPE_RATE_PRESETS
    "medium": {},
    "hard": {"short_max": 6, "long_max": 30},
    "hardest": {"short_max": 3, "long_max": 15},
}


def is_counted(url: str) -> bool:
    path = urlparse(url).path
    return any(rx.match(path) for rx in COUNTED)


def load_events(path: Path, interval: float | None):
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "steps" in data:            # trajectory.json
        urls = [s.get("url", "") for s in data["steps"] if s.get("url")]
        total = float((data.get("stats") or {}).get("seconds") or 0)
        step = interval if interval else (total / max(1, len(urls)) if total else 1.0)
        return [(i * step, u) for i, u in enumerate(urls)], f"trajectory ({len(urls)} steps)"
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    if not isinstance(data, list):
        raise SystemExit(f"unrecognized trace format in {path}")
    events, t_auto = [], 0.0
    for e in data:
        if isinstance(e, str):
            events.append((t_auto, e))
            t_auto += interval or 1.0
        else:
            events.append((float(e.get("t", t_auto)), str(e.get("url", ""))))
            t_auto = events[-1][0] + (interval or 1.0)
    return events, f"synthetic trace ({len(events)} events)"


def replay(events, *, short_window, short_max, long_window, long_max,
           sustained_window, sustained_max, ttl):
    short, long_, sustained = deque(), deque(), deque()
    counted = triggers = 0
    challenge_until = None
    first_trigger = None
    blocked = 0
    for t, url in sorted(events, key=lambda e: e[0]):
        if not is_counted(url):
            continue
        counted += 1
        if challenge_until is not None:
            if t < challenge_until:
                blocked += 1
                continue
            challenge_until = None       # TTL auto-clear (windows drained)
            short.clear(); long_.clear(); sustained.clear()
        short.append(t); long_.append(t); sustained.append(t)
        while short and t - short[0] > short_window:
            short.popleft()
        while long_ and t - long_[0] > long_window:
            long_.popleft()
        while sustained and t - sustained[0] > sustained_window:
            sustained.popleft()
        if len(short) > short_max or len(long_) > long_max or len(sustained) > sustained_max:
            triggers += 1
            blocked += 1
            challenge_until = t + ttl
            if first_trigger is None:
                first_trigger = (counted, t)
    return {"counted": counted, "would_trigger": triggers, "blocked": blocked,
            "first_trigger": first_trigger}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trace", type=Path, help="trajectory.json or synthetic trace file")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="medium")
    ap.add_argument("--short-window", type=float, default=10)
    ap.add_argument("--short-max", type=float, default=None)
    ap.add_argument("--long-window", type=float, default=60)
    ap.add_argument("--long-max", type=float, default=None)
    ap.add_argument("--sustained-window", type=float, default=300)
    ap.add_argument("--sustained-max", type=float, default=80)
    ap.add_argument("--ttl", type=float, default=45)
    ap.add_argument("--interval", type=float, default=None,
                    help="force a fixed inter-event spacing (seconds)")
    args = ap.parse_args()

    p = PRESETS[args.preset]
    short_max = args.short_max if args.short_max is not None else p.get("short_max", 12)
    long_max = args.long_max if args.long_max is not None else p.get("long_max", 60)

    events, src = load_events(args.trace, args.interval)
    if not events:
        print("no events found in trace"); sys.exit(2)
    res = replay(events, short_window=args.short_window, short_max=short_max,
                 long_window=args.long_window, long_max=long_max,
                 sustained_window=args.sustained_window, sustained_max=args.sustained_max,
                 ttl=args.ttl)

    print(f"source          : {src}")
    print(f"thresholds      : {short_max:g}/{args.short_window:g}s  "
          f"{long_max:g}/{args.long_window:g}s  "
          f"{args.sustained_max:g}/{args.sustained_window:g}s  "
          f"ttl={args.ttl:g}s  (preset={args.preset})")
    print(f"counted requests: {res['counted']}")
    print(f"would-trigger   : {res['would_trigger']}")
    print(f"blocked requests: {res['blocked']}")
    if res["first_trigger"]:
        n, t = res["first_trigger"]
        print(f"first trigger   : counted request #{n} at t={t:.1f}s")
    else:
        print("first trigger   : never (run stays under the gate)")


if __name__ == "__main__":
    main()
