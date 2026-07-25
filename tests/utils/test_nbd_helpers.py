"""Unit tests for ``qsnap.utils.nbd`` helper functions added for the
fast-compressed-full-backup change.

Tests verify:
- ``get_first_disk_path`` returns the file path of the first disk
  from ``virsh domblklist --details`` output.
- ``get_first_disk_path`` returns an empty string when no disk is
  found or the command fails.
"""

from __future__ import annotations

import pytest

from qsnap.models.results import ShellResult
from qsnap.utils.nbd import get_first_disk_path


def _ok_domblklist_details(entries: list[tuple[str, str, str, str]]) -> ShellResult:
    """Build a ``ShellResult`` simulating ``virsh domblklist --details`` output.

    *entries* is a list of ``(Type, Device, Target, Source)`` tuples.
    The real ``--details`` output has four columns; the code checks
    ``Device == "disk"`` and returns ``Source``.
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


class TestGetFirstDiskPath:
    """Unit tests for ``get_first_disk_path``."""

    @pytest.mark.unit
    def test_get_first_disk_path_returns_path(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` lists one disk entry,
        ``get_first_disk_path`` returns the ``Source`` (file path)."""
        # expect_first overrides the conftest.py fixture default which
        # registers a plain ``domblklist`` (2-column) expectation.
        mock_shell.expect_first("virsh domblklist").returns(
            _ok_domblklist_details(
                [("file", "disk", "vda", "/var/lib/libvirt/images/testvm.qcow2")]
            )
        )

        result = get_first_disk_path(mock_shell, "testvm")
        assert result == "/var/lib/libvirt/images/testvm.qcow2"

    @pytest.mark.unit
    def test_get_first_disk_path_no_disks(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` lists only cdrom and loop
        devices (no ``Device == "disk"`` rows), ``get_first_disk_path``
        returns an empty string."""
        # expect_first overrides the conftest.py fixture default.
        mock_shell.expect_first("virsh domblklist").returns(
            _ok_domblklist_details(
                [
                    ("file", "cdrom", "hda", "/var/lib/libvirt/images/seed.iso"),
                    ("file", "loop", "loop0", "-"),
                ]
            )
        )

        result = get_first_disk_path(mock_shell, "testvm")
        assert result == ""

    @pytest.mark.unit
    def test_get_first_disk_path_command_fails(self, mock_shell) -> None:
        """When ``virsh domblklist --details`` itself fails (non-zero
        return code), ``get_first_disk_path`` returns an empty string."""
        # expect_first overrides the conftest.py fixture default.
        mock_shell.expect_first("virsh domblklist").returns(
            ShellResult(
                success=False,
                stdout="",
                stderr="virsh: command failed",
                returncode=1,
                error="virsh: command failed",
            )
        )

        result = get_first_disk_path(mock_shell, "testvm")
        assert result == ""
