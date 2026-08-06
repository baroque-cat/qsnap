## Why

The pipeline's failure handling is inconsistent across stages: snapshot creation swallows per-disk failures and continues, blockcommit used to attempt risky automatic recovery (partial blockcommit + `qemu-img rebase -u` auto-rebase) on broken backing chains, and backup failures set a flag while the pipeline keeps going. A VM with a failing disk can end up half-processed, and broken chains were "healed" automatically instead of surfaced to the operator.

Decision: **the VM is the atomic unit of execution**. A definitive disk-level failure (after retries, where applicable) aborts the remaining steps of that VM; other VMs continue; already-completed steps are not rolled back. Automatic chain recovery is removed — a broken chain is an operator matter (`qsnap check --deep`). MAC denials (AppArmor/SELinux) remain deferred operations, not failures.

## What Changes

- **F1**: Snapshot creation failure on any disk — or a missing per-disk snapshot directory — aborts the VM with `RuntimeError` (was: log and continue to the next disk).
- **F2**: Pre-commit chain verification failure (broken chain) aborts the VM: CRITICAL log with the broken file path and a `qsnap check --deep` hint, then `RuntimeError`. Partial blockcommit and auto-rebase are removed (`_split_at_break`, `_auto_rebase_stuck` deleted, ~189 lines).
- **F3**: Non-MAC blockcommit failure and post-commit chain-length-unchanged abort the VM with `RuntimeError`. MAC denial stays a deferred operation.
- **F4**: Backup stage: FULL creation or incremental transfer failure after retries raises the new `BackupAbortError` → VM aborts with `backup_failed=True` → exit code 10 (`EXIT_BACKUP_ABORT`), which takes precedence over generic failure. Successful transfers are audited and their dependencies recorded before the abort. `BitmapBackupProvider.transfer_missing` breaks its snapshot loop on the first definitive failure.
- **F5**: `VMRunResult.backup_failed` is derived from `isinstance(exc, BackupAbortError)` in `_run_pipeline`; the CLI exit-code check orders backup abort before generic failure.
- **F6**: Spec drift fixed along the way: snapshot naming pinned as `{vm}.{ts}_{disk}_{6hex}.qcow2`; onchange snapshot gate normalized as VM-wide `any()` with snapshots created for ALL disks.

## Migration / Behavior Change

A single disk failure that was previously tolerated now stops that VM until an operator intervenes — this is the intent. MAC denials still defer silently. No state or config migration. `README.md` failure-behavior wording must be updated (stale "commits only the intact prefix" line).

## Capabilities

### Modified Capabilities

- `core-orchestrator`: VM-level failure isolation replaces per-disk partial tolerance; broken-chain and backup-abort semantics; naming and onchange-gate drift fixes.
- `blockcommit-recovery`: recovery requirements removed; verification kept as diagnostics; broken chain aborts the VM.
- `backup-provider`: transfer loop breaks on definitive failure; immediate deletion of failed files kept.
- `chain-integrity-verification`: failure scenarios change from "skip disk" to "abort VM pipeline".

## Impact

- **Code**: `qsnap/core/__init__.py` (abort model, `BackupAbortError`, −189 lines of recovery), `qsnap/modules/backup/bitmap.py` (continue→break), `qsnap/cli/commands.py` (exit-code precedence).
- **Tests**: partial-tolerance scenarios rewritten to abort semantics; new unit tests per abort point; recovery tests deleted (`tests/integration/test_blockcommit_recovery.py`).
- **Deferred operations**: unchanged (MAC denial is not a failure).
