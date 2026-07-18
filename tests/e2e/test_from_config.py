"""E2E test: full pipeline from a TOML config file.

This test runs the complete qsnap pipeline (snapshot → backup →
retention → cleanup) driven by a TOML config file, verifying that all
components work together in a real libvirt environment.

Marked ``@pytest.mark.e2e`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
def test_full_pipeline_from_config(e2e_vm):
    """Run the full qsnap pipeline from a TOML config file.

    Steps (placeholder — implement when libvirt test environment is
    available):
      1. Start the test VM (or leave stopped for direct-convert path).
      2. Run ``qsnap run --config <config_path>``.
      3. Verify a snapshot was created in ``snapshot_dir``.
      4. Verify a backup was transferred to ``target_dir``.
      5. Verify state was recorded correctly.
      6. Run ``qsnap run`` a second time — verify no duplicate work
         (onchange detection).
      7. Run ``qsnap check`` — verify backing chain integrity.
    """
    pytest.skip("Requires libvirt environment with test VM")
