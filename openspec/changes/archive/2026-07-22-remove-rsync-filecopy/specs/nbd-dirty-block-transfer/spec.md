## MODIFIED Requirements

### Requirement: Incremental output is a backing-chained COW delta

Every bitmap incremental SHALL be a qcow2 whose `backing-filename` resolves to the previous backup on the same target (previous incremental or FULL). Blocks not dirtied since the prior checkpoint SHALL NOT be written to the delta (they read through the backing chain). After Core records the incremental→FULL dependency, retention cascade-deletion and `check` SHALL treat bitmap chains as standard backup chains.

#### Scenario: qemu-img info shows the backing chain

- **WHEN** an incremental completes
- **THEN** `qemu-img info --output=json <incremental>` reports `backing-filename` equal to the previous backup path
- **AND** `format` is `qcow2`

#### Scenario: Restore resolves bitmap chains unchanged

- **WHEN** `qsnap restore` targets a bitmap incremental
- **THEN** the existing chain copy + `qemu-img rebase -u` flow rebuilds the FULL→incremental chain without bitmap-specific logic
