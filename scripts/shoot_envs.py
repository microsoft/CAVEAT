"""Launch each of the 10 marketplace envs (clean) and screenshot its homepage.

Mirrors the viewer's /api/launch: create env -> TASKS[0] @ condition=clean ->
start on a port -> start_url -> headless Chromium screenshot. Writes PNGs to OUT.
"""
import os, sys, time, traceback
from dataclasses import replace
from pathlib import Path
import tempfile

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
import agentarena.envs  # noqa: F401  (registers envs)
import importlib
from agentarena.core.environment import ENVIRONMENTS
from agentarena.scaffolds._browser import BrowserConfig

OUT = Path("/tmp/env_shots"); OUT.mkdir(parents=True, exist_ok=True)
ENVS = ["amazon", "ebay", "etsy", "stockx", "nike",
        "doordash", "instacart", "airbnb", "zillow", "fiverr"]
PORT0 = 9700
VW, VH = 1366, 940

bc = BrowserConfig.from_env(headless=True)
if bc.lib_path:
    os.environ["LD_LIBRARY_PATH"] = bc.lib_path + ":" + os.environ.get("LD_LIBRARY_PATH", "")
from playwright.sync_api import sync_playwright

results = {}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=bc.executable, args=bc.args, env=bc.child_env())
    for i, name in enumerate(ENVS):
        handle = None
        try:
            env = ENVIRONMENTS.create(name)
            mod = importlib.import_module(f"agentarena.envs.{name}")
            task = replace(mod.TASKS[0], condition="clean")
            port = PORT0 + i
            handle = env.start(port, task, work_dir=Path(tempfile.mkdtemp(prefix=f"shot_{name}_")))
            url = env.start_url(handle.port, task)
            page = browser.new_page(viewport={"width": VW, "height": VH}, device_scale_factor=2)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            out = OUT / f"{name}.png"
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": VW, "height": VH})
            page.close()
            results[name] = f"OK {url}"
            print(f"[{name}] OK -> {out}", flush=True)
        except Exception as e:
            results[name] = f"FAIL {type(e).__name__}: {e}"
            print(f"[{name}] FAIL {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
        finally:
            if handle is not None:
                try: handle.stop()
                except Exception: pass
    browser.close()

print("\n==== SUMMARY ====")
for n in ENVS:
    print(f"  {n:12} {results.get(n)}")
