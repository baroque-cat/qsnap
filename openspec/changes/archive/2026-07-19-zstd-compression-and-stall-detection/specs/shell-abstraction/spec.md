## MODIFIED Requirements

### Requirement: IShell ABC
The system SHALL provide an `IShell` ABC with a `run(cmd: list[str], timeout: int) → ShellResult` method that wraps subprocess execution. The ABC SHALL also provide a `run_with_stall_detection(cmd: list[str], output_file: Path | None = None, stall_timeout: int = 1800, check: bool = False) → ShellResult` method for long-running data-transfer commands that monitors output file growth and kills the process if no progress is detected for `stall_timeout` seconds.

#### Scenario: IShell is an ABC
- **WHEN** attempting to instantiate IShell directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

#### Scenario: IShell has run_with_stall_detection method
- **WHEN** inspecting the IShell ABC
- **THEN** both `run` and `run_with_stall_detection` are abstract methods
- **AND** any concrete implementation MUST implement both methods

### Requirement: SubprocessShell implements IShell
The system SHALL provide a `SubprocessShell` class that implements `IShell` using `subprocess.run()` for `run()` and `subprocess.Popen()` for `run_with_stall_detection()`.

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
SubprocessShell SHALL log every command execution at DEBUG level, including the full command, timeout, return code, and duration. For `run_with_stall_detection()`, the log SHALL include `stall_timeout` and `output_file` path, but SHALL NOT log speed, progress, or file size during execution — only stall detection events and final result.

#### Scenario: Command is logged
- **WHEN** SubprocessShell runs any command via `run()` or `run_with_stall_detection()`
- **THEN** a DEBUG-level log entry is emitted with the command and its result
- **AND** for `run_with_stall_detection()`, no intermediate progress or speed is logged

### Requirement: IShell.run() optional check parameter
`IShell.run()` SHALL accept an optional `check: bool = False` parameter. When `True`, the method SHALL return `ShellResult` without logging command failure as an ERROR (useful for pre-flight checks where command failure is expected and not an error condition). The command SHALL still be logged at DEBUG level. `run_with_stall_detection()` SHALL also accept the same `check` parameter with identical semantics.

#### Scenario: Check mode does not log error on failure
- **WHEN** `shell.run(["virsh", "dominfo", "--domain", "nonexistent"], timeout=30, check=True)` is called and virsh returns non-zero
- **THEN** the result is logged at DEBUG level, not ERROR
- **THEN** `ShellResult(success=False, ...)` is still returned

#### Scenario: Check mode works with run_with_stall_detection
- **WHEN** `shell.run_with_stall_detection(cmd, output_file=..., check=True)` is called and the process stalls
- **THEN** the stall is logged at DEBUG level, not ERROR
- **AND** `ShellResult(success=False, error="Stall detected...")` is returned

### Requirement: --force-share safety classification for qemu-img operations

The system SHALL classify `qemu-img` operations into two categories for `--force-share` usage:

**SAFE (metadata-only):** `qemu-img info`, `qemu-img info --backing-chain`, `qemu-img map`, `qemu-img check`, `qemu-img rebase -u`. These operations read only headers, L2 tables, and refcount structures — minimal I/O with low risk of reading inconsistent data. `--force-share` SHALL be used on these operations when the target file may be the active layer of a running VM.

**DANGEROUS (data-copying):** `qemu-img convert`, `qemu-img compare`, `qemu-img commit`. These operations read ALL data clusters — race conditions during concurrent QEMU writes produce silently corrupted output (missed writes, stale data, partial writes). `--force-share` SHALL NOT be used on these operations. Instead, the NBD pull-model SHALL be used for live-VM data-copying operations (FULL backup, fork). For offline operations (lifecycle commit), the VM MUST be stopped — `--force-share` would mask a dangerous state.

#### Scenario: Metadata-only operation uses --force-share on active layer
- **WHEN** `qemu-img info` is called on a file that is the active layer of a running VM
- **THEN** `--force-share` is included in the command
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: Data-copying operation does NOT use --force-share
- **WHEN** `qemu-img convert` or `qemu-img compare` is called on a file that is the active layer
- **THEN** `--force-share` is NOT included in the command
- **AND** the NBD pull-model is used instead (for FULL backup and fork)
- **AND** if the VM is stopped, direct operation is safe (no lock holder)

#### Scenario: Lifecycle commit operations remain offline-only
- **WHEN** `qemu-img commit` is called by `BlockCommitManager` or `QemuImgCommitManager`
- **THEN** `--force-share` is NOT added
- **AND** the operation is only safe when the VM is stopped (intentionally offline)
- **AND** if the VM is running, the operation may fail with a lock error (correct behavior — prevents dangerous commit on live disk)
