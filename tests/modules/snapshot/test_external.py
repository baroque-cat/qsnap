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
    snapshot_path = Path(
        "/var/lib/libvirt/snapshots/testvm/snap.20250101T000000"
    )

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
    qemu_info_json = json.dumps(
        {"actual-size": 1048576, "virtual-size": 1073741824}
    )
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
        )

    # Assert successful result with correct allocation from qemu-img info
    assert result.success is True
    assert result.new_allocation == 1048576
    assert result.error is None
    assert result.name == "snap.20250101T000000"
    assert result.path == snapshot_path

    # Assert the virsh command contains "vda" and the required flags
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    virsh_cmd = next(
        cmd for cmd in all_cmds if "snapshot-create-as" in cmd
    )
    assert "vda" in virsh_cmd
    assert "--disk-only" in virsh_cmd
    assert "--atomic" in virsh_cmd
    assert "--no-metadata" in virsh_cmd


# ──────────────────────────────────────────────────────────────────────────
# 2. virsh command fails
# ──────────────────────────────────────────────────────────────────────────


def test_create_snapshot_virsh_fails(mock_shell, make_vm_config):
    """When ``virsh snapshot-create-as`` returns a non-zero exit code,
    ``create()`` returns ``SnapshotResult(success=False, error=<stderr>)``
    and short-circuits — ``chmod`` and ``qemu-img info`` are NOT called.
    """
    vm_config = make_vm_config()
    snapshot_path = Path(
        "/var/lib/libvirt/snapshots/testvm/snap.20250101T000000"
    )

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
    all_cmds = [
        " ".join(call_obj.args[0]) for call_obj in shell_spy.call_args_list
    ]
    assert len(all_cmds) == 1, (
        "Only the virsh command should have been called, "
        f"but got {len(all_cmds)} calls"
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
    """
    vm_config = make_vm_config()
    snapshot_path = Path(
        "/var/lib/libvirt/snapshots/testvm/snap.20250101T000000"
    )

    mock_shell.expect("virsh snapshot-create-as").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="",
            returncode=-1,
            error="Command timed out after 120s",
        )
    )

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
            "filename": (
                "/var/lib/libvirt/snapshots/testvm/"
                "testvm.20250101T000000.qcow2"
            ),
            "actual-size": 1048576,
        },
        {
            "filename": (
                "/var/lib/libvirt/snapshots/testvm/"
                "testvm.20250102T000000.qcow2"
            ),
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
        path=Path(
            "/var/lib/libvirt/snapshots/testvm/snap.20250101T000000.qcow2"
        ),
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
        path=Path(
            "/var/lib/libvirt/snapshots/testvm/nonexistent.qcow2"
        ),
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
