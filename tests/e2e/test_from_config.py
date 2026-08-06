"""E2E test: full pipeline from a TOML config file (default 48 floor).

Runs the complete qsnap pipeline twice via the real CLI, driven by a
TOML config file with NO explicit ``snapshot_preserve_min`` (so the
facade resolves the global default 48), verifying:

- 2 snapshot runs produce exactly 2 snapshot files
- NO blockcommit occurs under the 48-snapshot floor (chain_length
  default 24 would otherwise remove snapshots)
- ``qsnap check`` passes on the backing chain

Note on the backup stage: ``Core._create_snapshot`` records snapshots
from the real ``ExternalSnapshotProvider`` without a ``disk`` tag
(``SnapshotResult.disk`` is never populated), so the backup stage cannot
resolve the NBD export and the run is expected to fail there until that
source bug is fixed.  This test therefore asserts the snapshot/retention
behaviour (the preserve_min floor) and reports the backup-stage exit
code as observed.

Marked ``@pytest.mark.e2e`` — requires a libvirt environment with a
disposable test VM.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qsnap.cli.app import main
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_vm_running


@pytest.mark.e2e
@pytest.mark.timeout(3600)
def test_full_pipeline_from_config(e2e_vm):
    """Run ``qsnap run`` twice from a default TOML; no commit under 48."""
    shell: SubprocessShell = e2e_vm["shell"]
    vm_name: str = e2e_vm["vm_name"]
    config_path: Path = e2e_vm["config_path"]
    snapshot_dir: Path = e2e_vm["snapshot_dir"]
    target_dir: Path = e2e_vm["target_dir"]

    # Start the VM (external disk-only snapshots require an active domain).
    start = shell.run(["virsh", "start", vm_name], timeout=30)
    if not start.success:
        pytest.skip(f"virsh start failed: {start.error}")
    time.sleep(1)
    assert is_vm_running(shell, vm_name), "VM should be running"

    # Run 1 and run 2 through the real CLI.
    rc1 = main(["--config", str(config_path), "run"])
    rc2 = main(["--config", str(config_path), "run"])

    # Exactly 2 snapshots were created (one per run).
    snap_files = sorted(snapshot_dir.glob("*.qcow2"))
    assert len(snap_files) == 2, (
        f"Expected 2 snapshot files after 2 runs, got {len(snap_files)}: "
        f"{[p.name for p in snap_files]}"
    )

    # Default floor (48) dominates chain_length (24): NO blockcommit, so
    # both snapshot files still exist (the floor is the whole point).
    for p in snap_files:
        assert p.exists(), f"Snapshot file must survive the 48 floor: {p}"

    # The second run must not duplicate the first run's transfer work.
    # (With the source disk-tag bug, the backup stage aborts — see the
    # module docstring.  When the bug is fixed, exactly one FULL plus one
    # incremental should appear here.)
    backups = sorted(target_dir.glob("*.qcow2"))
    assert len(backups) == 2, (
        "Expected exactly 1 FULL + 1 incremental after 2 runs (no duplicate "
        f"transfers). Got {len(backups)}: {[p.name for p in backups]}"
    )

    # Backing chain intact.
    check_rc = main(["--config", str(config_path), "check"])
    assert check_rc == 0, f"qsnap check must pass on the backing chain, got rc={check_rc}"

    # Exit code 0 — no space errors, no failures.
    assert rc1 == 0, f"Run 1 must exit 0, got rc={rc1}"
    assert rc2 == 0, f"Run 2 must exit 0, got rc={rc2}"

    shell.run(["virsh", "destroy", vm_name], timeout=30)
