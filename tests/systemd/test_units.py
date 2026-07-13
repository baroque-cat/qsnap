"""Tests for systemd unit files and example configuration."""

from __future__ import annotations

from pathlib import Path

from qsnap.config.facade import ConfigFacade

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_FILE = PROJECT_ROOT / "systemd" / "qsnap.service"
TIMER_FILE = PROJECT_ROOT / "systemd" / "qsnap.timer"
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
