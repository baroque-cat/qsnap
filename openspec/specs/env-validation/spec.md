# Environment Validation

## Purpose

Pre-flight environment validation before pipeline execution — verifies per-disk snapshot_dirs and base_images, virsh/qemu-img in PATH, VM defined in libvirt, target paths are reachable, and libnbd availability (hard requirement, NBD is the sole backup transfer mechanism).

## Requirements

### Requirement: Pre-flight environment validation before pipeline

Core SHALL execute `_validate_environment(vm_config)` before `_execute_pipeline(vm_config)`. Validation SHALL verify: (a) stale `.tmp`, `.partial`, and NBD socket files are cleaned up per `auto_cleanup` config, (b) orphan `.qcow2` snapshots are detected (WARNING only), (c) each distinct effective snapshot directory across all disks exists and is writable (via `test -d` and `test -w`), (d) each disk's `base_image` file exists (via `test -f`), (e) `virsh` and `qemu-img` binaries are in PATH, (f) VM is defined in libvirt (`virsh dominfo` returns 0), (g) the Python `nbd` module (libnbd bindings with `nbd.Error` and `nbd.NBD` attributes) is importable (hard requirement, pipeline aborts if missing or wrong), (h) the `qemu-nbd` compress driver is available (hard requirement for compressed FULL backups). The compress driver probe (`qemu-nbd --image-opts driver=compress`) SHALL use `check=True` so the expected non-zero exit is logged at DEBUG level, not ERROR. Failure on checks (c)-(h) SHALL return immediately — no partial pipeline execution. Cleanup steps (a)-(b) SHALL be skipped when `auto_cleanup = false`. Checks (a)-(b) SHALL NOT block pipeline execution — they are defensive, not critical.

In dry-run mode, `_validate_environment()` SHALL still be called. Validation failures SHALL be logged as WARNING (non-fatal) in dry-run mode. The pipeline SHALL NOT abort on validation failure in dry-run mode. In non-dry-run mode, validation failure SHALL raise `RuntimeError` and abort the pipeline as before.

The snapshot directory check SHALL collect the distinct effective snapshot directories across all disks (via `vm_config.snapshot_dir_for(disk)` for each `DiskConfig`), and check each unique directory once.

#### Scenario: Cleanup and orphan detection execute before main checks

- **WHEN** `_validate_environment()` is called and `auto_cleanup = true`
- **THEN** stale file cleanup and orphan detection run first
- **AND** then per-disk directory/file/binary existence checks run

#### Scenario: Cleanup skipped when auto_cleanup is false

- **WHEN** `_validate_environment()` is called and `auto_cleanup = false`
- **THEN** stale file cleanup is skipped
- **AND** an INFO log states "auto_cleanup is disabled"
- **AND** the remaining existence checks proceed normally

#### Scenario: All validations pass for multi-disk VM

- **WHEN** `_validate_environment()` checks a properly configured VM with disks vda and vdb
- **THEN** each disk's base_image is verified with `test -f`
- **AND** each distinct snapshot directory is verified with `test -d` and `test -w`
- **AND** the method returns without error and pipeline execution continues

#### Scenario: snapshot_dir does not exist

- **WHEN** a disk's effective snapshot directory does not exist as a directory
- **THEN** validation returns with an error and pipeline does NOT proceed to change detection or snapshot creation

#### Scenario: base_image missing for one disk

- **WHEN** `test -f` fails for one disk's `base_image`
- **THEN** validation returns with an error: `"base_image not found for disk <disk.target>: <path>"`

#### Scenario: virsh binary not in PATH

- **WHEN** `which virsh` returns non-zero
- **THEN** validation returns with an error message indicating `virsh` is required

#### Scenario: libvirt rejects dominfo — VM not defined

- **WHEN** `virsh dominfo --domain <name>` returns non-zero
- **THEN** validation returns with an error indicating the VM is not defined in libvirt

#### Scenario: Compress driver probe uses check=True

- **WHEN** `_validate_environment()` runs the `qemu-nbd --image-opts driver=compress` probe
- **THEN** the `shell.run()` call uses `check=True`
- **AND** the expected non-zero exit is logged at DEBUG level, not ERROR

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

### Requirement: libnbd availability check

Pre-flight environment validation SHALL verify that the Python `nbd` module (libnbd bindings, system package `python3-libnbd`) is importable AND has the required attributes (`nbd.Error` and `nbd.NBD`) on every pipeline run — NBD/libnbd is the sole backup transfer mechanism. The check SHALL call `_ensure_system_site_packages()` before the import attempt to make system bindings discoverable in venv environments. If the import fails or the attributes are missing, validation SHALL fail with an actionable error (`MISSING_LIBNBD_ERROR`) naming the system package for multiple distributions (Arch, Debian, Fedora) and warning against the PyPI `nbd` imposter. There SHALL be no fallback to any other transfer mechanism: a missing or wrong dependency is a hard validation error. In dry-run mode the failure SHALL be logged as a WARNING and SHALL NOT abort the pipeline.

#### Scenario: libnbd installed — validation passes

- **WHEN** `import nbd` succeeds and `hasattr(nbd, "Error")` and `hasattr(nbd, "NBD")` both return `True`
- **THEN** validation passes for that check

#### Scenario: PyPI nbd imposter — hard failure

- **WHEN** the `nbd` module is importable but lacks `nbd.Error` or `nbd.NBD` attributes
- **THEN** validation fails with `MISSING_LIBNBD_ERROR`
- **AND** the error message warns about the PyPI `nbd` imposter
- **AND** the pipeline does NOT proceed (non-dry-run)

#### Scenario: libnbd missing — hard failure

- **WHEN** the `nbd` module is not importable
- **THEN** validation fails with `MISSING_LIBNBD_ERROR`
- **AND** the pipeline does NOT proceed (non-dry-run)
- **AND** no fallback to any other transfer mechanism occurs

#### Scenario: Dry-run downgrades the failure to a warning

- **WHEN** the `nbd` module is not importable or lacks required attributes and the pipeline runs in dry-run mode
- **THEN** the failure is logged as a WARNING
- **AND** the dry-run continues

#### Scenario: Venv discovers system libnbd during validation

- **WHEN** qsnap runs in a venv without `--system-site-packages`
- **AND** system `libnbd` is installed
- **THEN** `_ensure_system_site_packages()` appends system paths to `sys.path` before the check
- **AND** validation passes

#### Scenario: Compress driver available — validation passes

- **WHEN** `qemu-nbd --image-opts driver=compress` is supported (probe passes despite non-zero exit)
- **THEN** validation passes for the compress driver check

#### Scenario: Compress driver missing — hard failure

- **WHEN** `qemu-nbd` does not support the compress driver
- **THEN** validation fails with an error naming the missing driver
- **AND** the pipeline does NOT proceed (non-dry-run)

#### Scenario: Compress driver missing in dry-run — warning

- **WHEN** `qemu-nbd` does not support the compress driver and the pipeline runs in dry-run mode
- **THEN** the failure is logged as a WARNING
- **AND** the dry-run continues
