# Test Plan: vm-level-isolation

## Coverage Map

| Group | Tests | Verifies |
|---|---|---|
| snapshot abort | `test_multi_disk_vdb_snapshot_failure_aborts_vm_pipeline`, `test_snapshot_failure_on_one_vm_does_not_affect_others` | F1 — disk failure aborts VM, other VMs continue |
| broken chain | `test_chain_verify_missing_file_aborts_vm_pipeline` | F2 — broken chain → CRITICAL + RuntimeError, no recovery |
| blockcommit abort | `test_post_commit_chain_length_unchanged_critical` (raises RuntimeError) | F3 — non-MAC commit failure / unchanged chain aborts |
| MAC deferral | `test_mac_denial_defers_without_aborting` | D6 — AppArmor/SELinux defers, does not abort |
| backup abort | `test_full_creation_not_retried_non_transient`, `test_pipeline_skips_retention_when_backup_transfer_fails`, `test_onchange_baseline_not_updated_on_failure`, `test_full_verification_pipeline.py` (8 tests) | F4 — BackupAbortError after retries; successes audited first |
| exit codes | `test_backup_abort_exit_wins_over_generic_failure`, `test_generic_failure_exit_when_no_backup_failure` | D5 — exit 10 precedence |
| provider break | `tests/interfaces/test_backup_provider.py::test_transfer_missing_result_carries_disk` | D4 — loop breaks on first definitive failure |
| recovery removed | `tests/integration/test_blockcommit_recovery.py` deleted | D2 — no partial commit / auto-rebase |
| regression | full non-integration suite | no fallout |

## Execution

```bash
.venv/bin/pytest -m "not integration and not stress and not e2e" -q
.venv/bin/ruff check qsnap tests && .venv/bin/ruff format --check qsnap tests
```

## Not covered (by design)

- Real MAC denial and real broken-chain recovery on libvirt: integration/e2e tier (`tests/integration/test_blockcommit_defer.py` covers deferral on a real VM).
- Operator repair workflow after abort (`qsnap check --deep`): diagnostic command, already covered by check tests.
