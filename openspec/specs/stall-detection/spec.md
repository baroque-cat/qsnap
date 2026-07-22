# Stall Detection

## Purpose

Output-file-growth monitoring for long-running data-transfer commands (qemu-img convert, rsync). Replaces hardcoded timeouts with stall detection: processes are killed only when the output file stops growing for a configurable duration. If data flows (even slowly), the process is allowed to continue.

## Requirements

### Requirement: IShell.run_with_stall_detection method

The `IShell` ABC SHALL provide a `run_with_stall_detection(cmd: list[str], output_file: Path | None = None, stall_timeout: int = 1800, check: bool = False) -> ShellResult` method for long-running data-transfer commands. The method SHALL use `subprocess.Popen()` instead of `subprocess.run()`, polling the process every 60 seconds. On each poll, if the process has completed, the method SHALL return the result. If the process is still running, the method SHALL check the size of `output_file` (if provided). If the file size has not increased since the last check for a continuous period exceeding `stall_timeout` seconds, the process SHALL be killed and `ShellResult(success=False, error="Stall detected: no progress for {N}s")` SHALL be returned. The method SHALL NOT log speed or progress — only stall and error events. If `output_file` is `None`, the method SHALL behave like `run()` with an infinite timeout (no stall detection).

#### Scenario: Process completes normally
- **WHEN** `run_with_stall_detection(cmd, output_file=Path(...), stall_timeout=1800)` is called
- **AND** the process completes within 60 seconds
- **THEN** `ShellResult(success=True, ...)` is returned with the process's stdout/stderr/returncode

#### Scenario: Stall detected — process killed
- **WHEN** `run_with_stall_detection(cmd, output_file=Path(...), stall_timeout=1800)` is called
- **AND** the process is running but `output_file` size has not increased for 1800 seconds
- **THEN** the process is killed via `proc.kill()` + `proc.wait()`
- **AND** `ShellResult(success=False, error="Stall detected: no progress for 1800s")` is returned

#### Scenario: Data flowing slowly — not killed
- **WHEN** `run_with_stall_detection(cmd, output_file=Path(...), stall_timeout=1800)` is called
- **AND** the process is running and `output_file` size increases periodically
- **THEN** the process is NOT killed
- **AND** the process continues until completion or genuine stall

#### Scenario: No output_file — no stall detection
- **WHEN** `run_with_stall_detection(cmd, output_file=None, stall_timeout=1800)` is called
- **THEN** the method waits for the process to complete (like `run()` with infinite timeout)
- **AND** no stall detection is performed

#### Scenario: Process exits with non-zero code
- **WHEN** the process exits with returncode=1
- **THEN** `ShellResult(success=False, returncode=1, error=<stderr>)` is returned
- **AND** no stall detection error is reported

#### Scenario: check=True suppresses error logging
- **WHEN** `run_with_stall_detection(cmd, output_file=..., check=True)` is called
- **AND** the process fails or stalls
- **THEN** the failure is logged at DEBUG level, not ERROR

### Requirement: SubprocessShell implements run_with_stall_detection

`SubprocessShell` SHALL implement `run_with_stall_detection()` using `subprocess.Popen()` with `stdout=PIPE, stderr=PIPE`. The implementation SHALL:
1. Start the process via `subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)`
2. Enter a polling loop: call `proc.wait(timeout=60)` every 60 seconds
3. If `proc.wait()` returns (process completed), collect stdout/stderr and return `ShellResult`
4. If `proc.wait()` raises `subprocess.TimeoutExpired` (process still running), check `output_file.stat().st_size`
5. If file size increased since last check, reset the stall timer
6. If file size unchanged for `stall_timeout` seconds, kill the process and return stall error
7. Log at DEBUG level: command, stall_timeout, output_file path (no speed/progress logging)

#### Scenario: SubprocessShell stall detection kills hung process
- **WHEN** `SubprocessShell.run_with_stall_detection(["sleep", "3600"], output_file=Path("/tmp/nonexistent"), stall_timeout=60)` is called
- **AND** the output file does not exist (never grows)
- **THEN** after 60 seconds, the process is killed
- **AND** `ShellResult(success=False, error="Stall detected: no progress for 60s")` is returned

#### Scenario: SubprocessShell stall detection allows growing file
- **WHEN** `SubprocessShell.run_with_stall_detection(cmd, output_file=Path("/tmp/growing.tmp"), stall_timeout=60)` is called
- **AND** a background process writes data to `/tmp/growing.tmp` periodically
- **THEN** the process is NOT killed (file is growing)
- **AND** the process continues until completion

### Requirement: MockShell implements run_with_stall_detection

`MockShell` SHALL implement `run_with_stall_detection()` to support testing. The mock SHALL accept the same parameters and return a predefined `ShellResult`. The mock SHALL NOT perform real subprocess execution or file monitoring. Tests SHALL be able to configure the mock to return success, failure, or stall-detected results.

#### Scenario: MockShell returns predefined result
- **WHEN** `mock_shell.run_with_stall_detection(cmd, output_file=Path(...), stall_timeout=1800)` is called
- **THEN** the mock returns the predefined `ShellResult` without executing any real process
- **AND** the mock records the call for assertion in tests

### Requirement: In-process stall watchdog for in-process transfers

When a data transfer executes as an in-process loop rather than a subprocess (currently: the bitmap dirty-block copy loop), stall detection SHALL be implemented as a progress watchdog inside that loop: a monotonic timestamp updated after every successful chunk write; if no chunk completes for `stall_timeout` seconds, the loop SHALL abort and the transfer SHALL return an error string identical to the shell-level contract — `"Stall detected: no progress for {N}s"`. When `stall_timeout` is 0, the watchdog SHALL be disabled. The watchdog SHALL NOT spawn threads and SHALL NOT log speed or progress — only the stall event. `IShell.run_with_stall_detection` remains the mechanism for subprocess-based transfers (`qemu-img convert` FULL exports, `rsync`) and is unchanged by this requirement.

#### Scenario: Watchdog aborts stalled copy loop

- **WHEN** the bitmap copy loop makes no progress for `stall_timeout` seconds
- **THEN** the loop aborts and the transfer returns `error="Stall detected: no progress for {N}s"`
- **AND** Core retry classification handles it exactly like a shell-level stall

#### Scenario: Watchdog disabled at zero timeout

- **WHEN** `stall_timeout` is 0
- **THEN** no watchdog check runs and the loop relies on NBD-level errors only

#### Scenario: Subprocess transfers unchanged

- **WHEN** a FULL backup runs `qemu-img convert`
- **THEN** it still uses `IShell.run_with_stall_detection` with output-file growth polling
