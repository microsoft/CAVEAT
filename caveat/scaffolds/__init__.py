"""Scaffolds. Importing this package registers every scaffold.

Built-in: ``simple`` (reference loop, no extra deps beyond Playwright),
``browseruse`` (needs ``browser-use``), ``playwright-mcp`` (tool-calling agent on the Playwright-MCP
browser-tool interface, Playwright only), ``stagehand`` (needs Node + npm install).
Heavy third-party imports happen lazily inside ``run()``, so importing this package
never requires those deps to be installed.
"""

from . import (  # noqa: F401  (registration side effects)
    browseruse,
    caveat_harness,
    magentic_one,
    playwright_mcp,
    simple,
    stagehand,
    websurfer,
)
