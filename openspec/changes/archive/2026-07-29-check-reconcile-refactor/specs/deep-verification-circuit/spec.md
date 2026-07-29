## MODIFIED Requirements

### Requirement: qsnap check --deep enhanced with per-image verification

The `qsnap check --deep` command SHALL, for each VM: (a) run `qemu-img check --output=json` on every snapshot in `IStateManager.get_snapshots()`, (b) run `qemu-img check --output=json` on every backup file on each target, (c) compare snapshot files on disk vs state records (orphan detection), (d) report `corruptions`, `errors`, AND `leaks` count per image, (e) aggregate per-VM status: OK (0 corruptions, 0 errors, 0 leaks), WARNING (>0 in any field but recoverable), CRITICAL (images missing/unreadable). The `qemu-img check` command SHALL use a timeout of 7200 seconds (2 hours) to accommodate large disks.

#### Scenario: All images pass deep check

- **WHEN** every snapshot and backup passes `qemu-img check` with 0 corruptions, 0 errors, and 0 leaks
- **THEN** each VM reports "OK" and exit code is 0

#### Scenario: Corruption detected in one image

- **WHEN** `qemu-img check` reports `corruptions: 3` for one snapshot
- **THEN** that VM reports "WARNING: 3 corruptions in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Errors detected in one image

- **WHEN** `qemu-img check` reports `errors: 2` for one snapshot
- **THEN** that VM reports "WARNING: 2 errors in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Leaks detected in one image

- **WHEN** `qemu-img check` reports `leaks: 5` for one snapshot
- **THEN** that VM reports "WARNING: 5 leaks in snap1.qcow2"
- **AND** the overall exit code is 0 (warnings are non-fatal)

#### Scenario: Image unreadable

- **WHEN** a snapshot file exists but `qemu-img check` fails with "Could not open"
- **THEN** that VM reports "CRITICAL: unreadable image /path/to/snap.qcow2"

#### Scenario: Deep check timeout accommodates large disks

- **WHEN** `qemu-img check` is run on a multi-GB disk
- **THEN** the timeout is 7200 seconds (2 hours)
- **AND** the command is not prematurely killed for large disks
