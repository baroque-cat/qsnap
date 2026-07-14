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
from qsnap.models.config import VMConfig
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
        ),
        SnapshotInfo(
            name="snap2",
            path=Path("/var/lib/libvirt/snapshots/testvm/testvm.snap2.qcow2"),
            timestamp=datetime(2025, 7, 14, 11, 0),
            allocation=2048,
        ),
    ]


def _make_vm_config() -> VMConfig:
    """Create a VMConfig with a base image for tree output."""
    return VMConfig(
        name="testvm",
        base_image=Path("/var/lib/libvirt/images/testvm.qcow2"),
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
