## 1. F1/F3 — Snapshot and blockcommit abort model (Core)

Specs: `specs/core-orchestrator/spec.md`, `specs/blockcommit-recovery/spec.md`, `specs/chain-integrity-verification/spec.md`. Design: D1, D2, D6.

- [x] 1.1 `_create_snapshot`: missing per-disk snapshot dir → `raise RuntimeError`; snapshot create failure → `raise RuntimeError` (was log+continue)
- [x] 1.2 `_blockcommit_one_disk`: unify both pre-commit verification branches → CRITICAL + `raise RuntimeError` (message includes `Break at: {broken_file}` and `qsnap check --deep` hint); remove the partial-blockcommit branch, the `stuck` variable and the auto-rebase call
- [x] 1.3 `_blockcommit_one_disk`: non-MAC commit failure → `raise RuntimeError`; post-commit chain-length-unchanged → `raise RuntimeError`; MAC denial stays deferred
- [x] 1.4 Delete `_split_at_break` and `_auto_rebase_stuck`; delete `tests/integration/test_blockcommit_recovery.py`

## 2. F4/F5 — Backup abort model

Specs: `specs/core-orchestrator/spec.md`, `specs/backup-provider/spec.md`. Design: D1, D3, D4, D5.

- [x] 2.1 Add `class BackupAbortError(RuntimeError)`; `_run_pipeline` sets `backup_failed=isinstance(exc, BackupAbortError)`
- [x] 2.2 FULL failure after retries → CRITICAL + `raise BackupAbortError`; transfer failure → WARNING + audit successful transfers + record dependencies, then `raise BackupAbortError`
- [x] 2.3 `BitmapBackupProvider.transfer_missing`: six failure-path `continue` → `break`; immediate file deletion kept
- [x] 2.4 `cli/commands.py::_format_pipeline_result`: check `any(r.backup_failed)` before generic failure (exit 10 precedence)

## 3. F6 — Drift fixes in specs

- [x] 3.1 core-orchestrator: snapshot naming pinned `{vm}.{ts}_{disk}_{6hex}.qcow2`; onchange gate `any()` → snapshots for ALL disks

## 4. Tests

- [x] 4.1 Rewrite partial-tolerance scenarios → abort semantics (`test_pipeline.py`, `test_engine.py`, `test_full_verification_pipeline.py`)
- [x] 4.2 New: snapshot failure on one VM does not affect others; MAC denial defers without aborting; backup-abort exit precedence; generic exit when no backup failure
- [x] 4.3 Update contract test `test_transfer_missing_result_carries_disk` for break-on-failure
- [x] 4.4 Full non-integration suite + ruff green

## 5. Validation

- [ ] 5.1 `openspec validate vm-level-isolation --strict`
- [ ] 5.2 Archive the change (syncs delta specs into main specs)
