"""Stress test: disk-full (ENOSPC) mid-transfer fault handling.

Real-filesystem disk-full scenario (design D2/D6/D7):

1. The test VM's images live on the default ``/tmp`` tmpfs (via
   ``stress_env``).  A dedicated target A is created on ``/var/tmp``
   (the ext4 root filesystem) and FILLED with ``fallocate`` so only a
   small margin remains.  A second target B lives on ``/tmp`` (tmpfs —
   different storage).
2. A FULL backup to target A hits ENOSPC mid-transfer (``qemu-img
   convert`` writes past the free space): NO completed backup file, NO
   deletion of prior data, run ``space_limited=True`` (CLI exit 4), and
   target B — on different storage — completes normally.
3. The filler is removed (space freed) and the next run auto-resumes:
   target A's FULL completes and passes verification.

No root/loopback mounts are required — ``fallocate`` fills the
filesystem and is instant on ext4.  The test skips when the root
filesystem has too little free space to stage a safe filler.

Marked ``@pytest.mark.stress`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from qsnap.cli.commands import _format_pipeline_result
from qsnap.cli.errors import EXIT_DISKFULL
from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, TargetConfig, VMConfig
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running
from tests.helpers import snapshot_create
from tests.mocks.mock_config import MockConfigFacade
from tests.mocks.mock_state import InMemoryStateManager

#: Free-space margin left on the root fs while the filler is in place.
#: The FULL backup of a ~300 MB disk needs far more than this, so the
#: convert fails mid-transfer with ENOSPC.
_FILL_MARGIN_BYTES = 150 * 1024 * 1024

#: Guard: skip when the root fs has less than this much free space.
_MIN_FREE_FOR_TEST = 2 * 1024 * 1024 * 1024


def _fill_fs(target_dir: Path, shell: SubprocessShell) -> Path | None:
    """Fill the filesystem holding *target_dir*, leaving a small margin.

    Returns the filler file path (caller must ``unlink`` it to free
    space), or ``None`` when the environment has too little free space.
    """
    usage = shutil.disk_usage(str(target_dir))
    if usage.free < _MIN_FREE_FOR_TEST:
        return None
    filler_size = usage.free - _FILL_MARGIN_BYTES
    filler = target_dir / f"enospc-filler-{time.time_ns()}.bin"
    result = shell.run(
        ["fallocate", "-l", str(filler_size), str(filler)],
        timeout=120,
        check=True,
    )
    if not result.success:
        return None
    return filler


def _qemu_img_check_ok(shell: SubprocessShell, path: Path) -> bool:
    result = shell.run(
        ["qemu-img", "check", "--force-share", str(path)],
        timeout=120,
    )
    return result.success and "No errors were found" in result.stdout


@pytest.mark.stress
@pytest.mark.timeout(3600)
def test_enospc_mid_transfer_isolation_and_auto_resume(stress_env):
    """FULL hits ENOSPC mid-transfer: no deletion, exit 4, other target
    continues; after freeing space the next run auto-resumes.

    1. Write 300 MB of data into the base image (VM stopped), start VM,
       create one snapshot (recorded with ``disk="vda"``).
    2. Fill ``/var/tmp`` (target A's filesystem) with ``fallocate``,
       leaving only a small margin.
    3. Run ``core.backup()`` with target A on the filled filesystem
       (``free_space_check="off"`` so the reactive ENOSPC path is
       exercised, not the proactive gate) and target B on ``/tmp``.
    4. Assert: target A produced NO completed FULL file (only possibly a
       ``.tmp``), no snapshot/state deletion, the run is
       ``space_limited`` and the CLI maps it to ``EXIT_DISKFULL`` (4),
       while target B's FULL completed normally.
    5. Free the space and run again → target A's FULL completes and
       passes ``qemu-img check`` (auto-resume contract).
    """
    shell: SubprocessShell = stress_env["shell"]
    vm_name: str = stress_env["vm_name"]
    base_image: Path = stress_env["base_image"]
    snapshot_dir: Path = stress_env["snapshot_dir"]
    tmpdir: Path = stress_env["tmpdir"]

    # Target A on /var/tmp (ext4 root — fillable); target B on /tmp
    # (tmpfs — different storage that must remain unaffected).
    target_a = Path(tempfile.mkdtemp(prefix="qsnap-enospc-targetA-", dir="/var/tmp"))
    target_b = tmpdir / "backup-b"
    target_b.mkdir(parents=True, exist_ok=True)

    filler: Path | None = None
    try:
        # Step 1: data + snapshot.
        if is_vm_running(shell, vm_name):
            shell.run(["virsh", "destroy", vm_name], timeout=30)
            time.sleep(1)
        write = shell.run(
            ["qemu-io", "-c", "write -P 0xAA 0 300M", str(base_image)],
            timeout=180,
            check=True,
        )
        if not write.success:
            pytest.skip(f"Failed to write 300M of test data: {write.error}")

        start = shell.run(["virsh", "start", vm_name], timeout=30)
        if not start.success:
            pytest.skip(f"virsh start failed: {start.error}")
        time.sleep(2)
        if not is_vm_running(shell, vm_name):
            pytest.skip("VM did not reach running state")

        import secrets

        snap = snapshot_create(
            shell,
            vm_name,
            f"{vm_name}.enospc-stress-{secrets.token_hex(3)}",
            "vda",
            snapshot_dir,
            base_image,
        )
        state = InMemoryStateManager()
        state.record_snapshot(vm_name, snap)
        state.set_last_allocation(vm_name, "vda", snap.allocation)

        # Step 2: fill target A's filesystem.
        filler = _fill_fs(target_a, shell)
        if filler is None:
            pytest.skip(
                "Root filesystem too tight or fallocate unavailable — "
                "cannot stage a safe disk-full scenario"
            )

        # Step 3: two targets — A (filled fs) and B (tmpfs).  The
        # free-space gate is OFF at the VM level so the REACTIVE ENOSPC
        # path is exercised (the proactive strict gate would otherwise
        # block the transfer before it starts).
        vm_config = VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
            free_space_check="off",
            targets=[
                TargetConfig(path=target_a, compress=False, verify="off"),
                TargetConfig(path=target_b, compress=False, verify="off"),
            ],
        )
        config = MockConfigFacade(
            vms=[vm_config],
            config_path=tmpdir / "enospc.toml",
        )
        core = Core(
            config=config,
            factory=DefaultFactory(shell=shell, state=state),
            state=state,
            shell=shell,
        )

        result = core.backup(vm_name)

        # Step 4: assertions.
        # (a) No completed FULL on target A — at most a .tmp leftover.
        fulls_a = list(target_a.glob("*.FULL.*.qcow2"))
        assert fulls_a == [], (
            f"No completed FULL may exist on the filled target: {[p.name for p in fulls_a]}"
        )
        # (b) No deletion of snapshot data/state.
        assert snap.path.exists(), "Source snapshot must survive ENOSPC"
        assert len(state.get_snapshots(vm_name)) == 1, "State records must survive ENOSPC"
        # (c) space_limited → CLI exit code 4.
        assert result.space_limited is True, "ENOSPC mid-transfer must mark the run space_limited"
        assert _format_pipeline_result(result) == EXIT_DISKFULL, (
            f"CLI must map the space-limited run to exit {EXIT_DISKFULL}"
        )
        # (d) No VM abort: the per-VM result is a success (target
        #     suspension, not failure) and backup_failed is False.
        vm_result = result.results[0]
        assert vm_result.success is True, f"ENOSPC must not abort the VM: {vm_result.error}"
        assert vm_result.backup_failed is False, (
            "ENOSPC must not set backup_failed (no BackupAbortError)"
        )
        # (e) Target B on different storage completed normally.
        fulls_b = list(target_b.glob("*.FULL.*.qcow2"))
        assert len(fulls_b) >= 1, (
            f"Target B (different storage) must complete its FULL. "
            f"Got: {[p.name for p in target_b.glob('*.qcow2')]}"
        )
        assert _qemu_img_check_ok(shell, fulls_b[0]), (
            f"Target B FULL must pass qemu-img check: {fulls_b[0]}"
        )

        # Step 5: free space → next run auto-resumes (FULL completes and
        # passes verification on target A).  Scope the resume run to
        # target A only: target B's incremental re-transfer of the same
        # snapshot is out of scope for the auto-resume contract (its
        # chain state was already validated in run 1).
        filler.unlink(missing_ok=True)
        filler = None

        vm_config_resume = VMConfig(
            name=vm_name,
            disks=[DiskConfig(target="vda", base_image=base_image)],
            snapshot_dir=snapshot_dir,
            free_space_check="off",
            targets=[
                TargetConfig(path=target_a, compress=False, verify="off"),
            ],
        )
        config_resume = MockConfigFacade(
            vms=[vm_config_resume],
            config_path=tmpdir / "enospc_resume.toml",
        )
        core_resume = Core(
            config=config_resume,
            factory=DefaultFactory(shell=shell, state=state),
            state=state,
            shell=shell,
        )

        result2 = core_resume.backup(vm_name)
        assert result2.results[0].success, f"Auto-resume run failed: {result2.results[0].error}"
        fulls_a_after = list(target_a.glob("*.FULL.*.qcow2"))
        assert len(fulls_a_after) >= 1, (
            f"Auto-resume must complete target A's FULL. "
            f"Got: {[p.name for p in target_a.glob('*.qcow2')]}"
        )
        assert _qemu_img_check_ok(shell, fulls_a_after[0]), (
            f"Auto-resumed FULL must pass qemu-img check: {fulls_a_after[0]}"
        )
        assert result2.space_limited is False, "A clean auto-resume run must not be space_limited"
    finally:
        if filler is not None:
            filler.unlink(missing_ok=True)
        shell.run(["virsh", "destroy", vm_name], timeout=30)
        shutil.rmtree(str(target_a), ignore_errors=True)
