# Backup Provider — delta

## MODIFIED Requirements

### Requirement: Backup creation work unit run_backup

`BitmapBackupProvider.run_backup(vm_config, target, disk, *, compression_type, stall_timeout, convert_parallel, convert_out_of_order) -> BackupResult` SHALL create exactly one backup for the given disk: a FULL when no qsnap checkpoint exists for this VM+target+disk, otherwise a delta of dirty blocks since the newest checkpoint whose bitmap is healthy. Before choosing the delta path, the provider SHALL probe the newest checkpoint's bitmap health (capability `checkpoint-bitmap-health-probe`): HEALTHY proceeds to the delta; DEAD routes into bitmap-loss recovery (capability `bitmap-loss-recovery` — recovered delta when gates G1-G3 pass, else FULL); UNKNOWN attempts the delta with the reactive backstop as safety net. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point (running VMs). Deltas SHALL use the `INbdClient` pread/pwrite engine with a backing-chained qcow2 delta onto the newest valid backup of this disk; `qemu-img convert` is the sole FULL transfer engine (shared `_full_pull_lifecycle()` helper for all FULL paths). Checkpoints and NBD sockets remain scoped per disk (`qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}`; `/tmp/qsnap-backup-{pid}-{disk}.sock`, `/tmp/qsnap-write-{pid}-{disk}.sock`).

#### Scenario: First backup — full export via qemu-img convert

- **WHEN** no prior qsnap checkpoint exists for this VM+target+disk combination
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>` (running VM) or the source file (stopped VM) and writes to the target qcow2
- **AND** the backup is a standalone qcow2 file named with the FULL scheme
- **AND** no `INbdClient` pread/pwrite loop runs

#### Scenario: Incremental backup — dirty blocks only

- **WHEN** a prior qsnap checkpoint exists for this VM+target+disk with a HEALTHY bitmap
- **AND** the VM has written data since that checkpoint
- **THEN** the `INbdClient` pread/pwrite engine transfers dirty∩allocated extents with `zero_skip=False`
- **AND** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Dead bitmap routes to recovery instead of failing

- **WHEN** a prior checkpoint exists but its bitmap probe returns DEAD
- **THEN** `run_backup` does not return failure on this condition alone
- **AND** the recovery path produces either a recovered delta or a FULL
- **AND** the returned `BackupResult.kind` reflects the produced backup kind

#### Scenario: Checkpoint rotation after successful transfer

- **WHEN** the backup completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **AND** all superseded (older) qsnap checkpoints for the same VM+target+disk are deleted via `virsh checkpoint-delete` with `--metadata` fallback
- **AND** exactly one qsnap checkpoint remains for this VM+target+disk

#### Scenario: Backup failure preserves prior checkpoint

- **WHEN** the backup fails (NBD error, stall, or verification)
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the provider returns `BackupResult(success=False, error=<message>, disk=<disk>)`
- **AND** the NBD sockets and qemu-nbd process are cleaned up

#### Scenario: A second run_backup in the same batch uses the successor as baseline

- **WHEN** Core invokes `run_backup` for the same disk again after a successful backup
- **THEN** the newest-wins discovery selects the successor checkpoint created by the previous invocation
- **AND** the new delta chains onto the previous backup file (gap-free chain)

## ADDED Requirements

### Requirement: Read-only baseline assessment for dry-run parity

`IBackupProvider` SHALL provide a read-only method `assess_baseline(vm_config, target, disk)` returning a frozen assessment result with: baseline status (`no_checkpoint | healthy | dead | unknown`), the newest checkpoint name when present, the recovery gate outcome and failed-gate reason when the checkpoint is dead, and the size estimate for the backup kind a real run would produce. The method SHALL issue only read-only shell commands and SHALL perform zero mutations. Core's dry-run prediction SHALL consume this method instead of re-implementing checkpoint inspection. All implementations and mocks SHALL implement the method (BREAKING interface change).

#### Scenario: Dry-run consumes assessment without mutation

- **WHEN** Core's dry-run calls `assess_baseline` for a disk with a dead checkpoint
- **THEN** the assessment reports `dead`, the gate outcome, and the predicted size
- **AND** no checkpoint, file, or state mutation occurs anywhere in the call

#### Scenario: Mock implements the assessment contract

- **WHEN** `MockBackupProvider.assess_baseline` is called in Core tests
- **THEN** it returns a valid assessment result object (never None)
- **AND** passes the contract tests parametrized over all implementations

### Requirement: Backup results carry the backup kind

`BackupResult` SHALL carry a `kind` field with value `"full"`, `"delta"`, or `"recovered_delta"` identifying how the backup was produced. Core SHALL record the kind in the action audit trail, and the summary renderer SHALL display recovered deltas distinctly. The field SHALL default to `"delta"`-compatible behavior for existing constructors only if explicitly defaulted; otherwise every producer SHALL set it.

#### Scenario: Recovered delta is auditable

- **WHEN** a recovered delta completes successfully
- **THEN** `BackupResult.kind` is `"recovered_delta"`
- **AND** the action record and summary identify it as a recovered delta

#### Scenario: Regular paths keep their kinds

- **WHEN** a normal FULL or delta completes
- **THEN** `BackupResult.kind` is `"full"` or `"delta"` respectively
