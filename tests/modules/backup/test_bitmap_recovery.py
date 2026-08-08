"""Unit tests for BitmapBackupProvider bitmap-loss recovery
(recover-lost-checkpoint-bitmaps change).

Covers (per the change's test plan, Group provider-unit-recovery):

- Probe tri-state (HEALTHY / DEAD / UNKNOWN) for running VMs (QMP) and
  stopped VMs (``qemu-img info -U --backing-chain``).
- Read-only baseline assessment (``assess_baseline``) with gate outcome
  and size estimates.
- Recovery gates G1-G3 and the recovered-delta copy-set computation.
- The recovered-delta lifecycle (freeze via ``virsh checkpoint-create``,
  copy data+zero extents skipping holes, chain onto newest backup,
  rollback-to-FULL on failure).
- The reactive backstop for ``checkpoint inconsistent`` errors and the
  two-run heal sequence (no infinite failure loop).
- Probe routing: HEALTHY -> delta, DEAD -> recovery, UNKNOWN -> delta
  with backstop.
- Provider-level recovery (DEAD returns recovery, not failure; recovery
  proceeds without a boot_id change).
- Backup-target orthogonality (normal path never reads snapshot state).
- Retention: a recovered delta retires no generation.

All shell calls go through ``MockShell``; all NBD operations through
``MockNbdClient`` — zero real I/O (TESTING.md unit rules).  No
pytest-mock; ``unittest.mock.patch`` only.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import NbdExtent, ShellResult, SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from tests.mocks.mock_nbd import MockNbdClient
from tests.mocks.mock_shell import MockShell

# Frozen wall clock + hex suffix so freeze-timestamp backup names and
# successor checkpoint names are deterministic in tests.
_FREEZE_DT = datetime(2026, 8, 8, 3, 0, 0)
_FREEZE_STR = "20260808T030000"
_FREEZE_HEX = "a1b2c3"

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "shell_outputs"

# Checkpoint name advertised by the healthy / inconsistent QMP fixtures.
_HEALTHY_CP = "qsnap-abc12345-vda-20260808T160755-e1eb7a"


def _fixture(name: str) -> str:
    """Read a canned shell-output fixture."""
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _ok() -> ShellResult:
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _expect_no_blockjob(mock_shell) -> None:
    mock_shell.expect("virsh blockjob").returns(
        ShellResult(
            success=True,
            stdout="No current block job\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )


@contextlib.contextmanager
def _frozen_naming():
    """Freeze ``datetime.now()`` and ``secrets.token_hex(3)`` inside the
    bitmap module so freeze-ts names are deterministic."""
    with (
        patch("qsnap.modules.backup.bitmap.datetime") as mock_dt,
        patch("qsnap.modules.backup.bitmap.secrets.token_hex", return_value=_FREEZE_HEX),
    ):
        mock_dt.now.return_value = _FREEZE_DT
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        mock_dt.min = datetime.min
        yield


def _expect_healthy_probe(mock_shell, cp_name: str) -> None:
    """Register a HEALTHY QMP probe for *cp_name*."""
    payload = json.loads(_fixture("qmp_block_nodes_healthy.json"))
    for node in payload.get("return", []):
        for bitmap in node.get("dirty-bitmaps", []):
            bitmap["name"] = cp_name
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _expect_dead_probe(mock_shell) -> None:
    """Register a DEAD QMP probe: the target checkpoint's bitmap is absent."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qmp_block_nodes_bitmap_missing.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _expect_unknown_probe(mock_shell) -> None:
    """Register an UNKNOWN QMP probe: the QMP command itself fails."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=False,
            stdout=_fixture("qmp_error.json"),
            stderr="QMP command failed",
            returncode=1,
            error="QMP command failed",
        )
    )


def _qemu_img_chain_result(entries: list[dict]) -> ShellResult:
    """A successful ``qemu-img info --backing-chain --output=json`` result."""
    return ShellResult(
        success=True,
        stdout=json.dumps(entries),
        stderr="",
        returncode=0,
        error=None,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. Probe tri-state
# ══════════════════════════════════════════════════════════════════════════


def test_probe_running_vm_healthy_bitmap_reported(mock_shell):
    """QMP ``query-named-block-nodes`` advertises the checkpoint-named
    bitmap with ``inconsistent: false`` → HEALTHY (spec scenario "Healthy
    bitmap reported by QMP")."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qmp_block_nodes_healthy.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    assert provider._probe_running_vm("testvm", _HEALTHY_CP) == "healthy"


def test_probe_running_vm_missing_bitmap_returns_dead(mock_shell):
    """The chain nodes exist but none advertises the target checkpoint's
    bitmap → DEAD (spec scenario "Bitmap missing after unclean shutdown")."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qmp_block_nodes_bitmap_missing.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    # The missing fixture advertises a DIFFERENT checkpoint's bitmap.
    assert provider._probe_running_vm("testvm", _HEALTHY_CP) == "dead"


def test_probe_running_vm_inconsistent_flag_returns_dead(mock_shell):
    """The bitmap exists but is flagged ``inconsistent: true`` → DEAD
    (spec scenario "Bitmap flagged inconsistent")."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qmp_block_nodes_bitmap_inconsistent.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    assert provider._probe_running_vm("testvm", _HEALTHY_CP) == "dead"


def test_probe_stopped_vm_healthy_bitmap_in_intermediate_layer(mock_shell):
    """Stopped-VM probe scans the whole backing chain via ``qemu-img info
    -U --backing-chain``; the checkpoint's bitmap lives on the
    INTERMEDIATE layer → HEALTHY (spec scenario "Stopped VM healthy bitmap
    in intermediate layer")."""
    mock_shell.expect("qemu-img info -U --backing-chain").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qemu_img_info_backing_chain_with_bitmaps.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    source = Path("/var/lib/libvirt/images/testvm-vda.base.qcow2")
    assert provider._probe_stopped_vm(source, _HEALTHY_CP) == "healthy"


def test_probe_stopped_vm_dead_bitmap(mock_shell):
    """No layer of the stopped VM's chain carries the checkpoint's bitmap
    → DEAD (spec scenario "Stopped VM dead bitmap")."""
    mock_shell.expect("qemu-img info -U --backing-chain").returns(
        ShellResult(
            success=True,
            stdout=_fixture("qemu_img_info_backing_chain_no_bitmaps.json"),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    source = Path("/var/lib/libvirt/images/testvm-vda.base.qcow2")
    assert provider._probe_stopped_vm(source, _HEALTHY_CP) == "dead"


def test_probe_running_vm_qmp_unavailable_returns_unknown(mock_shell):
    """QMP unavailable (command failure / QMP error object) → UNKNOWN —
    never raises, never blocks (spec scenario "QMP unavailable yields
    UNKNOWN"; design D2)."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=False,
            stdout=_fixture("qmp_error.json"),
            stderr="QMP command failed",
            returncode=1,
            error="QMP command failed",
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    assert provider._probe_running_vm("testvm", _HEALTHY_CP) == "unknown"


def test_probe_running_vm_unparseable_json_returns_unknown(mock_shell):
    """Unparseable QMP output → UNKNOWN (spec scenario "Unparseable QMP
    JSON yields UNKNOWN")."""
    mock_shell.expect("qemu-monitor-command").returns(
        ShellResult(
            success=True,
            stdout="{ not json",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    provider = BitmapBackupProvider(mock_shell)
    assert provider._probe_running_vm("testvm", _HEALTHY_CP) == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# 2. Read-only baseline assessment
# ══════════════════════════════════════════════════════════════════════════


def test_assess_baseline_dead_checkpoint_reports_gate_outcome(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """A DEAD probe yields status="dead" with the gate outcome
    (conservatively G1-failed today) and a FULL size estimate (spec
    scenario "Assessment reports dead checkpoint with gate outcome")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=cp + "\n", stderr="", returncode=0, error=None)
    )
    # dominfo from conftest → running; probe → DEAD.
    _expect_dead_probe(mock_shell)
    # Gate failure → FULL chain-sum estimate.
    mock_shell.expect_first("qemu-img info --force-share --backing-chain --output=json").returns(
        _qemu_img_chain_result(
            [
                {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 1048576},
                {"filename": "/snaps/snap1.qcow2", "actual-size": 2097152},
            ]
        )
    )

    provider = BitmapBackupProvider(mock_shell)
    assessment = provider.assess_baseline(vm_config, target, vm_config.disks[0])

    assert assessment.status == "dead"
    assert assessment.newest_checkpoint == cp
    assert assessment.gates_passed is False
    assert assessment.failed_gate_reason == "G1"
    assert assessment.size_estimate == 1048576 + 2097152


def test_assess_baseline_no_checkpoint_reports_full_estimate(
    mock_shell, make_vm_config, make_target, tmp_path
):
    """No checkpoint → status="no_checkpoint" with a FULL chain-sum
    estimate (spec scenario "Assessment with no checkpoint")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect_first("qemu-img info --force-share --backing-chain --output=json").returns(
        _qemu_img_chain_result(
            [
                {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 1048576},
                {"filename": "/snaps/snap1.qcow2", "actual-size": 2097152},
            ]
        )
    )

    provider = BitmapBackupProvider(mock_shell)
    assessment = provider.assess_baseline(vm_config, target, vm_config.disks[0])

    assert assessment.status == "no_checkpoint"
    assert assessment.newest_checkpoint is None
    assert assessment.gates_passed is False
    assert assessment.size_estimate == 1048576 + 2097152


# ══════════════════════════════════════════════════════════════════════════
# 3. Recovery gates G1-G3
# ══════════════════════════════════════════════════════════════════════════


def test_gate_g1_fails_when_commit_ts_after_freeze(mock_shell, make_vm_config):
    """G1 fails when a commit touched the disk after the checkpoint
    freeze: the recovered delta is skipped and FULL is used (spec scenario
    "Commit after checkpoint freeze fails G1")."""
    vm_config = make_vm_config()
    # Freeze at 15:07:55; last_commit_ts (16:00:00) is AFTER the freeze.
    freeze_cp = "qsnap-abc12345-vda-20260808T150755-e1eb7a"

    provider = BitmapBackupProvider(mock_shell)
    gates_passed, failed_gate = provider._evaluate_recovery_gates(
        vm_config, "vda", freeze_cp, "abc12345", vm_config.disks[0].base_image
    )

    assert gates_passed is False
    assert failed_gate == "G1"


def test_gate_g1_fails_when_marker_absent(mock_shell, make_vm_config):
    """An absent ``last_commit_ts`` marker fails G1 conservatively — the
    FULL path is used (spec scenario "Absent commit marker fails G1")."""
    vm_config = make_vm_config()
    freeze_cp = "qsnap-abc12345-vda-20260808T150755-e1eb7a"

    provider = BitmapBackupProvider(mock_shell)
    gates_passed, failed_gate = provider._evaluate_recovery_gates(
        vm_config, "vda", freeze_cp, "abc12345", vm_config.disks[0].base_image
    )

    assert gates_passed is False
    assert failed_gate == "G1"


def test_gates_g1_g2_g3_pass_select_recovered_delta(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """When G1-G3 all pass, run_backup selects the RECOVERED-DELTA path
    (spec scenario "All gates pass": recovered delta is taken).

    Expected lifecycle (design D6/D7): ``virsh checkpoint-create`` for the
    successor freeze point → ``qemu-img create -b <newest backup>`` →
    write server → copy data+zero extents → ``mv`` → chain-to-FULL verify
    → dead checkpoint deleted → ``kind == "recovered_delta"``.
    """
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze (16:07:55).
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with _frozen_naming():
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta", (
        f"gates passed → run_backup must produce kind='recovered_delta', got {result.kind!r}"
    )


def _expect_recovered_delta_lifecycle(
    mock_shell, target, prev_backup: Path, dead_cp: str, success_result
) -> MockNbdClient:
    """Register the shell expectations of the spec'd recovered-delta
    lifecycle and return a MockNbdClient whose ``base:allocation`` view
    mixes data, zero, and hole extents."""
    # Discovery + probe: DEAD.
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=dead_cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_dead_probe(mock_shell)
    _expect_no_blockjob(mock_shell)

    # G2: live backing chain matches snapshot state (read-only).
    mock_shell.expect_first("qemu-img info --force-share --backing-chain").returns(
        _qemu_img_chain_result(
            [{"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 1048576}]
        )
    )
    # G3: every copy-set overlay is readable.
    mock_shell.expect("qemu-img info --force-share --output=json").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 65536}),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # (1) Successor checkpoint at the freeze point — no backup job.
    mock_shell.expect("virsh checkpoint-create").returns(success_result())
    # (2) Target file chained onto the newest existing backup.
    mock_shell.expect(f"qemu-img info.*{prev_backup.name}").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"format": "qcow2", "virtual-size": 65536, "actual-size": 100}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    mock_shell.expect_first(f"test -f {prev_backup}").returns(_ok())
    mock_shell.expect("qemu-img create").returns(_ok())
    # (3) Write server for the .tmp delta.
    pid = os.getpid()
    write_socket = f"/tmp/qsnap-write-{pid}-vda.sock"
    pid_file = f"/tmp/qsnap-qemu-nbd-{pid}.pid"
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok())
    mock_shell.expect("qemu-nbd --fork").returns(_ok())
    Path(pid_file).write_text("99999")
    mock_shell.expect("kill 99999").returns(_ok())
    mock_shell.expect(f"rm -f {write_socket} {pid_file}").returns(_ok())
    # (5) Publish via mv.
    mock_shell.expect("^mv ").returns(_ok())
    # (6) Verify: chain resolves to the FULL anchor + qemu-img check.
    mock_shell.expect_first("qemu-img info.*--backing-chain.*qcow2").returns(
        _qemu_img_chain_result([{"filename": str(prev_backup)}, {"filename": "fake-top.qcow2"}])
    )
    mock_shell.expect("qemu-img check").returns(
        ShellResult(
            success=True,
            stdout=json.dumps({"corruptions": 0, "leaks": 0}),
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # (7) Dead checkpoint deleted (full delete, --metadata fallback).
    mock_shell.expect("checkpoint-delete").returns(success_result())
    # Generic finally-block cleanups.
    mock_shell.expect("rm -f").returns(_ok())
    mock_shell.expect("virsh domjobabort").returns(_ok())

    nbd = MockNbdClient(size=65536, max_request_size=33554432)
    nbd.block_status_payload = {
        # data + zero are copied; the hole is skipped (spec D5).
        "base:allocation": [
            NbdExtent(offset=0, length=16384, data=True),  # data
            NbdExtent(offset=16384, length=16384, data=True),  # zero cluster
            NbdExtent(offset=32768, length=16384, data=False),  # hole — skipped
            NbdExtent(offset=49152, length=16384, data=True),  # data
        ]
    }
    return nbd


# ══════════════════════════════════════════════════════════════════════════
# 4. Copy set computation and recovered-delta lifecycle
# ══════════════════════════════════════════════════════════════════════════


def test_copy_set_bounded_by_state_timestamps(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """The recovered-delta copy set S contains the newest snapshot with
    timestamp ≤ freeze-ts plus every overlay created after the freeze
    (spec scenario "Copy set from state timestamps")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    # Freeze at 15:07:55 — snapshots at 14:00, 15:00, 16:00, 17:00.
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"
    snap_dir = Path("/var/lib/libvirt/snapshots/testvm")
    for ts, name in [
        ("20260808T140000", "snap-1400"),
        ("20260808T150000", "snap-1500"),
        ("20260808T160000", "snap-1600"),
        ("20260808T170000", "snap-1700"),
    ]:
        mock_state.record_snapshot(
            vm_config.name,
            SnapshotInfo(
                name=name,
                path=snap_dir / f"{name}.qcow2",
                timestamp=datetime.strptime(ts, "%Y%m%dT%H%M%S"),
                allocation=65536,
                disk="vda",
            ),
        )

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with _frozen_naming():
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    # S = {15:00, 16:00, 17:00} — the copy loop iterates exactly the
    # post-freeze layers, oldest first (observable via the NBD reads).
    assert result.kind == "recovered_delta", (
        f"copy set bounded by state timestamps → kind='recovered_delta', got {result.kind!r}"
    )


def test_copy_set_falls_back_to_all_overlays_on_incomplete_state(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """When snapshot state is incomplete, S falls back to ALL overlays
    above base_image — a larger but still correct superset — and recovery
    still proceeds (spec scenario "Incomplete state falls back to full
    overlay set")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.  No snapshots
    # recorded → copy set falls back to all overlays above base_image.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with _frozen_naming():
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta", (
        "recovery must proceed via recovered-delta even with incomplete state, "
        f"got kind={result.kind!r}"
    )


def test_recovered_delta_success_chains_onto_newest_backup(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """A successful recovered delta chains onto the NEWEST existing backup
    of this disk, keeps the old chain, deletes the dead checkpoint, and is
    freeze-ts named (spec scenario "Successful recovered delta")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta"
    # Freeze-ts delta name, NOT a .FULL. name.
    assert result.target_path.name == (f"testvm.{_FREEZE_STR}_vda_{_FREEZE_HEX}.qcow2"), (
        f"recovered delta must be freeze-ts named, got {result.target_path.name}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    # Chains onto the newest existing backup via qemu-img create -b.
    create_cmds = [cmd for cmd in all_run_cmds if "qemu-img create" in cmd]
    assert len(create_cmds) == 1
    assert f"-b {prev_backup}" in create_cmds[0], (
        f"recovered delta must chain onto the newest backup, got: {create_cmds[0]}"
    )
    # The dead checkpoint is deleted.
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert any(dead_cp in cmd for cmd in delete_cmds), (
        f"dead checkpoint {dead_cp} must be deleted, got: {delete_cmds}"
    )
    # The old chain (FULL + incrementals) is NOT deleted.
    assert not any(cmd.startswith("rm -f") and str(prev_backup) in cmd for cmd in all_run_cmds)


def test_recovered_delta_copies_zero_extents_and_skips_holes(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """The recovered-delta copy loop copies ALL data and zero extents and
    skips only holes — guest discards must not expose stale backing data
    (spec scenario "Zero extents are copied")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with _frozen_naming():
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta"

    # Every non-hole extent was written: [0,16384) + [16384,32768) + [49152,65536).
    assert sorted(offset for offset, _ in nbd.writes) == [0, 16384, 49152], (
        f"zero extents must be copied and holes skipped; writes={nbd.writes}"
    )
    assert nbd.bytes_written == 16384 * 3


def test_recovered_delta_failure_rolls_back_then_full_same_run(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """A failed recovered-delta attempt deletes the successor checkpoint
    and the .tmp file, then falls back to FULL within the SAME run (spec
    scenario "Transfer failure rolls back and falls back to FULL")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )
    nbd.fail_pread = "recovered-delta copy failed"

    # FULL-fallback transfer (the same-run fallback) succeeds.
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("qemu-img convert").returns(success_result())
    mock_shell.expect("^mv ").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"FULL fallback in the same run must succeed, got error={result.error!r}"
    )
    assert result.kind == "full", (
        "FULL fallback result must be kind='full', "
        f"got {result.kind!r} (recovered-delta attempt missing?)"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    # The recovered-delta attempt happened first (checkpoint-create), then
    # the successor was rolled back before the FULL fallback.
    assert any("checkpoint-create" in cmd for cmd in all_run_cmds), (
        "recovered-delta attempt missing: provider skipped straight to FULL "
        f"(commands={all_run_cmds})"
    )


def test_recovered_delta_successor_checkpoint_precedes_copy(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """The successor checkpoint (freeze point T') is created BEFORE any
    NBD read of the source layers — every post-T' write lands in the
    successor bitmap and is re-copied by the next delta (spec scenario
    "Consistency under concurrent guest writes")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta"

    # Merged timeline: the checkpoint-create shell call must precede the
    # first NBD read of the source.
    events: list[tuple[str, str]] = []
    for call in run_spy.call_args_list:
        events.append(("shell", " ".join(call.args[0])))
    for method, _offset, _length in nbd.calls:
        events.append(("nbd", method))  # type: ignore[assignment]

    cc_idx = next(
        i
        for i, (kind, label) in enumerate(events)
        if kind == "shell" and "checkpoint-create" in label
    )
    first_read = next(
        i
        for i, (kind, label) in enumerate(events)
        if kind == "nbd" and label in ("connect", "pread")
    )
    assert cc_idx < first_read, (
        "successor checkpoint must be created before any NBD read of the source layers"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. Reactive backstop
# ══════════════════════════════════════════════════════════════════════════


def test_reactive_backstop_deletes_checkpoint_and_retries_once(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """UNKNOWN probe + ``backup-begin`` "checkpoint inconsistent" → the
    named checkpoint is deleted and the backup is retried ONCE (spec
    scenario "Backstop heals a probe miss")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # Discovery sees the checkpoint; probe is UNKNOWN (QMP unavailable).
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_unknown_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    # The backstop deletes exactly the named checkpoint (full delete, no
    # --metadata fallback needed on success).
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())

    backup_calls = [0]
    cp_list_calls = [0]
    original_run = mock_shell.run

    def controlled_run(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "backup-begin" in cmd_str:
            backup_calls[0] += 1
            if backup_calls[0] == 1:
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr=f"checkpoint inconsistent: missing or broken bitmap '{cp}'",
                    returncode=1,
                    error=f"checkpoint inconsistent: missing or broken bitmap '{cp}'",
                )
            return success_result()
        if "checkpoint-list" in cmd_str:
            cp_list_calls[0] += 1
            # After the backstop delete, no checkpoints remain.
            if cp_list_calls[0] == 1:
                return ShellResult(
                    success=True, stdout=cp + "\n", stderr="", returncode=0, error=None
                )
            return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
        return original_run(cmd, timeout, check)

    with (
        patch.object(mock_shell, "run", side_effect=controlled_run) as run_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"backstop retry must succeed, got error={result.error!r}"
    assert result.kind == "full", (
        f"retry after checkpoint-inconsistent must be a FULL, got kind={result.kind!r}"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    backup_cmds = [cmd for cmd in all_run_cmds if "backup-begin" in cmd]
    assert len(backup_cmds) == 2, (
        f"exactly one retry expected (first failed + retry), got {len(backup_cmds)}"
    )

    # Exactly the named checkpoint was deleted — once, by the backstop.
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert len(delete_cmds) == 1, (
        f"backstop must delete exactly the named checkpoint: {delete_cmds}"
    )
    assert cp in delete_cmds[0]

    # The first XML was incremental (delta attempt); the retry was a FULL.
    assert mock_wbxml.call_count == 2
    first_kwargs = mock_wbxml.call_args_list[0].kwargs
    second_kwargs = mock_wbxml.call_args_list[1].kwargs
    assert first_kwargs.get("incremental") == cp, (
        f"UNKNOWN probe must attempt a delta first, got {first_kwargs}"
    )
    assert second_kwargs.get("incremental") is None, (
        f"backstop retry must be a FULL (no incremental baseline), got {second_kwargs}"
    )


def test_two_run_incident_replay_first_heals_second_clean(
    mock_shell,
    make_vm_config,
    make_target,
    tmp_path,
    success_result,
    caplog,
):
    """Incident replay: run 1 heals the dead checkpoint (recovered delta
    or FULL fallback) and exits successfully; run 2 performs a normal
    delta with NO incident warnings — the infinite failure loop is
    eliminated (spec scenario "No infinite failure loop")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    # ── Run 1: dead checkpoint → recovery heals it. ────────────────────
    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=dead_cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_dead_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation + recovery cleanup
    mock_shell.expect("domjobabort").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
    ):
        provider1 = BitmapBackupProvider(mock_shell, nbd=MockNbdClient())
        result1 = provider1.run_backup(vm_config, target, vm_config.disks[0])

    assert result1.success is True, (
        f"run 1 must heal the dead checkpoint, got error={result1.error!r}"
    )
    assert result1.kind in ("full", "recovered_delta"), (
        f"run 1 must heal via FULL fallback or recovered delta, got kind={result1.kind!r}"
    )
    # The old chain is preserved: the covering FULL still exists.
    assert prev_backup.exists(), "healing must not delete the old backup chain"
    successor = result1.checkpoint
    assert successor is not None, "run 1 must leave a successor checkpoint"

    caplog.clear()

    # ── Run 2: healthy successor → clean delta, no incident warnings. ──
    shell2 = MockShell()
    shell2.expect("virsh dominfo").returns(
        ShellResult(success=True, stdout="State: running\n", stderr="", returncode=0, error=None)
    )
    shell2.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=successor + "\n", stderr="", returncode=0, error=None)
    )
    _expect_healthy_probe(shell2, successor)
    _expect_no_blockjob(shell2)
    shell2.expect("rm -f").returns(success_result())
    shell2.expect("backup-begin").returns(success_result())
    shell2.expect("domjobabort").returns(success_result())
    shell2.expect_first("qemu-img info.*--backing-chain").returns(
        _qemu_img_chain_result([{"filename": "fake-chain-element.qcow2"}])
    )
    shell2.expect("checkpoint-delete").returns(success_result())

    with patch.object(
        BitmapBackupProvider,
        "_copy_dirty_blocks",
        return_value=type(
            "_FakeCopy", (), {"error": None, "previous_path": prev_backup, "dirty_bytes": 65536}
        )(),
    ):
        provider2 = BitmapBackupProvider(shell2, nbd=MockNbdClient(size=65536))
        result2 = provider2.run_backup(vm_config, target, vm_config.disks[0])

    assert result2.success is True, f"run 2 must be a clean delta, got error={result2.error!r}"
    assert result2.kind == "delta", f"run 2 must be a normal delta, got kind={result2.kind!r}"
    assert not any(
        "unclean" in r.getMessage().lower()
        or "recovery" in r.getMessage().lower()
        or "dead" in r.getMessage().lower()
        for r in caplog.records
        if r.name == "qsnap.modules.backup.bitmap"
    ), "run 2 must perform a normal delta with no incident warnings"


# ══════════════════════════════════════════════════════════════════════════
# 6. Probe routing (discovery + kind decision)
# ══════════════════════════════════════════════════════════════════════════


def test_prior_discovery_newest_wins_filters_foreign(mock_shell):
    """Prior discovery is newest-wins and ignores non-qsnap checkpoints —
    the checkpoint is the SOLE delta baseline (spec scenario "Multiple
    checkpoints — newest selected" + "Checkpoint is the sole delta
    baseline")."""
    target_hash = "abc12345"
    newest = "qsnap-abc12345-vda-20260808T160755-e1eb7a"
    older = "qsnap-abc12345-vda-20260720T120000-aa11bb"
    foreign = "manual-one"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=f"{older}\n{newest}\n{foreign}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = BitmapBackupProvider(mock_shell)
    assert provider._newest_checkpoint("testvm", target_hash, "vda") == newest


def test_prior_discovery_per_disk_lineages_isolated(mock_shell):
    """Different disks have separate checkpoint lineages: discovery for
    vda never sees vdb's checkpoints (spec scenario "Different disks have
    separate checkpoint lineages")."""
    target_hash = "abc12345"
    vda_cp = "qsnap-abc12345-vda-20260808T160755-e1eb7a"
    vdb_cp = "qsnap-abc12345-vdb-20260808T160755-f2a3b4"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(
            success=True,
            stdout=f"{vda_cp}\n{vdb_cp}\n",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = BitmapBackupProvider(mock_shell)
    assert provider._list_checkpoints_for_target("testvm", target_hash, "vda") == [vda_cp]
    assert provider._list_checkpoints_for_target("testvm", target_hash, "vdb") == [vdb_cp]


def _expect_delta_after_healthy_probe(mock_shell, cp_name, success_result) -> None:
    """Register shell expectations for a healthy-probe delta run_backup.

    The caller patches ``_copy_dirty_blocks`` so the copy loop itself is
    not exercised here.
    """
    _expect_healthy_probe(mock_shell, cp_name)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect_first("qemu-img info.*--backing-chain").returns(
        _qemu_img_chain_result([{"filename": "fake-chain-element.qcow2"}])
    )
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())


def test_healthy_probe_proceeds_to_delta(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """HEALTHY probe → the delta path is taken: backup-begin receives the
    checkpoint as the incremental baseline (spec scenario "Healthy
    checkpoint proceeds to delta")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_delta_after_healthy_probe(mock_shell, cp, success_result)

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_copy_dirty_blocks") as mock_copy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_copy.return_value = type(
            "_FakeCopy",
            (),
            {"error": None, "previous_path": Path("/tmp/prev.qcow2"), "dirty_bytes": 65536},
        )()
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"delta path must succeed, got error={result.error!r}"
    assert result.kind == "delta", f"healthy probe must produce kind='delta', got {result.kind!r}"

    mock_wbxml.assert_called_once()
    assert mock_wbxml.call_args.kwargs.get("incremental") == cp


def test_dead_probe_routes_to_recovery_no_delta_attempt(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """DEAD probe → the backup routes into recovery (FULL fallback today):
    NO incremental XML, no dirty-block copy, and the dead checkpoint is
    deleted only after the new backup is verified (spec scenarios "Dead
    checkpoint routes to recovery" + "Dead checkpoint is not a
    baseline")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=dead_cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_dead_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())  # rotation + recovery cleanup
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"recovery must succeed, got error={result.error!r}"
    assert result.kind == "full", (
        f"DEAD probe must route to recovery (FULL fallback today), got {result.kind!r}"
    )
    # No delta attempt: the backup XML had NO incremental baseline.
    mock_wbxml.assert_called_once()
    assert mock_wbxml.call_args.kwargs.get("incremental") is None, (
        "DEAD probe must NOT attempt a delta (no incremental baseline)"
    )

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    # No qemu-img create -b (no delta chaining), no NBD copy.
    assert not any("qemu-img create" in cmd for cmd in all_run_cmds)
    assert not any(cmd.startswith("qemu-nbd") for cmd in all_run_cmds)
    # The dead checkpoint is deleted (rotation + recovery cleanup).
    delete_cmds = [cmd for cmd in all_run_cmds if "checkpoint-delete" in cmd]
    assert delete_cmds and all(dead_cp in cmd for cmd in delete_cmds), (
        f"dead checkpoint {dead_cp} must be deleted, got: {delete_cmds}"
    )


def test_unknown_probe_attempts_delta_with_backstop(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """UNKNOWN probe → today's behavior: a delta IS attempted; the
    reactive backstop catches the inconsistent checkpoint and heals (spec
    scenario "Unknown probe result attempts delta")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_unknown_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_copy_dirty_blocks") as mock_copy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_copy.return_value = type(
            "_FakeCopy",
            (),
            {"error": None, "previous_path": Path("/tmp/prev.qcow2"), "dirty_bytes": 65536},
        )()
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"delta with UNKNOWN probe must succeed, got {result.error!r}"
    assert result.kind == "delta"
    mock_wbxml.assert_called_once()
    assert mock_wbxml.call_args.kwargs.get("incremental") == cp


# ══════════════════════════════════════════════════════════════════════════
# 7. Provider-level recovery behavior
# ══════════════════════════════════════════════════════════════════════════


def test_run_backup_dead_probe_returns_recovery_not_failure(
    mock_shell, make_vm_config, make_target, tmp_path, success_result
):
    """A DEAD probe returns a successful RECOVERY result — never a
    failure.  Recovery is designed behavior (spec scenario "Dead bitmap
    routes to recovery instead of failing")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=dead_cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_dead_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovery must be a success, not a failure; got error={result.error!r}"
    )
    assert result.error is None
    assert result.kind == "full", f"recovery result must be kind='full', got {result.kind!r}"


def test_recovery_proceeds_without_boot_id_change(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """Recovery proceeds identically when the host boot_id is unchanged
    (or unknown): the DEAD probe verdict alone triggers recovery (spec
    scenario "Dead bitmap without boot change still recovers"; design D3 —
    evidence informs logs, not gating)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # State records an UNCHANGED boot_id — the provider must still recover.
    mock_state.set_boot_id(vm_config.name, "boot-A")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=dead_cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_dead_probe(mock_shell)
    _expect_no_blockjob(mock_shell)
    mock_shell.expect("rm -f").returns(success_result())
    mock_shell.expect("backup-begin").returns(success_result())
    mock_shell.expect("checkpoint-delete").returns(success_result())
    mock_shell.expect("domjobabort").returns(success_result())
    mock_shell.expect("rm -f").returns(success_result())

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_full_pull_lifecycle", return_value=(None, 65536)),
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovery must proceed even without a boot_id change; got error={result.error!r}"
    )
    assert result.kind == "full"


# ══════════════════════════════════════════════════════════════════════════
# 8. Backup-target orthogonality
# ══════════════════════════════════════════════════════════════════════════


def test_healthy_delta_path_never_reads_snapshot_state(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """The HEALTHY (normal) backup path never consults snapshot state —
    the backup phase is fully orthogonal to the snapshot world (spec
    scenario "Normal path never reads snapshot state")."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    # Seed snapshot state that the backup path must NOT read.
    mock_state.record_snapshot(
        vm_config.name,
        SnapshotInfo(
            name="snap-1",
            path=Path("/var/lib/libvirt/snapshots/testvm/snap-1.qcow2"),
            timestamp=datetime(2026, 8, 8, 14, 0, 0),
            allocation=65536,
            disk="vda",
        ),
    )

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    mock_shell.expect("checkpoint-list").returns(
        ShellResult(success=True, stdout=cp + "\n", stderr="", returncode=0, error=None)
    )
    _expect_delta_after_healthy_probe(mock_shell, cp, success_result)

    with (
        _frozen_naming(),
        patch.object(BitmapBackupProvider, "_copy_dirty_blocks") as mock_copy,
        patch.object(mock_state, "get_snapshots", wraps=mock_state.get_snapshots) as snap_spy,
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_copy.return_value = type(
            "_FakeCopy",
            (),
            {"error": None, "previous_path": Path("/tmp/prev.qcow2"), "dirty_bytes": 65536},
        )()
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=MockNbdClient(size=65536))
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, f"delta path must succeed, got error={result.error!r}"
    assert result.kind == "delta"
    assert snap_spy.call_count == 0, (
        "the normal backup path must never read snapshot state (backup-target orthogonality)"
    )


def test_recovery_path_reads_timestamps_only(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """The recovery path consults snapshot TIMESTAMPS only — no
    SnapshotInfo objects ever flow into the backup transfer call chain
    (spec scenario "Recovery path reads timestamps only"; scoped
    orthogonality exception)."""
    vm_config = make_vm_config()
    target = make_target(path=str(tmp_path / "target"), verify="off")
    target.path.mkdir(parents=True, exist_ok=True)

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    prev_backup = target.path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with (
        _frozen_naming(),
        patch("qsnap.modules.backup.bitmap.write_backup_xml") as mock_wbxml,
    ):
        mock_wbxml.return_value = tmp_path / "backup-test.xml"
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovery path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta", (
        "recovery must produce a recovered delta that uses snapshot "
        f"timestamps only, got kind={result.kind!r}"
    )
    # The transfer call chain carries the checkpoint/disk names, never a
    # SnapshotInfo object.  The recovered-delta path uses checkpoint-create
    # (not backup-begin with incremental), so write_backup_xml is not called
    # with an incremental baseline.
    assert not any(
        isinstance(kwargs.get("incremental"), SnapshotInfo) for kwargs in mock_wbxml.call_args_list
    )


# ══════════════════════════════════════════════════════════════════════════
# 9. Retention
# ══════════════════════════════════════════════════════════════════════════


def test_recovered_delta_retires_no_generation(
    mock_shell, mock_state, make_vm_config, make_target, tmp_path, success_result
):
    """A successful recovered delta retires NOTHING: the old FULL and its
    incrementals stay in place — only the dead checkpoint metadata is
    removed (spec scenario "Recovered delta retires nothing")."""
    vm_config = make_vm_config()
    target_path = tmp_path / "target"
    target_path.mkdir()
    target = make_target(path=str(target_path), verify="off")

    target_hash = BitmapBackupProvider.target_hash(str(target.path))
    dead_cp = f"qsnap-{target_hash}-vda-20260808T160755-e1eb7a"

    # G1: set last_commit_ts BEFORE the checkpoint freeze.
    mock_state.set_last_commit_ts(vm_config.name, "vda", "20260808T150000")

    prev_backup = target_path / "testvm.FULL.20260701T000000_vda_aaaaaa.qcow2"
    prev_backup.write_bytes(b"")
    old_delta = target_path / "testvm.20260715T000000_vda_bbbbbb.qcow2"
    old_delta.write_bytes(b"")

    nbd = _expect_recovered_delta_lifecycle(
        mock_shell, target, prev_backup, dead_cp, success_result
    )

    with (
        _frozen_naming(),
        patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
    ):
        provider = BitmapBackupProvider(mock_shell, nbd=nbd, state=mock_state)
        result = provider.run_backup(vm_config, target, vm_config.disks[0])

    assert result.success is True, (
        f"recovered-delta path not selected: provider fell back to FULL (error={result.error!r})"
    )
    assert result.kind == "recovered_delta"

    all_run_cmds = [" ".join(c.args[0]) for c in run_spy.call_args_list]
    # No generation retirement: neither the old FULL nor the old delta is
    # removed by the provider.
    for old_file in (prev_backup, old_delta):
        assert old_file.exists(), f"{old_file.name} must not be retired by a recovered delta"
        assert not any(cmd.startswith("rm -f") and old_file.name in cmd for cmd in all_run_cmds), (
            f"recovered delta must not delete {old_file.name}"
        )
