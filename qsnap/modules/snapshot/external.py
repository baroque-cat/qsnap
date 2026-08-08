"""ExternalSnapshotProvider — external disk-only snapshot management via virsh.

Implements ``ISnapshotProvider``.  Does NOT inherit from Core (design D1):
the only dependency is ``IShell``.  All virsh/qemu-img calls go through
the injected shell abstraction.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from qsnap.interfaces.shell import IShell
from qsnap.interfaces.snapshot import ISnapshotProvider
from qsnap.models.config import VMConfig
from qsnap.models.results import ShellResult, SnapshotInfo, SnapshotResult, SnapshotSpec
from qsnap.utils.parsing import (
    parse_domblklist_disks,
    parse_domblklist_path_map,
    parse_timestamp,
)

logger = logging.getLogger(__name__)

_LOCK_RETRY_MAX = 3  # 1 initial attempt + 2 retries
_LOCK_RETRY_BASE = 2.0  # seconds base backoff


class ExternalSnapshotProvider(ISnapshotProvider):
    """External disk-only snapshot provider using ``virsh snapshot-create-as``."""

    def __init__(self, shell: IShell) -> None:
        self._shell = shell

    # ── ISnapshotProvider implementation ──────────────────────────────

    def create(
        self,
        vm_config: VMConfig,
        snapshot_name: str,
        disk: str,
        snapshot_path: Path,
        quiesce: bool = False,
    ) -> SnapshotResult:
        """Create an external disk-only snapshot.

        1. ``virsh domblklist`` to capture the previous active layer path
           (used for backing-filename validation in step 5).
        2. ``virsh snapshot-create-as --disk-only --atomic --no-metadata``
           (with ``--quiesce`` when *quiesce* is True, timeout 180s)
        3. ``chmod g+rw,o+r`` on the new snapshot file
        4. ``qemu-img info --force-share --output=json`` to read
           ``actual-size`` and validate qcow2 metadata.
        5. Post-creation validation (design D4): file existence, qcow2
           format, corrupt-bit check, backing-filename matches previous
           active layer, libvirt pivot confirmed via ``domblklist``.
        """
        # Step 1: Capture previous active layer for backing-filename check.
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_pre = self._shell.run(domblklist_cmd, timeout=30, check=True)
        previous_active: str | None = None
        if domblklist_pre.success:
            try:
                for target, source in parse_domblklist_disks(domblklist_pre.stdout):
                    if target == disk:
                        previous_active = source
                        break
            except ValueError:
                pass  # Non-fatal — backing-filename check skipped

        # Step 2: virsh snapshot-create-as
        create_cmd = [
            "virsh",
            "snapshot-create-as",
            "--domain",
            vm_config.name,
            "--name",
            snapshot_name,
            "--diskspec",
            f"{disk},file={snapshot_path},snapshot=external",
            "--disk-only",
            "--atomic",
            "--no-metadata",
        ]
        if quiesce:
            create_cmd.append("--quiesce")
        timeout = 180 if quiesce else 120

        # Retry loop for state change lock conflicts (design D5).
        # A lingering NBD backup job or concurrent virsh operation can
        # hold the lock transiently.  Retry up to 3 times with exponential
        # backoff (2s, 4s).  Non-lock errors fail immediately.
        create_result = None
        for attempt in range(_LOCK_RETRY_MAX):
            create_result = self._shell.run(create_cmd, timeout=timeout, check=True)
            if create_result.success:
                break
            if (
                "cannot acquire state change lock" in (create_result.error or "")
                and attempt < _LOCK_RETRY_MAX - 1
            ):
                backoff = _LOCK_RETRY_BASE * (2**attempt)
                logger.warning(
                    "Snapshot creation lock conflict for VM %s "
                    "(attempt %d/%d, retrying in %.1fs): %s",
                    vm_config.name,
                    attempt + 1,
                    _LOCK_RETRY_MAX,
                    backoff,
                    create_result.error,
                )
                time.sleep(backoff)
                continue
            # Non-lock error or retries exhausted — fail immediately.
            break

        if create_result is None or not create_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=create_result.error if create_result is not None else "unknown error",
            )

        # Step 3: chmod g+rw,o+r
        chmod_cmd = ["chmod", "g+rw,o+r", str(snapshot_path)]
        chmod_result = self._shell.run(chmod_cmd, timeout=30, check=True)
        if not chmod_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=chmod_result.error,
            )

        # Step 4: qemu-img info to get actual-size and validate metadata
        # --force-share: the snapshot file may be the active layer of a
        # running VM, which has an exclusive write lock.  --force-share
        # requests a shared lock for this metadata-only read (design D5).
        info_cmd = [
            "qemu-img",
            "info",
            "--force-share",
            "--output=json",
            str(snapshot_path),
        ]
        info_result = self._shell.run(info_cmd, timeout=60, check=True)
        if not info_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=info_result.error,
            )

        try:
            info = json.loads(info_result.stdout)
            actual_size = int(info["actual-size"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=f"Failed to parse qemu-img info output: {exc}",
            )

        # Step 5: Post-creation validation (design D4).
        # 5a. File existence — verify the snapshot file landed on disk.
        test_cmd = ["test", "-f", str(snapshot_path)]
        test_result = self._shell.run(test_cmd, timeout=10, check=True)
        if not test_result.success:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error="snapshot file not found on disk after virsh success",
            )

        # 5b. qcow2 format check.
        fmt = info.get("format", "")
        if fmt != "qcow2":
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=f"unexpected image format: expected qcow2, got {fmt}",
            )

        # 5b-2. virtual-size check — the snapshot's virtual-size must
        # match the base image's virtual-size (if determinable).  A
        # mismatch indicates a wrong backing file or a full copy.
        snap_virtual_size = info.get("virtual-size")
        if snap_virtual_size is not None and previous_active is not None:
            base_info_cmd = [
                "qemu-img",
                "info",
                "--force-share",
                "--output=json",
                previous_active,
            ]
            base_info_result = self._shell.run(
                base_info_cmd,
                timeout=30,
                check=True,
            )
            if base_info_result.success:
                try:
                    base_info = json.loads(base_info_result.stdout)
                    base_virtual_size = base_info.get("virtual-size")
                    if base_virtual_size is not None and base_virtual_size != snap_virtual_size:
                        return SnapshotResult(
                            success=False,
                            name=snapshot_name,
                            path=snapshot_path,
                            new_allocation=0,
                            error=(
                                f"virtual-size mismatch: snapshot has "
                                f"{snap_virtual_size}, base image has "
                                f"{base_virtual_size}"
                            ),
                        )
                except json.JSONDecodeError:
                    pass  # Non-fatal — cannot determine base virtual-size

        # 5b-3. actual-size reasonableness — a new overlay should have
        # a small actual-size (just metadata).  If actual-size is
        # approximately equal to virtual-size, it's likely a full
        # copy instead of an overlay.
        if snap_virtual_size is not None and actual_size > snap_virtual_size * 0.5:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error=(
                    f"actual-size {actual_size} is unreasonable for a new "
                    f"overlay (virtual-size={snap_virtual_size}) — "
                    f"likely a full copy instead of an overlay"
                ),
            )

        # 5c. Corrupt-bit check.
        incompat_features = info.get("incompatible-features", [])
        if isinstance(incompat_features, list) and "corrupt" in incompat_features:
            return SnapshotResult(
                success=False,
                name=snapshot_name,
                path=snapshot_path,
                new_allocation=0,
                error="snapshot has corrupt bit set",
            )

        # 5d. Backing-filename check — must point to the previous active
        # layer (the disk path before the snapshot was created).
        if previous_active is not None:
            backing_filename = info.get("backing-filename")
            if backing_filename != previous_active:
                return SnapshotResult(
                    success=False,
                    name=snapshot_name,
                    path=snapshot_path,
                    new_allocation=0,
                    error=(
                        f"backing-filename mismatch: "
                        f"expected {previous_active}, got {backing_filename}"
                    ),
                )

        # 5e. libvirt pivot check — domblklist must now show the snapshot
        # path as the source for the snapshotted disk.
        pivot_result = self._shell.run(domblklist_cmd, timeout=30, check=True)
        if pivot_result.success:
            try:
                pivot_disks = parse_domblklist_disks(pivot_result.stdout)
                pivot_confirmed = any(
                    target == disk and source == str(snapshot_path)
                    for target, source in pivot_disks
                )
                if not pivot_confirmed:
                    old_path = next(
                        (s for t, s in pivot_disks if t == disk),
                        "<unknown>",
                    )
                    return SnapshotResult(
                        success=False,
                        name=snapshot_name,
                        path=snapshot_path,
                        new_allocation=0,
                        error=(f"libvirt pivot not confirmed: domblklist still shows {old_path}"),
                    )
            except ValueError:
                pass  # Non-fatal — cannot verify pivot

        return SnapshotResult(
            success=True,
            name=snapshot_name,
            path=snapshot_path,
            new_allocation=actual_size,
            error=None,
            disk=disk,
        )

    def create_multi(
        self,
        vm_config: VMConfig,
        specs: Sequence[SnapshotSpec],
        quiesce: bool = False,
    ) -> list[SnapshotResult]:
        """Create external disk-only snapshots for multiple disks in ONE
        ``virsh snapshot-create-as`` call (design D9).

        All disks are snapshotted under a single guest-agent freeze (when
        ``quiesce=True``) with ``--atomic`` for all-or-nothing creation.
        Returns one :class:`SnapshotResult` per spec, in spec order.
        On any failure, best-effort ``rm -f`` removes created files.
        """
        if not specs:
            return []

        batch_name = specs[0].name.rsplit("_", 1)[0] if specs else "batch"

        # Step 1: Capture pre-snapshot domblklist for backup-filename checks.
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_pre = self._shell.run(domblklist_cmd, timeout=30, check=True)
        pre_active: dict[str, str] = {}
        if domblklist_pre.success:
            try:
                for target, source in parse_domblklist_disks(domblklist_pre.stdout):
                    pre_active[target] = source
            except ValueError:
                pass  # Non-fatal

        # Step 2: Build the batch virsh command.
        create_cmd = [
            "virsh",
            "snapshot-create-as",
            "--domain",
            vm_config.name,
            "--name",
            batch_name,
        ]
        for spec in specs:
            create_cmd.extend(
                [
                    "--diskspec",
                    f"{spec.disk},file={spec.path},snapshot=external",
                ]
            )
        create_cmd.extend(["--disk-only", "--atomic", "--no-metadata"])
        if quiesce:
            create_cmd.append("--quiesce")

        # Timeout: 180s for quiesce; 120 + 30*(N-1) for non-quiesce.
        timeout = 180 if quiesce else 120 + 30 * (len(specs) - 1)

        # Retry loop for state change lock conflicts (design D9).
        create_result = None
        for attempt in range(_LOCK_RETRY_MAX):
            create_result = self._shell.run(create_cmd, timeout=timeout, check=True)
            if create_result.success:
                break
            if (
                "cannot acquire state change lock" in (create_result.error or "")
                and attempt < _LOCK_RETRY_MAX - 1
            ):
                backoff = _LOCK_RETRY_BASE * (2**attempt)
                logger.warning(
                    "Snapshot batch lock conflict for VM %s (attempt %d/%d, retrying in %.1fs): %s",
                    vm_config.name,
                    attempt + 1,
                    _LOCK_RETRY_MAX,
                    backoff,
                    create_result.error,
                )
                time.sleep(backoff)
                continue
            break

        if create_result is None or not create_result.success:
            # Best-effort cleanup of any created files.
            self._rm_files([spec.path for spec in specs])
            return [
                SnapshotResult(
                    success=False,
                    name=spec.name,
                    path=spec.path,
                    new_allocation=0,
                    error=(create_result.error if create_result is not None else "unknown error"),
                    disk=spec.disk,
                )
                for spec in specs
            ]

        # Step 3: chmod on all snapshot files.
        for spec in specs:
            chmod_result = self._shell.run(
                ["chmod", "g+rw,o+r", str(spec.path)],
                timeout=30,
                check=True,
            )
            if not chmod_result.success:
                self._rm_files([spec.path for spec in specs])
                return [
                    SnapshotResult(
                        success=False,
                        name=s.name,
                        path=s.path,
                        new_allocation=0,
                        error=f"chmod failed: {chmod_result.error}",
                        disk=s.disk,
                    )
                    for s in specs
                ]

        # Step 4: Per-file post-creation validation.
        # Every disk must pass before the batch is accepted; ONE
        # domblklist after the batch covers all disks.
        results: list[SnapshotResult] = []
        all_valid = True
        for spec in specs:
            info_cmd = [
                "qemu-img",
                "info",
                "--force-share",
                "--output=json",
                str(spec.path),
            ]
            info_result = self._shell.run(info_cmd, timeout=60, check=True)
            if not info_result.success:
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error=info_result.error,
                        disk=spec.disk,
                    )
                )
                continue

            try:
                info = json.loads(info_result.stdout)
            except json.JSONDecodeError as exc:
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error=f"Failed to parse qemu-img info: {exc}",
                        disk=spec.disk,
                    )
                )
                continue

            actual_size = int(info.get("actual-size", 0))
            previous_active = pre_active.get(spec.disk)

            # File existence check.
            test_cmd = ["test", "-f", str(spec.path)]
            test_result = self._shell.run(test_cmd, timeout=10, check=True)
            if not test_result.success:
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error="snapshot file not found on disk after virsh success",
                        disk=spec.disk,
                    )
                )
                continue

            # qcow2 format check.
            fmt = info.get("format", "")
            if fmt != "qcow2":
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error=f"unexpected image format: expected qcow2, got {fmt}",
                        disk=spec.disk,
                    )
                )
                continue

            # virtual-size check.
            snap_virtual_size = info.get("virtual-size")
            if snap_virtual_size is not None and previous_active is not None:
                base_info_result = self._shell.run(
                    ["qemu-img", "info", "--force-share", "--output=json", previous_active],
                    timeout=30,
                    check=True,
                )
                if base_info_result.success:
                    try:
                        base_info = json.loads(base_info_result.stdout)
                        base_virtual_size = base_info.get("virtual-size")
                        if base_virtual_size is not None and base_virtual_size != snap_virtual_size:
                            all_valid = False
                            results.append(
                                SnapshotResult(
                                    success=False,
                                    name=spec.name,
                                    path=spec.path,
                                    new_allocation=0,
                                    error=(
                                        f"virtual-size mismatch: snapshot has "
                                        f"{snap_virtual_size}, base image has "
                                        f"{base_virtual_size}"
                                    ),
                                    disk=spec.disk,
                                )
                            )
                            continue
                    except json.JSONDecodeError:
                        pass

            # actual-size reasonableness.
            if snap_virtual_size is not None and actual_size > snap_virtual_size * 0.5:
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error=(
                            f"actual-size {actual_size} is unreasonable for a new "
                            f"overlay (virtual-size={snap_virtual_size})"
                        ),
                        disk=spec.disk,
                    )
                )
                continue

            # Corrupt-bit check.
            incompat_features = info.get("incompatible-features", [])
            if isinstance(incompat_features, list) and "corrupt" in incompat_features:
                all_valid = False
                results.append(
                    SnapshotResult(
                        success=False,
                        name=spec.name,
                        path=spec.path,
                        new_allocation=0,
                        error="snapshot has corrupt bit set",
                        disk=spec.disk,
                    )
                )
                continue

            # Backing-filename check.
            if previous_active is not None:
                backing_filename = info.get("backing-filename")
                if backing_filename != previous_active:
                    all_valid = False
                    results.append(
                        SnapshotResult(
                            success=False,
                            name=spec.name,
                            path=spec.path,
                            new_allocation=0,
                            error=(
                                f"backing-filename mismatch: "
                                f"expected {previous_active}, got {backing_filename}"
                            ),
                            disk=spec.disk,
                        )
                    )
                    continue

            results.append(
                SnapshotResult(
                    success=True,
                    name=spec.name,
                    path=spec.path,
                    new_allocation=actual_size,
                    error=None,
                    disk=spec.disk,
                )
            )

        # Step 5: ONE domblklist pivot check for all disks.
        if all_valid:
            pivot_result = self._shell.run(domblklist_cmd, timeout=30, check=True)
            if pivot_result.success:
                try:
                    pivot_disks = dict(parse_domblklist_disks(pivot_result.stdout))
                    for spec in specs:
                        current = pivot_disks.get(spec.disk)
                        if current != str(spec.path):
                            all_valid = False
                            idx = next(i for i, r in enumerate(results) if r.name == spec.name)
                            results[idx] = SnapshotResult(
                                success=False,
                                name=spec.name,
                                path=spec.path,
                                new_allocation=0,
                                error=(
                                    f"libvirt pivot not confirmed: "
                                    f"domblklist shows {current or '<unknown>'}"
                                ),
                                disk=spec.disk,
                            )
                except (ValueError, StopIteration):
                    pass

        # On any failure, best-effort cleanup and report ALL as failed.
        if not all_valid:
            self._rm_files([spec.path for spec in specs])
            # Any remaining successful results become failures.
            for i, r in enumerate(results):
                if r.success:
                    results[i] = SnapshotResult(
                        success=False,
                        name=r.name,
                        path=r.path,
                        new_allocation=0,
                        error="batch rejected due to another disk's validation failure",
                        disk=r.disk,
                    )

        return results

    def _rm_files(self, paths: list[Path]) -> None:
        """Best-effort removal of created snapshot files."""
        for p in paths:
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)

    def list(self, vm_config: VMConfig) -> list[SnapshotInfo]:
        """List existing snapshots via the backing chains of all disks.

        Multi-disk (refactor): each configured disk has its own backing
        chain.  For every disk in ``vm_config.disks`` the active path is
        resolved via ``virsh domblklist`` and its chain is scanned via
        ``qemu-img info --backing-chain``.  Each resulting
        :class:`SnapshotInfo` carries the disk target it belongs to.
        Returns a single flat list sorted by timestamp.
        """
        # Step 1: Get all active disk paths via domblklist.
        domblklist_cmd = ["virsh", "domblklist", "--domain", vm_config.name]
        domblklist_result = self._shell.run(domblklist_cmd, timeout=30, check=True)
        if not domblklist_result.success:
            return []

        try:
            path_map = parse_domblklist_path_map(domblklist_result.stdout)
        except ValueError:
            return []

        # Step 2: Scan each configured disk's backing chain.
        snapshots: list[SnapshotInfo] = []
        for disk_cfg in vm_config.disks:
            active_disk = path_map.get(disk_cfg.target)
            if active_disk is None:
                logger.warning(
                    "Disk %s of VM %s not found in domblklist — skipping its chain",
                    disk_cfg.target,
                    vm_config.name,
                )
                continue
            snapshots.extend(self._list_chain(active_disk, disk_cfg.target))

        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    def _list_chain(self, active_disk: str, disk: str) -> list[SnapshotInfo]:
        """Scan one disk's backing chain and return its snapshots.

        Skips the first element (base image); builds a ``SnapshotInfo``
        (tagged with *disk*) for each subsequent chain element.
        """
        chain_cmd = [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            active_disk,
        ]
        chain_result = self._shell.run(chain_cmd, timeout=60, check=True)
        if not chain_result.success:
            return []

        try:
            chain = cast(list[dict[str, object]], json.loads(chain_result.stdout))
        except json.JSONDecodeError:
            return []

        if not isinstance(chain, list) or len(chain) <= 1:  # type: ignore[reportUnnecessaryIsInstance]
            return []

        snapshots: list[SnapshotInfo] = []
        for element in chain[1:]:
            filename = cast(str, element.get("filename", ""))
            name = Path(filename).stem
            actual_size = int(cast(int, element.get("actual-size", 0)))
            timestamp = parse_timestamp(name, Path(filename))
            snapshots.append(
                SnapshotInfo(
                    name=name,
                    path=Path(filename),
                    timestamp=timestamp,
                    allocation=actual_size,
                    disk=disk,
                )
            )
        return snapshots

    def delete(self, snapshot: SnapshotInfo) -> ShellResult:
        """Delete a snapshot file via ``rm -f``."""
        cmd = ["rm", "-f", str(snapshot.path)]
        return self._shell.run(cmd, timeout=30)
