"""Train a browser policy to internalize AgentArena's deliberative protocol.

The package is deliberately separate from :mod:`agentarena`: training may
consume public trajectories produced by AgentArena, but importing this package
must never register or modify a benchmark environment or scaffold.
"""

from __future__ import annotations

__version__ = "0.1.0"
