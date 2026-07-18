"""Backup module — file-copy backup transfer with rebase."""

from qsnap.utils.nbd import is_libvirt_new_enough
from qsnap.utils.verification import verify_backup, verify_full_backup

__all__ = ["is_libvirt_new_enough", "verify_backup", "verify_full_backup"]
