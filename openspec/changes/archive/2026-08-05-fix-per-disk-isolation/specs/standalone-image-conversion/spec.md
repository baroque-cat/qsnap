## ADDED Requirements

### Requirement: convert_to_standalone flattens a backing chain
The system SHALL provide a stateless function `convert_to_standalone(shell: IShell, source: Path, output: Path, timeout: int = 7200) -> ShellResult` in `qsnap/utils/convert.py` that executes `qemu-img convert --force-share -O qcow2 <source> <output>` through the injected `IShell`. On command failure the function SHALL best-effort remove the partial `output` file and return a failed `ShellResult`; it SHALL NOT raise exceptions for expected failures (conversion errors, timeouts, missing binaries).

#### Scenario: Successful conversion
- **WHEN** `convert_to_standalone(shell, source, output)` is called and `qemu-img convert` succeeds
- **THEN** a standalone qcow2 exists at `output`
- **AND** the returned `ShellResult` indicates success

#### Scenario: Failed conversion removes partial output
- **WHEN** `qemu-img convert` fails after writing a partial `output` file
- **THEN** the partial file is removed best-effort
- **AND** the returned `ShellResult` carries the failure details

#### Scenario: Expected failures are returned, not raised
- **WHEN** `qemu-img convert` times out or the binary is missing
- **THEN** a failed `ShellResult` is returned
- **AND** no exception propagates to the caller

### Requirement: verify_standalone_image verifies conversion output
The system SHALL provide a stateless function `verify_standalone_image(shell: IShell, source: Path, output: Path) -> str | None` in `qsnap/utils/convert.py` that performs two verification tiers on a freshly converted standalone image: M1 compares the `virtual-size` of `output` against the source chain via `qemu-img info --force-share --output=json` (mismatch indicates a truncated or wrong conversion); M2 runs `qemu-img check <output>` and requires no errors. The function SHALL return `None` when both tiers pass, or an error string naming the failed tier otherwise.

#### Scenario: Healthy image passes verification
- **WHEN** `verify_standalone_image(shell, source, output)` is called and virtual sizes match and `qemu-img check` is clean
- **THEN** the function returns `None`

#### Scenario: Virtual-size mismatch fails M1
- **WHEN** the converted output's `virtual-size` differs from the source chain's
- **THEN** the function returns an error string identifying the M1 mismatch
- **AND** `qemu-img check` is not required to pass for the failure to be reported

#### Scenario: Corrupted output fails M2
- **WHEN** virtual sizes match but `qemu-img check <output>` reports errors
- **THEN** the function returns an error string identifying the M2 failure

### Requirement: convert_with_retry applies the backup retry policy
The system SHALL provide a stateless function `convert_with_retry(shell: IShell, source: Path, output: Path, retry_max: int, retry_base: str) -> ShellResult` in `qsnap/utils/convert.py` that attempts `convert_to_standalone` and retries only errors classified retryable by `is_retryable()` (from `qsnap/utils/retry.py`), sleeping `compute_backoff()` between attempts, up to `retry_max` attempts. Callers SHALL pass `GlobalConfig.backup_retry_max` and `GlobalConfig.backup_retry_base` as the retry limits — no new configuration options are introduced. Any partial output file SHALL be removed before each retry attempt.

#### Scenario: Transient failure then success
- **WHEN** the first convert attempt fails with a retryable error and the second succeeds
- **THEN** the function returns a successful `ShellResult`
- **AND** exactly two convert attempts were made

#### Scenario: Non-retryable error fails immediately
- **WHEN** the first convert attempt fails with a non-retryable error
- **THEN** the function returns the failed `ShellResult` without further attempts

#### Scenario: Retries exhausted
- **WHEN** all `retry_max` attempts fail with retryable errors
- **THEN** the function returns the last failed `ShellResult`
- **AND** no partial output file remains

### Requirement: Conversion helpers are stateless utilities
The functions in `qsnap/utils/convert.py` SHALL NOT read or write `IStateManager` state, configuration files, domain XML, or libvirt objects; every external command SHALL go through the `IShell` instance passed as a parameter. This keeps the helpers shareable by `Core.fork()` and `Core.restore()` without module coupling, following the precedent of `scan_backing_chain()` in `qsnap/utils/verification.py`.

#### Scenario: Helpers perform no state or libvirt mutations
- **WHEN** any convert helper runs to completion
- **THEN** no `IStateManager` method is called
- **AND** no `virsh` command is executed
- **AND** only `qemu-img` commands and file removal run through the injected `IShell`
