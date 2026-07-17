## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b live-vm-full-backup-via-nbd`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. NBD Full-Export Helper & VM State Detection

- [x] 2.1 Create a shared NBD full-export helper function in `qsnap/modules/backup/` (e.g., `nbd_helper.py` or as a private method in each provider). The helper SHALL: (a) remove stale socket at `/tmp/qsnap-backup-{pid}.sock`, (b) write `<domainbackup mode='pull'><server transport='unix' path='{socket}'/></domainbackup>` XML, (c) run `virsh backup-begin --domain <vm> <xml>` WITHOUT `--incremental`, (d) run `qemu-img convert -n nbd:unix:<socket> <target>`, (e) clean up socket in `finally`. Reference: `specs/live-vm-full-backup/spec.md` — "NBD full-export helper for FULL backups"
- [x] 2.2 Implement VM running-state detection: call `virsh dominfo --domain <vm_name>` and parse the `State:` line. Return `True` if state is `running`, `False` otherwise. Reference: `specs/live-vm-full-backup/spec.md` — "VM running-state detection for FULL backup method selection"
- [x] 2.3 Implement libvirt version check for NBD path: verify libvirt >= 6.0 before attempting NBD. If too old, log WARNING and fall back to direct convert. Reference: `specs/live-vm-full-backup/spec.md` — "Libvirt version check for NBD FULL path"

## 3. FileCopyBackupProvider Hybrid NBD/Direct FULL Backup

- [x] 3.1 Modify `FileCopyBackupProvider.create_full_backup()` in `qsnap/modules/backup/file_copy.py` to branch on VM running state. If running: use NBD full-export helper (no `--force-share`, no checkpoint). If stopped: use existing direct `qemu-img convert [-c]` path. Reference: `specs/backup-provider/spec.md` — "FileCopyBackupProvider.create_full_backup creates standalone qcow2 on target" (MODIFIED), design D1
- [x] 3.2 Add atomic file creation for NBD path: write to `.tmp` path, rename to final `vm.FULL.YYYYMMDD.qcow2` on success. Remove `.tmp` on failure. Reference: `specs/live-vm-full-backup/spec.md` — "Atomic FULL file creation via NBD"
- [x] 3.3 Handle compress flag: if `compress=True` and NBD path selected, log WARNING "compress=True ignored for NBD-based FULL backup" (NBD path does not support `-c`). Reference: `specs/backup-provider/spec.md` — "NBD full backup ignores compress flag"
- [x] 3.4 Record FULL timestamp as snapshot's timestamp (not NBD export time) for retention bucket alignment. Reference: `specs/live-vm-full-backup/spec.md` — "NBD FULL exports current disk state"

## 4. BitmapBackupProvider create_full_backup Implementation

- [x] 4.1 Override `create_full_backup()` in `BitmapBackupProvider` (`qsnap/modules/backup/bitmap.py`) to use the NBD full-export path (no `--incremental`). No longer raises `NotImplementedError`. Reference: `specs/backup-provider/spec.md` — "BitmapBackupProvider.create_full_backup implemented via NBD" (ADDED), `specs/nbd-bitmap-backup/spec.md` — "BitmapBackupProvider.create_full_backup via NBD full export" (ADDED), design D4
- [x] 4.2 Ensure NO checkpoint is created or deleted in `create_full_backup()` — checkpoint lifecycle remains exclusively in `transfer_missing()`. Reference: `specs/nbd-bitmap-backup/spec.md` — "Bitmap FULL does not create checkpoint"

## 5. --force-share Fixes on Metadata-Only Operations

- [x] 5.1 Fix `ExternalSnapshotProvider.create()` in `qsnap/modules/snapshot/external.py` line 91: add `--force-share` to the post-snapshot `qemu-img info` command. The newly created snapshot IS the active layer. Reference: `specs/snapshot-provider/spec.md` (MODIFIED), bug A
- [x] 5.2 Fix `MapChangeDetector.has_changed()` in `qsnap/modules/change/map_detector.py` line 96: add `--force-share` to `qemu-img map` command. Reference: `specs/map-change-detection/spec.md` (MODIFIED), bug B
- [x] 5.3 Fix `Core.check_integrity()` in `qsnap/core/__init__.py` line 676: add `--force-share` to `qemu-img info --backing-chain` on snapshots (most recent may be active layer). Reference: `specs/chain-integrity-verification/spec.md` — "--force-share on check_integrity" (ADDED), bug P
- [x] 5.4 Fix `Core._deep_check_file()` in `qsnap/core/__init__.py` line 765: add `--force-share` to `qemu-img check` when file may be active layer. Reference: `specs/chain-integrity-verification/spec.md` — "--force-share on _deep_check_file" (ADDED)
- [x] 5.5 Fix `Core._verify_backing_chain()` and `Core._get_chain_length()` in `qsnap/core/__init__.py`: ensure `--force-share` is present on all `qemu-img info --backing-chain` calls (lines 1822, 1956 already have it — verify, don't change). Reference: `specs/chain-integrity-verification/spec.md` (MODIFIED)
- [x] 5.6 Fix `verify_backup()` in `qsnap/modules/backup/verification.py` line 73: add `--force-share` to source-side `qemu-img info` when source may be active layer. Do NOT add `--force-share` to `qemu-img compare` (line 142) — it is a data-copying operation. Reference: `specs/backup-verification/spec.md` (MODIFIED), design D5
- [x] 5.7 Add WARNING log in `verify_backup()` when `verify="full"` and source is active layer of running VM: "verify=full on running VM active layer — results may be unreliable, consider verify=metadata". Reference: `specs/backup-verification/spec.md` — "Full verification on live source logs warning"

## 6. Fork NBD/Direct Hybrid

- [x] 6.1 Modify `Core.fork()` in `qsnap/core/__init__.py` to detect VM running state. If running: use NBD full-export for `qemu-img convert`. If stopped: use existing direct `qemu-img convert -O qcow2`. Reference: `specs/fork-mode/spec.md` (MODIFIED), design D9
- [x] 6.2 Add `--force-share` to `qemu-img info --backing-chain` in fork chain-size estimation (line 1030). Reference: `specs/fork-mode/spec.md` — "Fork chain-size estimation uses --force-share", bug U

## 7. Dry-Run Enhancement

- [x] 7.1 Modify `Core._execute_pipeline()` in `qsnap/core/__init__.py` line 1573: remove the `if not self._dry_run:` guard around `_validate_environment()`. Always call validation. In dry-run mode, log failures as WARNING (non-fatal). In non-dry-run, raise `RuntimeError` as before. Reference: `specs/env-validation/spec.md` (MODIFIED), design D6
- [x] 7.2 Modify `Core._backup_target()` to evaluate `_should_create_bucket_full()` in dry-run mode and log: "[dry-run] Would create FULL backup (bucket=<level>, method=<NBD|direct convert>, VM=<running|stopped>)". Do NOT call `create_full_backup()`. Reference: `specs/periodic-full-backup/spec.md` — "Dry-run logs FULL-would-be-created without executing", `specs/cli-interface/spec.md` (MODIFIED), design D7
- [x] 7.3 Modify `Core._log_size_estimate()` to include FULL-would-be-created indicator in dry-run output. Reference: `specs/size-estimation/spec.md` (MODIFIED)

## 8. README Documentation

- [x] 8.1 Add section in `README.md` documenting the NBD-based FULL backup mechanism: how it works (virsh backup-begin + qemu-img convert -n nbd:), when it's used (running VMs), and why (avoids lock conflict on active layer). Reference: design D1
- [x] 8.2 Add `--force-share` safety classification table to README: safe for metadata-only (info, map, check, rebase -u), dangerous for data-copying (convert, compare, commit). Reference: design D5, `specs/shell-abstraction/spec.md` (ADDED)
- [x] 8.3 Document dry-run behavior: now runs validation (non-fatal warnings), logs FULL-would-be-created with method selection. Reference: design D6, D7
- [x] 8.4 Document libvirt permissions requirement: the `qsnap` user must have libvirt access (group membership: `sudo usermod -aG libvirt qsnap`, or polkit configuration). Reference: user's ERROR 1 root cause
- [x] 8.5 Document BitmapBackupProvider FULL backup support: no longer crashes on bucket boundaries, uses NBD full export. Reference: design D4

## 9. Mock & Contract Updates

- [x] 9.1 Update `MockBitmapBackupProvider` in `tests/mocks/mock_modules.py` to implement `create_full_backup()` returning a valid `BackupResult` instead of raising `NotImplementedError`. Reference: test-plan.md — contracts group
- [x] 9.2 Update `tests/interfaces/test_backup_provider.py` to parametrize `create_full_backup` contract test over both `FileCopyBackupProvider` and `BitmapBackupProvider` (with pre-configured MockShell for libvirt version check). Reference: test-plan.md — contracts group

## 10. Testing

**CRITICAL INSTRUCTION FOR THE IMPLEMENTING AGENT:**

When delegating test groups to @Mr.Tester subagents, you MUST pass the full content of `TESTING.md` (located at the project root `/home/openuser/vm/qsnap/TESTING.md`) to EACH test agent. This document describes the testing philosophy, directory structure, test categories, mock strategy, and paradigm that ALL test agents must follow. Without this document, test agents will not know the project's testing conventions.

The procedure:
1. Read `TESTING.md` from the project root
2. Read `test-plan.md` Delegation Groups section
3. For EACH delegation group, launch one @Mr.Tester subagent with:
   - The group's scope (file paths from test-plan.md)
   - The group's scenario list from the Coverage Map in test-plan.md
   - The FULL CONTENT of TESTING.md (paste it into the task prompt)
   - Instruction: "Write or fix ONLY these specific tests following the TESTING.md paradigm. Report source bugs, don't fix them."
4. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls)
5. After all testers return: fix any reported source bugs, re-delegate affected groups
6. Repeat until all groups pass

- [x] 10.1 Read `test-plan.md` Delegation Groups section and `TESTING.md` from project root
- [x] 10.2 Delegate group `backup-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`, `tests/modules/backup/test_bitmap.py`) — pass TESTING.md content
- [x] 10.3 Delegate group `force-share-unit` to @Mr.Tester (scope: `tests/modules/snapshot/test_external.py`, `tests/modules/change/test_map_detector.py`, `tests/modules/backup/test_verification.py`, `tests/modules/lifecycle/test_blockcommit.py`) — pass TESTING.md content
- [x] 10.4 Delegate group `core-pipeline` to @Mr.Tester (scope: `tests/core/test_validation.py`, `tests/core/test_pipeline.py`, `tests/core/test_engine.py`) — pass TESTING.md content
- [x] 10.5 Delegate group `fork-nbd` to @Mr.Tester (scope: `tests/core/test_fork.py`) — pass TESTING.md content
- [x] 10.6 Delegate group `contracts` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py`, `tests/mocks/mock_modules.py`) — pass TESTING.md content
- [x] 10.7 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_nbd_full_backup.py`) — pass TESTING.md content
- [x] 10.8 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 10.9 Re-delegate any groups affected by source fixes (with TESTING.md content)
- [x] 10.10 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 10.11 Verify coverage matches `test-plan.md` Coverage Map

## 11. Final Verification

- [x] 11.1 Run full test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 11.2 Run linter: `poetry run ruff check qsnap/`
- [x] 11.3 Run formatter: `poetry run ruff format --check qsnap/`
- [x] 11.4 Run type checker: `poetry run pyright qsnap/`
- [x] 11.5 Verify no `NotImplementedError` is raised by `BitmapBackupProvider.create_full_backup()`
- [x] 11.6 Verify dry-run output includes validation warnings and FULL-would-be-created indicators
- [x] 11.7 Verify all `--force-share` fixes are in place (grep for `qemu-img` calls on active layers)
