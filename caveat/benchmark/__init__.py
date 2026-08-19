"""CAVEAT benchmark generation pipeline (Amazon).

Deterministic scientific core (``scenarios``, ``pool``, ``preferences``) + LLM dressing
(``copy_gen``, ``instruction_gen``, ``faithfulness``, ``images``) + serialization/registry.
See ``caveat/benchmark/README`` notes in the plan file.
"""

from . import scenarios  # noqa: F401
