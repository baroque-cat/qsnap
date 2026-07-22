"""Unit tests for qsnap.utils.verification — shared verification functions.

Tests verify that backup verification functions are importable from the
shared utility module (not from a domain sub-package).
"""

from __future__ import annotations

from qsnap.utils.verification import verify_full_backup


def test_verify_full_backup_imported_from_utils() -> None:
    """``verify_full_backup`` is importable from ``qsnap.utils.verification``
    and is callable.
    """
    assert callable(verify_full_backup)
