"""Autonomous benchmark orchestrator for the 4 new products (+ remaining laptop-parity sweeps).

Runs a queue of `agentarena.benchmark.run` invocations with a fixed concurrency of CONC run.py
subprocesses (each at its own --jobs), keeping the box's ~10-cell memory budget. Self-gates: waits
for any currently-running benchmark.run processes to finish first (so it doesn't over-subscribe the
oc/mat headline runs already in flight). Resumable: run.py skips completed cells, so re-launching is
safe. Logs to stdout (redirect to a file).

Job list (visible-catalog; hidden-spec sweep handled separately):
  headlines (bp, tent):  P_g55 (gpt-5.5), P_g41 (gpt-4.1)          reps 4
  expansion (all 4):     P_exp = 12 models (vintage+scale+effort+xfamily)   reps 3
"""
import subprocess
import sys
import time

CONC = 2                      # concurrent run.py subprocesses (each --jobs JOBS -> CONC*JOBS cells)
JOBS = 5                      # 2x5 = 10 concurrent cells (memory cap on this box)
PY = ".venv/bin/python"
VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]
CONDS = ["clean", "combined"]

EXP_MODELS = ["phyagi/gpt-5", "phyagi/gpt-5.1", "phyagi/gpt-5.2", "phyagi/gpt-5.4",
              "phyagi/gpt-5.4-mini", "phyagi/gpt-5.4-nano",
              "phyagi/gpt-5.5#low", "phyagi/gpt-5.5#medium", "phyagi/gpt-5.5#high",
              "phyagi/DeepSeek-V4-Pro", "grok-4-1-fast-non-reasoning", "gpt-oss-120b"]

# (name, scenario, models, jobs, reps)
JOBQ = [
    ("bp_g55", "backpack", ["phyagi/gpt-5.5"], JOBS, 4),
    ("bp_g41", "backpack", ["phyagi/gpt-4.1"], JOBS, 4),
    ("tent_g55", "tent", ["phyagi/gpt-5.5"], JOBS, 4),
    ("tent_g41", "tent", ["phyagi/gpt-4.1"], JOBS, 4),
    # expansion (sweep figures): 2 reps — supporting figures; the headline carries 4 reps. Slow old
    # vintages (gpt-5 ~800s/cell) make 3 reps an ~18h run; 2 reps + jobs5 brings it to ~8h.
    ("oc_exp", "office_chair", EXP_MODELS, JOBS, 2),
    ("mat_exp", "mattress", EXP_MODELS, JOBS, 2),
    ("bp_exp", "backpack", EXP_MODELS, JOBS, 2),
    ("tent_exp", "tent", EXP_MODELS, JOBS, 2),
]


def now():
    return time.strftime("%H:%M:%S")


def n_runs():
    out = subprocess.run(["bash", "-c", "ps -eo cmd | grep -c '[b]enchmark.run'"],
                         capture_output=True, text=True)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 0


def cmd(job, port):
    name, scn, models, jobs, reps = job
    return [PY, "-m", "agentarena.benchmark.run", "--name", name, "--scenarios", scn,
            "--variants", *VARIANTS, "--conditions", *CONDS, "--models", *models,
            "--jobs", str(jobs), "--max-steps", "75", "--base-port", str(port),
            "--repeats", str(reps), "--results", "results"]


def main():
    print(f"[{now()}] orchestrator: waiting for current runs to finish...", flush=True)
    while n_runs() > 0:
        time.sleep(30)
    print(f"[{now()}] idle — starting {len(JOBQ)} jobs at concurrency {CONC} (jobs={JOBS})", flush=True)

    queue = list(enumerate(JOBQ))
    active = {}   # port -> (Popen, name, t0)
    ports = [9100, 9300, 9500, 9700]   # rotating port bands (CONC<=4)
    free = list(ports)

    while queue or active:
        while queue and len(active) < CONC and free:
            idx, job = queue.pop(0)
            port = free.pop(0)
            logf = open(f"/tmp/orch_{job[0]}.log", "w")
            p = subprocess.Popen(cmd(job, port), stdout=logf, stderr=subprocess.STDOUT)
            active[port] = (p, job[0], time.time(), logf)
            print(f"[{now()}] START {job[0]} (port {port}, {len(job[2])} models, reps {job[4]})", flush=True)
        # poll
        done = []
        for port, (p, name, t0, logf) in active.items():
            if p.poll() is not None:
                logf.close()
                mins = (time.time() - t0) / 60
                print(f"[{now()}] DONE  {name} rc={p.returncode} ({mins:.0f} min)", flush=True)
                done.append(port)
        for port in done:
            del active[port]
            free.append(port)
        if queue or active:
            time.sleep(20)
    print(f"[{now()}] orchestrator: ALL JOBS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
