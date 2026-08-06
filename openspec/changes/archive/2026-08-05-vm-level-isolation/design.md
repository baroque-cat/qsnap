# Design: vm-level-isolation

## D1 — One exception type per failure class

Two exception types carry the abort semantics through `_run_pipeline`'s per-VM `try/except`:

- `RuntimeError` — snapshot-stage and blockcommit-stage failures (snapshot create, missing snapshot dir, broken chain pre-commit, non-MAC commit failure, post-commit chain-length-unchanged). Maps to `VMRunResult(success=False, backup_failed=False)` → exit 1.
- `BackupAbortError(RuntimeError)` — backup-stage failures after retries are exhausted (FULL creation, incremental transfer). Maps to `VMRunResult(success=False, backup_failed=True)` → exit 10 (`EXIT_BACKUP_ABORT`).

`_run_pipeline` distinguishes them with `isinstance(exc, BackupAbortError)`. No other exception handling changes; the existing per-VM isolation loop (other VMs continue) is preserved.

## D2 — Recovery removed, verification kept

`_split_at_break` and `_auto_rebase_stuck` are deleted entirely. `_verify_backing_chain` stays — it now feeds the abort message (`Break at: {broken_file}`) and the `check` command. Both pre-commit verification branches (broken_file set or not) raise `RuntimeError` after a CRITICAL log that includes the `qsnap check --deep` hint.

## D3 — Backup abort records successful work first

When a transfer fails after retries, Core audits the successful transfers and records their incremental dependencies BEFORE raising `BackupAbortError`, so completed work is durable. Retention and cleanup for the aborted target are skipped (the raise unwinds the target loop). The onchange baseline update gate is simplified accordingly.

## D4 — Provider loop breaks on definitive failure

`BitmapBackupProvider.transfer_missing` changes six failure-path `continue`s to `break` (temporal mismatch, backup-begin failure, transfer error, verification failure, chain-to-FULL not traversable, checkpoint missing). Skip paths (already-existing, stale) still `continue`. The provider returns a partial result; Core decides the abort. Immediate deletion of the failed file is unchanged.

## D5 — Exit-code precedence

`_format_pipeline_result` checks `any(r.backup_failed)` → `EXIT_BACKUP_ABORT` BEFORE `not result.success` → `EXIT_GENERIC`, so a failed VM that aborted in the backup stage still exits 10.

## D6 — MAC denial is not a failure

AppArmor/SELinux commit denials remain deferred operations (`add_deferred_blockcommit` with reason `apparmor`/`selinux`) and do NOT raise. Only definitive, non-recoverable failures abort the VM.
