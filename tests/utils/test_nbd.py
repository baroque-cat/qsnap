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

from qsnap.utils.nbd import (
    is_libvirt_new_enough,
    is_vm_running,
    nbd_full_export,
    write_backup_xml,
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
