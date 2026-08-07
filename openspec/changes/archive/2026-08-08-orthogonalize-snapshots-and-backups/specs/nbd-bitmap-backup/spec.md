# NBD Bitmap Backup — delta

## REMOVED Requirements

### Requirement: Temporal mismatch detection

**Reason:** The check compared two unrelated time scales — the checkpoint-name wall-clock
timestamp (seconds, created at `backup-begin` time) against `SnapshotInfo.timestamp`
(microseconds, recorded at snapshot creation). A checkpoint created FOR a snapshot always
embeds a later timestamp than that snapshot, so the check fired on legitimate data by
construction and permanently blocked targets (production incident 2026-08-07). It also
contradicted design D3 chaining, which intends a successor checkpoint created mid-loop to be
the baseline of the next export. In the chain model (every export against the newest
checkpoint, every delta chained onto the previous backup) the FULL←delta chain is gap-free by
construction, so the protection is unnecessary.

**Migration:** None required. The backup phase no longer consumes snapshot timestamps
(capability `backup-target-orthogonality`); the size-based sanity check below remains as a
diagnostic.

## RENAMED Requirements

- FROM: `Size-based sanity check for temporal mismatch`
- TO: `Size-based sanity check for incremental transfer`

## MODIFIED Requirements

### Requirement: Size-based sanity check for incremental transfer

After incremental transfer, if the transferred bytes exceed 10× the expected delta upper
bound, a WARNING SHALL be logged indicating a possible stale bitmap or write burst. The
expected delta upper bound SHALL be the growth of the source disk's active-layer allocation
since the last successful backup of this disk+target (from `_target_state.json`
`last_backup_allocation`); when no baseline exists, the check SHALL be skipped. This is a
diagnostic warning only — the transfer is not aborted. The check SHALL NOT reference snapshot
allocation or snapshot timestamps.

#### Scenario: Large transfer triggers warning

- **WHEN** an incremental transfer transfers 15 GiB and the active-layer allocation grew by
  100 MiB since the last backup of this disk
- **THEN** a WARNING is logged naming the disk, target, dirty bytes, and expected bound
- **AND** the transfer is NOT aborted (warning only)

#### Scenario: No baseline skips the check

- **WHEN** no `last_backup_allocation` baseline exists for this disk+target
- **THEN** the sanity check is skipped without a warning
