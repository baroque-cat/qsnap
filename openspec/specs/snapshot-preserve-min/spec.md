# Snapshot Preserve-Min

## Purpose

Configurable snapshot preservation floor — guarantees the newest N snapshots of each disk are never blockcommitted, even when `snapshot_chain_length` is exceeded. Applied as a per-disk post-processing filter in `Core._evaluate_disk_retention()` after the oldest-prefix filter.

## Requirements

### Requirement: Per-disk snapshot preserve_min post-processing filter

Within `_evaluate_disk_retention`, after the retention engine produces keep/remove lists and the oldest-prefix post-processing filter has been applied for one disk's snapshots, Core SHALL apply a `preserve_min` filter that guarantees the newest N snapshots of that disk are never blockcommitted. The filter SHALL work as follows: if `len(final_remove) > len(disk_snapshots) - preserve_min`, Core SHALL trim `final_remove` to the oldest `max(0, len(disk_snapshots) - preserve_min)` items and SHALL move the trimmed (newest excess) items from `remove` to `keep`. When `preserve_min` is 0 (default), the filter SHALL be inactive (no trimming).

#### Scenario: preserve_min inactive (default)

- **WHEN** `preserve_min = 0` and one disk has 100 snapshots with `chain_length=72`
- **THEN** the retention engine produces keep=72, remove=28
- **AND** the oldest-prefix filter produces remove=28 (contiguous)
- **AND** the preserve_min filter does not trim (0 = inactive)
- **AND** all 28 snapshots are eligible for blockcommit

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

### Requirement: preserve_min ordering — trim from newest end of remove list

When the preserve_min filter trims the remove list for a disk, it SHALL move the NEWEST items from the remove list to the keep list. The remove list is ordered oldest-first (by timestamp). Trimming from the newest end means the oldest snapshots remain in remove (eligible for blockcommit) while the newer ones (closer to the active layer) are preserved.

#### Scenario: Trimming moves newest remove items to keep

- **WHEN** remove = [s1, s2, s3, s4, s5, s6] (oldest-first) and `max_removable = 3`
- **THEN** final remove = [s1, s2, s3] (oldest 3)
- **AND** s4, s5, s6 are moved to keep

### Requirement: preserve_min does not affect target retention

The `preserve_min` filter SHALL only apply to per-disk snapshot retention within `_evaluate_disk_retention`. It SHALL NOT affect target/backup retention. Target retention uses `keep_generations` at the chain level and does not use `preserve_min`.

#### Scenario: Target retention unaffected by preserve_min

- **WHEN** `preserve_min = 24` and target retention evaluates 3 FULL chains with `keep_generations=2`
- **THEN** the oldest chain is marked for removal regardless of `preserve_min`
- **AND** `preserve_min` is not consulted during target retention evaluation
