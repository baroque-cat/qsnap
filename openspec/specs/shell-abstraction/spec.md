## Requirements

### Requirement: IShell ABC
The system SHALL provide an `IShell` ABC with a `run(cmd: list[str], timeout: int) → ShellResult` method that wraps subprocess execution.

#### Scenario: IShell is an ABC
- **WHEN** attempting to instantiate IShell directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

### Requirement: SubprocessShell implements IShell
The system SHALL provide a `SubprocessShell` class that implements `IShell` using `subprocess.run()`.

#### Scenario: Successful command execution
- **WHEN** SubprocessShell runs `["echo", "hello"]` with timeout=30
- **THEN** the returned ShellResult has `success=True`, `stdout="hello\n"`, `returncode=0`

#### Scenario: Command timeout
- **WHEN** SubprocessShell runs a command that exceeds its timeout
- **THEN** the returned ShellResult has `success=False` and `error` contains "timed out"

#### Scenario: Command not found
- **WHEN** SubprocessShell runs a non-existent command
- **THEN** the returned ShellResult has `success=False` and `returncode != 0`

### Requirement: Structured logging of shell commands
SubprocessShell SHALL log every command execution at DEBUG level, including the full command, timeout, return code, and duration.

#### Scenario: Command is logged
- **WHEN** SubprocessShell runs any command
- **THEN** a DEBUG-level log entry is emitted with the command and its result

### Requirement: IShell.run() optional check parameter
`IShell.run()` SHALL accept an optional `check: bool = False` parameter. When `True`, the method SHALL return `ShellResult` without logging command failure as an ERROR (useful for pre-flight checks where command failure is expected and not an error condition). The command SHALL still be logged at DEBUG level.

#### Scenario: Check mode does not log error on failure
- **WHEN** `shell.run(["virsh", "dominfo", "--domain", "nonexistent"], timeout=30, check=True)` is called and virsh returns non-zero
- **THEN** the result is logged at DEBUG level, not ERROR
- **THEN** `ShellResult(success=False, ...)` is still returned
