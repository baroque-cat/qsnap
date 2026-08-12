# Blockjob Protocol

## ADDED Requirements

### Requirement: Shared block-job probe

Core SHALL provide a single shared helper that probes the block-job state of one disk:
`virsh blockjob --domain <vm> --path <disk>` via `IShell` with a 30-second timeout, returning
one of `"none"`, `"active"`, or `"error"`. Output containing "No current block job" (or an
empty job report) SHALL classify as `"none"`; any output describing a job SHALL classify as
`"active"`; a failed probe call (non-zero exit, timeout, parse failure) SHALL classify as
`"error"`. The existing backup-path blockjob probe SHALL be refactored onto this helper
without changing its observable behavior (backup deferral on active job).

#### Scenario: No job reported

- **WHEN** `virsh blockjob` outputs "No current block job" for disk `vda`
- **THEN** the probe returns `"none"`

#### Scenario: Active job reported

- **WHEN** `virsh blockjob` outputs a job description (e.g. a blockcommit with progress) for disk `vda`
- **THEN** the probe returns `"active"`

#### Scenario: Probe call fails

- **WHEN** `virsh blockjob` exits non-zero or times out
- **THEN** the probe returns `"error"`

#### Scenario: Backup path behavior unchanged

- **WHEN** the backup step probes a disk with an active block job
- **THEN** the disk's backup is deferred exactly as before (INFO log, no baseline update, not a failure)

### Requirement: Probe before blockcommit

Before invoking the lifecycle manager for a disk, Core SHALL probe the disk's block-job state:

- `"none"` → proceed with the commit.
- `"active"` AND a commit intent record exists for this disk → treat the job as qsnap's own
  probable zombie and run the reconciliation protocol instead of starting a new commit.
- `"active"` AND no intent record → unknown job: defer the disk's commit set with reason
  `"blockjob_active"`, log a WARNING naming the VM, disk, and the detected job, and continue
  the VM pipeline.
- `"error"` → fail closed: defer with reason `"vm_state_unknown"` and log a WARNING.

Core SHALL NEVER issue `virsh blockcommit` against a disk whose probe is not `"none"`.

The probe SHALL apply on the live (`virsh`) executor path. On the offline (`qemu-img`)
executor path the probe is skipped by design: `virsh blockjob` errors on inactive domains, so
an unconditional probe would fail-closed-block every legitimate offline commit. The offline
path is protected instead by the fail-closed offline race guard (an immediate `virsh
domstate` re-check before the manager, spec: `core-orchestrator`).

#### Scenario: Unknown active job blocks a new commit

- **WHEN** retention marks snapshots for commit on `vda`
- **AND** the probe returns `"active"` and no intent record exists for `vda`
- **THEN** `add_deferred_blockcommit("vm1", "vda", <remove set>, "blockjob_active")` is called
- **AND** no `virsh blockcommit` command is issued for `vda`
- **AND** a WARNING is logged and the VM pipeline continues

#### Scenario: Own zombie job is reconciled, not clobbered

- **WHEN** the probe returns `"active"` for `vda` and an intent record exists for `vda`
- **THEN** Core runs reconciliation instead of starting a new commit

#### Scenario: Probe error fails closed

- **WHEN** the probe returns `"error"` for `vda`
- **THEN** the commit set is deferred with reason `"vm_state_unknown"` and no commit is started

### Requirement: Probe before snapshot creation

Before creating snapshots for a RUNNING VM, Core SHALL probe the block-job state of every
disk about to be snapshotted. If any probe returns `"active"` or `"error"`, Core SHALL skip
snapshot creation for this VM this run, log a WARNING naming the VM and the affected disk(s),
and continue the pipeline. The change-detection baseline SHALL NOT be updated, so the
onchange gate stays open and the next run retries. No deferred-queue entry SHALL be created
for a skipped snapshot creation. For a VM that is not running, the probe SHALL be skipped.

#### Scenario: Active job defers snapshot creation

- **WHEN** snapshot creation is due for `vm1` disks `["vda"]`
- **AND** the probe for `vda` returns `"active"`
- **THEN** no `virsh snapshot-create-as` command is issued for `vm1`
- **AND** a WARNING is logged
- **AND** the last-allocation baseline for `vda` is unchanged
- **AND** the next run re-evaluates change detection normally

#### Scenario: All disks clear — snapshot proceeds

- **WHEN** every disk probe returns `"none"`
- **THEN** snapshot creation proceeds exactly as before

#### Scenario: Stopped VM skips the probe

- **WHEN** the VM is not running
- **THEN** no blockjob probe is issued before snapshot creation

### Requirement: No automatic block-job abort

qsnap SHALL NEVER issue `virsh blockjob --abort` (or any job-cancelling command)
automatically. WARNING messages emitted for active unknown or zombie jobs SHALL name the VM,
the disk, and the observed job so an operator can abort it manually; qsnap SHALL only observe
and defer.

#### Scenario: Zombie job is deferred, never aborted

- **WHEN** an active job is detected on `vda` before commit
- **THEN** no command containing `blockjob --abort` is issued
- **AND** the WARNING text contains the VM name and disk `vda`
