"""Environment base class + the FastAPI-subprocess plumbing both envs share.

An environment is a small web app (FastAPI backend + built SPA) that an agent
drives in a real browser. The base owns the boring, identical parts — pick a free
port, seed a fresh DB with the right catalog, launch the server, wait for health,
tear down — so an adapter only implements the env-specific bits: how to seed, the
start URL, and how to read back + score what the agent did.

Catalogs are injected at seed time (the website layout/logic is never changed —
only the data), so you can author completely different product/listing sets and
"steered" variants without touching the app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .registry import Registry
from .task import TaskSpec
from .trajectory import Evaluation

ENVIRONMENTS: Registry["Environment"] = Registry("environment")


# --------------------------------------------------------------------------- #
# small HTTP / port helpers
# --------------------------------------------------------------------------- #
def free_port(port: int) -> None:
    for cmd in (["fuser", "-k", f"{port}/tcp"],
                ["bash", "-c", f"lsof -ti tcp:{port} | xargs -r kill -9"]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except Exception:
            pass
    time.sleep(0.3)


def http_get(url: str, timeout: float = 15) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def http_json(url: str, method: str = "GET", body: Any = None, timeout: float = 20,
              headers: Optional[dict] = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:200]}


def http_get_json(url: str, *, retries: int = 5, delay: float = 0.5, timeout: float = 15) -> dict:
    """GET + parse JSON, retrying transient failures. Returns {} only after the
    server keeps failing — so a momentary hiccup right after the agent finishes
    never gets silently misread as 'no result'. Used by evaluators."""
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay * (i + 1))
    print(f"[agentarena] warning: {url} failed after {retries} tries: {last}")
    return {}


def wait_up(health_url: str, timeout: float = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(health_url, timeout=5).read()
            return True
        except Exception:
            time.sleep(0.4)
    return False


@dataclass
class ServerHandle:
    proc: subprocess.Popen
    port: int
    db_path: Path
    base_url: str
    env: dict

    def stop(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        free_port(self.port)


# --------------------------------------------------------------------------- #
# Environment base
# --------------------------------------------------------------------------- #
class Environment(ABC):
    """Subclass this to add an environment. See agentarena/envs/amazon|airbnb."""

    name: str = "env"
    server_dir: Path = Path()           # dir to run the backend from (contains `backend/`)
    server_module: str = "backend.app"
    health_path: str = "/api/health"    # a cheap GET that 200s once the server is ready
    default_start_path: str = "/"
    python: str = sys.executable        # interpreter that has the env's web deps

    # Named catalogs registered by the adapter ({"laptops": Catalog, ...}).
    catalogs: dict[str, Any] = {}

    # ---- adapter hooks ---------------------------------------------------- #
    @abstractmethod
    def seed_db(self, db_path: Path, *, catalog: Optional[str], condition: str,
                params: dict) -> None:
        """Create a fresh DB at db_path, seeded with the catalog for this condition."""

    @abstractmethod
    def evaluate(self, handle: ServerHandle, task: TaskSpec) -> Evaluation:
        """Read back what the agent did (via the API) and score it against task.preferences."""

    def server_command(self, port: int, db_path: Path) -> list[str]:
        return ["-m", self.server_module, "--host", "127.0.0.1",
                "--port", str(port), "--db", str(db_path)]

    def server_env(self, catalog: Optional[str], condition: str, params: dict) -> dict:
        """Extra env vars for the server process (e.g. experiment/steering gates)."""
        return {}

    def start_url(self, port: int, task: TaskSpec) -> str:
        path = task.start_path or self.default_start_path
        return f"http://127.0.0.1:{port}{path}"

    def after_start(self, handle: ServerHandle, task: TaskSpec) -> None:
        """Optional per-run prep once the server is up (e.g. clear cart)."""

    def reset(self, handle: ServerHandle, task: TaskSpec) -> None:
        """Default reset = re-seed a fresh DB in place (override for an endpoint reset)."""
        self.seed_db(handle.db_path, catalog=task.catalog, condition=task.condition,
                     params=task.params)

    # ---- lifecycle (shared) ----------------------------------------------- #
    def start(self, port: int, task: TaskSpec, *, work_dir: Path) -> ServerHandle:
        # Resolve to absolute: the seed + server subprocesses run with cwd=server_dir,
        # so a relative db path would be created in the wrong place (sqlite then fails
        # with "unable to open database file").
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        db = work_dir / f"{self.name}_{port}.db"
        free_port(port)
        try:
            db.unlink()
        except FileNotFoundError:
            pass
        self.seed_db(db, catalog=task.catalog, condition=task.condition, params=task.params)
        env = {**os.environ, **self.server_env(task.catalog, task.condition, task.params)}
        proc = subprocess.Popen([self.python, *self.server_command(port, db)],
                                cwd=str(self.server_dir), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        handle = ServerHandle(proc, port, db, f"http://127.0.0.1:{port}", env)
        if not wait_up(f"{handle.base_url}{self.health_path}"):
            handle.stop()
            raise RuntimeError(f"{self.name}: server failed to start on port {port}")
        self.after_start(handle, task)
        return handle

    # ---- catalog access --------------------------------------------------- #
    def catalog(self, name: Optional[str]):
        if name is None:
            return next(iter(self.catalogs.values())) if self.catalogs else None
        if name not in self.catalogs:
            raise KeyError(f"{self.name}: unknown catalog {name!r}. "
                           f"Have: {', '.join(self.catalogs) or '(none)'}")
        return self.catalogs[name]

    def _seed_subprocess(self, code: str, extra_env: dict) -> None:
        """Run a short seeding script in the server dir with the env's interpreter."""
        r = subprocess.run([self.python, "-c", code], cwd=str(self.server_dir),
                           env={**os.environ, **extra_env}, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{self.name} seed failed:\n{r.stderr[-1500:]}")
