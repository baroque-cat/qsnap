"""Tests for strict unknown-key rejection in ``ConfigFacade`` (design D3).

Covers the ``config-unknown-keys-unit`` (G2) group of the
``vm-level-backup-engine-options`` change:

- Unknown keys at each table level (``[global]``, ``[[vm]]``,
  ``[[vm.disk]]``, ``[[vm.target]]``) raise ``ConfigError`` naming the
  offending key (and, where applicable, the VM).
- Cross-level hints point the user at the level where the key belongs.
- Deprecated keys remain tolerated (warn-and-ignore), and every fixture
  config still parses without unknown-key errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.config.facade import ConfigError, ConfigFacade

FIXTURES = Path(__file__).parent.parent / "fixtures" / "configs"


def _write_config(tmp_path: Path, body: str) -> Path:
    """Write *body* to a fresh TOML file under *tmp_path* and return its path."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(body)
    return config_file


def _valid_vm_config(
    extra_vm_lines: str = "",
    extra_target_lines: str = "",
) -> str:
    """Return a fully valid TOML config for VM ``web01``.

    *extra_vm_lines* are injected into the ``[[vm]]`` table and
    *extra_target_lines* into the ``[[vm.target]]`` table, so tests can
    plant a single offending key without repeating the boilerplate.
    """
    return (
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        f"{extra_vm_lines}"
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/web01"\n'
        f"{extra_target_lines}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Unknown keys at each table level (design D3)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_unknown_vm_level_key_raises(tmp_path: Path) -> None:
    """A misspelled VM-level key (``compresion_type``) raises ConfigError
    naming both the VM and the offending key."""
    config_file = _write_config(
        tmp_path,
        _valid_vm_config(extra_vm_lines='compresion_type = "zlib"\n'),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "web01" in message, message
    assert "compresion_type" in message, message


@pytest.mark.unit
def test_unknown_target_level_key_raises(tmp_path: Path) -> None:
    """An unknown target-level key (``parallel`` — typo of
    ``convert_parallel``) raises ConfigError naming the key."""
    config_file = _write_config(
        tmp_path,
        _valid_vm_config(extra_target_lines="  parallel = 8\n"),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "parallel" in message, message
    assert "Unknown key" in message, message


@pytest.mark.unit
def test_unknown_global_level_key_raises(tmp_path: Path) -> None:
    """A misspelled global key (``compresss``) raises ConfigError naming it."""
    config_file = _write_config(
        tmp_path,
        "compresss = true\n\n" + _valid_vm_config(),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "compresss" in message, message
    assert "Unknown key" in message, message


@pytest.mark.unit
def test_unknown_disk_level_key_raises(tmp_path: Path) -> None:
    """A disk-level key ``base`` (typo for ``base_image``) raises
    ConfigError naming the key and the VM."""
    config_file = _write_config(
        tmp_path,
        "[[vm]]\n"
        'name = "web01"\n'
        'snapshot_dir = "/var/lib/libvirt/snapshots/web01"\n'
        "\n"
        "  [[vm.disk]]\n"
        '  target = "vda"\n'
        '  base_image = "/var/lib/libvirt/images/web01.qcow2"\n'
        '  base = "/images/web01.qcow2"\n'
        "\n"
        "  [[vm.target]]\n"
        '  path = "/mnt/backup/web01"\n',
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "Unknown key" in message, message
    assert "base" in message, message
    assert "web01" in message, message


# ──────────────────────────────────────────────────────────────────────────
# Cross-level hints (design D3)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_unknown_key_hint_points_to_correct_level(tmp_path: Path) -> None:
    """A VM-level key that only exists at target level (``backup_retry_max``)
    raises ConfigError whose hint points to ``[[vm.target]]`` and whose
    table label names the VM."""
    config_file = _write_config(
        tmp_path,
        _valid_vm_config(extra_vm_lines="backup_retry_max = 5\n"),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "web01" in message, message
    assert "backup_retry_max" in message, message
    assert "did you mean to set it in [[vm.target]]?" in message, message


@pytest.mark.unit
def test_global_verify_key_hints_vm_or_target(tmp_path: Path) -> None:
    """``verify`` has no global key — a top-level ``verify = ...`` raises
    ConfigError whose cross-level hint points at ``[[vm]]`` or
    ``[[vm.target]]``."""
    config_file = _write_config(
        tmp_path,
        'verify = "compare"\n\n' + _valid_vm_config(),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConfigFacade(config_file)

    message = str(exc_info.value)
    assert "verify" in message, message
    assert "did you mean to set it in" in message, message
    assert "[[vm.target]]" in message or "[[vm]]" in message, message


# ──────────────────────────────────────────────────────────────────────────
# Deprecated-key tolerance and fixture regression scan
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_deprecated_keys_remain_tolerated() -> None:
    """deprecated_fields.toml (snapshot_preserve, target_preserve,
    incremental, full_every, full_compress, verify=\"hash\") still parses
    without raising ConfigError."""
    facade = ConfigFacade(FIXTURES / "deprecated_fields.toml")

    assert len(facade.get_vms()) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "config_path",
    sorted(FIXTURES.glob("*.toml")),
    ids=lambda path: path.name,
)
def test_all_fixture_configs_parse_without_unknown_key_errors(
    config_path: Path,
) -> None:
    """No fixture config triggers the unknown-key rejection path.

    Some fixtures (invalid.toml, invalid_free_space_check.toml,
    low_free_space_factor.toml, negative_free_space_reserve.toml) are
    intentionally invalid and raise ConfigError for other reasons — only
    unknown-key rejection is forbidden here.
    """
    try:
        ConfigFacade(config_path)
    except ConfigError as exc:
        message = str(exc)
        assert "Unknown key" not in message, (
            f"{config_path.name} triggered unknown-key rejection:\n{message}"
        )
