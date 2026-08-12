# Shell Abstraction — delta

## ADDED Requirements

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
