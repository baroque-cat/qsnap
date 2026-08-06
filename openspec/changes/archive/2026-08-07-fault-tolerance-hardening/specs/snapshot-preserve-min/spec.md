# Snapshot Preserve-Min — Delta

## MODIFIED Requirements

### Requirement: Per-disk snapshot preserve_min post-processing filter

Within `_evaluate_disk_retention`, after the retention engine produces keep/remove lists and the oldest-prefix post-processing filter has been applied for one disk's snapshots, Core SHALL apply a `preserve_min` filter that guarantees the newest N snapshots of that disk are never blockcommitted. The filter SHALL work as follows: if `len(final_remove) > len(disk_snapshots) - preserve_min`, Core SHALL trim `final_remove` to the oldest `max(0, len(disk_snapshots) - preserve_min)` items and SHALL move the trimmed (newest excess) items from `remove` to `keep`. The default value of `preserve_min` is `48` (inherited from `GlobalConfig.snapshot_preserve_min`), so the floor is ACTIVE by default. When `preserve_min` is explicitly set to 0, the filter SHALL be inactive (no trimming). When `preserve_min` exceeds `snapshot_chain_length`, the floor dominates: effective retention keeps at least `preserve_min` newest snapshots per disk.

#### Scenario: preserve_min inactive when explicitly zero

- **WHEN** `preserve_min = 0` (explicit) and one disk has 100 snapshots with `chain_length=72`
- **THEN** the retention engine produces keep=72, remove=28
- **AND** the oldest-prefix filter produces remove=28 (contiguous)
- **AND** the preserve_min filter does not trim (0 = inactive)
- **AND** all 28 snapshots are eligible for blockcommit

#### Scenario: default preserve_min 48 keeps newest 48

- **WHEN** no `snapshot_preserve_min` is configured anywhere (default `48`)
- **AND** one disk has 100 snapshots with the default `chain_length=24`
- **THEN** the retention engine produces keep=24, remove=76
- **AND** `max_removable = max(0, 100 - 48) = 52`
- **AND** the preserve_min filter trims remove to the oldest 52 items
- **AND** final keep = 48 (newest), final remove = 52 (oldest)

#### Scenario: default floor dominates chain_length

- **WHEN** defaults apply (`preserve_min=48`, `chain_length=24`) and one disk has 30
  snapshots
- **THEN** `max_removable = max(0, 30 - 48) = 0`
- **AND** no snapshot is eligible for blockcommit
- **AND** all 30 snapshots are preserved

#### Scenario: preserve_min preserves newest snapshots of a disk

- **WHEN** `preserve_min = 24` and one disk has 30 snapshots with `chain_length=6`
- **THEN** the retention engine produces keep=6, remove=24
- **AND** the oldest-prefix filter produces remove=24 (contiguous)
- **AND** `max_removable = max(0, 30 - 24) = 6`
- **AND** the preserve_min filter trims remove to the oldest 6 items
- **AND** the 18 newest items in remove are moved to keep
- **AND** final remove = 6 (oldest), final keep = 24 (newest)

#### Scenario: preserve_min does not trigger when remove is small

- **WHEN** `preserve_min = 24` and one disk has 100 snapshots with `chain_length=72`
- **THEN** the retention engine produces keep=72, remove=28
- **AND** `max_removable = 100 - 24 = 76`
- **AND** since 28 <= 76, no trimming occurs
- **AND** all 28 snapshots are eligible for blockcommit

#### Scenario: preserve_min equals total snapshots for a disk

- **WHEN** `preserve_min = 30` and one disk has 30 snapshots with `chain_length=6`
- **THEN** `max_removable = max(0, 30 - 30) = 0`
- **AND** the preserve_min filter moves all 24 remove items to keep
- **AND** final remove is empty (no blockcommit for this disk)
- **AND** all 30 snapshots are preserved

#### Scenario: preserve_min greater than total snapshots

- **WHEN** `preserve_min = 50` and one disk has 30 snapshots with `chain_length=6`
- **THEN** `max_removable = max(0, 30 - 50) = 0`
- **AND** the preserve_min filter moves all remove items to keep
- **AND** final remove is empty (no blockcommit for this disk)

#### Scenario: preserve_min applied after oldest-prefix within a single disk

- **WHEN** one disk's snapshots are [s1..s10] (oldest-first) with `chain_length=4` and `preserve_min=6`
- **AND** retention produces keep=[s7,s8,s9,s10], remove=[s1,s2,s3,s4,s5,s6]
- **AND** oldest-prefix produces remove=[s1,s2,s3,s4,s5,s6] (contiguous)
- **THEN** `max_removable = max(0, 10 - 6) = 4`
- **AND** final remove = [s1,s2,s3,s4] (oldest 4)
- **AND** final keep = [s5,s6,s7,s8,s9,s10] (newest 6)

#### Scenario: Each disk applies preserve_min independently

- **WHEN** VM has two disks: vda with 100 snapshots and vdb with 30 snapshots, both with `chain_length=6` and `preserve_min=24`
- **THEN** vda: `max_removable = 100 - 24 = 76`, remove=28 (all eligible since 28 <= 76)
- **AND** vdb: `max_removable = max(0, 30 - 24) = 6`, remove trimmed from 24 to 6
- **AND** each disk's preserve_min operates on its own snapshot count
