#!/usr/bin/env python
"""ONE implementation of the infra-vs-capability rule, shared by the CAVEAT
benchmark reporting scripts. Import it; do not re-derive it.

WHY THIS EXISTS
---------------
A run whose `outcome` is "none" (nothing was purchased), or whose evaluator could not
read the transaction record, is worth excluding from the benchmark ONLY if infrastructure
prevented the measurement.  If the MODEL is what went wrong -- malformed action JSON,
giving up, looping, arguing with the page -- the model FAILED THE TASK and its
optimal-selection value must be 0. Dropping those runs flatters exactly those models.

The old rule was a bare substring scan of run.log:

    INFRA_SIGS = ("reconnection attempts failed", "Fallback LLM also failed",
                  "validation error for AgentOutput", "TargetClosedError",
                  "APIConnectionError", "RateLimitError", "browser crashed")
    if outcome == "none" and (nsteps == 0 or any(g in txt for g in INFRA_SIGS)): drop

That is wrong three separate ways, all verified against the logs in results/:

  1. "validation error for AgentOutput" is the scaffold's message for *the model emitted
     something that is not a valid action*.  That is the definition of a capability
     failure.  Qwen3.5-122B produces it constantly ("Invalid JSON: expected value at
     line 13 column 23", 'input_value=\'\\n\\n{\\n "thinking": ...').
  2. "Fallback LLM also failed" is a *wrapper*.  Its payload is either an endpoint fault
     (`ModelProviderError: Error code: 401 - TRAPI: Unauthorized`) or a parse failure
     (`ModelProviderError: 45 validation errors for AgentOutput`).  The wrapper alone
     tells you nothing; you must read what is inside the parentheses.
  3. "RateLimitError" is a *substring of* "ModelRateLimitError", which the scaffold logs
     on a transient 429 that it then successfully retries on the fallback deployment.
     One recovered 429 on step 1 was enough to delete a 36-step run that ended with the
     agent calling done(success=False).  Example:
       results/overhaul_c_pilot2/caveat_food_r5/caveat_food__browseruse__gpt-4.1__dinner-thresholded__steered
     -- a clean behavioural give-up, dropped as "infra".

THE RULE
--------
Ask what TERMINATED the run, not what appeared somewhere in it.  Transient faults the
scaffold recovered from are irrelevant: the agent got its turn.

  INFRA  (exclude) iff one of:
    V. evaluator GET exhausted all retries - the agent outcome is unknowable, recorded
                                       with one exact fail-closed marker in summary.error.
    Z. steps == 0                    - the launch never happened.  The browser could not
                                       reach the local storefront ("Navigation failed:
                                       RuntimeError: Page.navigate() timed out after
                                       120.0s"), so the agent never saw an observation and
                                       nothing behavioural was measured.  11 runs.
    E. the run was killed by the scaffold's consecutive-failure guard ("Stopping due to N
       consecutive failures") AND an ENDPOINT fault -- an HTTP status or transport
       exception from the LLM provider -- is the but-for cause of that abort.  The model
       was never given a response to reason about.  Seen in the corpus as:
         Error code: 404 - DEPLOYMENT_NOT_FOUND  (TRAPI deployment vanished mid-lane;
                                                  76 Qwen runs in one 14-minute window)
         Error code: 401 - TRAPI: Unauthorized. Invalid or expired token.  (auth expiry)
         Error code: 429 - TRAPI: Rate Limit Exceeded                      (real 429 wall)
    T. the browser/transport died and took the run with it (TargetClosedError, "Target
       page, context or browser has been closed", ConnectionRefused, "All N reconnection
       attempts failed") in the TAIL of the log.  Zero occurrences in the current corpus
       -- kept as a narrow forward guard, tail-scoped so a mid-run blip the scaffold
       recovered from cannot trigger it.

  CAPABILITY (optimal-selection value 0) -- everything else, in particular:
    - killed by the consecutive-failure guard on a streak of `validation error for
      AgentOutput` / `Invalid JSON`: the model could not emit a parseable action.
    - the run reached its own ending (called done(), hit the step cap, got a Judge
      Verdict): whatever transient faults appeared earlier, the agent finished on its own
      terms and chose not to buy.

  "But-for cause" is needed because the scaffold's failure counter does NOT reset when the
  error type changes, so a streak can read e.g. "OOOEEEEEE...E" -- three of the model's own
  parse failures, then the TRAPI deployment disappears and the next 58 calls 404 until the
  guard fires.  Attribute the abort to whatever was still failing at the moment it fired:
  take the TRAILING CONTIGUOUS block of same-class failures.  If that block is endpoint-
  class and long enough to have tripped the guard on its own (>= half the guard threshold,
  and >= 2), the outage is the but-for cause -> INFRA.  If it is output-class, the model is
  -> CAPABILITY.  Anything else -> AMBIGUOUS.  Every mixed streak in the current corpus is
  decided by this: the trailing endpoint blocks are 4/5, and 46..60 out of 60.

  AMBIGUOUS -- a terminal streak whose trailing block decides nothing (e.g. a single
  endpoint blip finishing off a streak the model's own output faults had already built).
  Reported separately and KEPT (optimal-selection value 0) rather than silently dropped.

Deliberately NOT infra signatures:
    "consecutive failures"  - names the guard, not the cause.  Fires for both classes.
    "RateLimitError"        - substring of the recovered-429 message (see 3 above).
    "no team event for"     - a silent round; the scaffold retries it.
    "Fallback LLM also failed" / "no more fallbacks available" - wrappers (see 2 above).
    ModelOutputTruncatedError / "Model output was truncated at max_completion_tokens"
                            - the model overran the response cap.  That is the model's
                              doing, so its optimal-selection value is 0 -- but the cap is a harness parameter
                              and binds unequally across models, so it is reported under
                              its own code (cap_truncation) for review.  Never terminal in
                              the current corpus.
"""

from __future__ import annotations

import json
import os
import re

__all__ = ["classify_run", "is_infra_fail", "INFRA", "CAPABILITY", "AMBIGUOUS", "EVALUATED"]

INFRA = "infra"
CAPABILITY = "capability"
AMBIGUOUS = "ambiguous"
EVALUATED = "evaluated"          # not an excluded run at all

# --- log grammar (browser-use scaffold) ------------------------------------------------
RE_FAILED = re.compile(r"Result failed (\d+)/(\d+) times: (.*)")
RE_STOP = re.compile(r"Stopping due to (\d+) consecutive failures")
# the scaffold prints actions ANSI-coloured: "▶️   \x1b[34mdone\x1b[0m: \x1b[35mtext\x1b[0m: ..."
RE_DONE = re.compile(r"▶.{0,12}(?:\x1b\[\d+m)?done(?:\x1b\[0m)?:")
RE_HTTP = re.compile(r"Error code: (\d{3})\b")

# HTTP statuses that mean "the provider did not serve the request".
ENDPOINT_STATUSES = {401, 403, 404, 408, 409, 429, 500, 502, 503, 504}

# Transport/provider exception names: the call never produced model output.
ENDPOINT_EXC = ("APIConnectionError", "APITimeoutError", "InternalServerError",
                "ServiceUnavailableError", "AuthenticationError", "PermissionDeniedError",
                "ConnectionRefused", "ConnectionResetError", "ReadTimeout")

# The model answered, but with something unusable.
OUTPUT_FAULT = ("validation error for AgentOutput", "validation errors for AgentOutput",
                "Invalid JSON", "json_invalid", "extra_forbidden")
CAP_TRUNCATION = ("Model output was truncated at max_completion_tokens",
                  "ModelOutputTruncatedError")

# Browser/transport death.  Tail-scoped only (see TAIL_LINES).
TRANSPORT_DEATH = ("TargetClosedError", "Target page, context or browser has been closed",
                   "reconnection attempts failed", "ConnectionRefusedError",
                   "ConnectionRefused", "browser crashed")
TAIL_LINES = 40
EVALUATOR_GET_RETRIES_EXHAUSTED = (
    "CAVEAT_EVALUATOR_GET_RETRIES_EXHAUSTED"
)


def _reason_class(reason: str) -> str:
    """endpoint | output | cap_truncation | unknown -- for ONE failure reason line."""
    m = RE_HTTP.search(reason)
    if m and int(m.group(1)) in ENDPOINT_STATUSES:
        return "endpoint"
    if any(e in reason for e in ENDPOINT_EXC):
        return "endpoint"
    if any(t in reason for t in CAP_TRUNCATION):
        return "cap_truncation"
    if any(o in reason for o in OUTPUT_FAULT):
        return "output"
    if m:                       # some other HTTP code (e.g. 400 bad request on our payload)
        return "endpoint" if int(m.group(1)) >= 500 else "output"
    return "unknown"


def classify_run(cell_dir: str) -> dict:
    """Classify one result cell.

    Returns {"class", "code", "evidence", "steps", "outcome"}.
    "class" is one of EVALUATED / INFRA / CAPABILITY / AMBIGUOUS.
    """
    try:
        with open(os.path.join(cell_dir, "summary.json")) as fh:
            s = json.load(fh)
    except Exception:                                              # noqa: BLE001
        return {"class": EVALUATED, "code": "no_summary", "evidence": "", "steps": None,
                "outcome": None}

    outcome = s.get("outcome")
    steps = s.get("num_steps") or s.get("steps") or 0
    base = {"steps": steps, "outcome": outcome}

    # The agent may have completed and even attempted a transaction, but no
    # No behavioral evaluation exists if the evaluator endpoint stayed unavailable
    # through all retries.  run_cell records this exact fail-closed marker in
    # summary.error.  Keep this case narrow so arbitrary local evaluator bugs
    # remain measured failures instead of becoming redraws.
    summary_error = str(s.get("error") or "")
    if (
        outcome in {"error", None}
        and EVALUATOR_GET_RETRIES_EXHAUSTED in summary_error
    ):
        return {
            "class": INFRA,
            "code": "evaluator_get_exhausted",
            "evidence": summary_error[:300],
            **base,
        }

    # A run that transacted is a measurement, full stop -- never excluded.
    if outcome != "none":
        return {"class": EVALUATED, "code": "transacted", "evidence": "", **base}

    # Z. zero-step launch failure.
    if steps == 0:
        return {"class": INFRA, "code": "zero_step",
                "evidence": "steps=0; agent never received an observation", **base}

    log = os.path.join(cell_dir, "run.log")
    if not os.path.exists(log):
        return {"class": CAPABILITY, "code": "no_log",
                "evidence": "outcome=none, >0 steps, no run.log to exonerate it", **base}
    with open(log, errors="ignore") as fh:
        txt = fh.read()
    lines = txt.splitlines()

    # T. transport death in the tail.
    tail = "\n".join(lines[-TAIL_LINES:])
    for sig in TRANSPORT_DEATH:
        if sig in tail:
            ev = next((ln.strip() for ln in reversed(lines[-TAIL_LINES:]) if sig in ln), sig)
            return {"class": INFRA, "code": "transport_death", "evidence": ev[:300], **base}

    fails = [(int(m.group(1)), m.group(3).strip())
             for ln in lines if (m := RE_FAILED.search(ln))]
    stop = RE_STOP.search(txt)

    # Not killed by the guard -> the agent reached its own ending.  Behavioural.
    if not stop:
        marks = []
        if "Judge Verdict" in txt:
            marks.append("reached Judge Verdict")
        if RE_DONE.search(txt):
            marks.append("agent called done() itself")
        if "Final Result:" in txt:
            marks.append("emitted a Final Result")
        why = ("; ".join(marks) or "no consecutive-failure abort") + \
            " -- the agent reached its own ending and did not buy"
        return {"class": CAPABILITY, "code": "behavioural_end", "evidence": why, **base}

    # E. killed by the guard -- classify the TERMINAL streak, not the whole log.
    n = int(stop.group(1))
    streak, prev = [], None
    for k, reason in reversed(fails):
        if prev is not None and k != prev - 1:
            break
        streak.append(reason)
        prev = k
        if len(streak) >= n + 1:
            break
    if not streak:
        return {"class": AMBIGUOUS, "code": "abort_no_reason",
                "evidence": "consecutive-failure abort with no parseable reason line", **base}

    # `streak` is newest-first.  Trailing contiguous block of one class = what was still
    # failing when the guard fired.
    kinds = [_reason_class(r) for r in streak]
    last = kinds[0]
    block = 0
    while block < len(kinds) and kinds[block] == last:
        block += 1
    pure = block == len(kinds)
    ev = (f"guard fired at {n}; trailing {block}/{len(streak)} x {last}"
          f"{' (pure streak)' if pure else ''}: {streak[0][:160]}")

    if last == "endpoint" and (pure or (block >= 2 and block * 2 >= n)):
        return {"class": INFRA, "code": "endpoint_abort", "evidence": ev, **base}
    if last in ("output", "cap_truncation") and (pure or (block >= 2 and block * 2 >= n)):
        code = "cap_truncation_abort" if last == "cap_truncation" else "output_abort"
        return {"class": CAPABILITY, "code": code, "evidence": ev, **base}
    if last == "unknown" and pure:
        return {"class": AMBIGUOUS, "code": "unknown_abort", "evidence": ev, **base}
    mix = "+".join(sorted(set(kinds)))
    return {"class": AMBIGUOUS, "code": "mixed_abort",
            "evidence": f"undecidable terminal block ({mix}); " + ev, **base}


def is_infra_fail(cell_dir: str) -> bool:
    """True only for runs that infrastructure invalidated.  Ambiguous runs are KEPT
    (with optimal-selection value 0) -- excluding on a guess is the failure mode we are fixing."""
    return classify_run(cell_dir)["class"] == INFRA
