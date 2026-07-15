"""Tests for systemd unit files and example configuration."""

from __future__ import annotations

from pathlib import Path

from qsnap.config.facade import ConfigFacade

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_FILE = PROJECT_ROOT / "systemd" / "qsnap.service"
TIMER_FILE = PROJECT_ROOT / "systemd" / "qsnap.timer"
CHECK_SERVICE_FILE = PROJECT_ROOT / "systemd" / "qsnap-check.service"
CHECK_TIMER_FILE = PROJECT_ROOT / "systemd" / "qsnap-check.timer"
EXAMPLE_CONFIG = PROJECT_ROOT / "qsnap.toml.example"


def test_service_unit_execstart_runs_qsnap_run_with_config():
    """The service ExecStart line invokes qsnap with the run subcommand."""
    content = SERVICE_FILE.read_text()
    assert "qsnap" in content
    assert "run" in content
    assert "Type=oneshot" in content


def test_timer_unit_triggers_service_on_hourly_calendar():
    """The timer fires on an hourly calendar schedule."""
    content = TIMER_FILE.read_text()
    assert "OnCalendar=hourly" in content


def test_timer_unit_has_persistent_true():
    """The timer uses Persistent=true to catch up on missed runs."""
    content = TIMER_FILE.read_text()
    assert "Persistent=true" in content


def test_timer_unit_has_randomized_delay():
    """The timer includes a randomized delay to spread load."""
    content = TIMER_FILE.read_text()
    assert "RandomizedDelaySec=300" in content


def test_multiple_timer_instances_pattern_documented():
    """The service ExecStart uses -c flag, enabling multiple timer instances
    with different config files."""
    content = SERVICE_FILE.read_text()
    execstart_line = [line for line in content.splitlines() if line.startswith("ExecStart")]
    assert execstart_line
    assert "-c" in execstart_line[0]


def test_example_config_is_parseable_by_configfacade():
    """The example TOML config parses without error and defines at least one VM."""
    facade = ConfigFacade(EXAMPLE_CONFIG)
    vms = facade.get_vms()
    assert len(vms) >= 1


# ── example config documentation tests ──────────────────────────────────


def test_example_config_documents_preserve_min_fields():
    """The example config documents snapshot_preserve_min and
    target_preserve_min fields."""
    content = EXAMPLE_CONFIG.read_text()
    assert "snapshot_preserve_min" in content
    assert "target_preserve_min" in content


def test_example_config_documents_all_safety_fields():
    """The example config documents all fault-tolerance and safety fields.

    Global: auto_cleanup, state_backup_count, chain_verify_before_commit,
    chain_verify_after_commit, deep_check_schedule.

    Per-VM: blockcommit_deep_verify, snapshot_deep_verify.

    Per-target: backup_retry_max, backup_retry_base.
    """
    content = EXAMPLE_CONFIG.read_text()
    assert "auto_cleanup" in content
    assert "state_backup_count" in content
    assert "chain_verify_before_commit" in content
    assert "chain_verify_after_commit" in content
    assert "deep_check_schedule" in content
    assert "blockcommit_deep_verify" in content
    assert "snapshot_deep_verify" in content
    assert "backup_retry_max" in content
    assert "backup_retry_base" in content


# ── deep check timer tests ──────────────────────────────────────────────


def test_deep_check_timer_ships_with_correct_defaults():
    """The qsnap-check.timer unit file has correct default timer settings.

    - Weekly schedule (OnCalendar=Sun *-*-* 03:00:00)
    - Persistent=true for catching up on missed runs
    - RandomizedDelaySec=1800 to spread load
    - Unit=qsnap-check.service
    - WantedBy=timers.target
    """
    content = CHECK_TIMER_FILE.read_text()
    assert "OnCalendar=Sun *-*-* 03:00:00" in content
    assert "Persistent=true" in content
    assert "RandomizedDelaySec=1800" in content
    assert "Unit=qsnap-check.service" in content
    assert "WantedBy=timers.target" in content


def test_deep_check_service_uses_config_flag():
    """The qsnap-check.service uses Type=oneshot and the -c config flag.

    The -c flag ensures the service uses the same config file path and
    therefore the same lockfile path as the main qsnap service.
    """
    content = CHECK_SERVICE_FILE.read_text()
    assert "Type=oneshot" in content
    assert "ExecStart=/usr/bin/qsnap -c /etc/qsnap/qsnap.toml check --deep" in content


def test_deep_check_timer_weekly_schedule():
    """The qsnap-check.timer OnCalendar line contains 'Sun' for weekly
    scheduling."""
    content = CHECK_TIMER_FILE.read_text()
    oncalendar_line = [
        line for line in content.splitlines()
        if line.strip().startswith("OnCalendar=")
    ]
    assert oncalendar_line
    assert "Sun" in oncalendar_line[0]


def test_deep_check_timer_persistent_true():
    """The qsnap-check.timer has Persistent=true to catch up on
    missed runs after system downtime."""
    content = CHECK_TIMER_FILE.read_text()
    assert "Persistent=true" in content
