"""Mock verification: MockShell implements IShell."""

from __future__ import annotations

from pathlib import Path

from qsnap.interfaces.shell import IShell
from qsnap.models.results import ShellResult
from tests.mocks.mock_shell import MockShell


def test_mock_shell_is_ishell():
    """MockShell passes isinstance against IShell and run() returns the
    preconfigured ShellResult."""
    mock_shell = MockShell()
    assert isinstance(mock_shell, IShell)

    # Configure an expectation and verify run() returns the expected result.
    expected = ShellResult(
        success=True,
        stdout="Domain snapshot created",
        stderr="",
        returncode=0,
        error=None,
    )
    mock_shell.expect("virsh.*snapshot-create").returns(expected)

    result = mock_shell.run(
        ["virsh", "snapshot-create-as", "testvm", "snap1"],
        timeout=30,
    )
    assert result is expected
    assert result.success is True
    assert result.stdout == "Domain snapshot created"
    assert result.returncode == 0


def test_run_with_stall_detection_returns_expected_result():
    """run_with_stall_detection matches expectations like run() and returns
    the preconfigured ShellResult."""
    mock_shell = MockShell()
    assert isinstance(mock_shell, IShell)

    expected = ShellResult(
        success=True,
        stdout="convert completed",
        stderr="",
        returncode=0,
        error=None,
    )
    mock_shell.expect("qemu-img convert").returns(expected)

    result = mock_shell.run_with_stall_detection(
        ["qemu-img", "convert", "-c", "-o", "compression_type=zstd", "source.qcow2", "dest.qcow2"],
        output_file=Path("/tmp/dest.qcow2"),
        stall_timeout=60,
    )
    assert result is expected
    assert result.success is True
    assert result.stdout == "convert completed"
    assert result.returncode == 0


def test_run_with_stall_detection_returns_default_error_on_no_match():
    """When no expectation matches, run_with_stall_detection returns an
    error ShellResult (like run())."""
    mock_shell = MockShell()

    result = mock_shell.run_with_stall_detection(
        ["qemu-img", "convert", "source.qcow2", "dest.qcow2"],
        output_file=Path("/tmp/backup.qcow2"),
        stall_timeout=1800,
    )
    assert result.success is False
    assert result.returncode == -1
    assert "No mock configured for:" in result.error


def test_run_with_stall_detection_raises_on_expectation_raises():
    """run_with_stall_detection raises exceptions configured via
    expect().raises(), matching the run() behaviour."""
    import subprocess

    mock_shell = MockShell()
    mock_shell.expect("qemu-img convert").raises(
        subprocess.TimeoutExpired(cmd="qemu-img convert", timeout=30)
    )

    try:
        mock_shell.run_with_stall_detection(
            ["qemu-img", "convert", "source.qcow2", "dest.qcow2"],
            output_file=Path("/tmp/backup.qcow2"),
            stall_timeout=300,
        )
        raise AssertionError("Expected TimeoutExpired was not raised")
    except subprocess.TimeoutExpired:
        pass


def test_run_with_stall_detection_expect_first_priority():
    """expect_first() takes priority over expect() for run_with_stall_detection,
    matching the run() behaviour."""
    mock_shell = MockShell()

    general = ShellResult(
        success=False,
        stdout="",
        stderr="general match",
        returncode=1,
        error="general",
    )
    specific = ShellResult(
        success=True,
        stdout="specific match",
        stderr="",
        returncode=0,
        error=None,
    )

    mock_shell.expect("qemu-img convert").returns(general)
    mock_shell.expect_first("qemu-img convert.*zstd").returns(specific)

    result = mock_shell.run_with_stall_detection(
        ["qemu-img", "convert", "-c", "-o", "compression_type=zstd", "source.qcow2", "dest.qcow2"],
        output_file=Path("/tmp/dest.qcow2"),
        stall_timeout=60,
    )
    assert result is specific
    assert result.success is True
    assert result.stdout == "specific match"


def test_run_with_heartbeat_scripted_heartbeats():
    """run_with_heartbeat with expect(...).returns(result, heartbeats=2) records
    the call, invokes the callback twice, and returns the result.

    Covers the shell-abstraction scenario "MockShell implements the contract"
    (test-plan mocks-contracts group): the mock must stay an ``IShell``, the
    call must be recorded, ``on_heartbeat(elapsed)`` must fire exactly the
    scripted number of times, and the scripted ``ShellResult`` is returned.
    """
    mock_shell = MockShell()
    assert isinstance(mock_shell, IShell)

    expected = ShellResult(
        success=True,
        stdout="commit complete",
        stderr="",
        returncode=0,
        error=None,
    )
    mock_shell.expect("virsh blockcommit").returns(expected, heartbeats=2)

    heartbeats: list[int] = []
    result = mock_shell.run_with_heartbeat(
        ["virsh", "blockcommit", "--domain", "testvm", "--path", "vda", "--wait"],
        timeout=1800,
        heartbeat_seconds=60,
        on_heartbeat=heartbeats.append,
    )

    assert result is expected
    assert result.success is True
    assert result.stdout == "commit complete"
    assert heartbeats == [60, 120], f"Expected two heartbeats at 60s/120s, got {heartbeats}"
    assert mock_shell.call_history == ["virsh blockcommit --domain testvm --path vda --wait"]
    # The mock still satisfies the ABC after the heartbeat path was added.
    assert isinstance(mock_shell, IShell)


def test_run_with_heartbeat_no_heartbeats_by_default():
    """Without a scripted heartbeat count, run_with_heartbeat never calls back."""
    mock_shell = MockShell()
    assert isinstance(mock_shell, IShell)

    expected = ShellResult(
        success=True,
        stdout="done",
        stderr="",
        returncode=0,
        error=None,
    )
    mock_shell.expect("virsh blockcommit").returns(expected)

    heartbeats: list[int] = []
    result = mock_shell.run_with_heartbeat(
        ["virsh", "blockcommit", "--domain", "testvm", "--path", "vda", "--wait"],
        timeout=1800,
        heartbeat_seconds=60,
        on_heartbeat=heartbeats.append,
    )

    assert result is expected
    assert heartbeats == [], f"Expected no heartbeats, got {heartbeats}"


def test_run_with_heartbeat_returns_default_error_on_no_match():
    """When no expectation matches, run_with_heartbeat returns an error
    ShellResult (like run() and run_with_stall_detection())."""
    mock_shell = MockShell()

    result = mock_shell.run_with_heartbeat(
        ["virsh", "blockcommit", "--domain", "testvm", "--path", "vda", "--wait"],
        timeout=1800,
        heartbeat_seconds=60,
        on_heartbeat=lambda elapsed: None,
    )
    assert result.success is False
    assert result.returncode == -1
    assert "No mock configured for:" in result.error
