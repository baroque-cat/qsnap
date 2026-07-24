"""Tests for systemd unit files, example configuration, and PKGBUILD structure."""

from __future__ import annotations

import re
from pathlib import Path

from qsnap.config.facade import ConfigFacade

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PKGBUILD = PROJECT_ROOT / "PKGBUILD"
PYPROJECT_TOML = PROJECT_ROOT / "pyproject.toml"
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

    Per-VM: blockcommit_deep_verify.

    Per-target: backup_retry_max, backup_retry_base.
    """
    content = EXAMPLE_CONFIG.read_text()
    assert "auto_cleanup" in content
    assert "state_backup_count" in content
    assert "chain_verify_before_commit" in content
    assert "chain_verify_after_commit" in content
    assert "deep_check_schedule" in content
    assert "blockcommit_deep_verify" in content
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
        line for line in content.splitlines() if line.strip().startswith("OnCalendar=")
    ]
    assert oncalendar_line
    assert "Sun" in oncalendar_line[0]


def test_deep_check_timer_persistent_true():
    """The qsnap-check.timer has Persistent=true to catch up on
    missed runs after system downtime."""
    content = CHECK_TIMER_FILE.read_text()
    assert "Persistent=true" in content


# ── stall detection / oneshot service tests ─────────────────────────────


def test_qsnap_service_has_timeout_start_sec_zero():
    """The qsnap.service unit file contains TimeoutStartSec=0.

    Systemd's default timeout is disabled because qsnap uses stall
    detection (output-file-growth monitoring) for long-running data
    transfers.  A backup that is progressing correctly should never be
    killed by a fixed timeout.
    """
    content = SERVICE_FILE.read_text()
    assert "TimeoutStartSec=0" in content


def test_qsnap_service_is_oneshot():
    """The qsnap.service unit file declares Type=oneshot.

    qsnap runs as a one-shot pipeline: it takes snapshots, transfers
    backups, applies retention, and exits.  There is no long-running
    daemon to manage.
    """
    content = SERVICE_FILE.read_text()
    assert "Type=oneshot" in content


# ── PKGBUILD structural tests ──────────────────────────────────────────


def _read_pkgbuild() -> str:
    """Read the PKGBUILD file as text."""
    return PKGBUILD.read_text()


def _read_pyproject_toml() -> str:
    """Read pyproject.toml as text."""
    return PYPROJECT_TOML.read_text()


def _extract_pkgbuild_variable(content: str, name: str) -> str | None:
    """Extract a PKGBUILD variable value (e.g. pkgver=0.2.1 → '0.2.1')."""
    m = re.search(rf"^{name}=(.+)$", content, re.MULTILINE)
    return m.group(1) if m else None


def _extract_pkgbuild_array(content: str, name: str) -> list[str] | None:
    """Extract a PKGBUILD bash array (e.g. depends=('a' 'b') → ['a', 'b'])."""
    m = re.search(rf"^{name}=\((.*)\)$", content, re.MULTILINE)
    if not m:
        return None
    return [v.strip("'\"") for v in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(1)) for v in v if v]


def _extract_package_section(content: str) -> str:
    """Extract the body of the package() function from a PKGBUILD.

    Handles nested braces (e.g. ``${srcdir}``, ``${pkgdir}``) by
    counting brace depth from ``package() {`` to the matching ``}``.
    """
    start_m = re.search(r"package\(\)\s*\{", content)
    if not start_m:
        return ""
    start = start_m.end()  # just after the opening brace
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    return content[start : i - 1] if depth == 0 else ""


def test_pkgbuild_install_target_is_system_python():
    """PKGBUILD installs to system Python via ``pip install --prefix=/usr``.

    The ``package()`` function must NOT install into a virtual
    environment (no ``venv``, ``virtualenv``, or ``--user``).  It must
    use ``--prefix=/usr`` so that system-installed libnbd bindings are
    discoverable on ``sys.path``.
    """
    package_body = _extract_package_section(_read_pkgbuild())
    assert package_body != "", "PKGBUILD must contain a package() function"

    # Must install to system Python.
    assert "--prefix=/usr" in package_body or "--prefix=\"/usr\"" in package_body, (
        "package() must install with --prefix=/usr for system Python"
    )
    assert "pip install" in package_body, "package() must use pip install"

    # Must NOT install into a venv or with --user.
    # Only inspect non-comment, non-blank lines to avoid false
    # positives from documentation strings like "(not a venv)".
    code_lines = [
        line.strip() for line in package_body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code_text = "\n".join(code_lines)
    assert "python -m venv" not in code_text, (
        "package() must NOT use python -m venv (system install required)"
    )
    assert "virtualenv" not in code_text, (
        "package() must NOT use virtualenv (system install required)"
    )
    assert "--user" not in code_text, (
        "package() must NOT use --user (system install required)"
    )
    assert "--target" not in code_text, (
        "package() must NOT use --target (system install required)"
    )


def test_pkgbuild_pkgver_matches_pyproject():
    """``pkgver`` in PKGBUILD matches ``version`` in ``pyproject.toml``.

    These two values MUST stay synchronized.  The PKGBUILD comment
    (line 4) already documents this constraint — this test enforces it
    so that CI catches a desync.
    """
    pkgver = _extract_pkgbuild_variable(_read_pkgbuild(), "pkgver")
    assert pkgver is not None, "PKGBUILD must define pkgver"

    pyproject = _read_pyproject_toml()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert m is not None, "pyproject.toml must define a project.version"

    pyproject_version = m.group(1)
    assert pkgver == pyproject_version, (
        f"PKGBUILD pkgver ({pkgver}) does not match pyproject.toml version"
        f" ({pyproject_version})"
    )


def test_pkgbuild_depends_includes_required_packages():
    """All runtime dependencies are declared in the ``depends`` array.

    The required runtime packages are: python (>=3.11), libnbd, libvirt,
    qemu-utils.  Many of these are system packages (not PyPI) and are
    essential for the NBD pull-model, domain management, and image
    conversion.
    """
    depends = _extract_pkgbuild_array(_read_pkgbuild(), "depends")
    assert depends is not None, "PKGBUILD must define a depends array"

    # Collect bare package names (strip version constraints like >=3.11).
    bare_names = {re.split(r"[<>=!]", d)[0] for d in depends}

    required = {"python", "libnbd", "libvirt", "qemu-utils"}
    missing = required - bare_names
    assert not missing, (
        f"PKGBUILD depends array is missing required packages: {sorted(missing)}."
        f"  Found: {sorted(bare_names)}"
    )


def test_pkgbuild_installs_systemd_units():
    """Systemd units are installed to ``/usr/lib/systemd/system/``.

    The ``package()`` function must install all four unit files:
    qsnap.service, qsnap.timer, qsnap-check.service, qsnap-check.timer.
    Each must target the systemd system unit directory.
    """
    package_body = _extract_package_section(_read_pkgbuild())
    expected_units = [
        "qsnap.service",
        "qsnap.timer",
        "qsnap-check.service",
        "qsnap-check.timer",
    ]
    target_dir = "/usr/lib/systemd/system"

    for unit in expected_units:
        target_path = f"{target_dir}/{unit}"
        assert target_path in package_body, (
            f"package() must install {unit} to {target_dir}"
        )


def test_pkgbuild_installs_config_example():
    """The example config is installed to ``/etc/qsnap/``.

    The ``package()`` function must install ``qsnap.toml.example`` to
    ``/etc/qsnap/qsnap.toml.example`` so that new users can copy it as a
    starting point.
    """
    package_body = _extract_package_section(_read_pkgbuild())
    assert "/etc/qsnap/qsnap.toml.example" in package_body, (
        "package() must install qsnap.toml.example to /etc/qsnap/"
    )


def test_pkgbuild_creates_state_directory():
    """The state directory ``/var/lib/qsnap/state/`` is created with mode 755.

    The ``package()`` function must use ``install -d -m755`` (or
    ``mkdir -p -m 755``) to create the state directory so that qsnap
    can persist cross-run data (allocation sizes, timestamps, etc.).
    """
    package_body = _extract_package_section(_read_pkgbuild())

    state_dir = "/var/lib/qsnap/state"
    assert state_dir in package_body, (
        f"package() must create state directory {state_dir}"
    )

    # Verify the permissions: must be 755 (or 0755 in octal notation).
    assert "755" in package_body, (
        f"package() must set mode 755 on {state_dir}"
    )
