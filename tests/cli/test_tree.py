"""Tests for tree-format backing-chain listing via the --tree flag.

Verifies that ``handle_list`` with ``tree=True`` produces an indented
backing-chain tree, that the flat table format is used without ``--tree``,
and that the ``-L`` flag translates to long-format output.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from qsnap.cli.commands import handle_list
from qsnap.cli.errors import EXIT_SUCCESS
from qsnap.models.config import DiskConfig, VMConfig
from qsnap.models.results import SnapshotInfo

# ── helpers ─────────────────────────────────────────────────────────────


def _make_snapshots() -> list[SnapshotInfo]:
    """Create a 2-snapshot chain (base <- snap1 <- snap2)."""
    return [
        SnapshotInfo(
            name="snap1",
            path=Path("/var/lib/libvirt/snapshots/testvm/testvm.snap1.qcow2"),
            timestamp=datetime(2025, 7, 14, 10, 0),
            allocation=1024,
                    disk="vda",
        ),
        SnapshotInfo(
            name="snap2",
            path=Path("/var/lib/libvirt/snapshots/testvm/testvm.snap2.qcow2"),
            timestamp=datetime(2025, 7, 14, 11, 0),
            allocation=2048,
                    disk="vda",
        ),
    ]


def _make_vm_config() -> VMConfig:
    """Create a VMConfig with a base image for tree output."""
    return VMConfig(
        name="testvm",
        disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/testvm.qcow2"))],
        snapshot_dir=Path("/var/lib/libvirt/snapshots/testvm"),
    )


def _make_mock_core() -> Mock:
    """Create a Mock core with tree-friendly return values."""
    core = Mock()
    core.list_snapshots.return_value = {"testvm": _make_snapshots()}
    core.list_config.return_value = [_make_vm_config()]
    return core


def _make_list_args(**overrides: object) -> Namespace:
    """Create a Namespace for the list snapshots subcommand."""
    defaults: dict[str, object] = {
        "command": "list",
        "list_subcommand": "snapshots",
        "vm": [],
        "format": "table",
        "tree": False,
        "long_format": False,
        "dry_run": False,
        "preserve": False,
        "preserve_snapshots": False,
        "preserve_backups": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


# ── tree output tests ───────────────────────────────────────────────────


def test_tree_output_3_level_chain(capsys):
    """3-level backing chain (base <- snap1 <- snap2) shows indented tree."""
    mock_core = _make_mock_core()
    args = _make_list_args(tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    # Header line
    assert "=== testvm ===" in output
    # Base image at indent level 0
    assert "testvm.qcow2" in output
    # snap1 at indent level 1 (2 spaces)
    assert "  testvm.snap1.qcow2" in output
    # snap2 at indent level 2 (4 spaces)
    assert "    testvm.snap2.qcow2" in output
    # Verify ordering: base before snap1 before snap2
    lines = output.strip().split("\n")
    base_idx = next(i for i, line in enumerate(lines) if "testvm.qcow2" in line)
    snap1_idx = next(i for i, line in enumerate(lines) if "snap1" in line)
    snap2_idx = next(i for i, line in enumerate(lines) if "snap2" in line)
    assert base_idx < snap1_idx < snap2_idx


def test_flat_output_without_tree(capsys):
    """Without --tree flag, output is standard flat table format."""
    mock_core = _make_mock_core()
    args = _make_list_args(tree=False)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    # Table format: uppercase headers
    assert "VM" in output
    assert "NAME" in output
    assert "PATH" in output
    assert "TIMESTAMP" in output
    assert "ALLOCATION" in output
    # Snapshot names appear in rows
    assert "snap1" in output
    assert "snap2" in output
    # No tree header
    assert "=== testvm ===" not in output


def test_long_flag_with_list(cli_app, capsys):
    """-L with list command produces long-format output."""
    mock_core = _make_mock_core()
    args = cli_app.parse_args(["-L", "list", "snapshots"])
    # Simulate main() resolution: -L -> format="long"
    if getattr(args, "long_format", False):
        args.format = "long"

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    # Long format shows all available columns (uppercase headers)
    assert "VM" in output
    assert "NAME" in output
    assert "PATH" in output
    assert "TIMESTAMP" in output
    assert "ALLOCATION" in output
    assert "snap1" in output
    assert "snap2" in output


# ── backup tree output tests ───────────────────────────────────────────────


def _make_backup_tree_data(
    vm_name: str = "testvm",
    target: str = "/mnt/backup/testvm",
    chains: dict | None = None,
) -> dict:
    """Create mock backup tree data for CLI tests.

    Returns the structure ``{vm_name: [(target_path, chains)]}`` where
    *chains* is a ``{chain_id: [SnapshotInfo, ...]}`` dict.
    """
    if chains is None:
        full1 = SnapshotInfo(
            name="testvm.FULL.20250701T120000_abc123",
            path=Path(f"{target}/testvm.FULL.20250701T120000_abc123.qcow2"),
            timestamp=datetime(2025, 7, 1, 12, 0),
            allocation=5000,
                    disk="vda",
        )
        inc1 = SnapshotInfo(
            name="testvm.20250702T120000_def456",
            path=Path(f"{target}/testvm.20250702T120000_def456.qcow2"),
            timestamp=datetime(2025, 7, 2, 12, 0),
            allocation=1000,
                    disk="vda",
        )
        inc2 = SnapshotInfo(
            name="testvm.20250703T120000_ghi789",
            path=Path(f"{target}/testvm.20250703T120000_ghi789.qcow2"),
            timestamp=datetime(2025, 7, 3, 12, 0),
            allocation=1000,
                    disk="vda",
        )
        chains = {"testvm.FULL.20250701T120000_abc123": [full1, inc1, inc2]}
    return {vm_name: [(target, chains)]}


def test_backup_tree_output_for_chains(capsys):
    """``list backups --tree`` displays FULL anchors at top level with indented incrementals."""
    mock_core = Mock()
    mock_core.list_backups.return_value = _make_backup_tree_data()
    mock_core.list_config.return_value = [_make_vm_config()]

    args = _make_list_args(list_subcommand="backups", tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    # Header and target
    assert "=== testvm ===" in output
    assert "Target: /mnt/backup/testvm" in output
    # FULL at 2-space indent
    assert "  testvm.FULL.20250701T120000_abc123.qcow2" in output
    # Incrementals at 4-space indent
    assert "    testvm.20250702T120000_def456.qcow2" in output
    assert "    testvm.20250703T120000_ghi789.qcow2" in output

    # Verify ordering: FULL before incrementals
    lines = output.strip().split("\n")
    full_idx = next(
        i for i, line in enumerate(lines) if "FULL.20250701T120000" in line
    )
    inc1_idx = next(
        i for i, line in enumerate(lines) if "20250702T120000" in line
    )
    inc2_idx = next(
        i for i, line in enumerate(lines) if "20250703T120000" in line
    )
    assert full_idx < inc1_idx < inc2_idx


def test_backup_tree_output_orphan_backups(capsys):
    """``list backups --tree`` shows orphans under ``(orphan)`` header."""
    orphan1 = SnapshotInfo(
        name="testvm.20250702T120000_def456",
        path=Path("/mnt/backup/testvm/testvm.20250702T120000_def456.qcow2"),
        timestamp=datetime(2025, 7, 2, 12, 0),
        allocation=1000,
                    disk="vda",
    )
    orphan2 = SnapshotInfo(
        name="testvm.20250703T120000_ghi789",
        path=Path("/mnt/backup/testvm/testvm.20250703T120000_ghi789.qcow2"),
        timestamp=datetime(2025, 7, 3, 12, 0),
        allocation=1000,
                    disk="vda",
    )

    data = _make_backup_tree_data(
        chains={"__orphan__": [orphan1, orphan2]}
    )

    mock_core = Mock()
    mock_core.list_backups.return_value = data
    mock_core.list_config.return_value = [_make_vm_config()]

    args = _make_list_args(list_subcommand="backups", tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    assert "=== testvm ===" in output
    assert "Target: /mnt/backup/testvm" in output
    assert "  (orphan)" in output
    assert "    testvm.20250702T120000_def456.qcow2" in output
    assert "    testvm.20250703T120000_ghi789.qcow2" in output


def test_backup_tree_output_with_vm_filter(capsys):
    """``list backups --tree VM`` filters output to the specified VM and calls
    Core with the correct filter."""
    full1 = SnapshotInfo(
        name="vm1.FULL.20250701T120000_abc123",
        path=Path("/mnt/backup/vm1/vm1.FULL.20250701T120000_abc123.qcow2"),
        timestamp=datetime(2025, 7, 1, 12, 0),
        allocation=5000,
                    disk="vda",
    )
    chains = {"vm1.FULL.20250701T120000_abc123": [full1]}
    data = {"vm1": [("/mnt/backup/vm1", chains)]}

    mock_core = Mock()
    mock_core.list_backups.return_value = data
    mock_core.list_config.return_value = [
        VMConfig(
            name="vm1",
            disks=[DiskConfig(target="vda", base_image=Path("/var/lib/libvirt/images/vm1.qcow2"))],
            snapshot_dir=Path("/var/lib/libvirt/snapshots/vm1"),
        )
    ]

    args = _make_list_args(list_subcommand="backups", vm=["vm1"], tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    assert "=== vm1 ===" in output
    assert "Target: /mnt/backup/vm1" in output
    assert "  vm1.FULL.20250701T120000_abc123.qcow2" in output
    # Core was called with the correct filter
    mock_core.list_backups.assert_called_once_with("vm1", tree=True)


def test_backup_tree_output_multiple_chains(capsys):
    """``list backups --tree`` displays multiple FULL chains each with their own incrementals."""
    full1 = SnapshotInfo(
        name="testvm.FULL.20250701T120000_abc123",
        path=Path("/mnt/backup/testvm/testvm.FULL.20250701T120000_abc123.qcow2"),
        timestamp=datetime(2025, 7, 1, 12, 0),
        allocation=5000,
                    disk="vda",
    )
    inc1a = SnapshotInfo(
        name="testvm.20250702T120000_def456",
        path=Path("/mnt/backup/testvm/testvm.20250702T120000_def456.qcow2"),
        timestamp=datetime(2025, 7, 2, 12, 0),
        allocation=1000,
                    disk="vda",
    )
    full2 = SnapshotInfo(
        name="testvm.FULL.20250704T120000_ghi789",
        path=Path("/mnt/backup/testvm/testvm.FULL.20250704T120000_ghi789.qcow2"),
        timestamp=datetime(2025, 7, 4, 12, 0),
        allocation=5000,
                    disk="vda",
    )
    inc2a = SnapshotInfo(
        name="testvm.20250705T120000_jkl012",
        path=Path("/mnt/backup/testvm/testvm.20250705T120000_jkl012.qcow2"),
        timestamp=datetime(2025, 7, 5, 12, 0),
        allocation=1000,
                    disk="vda",
    )

    chains = {
        "testvm.FULL.20250701T120000_abc123": [full1, inc1a],
        "testvm.FULL.20250704T120000_ghi789": [full2, inc2a],
    }
    data = _make_backup_tree_data(chains=chains)

    mock_core = Mock()
    mock_core.list_backups.return_value = data
    mock_core.list_config.return_value = [_make_vm_config()]

    args = _make_list_args(list_subcommand="backups", tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    assert "=== testvm ===" in output
    # First chain
    assert "  testvm.FULL.20250701T120000_abc123.qcow2" in output
    assert "    testvm.20250702T120000_def456.qcow2" in output
    # Second chain
    assert "  testvm.FULL.20250704T120000_ghi789.qcow2" in output
    assert "    testvm.20250705T120000_jkl012.qcow2" in output

    # Verify chain ordering: full1 before full2 (by timestamp)
    lines = output.strip().split("\n")
    full1_idx = next(
        i for i, line in enumerate(lines) if "FULL.20250701T120000" in line
    )
    full2_idx = next(
        i for i, line in enumerate(lines) if "FULL.20250704T120000" in line
    )
    assert full1_idx < full2_idx


def test_backup_tree_output_chain_without_full(capsys):
    """``list backups --tree`` shows backups at 2-space indent for chains with no FULL entries
    (defensive fallback — should not happen in practice)."""
    backup1 = SnapshotInfo(
        name="testvm.20250702T120000_def456",
        path=Path("/mnt/backup/testvm/testvm.20250702T120000_def456.qcow2"),
        timestamp=datetime(2025, 7, 2, 12, 0),
        allocation=1000,
                    disk="vda",
    )
    backup2 = SnapshotInfo(
        name="testvm.20250703T120000_ghi789",
        path=Path("/mnt/backup/testvm/testvm.20250703T120000_ghi789.qcow2"),
        timestamp=datetime(2025, 7, 3, 12, 0),
        allocation=1000,
                    disk="vda",
    )

    # Chain with non-orphan chain_id but no .FULL. entries
    data = _make_backup_tree_data(
        chains={"testvm.20250702T120000_def456": [backup1, backup2]}
    )

    mock_core = Mock()
    mock_core.list_backups.return_value = data
    mock_core.list_config.return_value = [_make_vm_config()]

    args = _make_list_args(list_subcommand="backups", tree=True)

    result = handle_list(mock_core, args)

    assert result == EXIT_SUCCESS
    captured = capsys.readouterr()
    output = captured.out

    assert "=== testvm ===" in output
    # No (orphan) header — it's a normal chain with no FULL entries
    assert "(orphan)" not in output
    # Backups at 2-space indent (fallback branch)
    assert "  testvm.20250702T120000_def456.qcow2" in output
    assert "  testvm.20250703T120000_ghi789.qcow2" in output
