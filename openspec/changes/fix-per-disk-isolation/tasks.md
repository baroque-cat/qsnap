## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-per-disk-isolation`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. F1 — Per-disk active-layer verification (triple-source-check)

Specs: `specs/triple-source-check/spec.md`. Design: D1.

- [x] 2.1 In `qsnap/core/__init__.py::_verify_active_layer_match`: build `newest_by_disk: dict[str, SnapshotInfo]` by grouping `snapshots` on `snap.disk` and taking max timestamp per disk; iterate `parse_domblklist_path_map` entries; skip disks absent from `newest_by_disk`; compare each disk's domblklist path against its OWN newest snapshot; include the disk name in every mismatch appended to `broken`
- [x] 2.2 Verify with `tests/core/test_check_per_disk.py` scenarios from the Coverage Map (group `core-triple-check-unit`)

## 3. F2 — Disk field through result objects (BREAKING: result contracts)

Specs: `specs/result-types/spec.md`, `specs/action-audit-trail/spec.md`, `specs/backup-provider/spec.md`, `specs/backup-summary/spec.md`, `specs/transaction-log/spec.md`. Design: D2. Land model + provider + mocks in ONE atomic step.

- [x] 3.1 `qsnap/models/results.py`: add `disk: str | None = None` to `ActionRecord` and `BackupResult` (frozen dataclasses, docstrings in English)
- [x] 3.2 `qsnap/modules/backup/bitmap.py::transfer_missing`: set `disk=snapshot.disk` on every returned `BackupResult` (success AND failure paths); `create_full_backup`: set `disk=source_snapshot.disk`
- [x] 3.3 `tests/mocks/mock_modules.py`: `MockBitmapBackupProvider` / `MockBackupProvider` populate `disk` identically (mock parity per TESTING.md)
- [x] 3.4 `qsnap/core/__init__.py` — populate `disk` at all 7 `ActionRecord` creation sites: error record at ~2218 gets `disk=None`; snapshot_create ~3122 gets `disk.target`; snapshot_delete ~3852 gets the `_blockcommit_one_disk` disk param; backup_full ~4534 gets `mr.disk`; backup_transfer ~4616 gets `BackupResult.disk`; backup_delete ~4965/~4989 gets `backup.disk`
- [x] 3.5 `qsnap/cli/summary.py::_format_action`: render `[<disk>]` after the action symbol only when `record.disk is not None`; VM-level lines unchanged
- [x] 3.6 `qsnap/utils/transaction.py`: NO format change — confirm the line stays exactly 6 btrbk-compatible fields (disk travels inside paths); add the guard test from group `utils-tx-log`
- [x] 3.7 Contract tests: `tests/interfaces/test_backup_provider.py` asserts `BackupResult.disk` populated for all implementations (group `contract-backup-disk`)

## 4. F5 — Standalone-image conversion helpers (new utils module)

Specs: `specs/standalone-image-conversion/spec.md`, plus fork/restore integration in `specs/fork-mode/spec.md`, `specs/restore-command/spec.md`, `specs/core-orchestrator/spec.md`. Design: D5.

- [x] 4.1 Create `qsnap/utils/convert.py` with `convert_to_standalone(shell, source, output, timeout=7200) -> ShellResult` (`qemu-img convert --force-share -O qcow2`; best-effort remove partial output on failure; expected failures returned, never raised)
- [x] 4.2 Add `verify_standalone_image(shell, source, output) -> str | None` (M1 virtual-size equality via `qemu-img info --force-share --output=json`; M2 `qemu-img check`; returns `None` on pass, error string otherwise — same convention as `verify_full_backup`)
- [x] 4.3 Add `convert_with_retry(shell, source, output, retry_max, retry_base) -> ShellResult` using `is_retryable`/`compute_backoff` from `qsnap/utils/retry.py`; partial output removed before each retry; no new config options (callers pass `GlobalConfig.backup_retry_max`/`backup_retry_base`)
- [x] 4.4 Re-export per package convention (`__all__` if the utils package re-exports); unit tests in `tests/utils/test_convert.py` (group `convert-utils-unit`)
- [x] 4.5 `Core.fork()` (`qsnap/core/__init__.py:1321+`): route conversion through `convert_with_retry`; after success run `verify_standalone_image`; on verification failure remove the output file and return failed `RestoreResult`
- [x] 4.6 `Core.restore()` (`qsnap/core/__init__.py:1041+`): route tmp conversion through `convert_with_retry`; verify the tmp image via `verify_standalone_image` BEFORE `os.replace(tmp, base_image)`; on failure remove tmp and abort with the base image untouched

## 5. F3 — Fork dry-run

Specs: `specs/fork-mode/spec.md`, `specs/cli-interface/spec.md`. Design: D3.

- [x] 5.1 `qsnap/cli/app.py`: add local `--dry-run` flag to the fork subparser with `default=argparse.SUPPRESS` (same pattern as reconcile)
- [x] 5.2 `qsnap/cli/commands.py::handle_fork`: when the local flag is present, set `core.dry_run = True` before calling `Core.fork()` (global `-n` already assigned at `app.py:290`)
- [x] 5.3 `Core.fork()`: after the read-only chain-size estimate, if `self._dry_run`: log the planned conversion (source, output, estimated size) at INFO and return `RestoreResult(success=True)` WITHOUT converting or creating any file

## 6. F4 — Per-disk state reset for restore (BREAKING: IStateManager)

Specs: `specs/state-management/spec.md`, `specs/restore-command/spec.md`, `specs/core-orchestrator/spec.md`. Design: D4. Land ABC + both implementations + mock in ONE atomic step.

- [x] 6.1 `qsnap/interfaces/state.py`: add abstract methods `reset_vm_disk_state(vm_name: str, disk: str) -> None` and `reset_target_disk_state(target_path: str, vm_name: str, disk: str) -> None` with full docstring contracts (English)
- [x] 6.2 `qsnap/state/json_manager.py`: implement both — per-disk clearing of snapshots/`last_allocation` key/deferred ops (legacy bare-int `last_allocation` treated as absent); per-(vm,disk) clearing of `_full_backups.json` entries (name startswith `{vm_name}.` AND disk match), `_dependencies.json` keys (disk parsed from FULL name via `parse_disk_from_snapshot_name`), `_target_state.json` `last_backup_allocation[disk]`; all writes atomic via existing `.tmp` + `os.replace` path
- [x] 6.3 `tests/mocks/mock_state.py::InMemoryStateManager`: implement both methods with identical semantics (mock parity); contract tests in `tests/interfaces/test_state_manager.py` (group `contract-state-perdisk`)
- [x] 6.4 `Core.restore()` step 8: replace `reset_vm_state(vm_name)` + per-target `reset_target_state(target_path)` with `reset_vm_disk_state(vm_name, disk)` + per-target `reset_target_disk_state(target_path, vm_name, disk)`
- [x] 6.5 `Core._cleanup_checkpoints_after_restore`: accept the restored disk; delete only checkpoints whose 3rd dash-separated segment equals the disk (name format `qsnap-{target_hash}-{disk}-{timestamp}-{hex}`); skip legacy names without a disk segment with a WARNING; never delete other disks' checkpoints
- [x] 6.6 Update `restore-command`/`state-management` spec references in docstrings; ensure `reset_vm_state`/`reset_target_state` remain for their other spec'd uses (do NOT remove them)

## 7. Testing

MANDATORY DELEGATION PROTOCOL for the lead implementation agent (@Mr.Programmer): the lead agent MUST NOT write the test suites itself — it MUST delegate every group below to a dedicated @Mr.Tester subagent. **With EVERY delegation the lead agent MUST attach the testing paradigm document `/home/openuser/vm/qsnap/TESTING.md` and instruct the tester to conform to it** (directory mirroring, mock strategy without pytest-mock, markers, fixture rules). Each tester writes or fixes ONLY its own group's test files and reports source bugs instead of fixing them. Launch all groups IN PARALLEL (single message).

- [x] 7.1 Read `test-plan.md` — Delegation Groups, Coverage Map, Test Deletions, Test Modifications sections
- [x] 7.2 Delegate group `models-results-disk` to @Mr.Tester (scope: `tests/models/test_results.py`) — attach TESTING.md
- [x] 7.3 Delegate group `core-audit-disk` to @Mr.Tester (scope: `tests/core/test_engine.py`) — attach TESTING.md
- [x] 7.4 Delegate group `core-triple-check-unit` to @Mr.Tester (scope: `tests/core/test_check_per_disk.py`) — attach TESTING.md
- [x] 7.5 Delegate group `cli-summary-disk` to @Mr.Tester (scope: `tests/cli/test_summary.py`) — attach TESTING.md
- [x] 7.6 Delegate group `utils-tx-log` to @Mr.Tester (scope: `tests/utils/test_transaction.py`) — attach TESTING.md
- [x] 7.7 Delegate group `contract-backup-disk` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py`) — attach TESTING.md
- [x] 7.8 Delegate group `backup-bitmap-disk` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`) — attach TESTING.md
- [x] 7.9 Delegate group `contract-state-perdisk` to @Mr.Tester (scope: `tests/interfaces/test_state_manager.py`) — attach TESTING.md
- [x] 7.10 Delegate group `state-per-disk-reset` to @Mr.Tester (scope: `tests/state/test_manager.py`) — attach TESTING.md
- [x] 7.11 Delegate group `core-fork-unit` to @Mr.Tester (scope: `tests/core/test_fork.py`) — attach TESTING.md
- [x] 7.12 Delegate group `cli-commands-disk` to @Mr.Tester (scope: `tests/cli/test_commands.py`, `tests/cli/test_app.py`) — attach TESTING.md
- [x] 7.13 Delegate group `fork-integration` to @Mr.Tester (scope: `tests/integration/test_fork.py`) — attach TESTING.md; real libvirt/qemu available
- [x] 7.14 Delegate group `core-restore-perdisk` to @Mr.Tester (scope: `tests/integration/test_restore.py`, `tests/integration/test_multi_disk.py`) — attach TESTING.md; real libvirt/qemu available
- [x] 7.15 Delegate group `core-restore-unit` to @Mr.Tester (scope: `tests/core/test_restore.py`) — attach TESTING.md; MUST execute the 2 deletions listed in test-plan.md Test Deletions (`test_restore_resets_all_vm_state`, `test_restore_best_effort_checkpoint_cleanup`) and write their replacements
- [x] 7.16 Delegate group `convert-utils-unit` to @Mr.Tester (scope: `tests/utils/test_convert.py`) — attach TESTING.md
- [x] 7.17 Delegate group `check-real-multi-disk` to @Mr.Tester (scope: `tests/integration/test_check_snapshots.py`) — attach TESTING.md; real libvirt/qemu available (multi-disk fixture `test_vm_multi_disk`)
- [x] 7.18 Review all @Mr.Tester reports; fix source-level bugs discovered (in production code, not by editing tests)
- [x] 7.19 Re-delegate any groups affected by source fixes (again with TESTING.md attached)
- [x] 7.20 Verify every Coverage Map row passes and no test file was touched by two groups; confirm deletions applied

## 8. Final Verification

- [x] 8.1 Run the fast suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — all green
- [x] 8.2 Run integration: `poetry run pytest tests/integration/ -m integration` — all green (real libvirt/qemu)
- [x] 8.3 Run e2e: `poetry run pytest tests/e2e/ -m e2e` — all green
- [x] 8.4 Lint & types: `poetry run ruff check qsnap tests` and `poetry run ruff format --check qsnap tests` and `poetry run pyright qsnap` — clean
- [x] 8.5 `openspec validate fix-per-disk-isolation` passes; spot-check that implementation matches all 12 delta specs
- [ ] 8.6 Commit with a message referencing the change name; do NOT archive (archiving is a separate step after review)
