# Shell Abstraction

## Purpose

The `IShell` interface that wraps all external command execution (`virsh`, `qemu-img`, filesystem calls). It provides timeout enforcement, stall detection for long transfers, structured logging, and full mockability in tests.

## Requirements

### Requirement: IShell ABC
The system SHALL provide an `IShell` ABC with a `run(cmd: list[str], timeout: int) → ShellResult` method that wraps subprocess execution. The ABC SHALL also provide a `run_with_stall_detection(cmd: list[str], output_file: Path | None = None, stall_timeout: int = 1800, check: bool = False) → ShellResult` method for long-running data-transfer commands that monitors output file growth and kills the process if no progress is detected for `stall_timeout` seconds.

The `run_with_stall_detection()` method SHALL be used by `BitmapBackupProvider` to execute `qemu-img convert` commands for FULL backup transfers. The target `.tmp` file SHALL be passed as `output_file`, and `target.backup_stall_timeout` SHALL be passed as `stall_timeout`. This activates the method for production use — it is no longer reserved for future needs.

The in-process stall watchdog (monotonic timestamp between chunk writes) remains the mechanism for incremental transfers via the Python `pread`/`pwrite` loop.

#### Scenario: IShell is an ABC
- **WHEN** attempting to instantiate IShell directly
- **THEN** TypeError is raised (cannot instantiate abstract class)

#### Scenario: IShell has run_with_stall_detection method
- **WHEN** inspecting the IShell ABC
- **THEN** both `run` and `run_with_stall_detection` are abstract methods
- **AND** any concrete implementation MUST implement both methods

#### Scenario: run_with_stall_detection used for qemu-img convert FULL backup
- **WHEN** `BitmapBackupProvider` executes a FULL backup via `qemu-img convert`
- **THEN** `IShell.run_with_stall_detection(cmd, output_file=<target>.tmp, stall_timeout=<target.backup_stall_timeout>)` is called
- **AND** the output file growth is monitored every 60 seconds
- **AND** the process is killed only if no growth is observed for `stall_timeout` seconds

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

**DANGEROUS (data-copying):** `qemu-img convert`, `qemu-img compare`, `qemu-img commit`. These operations read ALL data clusters — race conditions during concurrent QEMU writes produce silently corrupted output (missed writes, stale data, partial writes). The NBD pull-model SHALL be used for live-VM data-copying operations (FULL backup only). For offline operations (lifecycle commit), the VM MUST be stopped.

**LEGACY-DANGEROUS-ACCEPTED (data-copying with --force-share):** `qemu-img convert` in `fork` and `restore` SHALL use `--force-share` for direct file reads. This is an accepted trade-off: the operator understands that reads from a running VM's active layer may produce an inconsistent point-in-time copy. For consistent copies, the operator SHALL stop the VM first or fork from a non-active snapshot.

#### Scenario: Metadata-only operation uses --force-share on active layer
- **WHEN** `qemu-img info` is called on a file that is the active layer of a running VM
- **THEN** `--force-share` is included in the command
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: Data-copying operation uses NBD for FULL backup
- **WHEN** `qemu-img convert` is called for a FULL backup on a running VM
- **THEN** `--force-share` is NOT included in the command
- **AND** the NBD pull-model is used instead (for FULL backup)
- **AND** if the VM is stopped, direct operation is safe (no lock holder)

#### Scenario: fork and restore use --force-share for direct reads
- **WHEN** `qemu-img convert` is called for fork or restore on a file that may be the active layer
- **THEN** `--force-share` IS included in the command (accepted risk of inconsistency)
- **AND** the operator is responsible for stopping the VM if consistency is required

#### Scenario: Lifecycle commit operations remain offline-only
- **WHEN** `qemu-img commit` is called by `BlockCommitManager` or `QemuImgCommitManager`
- **THEN** `--force-share` is NOT added
- **AND** the operation is only safe when the VM is stopped (intentionally offline)
- **AND** if the VM is running, the operation may fail with a lock error (correct behavior — prevents dangerous commit on live disk)

### Requirement: check=True for probing shell.run() calls

All `shell.run()` calls that are probing or testing (where command failure is expected and handled by the caller in conditional logic) SHALL use `check=True` to avoid misleading ERROR-level logs. The criterion is: if the calling code handles the failure result in a conditional branch (if/else, try/except) rather than treating it as an unexpected error, the call is a probe and SHALL use `check=True`.

The following call sites SHALL be audited and updated to `check=True` where applicable:
- `utils/nbd.py`: `is_vm_running()`, `is_libvirt_new_enough()`, `get_disk_targets()`
- `modules/snapshot/external.py`: `virsh domblklist`, `qemu-img info --backing-chain`
- `modules/change/allocation_detector.py`: `qemu-img info`
- `modules/change/map_detector.py`: `qemu-img map`
- `modules/backup/bitmap.py`: `qemu-img info`, `test -f` existence checks
- `core/__init__.py`: `qemu-img info`, `qemu-img check`, `virsh domstate` (outside `_validate_environment`)
- `utils/verification.py`: `qemu-img info`, `qemu-img check`, `qemu-img compare`
- `modules/lifecycle/blockcommit_manager.py`: `virsh domblklist`, `qemu-img check`
- `utils/space.py`: `estimate_full_size()`, `estimate_incremental_size()` (size-estimation probes — failure is expected and handled by returning `None`)

#### Scenario: Probing call with check=True logs at DEBUG on failure

- **WHEN** `shell.run(["virsh", "dominfo", "--domain", "nonexistent"], timeout=30, check=True)` is called and virsh returns non-zero
- **THEN** the failure is logged at DEBUG level, not ERROR
- **AND** `ShellResult(success=False, ...)` is returned

#### Scenario: Compress driver probe uses check=True

- **WHEN** `_validate_environment()` runs `qemu-nbd --image-opts driver=compress` probe
- **THEN** `shell.run()` is called with `check=True`
- **AND** the expected non-zero exit is logged at DEBUG level, not ERROR

#### Scenario: Size-estimation probes use check=True

- **WHEN** `estimate_full_size()` or `estimate_incremental_size()` runs `qemu-img info` and the command fails
- **THEN** `shell.run()` was called with `check=True`
- **AND** the failure is logged at DEBUG level, not ERROR
- **AND** the function returns `None`

### Requirement: run_with_heartbeat execution method

`IShell` SHALL provide a third execution method:

```
run_with_heartbeat(
    cmd: list[str],
    timeout: int,
    heartbeat_seconds: int,
    on_heartbeat: Callable[[int], None],
    check: bool = False,
) -> ShellResult
```

Semantics:

- Runs `cmd` as a child process with captured stdout/stderr pipes (no shell interpretation).
- Polls the child in `heartbeat_seconds` slices; after each slice in which the child is still
  running it SHALL call `on_heartbeat(elapsed_seconds)`.
- Enforces a HARD wall-clock maximum: when `timeout` seconds elapse, it SHALL kill the child
  and return `ShellResult(success=False, returncode=-1, error="Command timed out after
  {timeout}s")`. Unlike `run_with_stall_detection`, a steadily progressing command MUST NOT
  run past `timeout`.
- stdout/stderr SHALL be drained continuously (reader threads or equivalent) so a chatty
  child can never block on a full pipe buffer; captured output SHALL be returned in the
  `ShellResult` after process exit.
- On normal exit it SHALL return `ShellResult` with the child's returncode, stdout, stderr.
  With `check=True` a non-zero exit SHALL be logged like `run(check=True)`.
- Killing the child kills only the direct child process (same contract as `run`).

All `IShell` implementations (`SubprocessShell`, `MockShell`, and any test doubles) SHALL
implement this method — this is a BREAKING interface addition.

#### Scenario: Normal completion before timeout

- **WHEN** `run_with_heartbeat(["echo", "hi"], timeout=60, heartbeat_seconds=10, on_heartbeat=cb)` is called and the child exits 0 immediately
- **THEN** the result has `success=True`, `stdout` containing "hi", and `on_heartbeat` was never called

#### Scenario: Heartbeat fires while the child runs

- **WHEN** the child runs for 150 seconds with `heartbeat_seconds=60`
- **THEN** `on_heartbeat` is called at least twice with increasing elapsed values before the child exits

#### Scenario: Hard timeout kills the child

- **WHEN** the child is still running when `timeout` seconds elapse
- **THEN** the child is killed and `ShellResult(success=False, returncode=-1, error="Command timed out after {timeout}s")` is returned
- **AND** no further heartbeat callbacks fire after the kill

#### Scenario: Chatty child does not deadlock the pipes

- **WHEN** the child writes more than 64 KB to stdout/stderr while running
- **THEN** the child never blocks on a full pipe buffer and the full output is captured in the returned `ShellResult`

#### Scenario: MockShell implements the contract

- **WHEN** `MockShell.run_with_heartbeat` is scripted with a result and a heartbeat count
- **THEN** it records the call, invokes `on_heartbeat` the scripted number of times, and returns the scripted `ShellResult`
- **AND** `isinstance(mock_shell, IShell)` remains True
