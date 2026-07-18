"""Integration tests for ConfigFacade parsing of TOML configurations.

All tests in this module are marked ``@pytest.mark.integration``.
They parse real TOML files with ``ConfigFacade`` and verify that
configuration warnings, downgrades, and option inheritance work
correctly.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from qsnap.config.facade import ConfigFacade

# ──────────────────────────────────────────────────────────────────────
# Test 1: Bitmap + hash warns and downgrades to metadata
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_bitmap_hash_warns_and_downgrades(caplog: pytest.LogCaptureFixture):
    """Verify that ConfigFacade logs a WARNING and downgrades
    ``verify="hash"`` to ``"metadata"`` when ``incremental_mode="bitmap"``.

    Per design D8, bitmap mode (NBD-based) does not support hash
    verification because NBD-converted qcow2 files have different
    internal structure than the source snapshot.  ConfigFacade should
    warn and auto-downgrade.
    """
    toml_content = """\
[[vm]]
name = "test-vm-int"
base_image = "/var/lib/libvirt/images/test-vm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/test-vm"

[[vm.target]]
path = "/mnt/backup/test-vm"
incremental_mode = "bitmap"
verify = "hash"
compress = true
"""

    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-config-"))
    config_path = tmpdir / "test_bitmap_hash.toml"
    config_path.write_text(toml_content)

    try:
        # Capture logs at WARNING level.
        with caplog.at_level(logging.WARNING, logger="qsnap.config.facade"):
            facade = ConfigFacade(str(config_path))

        # Verify the WARNING was logged.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        verify_warnings = [
            r
            for r in warnings
            if "verify" in (r.getMessage() or "").lower()
            and "hash" in (r.getMessage() or "").lower()
        ]
        assert len(verify_warnings) >= 1, (
            f"Expected at least one WARNING about verify='hash' in bitmap mode. "
            f"Warnings logged: {[r.getMessage() for r in warnings]}"
        )

        warning_msg = verify_warnings[0].getMessage() or ""
        assert "downgrading" in warning_msg.lower() or "downgrade" in warning_msg.lower(), (
            f"WARNING should mention downgrading, got: {warning_msg!r}"
        )

        # Verify the target's verify mode was downgraded to "metadata".
        vms = facade.get_vms()
        assert len(vms) == 1, f"Expected 1 VM, got {len(vms)}"

        vm = vms[0]
        assert vm.name == "test-vm-int"
        assert len(vm.targets) == 1, f"Expected 1 target, got {len(vm.targets)}"

        target = vm.targets[0]
        assert target.verify == "metadata", (
            f"Expected verify='metadata' after downgrade, got verify={target.verify!r}"
        )
        assert target.incremental_mode == "bitmap", "incremental_mode should remain 'bitmap'"

    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)
