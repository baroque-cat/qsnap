"""E2E test: restore a backup to a new VM.

This test verifies that a qsnap backup can be restored to a new VM
using the backing chain and FULL anchor files, producing a bootable
disk image.

Marked ``@pytest.mark.e2e`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
def test_restore_backup_to_new_vm(e2e_vm):
    """Restore a qsnap backup to a new VM.

    Steps (placeholder — implement when libvirt test environment is
    available):
      1. Run ``qsnap run`` to create a snapshot + backup.
      2. Create a new VM definition pointing at the backup target
         directory's FULL anchor file.
      3. Verify the new VM's disk image has a valid backing chain.
      4. Start the new VM and verify it boots (or at least reaches
         the firmware/boot stage without errors).
      5. Verify ``qemu-img check`` passes on the restored image.
    """
    pytest.skip("Requires libvirt environment with test VM")
