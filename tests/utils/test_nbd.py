"""Unit tests for qsnap.utils.nbd — shared NBD utility functions.

Tests verify that public NBD functions are importable from the shared
utility module (not from a domain sub-package).
"""

from __future__ import annotations

from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
)


def test_nbd_public_functions_importable() -> None:
    """``is_vm_running``, ``is_libvirt_new_enough``, and ``nbd_full_export``
    are importable from ``qsnap.utils.nbd`` and are callable.
    """
    assert callable(is_vm_running)
    assert callable(is_libvirt_new_enough)
    assert callable(nbd_full_export)
