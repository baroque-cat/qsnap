# Proposal: fix-dry-run-predictions

## Why

Dry-run mode (`qsnap -n`) is architecturally honest for most steps (all mutations are guarded, no state or transaction-log writes), but its predictions are unreliable and one step mutates the system outright:

1. **Bug — mutation during dry-run**: `_check_deferred_operations()` (`qsnap/core/__init__.py:2979-3122`) has no `_dry_run` guard. When deferred blockcommits are queued, `qsnap -n run` executes real `manager.blockcommit()` calls, removes snapshots from state, rewrites the deferred queue, and refreshes domain XML. This violates the core dry-run promise.
2. **Stale retention prediction**: `_create_snapshot()` returns `[]` in dry-run (`core:3136-3141`), so snapshot retention (`core:3242`) and backup steps (`core:4185`) evaluate against state that excludes the would-be-created snapshots. The keep/remove split and the FULL decision shown to the operator do not match what a real run would do.
3. **Silent incremental transfers**: the incremental transfer block (`core:4620-4692`) is skipped without any prediction — no snapshot names, no count, no size estimates.
4. **No size estimates**: the FULL prediction (`core:4498-4511`) logs only `chain_length`; no byte-size estimates anywhere in the pipeline dry-run (fork already demonstrates the read-only `qemu-img info --backing-chain` estimation pattern at `core:1408-1431`).
5. **Single-disk-era log messages**: `_create_snapshot` and `_blockcommit_snapshots` dry-run logs are per-VM counters with no per-disk breakdown, inconsistent with the multi-disk refactor.

Dry-run is invoked infrequently, so spending extra read-only `qemu-img info` calls on accurate prediction is acceptable and desirable.

## What Changes

- **Fix the deferred-operations dry-run leak**: `_check_deferred_operations()` SHALL skip all blockcommit executions and state writes in dry-run and SHALL log a per-disk prediction of what would be drained.
- **Simulated future snapshots**: in dry-run, `_create_snapshot()` SHALL build in-memory `SnapshotInfo` objects (predicted name via `_generate_snapshot_name()`, resolved path, current timestamp, current allocation from read-only `qemu-img info`) for every configured disk, log them per-disk, and return them WITHOUT writing state.
- **Prediction threading**: `_evaluate_snapshot_retention()`, `_execute_backup_steps()`, and `_backup_target()` SHALL accept the simulated snapshots so retention, the per-disk FULL decision, and the incremental transfer list are evaluated against the post-run state.
- **Per-disk blockcommit prediction**: `_blockcommit_snapshots()` SHALL log the remove set grouped by disk with snapshot names instead of a per-VM counter.
- **Size estimates in predictions**: extract the fork chain-size estimation into a reusable helper; FULL predictions SHALL include an estimated size; incremental predictions SHALL include a per-snapshot upper-bound estimate (snapshot file `actual-size`, marked approximate).
- **Structured predictions channel**: `PipelineResult` gains a `predictions: list[ActionRecord]` field, populated only in dry-run. `actions` remains empty in dry-run (existing spec preserved). The CLI summary SHALL render a per-VM/per-disk "planned actions" section when `Dryrun: YES`.
- **Test cleanup and expansion**: outdated dry-run tests and stale comments are identified and removed or updated; new unit tests and new integration tests (real libvirt/qemu) verify the prediction behavior and the zero-mutation invariant.

No ABC (`I`-prefix) interface changes. No state-file schema changes. No config changes.

## Capabilities

### New Capabilities

- `dry-run-prediction`: accurate, per-VM/per-disk prediction of all pipeline actions in dry-run mode — simulated future snapshots, retention/backup evaluation against post-run state, size estimates, structured `predictions` channel, and the zero-mutation invariant.

### Modified Capabilities

- `core-orchestrator`: dry-run pipeline semantics change — simulated snapshots are threaded through retention and backup steps; prediction records are accumulated; `_check_deferred_operations` is guarded.
- `deferred-operations`: dry-run SHALL NOT execute or re-queue deferred blockcommits; it SHALL log what would be drained (per-disk).
- `cli-interface`: dry-run logging requirements — every planned mutation is logged at INFO with VM and disk context and, where computable read-only, a size estimate.
- `backup-summary`: the summary SHALL render the planned-actions section per VM and per disk when `Dryrun: YES`.
- `action-audit-trail`: `PipelineResult` SHALL carry `predictions: list[ActionRecord]` alongside `actions`; predictions are accumulated only in dry-run and are never written to the transaction log.

## Impact

- **Code**:
  - `qsnap/core/__init__.py` — `_check_deferred_operations`, `_create_snapshot`, `_evaluate_snapshot_retention`, `_blockcommit_snapshots`, `_execute_snapshot_steps`, `_execute_backup_steps`, `_backup_target`, new size-estimation helper extracted from `fork()`, prediction accumulation in `_run_pipeline`.
  - `qsnap/models/results.py` — `SnapshotResult` gains optional `disk` field; `PipelineResult` gains `predictions` field (both with defaults — backward compatible).
  - `qsnap/cli/summary.py` — planned-actions rendering for dry-run.
- **Interfaces**: none changed (no BREAKING changes).
- **State schema**: unchanged; dry-run writes nothing.
- **Tests**: `tests/core/test_pipeline.py` dry-run tests updated (stale `_log_size_estimate` comment at line 462 removed); new `tests/core/test_dry_run_prediction.py`; new `tests/integration/test_dry_run.py`; existing integration tests that run pipelines extended with dry-run zero-mutation assertions where relevant. Full inventory of tests to delete/update is produced in `test-plan.md`.
- **Dependencies**: none (pure stdlib preserved).
- **Builds on**: multi-disk refactor and `2026-08-05-fix-per-disk-isolation` archived change (per-disk conventions for logs and records).
