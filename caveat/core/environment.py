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

EVALUATOR_GET_RETRIES_EXHAUSTED = (
    "CAVEAT_EVALUATOR_GET_RETRIES_EXHAUSTED"
)
ENVIRONMENT_STARTUP_TIMEOUT = "CAVEAT_ENVIRONMENT_STARTUP_TIMEOUT"
ENVIRONMENT_SERVER_EXITED = "CAVEAT_ENVIRONMENT_SERVER_EXITED"

# Infrastructure backstop only.  A healthy storefront normally starts in a few
# seconds, but many independent Python servers and browsers can cold-start at
# once in publication campaigns.  Keep this far beyond that healthy path so
# host scheduling cannot become a measured constraint.
ENVIRONMENT_STARTUP_TIMEOUT_SECONDS = 300


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


def http_get_json(url: str, *, retries: int = 5, delay: float = 0.5, timeout: float = 15,
                  headers: Optional[dict] = None) -> dict:
    """GET + parse JSON, retrying transient failures.

    Exhaustion is an explicit infrastructure error: returning an empty object
    here would make an unavailable evaluator endpoint indistinguishable from a
    genuine empty order/bookings response.  Evaluators may pass an
    X-Storefront-Ops credential via ``headers``; the error deliberately never
    includes request headers.
    """
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay * (i + 1))
    last_description = (
        f"{type(last).__name__}: {last}"
        if last is not None else "no attempts configured"
    )
    raise RuntimeError(
        f"{EVALUATOR_GET_RETRIES_EXHAUSTED}: "
        f"{url} failed after {retries} attempts; "
        f"last_error={last_description}"
    ) from last


def wait_up(
    health_url: str,
    timeout: float = ENVIRONMENT_STARTUP_TIMEOUT_SECONDS,
    *,
    process: Optional[subprocess.Popen] = None,
) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        # Do not spend the whole backstop polling a child that has already
        # failed.  Environment.start() deliberately distinguishes this local
        # server failure from a still-live process starved past the deadline.
        if process is not None and process.poll() is not None:
            return False
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
    server_log: Any = None

    def stop(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        finally:
            if self.server_log is not None:
                try:
                    self.server_log.close()
                except Exception:
                    pass
            free_port(self.port)


# --------------------------------------------------------------------------- #
# Environment base
# --------------------------------------------------------------------------- #
class Environment(ABC):
    """Subclass this to add an environment. See caveat/envs/caveat_shop|caveat_stay."""

    name: str = "env"
    server_dir: Path = Path()           # dir to run the backend from (contains `backend/`)
    server_module: str = "backend.app"
    health_path: str = "/api/health"    # a cheap GET that 200s once the server is ready
    default_start_path: str = "/"
    python: str = sys.executable        # interpreter that has the env's web deps

    # Named catalogs registered by the adapter ({"laptops": Catalog, ...}).
    catalogs: dict[str, Any] = {}

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """tmp + os.replace: concurrent cells seed the same catalog cache at matrix launch;
        a reader catching a half-written JSON gets a broken storefront (mass 0-step
        navigation-timeout cells). Content is deterministic, so last-writer-wins is safe."""
        import tempfile as _tf
        fd, tmp = _tf.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

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
        server_log = (work_dir / "environment_server.log").open(
            "w", encoding="utf-8"
        )
        try:
            proc = subprocess.Popen(
                [self.python, *self.server_command(port, db)],
                cwd=str(self.server_dir), env=env,
                stdout=server_log, stderr=subprocess.STDOUT,
            )
        except BaseException:
            server_log.close()
            raise
        handle = ServerHandle(
            proc, port, db, f"http://127.0.0.1:{port}", env, server_log
        )
        if not wait_up(
            f"{handle.base_url}{self.health_path}",
            timeout=ENVIRONMENT_STARTUP_TIMEOUT_SECONDS,
            process=proc,
        ):
            returncode = proc.poll()
            handle.stop()
            if returncode is not None:
                raise RuntimeError(
                    f"{ENVIRONMENT_SERVER_EXITED}: env={self.name} port={port} "
                    f"returncode={returncode} before_health"
                )
            raise RuntimeError(
                f"{ENVIRONMENT_STARTUP_TIMEOUT}: env={self.name} port={port} "
                f"timeout_seconds={ENVIRONMENT_STARTUP_TIMEOUT_SECONDS}"
            )
        try:
            self.after_start(handle, task)
        except BaseException:
            # The caller cannot own a handle until start() returns.  If a
            # post-health hook (for example the evaluator's initial snapshot)
            # fails, tear down here and preserve the original exception.
            handle.stop()
            raise
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
