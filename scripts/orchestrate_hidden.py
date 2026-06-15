"""Hidden-spec (capability-vs-visibility) sweep for the 4 new products. Run AFTER all visible runs
finish (it swaps each product's live catalog to a PDP-only variant). Steps:

  1. regen --hidden each product  (drops the 2 card-visible graded dims to PDP-only; bool + always-hard
     numeric stay on the card so a weak agent can still confirm the hard requirements).
  2. run P_hid for each (gpt-5.5 + gpt-4.1, 5 variants, clean+combined) at concurrency CONC.
     run.py rescores each repeat against the HIDDEN pool while it is live (titles match) -> correct P.
  3. regen (visible) each product to RESTORE the canonical catalog.

CRITICAL: never global-rescore (write_preservation over all results) while a hidden catalog is live —
it would null the visible runs' P (title mismatch). run.py only rescores its own P_hid run, which is safe.
"""
import subprocess
import time

PY = ".venv/bin/python"
PRODUCTS = ["office_chair", "mattress", "backpack", "tent"]
VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]
CONDS = ["clean", "combined"]
PREFIX = {"office_chair": "oc", "mattress": "mat", "backpack": "bp", "tent": "tent"}
# PhyAGI hit its monthly cost cap mid-run -> hidden sweep runs on TRAPI. TRAPI gpt-5.5 is
# throttle-prone (429s) under heavy concurrent reasoning, so keep concurrency LOW (4 cells).
CONC = 2
JOBS = 5
REPS = 2
# gpt-5.5 hidden is BLOCKED (PhyAGI monthly cap + TRAPI gpt-5.5 browseruse throttle). gpt-4.1
# (non-reasoning, no throttle) runs fine on TRAPI -> gpt-4.1-only hidden for the new products; the
# laptop hidden figure (collected earlier) carries the full gpt-5.5-vs-gpt-4.1 capability×visibility story.
MODELS = ["gpt-4.1"]   # TRAPI


def now():
    return time.strftime("%H:%M:%S")


def sh(cmd):
    print(f"[{now()}] $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd)


def main():
    # 1. swap all 4 catalogs to hidden
    for sid in PRODUCTS:
        sh([PY, "scripts/regen_scenario.py", sid, "--hidden"])
    # 2. run P_hid for each at concurrency CONC
    ports = [9100, 9300, 9500, 9700]
    queue = list(PRODUCTS)
    active = {}
    free = list(ports[:CONC])
    while queue or active:
        while queue and len(active) < CONC and free:
            sid = queue.pop(0)
            port = free.pop(0)
            name = f"{PREFIX[sid]}_hid"
            logf = open(f"/tmp/orch_{name}.log", "w")
            cmd = [PY, "-m", "agentarena.benchmark.run", "--name", name, "--scenarios", sid,
                   "--variants", *VARIANTS, "--conditions", *CONDS,
                   "--models", *MODELS,
                   "--jobs", str(JOBS), "--max-steps", "75", "--base-port", str(port),
                   "--repeats", str(REPS), "--results", "results"]
            p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
            active[port] = (p, name, time.time(), logf)
            print(f"[{now()}] START {name} (port {port})", flush=True)
        done = [pt for pt, (p, *_ ) in active.items() if p.poll() is not None]
        for pt in done:
            p, name, t0, logf = active.pop(pt)
            logf.close()
            free.append(pt)
            print(f"[{now()}] DONE {name} rc={p.returncode} ({(time.time()-t0)/60:.0f} min)", flush=True)
        if queue or active:
            time.sleep(20)
    # 3. restore visible catalogs (deterministic regen reproduces the canonical pool exactly)
    for sid in PRODUCTS:
        sh([PY, "scripts/regen_scenario.py", sid])
    print(f"[{now()}] HIDDEN SWEEP COMPLETE; visible catalogs restored", flush=True)


if __name__ == "__main__":
    main()
