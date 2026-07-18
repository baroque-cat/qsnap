"""Unit tests for qsnap.utils.transaction.TransactionWriter.

Tests the btrbk-compatible transaction log writer for all action types,
append behavior, file creation, and architectural isolation from Core.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.models.results import ActionRecord
from qsnap.utils.transaction import TransactionWriter

# ── Helpers ──────────────────────────────────────────────────────────────


def _read_file(path: Path) -> str:
    """Read the entire transaction log as a single string."""
    return path.read_text(encoding="utf-8")


# ── Line-format tests ────────────────────────────────────────────────────


def test_write_snapshot_create_line(tmp_path: Path) -> None:
    """Write a snapshot_create ActionRecord — verify btrbk format.

    Expected: <localtime> snapshot success - <path> -
    """
    log = tmp_path / "transaction.log"
    record = ActionRecord(
        action="snapshot_create",
        vm_name="test-vm",
        name="snap1",
        path=Path("/var/lib/libvirt/images/test-vm_snap1.qcow2"),
    )
    TransactionWriter.write(log, record)

    content = _read_file(log).strip()
    fields = content.split(" ")

    # 6 space-separated fields: localtime type status target_url source_url parent_url
    assert len(fields) == 6, f"Expected 6 fields, got {len(fields)}: {fields}"
    assert fields[1] == "snapshot"
    assert fields[2] == "success"
    assert fields[3] == "-"
    assert fields[4] == "/var/lib/libvirt/images/test-vm_snap1.qcow2"
    assert fields[5] == "-"


def test_write_snapshot_delete_line(tmp_path: Path) -> None:
    """Write a snapshot_delete ActionRecord — verify btrbk format.

    Expected: <localtime> delete_snapshot success - <path> -
    """
    log = tmp_path / "transaction.log"
    record = ActionRecord(
        action="snapshot_delete",
        vm_name="test-vm",
        name="snap1",
        path=Path("/var/lib/libvirt/images/test-vm_snap1.qcow2"),
    )
    TransactionWriter.write(log, record)

    content = _read_file(log).strip()
    fields = content.split(" ")

    assert len(fields) == 6
    assert fields[1] == "delete_snapshot"
    assert fields[2] == "success"
    assert fields[3] == "-"
    assert fields[4] == "/var/lib/libvirt/images/test-vm_snap1.qcow2"
    assert fields[5] == "-"


def test_write_backup_transfer_line(tmp_path: Path) -> None:
    """Write a backup_transfer ActionRecord — verify btrbk format.

    Expected: <localtime> backup success <path> - -
    """
    log = tmp_path / "transaction.log"
    record = ActionRecord(
        action="backup_transfer",
        vm_name="test-vm",
        name="backup1",
        path=Path("/backup/test-vm_snap1.qcow2"),
    )
    TransactionWriter.write(log, record)

    content = _read_file(log).strip()
    fields = content.split(" ")

    assert len(fields) == 6
    assert fields[1] == "backup"
    assert fields[2] == "success"
    assert fields[3] == "/backup/test-vm_snap1.qcow2"
    assert fields[4] == "-"
    assert fields[5] == "-"


def test_write_full_backup_line(tmp_path: Path) -> None:
    """Write a backup_full ActionRecord — verify btrbk format.

    Expected: <localtime> backup_full success <path> - -
    """
    log = tmp_path / "transaction.log"
    record = ActionRecord(
        action="backup_full",
        vm_name="test-vm",
        name="full1",
        path=Path("/backup/test-vm_full.qcow2"),
    )
    TransactionWriter.write(log, record)

    content = _read_file(log).strip()
    fields = content.split(" ")

    assert len(fields) == 6
    assert fields[1] == "backup_full"
    assert fields[2] == "success"
    assert fields[3] == "/backup/test-vm_full.qcow2"
    assert fields[4] == "-"
    assert fields[5] == "-"


def test_write_error_line(tmp_path: Path) -> None:
    """Write an error ActionRecord — verify error format.

    Expected: <localtime> error ERROR - - # <error_message>
    """
    log = tmp_path / "transaction.log"
    record = ActionRecord(
        action="error",
        vm_name="test-vm",
        name="err1",
        path=Path("/tmp"),
        error="disk full",
    )
    TransactionWriter.write(log, record)

    content = _read_file(log).strip()
    fields = content.split(" ")

    assert len(fields) >= 6  # error message may contain spaces
    assert fields[1] == "error"
    assert fields[2] == "ERROR"
    assert fields[3] == "-"
    assert fields[4] == "-"
    # parent_url starts with "# " followed by the error message
    assert fields[5] == "#"
    assert " ".join(fields[6:]) == "disk full" if len(fields) > 6 else False
    # Alternative: check the full parent_url suffix
    assert "# disk full" in content


def test_write_finished_line(tmp_path: Path) -> None:
    """Call write_finished — verify finished line format.

    Expected: <localtime> finished success - - -
    """
    log = tmp_path / "transaction.log"
    TransactionWriter.write_finished(log)

    content = _read_file(log).strip()
    fields = content.split(" ")

    # write_finished produces 6 fields: localtime + type + status + 3 dashes
    # Format: <localtime> finished success - - -
    assert len(fields) == 6, f"Expected 6 fields, got {len(fields)}: {fields}"
    assert fields[1] == "finished"
    assert fields[2] == "success"
    assert fields[3] == "-"
    assert fields[4] == "-"
    assert fields[5] == "-"


# ── Behavioral tests ─────────────────────────────────────────────────────


def test_writer_appends_to_existing_file(tmp_path: Path) -> None:
    """Write two records — verify both lines preserved."""
    log = tmp_path / "transaction.log"
    record1 = ActionRecord(
        action="snapshot_create",
        vm_name="vm1",
        name="snap1",
        path=Path("/images/vm1_snap1.qcow2"),
    )
    record2 = ActionRecord(
        action="snapshot_delete",
        vm_name="vm1",
        name="snap1",
        path=Path("/images/vm1_snap1.qcow2"),
    )

    TransactionWriter.write(log, record1)
    TransactionWriter.write(log, record2)

    lines = _read_file(log).strip().split("\n")
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"

    # First line should still be snapshot_create
    fields1 = lines[0].split(" ")
    assert fields1[1] == "snapshot"

    # Second line should be snapshot_delete
    fields2 = lines[1].split(" ")
    assert fields2[1] == "delete_snapshot"


def test_writer_creates_file_if_not_exists(tmp_path: Path) -> None:
    """Write to a non-existent file — verify file is created."""
    log = tmp_path / "transaction.log"
    assert not log.is_file()

    record = ActionRecord(
        action="snapshot_create",
        vm_name="test-vm",
        name="snap1",
        path=Path("/images/snap1.qcow2"),
    )
    TransactionWriter.write(log, record)

    assert log.is_file()
    lines = _read_file(log).strip().split("\n")
    assert len(lines) == 1


def test_write_creating_nonexistent_directory(tmp_path: Path) -> None:
    """Write to a path where the parent directory doesn't exist.

    TransactionWriter should create intermediate directories gracefully.
    """
    log = tmp_path / "deeply" / "nested" / "dir" / "transaction.log"
    assert not log.parent.is_dir()

    record = ActionRecord(
        action="snapshot_create",
        vm_name="test-vm",
        name="snap1",
        path=Path("/images/snap1.qcow2"),
    )
    TransactionWriter.write(log, record)

    assert log.is_file()
    content = _read_file(log).strip()
    assert "snapshot" in content


# ── Architectural isolation test ─────────────────────────────────────────


def test_writer_has_no_core_dependency() -> None:
    """Verify TransactionWriter has no imports from Core, config, or modules.

    Reads the source file directly and checks that no import lines
    reference ``qsnap.core``, ``qsnap.config``, or ``qsnap.modules``.
    """
    source_path = Path(__file__).resolve().parent.parent.parent / "qsnap" / "utils" / "transaction.py"
    source = source_path.read_text(encoding="utf-8")

    forbidden = ["qsnap.core", "qsnap.config", "qsnap.modules"]
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            for f in forbidden:
                assert f not in stripped, (
                    f"Forbidden import '{f}' found in line: {stripped}"
                )
