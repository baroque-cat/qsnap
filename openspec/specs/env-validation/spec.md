# Environment Validation

## Purpose

Pre-flight environment validation before pipeline execution — verifies snapshot_dir, base_image, virsh/qemu-img in PATH, VM defined in libvirt, target paths are reachable, and rsync availability (hard requirement).

## Requirements

### Requirement: Pre-flight rsync availability check

The pre-flight environment validation SHALL check that the `rsync` binary is available in PATH on every pipeline run. If `rsync` is not found, validation SHALL fail with an error and the pipeline SHALL NOT proceed. This is a hard requirement — `rsync` is the sole file transfer mechanism and no fallback exists.

#### Scenario: Rsync available — validation passes
- **WHEN** `which rsync` succeeds
- **THEN** validation passes and pipeline execution continues

#### Scenario: Rsync unavailable — pipeline aborts
- **WHEN** `which rsync` returns non-zero
- **THEN** validation fails with an error: "rsync is required but not found in PATH"
- **AND** the pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: Rsync check always runs
- **WHEN** `_validate_environment()` is called
- **THEN** `which rsync` is always called, regardless of `rate_limit` configuration

### Requirement: Pre-flight environment validation before pipeline

Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) stale `.tmp`, `.partial`, and NBD socket files are cleaned up per `auto_cleanup` config, (b) orphan `.qcow2` snapshots are detected (WARNING only), (c) `snapshot_dir` exists and is a writable directory, (d) `base_image` file exists, (e) `virsh` and `qemu-img` binaries are in PATH, (f) VM is defined in libvirt (`virsh dominfo` returns 0), (g) `rsync` is in PATH (hard requirement, pipeline aborts if missing). Failure on checks (c)-(f) SHALL return immediately — no partial pipeline execution. Cleanup steps (a)-(b) SHALL be skipped when `auto_cleanup = false`. Checks (a)-(b) SHALL NOT block pipeline execution — they are defensive, not critical.

#### Scenario: Cleanup and orphan detection execute before main checks
- **WHEN** `_validate_environment()` is called and `auto_cleanup = true`
- **THEN** stale file cleanup and orphan detection run first
- **AND** then directory/file/binary existence checks run

#### Scenario: Cleanup skipped when auto_cleanup is false
- **WHEN** `_validate_environment()` is called and `auto_cleanup = false`
- **THEN** stale file cleanup is skipped
- **AND** an INFO log states "auto_cleanup is disabled"
- **AND** the remaining existence checks proceed normally

#### Scenario: All validations pass

- **WHEN** `_validate_environment()` checks a properly configured VM
- **THEN** the method returns without error and pipeline execution continues

#### Scenario: snapshot_dir does not exist

- **WHEN** `snapshot_dir` path does not exist as a directory
- **THEN** validation returns with an error and pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: virsh binary not in PATH

- **WHEN** `which virsh` returns non-zero
- **THEN** validation returns with an error message indicating `virsh` is required

#### Scenario: libvirt rejects dominfo — VM not defined

- **WHEN** `virsh dominfo --domain <name>` returns non-zero
- **THEN** validation returns with an error indicating the VM is not defined in libvirt

### Requirement: Validation for targets

Pre-flight validation SHALL check each configured target: if `target.path` does not exist as a directory, the behaviour SHALL depend on `snapshot_create` mode. `"ondemand"`: skip target with INFO log. All other modes: return validation error.

#### Scenario: Ondemand target missing — skip target

- **WHEN** `snapshot_create = "ondemand"` and a target directory does not exist
- **THEN** validation passes (target is skipped later)
- **THEN** an INFO message is logged about the unreachable target

#### Scenario: Always mode target missing — error

- **WHEN** `snapshot_create = "always"` and a target directory does not exist
- **THEN** validation fails with an error pointing to the missing target
