# Core Orchestrator — delta

## ADDED Requirements

### Requirement: Commit path intent-journal orchestration

In `Core._blockcommit_one_disk` (main path and deferred drain), Core SHALL write the commit
intent record for the disk (`set_commit_in_progress`) before invoking the lifecycle manager
and SHALL clear it only after the outcome is finalized per the `commit-intent-journal` spec.
On `outcome="success"` the state-write order SHALL be: `set_last_commit_ts` →
`remove_snapshot` per merged snapshot → `clear_commit_in_progress` (last). On definitive
`outcome="failure"` the intent SHALL be cleared before failure classification. On
`outcome="unknown"` the intent SHALL be kept until reconciliation finalizes the outcome.

#### Scenario: Success path ordering

- **WHEN** the manager returns `CommitResult(success=True, outcome="success")` for merge set `["s1"]`
- **THEN** state writes occur in the order `set_last_commit_ts`, `remove_snapshot("s1")`, `clear_commit_in_progress`

#### Scenario: Unknown path keeps intent

- **WHEN** the manager returns `outcome="unknown"`
- **THEN** `clear_commit_in_progress` is NOT called before reconciliation finishes

### Requirement: Unknown commit outcome dispatches reconciliation

When the lifecycle manager returns `outcome="unknown"`, Core SHALL invoke the reconciliation
helper (spec: `commit-reconciliation`) and act on its result: `late_success` → converge
state and continue; `job_active` → defer with reason `"blockjob_active"` and continue the VM
pipeline; `failure` → clear intent and raise `RuntimeError`; `inconclusive` → defer with
reason `"vm_state_unknown"` and continue. Core SHALL NOT raise on the timeout alone.

#### Scenario: Timeout no longer aborts before reconciliation

- **WHEN** the manager returns `CommitResult(success=False, outcome="unknown")`
- **AND** reconciliation returns `late_success`
- **THEN** no `RuntimeError` is raised and the VM pipeline reaches the backup steps

### Requirement: Block-job probe before blockcommit

Before invoking the lifecycle manager for a disk, Core SHALL probe the disk via the shared
block-job helper (spec: `blockjob-protocol`). Only probe result `"none"` SHALL proceed to
commit. `"active"` with an intent record for the disk SHALL trigger reconciliation instead of
a new commit; `"active"` without intent SHALL defer with reason `"blockjob_active"`;
`"error"` SHALL defer with reason `"vm_state_unknown"`. Deferrals SHALL NOT abort the VM
pipeline. The probe applies on the live (`virsh`) executor path; the offline (`qemu-img`)
path skips the probe (it errors on inactive domains) and relies on the fail-closed offline
race guard below.

#### Scenario: Active unknown job defers the commit

- **WHEN** the probe returns `"active"` for `vda` and no intent record exists
- **THEN** the merge set is deferred with reason `"blockjob_active"` and no commit command runs

### Requirement: Block-job probe before snapshot creation

Before `_create_snapshot` for a running VM, Core SHALL probe every disk about to be
snapshotted (spec: `blockjob-protocol`). Any `"active"` or `"error"` result SHALL skip
snapshot creation for the whole VM this run with a WARNING; the change-detection baseline
SHALL remain untouched so the onchange gate stays open. No deferred entry SHALL be created.

#### Scenario: Snapshot creation skipped while a job is active

- **WHEN** the probe for any disk returns `"active"`
- **THEN** no `virsh snapshot-create-as` runs for this VM this run and the baseline is unchanged

### Requirement: Fail-closed offline race guard

When the plan selected `QemuImgCommitManager`, Core re-checks `virsh domstate` immediately
before invoking the manager. If the re-check reports a non-shut-off state, Core defers the
committable subset with reason `"vm_running"` (existing behavior). If the re-check call
FAILS (`ShellResult.success is False`), Core SHALL defer the committable subset with reason
`"vm_state_unknown"` and SHALL NOT invoke `QemuImgCommitManager` — unknown VM state fails
closed. This guard SHALL apply on BOTH commit call sites: the main commit path
(`_blockcommit_one_disk`) and the deferred-queue drain path, in each case before any intent
record is written.

#### Scenario: Recheck failure defers instead of committing

- **WHEN** the plan selected the offline executor and the immediate `virsh domstate` re-check fails
- **THEN** the committable subset is deferred with reason `"vm_state_unknown"`
- **AND** no `qemu-img commit` command is issued

### Requirement: Configurable commit timeout pass-through

Core SHALL pass `GlobalConfig.blockcommit_timeout` as the `timeout` keyword argument on every
lifecycle-manager invocation (main commit path and deferred drain). No hard-coded timeout
value SHALL remain in the commit path.

#### Scenario: Configured timeout reaches the manager

- **WHEN** `blockcommit_timeout = 900` and a commit runs
- **THEN** the manager's `blockcommit` is called with `timeout=900`

### Requirement: Intent recovery in the deferred-operations step

During the deferred-operations step (step 0 of the snapshot steps), Core SHALL process stale
commit intent records per the `commit-intent-journal` crash-recovery requirement, before
evaluating new commits. Dry-run SHALL only predict recovery actions, never write state.

#### Scenario: Stale intent resolved before new commit evaluation

- **WHEN** step 0 runs with a stale intent record for `vda` and no active block job
- **THEN** the intent is reconciled (converged or cleared) before retention/blockcommit evaluation for `vda`
