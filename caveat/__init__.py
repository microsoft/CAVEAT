"""CAVEAT — run many web-agent scaffolds (× many models) against shopping/booking
environments, and measure how faithfully they honor the user's stated preferences.

Quick start (Python)::

    import caveat.envs, caveat.scaffolds          # register plugins
    from caveat import Experiment, Runner
    from caveat.envs.caveat_shop import LAPTOP

    exp = Experiment(name="laptops", scaffolds=["browseruse"],
                     models=["gpt-5.5", "gpt-4.1"], tasks=[LAPTOP],
                     conditions=["clean", "steered"])
    Runner(results_dir="results").run(exp, jobs=4)

Or from the CLI::  ``caveat run --env caveat_shop --scaffolds browseruse``  then
``caveat view``.
"""

from .core.environment import ENVIRONMENTS, Environment
from .core.experiment import Experiment, Runner, run_cell
from .core.models import ModelSpec
from .core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from .core.task import TaskSpec, check_constraints, load_tasks
from .core.trajectory import Evaluation, Step, Trajectory

__version__ = "0.1.0"

__all__ = [
    "Experiment", "Runner", "run_cell", "ModelSpec", "TaskSpec", "load_tasks",
    "check_constraints", "Scaffold", "RunContext", "RawTrajectory", "SCAFFOLDS",
    "Environment", "ENVIRONMENTS", "Trajectory", "Step", "Evaluation",
]
