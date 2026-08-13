## ADDED Requirements

### Requirement: Scaled timeout budget for bulk collapse

For the live bulk collapse Core SHALL pass an effective timeout of `blockcommit_timeout × len(committable)` to the lifecycle manager, preserving the documented per-layer budget semantics of `blockcommit_timeout` while letting a single multi-layer job run to completion. The intent log line SHALL show the scaled value. On the offline path the per-layer meaning is unchanged (each `qemu-img commit` iteration keeps the unscaled `blockcommit_timeout`). When the scaled budget expires, the outcome is `"unknown"` and the existing reconciliation/deferral machinery applies unchanged.

#### Scenario: Budget scales with the merge set

- **WHEN** `blockcommit_timeout = 1800` and the committable set has 49 snapshots
- **THEN** the lifecycle manager receives `timeout=88200` for the single bulk job

#### Scenario: Offline budget stays per layer

- **WHEN** the same 49-snapshot set is committed offline via `QemuImgCommitManager`
- **THEN** each `qemu-img commit` call uses the unscaled `blockcommit_timeout`

### Requirement: Pre-commit chain-length baseline derived from the integrity scan

When `chain_verify_before_commit` is enabled, Core SHALL obtain the pre-commit chain-length baseline (`chain_length_before`) from the result of the pre-commit backing-chain integrity scan instead of issuing a second full `qemu-img info --backing-chain` walk. A separate `_get_chain_length` measurement SHALL be issued only when the pre-commit verification is disabled or its result carries no measured length. Post-commit measurement remains an independent fresh walk. This removes one duplicated full-chain traversal per batch without weakening any verification.

#### Scenario: Baseline reused from the scan

- **WHEN** `chain_verify_before_commit = True` and the pre-commit scan succeeds over a 73-file chain
- **THEN** `chain_length_before` equals 73
- **AND** no additional `qemu-img info --backing-chain` command is executed before the commit

#### Scenario: Fallback when verification is disabled

- **WHEN** `chain_verify_before_commit = False`
- **THEN** Core measures the baseline via its own `qemu-img info --backing-chain` call as before

## MODIFIED Requirements

### Requirement: Hysteresis retention evaluation flow
`Core._evaluate_disk_retention` SHALL branch on the VM's resolved `snapshot_retention_mode`. In `"steady"` mode behavior is unchanged. In `"hysteresis"` mode Core SHALL: (1) read the disk's snapshot count `N`; (2) if `N ≤ H`, return an empty remove set; (3) if `N > H`, invoke the pure retention engine with effective keep-count `L`, apply the oldest-prefix filter and the preserve_min floor trim, and mark the resulting FULL oldest `N − L` set for commit within the same run — no per-run cap truncation and no persisted phase marker exist. The retention engine itself SHALL remain a pure function unaware of modes.

#### Scenario: Steady mode untouched
- **WHEN** the mode is `"steady"`
- **THEN** evaluation produces exactly the pre-existing keep/remove result

#### Scenario: Hysteresis collapse evaluation
- **WHEN** the mode is `"hysteresis"`, `H = 72`, `L = 24`, `N = 73`
- **THEN** the engine is invoked with effective keep-count 24
- **AND** the final remove set is ALL 49 oldest snapshots
- **AND** no phase marker is written anywhere

#### Scenario: Below threshold
- **WHEN** the mode is `"hysteresis"` and `N = 50`, `H = 72`
- **THEN** the remove set is empty

## REMOVED Requirements

### Requirement: Collapse phase completion handling
**Reason**: With single-shot bulk collapse there is no multi-run phase to complete: after a successful commit the disk is already at the floor `L` by construction (the whole `N − L` set was merged), and deferred or failed collapses re-trigger naturally on the next run via `N > H`. The post-commit re-read used only to drive phase bookkeeping.

**Migration**: Delete the post-commit phase-convergence block from `Core._dispatch_commit_outcome` and the `collapse_in_progress` reads/writes from `_evaluate_disk_retention`. Success observability is preserved by the bulk collapse success line (capability `commit-observability`); the post-commit chain-length VERIFICATION (capability `chain-integrity-verification`) is unaffected and still runs.
