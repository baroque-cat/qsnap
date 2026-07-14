## ADDED Requirements

### Requirement: Pre-flight rsync availability check

When `rate_limit` is set to a value other than `"no"` at the target level, the pre-flight environment validation SHALL check that the `rsync` binary is available in PATH. If `rsync` is not found, a WARNING SHALL be logged. This check SHALL NOT block pipeline execution.

#### Scenario: Rsync available — no warning

- **WHEN** `rate_limit` is `"100M"` for a target and `which rsync` succeeds
- **THEN** no WARNING is logged during environment validation

#### Scenario: Rsync unavailable — warning logged

- **WHEN** `rate_limit` is `"100M"` for a target and `which rsync` returns non-zero
- **THEN** a WARNING is logged: "rsync not found — rate limiting disabled for target <path>"
- **AND** validation does not fail

#### Scenario: Rsync check skipped when rate_limit is "no"

- **WHEN** `rate_limit` is `"no"` for all targets
- **THEN** `which rsync` is never called during environment validation

## MODIFIED Requirements

### Requirement: Pre-flight environment validation before pipeline

Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) `snapshot_dir` exists and is a writable directory, (b) `base_image` file exists, (c) `virsh` and `qemu-img` binaries are in PATH, (d) VM is defined in libvirt (`virsh dominfo` returns 0), (e) if any target has `rate_limit != "no"`, `rsync` is in PATH (WARNING on missing, non-blocking). Failure on checks (a)-(d) SHALL return immediately — no partial pipeline execution.

#### Scenario: All validations pass

- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** the method returns without error and pipeline execution continues

#### Scenario: snapshot_dir does not exist

- **WHEN** `snapshot_dir` path does not exist as a directory
- **THEN** validation returns with an error and pipeline does NOT proceed

#### Scenario: virsh binary not in PATH

- **WHEN** `which virsh` returns non-zero
- **THEN** validation returns with an error message indicating `virsh` is required

#### Scenario: libvirt rejects dominfo — VM not defined

- **WHEN** `virsh dominfo --domain <name>` returns non-zero
- **THEN** validation returns with an error
