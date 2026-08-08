"""Unit tests for free-space estimation and gate helpers in qsnap.utils.space.

Tests cover ``estimate_full_size`` (sum of ``actual-size`` over the source
backing chain), ``estimate_incremental_size`` (active-layer ``actual-size``),
and ``check_free_space`` (``free >= estimate * factor + reserve``).  All
external calls go through :class:`MockShell` (mocked ``qemu-img info``) and
``shutil.disk_usage`` is patched — zero real I/O (TESTING.md unit rules;
design D5: undecidable estimates proceed with a warning, never block).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from qsnap.models.results import ShellResult
from qsnap.utils.space import (
    check_free_space,
    estimate_full_size,
    estimate_incremental_size,
    estimate_recovered_delta_size,
)

# ── estimate_full_size ───────────────────────────────────────────────────


def _json_result(payload: object) -> ShellResult:
    """A successful ShellResult carrying *payload* serialized as JSON."""
    return ShellResult(
        success=True,
        stdout=json.dumps(payload),
        stderr="",
        returncode=0,
        error=None,
    )


def test_estimate_full_size_sums_actual_size_over_chain(clean_shell) -> None:
    """FULL estimate = sum of ``actual-size`` over every chain element.

    ``qemu-img info --force-share --backing-chain --output=json`` returns a
    flat list of images; the estimate must be the sum of their ``actual-size``
    values (worst-case standalone copy size, design D5).
    """
    shell = clean_shell
    shell.expect("qemu-img info --force-share --backing-chain --output=json").returns(
        _json_result(
            [
                {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 1048576},
                {"filename": "/snaps/snap1.qcow2", "actual-size": 2097152},
                {"filename": "/snaps/snap2.qcow2", "actual-size": 3145728},
            ]
        )
    )

    estimate = estimate_full_size(shell, Path("/var/lib/libvirt/images/testvm.qcow2"))

    assert estimate == 1048576 + 2097152 + 3145728
    # Exactly one command, with the backing-chain flag and the source path.
    assert shell.call_history == [
        "qemu-img info --force-share --backing-chain --output=json "
        "/var/lib/libvirt/images/testvm.qcow2"
    ]


def test_estimate_full_size_skips_elements_without_actual_size(clean_shell) -> None:
    """Chain elements lacking ``actual-size`` are skipped, not fatal."""
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        _json_result(
            [
                {"filename": "/snaps/base.qcow2"},  # no actual-size key
                {"filename": "/snaps/overlay.qcow2", "actual-size": 512},
            ]
        )
    )

    assert estimate_full_size(shell, Path("/snaps/overlay.qcow2")) == 512


def test_estimate_full_size_shell_failure_returns_none(clean_shell, caplog) -> None:
    """``qemu-img info`` failure yields None; the probe failure logs at DEBUG only.

    The size-estimation probe is a ``check=True`` call (shell-abstraction
    probe rule): a failed command is an expected, non-error condition, so
    the failure must be logged at DEBUG level — never WARNING/ERROR
    (spec scenario "Size-estimation probes use check=True").  The
    undecidable estimate still returns ``None`` (design D5, never blocks).
    """
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: could not open disk image",
            returncode=1,
            error="qemu-img: could not open disk image",
        )
    )

    with caplog.at_level(logging.DEBUG, logger="qsnap.utils.space"):
        assert estimate_full_size(shell, Path("/snaps/overlay.qcow2")) is None

    space_records = [r for r in caplog.records if r.name == "qsnap.utils.space"]
    assert any(
        r.levelno == logging.DEBUG and "Cannot estimate FULL size" in r.message
        for r in space_records
    ), "probe failure must log at DEBUG level"
    assert not any(r.levelno in (logging.WARNING, logging.ERROR) for r in space_records), (
        "probe failure must NOT log at WARNING/ERROR level"
    )


def test_estimate_full_size_unparseable_json_returns_none(clean_shell) -> None:
    """Unparseable JSON output yields None (undecidable)."""
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        ShellResult(
            success=True,
            stdout="{ not json",
            stderr="",
            returncode=0,
            error=None,
        )
    )

    assert estimate_full_size(shell, Path("/snaps/overlay.qcow2")) is None


def test_estimate_full_size_zero_total_returns_none(clean_shell) -> None:
    """A chain with no usable ``actual-size`` values yields None."""
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        _json_result(
            [
                {"filename": "/snaps/base.qcow2"},
                {"filename": "/snaps/overlay.qcow2"},
            ]
        )
    )

    assert estimate_full_size(shell, Path("/snaps/overlay.qcow2")) is None


def test_estimate_full_size_probe_uses_check_true(clean_shell) -> None:
    """The FULL size-estimation probe passes ``check=True`` to ``shell.run()``.

    ``estimate_full_size`` is a probe: command failure is expected and
    handled by returning ``None``, so the ``shell.run()`` call for
    ``qemu-img info --backing-chain`` must carry ``check=True``
    (shell-abstraction spec scenario "Size-estimation probes use
    check=True") — otherwise ``SubprocessShell`` would log the expected
    failure at ERROR instead of DEBUG.
    """
    shell = clean_shell
    shell.expect("qemu-img info --force-share --backing-chain --output=json").returns(
        _json_result([{"filename": "/snaps/overlay.qcow2", "actual-size": 512}])
    )

    # Spy/wrapper on the mock shell to capture the run() call arguments.
    calls: list[tuple[list[str], int, bool]] = []
    original_run = shell.run

    def spy_run(cmd: list[str], timeout: int = 0, check: bool = False) -> ShellResult:
        calls.append((list(cmd), timeout, check))
        return original_run(cmd, timeout=timeout, check=check)

    shell.run = spy_run  # type: ignore[method-assign]

    assert estimate_full_size(shell, Path("/snaps/overlay.qcow2")) == 512

    assert len(calls) == 1, f"expected exactly one probe call, got {len(calls)}"
    cmd, timeout, check = calls[0]
    assert cmd[:2] == ["qemu-img", "info"]
    assert "--backing-chain" in cmd
    assert timeout == 30
    assert check is True


# ── estimate_incremental_size ─────────────────────────────────────────────


def test_estimate_incremental_size_active_layer_actual_size(clean_shell) -> None:
    """Incremental estimate = active-layer ``actual-size`` (no backing chain)."""
    shell = clean_shell
    shell.expect("qemu-img info --force-share --output=json").returns(
        _json_result({"filename": "/snaps/overlay.qcow2", "actual-size": 5242880})
    )

    estimate = estimate_incremental_size(shell, Path("/snaps/overlay.qcow2"))

    assert estimate == 5242880
    # The incremental estimate must NOT use --backing-chain (single layer).
    assert shell.call_history == ["qemu-img info --force-share --output=json /snaps/overlay.qcow2"]


def test_estimate_incremental_size_undecidable_returns_none(clean_shell) -> None:
    """Incremental estimation yields None on failure or missing actual-size."""
    # ── qemu-img info fails ──────────────────────────────────────────
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: error",
            returncode=1,
            error="qemu-img: error",
        )
    )
    assert estimate_incremental_size(shell, Path("/snaps/overlay.qcow2")) is None

    # ── unparseable JSON ─────────────────────────────────────────────
    shell = clean_shell
    shell.expect("qemu-img info").returns(
        ShellResult(success=True, stdout="not json", stderr="", returncode=0, error=None)
    )
    assert estimate_incremental_size(shell, Path("/snaps/overlay.qcow2")) is None

    # ── no actual-size in the payload ────────────────────────────────
    shell = clean_shell
    shell.expect("qemu-img info").returns(_json_result({"filename": "/snaps/overlay.qcow2"}))
    assert estimate_incremental_size(shell, Path("/snaps/overlay.qcow2")) is None


def test_estimate_incremental_size_probe_uses_check_true(clean_shell) -> None:
    """The incremental size-estimation probe passes ``check=True`` to ``shell.run()``.

    ``estimate_incremental_size`` is a probe: command failure is expected
    and handled by returning ``None``, so the ``shell.run()`` call for
    ``qemu-img info`` must carry ``check=True`` (shell-abstraction spec
    scenario "Size-estimation probes use check=True") — otherwise
    ``SubprocessShell`` would log the expected failure at ERROR instead
    of DEBUG.
    """
    shell = clean_shell
    shell.expect("qemu-img info --force-share --output=json").returns(
        _json_result({"filename": "/snaps/overlay.qcow2", "actual-size": 5242880})
    )

    # Spy/wrapper on the mock shell to capture the run() call arguments.
    calls: list[tuple[list[str], int, bool]] = []
    original_run = shell.run

    def spy_run(cmd: list[str], timeout: int = 0, check: bool = False) -> ShellResult:
        calls.append((list(cmd), timeout, check))
        return original_run(cmd, timeout=timeout, check=check)

    shell.run = spy_run  # type: ignore[method-assign]

    assert estimate_incremental_size(shell, Path("/snaps/overlay.qcow2")) == 5242880

    assert len(calls) == 1, f"expected exactly one probe call, got {len(calls)}"
    cmd, timeout, check = calls[0]
    assert cmd[:2] == ["qemu-img", "info"]
    assert "--backing-chain" not in cmd
    assert timeout == 30
    assert check is True


# ── estimate_recovered_delta_size ─────────────────────────────────────────


def test_estimate_recovered_delta_size_sums_copy_set(clean_shell) -> None:
    """Recovered-delta estimate = sum of ``actual-size`` over every layer
    of the copy set (bitmap-loss-recovery spec, size-estimation scenario
    "Estimate sums the copy set").

    Each layer is probed individually via ``qemu-img info --force-share
    --output=json`` (no ``--backing-chain`` — the copy set is bounded by
    state timestamps, not by the live chain).
    """
    shell = clean_shell
    layers = [
        Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
        Path("/var/lib/libvirt/snapshots/testvm/snap3.qcow2"),
    ]
    for layer, size in zip(layers, [1048576, 2097152, 3145728], strict=True):
        # Distinct pattern per layer (MockShell first-match-wins).
        shell.expect(f"qemu-img info --force-share --output=json {layer}").returns(
            _json_result({"filename": str(layer), "actual-size": size})
        )

    estimate = estimate_recovered_delta_size(shell, layers)

    assert estimate == 1048576 + 2097152 + 3145728
    # One probe per layer, in order, without --backing-chain.
    assert shell.call_history == [
        f"qemu-img info --force-share --output=json {layer}" for layer in layers
    ]


def test_estimate_recovered_delta_falls_back_to_full_on_unreadable_layer(
    clean_shell,
) -> None:
    """An unreadable copy-set layer falls back to the FULL chain-sum
    estimate of the topmost layer — a conservative upper bound that is
    always safe (spec scenario "Unreadable layer falls back to FULL
    estimate")."""
    shell = clean_shell
    layers = [
        Path("/var/lib/libvirt/snapshots/testvm/snap1.qcow2"),
        Path("/var/lib/libvirt/snapshots/testvm/snap2.qcow2"),
    ]

    # First layer probe fails (unreadable) → FULL fallback.
    shell.expect("qemu-img info --force-share --output=json").returns(
        ShellResult(
            success=False,
            stdout="",
            stderr="qemu-img: Could not open snap1.qcow2",
            returncode=1,
            error="qemu-img: Could not open snap1.qcow2",
        )
    )
    # Fallback: chain-sum over the topmost layer.
    shell.expect("qemu-img info --force-share --backing-chain --output=json").returns(
        _json_result(
            [
                {"filename": "/var/lib/libvirt/images/testvm.qcow2", "actual-size": 1048576},
                {
                    "filename": "/var/lib/libvirt/snapshots/testvm/snap2.qcow2",
                    "actual-size": 2097152,
                },
            ]
        )
    )

    estimate = estimate_recovered_delta_size(shell, layers)

    assert estimate == 1048576 + 2097152
    # The unreadable layer triggered the conservative fallback: the
    # backing-chain probe of the topmost layer was issued.
    assert any("--backing-chain" in cmd for cmd in shell.call_history)


def test_estimate_recovered_delta_empty_copy_set_returns_none(clean_shell) -> None:
    """An empty copy set yields None (undecidable — nothing to sum)."""
    assert estimate_recovered_delta_size(clean_shell, []) is None


# ── check_free_space ─────────────────────────────────────────────────────


def test_check_free_space_reserve_and_factor() -> None:
    """Gate passes only when ``free >= estimate * factor + reserve``.

    Spec scenario "Reserve and factor applied": with
    ``free_space_reserve = 1073741824`` and ``free_space_factor = 1.1`` the
    gate requires ``free >= estimate * 1.1 + 1073741824``.
    """
    estimate = 1_000_000_000
    reserve = 1073741824  # 1 GiB
    factor = 1.1
    required = int(estimate * factor) + reserve

    # Exactly enough free space → sufficient.
    with patch("qsnap.utils.space.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = SimpleNamespace(free=required)
        result = check_free_space(Path("/mnt/backup"), estimate, reserve=reserve, factor=factor)

    assert result.sufficient is True
    assert result.required == required
    assert result.estimate == estimate
    assert result.free_bytes == required
    assert result.error is None

    # One byte short → insufficient.
    with patch("qsnap.utils.space.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = SimpleNamespace(free=required - 1)
        result = check_free_space(Path("/mnt/backup"), estimate, reserve=reserve, factor=factor)

    assert result.sufficient is False
    assert result.required == required


def test_check_free_space_defaults_no_reserve_no_factor() -> None:
    """With defaults (reserve=0, factor=1.0) the gate is ``free >= estimate``."""
    with patch("qsnap.utils.space.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = SimpleNamespace(free=4096)
        result = check_free_space(Path("/mnt/backup"), 4096)

    assert result.sufficient is True
    assert result.required == 4096

    with patch("qsnap.utils.space.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = SimpleNamespace(free=4095)
        result = check_free_space(Path("/mnt/backup"), 4096)

    assert result.sufficient is False


def test_estimate_none_proceeds_with_warning() -> None:
    """An undecidable estimate (None) proceeds — gate not applied.

    ``check_free_space`` returns ``sufficient=True`` with an error marker so
    the caller can log a WARNING and proceed (spec scenario "Undecidable
    estimate proceeds with warning"; design D5 — never block on an
    undecidable estimate).
    """
    with patch("qsnap.utils.space.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = SimpleNamespace(free=12345)
        result = check_free_space(Path("/mnt/backup"), None)

    assert result.sufficient is True
    assert result.free_bytes == 12345
    assert result.estimate is None
    assert result.required is None
    assert result.error is not None
    assert "undecidable" in result.error


def test_check_free_space_disk_usage_failure_proceeds() -> None:
    """``shutil.disk_usage`` failure also proceeds (gate not applied)."""
    with patch(
        "qsnap.utils.space.shutil.disk_usage",
        side_effect=OSError("no such directory"),
    ):
        result = check_free_space(Path("/mnt/backup"), 1000)

    assert result.sufficient is True
    assert result.free_bytes == 0
    assert result.error is not None
    assert "gate not applied" in result.error
