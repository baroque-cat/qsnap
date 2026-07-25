## MODIFIED Requirements

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
