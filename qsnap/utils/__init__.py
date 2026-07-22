"""Utility functions for qsnap.

Cross-cutting stateless helpers shared across module boundaries:
- :mod:`qsnap.utils.mac` — AppArmor/SELinux denial detection.
- :mod:`qsnap.utils.nbd` — NBD/libvirt helper functions.
- :mod:`qsnap.utils.verification` — backup verification functions.
- :mod:`qsnap.utils.parsing` — config value parsing.
- :mod:`qsnap.utils.retry` — retry/backoff helpers.
- :mod:`qsnap.utils.time` — timestamp formatting.
"""

from __future__ import annotations

from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
)
from qsnap.utils.verification import verify_full_backup

__all__ = [
    "is_libvirt_new_enough",
    "is_vm_running",
    "verify_full_backup",
]
