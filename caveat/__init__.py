"""CAVEAT measures whether web agents make the user's optimal selection.

The public command-line path runs an OpenAI-compatible model endpoint on the
CAVEAT-Standard or CAVEAT-Hard tier and reports optimal-selection rate.  The
Python API remains available for custom harness integrations.
"""

from .core.environment import ENVIRONMENTS, Environment
from .core.experiment import Experiment, Runner, run_cell
from .core.models import ModelSpec
from .core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from .core.task import TaskSpec, check_constraints, load_tasks
from .core.trajectory import Evaluation, Step, Trajectory

__version__ = "0.1.0"

__all__ = [
    "ENVIRONMENTS",
    "SCAFFOLDS",
    "Environment",
    "Evaluation",
    "Experiment",
    "ModelSpec",
    "RawTrajectory",
    "RunContext",
    "Runner",
    "Scaffold",
    "Step",
    "TaskSpec",
    "Trajectory",
    "check_constraints",
    "load_tasks",
    "run_cell",
]
