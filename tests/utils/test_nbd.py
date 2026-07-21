"""Unit tests for qsnap.utils.nbd — shared NBD utility functions.

Tests verify that public NBD functions are importable from the shared
utility module (not from a domain sub-package), and that
``write_backup_xml`` correctly produces pull-model backup XML for both
full and incremental NBD exports.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from qsnap.models.results import ShellResult
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
    write_backup_xml,
    write_checkpoint_xml,
)

# ── Shared test helpers ─────────────────────────────────────────────────


def _ok_result() -> ShellResult:
    """A generic successful ShellResult."""
    return ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)


def _ok_version_result(version: str = "8.2.0") -> ShellResult:
    """A successful ``virsh --version`` ShellResult."""
    return ShellResult(
        success=True,
        stdout=f"virsh {version}\n",
        stderr="",
        returncode=0,
        error=None,
    )


def test_nbd_public_functions_importable() -> None:
    """``is_vm_running``, ``is_libvirt_new_enough``, and ``nbd_full_export``
    are importable from ``qsnap.utils.nbd`` and are callable.
    """
    assert callable(is_vm_running)
    assert callable(is_libvirt_new_enough)
    assert callable(nbd_full_export)


class TestCoreImportsNbdFromUtils:
    """Verify that Core imports NBD utilities from ``qsnap.utils.nbd``,
    not from a domain sub-package (design D1: shared utilities live
    under ``qsnap.utils``)."""

    def test_core_imports_nbd_from_utils(self) -> None:
        """Core's source imports NBD utilities from ``qsnap.utils.nbd``,
        and all four public NBD functions are importable from that module
        with the correct ``__module__`` attribute."""
        import qsnap.core as core_mod

        core_source = inspect.getsource(core_mod)
        assert "from qsnap.utils.nbd import" in core_source, (
            "Core must import NBD utilities from qsnap.utils.nbd"
        )

        # All four NBD functions must be importable from qsnap.utils.nbd
        # and have the correct source module.
        for fn in (write_backup_xml, nbd_full_export, is_vm_running, is_libvirt_new_enough):
            assert fn.__module__ == "qsnap.utils.nbd", (
                f"{fn.__name__} must originate from qsnap.utils.nbd, got {fn.__module__}"
            )

    def test_file_copy_provider_imports_nbd_from_utils(self) -> None:
        """FileCopyBackupProvider imports NBD utilities from
        ``qsnap.utils.nbd`` (not from any other location)."""
        import qsnap.modules.backup.file_copy as fc_mod

        fc_source = inspect.getsource(fc_mod)
        assert "from qsnap.utils.nbd import" in fc_source, (
            "FileCopyBackupProvider must import NBD utilities from qsnap.utils.nbd"
        )
        assert "is_libvirt_new_enough" in fc_source
        assert "is_vm_running" in fc_source
        assert "nbd_full_export" in fc_source

    def test_bitmap_provider_imports_write_backup_xml_from_utils(self) -> None:
        """BitmapBackupProvider imports ``write_backup_xml`` from
        ``qsnap.utils.nbd`` and does NOT define its own private
        ``_write_backup_xml`` method (design D2: deduplication)."""
        import qsnap.modules.backup.bitmap as bm_mod
        from qsnap.modules.backup.bitmap import BitmapBackupProvider

        bm_source = inspect.getsource(bm_mod)
        assert "from qsnap.utils.nbd import" in bm_source, (
            "BitmapBackupProvider must import NBD utilities from qsnap.utils.nbd"
        )
        assert "write_backup_xml" in bm_source, (
            "BitmapBackupProvider must import write_backup_xml from qsnap.utils.nbd"
        )

        # No private _write_backup_xml method on the class (deduplication).
        assert not hasattr(BitmapBackupProvider, "_write_backup_xml"), (
            "BitmapBackupProvider must NOT define _write_backup_xml — "
            "the shared utility must be imported instead (design D2)"
        )

        # Also verify the imported write_backup_xml originates from
        # qsnap.utils.nbd, not re-exported from elsewhere.
        assert write_backup_xml.__module__ == "qsnap.utils.nbd", (
            "write_backup_xml must originate from qsnap.utils.nbd, "
            f"got {write_backup_xml.__module__}"
        )


class TestWriteBackupXml:
    """Behaviour of ``write_backup_xml`` when called with and without an
    ``incremental`` checkpoint name."""

    def test_write_backup_xml_with_incremental(self) -> None:
        """When an ``incremental`` checkpoint name is provided, the XML
        contains an ``<incremental>`` element placed before the
        ``<server>`` element, and the XML is well-formed."""
        socket = "/tmp/qsnap-test-incremental.sock"
        checkpoint = "qsnap-testhash-testsnap"
        xml_path = write_backup_xml(socket, incremental=checkpoint)

        try:
            raw = xml_path.read_text()

            # The <incremental> element must be present with the correct value.
            assert "<incremental>" in raw
            assert f"<incremental>{checkpoint}</incremental>" in raw

            # Parse to check well-formedness and element ordering.
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            assert root.tag == "domainbackup"
            assert root.attrib.get("mode") == "pull"

            children = list(root)
            inc_idx = next(i for i, el in enumerate(children) if el.tag == "incremental")
            server_idx = next(i for i, el in enumerate(children) if el.tag == "server")
            assert inc_idx < server_idx, (
                "<incremental> must appear BEFORE <server> in the backup XML"
            )
        finally:
            xml_path.unlink(missing_ok=True)

    def test_write_backup_xml_without_incremental(self) -> None:
        """When no ``incremental`` checkpoint is provided (full export),
        the XML does NOT contain an ``<incremental>`` element but still
        has the ``<server>`` and ``<transport>`` elements."""
        socket = "/tmp/qsnap-test-full.sock"
        xml_path = write_backup_xml(socket)

        try:
            raw = xml_path.read_text()

            # No <incremental> element for a full export.
            assert "<incremental>" not in raw

            # Full-export boilerplate must still be present.
            assert "<server" in raw
            assert "transport='unix'" in raw
            assert f"socket='{socket}'" in raw
            assert "mode='pull'" in raw

            # Parse to verify well-formedness.
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            assert root.tag == "domainbackup"
            assert root.attrib.get("mode") == "pull"

            # <server> must be a direct child of <domainbackup>.
            server_el = root.find("server")
            assert server_el is not None
            assert server_el.attrib.get("transport") == "unix"

            # <incremental> must NOT exist anywhere in the tree.
            assert root.find("incremental") is None
        finally:
            xml_path.unlink(missing_ok=True)

    @pytest.mark.parametrize("incremental", [None])
    def test_write_backup_xml_with_explicit_none(self, incremental: None) -> None:
        """Passing ``incremental=None`` explicitly behaves the same as
        the default — no ``<incremental>`` element is produced."""
        socket = "/tmp/qsnap-test-none.sock"
        xml_path = write_backup_xml(socket, incremental=incremental)

        try:
            raw = xml_path.read_text()
            assert "<incremental>" not in raw

            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            assert root.find("incremental") is None
        finally:
            xml_path.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────
# write_checkpoint_xml tests
# ──────────────────────────────────────────────────────────────────────────


class TestWriteCheckpointXml:
    """Behaviour of ``write_checkpoint_xml`` — generates libvirt checkpoint
    XML (atomic checkpoint creation via ``virsh backup-begin``, design D1)."""

    def test_generates_valid_xml(self) -> None:
        """``write_checkpoint_xml("qsnap-h-20260721T010000")`` writes a
        compact (single-line) XML with the checkpoint name embedded."""
        checkpoint_name = "qsnap-h-20260721T010000"
        xml_path = write_checkpoint_xml(checkpoint_name)

        try:
            raw = xml_path.read_text()

            # The XML must contain the domaincheckpoint element with the
            # correct name, and must be well-formed.
            assert f"<domaincheckpoint><name>{checkpoint_name}</name></domaincheckpoint>" in raw, (
                f"Expected checkpoint XML with name {checkpoint_name}, got: {raw!r}"
            )

            # Parse to verify well-formedness.
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            assert root.tag == "domaincheckpoint"

            name_el = root.find("name")
            assert name_el is not None
            assert name_el.text == checkpoint_name
        finally:
            xml_path.unlink(missing_ok=True)

    def test_file_removable(self) -> None:
        """The returned path exists on the filesystem and can be unlinked."""
        xml_path = write_checkpoint_xml("qsnap-h-20260721T010000")

        assert xml_path.exists(), f"Temp file must exist: {xml_path}"
        assert xml_path.is_file()

        # Unlink should succeed.
        xml_path.unlink()
        assert not xml_path.exists()


# ──────────────────────────────────────────────────────────────────────────
# is_libvirt_new_enough tests
# ──────────────────────────────────────────────────────────────────────────


class TestIsLibvirtNewEnough:
    """Version gate for libvirt >= 7.2 (the baseline for the incremental
    backup API, including ``<incremental>`` XML element and checkpoint
    XML argument to ``virsh backup-begin``)."""

    @pytest.mark.parametrize(
        "version, expected",
        [
            ("7.1.0", False),
            ("7.2.0", True),
            ("9.0.0", True),
            ("6.5.0", False),
        ],
    )
    def test_boundary_7_2(self, mock_shell, version, expected) -> None:
        """``is_libvirt_new_enough`` returns ``True`` only when
        ``virsh --version`` reports >= 7.2.0 (the gate raised from 6.0
        to 7.2 per D6)."""
        mock_shell.expect("virsh --version").returns(_ok_version_result(version))
        assert is_libvirt_new_enough(mock_shell) == expected

    def test_accepts_min_major_override(self, mock_shell) -> None:
        """``is_libvirt_new_enough(shell, min_major=8)`` returns
        ``False`` for virsh 7.2.0 — the override path still works,
        and (7,2) >= (8,2) is False."""
        mock_shell.expect("virsh --version").returns(_ok_version_result("7.2.0"))
        assert is_libvirt_new_enough(mock_shell, min_major=8) is False

    def test_unparseable_version_returns_false(self, mock_shell) -> None:
        """Returns ``False`` when ``virsh --version`` output cannot be
        parsed (no major.minor pattern)."""
        mock_shell.expect("virsh --version").returns(
            ShellResult(
                success=True,
                stdout="unknown\n",
                stderr="",
                returncode=0,
                error=None,
            )
        )
        assert is_libvirt_new_enough(mock_shell) is False

    def test_command_failure_returns_false(self, mock_shell) -> None:
        """Returns ``False`` when ``virsh --version`` itself fails."""
        mock_shell.expect("virsh --version").returns(
            ShellResult(
                success=False,
                stdout="",
                stderr="virsh: command not found",
                returncode=127,
                error="virsh: command not found",
            )
        )
        assert is_libvirt_new_enough(mock_shell) is False


# ──────────────────────────────────────────────────────────────────────────
# nbd_full_export checkpoint tests
# ──────────────────────────────────────────────────────────────────────────


class TestNbdFullExportCheckpoint:
    """Tests for the ``checkpoint_name`` kwarg on ``nbd_full_export``
    — atomic checkpoint creation at the export's freeze point (design D1)."""

    def test_passes_checkpoint_xml_when_provided(self, mock_shell, tmp_path) -> None:
        """When ``checkpoint_name`` is non-``None``, ``virsh backup-begin``
        receives a checkpoint XML path as the third positional argument,
        and it comes LAST after the backup XML path."""
        target = tmp_path / "target.qcow2"
        mock_shell.expect("rm -f").returns(_ok_result())
        mock_shell.expect("backup-begin").returns(_ok_result())
        mock_shell.expect("qemu-img convert").returns(_ok_result())
        mock_shell.expect("domjobabort").returns(_ok_result())

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
            patch.object(
                mock_shell,
                "run_with_stall_detection",
                wraps=mock_shell.run_with_stall_detection,
            ) as stall_spy,
        ):
            result = nbd_full_export(
                mock_shell,
                "testvm",
                target,
                checkpoint_name="qsnap-h-20260721T010000",
            )

        assert result.success

        all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
        all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
        all_cmds = all_run_cmds + all_stall_cmds

        # Exactly one backup-begin call.
        backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
        assert len(backup_cmds) == 1, (
            f"Expected exactly 1 backup-begin command, got {len(backup_cmds)}"
        )

        # The backup-begin command must contain BOTH XML paths.
        backup_cmd = backup_cmds[0]
        assert "qsnap-backup-" in backup_cmd, "backup-begin command must contain backup XML path"
        assert "qsnap-checkpoint-" in backup_cmd, (
            "backup-begin command must contain checkpoint XML path"
        )

        # Both XML paths should end with .xml.
        parts = backup_cmd.split()
        xml_args = [p for p in parts if p.endswith(".xml")]
        assert len(xml_args) == 2, (
            f"Expected exactly 2 .xml arguments in backup-begin, got {len(xml_args)}: {xml_args}"
        )
        assert "qsnap-backup-" in xml_args[0], "First .xml argument must be the backup XML"
        assert "qsnap-checkpoint-" in xml_args[1], (
            "Second .xml argument must be the checkpoint XML (LAST positional arg)"
        )

    def test_no_checkpoint_when_none(self, mock_shell, tmp_path) -> None:
        """When ``checkpoint_name`` is ``None`` (the default),
        ``virsh backup-begin`` receives exactly two positional args
        (backup XML only), and no command contains ``checkpoint``."""
        target = tmp_path / "target.qcow2"
        mock_shell.expect("rm -f").returns(_ok_result())
        mock_shell.expect("backup-begin").returns(_ok_result())
        mock_shell.expect("qemu-img convert").returns(_ok_result())
        mock_shell.expect("domjobabort").returns(_ok_result())

        with (
            patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy,
            patch.object(
                mock_shell,
                "run_with_stall_detection",
                wraps=mock_shell.run_with_stall_detection,
            ) as stall_spy,
        ):
            result = nbd_full_export(
                mock_shell,
                "testvm",
                target,
                checkpoint_name=None,
            )

        assert result.success

        all_run_cmds = [" ".join(call_obj.args[0]) for call_obj in run_spy.call_args_list]
        all_stall_cmds = [" ".join(call_obj.args[0]) for call_obj in stall_spy.call_args_list]
        all_cmds = all_run_cmds + all_stall_cmds

        # Exactly one backup-begin call.
        backup_cmds = [cmd for cmd in all_cmds if "backup-begin" in cmd]
        assert len(backup_cmds) == 1, (
            f"Expected exactly 1 backup-begin command, got {len(backup_cmds)}"
        )

        backup_cmd = backup_cmds[0]
        parts = backup_cmd.split()
        xml_args = [p for p in parts if p.endswith(".xml")]
        assert len(xml_args) == 1, (
            f"Expected exactly 1 .xml argument (backup XML only), got {len(xml_args)}: {xml_args}"
        )
        assert "qsnap-backup-" in xml_args[0], "The single .xml argument must be the backup XML"
        assert "checkpoint" not in xml_args[0], "The backup XML path must not contain 'checkpoint'"

        # No command in the entire run should mention checkpoint-related
        # operations (checkpoint-delete, checkpoint-create-as, etc.).
        # The literal "checkpoint" from pytest temp directory paths is
        # harmless and must not trigger a false positive.
        checkpoint_ops = [
            "checkpoint-delete",
            "checkpoint-create-as",
            "qsnap-checkpoint-",
            "checkpoint-list",
        ]
        for cmd in all_cmds:
            for op in checkpoint_ops:
                assert op not in cmd, (
                    f"No checkpoint operation should appear when "
                    f"checkpoint_name is None, but found '{op}' in: {cmd}"
                )
