"""Integration tests: crash recovery mid-bulk-blockcommit (design risk #344).

The bulk collapse is all-or-nothing and the client (``virsh blockcommit
--wait``) can die while QEMU keeps running the job.  These tests prove,
against a REAL 9-layer chain, that the next run's step-0 intent recovery
handles both halves of that window:

- ``test_bulk_job_killed_midflight_reconciles_next_run`` — a real
  throttled segment ``virsh blockcommit`` is started, its virsh client is
  SIGKILLed mid-flight (the QEMU job keeps running), and the next
  pipeline run probes ``virsh blockjob`` → sees the job active → defers
  with the intent kept and starts NO competing commit; once the job
  completes, step-0 recovery converges ``late_success`` with zero data
  loss.
- ``test_bulk_timeout_then_late_success_real_chain`` — the injected
  scaled timeout fires (``GlobalConfig.blockcommit_timeout=1``) while
  QEMU finishes the (test-throttled) job; the run classifies the outcome
  as ``unknown`` → ``job_active`` deferral with the intent kept, and the
  next-run reconciliation converges without data loss.

All tests require a running libvirt daemon and are marked
``@pytest.mark.integration``.  Run only when explicitly requested::

    poetry run pytest tests/integration/test_bulk_crash_recovery.py -v -m integration
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from qsnap.core import Core
from qsnap.factory.default import DefaultFactory
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.models.results import SnapshotInfo
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_config import MockConfigFacade

#: Test throttle for the foreign / injected segment job (bytes/s).
#: 256 KiB/s keeps the job active long enough to observe mid-flight
#: states while still completing in a testable window.
_THROTTLE_BYTES = 262144  # 256 KiB/s

#: Data written into the oldest overlay so the segment job has real work.
_DATA_BYTES = "16M"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _build_core(
    shell: SubprocessShell,
    vm_name: str,
    base_image: Path,
    snapshot_dir: Path,
    target_dir: Path,
    state_dir: Path,
    *,
    blockcommit_timeout: int = 1800,
    h: int = 8,
    floor: int = 3,
) -> tuple[Core, VMConfig, JsonStateManager]:
    """Build a hysteresis Core with a JSON-backed state manager."""
    state = JsonStateManager(state_dir)
    vm_config = VMConfig(
        name=vm_name,
        disks=[DiskConfig(target="vda", base_image=base_image)],
        snapshot_dir=snapshot_dir,
        snapshot_chain_length=h,
        snapshot_preserve_min=floor,
        snapshot_retention_mode="hysteresis",
        lifecycle_mode="virsh",
        targets=[
            TargetConfig(
                path=target_dir,
                compress=False,
                verify="off",
            )
        ],
    )
    config = MockConfigFacade(
        global_config=GlobalConfig(blockcommit_timeout=blockcommit_timeout),
        vms=[vm_config],
    )
    factory = DefaultFactory(shell=shell, state=state)
    core = Core(config=config, factory=factory, state=state, shell=shell)
    return core, vm_config, state


def _start_vm(shell: SubprocessShell, vm_name: str) -> None:
    """Start the test VM or skip when libvirt is not usable."""
    start_result = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start_result.success:
        pytest.skip(f"virsh start failed: {start_result.error}")
    time.sleep(1)
    assert _vm_is_running(shell, vm_name), "VM should be running"


def _vm_is_running(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True if the VM is running."""
    result = shell.run(["virsh", "domstate", "--domain", vm_name], timeout=30)
    return result.success and "running" in result.stdout.lower()


def _create_snapshots(core: Core, vm_config: VMConfig, count: int) -> list[SnapshotInfo]:
    """Create *count* external snapshots via Core, return oldest-first."""
    for i in range(count):
        results = core._create_snapshot(vm_config)
        assert len(results) >= 1, f"Snapshot {i + 1} creation returned no results"
        assert results[0].success, f"Snapshot {i + 1} failed: {results[0].error}"
        time.sleep(1.1)
    return sorted(core._state.get_snapshots(vm_config.name), key=lambda s: s.timestamp)


def _write_data_into_overlay(
    shell: SubprocessShell,
    vm_name: str,
    overlay: Path,
    size: str = _DATA_BYTES,
) -> None:
    """Stop the VM, write *size* of data into *overlay*, restart the VM.

    The write gives the segment blockcommit real work so the job stays
    active long enough to observe mid-flight states.  Safe: the VM is
    stopped while the overlay is written.
    """
    stop = shell.run(["virsh", "destroy", vm_name], timeout=30)
    assert stop.success, f"virsh destroy failed: {stop.error}"
    time.sleep(1)
    write = shell.run(
        ["qemu-io", "-f", "qcow2", "-c", f"write 0 {size}", str(overlay)],
        timeout=120,
    )
    assert write.success, f"qemu-io write failed: {write.error}"
    start_again = shell.run(["virsh", "start", vm_name], timeout=30)
    assert start_again.success, f"virsh start after data write failed: {start_again.error}"
    time.sleep(2)
    assert _vm_is_running(shell, vm_name), "VM should be running after the data write"


def _backing_chain_length(tip_path: Path, shell: SubprocessShell) -> int | None:
    """Return the backing-chain length from *tip_path* (base image included)."""
    result = shell.run(
        [
            "qemu-img",
            "info",
            "--force-share",
            "--backing-chain",
            "--output=json",
            str(tip_path),
        ],
        timeout=30,
    )
    if not result.success:
        return None
    chain = json.loads(result.stdout)
    return len(chain) if isinstance(chain, list) else None


def _blockjob_idle(shell: SubprocessShell, vm_name: str) -> bool:
    """Return True when ``virsh blockjob`` reports no active job."""
    result = shell.run(
        ["virsh", "blockjob", "--domain", vm_name, "--path", "vda"],
        timeout=30,
    )
    if not result.success:
        return False
    return "No current block job" in result.stdout or not result.stdout.strip()


def _wait_for_blockjob_idle(shell: SubprocessShell, vm_name: str, timeout: int = 180) -> None:
    """Poll until the disk's block job is gone, or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _blockjob_idle(shell, vm_name):
            return
        time.sleep(2)
    pytest.fail(f"Block job did not complete within {timeout}s")


def _wait_for_file_gone(path: Path, timeout: int = 30) -> None:
    """Poll until *path* is gone (libvirt ``--delete`` races job end)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return
        time.sleep(1)
    pytest.fail(f"File was not deleted within {timeout}s: {path}")


# ──────────────────────────────────────────────────────────────────────
# Test 1: SIGKILLed client mid-flight → defer → late_success convergence
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_bulk_job_killed_midflight_reconciles_next_run(test_vm, caplog):
    """A SIGKILLed bulk-job client defers next run, then late-converges.

    1. Start VM, create 9 snapshots (H=8, L=3 → merge set = s1..s6).
    2. Write real data into s1 (so the commit has work), restart VM.
    3. Plant the commit intent exactly as the pipeline would have, then
       start a REAL throttled segment ``virsh blockcommit`` (--top s6)
       and SIGKILL its virsh client — the QEMU job keeps running.
    4. Run the next pipeline snapshot steps: step-0 intent recovery
       probes the active job → defers (reason ``blockjob_active``) with
       the intent kept; the pre-commit race guard starts NO competing
       commit; snapshot creation is skipped.
    5. When the background job completes, run step-0 recovery again:
       ``late_success`` converges the full merge set without data loss.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    _start_vm(shell, vm_name)
    core, vm_config, state = _build_core(
        shell, vm_name, base_image, snapshot_dir, target_dir, state_dir
    )
    snaps = _create_snapshots(core, vm_config, 9)
    merge_set = snaps[:6]
    floor_set = snaps[6:]
    newest_removable = merge_set[-1]

    # Give the segment job real work.
    _write_data_into_overlay(shell, vm_name, merge_set[0].path)

    # ── Plant the intent + start the real throttled segment job ───────
    state.set_commit_in_progress(
        vm_name,
        "vda",
        [s.name for s in merge_set],
        str(base_image),
        datetime.now().strftime("%Y%m%dT%H%M%S"),
    )
    commit_cmd = [
        "virsh",
        "blockcommit",
        "--domain",
        vm_name,
        "--path",
        "vda",
        "--base",
        str(base_image),
        "--top",
        str(newest_removable.path),
        "--delete",
        "--verbose",
        "--wait",
        "--bandwidth",
        str(_THROTTLE_BYTES),
        "--bytes",
    ]
    proc = subprocess.Popen(commit_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(2)
        assert not _blockjob_idle(shell, vm_name), "Foreign job must be active"

        # SIGKILL the client — the QEMU job keeps running.
        proc.kill()
        proc.wait(timeout=10)

        # ── Next pipeline run: step-0 recovery sees the active job ────
        recorded: list[list[str]] = []
        orig_run = shell.run
        orig_rwh = shell.run_with_heartbeat

        def _recording_run(cmd: list[str], timeout: int, check: bool = False):
            recorded.append(list(cmd))
            return orig_run(cmd, timeout=timeout, check=check)

        def _recording_rwh(cmd, timeout, heartbeat_seconds, on_heartbeat, check=False):
            recorded.append(list(cmd))
            return orig_rwh(
                cmd,
                timeout=timeout,
                heartbeat_seconds=heartbeat_seconds,
                on_heartbeat=on_heartbeat,
                check=check,
            )

        caplog.clear()
        with (
            patch.object(shell, "run", side_effect=_recording_run),
            patch.object(shell, "run_with_heartbeat", side_effect=_recording_rwh),
            caplog.at_level(logging.WARNING),
        ):
            core._execute_snapshot_steps(vm_config)

        # (a) Deferred with reason blockjob_active; intent kept.
        deferred = state.get_deferred_operations(vm_name)
        assert any(d.reason == "blockjob_active" for d in deferred), (
            f"Expected a blockjob_active deferred entry, got: {deferred}"
        )
        intents = state.get_commit_in_progress(vm_name)
        assert len(intents) == 1, f"Intent must be kept, got {intents}"
        assert intents[0].snapshots == [s.name for s in merge_set], intents[0].snapshots

        # (b) No competing commit started by qsnap.
        qsnap_commits = [c for c in recorded if "blockcommit" in c]
        assert qsnap_commits == [], (
            f"qsnap must not start a second commit while the job is active: {qsnap_commits}"
        )

        # (c) Recovery WARNING names the active-job deferral.
        assert any("active block job" in r.message for r in caplog.records), (
            f"Expected active-job WARNING, got: {[r.message for r in caplog.records]}"
        )

        # (d) No snapshot was created while the job was active.
        assert len(state.get_snapshots(vm_name)) == 9, (
            "Snapshot creation must be skipped while a block job is active"
        )
        assert _vm_is_running(shell, vm_name), "VM must keep running"

        # ── Wait for the background job to complete ───────────────────
        _wait_for_blockjob_idle(shell, vm_name, timeout=180)
        _wait_for_file_gone(merge_set[0].path, timeout=30)

        # ── Step-0 recovery converges late_success ────────────────────
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            core._recover_commit_intents(vm_config)

        assert any(
            "commit completed after previous run timed out" in r.message for r in caplog.records
        ), f"Expected late-success WARNING, got: {[r.message for r in caplog.records]}"

        remaining = {s.name for s in state.get_snapshots(vm_name)}
        assert remaining == {s.name for s in floor_set}, (
            f"Only the floor L=3 must remain, got {remaining}"
        )
        assert state.get_commit_in_progress(vm_name) == [], "Intent must be cleared"
        assert state.get_last_commit_ts(vm_name, "vda") is not None, (
            "last_commit_ts must be written for the late success"
        )
        for sn in merge_set:
            assert not sn.path.exists(), f"Merged overlay must be gone: {sn.path}"
        chain_len = _backing_chain_length(floor_set[-1].path, shell)
        assert chain_len == 4, f"Expected base + 3 floor overlays, got {chain_len}"
        assert _vm_is_running(shell, vm_name), "VM must keep running"
    finally:
        # Test-side safety: abort any still-running job so the fixture can
        # destroy/undefine the VM.  qsnap never issued this abort.
        shell.run(
            ["virsh", "blockjob", "--abort", "--domain", vm_name, "--path", "vda"],
            timeout=30,
        )
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ──────────────────────────────────────────────────────────────────────
# Test 2: injected scaled timeout → job_active deferral → late_success
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_bulk_timeout_then_late_success_real_chain(test_vm, caplog):
    """A scaled-timeout kill mid-job defers, then late-converges.

    1. Start VM; build Core with ``blockcommit_timeout=1`` so the bulk
       job's scaled budget is 1 × 6 = 6 s; create 9 snapshots.
    2. Write real data into s1 and restart the VM.
    3. Throttle the real ``virsh blockcommit`` (test-side ``--bandwidth``
       injection) so the job outlives the injected timeout: the shell
       kills the client while QEMU keeps running the job.
    4. The run classifies the outcome as ``unknown`` → reconciliation
       probes the still-active job → defers (reason ``blockjob_active``),
       intent kept.
    5. When the job completes, step-0 recovery converges ``late_success``
       without data loss.
    """
    shell: SubprocessShell = test_vm["shell"]
    vm_name: str = test_vm["vm_name"]
    base_image: Path = test_vm["base_image"]
    snapshot_dir: Path = test_vm["snapshot_dir"]
    target_dir: Path = test_vm["target_dir"]
    tmpdir: Path = test_vm["tmpdir"]
    state_dir = tmpdir / "state"

    _start_vm(shell, vm_name)
    core, vm_config, state = _build_core(
        shell,
        vm_name,
        base_image,
        snapshot_dir,
        target_dir,
        state_dir,
        blockcommit_timeout=1,  # scaled budget = 1 × 6 = 6 s
    )
    snaps = _create_snapshots(core, vm_config, 9)
    merge_set = snaps[:6]
    floor_set = snaps[6:]

    _write_data_into_overlay(shell, vm_name, merge_set[0].path, size="24M")

    # ── Throttled real commit with a tiny scaled timeout ──────────────
    recorded: list[list[str]] = []
    orig_rwh = shell.run_with_heartbeat

    def _throttled_rwh(cmd, timeout, heartbeat_seconds, on_heartbeat, check=False):
        if "blockcommit" in cmd:
            cmd = [*cmd, "--bandwidth", str(_THROTTLE_BYTES), "--bytes"]
            recorded.append(list(cmd))
        return orig_rwh(
            cmd,
            timeout=timeout,
            heartbeat_seconds=heartbeat_seconds,
            on_heartbeat=on_heartbeat,
            check=check,
        )

    caplog.clear()
    with (
        patch.object(shell, "run_with_heartbeat", side_effect=_throttled_rwh),
        caplog.at_level(logging.INFO),
    ):
        result = core.prune(vm_name)

    # (a) The prune itself completed (outcome unknown → deferred, not failed).
    assert result.results[0].success, f"prune must not fail: {result.results[0].error}"

    # (b) Exactly one (throttled) blockcommit was issued.
    assert len(recorded) == 1, f"Expected exactly one blockcommit, got {recorded}"
    assert "--bandwidth" in recorded[0], f"Throttle must be injected: {recorded[0]}"

    # (c) The run logged the unknown-outcome → job_active deferral.
    commit_lines = [r.message for r in caplog.records if "[blockcommit]" in r.message]
    assert any("collapsing 6 snapshot(s)" in m for m in commit_lines), (
        f"Expected the bulk intent line, got: {commit_lines}"
    )
    assert any("still active after timeout" in r.message for r in caplog.records), (
        f"Expected job_active deferral WARNING, got: {[r.message for r in caplog.records]}"
    )

    # (d) Intent kept; deferred entry with reason blockjob_active.
    intents = state.get_commit_in_progress(vm_name)
    assert len(intents) == 1, f"Intent must be kept, got {intents}"
    assert intents[0].snapshots == [s.name for s in merge_set], intents[0].snapshots
    deferred = state.get_deferred_operations(vm_name)
    assert any(d.reason == "blockjob_active" for d in deferred), (
        f"Expected a blockjob_active deferred entry, got: {deferred}"
    )
    assert _vm_is_running(shell, vm_name), "VM must keep running"

    # ── Wait for the QEMU job to finish the merge ────────────────────
    _wait_for_blockjob_idle(shell, vm_name, timeout=240)
    _wait_for_file_gone(merge_set[0].path, timeout=30)

    # ── Next-run step-0 recovery: late_success, no data loss ──────────
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        core._recover_commit_intents(vm_config)

    assert any(
        "commit completed after previous run timed out" in r.message for r in caplog.records
    ), f"Expected late-success WARNING, got: {[r.message for r in caplog.records]}"

    remaining = {s.name for s in state.get_snapshots(vm_name)}
    assert remaining == {s.name for s in floor_set}, (
        f"Only the floor L=3 must remain, got {remaining}"
    )
    assert state.get_commit_in_progress(vm_name) == [], "Intent must be cleared"
    assert state.get_last_commit_ts(vm_name, "vda") is not None, (
        "last_commit_ts must be written for the late success"
    )
    for sn in merge_set:
        assert not sn.path.exists(), f"Merged overlay must be gone: {sn.path}"
    chain_len = _backing_chain_length(floor_set[-1].path, shell)
    assert chain_len == 4, f"Expected base + 3 floor overlays, got {chain_len}"
    assert _vm_is_running(shell, vm_name), "VM must keep running"
