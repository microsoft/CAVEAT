"""Register the two supported CAVEAT release harnesses.

``browseruse`` is the BrowserUse baseline and ``caveat-harness`` is the improved
CAVEAT-Harness. Third-party imports remain lazy so listing the registry does not
require BrowserUse to be installed.
"""

from . import browseruse, caveat_harness  # noqa: F401  (registration side effects)
