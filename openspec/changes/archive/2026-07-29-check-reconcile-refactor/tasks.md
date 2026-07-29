## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b check-reconcile-refactor`
- [x] 1.2 Verify all existing tests pass before starting: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 1.3 Read all spec files under `openspec/changes/check-reconcile-refactor/specs/` and `openspec/changes/check-reconcile-refactor/design.md` before starting implementation

## 2. Config & Model Changes

- [x] 2.1 Change default retention values in `qsnap/models/config.py`: `GlobalConfig.snapshot_chain_length` from `None` to `24`, `GlobalConfig.target_chain_length` from `None` to `168`, `GlobalConfig.target_keep_generations` from `None` to `2` (spec: config-model, design: D7)
- [x] 2.2 Add new fields to `ReconcileResult` in `qsnap/models/results.py`: `state_supplemented: int = 0`, `xml_refreshed: bool = False`, `allocation_fixed: bool = False` (spec: state-reconciliation, design: D8)
- [x] 2.3 Verify existing config parsing tests still pass: `poetry run pytest tests/config/ -v`

## 3. Post-Creation Validation (Snapshot Provider)

- [x] 3.1 Add post-creation validation to `ExternalSnapshotProvider.create()` in `qsnap/modules/snapshot/external.py`: after `virsh snapshot-create-as` returns exit code 0, verify (a) file exists via `test -f`, (b) qcow2 format + corrupt bit + backing-filename from already-obtained `qemu-img info`, (c) `virsh domblklist` confirms libvirt pivot (spec: post-creation-validation, snapshot-provider; design: D4)
- [x] 3.2 Return `SnapshotResult(success=False, error=<message>)` if any validation step fails — Core must NOT call `record_snapshot()` for failed snapshots
- [x] 3.3 Verify: `poetry run pytest tests/modules/snapshot/test_external.py -v`

## 4. Post-Transfer Validation (Backup Provider)

- [x] 4.1 Add post-transfer chain-to-FULL verification to `BitmapBackupProvider.transfer_missing()` in `qsnap/modules/backup/bitmap.py`: after atomic rename, run `qemu-img info --backing-chain` to verify chain traversability to FULL (spec: post-creation-validation, backup-provider; design: D5)
- [x] 4.2 Add checkpoint existence verification after both incremental transfer and FULL creation: `virsh checkpoint-list --name --domain <vm>` (spec: post-creation-validation; design: D5)
- [x] 4.3 Add FULL backup verification: `qemu-img info` → `backing-filename` must be absent/none (spec: post-creation-validation; design: D5)
- [x] 4.4 Return `BackupResult(success=False, error=<message>)` if any post-transfer validation fails — Core must NOT call `record_incremental_dependency()` for failed incrementals
- [x] 4.5 Verify: `poetry run pytest tests/modules/backup/test_bitmap.py -v`

## 5. Check Refactoring (Triple-Source Verification)

- [x] 5.1 Refactor `Core.check()` in `qsnap/core/__init__.py` to perform triple-source snapshot verification: cross-reference state JSON ↔ disk files ↔ domain XML (spec: triple-source-check; design: D1)
- [x] 5.2 Add `virsh dumpxml --domain <vm>` parsing to extract `<backingStore>` elements and compare with disk and state (spec: triple-source-check)
- [x] 5.3 Add `virsh domblklist` verification: active layer matches newest snapshot in state (spec: triple-source-check)
- [x] 5.4 Parse JSON output of `qemu-img info --backing-chain` (not just exit code) — verify backing-filename consistency, format, cycles (spec: triple-source-check, chain-integrity-verification)
- [x] 5.5 Add triple-source target verification: state ↔ disk ↔ checkpoints for FULLs, incrementals, and orphan detection (spec: triple-source-check)
- [x] 5.6 Add checkpoint verification: one per target, no orphans, missing checkpoint warning (spec: triple-source-check)
- [x] 5.7 Fix `Core._deep_check_file()`: check `errors` + `leaks` + `corruptions` (not just `corruptions`), increase timeout from 60s to 7200s (spec: deep-verification-circuit, chain-integrity-verification; design: D6)
- [x] 5.8 Ensure `Core.check()` is completely read-only — no state/disk/XML modifications except `_last_deep_check.json` timestamp (spec: triple-source-check)
- [x] 5.9 Verify: `poetry run pytest tests/core/test_pipeline.py tests/core/test_list_commands.py tests/core/test_state_check.py -v`

## 6. Reconcile Refactoring

- [x] 6.1 Modify `Core.reconcile()` in `qsnap/core/__init__.py`: when orphan file exists on disk AND domain XML references it, call `record_snapshot()` / `record_full_backup()` / `record_incremental_dependency()` to supplement state instead of deleting the file (spec: state-reconciliation; design: D2)
- [x] 6.2 Add `_refresh_domain_backing_store()` call to `Core.reconcile()`: when domain XML contains stale `<backingStore>` references to deleted files, strip them and `virsh define` (spec: state-reconciliation; design: D2)
- [x] 6.3 Remove auto-rebase from reconcile: when broken chain detected, log CRITICAL and do NOT call `qemu-img rebase -u` (spec: state-reconciliation; design: D3)
- [x] 6.4 Add `last_allocation` mismatch detection and correction: compare state's `last_allocation` with `qemu-img info actual-size`, call `set_last_allocation()` if mismatch (spec: state-reconciliation)
- [x] 6.5 Update `ReconcileResult` construction to populate new fields: `state_supplemented`, `xml_refreshed`, `allocation_fixed` (spec: state-reconciliation; design: D8)
- [x] 6.6 Verify: `poetry run pytest tests/core/test_reconcile.py -v`

## 7. Testing

**CRITICAL: The main programmer agent (@Mr.Programmer) MUST pass the `TESTING.md` document to each @Mr.Tester agent when delegating test groups. The TESTING.md file at the project root describes the testing philosophy and paradigm that all testers MUST follow. Without this document, testers will not understand the mock strategy, fixture patterns, and test category rules.**

- [x] 7.1 Read `openspec/changes/check-reconcile-refactor/test-plan.md` Delegation Groups section
- [x] 7.2 Delegate group `check-snapshots-unit` to @Mr.Tester — scope: `tests/core/test_check_snapshots.py` (NEW, 15 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.3 Delegate group `check-targets-unit` to @Mr.Tester — scope: `tests/core/test_check_targets.py` (NEW, 9 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.4 Delegate group `reconcile-snapshots-unit` to @Mr.Tester — scope: `tests/core/test_reconcile_snapshots.py` (NEW, 10 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.5 Delegate group `reconcile-targets-unit` to @Mr.Tester — scope: `tests/core/test_reconcile_targets.py` (NEW, 9 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.6 Delegate group `check-integration` to @Mr.Tester — scope: `tests/integration/test_check_snapshots.py` + `tests/integration/test_check_targets.py` (NEW, 12 scenarios). System has full access to libvirt and qemu-img. **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.7 Delegate group `reconcile-integration` to @Mr.Tester — scope: `tests/integration/test_reconcile_snapshots.py` + `tests/integration/test_reconcile_targets.py` (NEW, 10 scenarios). System has full access to libvirt and qemu-img. **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.8 Delegate group `post-validation-integration` to @Mr.Tester — scope: `tests/integration/test_post_creation_validation.py` + `tests/integration/test_refresh_backing_store.py` (NEW, 8 scenarios). System has full access to libvirt and qemu-img. **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.9 Delegate group `provider-unit-modifications` to @Mr.Tester — scope: `tests/modules/snapshot/test_external.py` + `tests/modules/backup/test_bitmap.py` (MODIFY, 14 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.10 Delegate group `core-unit-modifications` to @Mr.Tester — scope: `tests/core/test_list_commands.py` + `tests/core/test_pipeline.py` + `tests/core/test_reconcile.py` + `tests/integration/test_reconcile.py` (MODIFY, 14 scenarios). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.11 Delegate group `config-and-mock-infra` to @Mr.Tester — scope: `tests/config/test_model.py` + `tests/models/test_results.py` + `tests/mocks/mock_modules.py` + `tests/mocks/mock_shell.py` + `tests/conftest.py` + 3 NEW fixture XML files (MODIFY + NEW). **MUST pass `TESTING.md` to the tester agent.**
- [x] 7.12 Review @Mr.Tester reports and fix any source-level bugs discovered by tests
- [x] 7.13 Re-delegate any groups affected by source fixes
- [x] 7.14 Verify all groups pass and coverage matches test-plan.md: `poetry run pytest tests/ -m "not stress and not e2e" -v`

## 8. Final Verification

- [x] 8.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 8.2 Run integration test suite: `poetry run pytest tests/integration/ -m integration -v`
- [x] 8.3 Verify no regressions in existing pipeline tests: `poetry run pytest tests/core/test_pipeline.py tests/core/test_engine.py -v`
- [x] 8.4 Run linter: `poetry run ruff check qsnap/ tests/`
- [x] 8.5 Run type checker: `poetry run pyright qsnap/`
- [x] 8.6 Verify openspec validation: `openspec validate --change check-reconcile-refactor`
