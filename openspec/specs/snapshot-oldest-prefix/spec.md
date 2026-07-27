# Snapshot Oldest-Prefix Retention

## Purpose

Post-processes snapshot retention results to only remove a contiguous oldest prefix. Prevents blockcommit from receiving gapped snapshot lists, which are unreliable and can cause stuck blockcommits. Non-prefix items in the remove list are kept as chain gap fillers.

## Requirements

### Requirement: Oldest-prefix-only snapshot retention

After the retention engine produces keep/remove lists for snapshots, Core SHALL post-process the remove list to only include items forming a contiguous oldest prefix. Starting from the oldest snapshot (by timestamp), Core SHALL include items in the final remove list while they are contiguous (each subsequent item is also in the original remove list). When the first item in the original keep list is encountered, the prefix stops. Items in the original remove list that are NOT in the oldest prefix SHALL be moved to the keep list (chain gap fillers).

#### Scenario: Contiguous oldest prefix removed

- **WHEN** snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s4, s5, s6, s7], remove=[s1, s2, s3]
- **THEN** the final remove list is [s1, s2, s3] (contiguous oldest prefix)
- **AND** the final keep list is [s4, s5, s6, s7]

#### Scenario: Middle snapshots moved to keep

- **WHEN** snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s1, s5, s6, s7], remove=[s2, s3, s4]
- **THEN** s2 is in remove but s1 (before it) is in keep
- **AND** the contiguous prefix is empty (s1 is in keep, prefix stops immediately)
- **THEN** s2, s3, s4 are moved to the keep list (chain gap fillers)
- **AND** the final remove list is empty

#### Scenario: Mixed prefix and gap fillers

- **WHEN** snapshots are [s1, s2, s3, s4, s5, s6, s7] (oldest-first)
- **AND** retention produces keep=[s3, s5, s6, s7], remove=[s1, s2, s4]
- **THEN** s1 is in remove and is the oldest -> included in prefix
- **AND** s2 is in remove and follows s1 -> included in prefix
- **AND** s3 is in keep -> prefix stops
- **AND** s4 is in remove but NOT in the prefix -> moved to keep
- **THEN** final remove = [s1, s2], final keep = [s3, s4, s5, s6, s7]

### Requirement: Blockcommit receives only oldest prefix

`Core._blockcommit_snapshots()` SHALL receive only the final remove list (oldest contiguous prefix) from the post-processed retention result. This ensures blockcommit always processes a contiguous range from the base image, which is simpler and more reliable than processing gapped snapshots.

#### Scenario: Blockcommit processes contiguous prefix

- **WHEN** the final remove list is [s1, s2, s3] (contiguous oldest prefix)
- **THEN** blockcommit merges s1, s2, s3 into the base image (oldest-first)
- **AND** no gaps exist in the committed set
