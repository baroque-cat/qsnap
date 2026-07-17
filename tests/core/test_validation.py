"""Tests for Core._validate_environment() pre-flight checks.

Covers:
- All validation checks pass → CheckResult(status="ok").
- Individual check failures: snapshot_dir missing, virsh not in PATH,
  VM not defined in libvirt, rsync not found (hard error, design D3).
- rsync check always runs (no longer conditional on rate_limit).
- Pipeline-level behaviour: validation passes → pipeline continues;
  validation fails → VMRunResult(success=False).
- ondemand mode with no reachable target → snapshot skipped (validation
  still passes).
- always mode with validation failure → VMRunResult(success=False).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from qsnap.core import Core, VMRunResult
from qsnap.models.results import CheckResult, ShellResult
from tests.mocks import MockConfigFacade, MockShell

_OK = ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
_FAIL = ShellResult(success=False, stdout="", stderr="", returncode=1, error="not found")


def _override(shell: MockShell, pattern: str, result: ShellResult) -> None:
    """Replace an existing MockShell expectation for *pattern* with *result*.

    MockShell matches expectations in insertion order (first match wins).
    To override a default success expectation, we remove the old entry
    and append a new one so it becomes the sole match for that pattern.
    """
    shell._expectations = [e for e in shell._expectations if e.pattern != pattern]
    shell.expect(pattern).returns(result)


# ── test_validate_environment_all_pass ────────────────────────────────────


def test_validate_environment_all_pass(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """All shell checks succeed → CheckResult(status="ok")."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert isinstance(result, CheckResult)
    assert result.status == "ok"
    assert result.broken_snapshots == []
    assert result.vm_name == "testvm"


# ── test_validate_environment_snapshot_dir_missing ─────────────────────────


def test_validate_environment_snapshot_dir_missing(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``test -d`` fails → CheckResult(status="validation_failed")."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("snapshot_dir not found" in b for b in result.broken_snapshots)


# ── test_validate_environment_virsh_not_in_path ───────────────────────────


def test_validate_environment_virsh_not_in_path(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``which virsh`` fails → validation_failed with 'virsh not in PATH'."""
    _override(mock_shell, "which virsh", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("virsh not in PATH" in b for b in result.broken_snapshots)


# ── test_validate_environment_vm_not_defined ──────────────────────────────


def test_validate_environment_vm_not_defined(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``virsh dominfo`` fails → validation_failed with 'VM not defined'."""
    _override(mock_shell, "virsh dominfo", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    assert result.status == "validation_failed"
    assert any("VM not defined" in b for b in result.broken_snapshots)


# ── test_rsync_available_validation_passes ────────────────────────────────


def test_rsync_available_validation_passes(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """``which rsync`` succeeds → validation passes, no warnings.

    rsync check is always run (design D3).  When rsync is available,
    validation passes with status ``"ok"`` and no broken snapshots.
    """
    target = make_target(rate_limit="100M")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with caplog.at_level(logging.WARNING, logger="qsnap.core"):
        result = core._validate_environment(vm)

    assert result.status == "ok"
    assert result.broken_snapshots == []
    assert not any("rsync not found" in r.message for r in caplog.records)


# ── test_rsync_unavailable_pipeline_aborts ─────────────────────────────


def test_rsync_unavailable_pipeline_aborts(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``which rsync`` fails → validation_failed (hard error, not a warning).

    rsync is now a hard requirement (design D3).  Missing rsync causes
    ``validation_failed`` status and adds "rsync not found" to the
    broken list — the pipeline will not proceed.
    """
    _override(mock_shell, "which rsync", _FAIL)
    target = make_target(rate_limit="100M")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core._validate_environment(vm)

    # Hard error: validation fails.
    assert result.status == "validation_failed"
    assert any("rsync not found" in b for b in result.broken_snapshots)


# ── test_rsync_check_always_runs_regardless_of_rate_limit ─────────────


def test_rsync_check_always_runs_regardless_of_rate_limit(
    make_vm_config,
    make_target,
    mock_factory,
    mock_state,
    mock_shell,
):
    """rate_limit='no' → ``which rsync`` still runs (always checked).

    rsync availability is now a hard requirement (design D3).  The check
    runs unconditionally — even when every target has ``rate_limit == "no"``,
    ``which rsync`` must be invoked.
    """
    target = make_target(rate_limit="no")
    vm = make_vm_config(name="testvm", targets=[target])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(mock_shell, "run", wraps=mock_shell.run) as run_spy:
        result = core._validate_environment(vm)

    # Validation passes (rsync is available in mock_shell fixture).
    assert result.status == "ok"
    assert result.broken_snapshots == []

    # ``which rsync`` WAS called — the check always runs.
    rsync_calls = [call for call in run_spy.call_args_list if call.args and "rsync" in call.args[0]]
    assert len(rsync_calls) > 0, "which rsync must always run regardless of rate_limit"


# ── test_validate_environment_ondemand_target_missing_skipped ──────────────


def test_validate_environment_ondemand_target_missing_skipped(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """ondemand mode, no reachable target → validation passes, snapshot skipped.

    The pipeline should still pass environment validation, but because no
    target directory exists, the snapshot is skipped (ondemand semantics).
    """
    vm = make_vm_config(
        name="testvm",
        snapshot_create="ondemand",
        targets=[],
    )
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called (pipeline did not short-circuit).
    assert validate_spy.called

    # Snapshot was NOT created (ondemand with no reachable target).
    assert not create_spy.called

    # Pipeline succeeded — validation passed (no "validation failed" error).
    assert result.success is True
    assert result.results[0].error is None


# ── test_validate_environment_always_mode_target_missing_error ─────────────


def test_validate_environment_always_mode_target_missing_error(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """always mode, validation fails → VMRunResult(success=False)."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm", snapshot_create="always")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    result = core.run()

    assert len(result.results) == 1
    vm_result = result.results[0]
    assert isinstance(vm_result, VMRunResult)
    assert vm_result.success is False
    assert vm_result.error is not None
    assert "snapshot_dir" in vm_result.error


# ── test_pipeline_continues_after_validation_pass ──────────────────────────


def test_pipeline_continues_after_validation_pass(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Validation passes → pipeline continues normally (snapshot created)."""
    vm = make_vm_config(name="testvm", snapshot_create="always", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called and passed (pipeline continued to snapshot).
    assert validate_spy.called

    # Pipeline continued — snapshot was created.
    assert create_spy.called

    # Pipeline succeeded.
    assert result.success is True


# ── test_pipeline_returns_failure_on_missing_snapshot_dir ──────────────────


def test_pipeline_returns_failure_on_missing_snapshot_dir(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """Validation fails (snapshot_dir missing) → VMRunResult(success=False,
    error contains 'validation failed')."""
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    snapshot_provider = mock_factory._snapshot_provider

    with (
        patch.object(
            core,
            "_validate_environment",
            wraps=core._validate_environment,
        ) as validate_spy,
        patch.object(
            snapshot_provider,
            "create",
            wraps=snapshot_provider.create,
        ) as create_spy,
    ):
        result = core.run()

    # Validation was called and failed (pipeline stopped).
    assert validate_spy.called

    # Snapshot was NOT created (validation stopped the pipeline).
    assert not create_spy.called

    # VMRunResult reflects failure.
    assert len(result.results) == 1
    assert result.results[0].success is False
    assert result.results[0].error is not None
    assert "snapshot_dir not found" in result.results[0].error


# ── Pre-flight cleanup: stale .tmp/.partial files ────────────────────────


def _fresh_shell_with_cleanup_defaults() -> MockShell:
    """Return a MockShell with cleanup expectations that return empty results.

    The generic ``find`` and ``rm`` patterns are at the END of the
    expectations list so that specific patterns inserted BEFORE them will
    be matched first.
    """
    shell = MockShell()
    shell.expect("rm").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    shell.expect("find").returns(
        ShellResult(success=True, stdout="", stderr="", returncode=0, error=None)
    )
    return shell


def _insert_specific_find(shell: MockShell, pattern: str, stdout: str) -> None:
    """Insert a specific ``find`` expectation at the front of *shell*."""
    exp = MockShell.__dict__["expect"](shell, pattern)
    exp.returns(ShellResult(success=True, stdout=stdout, stderr="", returncode=0, error=None))
    # Move the just-added expectation to the front
    shell._expectations.insert(0, shell._expectations.pop())


def test_preflight_cleanup_tmp_files_in_snapshot_dir_removed(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
):
    """``find`` returns a .tmp file in snapshot_dir → ``rm -f`` is called."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()
    _insert_specific_find(
        shell,
        r"snapshots/testvm.*\.tmp",
        "/var/lib/libvirt/snapshots/testvm/stale.tmp\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(shell, "run", wraps=shell.run) as run_spy:
        core._preflight_cleanup(vm)

    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 1
    assert "stale.tmp" in str(rm_calls[0].args)


def test_preflight_cleanup_tmp_files_in_target_dirs_removed(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
):
    """``find`` returns a .tmp file in a target dir → ``rm -f`` is called."""
    target = make_target(path="/mnt/backup/testvm")
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()
    _insert_specific_find(
        shell,
        r"backup/testvm.*\.tmp",
        "/mnt/backup/testvm/incomplete.tmp\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(shell, "run", wraps=shell.run) as run_spy:
        core._preflight_cleanup(vm)

    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 1
    assert "incomplete.tmp" in str(rm_calls[0].args)


# ── Pre-flight cleanup: stale NBD sockets ───────────────────────────────


def test_preflight_cleanup_stale_nbd_sockets_removed(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
):
    """``find /tmp`` returns a qsnap-backup-*.sock → ``rm -f`` called on it."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()
    _insert_specific_find(
        shell,
        r"qsnap-backup-.*\.sock",
        "/tmp/qsnap-backup-abc123.sock\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(shell, "run", wraps=shell.run) as run_spy:
        core._preflight_cleanup(vm)

    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 1
    assert "qsnap-backup-abc123.sock" in str(rm_calls[0].args)


# ── Pre-flight cleanup: no stale files → no action ───────────────────────


def test_preflight_cleanup_no_stale_files_no_action(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
):
    """No stale files found by any find → ``rm -f`` is never called."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()
    # All finds return empty — no inserts needed, defaults suffice.

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with patch.object(shell, "run", wraps=shell.run) as run_spy:
        core._preflight_cleanup(vm)

    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 0


# ── Pre-flight cleanup: orphan .qcow2 detection ──────────────────────────


def test_preflight_cleanup_orphan_snapshot_detected(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """Orphan .qcow2 in snapshot_dir → WARNING logged, file NOT deleted."""
    from datetime import datetime
    from pathlib import Path

    from qsnap.models.results import SnapshotInfo

    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    # Record a known snapshot whose path.name does NOT match the orphan
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.known_vda",
            path=Path("/var/lib/libvirt/snapshots/testvm/testvm.known_vda.qcow2"),
            timestamp=datetime(2025, 1, 1),
            allocation=1024,
        ),
    )

    shell = _fresh_shell_with_cleanup_defaults()
    _insert_specific_find(
        shell,
        r"snapshots/testvm.*\.qcow2",
        "/var/lib/libvirt/snapshots/testvm/testvm.20250101T120000.qcow2\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with caplog.at_level(logging.WARNING, logger="qsnap.core"):
        with patch.object(shell, "run", wraps=shell.run) as run_spy:
            core._preflight_cleanup(vm)

    # The orphan .qcow2 must NOT be deleted
    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 0  # no rm calls at all (no tmp/partial/sock either)

    # Orphan WARNING logged
    assert any(
        "Orphan snapshot file detected" in r.message and "testvm.20250101T120000.qcow2" in r.message
        for r in caplog.records
    ), f"Expected orphan warning, got: {[r.message for r in caplog.records]}"


def test_preflight_cleanup_non_matching_qcow2_not_orphan(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """A .qcow2 file that does NOT match the qsnap naming pattern is skipped."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()
    # File matches find pattern "testvm.*.qcow2" but NOT the regex
    # ^testvm\.\d{8}T\d{6}\.qcow2$
    _insert_specific_find(
        shell,
        r"snapshots/testvm.*\.qcow2",
        "/var/lib/libvirt/snapshots/testvm/testvm.backup.qcow2\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with caplog.at_level(logging.WARNING, logger="qsnap.core"):
        core._preflight_cleanup(vm)

    # No orphan WARNING — file doesn't match qsnap naming pattern
    orphan_warnings = [r for r in caplog.records if "Orphan" in r.message]
    assert len(orphan_warnings) == 0


def test_preflight_cleanup_all_snapshots_accounted_no_warning(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """All .qcow2 files are in state → no orphan WARNING, no deletion."""
    from datetime import datetime
    from pathlib import Path

    from qsnap.models.results import SnapshotInfo

    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    # Record a snapshot that matches the "found" .qcow2 file
    mock_state.record_snapshot(
        "testvm",
        SnapshotInfo(
            name="testvm.20250101T120000",
            path=Path("/var/lib/libvirt/snapshots/testvm/testvm.20250101T120000.qcow2"),
            timestamp=datetime(2025, 1, 1),
            allocation=1024,
        ),
    )

    shell = _fresh_shell_with_cleanup_defaults()
    _insert_specific_find(
        shell,
        r"snapshots/testvm.*\.qcow2",
        "/var/lib/libvirt/snapshots/testvm/testvm.20250101T120000.qcow2\n",
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with caplog.at_level(logging.WARNING, logger="qsnap.core"):
        with patch.object(shell, "run", wraps=shell.run) as run_spy:
            core._preflight_cleanup(vm)

    # No rm calls for .qcow2 files
    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 0

    # No orphan WARNING
    orphan_warnings = [r for r in caplog.records if "Orphan" in r.message]
    assert len(orphan_warnings) == 0


# ── Pre-flight cleanup: auto_cleanup disabled ────────────────────────────


def test_preflight_cleanup_auto_cleanup_disabled(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """``auto_cleanup=False`` → cleanup skipped, no ``find`` calls at all."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=False)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    shell = _fresh_shell_with_cleanup_defaults()

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    with caplog.at_level(logging.INFO, logger="qsnap.core"):
        with patch.object(shell, "run", wraps=shell.run) as run_spy:
            core._preflight_cleanup(vm)

    # No shell commands at all — cleanup was skipped immediately
    assert len(run_spy.call_args_list) == 0

    # INFO log about disabled cleanup
    info_messages = [r.message for r in caplog.records]
    assert any("auto_cleanup is disabled" in msg for msg in info_messages), (
        f"Expected 'auto_cleanup is disabled' in: {info_messages}"
    )


# ── validate_environment: cleanup integration ────────────────────────────


def test_validate_env_cleanup_before_main_checks(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``_validate_environment`` calls ``_preflight_cleanup`` before main checks."""
    vm = make_vm_config(name="testvm")
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(core, "_preflight_cleanup") as mock_cleanup:
        result = core._validate_environment(vm)

    # _preflight_cleanup was invoked (and before returning, checking succeeded)
    mock_cleanup.assert_called_once_with(vm)
    assert result.status == "ok"


def test_validate_env_cleanup_skipped_when_auto_cleanup_false(
    make_vm_config,
    make_global_config,
    mock_factory,
    mock_state,
    mock_shell,
):
    """``auto_cleanup=False`` → validation proceeds, ``_preflight_cleanup``
    still invoked (but does nothing), main checks still pass."""
    vm = make_vm_config(name="testvm")
    global_cfg = make_global_config(auto_cleanup=False)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )

    with patch.object(core, "_preflight_cleanup") as mock_cleanup:
        result = core._validate_environment(vm)

    # _preflight_cleanup was still called (step 0)
    mock_cleanup.assert_called_once_with(vm)

    # Main validation still passes — cleanup being disabled is not a failure
    assert result.status == "ok"


# ── Pre-flight cleanup: truncated .qcow2 detection on backup targets ──────


def test_preflight_cleanup_truncated_qcow2_detected_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """A non-FULL .qcow2 on target where ``qemu-img info`` fails → file
    is deleted and a WARNING is logged."""
    from pathlib import Path
    from unittest.mock import patch

    target_path = Path("/mnt/backup/testvm")
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    truncated_file = target_path / "snap-incomplete.qcow2"

    shell = _fresh_shell_with_cleanup_defaults()
    shell.expect_first(
        f"qemu-img info.*--output=json.*snap-incomplete\\.qcow2"
    ).returns(
        ShellResult(
            success=False, stdout="", stderr="corrupt", returncode=1, error="truncated"
        )
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    def _glob_side_effect(self, pattern):
        if self == target_path and pattern == "*.qcow2":
            return [truncated_file]
        return []

    with patch.object(Path, "glob", _glob_side_effect):
        with caplog.at_level(logging.WARNING, logger="qsnap.core"):
            with patch.object(shell, "run", wraps=shell.run) as run_spy:
                core._preflight_cleanup(vm)

    # Verify rm -f was called for the truncated file
    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 1, f"Expected 1 rm call, got {len(rm_calls)}"
    assert "snap-incomplete.qcow2" in str(rm_calls[0].args)

    # WARNING was logged for stale partial transfer
    assert any(
        "Stale partial transfer detected and deleted" in r.message
        for r in caplog.records
    ), f"Expected stale partial transfer warning, got: {[r.message for r in caplog.records]}"


def test_preflight_cleanup_valid_qcow2_not_deleted(
    make_vm_config,
    make_target,
    make_global_config,
    mock_factory,
    mock_state,
    caplog,
):
    """A non-FULL .qcow2 on target where ``qemu-img info`` succeeds → file
    is NOT deleted."""
    from pathlib import Path
    from unittest.mock import patch

    target_path = Path("/mnt/backup/testvm")
    target = make_target(path=str(target_path))
    vm = make_vm_config(name="testvm", targets=[target])
    global_cfg = make_global_config(auto_cleanup=True)
    config = MockConfigFacade(global_config=global_cfg, vms=[vm])

    valid_file = target_path / "snap-valid.qcow2"

    shell = _fresh_shell_with_cleanup_defaults()
    shell.expect_first(
        f"qemu-img info.*--output=json.*snap-valid\\.qcow2"
    ).returns(
        ShellResult(
            success=True,
            stdout='{"format": "qcow2", "virtual-size": 1073741824}',
            stderr="",
            returncode=0,
            error=None,
        )
    )

    core = Core(config=config, factory=mock_factory, state=mock_state, shell=shell)

    def _glob_side_effect(self, pattern):
        if self == target_path and pattern == "*.qcow2":
            return [valid_file]
        return []

    with patch.object(Path, "glob", _glob_side_effect):
        with caplog.at_level(logging.WARNING, logger="qsnap.core"):
            with patch.object(shell, "run", wraps=shell.run) as run_spy:
                core._preflight_cleanup(vm)

    # No rm calls at all — valid file not deleted, no stale tmp/partial/sockets found
    rm_calls = [
        c
        for c in run_spy.call_args_list
        if c.args and isinstance(c.args[0], list) and c.args[0][0] == "rm"
    ]
    assert len(rm_calls) == 0, (
        f"Expected 0 rm calls for valid qcow2, got {len(rm_calls)}: "
        f"{[c.args for c in rm_calls]}"
    )

    # No stale transfer warning
    stale_warnings = [
        r for r in caplog.records if "Stale partial transfer" in r.message
    ]
    assert len(stale_warnings) == 0, (
        f"Expected no stale warnings, got: {[r.message for r in stale_warnings]}"
    )


# ── test_dry_run_runs_validation_non_fatal_warnings ────────────────────────


def test_dry_run_runs_validation_non_fatal_warnings(
    make_vm_config,
    mock_factory,
    mock_state,
    mock_shell,
    caplog,
):
    """Dry-run mode → validation failures logged as WARNING, pipeline continues.

    In dry-run mode, ``_validate_environment()`` is always called (design D6).
    Validation failures are logged as WARNING (non-fatal), and the pipeline
    does NOT abort.  It continues to log planned actions
    (e.g. ``[dry-run] Would create snapshot``).
    """
    # Make validation fail by overriding snapshot_dir check.
    _override(mock_shell, "test -d", _FAIL)
    vm = make_vm_config(name="testvm", disks=["vda"])
    config = MockConfigFacade(vms=[vm])
    core = Core(
        config=config,
        factory=mock_factory,
        state=mock_state,
        shell=mock_shell,
    )
    core.dry_run = True

    caplog.set_level(logging.INFO)

    with patch.object(
        core, "_validate_environment", wraps=core._validate_environment
    ) as validate_spy:
        result = core.run()

    # Validation was called (always runs in dry-run mode — design D6).
    assert validate_spy.called

    # Validation failure is logged as WARNING in dry-run mode.
    assert any(
        "Environment validation failed" in r.message and "dry-run" in r.message
        for r in caplog.records
    ), f"Expected dry-run validation WARNING in: {[r.message for r in caplog.records]}"

    # Pipeline does NOT abort — snapshot dry-run log appears.
    assert "[dry-run] Would create snapshot" in caplog.text, (
        "Pipeline should continue after dry-run validation warning"
    )

    # Pipeline succeeds (dry-run failures do not cause pipeline failure).
    assert result.success is True
