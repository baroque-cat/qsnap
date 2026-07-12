"""Mock verification: MockShell implements IShell."""

from __future__ import annotations

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
