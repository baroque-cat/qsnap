# Environment Validation (delta spec)

## ADDED Requirements

### Requirement: libnbd availability check for bitmap mode

Pre-flight environment validation SHALL verify that the Python `nbd` module
(libnbd bindings, system package `python3-libnbd`) is importable whenever any
configured target uses `incremental_mode = "bitmap"`. If the import fails,
validation SHALL fail with an actionable error naming the system package
(e.g. "python3-libnbd is required for incremental_mode='bitmap' — install via
apt install python3-libnbd"). When no bitmap-mode target is configured, the
check SHALL NOT run and qsnap SHALL remain fully functional without libnbd.
There SHALL be no silent fallback to file-copy mode: the user explicitly
selected bitmap mode, so a missing dependency is a hard validation error.

#### Scenario: Bitmap mode with libnbd installed

- **WHEN** a target uses `incremental_mode = "bitmap"` and `import nbd`
  succeeds
- **THEN** validation passes for that check

#### Scenario: Bitmap mode without libnbd — hard failure

- **WHEN** a target uses `incremental_mode = "bitmap"` and `import nbd` fails
- **THEN** validation fails with an error naming `python3-libnbd`
- **AND** the pipeline does NOT proceed (non-dry-run)
- **AND** no fallback to file-copy mode occurs

#### Scenario: No bitmap targets — check skipped

- **WHEN** every configured target uses `incremental_mode = "file"` (or
  another non-bitmap mode)
- **THEN** the libnbd import check is not performed
