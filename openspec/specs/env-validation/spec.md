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

In dry-run mode, `_validate_environment()` SHALL still be called. Validation failures SHALL be logged as WARNING (non-fatal) in dry-run mode. The pipeline SHALL NOT abort on validation failure in dry-run mode. In non-dry-run mode, validation failure SHALL raise `RuntimeError` and abort the pipeline as before.

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

#### Scenario: Dry-run runs validation as non-fatal warnings
- **WHEN** `Core._execute_pipeline()` is called in dry-run mode
- **THEN** `_validate_environment()` is called (NOT skipped)
- **AND** if validation fails, the broken checks are logged as WARNING
- **AND** the pipeline does NOT abort (continues to dry-run snapshot/backup steps)
- **AND** the dry-run output includes the validation warnings

#### Scenario: Non-dry-run aborts on validation failure
- **WHEN** `Core._execute_pipeline()` is called in non-dry-run mode
- **AND** `_validate_environment()` returns `status="validation_failed"`
- **THEN** a `RuntimeError` is raised with the broken checks
- **AND** the pipeline does NOT proceed to snapshot or backup steps

### Requirement: libnbd availability check for bitmap mode

Pre-flight environment validation SHALL verify that the Python `nbd` module (libnbd bindings, system package `python3-libnbd`) is importable whenever any configured target uses `incremental_mode = "bitmap"`. If the import fails, validation SHALL fail with an actionable error naming the system package (e.g. "python3-libnbd is required for incremental_mode='bitmap' — install via apt install python3-libnbd"). When no bitmap-mode target is configured, the check SHALL NOT run and qsnap SHALL remain fully functional without libnbd. There SHALL be no silent fallback to file-copy mode: the user explicitly selected bitmap mode, so a missing dependency is a hard validation error.

#### Scenario: Bitmap mode with libnbd installed

- **WHEN** a target uses `incremental_mode = "bitmap"` and `import nbd` succeeds
- **THEN** validation passes for that check

#### Scenario: Bitmap mode without libnbd — hard failure

- **WHEN** a target uses `incremental_mode = "bitmap"` and `import nbd` fails
- **THEN** validation fails with an error naming `python3-libnbd`
- **AND** the pipeline does NOT proceed (non-dry-run)
- **AND** no fallback to file-copy mode occurs

#### Scenario: No bitmap targets — check skipped

- **WHEN** every configured target uses `incremental_mode = "file"` (or another non-bitmap mode)
- **THEN** the libnbd import check is not performed
