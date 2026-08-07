# Startup State Validation — delta

## ADDED Requirements

### Requirement: Orphan checkpoint invariant at startup

At pipeline startup, for each configured (target, disk) of each VM, the system SHALL verify
that the newest `qsnap-{target_hash}-{disk}-*` checkpoint has a corresponding backup file on
the target whose `mtime` is greater than or equal to the checkpoint's name timestamp. If no
such file exists, the checkpoint is an orphan of a crashed or killed export (created at
`backup-begin` but never completed to a final file) and the system SHALL delete it
best-effort via `virsh checkpoint-delete` with `--metadata` fallback and log a WARNING naming
the checkpoint and target. Deleting the orphan makes the previous checkpoint newest again, so
the next export re-covers the interval with no coverage gap. The invariant SHALL be non-fatal:
deletion failures log a WARNING and the pipeline continues. When freeze-timestamp backup
naming is in effect (capability `backup-target-orthogonality`), the check MAY match by exact
freeze-ts equality between checkpoint name and backup file name instead of `mtime`.

#### Scenario: Orphan checkpoint deleted at startup

- **WHEN** the newest checkpoint for (target, vda) is `qsnap-abc12345-vda-20260808T030000-aa11bb`
- **AND** no backup file on the target has `mtime >= 2026-08-08T03:00:00`
- **THEN** the checkpoint is deleted best-effort
- **AND** a WARNING log names the checkpoint and the target
- **AND** the next backup for that disk uses the previous checkpoint as baseline

#### Scenario: Healthy checkpoint kept

- **WHEN** the newest checkpoint has a matching backup file with `mtime >= checkpoint ts`
- **THEN** the checkpoint is kept and no warning is logged

#### Scenario: Invariant failure is non-fatal

- **WHEN** checkpoint deletion fails (e.g., libvirt error)
- **THEN** a WARNING is logged
- **AND** the pipeline continues without raising
