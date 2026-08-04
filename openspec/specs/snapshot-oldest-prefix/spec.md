# Snapshot Oldest-Prefix Retention

## Purpose

Post-processes snapshot retention results per disk to only remove a contiguous oldest prefix from each disk's snapshot chain. Prevents blockcommit from receiving gapped snapshot lists, which are unreliable and can cause stuck blockcommits. Non-prefix items in the remove list are kept as chain gap fillers.

## Requirements

### Requirement: Per-disk oldest-prefix-only snapshot retention

After the retention engine produces keep/remove lists, Core SHALL evaluate retention separately for each disk's snapshots via `_evaluate_disk_retention`. For each disk's chronologically-ordered snapshot chain (oldest-first by timestamp), Core SHALL post-process the remove list to only include items forming a contiguous oldest prefix. Starting from the oldest snapshot, Core SHALL include items in the final remove list while they are contiguous (each subsequent item is also in the original remove list). When the first item in the original keep list is encountered, the prefix stops. Items in the original remove list that are NOT in the oldest prefix SHALL be moved to the keep list (chain gap fillers).

#### Scenario: Contiguous oldest prefix removed

- **WHEN** one disk's snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s4, s5, s6, s7], remove=[s1, s2, s3]
- **THEN** the final remove list is [s1, s2, s3] (contiguous oldest prefix)
- **AND** the final keep list is [s4, s5, s6, s7]

#### Scenario: Middle snapshots moved to keep

- **WHEN** one disk's snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s1, s5, s6, s7], remove=[s2, s3, s4]
- **THEN** s2 is in remove but s1 (before it) is in keep
- **AND** the contiguous prefix is empty (s1 is in keep, prefix stops immediately)
- **AND** s2, s3, s4 are moved to the keep list (chain gap fillers)
- **AND** the final remove list is empty

#### Scenario: Mixed prefix and gap fillers

- **WHEN** one disk's snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s3, s5, s6, s7], remove=[s1, s2, s4]
- **THEN** s1 is in remove and is the oldest → included in prefix
- **AND** s2 is in remove and follows s1 → included in prefix
- **AND** s3 is in keep → prefix stops
- **AND** s4 is in remove but NOT in the prefix → moved to keep
- **AND** final remove = [s1, s2], final keep = [s3, s4, s5, s6, s7]

#### Scenario: Each disk's oldest-prefix is independent

- **WHEN** VM has two disks: vda with snapshots [s1, s2, s3, s4] and vdb with snapshots [t1, t2, t3, t4]
- **AND** for vda, retention produces keep=[s3, s4], remove=[s1, s2]
- **AND** for vdb, retention produces keep=[t1, t4], remove=[t2, t3]
- **THEN** vda's final remove = [s1, s2] (contiguous prefix)
- **AND** vdb's final remove = [] (prefix stops at t1 which is in keep; t2, t3 are gap fillers)
- **AND** the merged retention result reflects both disks independently

### Requirement: Blockcommit receives per-disk oldest prefix

`Core._blockcommit_snapshots()` SHALL group the remove set by disk target and invoke `_blockcommit_one_disk(vm_config, disk, disk_snapshots)` for each disk. This ensures blockcommit always processes a contiguous range from each disk's base image, which is simpler and more reliable than processing gapped snapshots.

#### Scenario: Blockcommit processes contiguous prefix per disk

- **WHEN** the final remove list for disk `vda` is [s1, s2, s3] (contiguous oldest prefix)
- **THEN** blockcommit merges s1, s2, s3 into `vda`'s base image (oldest-first)
- **AND** no gaps exist in the committed set for `vda`
