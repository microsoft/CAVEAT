"""Scaffolds. Importing this package registers every scaffold.

Built-in: ``simple`` (reference loop, no extra deps beyond Playwright),
``browseruse`` (needs ``browser-use``), ``stagehand`` (needs Node + npm install).
Heavy third-party imports happen lazily inside ``run()``, so importing this package
never requires those deps to be installed.
"""

from . import browseruse, simple, stagehand  # noqa: F401  (registration side effects)
