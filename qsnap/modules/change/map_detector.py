"""MapChangeDetector — change detection via qemu-img map comparison.

Implements ``IChangeDetector``.  Does NOT inherit from Core (design D1).
Dependencies: ``IShell`` and ``IStateManager``.

Uses ``qemu-img map --output=json`` to obtain the set of allocated disk
regions and compares a hash of the sorted ``(offset, length)`` tuples
against the prior recorded state.  Fail-safe: returns ``changed=True``
on any command error.
"""

from __future__ import annotations

import hashlib
import json
import logging

from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import VMConfig
from qsnap.models.results import ChangeResult
from qsnap.utils.parsing import parse_domblklist_disks

logger = logging.getLogger(__name__)


class MapChangeDetector(IChangeDetector):
    """Detects VM disk changes by comparing allocation maps.

    The current allocation map is obtained via ``qemu-img map`` on the
    *active* image (resolved via ``virsh domblklist``, design D3).  A
    hash of the sorted ``(offset, length)`` tuples is stored as the
    "allocation" value in ``IStateManager``.
    """

    def __init__(self, shell: IShell, state: IStateManager) -> None:
        self._shell = shell
        self._state = state

    # ── IChangeDetector implementation ────────────────────────────────

    def has_changed(self, vm_config: VMConfig, disk: str | None = None) -> ChangeResult:
        """Check whether the VM disk allocation map has changed since last run.

        Fail-safe: any command failure returns ``changed=True``.
        """
        # Step 1: Get last allocation hash from state
        last_alloc = self._state.get_last_allocation(vm_config.name)
        if last_alloc is None:
            return ChangeResult(
                changed=True,
                last_allocation=0,
                current_allocation=0,
            )

        # Step 2: Get active disk paths via domblklist (design D3)
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_result = self._shell.run(domblklist_cmd, timeout=30)
        if not domblklist_result.success:
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        disks = parse_domblklist_disks(domblklist_result.stdout)
        if not disks:
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        # Resolve the target disk path.
        if disk is not None:
            active_disk = next((path for target, path in disks if target == disk), None)
            if active_disk is None:
                return ChangeResult(
                    changed=True,
                    last_allocation=last_alloc,
                    current_allocation=0,
                )
        else:
            active_disk = disks[0][1]

        # Step 3: Get current allocation map via qemu-img map
        # --force-share: the active disk may be the active layer of a
        # running VM, which has an exclusive write lock.  --force-share
        # requests a shared lock for this metadata-only read (design D5).
        map_cmd = [
            "qemu-img",
            "map",
            "--force-share",
            "--output=json",
            active_disk,
        ]
        map_result = self._shell.run(map_cmd, timeout=60)
        if not map_result.success:
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        # Step 4: Compute hash of allocated regions
        try:
            regions = json.loads(map_result.stdout)
            if not isinstance(regions, list):
                return ChangeResult(
                    changed=True,
                    last_allocation=last_alloc,
                    current_allocation=0,
                )
            # Build a deterministic hash from sorted (offset, length) tuples.
            offsets = sorted((int(r.get("offset", 0)), int(r.get("length", 0))) for r in regions)
            map_hash = int(
                hashlib.sha256(repr(offsets).encode()).hexdigest(),
                16,
            )
            # Truncate to fit in a standard integer range.
            current_alloc = map_hash % (2**31)
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        # Step 5: Compare
        changed = current_alloc != last_alloc
        return ChangeResult(
            changed=changed,
            last_allocation=last_alloc,
            current_allocation=current_alloc,
        )
