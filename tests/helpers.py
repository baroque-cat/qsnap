"""Shared test helper functions used across multiple test modules."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import DeferredBlockcommit, SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider


def add_deferred_with_since(
    state,
    vm_name: str,
    disk: str,
    snapshots: list[str],
    reason: str,
    since: datetime,
) -> None:
    """Add a deferred blockcommit with a specific ``since`` timestamp.

    Unlike ``InMemoryStateManager.add_deferred_blockcommit`` which always
    uses ``datetime.now()``, this helper lets tests control the ``since``
    timestamp for age-based assertions.
    """
    if vm_name not in state._state:
        state._state[vm_name] = {}
    deferred = state._state[vm_name].setdefault("deferred_operations", [])
    deferred.append(
        DeferredBlockcommit(
            snapshots=list(snapshots),
            reason=reason,
            since=since,
            disk=disk,
        )
    )


def snapshot_create(
    shell,
    vm_name: str,
    snap_name: str,
    disk: str,
    snapshot_dir: Path,
    base_image: Path,
) -> SnapshotInfo:
    """Create an external disk-only snapshot and return ``SnapshotInfo``.

    Uses :class:`ExternalSnapshotProvider` with a minimal single-disk
    ``VMConfig`` so the helper works with both single-disk and multi-disk
    fixtures.  The returned ``SnapshotInfo`` includes all required fields
    (including ``disk``).

    Args:
        shell: ``IShell`` implementation (e.g. ``SubprocessShell``).
        vm_name: Name of the VM (must be running).
        snap_name: Human-readable snapshot identifier (e.g. ``"myvm.snap1"``).
        disk: Libvirt target device name (e.g. ``"vda"``).
        snapshot_dir: Directory where the snapshot file will be created.
        base_image: Path to the base qcow2 image for this disk.

    Returns:
        ``SnapshotInfo`` with name, path, timestamp, allocation, and disk.
    """
    snap_path = snapshot_dir / f"{snap_name}.qcow2"
    provider = ExternalSnapshotProvider(shell)
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target=disk, base_image=base_image, snapshot_dir=snapshot_dir)],
        snapshot_dir=snapshot_dir,
    )
    result = provider.create(vm_config, snap_name, disk, snap_path)
    assert result.success, f"Snapshot creation failed for {disk}: {result.error}"
    return SnapshotInfo(
        name=result.name,
        path=result.path,
        timestamp=datetime.now(),
        allocation=result.new_allocation,
        disk=disk,
    )
