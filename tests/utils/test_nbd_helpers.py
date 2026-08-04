"""Unit tests for ``qsnap.utils.nbd`` helper functions.

Tests verify:
- ``get_disk_targets`` returns all disk ``(target, source_path)`` pairs
  from ``virsh domblklist --details`` output.
- ``get_disk_targets`` returns an empty list when no disks are
  found or the command fails.
"""

from __future__ import annotations

import pytest

from qsnap.models.results import ShellResult
from qsnap.utils.nbd import get_disk_targets


def _ok_domblklist_details(entries: list[tuple[str, str, str, str]]) -> ShellResult:
    """Build a ``ShellResult`` simulating ``virsh domblklist --details`` output.

    *entries* is a list of ``(Type, Device, Target, Source)`` tuples.
    The real ``--details`` output has four columns; the code checks
    ``Device == "disk"`` and returns ``(Target, Source)``.
    """
    header = " Type      Device      Target  Source\n"
    separator = "------------------------------------------------\n"
    lines = [header, separator]
    for type_, device, target, source in entries:
        lines.append(f" {type_:<10} {device:<8} {target:<8} {source}\n")
    return ShellResult(
        success=True,
        stdout="".join(lines),
        stderr="",
        returncode=0,
        error=None,
    )


class TestGetDiskTargets:
    """Unit tests for ``get_disk_targets``."""

    @pytest.mark.unit
    def test_get_disk_targets_returns_single_disk(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` lists one disk entry,
        ``get_disk_targets`` returns a single ``(target, source_path)`` tuple."""
        # expect_first overrides the conftest.py fixture default which
        # registers a plain ``domblklist`` (2-column) expectation.
        mock_shell.expect_first("virsh domblklist").returns(
            _ok_domblklist_details(
                [("file", "disk", "vda", "/var/lib/libvirt/images/testvm.qcow2")]
            )
        )

        result = get_disk_targets(mock_shell, "testvm")
        assert result == [("vda", "/var/lib/libvirt/images/testvm.qcow2")]

    @pytest.mark.unit
    def test_get_disk_targets_returns_multiple_disks(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` lists multiple disk entries,
        ``get_disk_targets`` returns all of them."""
        mock_shell.expect_first("virsh domblklist").returns(
            _ok_domblklist_details(
                [
                    ("file", "disk", "vda", "/var/lib/libvirt/images/testvm.qcow2"),
                    ("file", "disk", "vdb", "/var/lib/libvirt/images/testvm-disk2.qcow2"),
                ]
            )
        )

        result = get_disk_targets(mock_shell, "testvm")
        assert result == [
            ("vda", "/var/lib/libvirt/images/testvm.qcow2"),
            ("vdb", "/var/lib/libvirt/images/testvm-disk2.qcow2"),
        ]

    @pytest.mark.unit
    def test_get_disk_targets_no_disks(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` lists only cdrom and loop
        devices (no ``Device == "disk"`` rows), ``get_disk_targets``
        returns an empty list."""
        mock_shell.expect_first("virsh domblklist").returns(
            _ok_domblklist_details(
                [
                    ("file", "cdrom", "hda", "/var/lib/libvirt/images/seed.iso"),
                    ("file", "loop", "loop0", "-"),
                ]
            )
        )

        result = get_disk_targets(mock_shell, "testvm")
        assert result == []

    @pytest.mark.unit
    def test_get_disk_targets_command_fails(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` itself fails (non-zero
        return code), ``get_disk_targets`` returns an empty list."""
        mock_shell.expect_first("virsh domblklist").returns(
            ShellResult(
                success=False,
                stdout="",
                stderr="virsh: command failed",
                returncode=1,
                error="virsh: command failed",
            )
        )

        result = get_disk_targets(mock_shell, "testvm")
        assert result == []
