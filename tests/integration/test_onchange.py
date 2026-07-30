"""Integration tests for per-target ``onchange`` backup gate.

All old Approach B (snapshot-name comparison) integration tests have been
deleted.  New source-disk-based onchange integration tests are in
``tests/integration/test_preserve_min.py``.

See ``openspec/changes/preserve-min-independent-onchange``.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_onchange_module_exists():
    """Placeholder — onchange integration tests moved to test_preserve_min.py."""
    pass
