"""Integration tests for ConfigFacade parsing of TOML configurations.
All tests in this module are marked ``@pytest.mark.integration``.
They parse real TOML files with ``ConfigFacade`` and verify that
configuration warnings, downgrades, and option inheritance work
correctly.

NOTE: The ``"hash"`` and ``"full"`` verify modes have been unified into
``"compare"``.  ``ConfigFacade`` translates ``"hash"`` → ``"compare"``
with a deprecation warning.

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
# Test 1: Bitmap + hash is translated to compare (deprecated, dignified)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_bitmap_hash_preserved(caplog: pytest.LogCaptureFixture):
    """Verify that ConfigFacade translates ``verify="hash"`` → ``"compare"``
    with a deprecation warning.

    The ``"hash"`` and ``"full"`` verify modes have been unified into
    ``"compare"`` (chain-traversing ``qemu-img compare``).  ConfigFacade
    accepts ``"hash"`` as valid input and translates it to ``"compare"``
    with a WARNING.
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

        # Verify a deprecation/dignified translation warning was emitted.
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        deprecation_warnings = [
            r
            for r in warnings
            if "deprecated" in (r.getMessage() or "").lower()
            or "treating as" in (r.getMessage() or "").lower()
        ]
        # At least one deprecation warning for hash→compare translation.
        assert len(deprecation_warnings) >= 1, (
            f"Expected deprecation warning for verify='hash' → 'compare' translation. "
            f"Got: {[r.getMessage() for r in warnings]}"
        )

        # Verify the target's verify mode was translated to "compare".
        vms = facade.get_vms()
        assert len(vms) == 1, f"Expected 1 VM, got {len(vms)}"

        vm = vms[0]
        assert vm.name == "test-vm-int"
        assert len(vm.targets) == 1, f"Expected 1 target, got {len(vm.targets)}"

        target = vm.targets[0]
        # "hash" is translated to "compare" by the facade.
        assert target.verify == "compare", (
            f"Expected verify='hash' translated to 'compare', got verify={target.verify!r}"
        )

    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)
