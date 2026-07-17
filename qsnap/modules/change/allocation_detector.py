"""AllocationSizeDetector — change detection via allocation-size comparison.

Implements ``IChangeDetector``.  Does NOT inherit from Core (design D1).
Dependencies: ``IShell`` and ``IStateManager`` (design D2 — state is
injected by the factory, not passed through Core).
"""

from __future__ import annotations

import json
import logging

from qsnap.interfaces.change import IChangeDetector
from qsnap.interfaces.shell import IShell
from qsnap.interfaces.state import IStateManager
from qsnap.models.config import VMConfig
from qsnap.models.results import ChangeResult
from qsnap.utils.parsing import parse_domblklist_disks

logger = logging.getLogger(__name__)


class AllocationSizeDetector(IChangeDetector):
    """Detects VM disk changes by comparing allocation sizes.

    The current allocation is obtained via ``qemu-img info`` on the
    *active* image (resolved via ``virsh domblklist``, design D3 — not
    ``vm_config.base_image``).  The last recorded allocation comes from
    ``IStateManager``.
    """

    def __init__(self, shell: IShell, state: IStateManager) -> None:
        self._shell = shell
        self._state = state

    # ── IChangeDetector implementation ────────────────────────────────

    def has_changed(self, vm_config: VMConfig, disk: str | None = None) -> ChangeResult:
        """Check whether the VM disk allocation has grown since last run.

        When *disk* is provided, scope detection to that specific disk.
        When omitted, use the first discovered disk (backward-compatible).

        Fail-safe: any command failure returns ``changed=True``
        (rather create an unnecessary snapshot than miss changes).
        """
        # Step 1: Get last allocation from state
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

        # Step 3: Get current allocation via qemu-img info
        info_cmd = [
            "qemu-img",
            "info",
            "--force-share",
            "--output=json",
            active_disk,
        ]
        info_result = self._shell.run(info_cmd, timeout=60)
        if not info_result.success:
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        try:
            info = json.loads(info_result.stdout)
            current_alloc = int(info["actual-size"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return ChangeResult(
                changed=True,
                last_allocation=last_alloc,
                current_allocation=0,
            )

        # Step 5: Compare
        return ChangeResult(
            changed=(current_alloc > last_alloc),
            last_allocation=last_alloc,
            current_allocation=current_alloc,
        )
