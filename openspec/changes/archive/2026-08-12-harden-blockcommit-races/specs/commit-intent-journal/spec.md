# Commit Intent Journal

## ADDED Requirements

### Requirement: CommitIntent model and IStateManager journal API

The system SHALL provide a frozen `CommitIntent` dataclass in `qsnap/models/` with fields
`disk: str`, `snapshots: list[str]` (merge set, oldest first), `base: str` (base image path),
and `started_ts: str` (`YYYYMMDDTHHMMSS`). `IStateManager` SHALL provide:

- `set_commit_in_progress(vm_name: str, disk: str, snapshots: list[str], base: str, started_ts: str) -> None`
  — upsert the intent record for `(vm_name, disk)` (at most one record per disk).
- `get_commit_in_progress(vm_name: str) -> list[CommitIntent]` — all intent records of the VM.
- `clear_commit_in_progress(vm_name: str, disk: str) -> None` — remove the record for the disk.

All implementations of `IStateManager` (`JsonStateManager`, `InMemoryStateManager`, test
mocks) SHALL implement these methods.

#### Scenario: Set, read, and clear an intent record

- **WHEN** `set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")` is called
- **THEN** `get_commit_in_progress("vm1")` returns one `CommitIntent` with those exact fields
- **AND** after `clear_commit_in_progress("vm1", "vda")`, `get_commit_in_progress("vm1")` returns an empty list

#### Scenario: Upsert replaces the record for the same disk

- **WHEN** `set_commit_in_progress` is called twice for `("vm1", "vda")` with different snapshot lists
- **THEN** `get_commit_in_progress("vm1")` returns exactly one record holding the latest values

#### Scenario: Multiple disks hold independent intent records

- **WHEN** intent records are set for `("vm1", "vda")` and `("vm1", "vdb")`
- **THEN** `get_commit_in_progress("vm1")` returns two records
- **AND** clearing `vda` leaves the `vdb` record intact

### Requirement: Atomic persistence with backward-compatible reads

`JsonStateManager` SHALL persist intent records under the top-level key `commit_in_progress`
of the per-VM state file (`{vm}.json`) as a list of objects, using the existing atomic
tmp-file + `os.replace` write path. A state file without the key SHALL load as an empty list
(no migration required). A record written by `set_commit_in_progress` SHALL be durable on disk
before the method returns.

#### Scenario: Old state file without commit_in_progress

- **WHEN** a state JSON file predates this feature and lacks `commit_in_progress`
- **THEN** `get_commit_in_progress` returns an empty list and no error is raised

#### Scenario: Intent survives a state round-trip

- **WHEN** an intent record is written and the state manager is re-instantiated from the same file
- **THEN** `get_commit_in_progress` returns the identical record

### Requirement: Intent written before the irreversible commit

Core SHALL call `set_commit_in_progress` for a disk BEFORE invoking the lifecycle manager for
that disk, with the exact merge set and base image about to be committed. The intent record
SHALL exist on disk before the first `virsh blockcommit` / `qemu-img commit` command of the
merge set is spawned.

#### Scenario: Intent precedes the manager call

- **WHEN** Core reaches the commit step for disk `vda` with merge set `["s1"]`
- **THEN** `set_commit_in_progress("vm1", "vda", ["s1"], <base>, <now>)` is called before `manager.blockcommit(...)`
- **AND** the mock/recorded call order shows the state write first

### Requirement: Intent cleared only after the outcome is finalized

Core SHALL clear the intent record for a disk only after the commit outcome is final:

- `outcome="success"` → after `set_last_commit_ts` AND `remove_snapshot` for all merged
  snapshots (intent clear is the LAST state write of the commit step).
- `outcome="unknown"` → only after reconciliation finalizes the outcome: cleared on
  `late_success` (after the convergence state writes) and on `failure`; kept on `job_active`
  and `inconclusive`.
- `outcome="failure"` (definitive) → cleared before the failure classification raises or
  defers, because the manager's short-circuit contract guarantees the chain is unchanged.

Core SHALL NOT clear the intent record at any earlier point.

#### Scenario: Success ordering — intent cleared last

- **WHEN** the manager returns `outcome="success"` for merge set `["s1"]`
- **THEN** the recorded state-write order is `set_last_commit_ts`, `remove_snapshot("s1")`, then `clear_commit_in_progress`

#### Scenario: Definitive failure clears the intent

- **WHEN** the manager returns `outcome="failure"` with a non-MAC, non-space error
- **THEN** the intent record for the disk is cleared before `RuntimeError` is raised

#### Scenario: Unknown outcome keeps intent until reconciliation decides

- **WHEN** the manager returns `outcome="unknown"`
- **THEN** the intent record is NOT cleared before reconciliation runs
- **AND** it is kept when reconciliation returns `job_active` or `inconclusive`

### Requirement: Crash recovery of stale intent records

At the start of a VM's pipeline (with the deferred-operations check, before snapshot steps),
Core SHALL read the VM's intent records. For each record Core SHALL probe
`virsh blockjob --domain <vm> --path <disk>` (30 s):

- Active job → keep the intent record, skip commits for this disk this run, log a WARNING
  naming the disk and the in-flight job, and add/refresh a deferred entry with reason
  `"blockjob_active"`.
- No active job → reconcile reality per the reconciliation protocol: merge-set files gone and
  chain shorter → perform the late-success state convergence (WARNING "commit completed after
  previous run timed out"), clear the intent; chain unchanged → clear the intent with a
  WARNING that the previous run died during a commit attempt with no effect; contradictory or
  probe failure → keep the intent and defer with reason `"vm_state_unknown"`.

Dry-run mode SHALL NOT clear or write intent records and SHALL only predict the recovery
action.

#### Scenario: Stale intent with a completed job self-heals

- **WHEN** a run starts with an intent record for `vda` from a crashed previous run
- **AND** `virsh blockjob` reports no active job and the merge-set file is gone
- **THEN** Core writes `set_last_commit_ts` and `remove_snapshot` for the merged snapshots
- **AND** clears the intent record with a WARNING log line

#### Scenario: Stale intent with a live job defers

- **WHEN** a run starts with an intent record for `vda`
- **AND** `virsh blockjob` reports an active job on `vda`
- **THEN** the intent record is kept, a deferred entry with reason `"blockjob_active"` exists, and no commit is started on `vda` this run

#### Scenario: Stale intent with no effect is discarded

- **WHEN** a run starts with an intent record for `vda`
- **AND** no block job is active, all merge-set files exist, and the chain length is unchanged
- **THEN** the intent record is cleared with a WARNING and the disk proceeds to normal retention/commit evaluation
