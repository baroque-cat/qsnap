# Commit Reconciliation

## Purpose

Reconciliation of `"unknown"` commit outcomes (client timeout/kill) against observed disk
reality. Core converges state on late success and defers fail-closed when reality cannot be
determined.

## Requirements

### Requirement: Three-valued commit outcome classification

Core SHALL distinguish three commit outcomes based on `CommitResult.outcome`: `"success"`,
`"failure"`, and `"unknown"`. An `"unknown"` outcome (command timed out, was killed, or the
process crashed mid-commit) SHALL NEVER be treated as a definitive failure: Core MUST NOT
report the VM as FAILED on `"unknown"` alone, and MUST run the reconciliation protocol before
any state mutation or pipeline abort. A definitive `"failure"` outcome SHALL follow the
existing classification path (MAC denial deferral, ENOSPC deferral, otherwise `RuntimeError`).

#### Scenario: Timeout outcome is classified unknown, not failure

- **WHEN** the lifecycle manager returns `CommitResult(success=False, outcome="unknown", error="Command timed out after 1800s")`
- **THEN** Core does NOT raise `RuntimeError` immediately
- **AND** Core does NOT record the VM as failed before reconciliation completes
- **AND** Core invokes the reconciliation protocol for that disk

#### Scenario: Definitive failure still aborts the VM

- **WHEN** the lifecycle manager returns `CommitResult(success=False, outcome="failure", error="No such file or directory")`
- **AND** the error is neither a MAC denial nor a space error
- **THEN** Core raises `RuntimeError`, aborting the remaining steps of this VM

### Requirement: Post-unknown reconciliation protocol

After an `"unknown"` commit outcome Core SHALL reconcile against observed reality via a helper
that returns one of `late_success`, `job_active`, `failure`, or `inconclusive`, together with
the verified merged set (the contiguous oldest prefix of the merge set confirmed gone):

1. Probe `virsh blockjob --domain <vm> --path <disk>` (30 s timeout). If a block job is
   reported active, the outcome is `job_active`. If the probe call itself fails, the outcome is
   `inconclusive`.
2. With no active job, inspect the merge set oldest first: for each snapshot, check
   `os.path.exists(snap.path)` and measure the backing-chain length via
   `qemu-img info --backing-chain`.
3. Determine `k`: the largest number such that the first `k` entries of the merge set (oldest
   first) form a contiguous prefix whose files are ALL absent from disk. A deletion pattern
   that is not an oldest prefix (an older file present while a newer one is absent) is a
   contradiction and yields `inconclusive` regardless of `k`.
4. `k` equals the merge-set size AND the chain length decreased accordingly → `late_success`
   with the full merge set verified (the job completed after the client died; `--delete`
   removed the files). `0 < k < merge-set size` AND the chain length decreased by exactly
   `k` → `late_success` with the first `k` snapshots verified (partial completion: a multi-
   snapshot commit timed out between two per-snapshot jobs). `k = 0` AND the chain length
   unchanged → `failure` (the job died without effect).
5. Any contradiction (chain length inconsistent with `k`, measurement failure) →
   `inconclusive`.

Classification SHALL require the file check and the chain-length check to agree; when they
disagree the outcome MUST be `inconclusive`. Agreement is quantitative when a pre-commit
chain-length baseline is available (the post-timeout dispatch path captures it before
invoking the manager): `late_success` requires the chain to have shrunk by exactly the
verified prefix size `k`, `failure` requires an unchanged length, and any other delta yields
`inconclusive`. Step-0 crash recovery invokes the same protocol without a pre-commit
baseline (the crashed run never recorded one); in that case the file evidence is
corroborated by chain measurability alone. For a single-snapshot merge set this protocol is
byte-for-byte equivalent to the previous all-or-nothing rules (`k` is either 0 or 1).

#### Scenario: Late success detected after client timeout

- **WHEN** reconciliation runs after an `"unknown"` outcome for merge set `["s1"]`
- **AND** `virsh blockjob` reports no active job
- **AND** `s1.qcow2` no longer exists on disk and the backing chain is one layer shorter
- **THEN** the outcome is `late_success` with verified set `["s1"]`

#### Scenario: Job still active after timeout

- **WHEN** reconciliation runs after an `"unknown"` outcome
- **AND** `virsh blockjob` reports an active block job on the disk
- **THEN** the outcome is `job_active`

#### Scenario: Dead job with no effect

- **WHEN** reconciliation runs after an `"unknown"` outcome
- **AND** `virsh blockjob` reports no active job
- **AND** all merge-set files still exist and the chain length is unchanged
- **THEN** the outcome is `failure`

#### Scenario: Contradictory evidence is inconclusive

- **WHEN** the merge set is `["s1", "s2"]` and `s1.qcow2` is gone but `s2.qcow2` exists and the chain length is unchanged
- **THEN** the outcome is `inconclusive` because the chain delta (0) disagrees with the vanished prefix size (1)

#### Scenario: Probe failure is inconclusive

- **WHEN** the `virsh blockjob` probe itself fails (non-zero exit or timeout)
- **THEN** the outcome is `inconclusive` and no file or chain inspection overrides it

#### Scenario: Partial oldest prefix verified after multi-snapshot timeout

- **WHEN** reconciliation runs after an `"unknown"` outcome for merge set `["s1", "s2", "s3"]` committed oldest first
- **AND** `virsh blockjob` reports no active job
- **AND** `s1.qcow2` is absent while `s2.qcow2` and `s3.qcow2` still exist
- **AND** the backing chain is exactly one layer shorter than the pre-commit baseline
- **THEN** the outcome is `late_success` with verified set `["s1"]`

#### Scenario: Prefix size disagreeing with chain delta is inconclusive

- **WHEN** the merge set is `["s1", "s2"]` and `s1.qcow2` and `s2.qcow2` are both absent
- **AND** the backing chain is only one layer shorter than the pre-commit baseline
- **THEN** the outcome is `inconclusive` because the chain delta (1) disagrees with the vanished prefix size (2)

#### Scenario: Non-contiguous deletion pattern is inconclusive

- **WHEN** the merge set is `["s1", "s2"]` and `s1.qcow2` still exists while `s2.qcow2` is absent
- **THEN** the outcome is `inconclusive` regardless of the measured chain delta

### Requirement: Late-success state convergence

When reconciliation yields `late_success`, Core SHALL converge state with reality for the
verified merged set: log a WARNING naming the VM, disk, and verified merged snapshots
("commit completed after client timeout"), call `set_last_commit_ts` for the disk, and call
`remove_snapshot` for every verified merged snapshot. When the verified set equals the full
merge set, Core SHALL clear the commit intent record for the disk. When the verified set is
a strict oldest prefix of the merge set (partial completion), Core SHALL rewrite the commit
intent record to the remaining suffix instead of clearing it, so the suffix is retried by the
next reconciliation or commit attempt, and the WARNING SHALL also name the still-pending
snapshots. In both cases the pipeline continues (post-commit chain verification still
applies to the converged state) and the VM SHALL NOT be marked failed.

#### Scenario: State synced after late success

- **WHEN** reconciliation returns `late_success` for disk `vda` with verified set `["s1"]` equal to the full merge set
- **THEN** `set_last_commit_ts("vm1", "vda", <now>)` is called
- **AND** `remove_snapshot("vm1", "s1")` is called
- **AND** the intent record for `vda` is cleared
- **AND** a WARNING log line is emitted
- **AND** the VM pipeline continues to the backup steps

#### Scenario: Partial late success converges the prefix and keeps the suffix

- **WHEN** reconciliation returns `late_success` for disk `vda` with merge set `["s1", "s2", "s3"]` and verified set `["s1"]`
- **THEN** `remove_snapshot("vm1", "s1")` is called and `s2`, `s3` remain in state
- **AND** the intent record for `vda` is rewritten to merge set `["s2", "s3"]`
- **AND** a WARNING log line names both the converged snapshot and the pending suffix
- **AND** the VM pipeline continues without being marked failed

### Requirement: Job-active and inconclusive outcomes fail closed

When reconciliation yields `job_active`, Core SHALL add a deferred blockcommit entry for the
disk with reason `"blockjob_active"`, keep the commit intent record, skip this disk for the
current run, and SHALL NOT treat the VM as failed. When reconciliation yields `inconclusive`,
Core SHALL add a deferred entry with reason `"vm_state_unknown"`, keep the intent record, and
skip the disk. In neither case SHALL Core start a new blockcommit on the disk or abort the VM
pipeline.

#### Scenario: Active job defers the disk without failing the VM

- **WHEN** reconciliation returns `job_active` for disk `vda`
- **THEN** `add_deferred_blockcommit("vm1", "vda", <merge set>, "blockjob_active")` is called
- **AND** the intent record for `vda` is kept
- **AND** the VM pipeline continues

#### Scenario: Inconclusive reconciliation defers fail-closed

- **WHEN** reconciliation returns `inconclusive` for disk `vda`
- **THEN** `add_deferred_blockcommit("vm1", "vda", <merge set>, "vm_state_unknown")` is called
- **AND** no state write for the merge set occurs
- **AND** the VM pipeline continues

### Requirement: True failure after reconciliation aborts with diagnostics

When reconciliation yields `failure`, Core SHALL clear the commit intent record for the disk
and raise `RuntimeError`, aborting the remaining steps of this VM. The error message SHALL
include the original timeout error and a remediation hint to inspect `virsh blockjob` and the
libvirtd journal.

#### Scenario: Dead job aborts the VM with a hint

- **WHEN** reconciliation returns `failure` after an `"unknown"` outcome
- **THEN** the intent record for the disk is cleared
- **AND** `RuntimeError` is raised whose message mentions the timeout and `virsh blockjob`
- **AND** other VMs in the pipeline are still processed
