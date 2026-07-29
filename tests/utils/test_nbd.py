"""Unit tests for qsnap.utils.nbd — shared NBD utility functions.

Tests verify that public NBD functions are importable from the shared
utility module (not from a domain sub-package), and that
``write_backup_xml`` correctly produces pull-model backup XML for both
full and incremental NBD exports.
"""

from __future__ import annotations

import inspect
from xml.etree import ElementTree as ET

import pytest

from qsnap.models.results import ShellResult
from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
    write_backup_xml,
    write_checkpoint_xml,
)

# ── Shared test helpers ─────────────────────────────────────────────────


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
    """``is_vm_running``, ``is_libvirt_new_enough``, ``write_backup_xml``,
    and ``write_checkpoint_xml`` are importable from ``qsnap.utils.nbd``
    and are callable.
    """
    assert callable(is_vm_running)
    assert callable(is_libvirt_new_enough)
    assert callable(write_backup_xml)
    assert callable(write_checkpoint_xml)


class TestCoreImportsNbdFromUtils:
    """Verify that Core imports NBD utilities from ``qsnap.utils.nbd``,
    not from a domain sub-package (design D1: shared utilities live
    under ``qsnap.utils``)."""

    def test_core_imports_nbd_from_utils(self) -> None:
        """Core's source imports NBD utilities from ``qsnap.utils.nbd``,
        and the surviving NBD functions are importable from that module
        with the correct ``__module__`` attribute."""
        import qsnap.core as core_mod

        core_source = inspect.getsource(core_mod)
        assert "from qsnap.utils.nbd import" in core_source, (
            "Core must import NBD utilities from qsnap.utils.nbd"
        )

        # Surviving NBD functions must be importable from qsnap.utils.nbd
        # and have the correct source module.
        for fn in (write_backup_xml, is_vm_running, is_libvirt_new_enough, write_checkpoint_xml):
            assert fn.__module__ == "qsnap.utils.nbd", (
                f"{fn.__name__} must originate from qsnap.utils.nbd, got {fn.__module__}"
            )

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
