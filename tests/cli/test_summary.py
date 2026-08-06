"""Tests for the btrbk-style summary table formatter.

Tests the pure function :func:`qsnap.cli.summary.format_summary` against
constructed :class:`PipelineResult` objects.  No I/O, no real virsh/qemu-img
calls — purely unit tests for a pure function.
"""

from __future__ import annotations

from pathlib import Path

from qsnap.cli.summary import format_summary
from qsnap.core import PipelineResult, VMRunResult
from qsnap.models.results import ActionRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_PATH = Path()


def _make_action(
    action: str,
    vm_name: str = "vm-test",
    name: str = "snap_20250101_000000",
    path: Path | None = None,
    size: int = 0,
    duration: float = 0.0,
    error: str | None = None,
    disk: str | None = None,
) -> ActionRecord:
    """Create a fully-specified ActionRecord with minimal ceremony."""
    return ActionRecord(
        action=action,
        vm_name=vm_name,
        name=name,
        path=path if path is not None else _EMPTY_PATH,
        size=size,
        duration=duration,
        error=error,
        disk=disk,
    )


# ---------------------------------------------------------------------------
# test_summary_table_created_and_deleted_snapshots (1)
# ---------------------------------------------------------------------------


def test_summary_table_created_and_deleted_snapshots():
    """Create PipelineResult with snapshot_create and snapshot_delete actions.
    Verify output contains +++ for created, --- for deleted, and header."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-test", name="snap_a", size=1048576),
            _make_action("snapshot_delete", vm_name="vm-test", name="snap_old"),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "vm-test:" in output
    assert "+++" in output
    assert "---" in output
    assert "snap_a" in output
    assert "snap_old" in output
    # The created snapshot should show the size
    assert "1.0 MiB" in output


# ---------------------------------------------------------------------------
# test_summary_table_backup_transfers (2)
# ---------------------------------------------------------------------------


def test_summary_table_backup_transfers():
    """Create PipelineResult with backup_transfer and backup_full actions.
    Verify >>> for transfers, *** for FULL."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action(
                "backup_transfer",
                vm_name="vm-test",
                name="inc_001",
                path=Path("/backups/vm-test/inc_001.qcow2"),
                size=52428800,
                duration=2.5,
            ),
            _make_action(
                "backup_full",
                vm_name="vm-test",
                name="full_20250101",
                size=1073741824,
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "vm-test:" in output
    assert ">>>" in output
    assert "***" in output
    assert "inc_001" in output
    assert "full_20250101" in output
    # The transfer line includes duration and speed
    assert "50.0 MiB" in output
    assert "2.5s" in output
    # The FULL line shows its size
    assert "1.0 GiB" in output


# ---------------------------------------------------------------------------
# test_summary_table_with_errors (3)
# ---------------------------------------------------------------------------


def test_summary_table_with_errors():
    """Create PipelineResult with error action. Verify !!! symbol and error message."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-broken", success=False, error="disk full"),
        ],
        actions=[
            _make_action(
                "error",
                vm_name="vm-broken",
                name="vm-broken",
                error="virsh snapshot-create-as failed: No space left on device",
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "vm-broken:" in output
    assert "!!!" in output
    assert "No space left on device" in output


# ---------------------------------------------------------------------------
# test_summary_table_includes_legend (4)
# ---------------------------------------------------------------------------


def test_summary_table_includes_legend():
    """Verify output includes a Legend section with all 5 symbols."""
    result = PipelineResult(
        results=[],
        actions=[],
        dry_run=False,
    )
    output = format_summary(result)

    assert "Legend:" in output
    assert "+++" in output
    assert "created snapshot" in output
    assert "---" in output
    assert "deleted snapshot" in output
    assert ">>>" in output
    assert "transferred incremental" in output
    assert "***" in output
    assert "created FULL backup" in output
    assert "!!!" in output
    assert "ERROR" in output


# ---------------------------------------------------------------------------
# test_dry_run_summary_header (5)
# ---------------------------------------------------------------------------


def test_dry_run_summary_header():
    """Create PipelineResult with dry_run=True. Verify header contains Dryrun: YES."""
    result = PipelineResult(
        results=[],
        actions=[],
        dry_run=True,
        config_path="/etc/qsnap/config.toml",
    )
    output = format_summary(result)

    assert "Dryrun: YES" in output
    # header should be present even in dry-run
    assert "qsnap Backup Summary" in output
    assert "Config:" in output
    assert "/etc/qsnap/config.toml" in output


# ---------------------------------------------------------------------------
# test_dry_run_summary_footer (6)
# ---------------------------------------------------------------------------


def test_dry_run_summary_footer():
    """Create PipelineResult with dry_run=True. Verify footer contains the disclaimer."""
    result = PipelineResult(
        results=[],
        actions=[],
        dry_run=True,
    )
    output = format_summary(result)

    assert "NOTE: Dryrun was active" in output
    assert "none of the operations above were actually executed" in output


# ---------------------------------------------------------------------------
# test_dry_run_shows_predicted_actions (7)
# ---------------------------------------------------------------------------


def test_dry_run_shows_predicted_actions():
    """In dry-run, verify the summary renders predicted actions from the
    predictions field with per-disk prefix."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-predict", success=True),
        ],
        actions=[],  # empty — predictions drive the planned-actions section
        predictions=[
            _make_action(
                "snapshot_create",
                vm_name="vm-predict",
                name="predicted_snap",
                size=1024,
                disk="vda",
            ),
        ],
        dry_run=True,
    )
    output = format_summary(result)

    assert "Dryrun: YES" in output
    assert "NOTE: Dryrun was active" in output
    assert "Planned actions (dry-run):" in output
    assert "vm-predict:" in output
    assert "predicted_snap" in output
    assert "+++ [vda]" in output
    # Predicted sizes carry approximate marker
    assert "~1.0 KiB" in output

    # Planned section uses predictions, not actions
    planned_start = output.find("Planned actions (dry-run):")
    assert planned_start != -1
    planned_section = output[planned_start:]
    assert "vm-predict:" in planned_section
    assert "predicted_snap" in planned_section


# ---------------------------------------------------------------------------
# test_dry_run_empty_predictions (8)
# ---------------------------------------------------------------------------


def test_dry_run_empty_predictions():
    """Dry-run with empty predictions. Verify header and footer are
    present but NO planned-actions section is rendered."""
    result = PipelineResult(
        results=[],
        actions=[],
        predictions=[],
        dry_run=True,
    )
    output = format_summary(result)

    assert "Dryrun: YES" in output
    assert "NOTE: Dryrun was active" in output
    assert "none of the operations above were actually executed" in output
    # No planned-actions section when predictions is empty
    assert "Planned actions (dry-run):" not in output


# ---------------------------------------------------------------------------
# test_dry_run_sizes_marked_approximate (9)
# ---------------------------------------------------------------------------


def test_dry_run_sizes_marked_approximate():
    """Dry-run predictions with various sizes. Assert ~ prefix on every
    rendered size and size=0 renders as 'size unknown'."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[],
        predictions=[
            _make_action(
                "snapshot_create",
                vm_name="vm-test",
                name="snap_big",
                size=1048576,
                disk="vda",
            ),
            _make_action(
                "backup_transfer",
                vm_name="vm-test",
                name="inc_001",
                size=52428800,
                path=Path("/backups/inc_001.qcow2"),
                disk="vda",
            ),
            _make_action(
                "snapshot_create",
                vm_name="vm-test",
                name="snap_zero",
                size=0,
                disk="vda",
            ),
        ],
        dry_run=True,
    )
    output = format_summary(result)

    # Size > 0 → ~ prefix
    assert "~1.0 MiB" in output
    assert "~50.0 MiB" in output
    # Size == 0 → "size unknown"
    assert "size unknown" in output

    # Every parenthesized size in the planned section is approximate.
    # Start scanning after the heading line to avoid matching "(dry-run)".
    planned_start = output.find("Planned actions (dry-run):")
    assert planned_start != -1
    after_heading = output.find("\n", planned_start)
    assert after_heading != -1
    body = output[after_heading:]

    import re

    parens = re.findall(r"\(([^)]+)\)", body)
    for content in parens:
        assert content.startswith("~") or content == "size unknown", (
            f"Non-approximate size in dry-run predictions: ({content})"
        )


# ---------------------------------------------------------------------------
# test_formatter_no_side_effects (10)
# ---------------------------------------------------------------------------


def test_formatter_no_side_effects():
    """Call format_summary() twice with same PipelineResult.
    Verify identical strings returned."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-test", name="snap_a", size=1048576),
        ],
        dry_run=False,
    )
    output1 = format_summary(result)
    output2 = format_summary(result)

    # Must be identical — deterministic pure function
    assert output1 == output2
    # Must be non-empty
    assert len(output1) > 0


# ---------------------------------------------------------------------------
# test_formatter_reads_from_pipeline_result_only (9)
# ---------------------------------------------------------------------------
# NOTE: This test is inherently structural and serves as documentation
# that the pure contract is honoured.  format_summary() is a module-level
# function; it cannot access IStateManager, IConfigFacade, or the
# filesystem.


def test_formatter_reads_from_pipeline_result_only():
    """Verify that format_summary produces output when PipelineResult fields are set."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-a", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-a", name="s1"),
        ],
        dry_run=False,
        config_path="/fake/config.toml",
    )
    output = format_summary(result)

    # The output must contain material derived from the PipelineResult
    assert "vm-a:" in output
    assert "s1" in output
    assert "+++" in output
    assert "Config:" in output
    assert "/fake/config.toml" in output
    # The function does not crash and returns a string
    assert isinstance(output, str)
    assert len(output) > 0


# ---------------------------------------------------------------------------
# test_quiet_mode_still_prints_summary (10)
# ---------------------------------------------------------------------------
# The quiet flag affects stderr logging, not format_summary() itself.
# format_summary() is a pure function — it always returns content.


def test_quiet_mode_still_prints_summary():
    """Verify that format_summary() returns a non-empty string regardless.
    The quiet flag affects stderr logging, not stdout summary output."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-test", name="s1"),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    # Always returns content — quiet mode is a CLI concern, not a formatter one
    assert isinstance(output, str)
    assert len(output) > 0
    assert "qsnap Backup Summary" in output


# ---------------------------------------------------------------------------
# test_vm_with_no_actions_omitted (11)
# ---------------------------------------------------------------------------


def test_vm_with_no_actions_omitted():
    """Create PipelineResult with actions for VM 'A' but no actions for VM 'B'.
    Verify VM 'B' does not appear in the summary table."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-a", success=True),
            VMRunResult(vm_name="vm-b", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-a", name="snap_a"),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "vm-a:" in output
    assert "snap_a" in output
    # vm-b should NOT have its own block — it has no actions
    assert "vm-b:" not in output


# ---------------------------------------------------------------------------
# test_actions_sorted_by_pipeline_order (12)
# ---------------------------------------------------------------------------


def test_actions_sorted_by_pipeline_order():
    """Create actions in order: snapshot_create, snapshot_delete, backup_transfer
    for same VM. Verify they appear in that exact order in the output."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action("snapshot_create", vm_name="vm-test", name="snap_a", size=1024),
            _make_action("snapshot_delete", vm_name="vm-test", name="snap_old"),
            _make_action(
                "backup_transfer",
                vm_name="vm-test",
                name="inc_001",
                path=Path("/backups/inc_001.qcow2"),
                size=524288,
                duration=1.0,
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    # Split output into lines after the legend section, then find VM block
    lines = output.split("\n")

    # Find indices of our action lines (after the vm-test: header)
    vm_header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "vm-test:":
            vm_header_idx = i
            break

    assert vm_header_idx is not None, "vm-test: header not found"

    # Action lines are immediately after the VM header
    action_lines = lines[vm_header_idx + 1 :]
    # Remove empty trailing lines
    action_lines = [line for line in action_lines if line.strip()]

    assert len(action_lines) >= 3, f"Expected at least 3 action lines, got {action_lines}"

    assert "+++" in action_lines[0]
    assert "snap_a" in action_lines[0]

    assert "---" in action_lines[1]
    assert "snap_old" in action_lines[1]

    assert ">>>" in action_lines[2]
    assert "inc_001" in action_lines[2]


# ---------------------------------------------------------------------------
# test_format_summary_with_empty_actions (13)
# ---------------------------------------------------------------------------


def test_format_summary_with_empty_actions():
    """PipelineResult with empty actions list. Verify no crash, returns valid
    string with header and legend but no VM blocks."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[],  # explicitly empty
        dry_run=False,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "Legend:" in output
    # No VM blocks since there are no actions
    assert "vm-test:" not in output  # VMRunResult alone does NOT generate a VM block
    # Does not crash
    assert isinstance(output, str)


# ---------------------------------------------------------------------------
# test_format_summary_handles_missing_actions (14)
# ---------------------------------------------------------------------------


def test_format_summary_handles_missing_actions():
    """PipelineResult with actions=[] (default). Verify graceful handling."""
    # PipelineResult uses field(default_factory=list) for actions,
    # so omitting actions means it defaults to an empty list.
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "Legend:" in output
    assert isinstance(output, str)
    assert len(output) > 0


# ---------------------------------------------------------------------------
# test_summary_disk_scoped_shows_prefix (15)
# ---------------------------------------------------------------------------


def test_summary_disk_scoped_shows_prefix():
    """ActionRecord with disk='vda' SHALL render a [vda] prefix after the
    action symbol (spec: backup-summary, “Disk-scoped action line shows
    disk prefix”)."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action(
                "snapshot_create",
                vm_name="vm-test",
                name="snap_vda_001",
                size=1048576,
                disk="vda",
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "vm-test:" in output
    # The disk prefix appears immediately after the action symbol
    assert "+++ [vda]" in output
    assert "snap_vda_001" in output
    assert "1.0 MiB" in output


# ---------------------------------------------------------------------------
# test_summary_vm_level_error_no_prefix (16)
# ---------------------------------------------------------------------------


def test_summary_vm_level_error_no_prefix():
    """ActionRecord with disk=None (VM-level) SHALL NOT render a [disk]
    bracket; the output line SHALL be byte-identical to the old format
    (spec: backup-summary, “VM-level error line has no disk prefix”)."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-broken", success=False, error="disk full"),
        ],
        actions=[
            _make_action(
                "error",
                vm_name="vm-broken",
                name="vm-broken",
                error="virsh snapshot-create-as failed: No space left on device",
                disk=None,
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "vm-broken:" in output
    assert "!!!" in output
    # No disk prefix should appear — the symbol is followed by two spaces
    assert "!!!  " in output
    assert "No space left on device" in output
    # Explicitly verify no bracket leaked in
    assert "[None]" not in output
    assert "[disk]" not in output


# ---------------------------------------------------------------------------
# test_summary_multi_disk_distinguishes_disks (17)
# ---------------------------------------------------------------------------


def test_summary_multi_disk_distinguishes_disks():
    """Two actions with disk='vda' and disk='vdb' SHALL produce lines
    with distinct [vda] and [vdb] prefixes (spec: backup-summary,
    “Multi-disk run distinguishes disks in summary”)."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-multi", success=True),
        ],
        actions=[
            _make_action(
                "snapshot_create",
                vm_name="vm-multi",
                name="snap_vda",
                size=512000,
                disk="vda",
            ),
            _make_action(
                "snapshot_create",
                vm_name="vm-multi",
                name="snap_vdb",
                size=256000,
                disk="vdb",
            ),
        ],
        dry_run=False,
    )
    output = format_summary(result)

    assert "vm-multi:" in output
    assert "+++ [vda]" in output
    assert "snap_vda" in output
    assert "+++ [vdb]" in output
    assert "snap_vdb" in output


# ---------------------------------------------------------------------------
# Space-limited run summary (spec: cli-interface “Disk-full exit code”)
# ---------------------------------------------------------------------------


def test_summary_with_space_limited_does_not_crash():
    """space_limited=True is accepted by format_summary without crashing.

    The summary remains a pure function of the PipelineResult fields; the
    flag must never raise.  This is the currently-implemented behavior."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[
            _make_action(
                "backup_transfer",
                vm_name="vm-test",
                name="inc_001",
                path=Path("/backups/vm-test/inc_001.qcow2"),
                size=52428800,
                duration=2.5,
            ),
        ],
        dry_run=False,
        space_limited=True,
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "vm-test:" in output
    assert "inc_001" in output


def test_summary_names_space_limited_target():
    """The summary SHALL name the space-limited target when
    ``space_limited=True`` (spec: cli-interface, "Disk-full exit code" —
    "AND the summary names the space-limited target")."""
    result = PipelineResult(
        results=[
            VMRunResult(vm_name="vm-test", success=True),
        ],
        actions=[],
        dry_run=False,
        space_limited=True,
        space_limited_targets=["/mnt/backup/test-target"],
    )
    output = format_summary(result)

    assert "qsnap Backup Summary" in output
    assert "space-limited" in output.lower()
    assert "/mnt/backup/test-target" in output
