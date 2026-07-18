"""Utility functions for qsnap.

Cross-cutting stateless helpers shared across module boundaries:
- :mod:`qsnap.utils.hash` — file SHA-256 hashing.
- :mod:`qsnap.utils.nbd` — NBD/libvirt helper functions.
- :mod:`qsnap.utils.verification` — backup verification functions.
- :mod:`qsnap.utils.parsing` — config value parsing.
- :mod:`qsnap.utils.retry` — retry/backoff helpers.
- :mod:`qsnap.utils.time` — timestamp formatting.
"""

from __future__ import annotations

from qsnap.utils.hash import file_sha256
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
)
from qsnap.utils.verification import verify_backup, verify_full_backup

__all__ = [
    "file_sha256",
    "is_libvirt_new_enough",
    "is_vm_running",
    "nbd_full_export",
    "verify_backup",
    "verify_full_backup",
]
