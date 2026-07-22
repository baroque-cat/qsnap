"""Backup module — NBD pull-model bitmap backup transfer."""

from qsnap.utils.nbd import is_libvirt_new_enough
from qsnap.utils.verification import verify_full_backup

__all__ = ["is_libvirt_new_enough", "verify_full_backup"]
