"""Unit tests for ExternalSnapshotProvider.

Tests cover the three ``ISnapshotProvider`` methods (``create``, ``list``,
``delete``) using ``MockShell`` to simulate ``virsh``/``qemu-img``/``chmod``/
``rm`` commands.  No real I/O occurs — all shell calls are intercepted by
``MockShell``.

Design decisions verified:
- **D1**: ``ExternalSnapshotProvider`` does NOT inherit from ``Core``; its
  only dependency is ``IShell``.
- **R4**: Disk is hardcoded as ``"vda"`` (passed by Core, not by the module).
- All fallible operations return ``Result`` types, never raise exceptions.
- The constructor accepts only ``IShell`` (no ``Core``, no ``IStateManager``).

Scenarios (from ``specs/snapshot-provider/spec.md``):
1. Successful snapshot creation — virsh + chmod + qemu-img all succeed.
2. virsh command fails — non-zero exit, short-circuits before chmod/qemu-img.
3. virsh command times out — simulated via ShellResult with "timed out".
4. Backing chain with snapshots — 3-element chain yields 2 SnapshotInfo.
5. No snapshots (fresh VM) — 1-element chain yields empty list.
6. Successful file deletion — rm -f returns success.
7. File not found — rm -f is idempotent, still returns success.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from qsnap.models.results import ShellResult, SnapshotInfo
from qsnap.modules.snapshot.external import ExternalSnapshotProvider

# ──────────────────────────────────────────────────────────────────────────
# 1. Successful snapshot creation
# ──────────────────────────────────────────────────────────────────────────


def test_create_snapshot_success(mock_shell, make_vm_config):
    """When virsh, chmod, and qemu-img all succeed, ``create()`` returns
    ``SnapshotResult(success=True)`` with ``new_allocation`` from the
    ``actual-size`` field of ``qemu-img info`` JSON output.

    Also verifies:
    - The virsh command contains ``vda`` (hardcoded disk, design R4).
    - The virsh command contains ``--disk-only --atomic --no-metadata``.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    # Step 1: virsh snapshot-create-as succeeds
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Step 2: chmod succeeds
    mock_shell.expect("chmod").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Step 3: qemu-img info returns JSON with actual-size
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Spy on shell.run to capture the actual commands passed to the shell
    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=False,  # explicit — verify backward-compatible signature
        )

    # Assert successful result with correct allocation from qemu-img info
    assert result.success is True
    assert result.new_allocation == 1048576
    assert result.error is None
    assert result.name == "snap.20250101T000000"
    assert result.path == snapshot_path

    # Assert the virsh command contains "vda" and the required flags
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    virsh_cmd = next(cmd for cmd in all_cmds if "snapshot-create-as" in cmd)
    assert "vda" in virsh_cmd
    assert "--disk-only" in virsh_cmd
    assert "--atomic" in virsh_cmd
    assert "--no-metadata" in virsh_cmd
    # quiesce=False → no --quiesce flag
    assert "--quiesce" not in virsh_cmd

    # Assert qemu-img info includes --force-share (design D5)
    qemu_cmd = next(cmd for cmd in all_cmds if "qemu-img info" in cmd)
    assert "--force-share" in qemu_cmd


# ──────────────────────────────────────────────────────────────────────────
# 2. virsh command fails
# ──────────────────────────────────────────────────────────────────────────


def test_create_snapshot_virsh_fails(mock_shell, make_vm_config):
    """When ``virsh snapshot-create-as`` returns a non-zero exit code,
    ``create()`` returns ``SnapshotResult(success=False, error=<stderr>)``
    and short-circuits — ``chmod`` and ``qemu-img info`` are NOT called.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    stderr_msg = "error: internal error: snapshot creation failed"
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=stderr_msg,
            returncode=1,
            error=stderr_msg,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    # Assert failure with stderr propagated as the error
    assert result.success is False
    assert result.error == stderr_msg
    assert result.new_allocation == 0

    # Assert short-circuit: only virsh was called; chmod and qemu-img were NOT
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    assert len(all_cmds) == 1, (
        f"Only the virsh command should have been called, but got {len(all_cmds)} calls"
    )
    assert "snapshot-create-as" in all_cmds[0]
    assert not any("chmod" in cmd for cmd in all_cmds), (
        "chmod should NOT be called when virsh fails"
    )
    assert not any("qemu-img" in cmd for cmd in all_cmds), (
        "qemu-img info should NOT be called when virsh fails"
    )


# ──────────────────────────────────────────────────────────────────────────
# 3. virsh command times out
# ──────────────────────────────────────────────────────────────────────────


def test_create_snapshot_timeout(mock_shell, make_vm_config):
    """When ``virsh snapshot-create-as`` exceeds the timeout (120 seconds),
    ``create()`` returns ``SnapshotResult(success=False)`` with an error
    containing "timed out".

    ``SubprocessShell`` catches ``subprocess.TimeoutExpired`` and returns a
    ``ShellResult`` with ``success=False`` and an error message containing
    "timed out".  Since ``MockShell``'s ``.raises()`` would actually raise
    the exception (bypassing the module's result-object contract), we
    simulate the timeout by configuring ``MockShell`` to return the same
    ``ShellResult`` that ``SubprocessShell`` would produce.

    Also verifies the default (non-quiesce) timeout is 120 seconds.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Command timed out after 120s",
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert result.new_allocation == 0

    # Verify 120s default timeout (non-quiesce path)
    virsh_calls = [
        call_obj
        for call_obj in shell_spy.call_args_list
        if "snapshot-create-as" in " ".join(call_obj.args[0])
    ]
    assert len(virsh_calls) == 1
    assert virsh_calls[0].kwargs["timeout"] == 120


# ──────────────────────────────────────────────────────────────────────────
# 3a. Quiesce support — --quiesce flag and 180s timeout
# ──────────────────────────────────────────────────────────────────────────


def _expect_successful_create(mock_shell):
    """Configure MockShell expectations for a successful create() pipeline.

    Sets up virsh snapshot-create-as, chmod, and qemu-img info to all
    succeed.  The qemu-img info JSON carries ``actual-size: 1048576``.
    """
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    mock_shell.expect("chmod").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )


def _virsh_create_calls(shell_spy):
    """Extract all virsh snapshot-create-as calls recorded by *shell_spy*."""
    return [
        call_obj
        for call_obj in shell_spy.call_args_list
        if "snapshot-create-as" in " ".join(call_obj.args[0])
    ]


def test_create_snapshot_with_quiesce_enabled(mock_shell, make_vm_config):
    """When ``quiesce=True``, the virsh command contains ``--quiesce``."""
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is True
    virsh_cmds = [" ".join(c.args[0]) for c in _virsh_create_calls(shell_spy)]
    assert len(virsh_cmds) == 1
    assert "--quiesce" in virsh_cmds[0]

    # Verify --force-share on qemu-img info (design D5)
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    qemu_cmd = next(cmd for cmd in all_cmds if "qemu-img info" in cmd)
    assert "--force-share" in qemu_cmd


def test_create_snapshot_without_quiesce_default(mock_shell, make_vm_config):
    """When ``quiesce`` is not passed (defaults to ``False``), the virsh
    command does NOT contain ``--quiesce``.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            # quiesce not passed — defaults to False
        )

    assert result.success is True
    virsh_cmds = [" ".join(c.args[0]) for c in _virsh_create_calls(shell_spy)]
    assert len(virsh_cmds) == 1
    assert "--quiesce" not in virsh_cmds[0]


def test_create_snapshot_quiesce_enabled(mock_shell, make_vm_config):
    """When ``quiesce=True``, the timeout passed to ``shell.run()`` for the
    virsh command is 180 seconds.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is True
    virsh_calls = _virsh_create_calls(shell_spy)
    assert len(virsh_calls) == 1
    assert virsh_calls[0].kwargs["timeout"] == 180


def test_create_snapshot_quiesce_disabled_default(mock_shell, make_vm_config):
    """When ``quiesce`` is not passed (defaults to ``False``), the timeout
    passed to ``shell.run()`` for the virsh command is 120 seconds.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            # quiesce not passed — defaults to False
        )

    assert result.success is True
    virsh_calls = _virsh_create_calls(shell_spy)
    assert len(virsh_calls) == 1
    assert virsh_calls[0].kwargs["timeout"] == 120


def test_create_snapshot_quiesce_guest_agent_not_installed(mock_shell, make_vm_config):
    """When virsh returns a non-zero exit with a guest-agent error (because
    qemu-guest-agent is not installed), ``create()`` returns
    ``SnapshotResult(success=False)`` and does NOT silently fall back to a
    non-quiesce snapshot.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    stderr_msg = (
        "error: internal error: unable to execute guest agent: qemu-guest-agent is not running"
    )
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=stderr_msg,
            returncode=1,
            error=stderr_msg,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is False
    assert result.error == stderr_msg
    assert result.new_allocation == 0

    # No silent fallback: exactly ONE virsh call (with --quiesce), no retry
    virsh_cmds = [" ".join(c.args[0]) for c in _virsh_create_calls(shell_spy)]
    assert len(virsh_cmds) == 1, (
        f"Should not retry/fallback — exactly one virsh call expected, but got {len(virsh_cmds)}"
    )
    assert "--quiesce" in virsh_cmds[0]


def test_create_snapshot_quiesce_timeout_180s(mock_shell, make_vm_config):
    """When ``quiesce=True``, the timeout passed to ``shell.run()`` for the
    virsh command is exactly 180 seconds (not 120).
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is True
    virsh_calls = _virsh_create_calls(shell_spy)
    assert len(virsh_calls) == 1
    assert virsh_calls[0].kwargs["timeout"] == 180


# ──────────────────────────────────────────────────────────────────────────
# 3b. Quiesce risk tests — timeout and fallback safety
# ──────────────────────────────────────────────────────────────────────────


def test_risk_quiesce_timeout_180s_not_120s(mock_shell, make_vm_config):
    """Risk test: when ``quiesce=True``, the virsh timeout MUST be 180s,
    NOT 120s.  A 120s timeout for a quiesced snapshot is a bug — the
    guest agent freeze/thaw cycle needs extra time.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")
    _expect_successful_create(mock_shell)

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is True
    virsh_calls = _virsh_create_calls(shell_spy)
    assert len(virsh_calls) == 1
    timeout = virsh_calls[0].kwargs["timeout"]
    assert timeout == 180, f"Expected 180s timeout for quiesce, got {timeout}"
    assert timeout != 120, "120s timeout for quiesce path is a bug"


def test_risk_quiesce_agent_timeout_returns_failure(mock_shell, make_vm_config):
    """Risk test: when the quiesced virsh command times out, ``create()``
    returns ``SnapshotResult(success=False)`` with an error containing
    "timed out".
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Command timed out after 180s",
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert result.new_allocation == 0

    # Verify the timeout was 180 (quiesce path)
    virsh_calls = _virsh_create_calls(shell_spy)
    assert len(virsh_calls) == 1
    assert virsh_calls[0].kwargs["timeout"] == 180


def test_risk_quiesce_no_silent_fallback(mock_shell, make_vm_config):
    """Risk test: when the quiesced snapshot fails due to a guest-agent
    error, ``create()`` must return the error — it must NOT silently
    retry without ``--quiesce``.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    stderr_msg = (
        "error: internal error: unable to execute guest agent: qemu-guest-agent is not responding"
    )
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr=stderr_msg,
            returncode=1,
            error=stderr_msg,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
            quiesce=True,
        )

    assert result.success is False
    assert result.error == stderr_msg
    assert result.new_allocation == 0

    # No fallback: exactly ONE virsh call, and it must contain --quiesce
    virsh_cmds = [" ".join(c.args[0]) for c in _virsh_create_calls(shell_spy)]
    assert len(virsh_cmds) == 1, (
        "Must NOT retry without --quiesce — exactly one virsh call expected, "
        f"but got {len(virsh_cmds)}"
    )
    assert "--quiesce" in virsh_cmds[0], (
        "The single virsh call must include --quiesce (no silent fallback)"
    )


# ──────────────────────────────────────────────────────────────────────────
# 3c. Lock conflict retry (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_create_snapshot_retry_lock_conflict_resolves(mock_shell, make_vm_config):
    """When the first ``virsh snapshot-create-as`` attempt fails with a lock
    conflict error ('cannot acquire state change lock'), the provider retries
    with exponential backoff.  After one retry succeeds, the overall
    ``create()`` returns success and exactly 2 virsh attempts were made.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    # Pre-configure chmod and qemu-img info for the successful path
    mock_shell.expect("chmod").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    virsh_attempts: list[None] = []
    original_run = mock_shell.run

    def side_effect(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "snapshot-create-as" in cmd_str:
            virsh_attempts.append(None)
            if len(virsh_attempts) == 1:
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="",
                    returncode=1,
                    error="error: internal error: cannot acquire state change lock",
                )
            return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
        return original_run(cmd, timeout=timeout, check=check)

    with (
        patch("qsnap.modules.snapshot.external.time.sleep", return_value=None),
        patch.object(mock_shell, "run", side_effect=side_effect),
    ):
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    assert result.success is True
    assert result.new_allocation == 1048576
    assert result.error is None
    assert len(virsh_attempts) == 2, (
        f"Expected 2 virsh attempts (1 lock-conflict fail + 1 success), got {len(virsh_attempts)}"
    )


def test_create_snapshot_retry_lock_conflict_persists(mock_shell, make_vm_config):
    """When all 3 virsh attempts fail with a lock conflict error, the provider
    returns ``SnapshotResult(success=False)`` with the error from the last
    attempt, and exactly 3 virsh calls were made.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    virsh_attempts: list[None] = []
    original_run = mock_shell.run
    lock_error = "error: internal error: cannot acquire state change lock"

    def side_effect(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "snapshot-create-as" in cmd_str:
            virsh_attempts.append(None)
            return ShellResult(
                success=False,
                stdout="",
                stderr="",
                returncode=1,
                error=lock_error,
            )
        return original_run(cmd, timeout=timeout, check=check)

    with (
        patch("qsnap.modules.snapshot.external.time.sleep", return_value=None),
        patch.object(mock_shell, "run", side_effect=side_effect),
    ):
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    assert result.success is False
    assert result.error == lock_error
    assert len(virsh_attempts) == 3, (
        f"Expected 3 virsh attempts (1 initial + 2 retries), got {len(virsh_attempts)}"
    )


def test_create_snapshot_no_retry_non_lock_error(mock_shell, make_vm_config):
    """When ``virsh snapshot-create-as`` fails with a non-lock error
    ('No space left on device'), the provider fails immediately with NO
    retry.  Exactly 1 virsh call is made.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    stderr_msg = "error: internal error: No space left on device"
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=1,
            error=stderr_msg,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    assert result.success is False
    assert result.error == stderr_msg
    assert result.new_allocation == 0

    virsh_cmds = [
        " ".join(c.args[0])
        for c in shell_spy.call_args_list
        if "snapshot-create-as" in " ".join(c.args[0])
    ]
    assert len(virsh_cmds) == 1, (
        f"Non-lock errors must NOT be retried; expected 1 virsh call, got {len(virsh_cmds)}"
    )


def test_create_snapshot_retry_lock_conflict_timeout(mock_shell, make_vm_config):
    """When ``virsh snapshot-create-as`` times out (returncode=-1) but the
    error message contains 'cannot acquire state change lock', the provider
    retries.  If the second attempt succeeds, the snapshot is created.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    # Pre-configure chmod and qemu-img info for the successful path
    mock_shell.expect("chmod").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    virsh_attempts: list[None] = []
    original_run = mock_shell.run

    def side_effect(cmd, timeout, check=False):
        cmd_str = " ".join(cmd)
        if "snapshot-create-as" in cmd_str:
            virsh_attempts.append(None)
            if len(virsh_attempts) == 1:
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="",
                    returncode=-1,
                    error=("Command timed out after 120s: cannot acquire state change lock"),
                )
            return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
        return original_run(cmd, timeout=timeout, check=check)

    with (
        patch("qsnap.modules.snapshot.external.time.sleep", return_value=None),
        patch.object(mock_shell, "run", side_effect=side_effect),
    ):
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    assert result.success is True
    assert result.new_allocation == 1048576
    assert len(virsh_attempts) == 2, (
        "Timeout with lock conflict should trigger retry; "
        f"expected 2 virsh attempts, got {len(virsh_attempts)}"
    )


# ──────────────────────────────────────────────────────────────────────────
# 4. List backing chain with snapshots
# ──────────────────────────────────────────────────────────────────────────


def test_list_backing_chain_with_snapshots(mock_shell, make_vm_config):
    """When the active image has a backing chain of 3 elements
    (base <- snap1 <- snap2), ``list()`` returns a list of 2
    ``SnapshotInfo`` objects (for snap1 and snap2), sorted oldest-first.
    """
    vm_config = make_vm_config()

    # Step 1: virsh domblklist returns the active disk path
    domblklist_output = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Step 2: qemu-img info --backing-chain returns JSON array of 3 elements
    chain = [
        {
            "filename": "/var/lib/libvirt/images/testvm.qcow2",
            "actual-size": 1073741824,
        },
        {
            "filename": ("/var/lib/libvirt/snapshots/testvm/testvm.20250101T000000.qcow2"),
            "actual-size": 1048576,
        },
        {
            "filename": ("/var/lib/libvirt/snapshots/testvm/testvm.20250102T000000.qcow2"),
            "actual-size": 2097152,
        },
    ]
    mock_shell.expect("qemu-img info.*backing-chain").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(chain),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = ExternalSnapshotProvider(mock_shell)
    snapshots = provider.list(vm_config)

    # Exactly 2 snapshots (base image is skipped)
    assert len(snapshots) == 2

    # First snapshot is the older one (snap1, Jan 1)
    assert snapshots[0].name == "testvm.20250101T000000"
    assert snapshots[0].timestamp == datetime(2025, 1, 1, 0, 0, 0)
    assert snapshots[0].allocation == 1048576
    assert snapshots[0].path == Path(
        "/var/lib/libvirt/snapshots/testvm/testvm.20250101T000000.qcow2"
    )

    # Second snapshot is the newer one (snap2, Jan 2)
    assert snapshots[1].name == "testvm.20250102T000000"
    assert snapshots[1].timestamp == datetime(2025, 1, 2, 0, 0, 0)
    assert snapshots[1].allocation == 2097152
    assert snapshots[1].path == Path(
        "/var/lib/libvirt/snapshots/testvm/testvm.20250102T000000.qcow2"
    )

    # Sorted oldest-first
    assert snapshots[0].timestamp < snapshots[1].timestamp


# ──────────────────────────────────────────────────────────────────────────
# 5. List with no snapshots (fresh VM)
# ──────────────────────────────────────────────────────────────────────────


def test_list_no_snapshots_fresh_vm(mock_shell, make_vm_config):
    """When the active image has no backing chain (only the base image),
    ``list()`` returns an empty list.
    """
    vm_config = make_vm_config()

    domblklist_output = (
        " Target   Source\n"
        "------------------------------------\n"
        " vda      /var/lib/libvirt/images/testvm.qcow2\n"
    )
    mock_shell.expect("virsh domblklist").returns(
        ShellResult(
            success=True,
            stdout=domblklist_output,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    # Only 1 element in the chain — the base image itself
    chain = [
        {
            "filename": "/var/lib/libvirt/images/testvm.qcow2",
            "actual-size": 1073741824,
        },
    ]
    mock_shell.expect("qemu-img info.*backing-chain").returns(
        ShellResult(
            success=True,
            stdout=json.dumps(chain),
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = ExternalSnapshotProvider(mock_shell)
    snapshots = provider.list(vm_config)

    assert snapshots == []


# ──────────────────────────────────────────────────────────────────────────
# 6. Delete snapshot — success
# ──────────────────────────────────────────────────────────────────────────


def test_delete_snapshot_success(mock_shell):
    """When ``rm -f <snapshot.path>`` completes successfully, ``delete()``
    returns ``ShellResult(success=True)``.
    """
    mock_shell.expect("rm -f").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    snapshot = SnapshotInfo(
        name="snap.20250101T000000",
        path=Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=1048576,
    )

    provider = ExternalSnapshotProvider(mock_shell)
    result = provider.delete(snapshot)

    assert result.success is True
    assert result.error is None
    assert result.returncode == 0


# ──────────────────────────────────────────────────────────────────────────
# 7. Delete snapshot — file not found
# ──────────────────────────────────────────────────────────────────────────


def test_delete_snapshot_file_not_found(mock_shell):
    """When the snapshot file does not exist, ``rm -f`` is idempotent and
    still returns success.  ``delete()`` therefore returns
    ``ShellResult(success=True)``.
    """
    mock_shell.expect("rm -f").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    snapshot = SnapshotInfo(
        name="snap.20250101T000000",
        path=Path("/var/lib/libvirt/snapshots/testvm/nonexistent.qcow2"),
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
        allocation=1048576,
    )

    provider = ExternalSnapshotProvider(mock_shell)
    result = provider.delete(snapshot)

    assert result.success is True
    assert result.error is None


# ──────────────────────────────────────────────────────────────────────────
# 8. Shared parser imports
# ──────────────────────────────────────────────────────────────────────────


def test_external_snapshot_provider_imports_shared_parsers():
    """Verify ``external.py`` imports ``parse_domblklist_path`` and
    ``parse_timestamp`` from ``qsnap.utils.parsing``, and has NO
    module-level ``_parse_domblklist_path`` or ``_parse_timestamp``
    functions (i.e. the local duplicates have been removed).
    """
    from qsnap.modules.snapshot import external
    from qsnap.utils.parsing import parse_domblklist_path, parse_timestamp

    # Shared parsers are imported (same object reference)
    assert external.parse_domblklist_path is parse_domblklist_path
    assert external.parse_timestamp is parse_timestamp

    # No local duplicate functions exist
    assert not hasattr(external, "_parse_domblklist_path"), (
        "external.py should NOT have a local _parse_domblklist_path; "
        "it must use the shared parser from qsnap.utils.parsing"
    )
    assert not hasattr(external, "_parse_timestamp"), (
        "external.py should NOT have a local _parse_timestamp; "
        "it must use the shared parser from qsnap.utils.parsing"
    )


# ──────────────────────────────────────────────────────────────────────────
# 10. Post-snapshot qemu-img info uses --force-share (design D5)
# ──────────────────────────────────────────────────────────────────────────


def test_post_snapshot_info_uses_force_share(mock_shell, make_vm_config):
    """After ``virsh snapshot-create-as`` succeeds, the ``qemu-img info``
    command includes ``--force-share`` so it can read metadata despite the
    VM holding an exclusive write lock (design D5).

    The command succeeds even though the VM is running.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    # Step 1: virsh snapshot-create-as succeeds
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Step 2: chmod succeeds
    mock_shell.expect("chmod").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Step 3: qemu-img info --force-share succeeds
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info.*--force-share").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as shell_spy:
        provider = ExternalSnapshotProvider(mock_shell)
        result = provider.create(
            vm_config=vm_config,
            snapshot_name="snap.20250101T000000",
            disk="vda",
            snapshot_path=snapshot_path,
        )

    # The command succeeded despite the VM holding a write lock
    assert result.success is True
    assert result.new_allocation == 1048576

    # Verify --force-share is in the qemu-img info command
    all_cmds = [" ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list]
    qemu_cmd = next(cmd for cmd in all_cmds if "qemu-img info" in cmd)
    assert "--force-share" in qemu_cmd, (
        f"qemu-img info command must include --force-share, got: {qemu_cmd}"
    )


def test_post_snapshot_info_without_force_share_regression(mock_shell, make_vm_config):
    """Regression guard: without ``--force-share``, ``qemu-img info`` on
    a running VM's active layer fails with a lock error because the VM
    holds an exclusive write lock.

    This test uses ``expect_first`` with a negative-lookahead pattern to
    intercept a ``qemu-img info`` command that does NOT contain
    ``--force-share``.  If someone removes ``--force-share`` from the
    source code, the negative-lookahead pattern matches first and returns
    the lock error, causing this test to fail.
    """
    vm_config = make_vm_config()
    snapshot_path = Path("/var/lib/libvirt/snapshots/testvm/snap.20250101T000000")

    # Step 1: virsh snapshot-create-as succeeds
    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Step 2: chmod succeeds
    mock_shell.expect("chmod").returns(
        ShellResult(
            success=True,
            stdout="",
            stderr="",
            returncode=0,
            error=None,
        )
    )
    # Regression guard (expect_first, highest priority):
    # Pattern only matches if --force-share is NOT in the command.
    # If someone removes --force-share, this returns a lock error.
    mock_shell.expect_first(r"qemu-img info(?!.*--force-share)").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="Failed to get shared lock: Is another process using the image?",
            returncode=1,
            error="Failed to get shared lock: Is another process using the image?",
        )
    )
    # Normal expectation: --force-share IS present, so this succeeds
    qemu_info_json = json.dumps({"actual-size": 1048576, "virtual-size": 1073741824})
    mock_shell.expect("qemu-img info.*--force-share").returns(
        ShellResult(
            success=True,
            stdout=qemu_info_json,
            stderr="",
            returncode=0,
            error=None,
        )
    )

    provider = ExternalSnapshotProvider(mock_shell)
    result = provider.create(
        vm_config=vm_config,
        snapshot_name="snap.20250101T000000",
        disk="vda",
        snapshot_path=snapshot_path,
    )

    # The command MUST succeed — --force-share was present
    assert result.success is True, (
        f"Snapshot creation failed: {result.error}. "
        "This likely means --force-share was removed from qemu-img info."
    )
    assert result.new_allocation == 1048576


def test_external_snapshot_no_cross_domain_imports():
    """``qsnap.modules.snapshot.external`` does NOT import anything from
    ``qsnap.modules.backup`` (cross-domain import violation per AGENTS.md).
    """
    import qsnap.modules.snapshot.external as ext_mod

    source = inspect.getsource(ext_mod)
    assert "qsnap.modules.backup" not in source, (
        "external.py must not import from qsnap.modules.backup "
        "(shared utilities live in qsnap.utils.*)"
    )
