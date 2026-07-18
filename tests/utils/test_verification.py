"""Unit tests for qsnap.utils.verification — shared verification functions.

Tests verify that backup verification functions are importable from the
shared utility module (not from a domain sub-package).
"""

from __future__ import annotations

from qsnap.utils.verification import verify_backup, verify_full_backup


def test_verify_full_backup_imported_from_utils() -> None:
    """``verify_full_backup`` and ``verify_backup`` are importable from
    ``qsnap.utils.verification`` and are callable.
    """
    assert callable(verify_full_backup)
    assert callable(verify_backup)
