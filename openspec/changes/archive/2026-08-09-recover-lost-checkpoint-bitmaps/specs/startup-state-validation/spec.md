# Startup State Validation — delta

## MODIFIED Requirements

### Requirement: Orphan checkpoint invariant at startup

At pipeline startup, for each configured (target, disk) of each VM, the system SHALL verify that the newest `qsnap-{target_hash}-{disk}-*` checkpoint has a corresponding backup file on the target whose `mtime` is greater than or equal to the checkpoint's name timestamp. If no such file exists, the checkpoint is an orphan of a crashed or killed export (created at `backup-begin` but never completed to a final file) and the system SHALL delete it best-effort via `virsh checkpoint-delete` with `--metadata` fallback and log a WARNING naming the checkpoint and target. Deleting the orphan makes the previous checkpoint newest again, so the next export re-covers the interval with no coverage gap. The invariant SHALL be non-fatal: deletion failures log a WARNING and the pipeline continues. When freeze-timestamp backup naming is in effect (capability `backup-target-orthogonality`), the check MAY match by exact freeze-ts equality between checkpoint name and backup file name instead of `mtime`.

Additionally, a checkpoint whose bitmap health probe (capability `checkpoint-bitmap-health-probe`) returns DEAD SHALL be treated as an orphan even when a covering backup file exists: its bitmap no longer exists, so it can never serve as a delta baseline. In real runs the system SHALL delete the dead checkpoint best-effort and log a WARNING; in dry-run the system SHALL log a `[dry-run] Would remove dead-bitmap checkpoint ...` prediction and SHALL NOT delete anything. More generally, every checkpoint deletion performed by this invariant SHALL be guarded by the dry-run flag: dry-run executions SHALL only predict.

#### Scenario: Orphan checkpoint deleted at startup

- **WHEN** the newest checkpoint for (target, vda) is `qsnap-abc12345-vda-20260808T030000-aa11bb`
- **AND** no backup file on the target has `mtime >= 2026-08-08T03:00:00`
- **THEN** the checkpoint is deleted best-effort
- **AND** a WARNING log names the checkpoint and the target
- **AND** the next backup for that disk uses the previous checkpoint as baseline

#### Scenario: Healthy checkpoint kept

- **WHEN** the newest checkpoint has a matching backup file with `mtime >= checkpoint ts`
- **AND** its bitmap probe returns HEALTHY
- **THEN** the checkpoint is kept and no warning is logged

#### Scenario: Dead-bitmap checkpoint removed despite covering file

- **WHEN** the newest checkpoint has a covering backup file but its bitmap probe returns DEAD
- **THEN** the checkpoint is deleted best-effort in a real run with a WARNING
- **AND** the next backup for that disk starts a new FULL

#### Scenario: Dry-run predicts without deleting

- **WHEN** the invariant finds an orphan or dead-bitmap checkpoint during a dry-run
- **THEN** it logs a `[dry-run] Would remove ...` prediction
- **AND** `virsh checkpoint-delete` is NOT executed

#### Scenario: Invariant failure is non-fatal

- **WHEN** checkpoint deletion fails (e.g., libvirt error)
- **THEN** a WARNING is logged
- **AND** the pipeline continues without raising
