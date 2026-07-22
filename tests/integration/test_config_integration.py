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
# Test 1: Bitmap + hash is preserved (no downgrade)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_bitmap_hash_preserved(caplog: pytest.LogCaptureFixture):
    """Verify that ConfigFacade preserves ``verify="hash"`` for targets.

    Now ``verify_bitmap_incremental()`` supports chain-traversing
    ``qemu-img compare`` in hash/full tiers (with a live-source
    reliability caveat), so the explicit verify value is preserved
    and no downgrade warning is emitted.
    """
    toml_content = """\
[[vm]]
name = "test-vm-int"
base_image = "/var/lib/libvirt/images/test-vm.qcow2"
snapshot_dir = "/var/lib/libvirt/snapshots/test-vm"

[[vm.target]]
path = "/mnt/backup/test-vm"
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

        # Verify NO downgrade warning was emitted.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        downgrade_warnings = [
            r
            for r in warnings
            if "downgrading" in (r.getMessage() or "").lower()
            or "downgrade" in (r.getMessage() or "").lower()
        ]
        assert len(downgrade_warnings) == 0, (
            f"Expected no downgrade warnings for bitmap+hash (now supported). "
            f"Got: {[r.getMessage() for r in downgrade_warnings]}"
        )

        # Verify the target's verify mode was preserved as configured.
        vms = facade.get_vms()
        assert len(vms) == 1, f"Expected 1 VM, got {len(vms)}"

        vm = vms[0]
        assert vm.name == "test-vm-int"
        assert len(vm.targets) == 1, f"Expected 1 target, got {len(vm.targets)}"

        target = vm.targets[0]
        assert target.verify == "hash", (
            f"Expected verify='hash' preserved, got verify={target.verify!r}"
        )

    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)
