## MODIFIED Requirements

### Requirement: Truncated qcow2 detection on backup targets

After cleaning `*.tmp` and `*.partial` files, `Core._preflight_cleanup()` SHALL scan backup target directories for `.qcow2` files that are NOT `*.FULL.*.qcow2`. For each candidate, run `qemu-img info --output=json` with a 10-second timeout. If the command fails, the file is a truncated transfer artifact — delete it and log WARNING.

#### Scenario: Truncated qcow2 on target is deleted
- **WHEN** `target.path` contains `vm.20250101T1200.qcow2` (not a FULL) and `qemu-img info --output=json` fails on it
- **THEN** the file is deleted
- **AND** a WARNING is logged

#### Scenario: Valid qcow2 is kept
- **WHEN** `qemu-img info --output=json` succeeds on the candidate file
- **THEN** the file is kept and no WARNING is emitted
