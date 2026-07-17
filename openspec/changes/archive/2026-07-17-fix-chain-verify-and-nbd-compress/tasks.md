## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix/chain-verify-and-nbd-compress`
- [x] 1.2 Run the full test suite to establish a passing baseline: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`

## 2. Fix: `qemu-img` JSON key compatibility (`"image"` vs `"filename"`)

- [x] 2.1 Fix `_verify_backing_chain()` in `qsnap/core/__init__.py` line 1879: change `image = item.get("image")` to `image = item.get("image") or item.get("filename", "")` (design decision 1)
- [x] 2.2 Fix `_verify_backing_chain()` in `qsnap/core/__init__.py` line 1943: change `next_image = chain_data[i + 1].get("image")` to `next_image = chain_data[i + 1].get("image") or chain_data[i + 1].get("filename", "")` (cross-check consistency)
- [x] 2.3 Fix `_restore_snapshot()` chain path extraction in `qsnap/core/__init__.py` line 933: change `image = item.get("image")` to `image = item.get("image") or item.get("filename", "")` (restore command compatibility)
- [x] 2.4 Add comment above each fallback explaining QEMU 11.0+ format change: `# Accept both legacy "image" (QEMU < 11.0) and "filename" (QEMU 11.0+) keys.`

## 3. Fix: NBD full-export compression support

- [x] 3.1 Add `compress: bool = False` parameter to `nbd_full_export()` in `qsnap/modules/backup/nbd_helper.py` (line 135, function signature)
- [x] 3.2 Add `-c` flag to `convert_cmd` in `nbd_full_export()` when `compress=True` (line 184): conditionally insert `"-c"` before `nbd_uri` in the `convert_cmd` list
- [x] 3.3 Update `nbd_full_export()` docstring (line 140–154) to document the new `compress` parameter and remove the statement "The NBD path does not support compression"
- [x] 3.4 In `FileCopyBackupProvider.create_full_backup()` (`qsnap/modules/backup/file_copy.py`, lines 370–374): remove the WARNING `"compress=True ignored for NBD-based FULL backup"` and pass `compress` through to `nbd_full_export()`
- [x] 3.5 Update `FileCopyBackupProvider.create_full_backup()` docstring (line 339–341): replace "The NBD path does not support compression — if compress=True and NBD is selected, a WARNING is logged and the result is uncompressed" with "Both NBD and direct-convert paths support compression via `-c` flag on `qemu-img convert`"
- [x] 3.6 In `BitmapBackupProvider.create_full_backup()` (`qsnap/modules/backup/bitmap.py`, lines 290–293): remove the WARNING `"compress=True ignored for NBD-based FULL backup"` and pass `compress` through to `nbd_full_export()`
- [x] 3.7 Run ruff lint + format: `ruff check . && ruff format .`

## 4. Test Fixtures

- [x] 4.1 Create new fixture `tests/fixtures/shell_outputs/backing_chain_intact_new.json` — 5-file intact chain using `"filename"` keys and `"children"` arrays (see test-plan.md for exact content)
- [x] 4.2 Create new fixture `tests/fixtures/shell_outputs/backing_chain_broken_new.json` — broken chain (MISSING_FILE) using `"filename"` keys and `"children"` arrays (see test-plan.md for exact content)

## 5. Testing

**CRITICAL — TEST ORCHESTRATION PROTOCOL for the implementing agent:**

When delegating each group to @Mr.Tester, you MUST:
1. Read `TESTING.md` at the project root — this document defines the project's testing paradigm (test location mirrors production, custom mock classes, contract tests, pytest markers, etc.)
2. Pass the ENTIRE contents of `TESTING.md` as context to each @Mr.Tester subagent so they follow the project's test architecture
3. Also pass the relevant Coverage Map rows for the group being delegated

- [x] 5.1 Read `test-plan.md` Delegation Groups section and Coverage Map tables
- [x] 5.2 Delegate group `chain-verify-tests` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/fixtures/shell_outputs/`) — pass TESTING.md + Coverage Map rows for chain-integrity-verification
- [x] 5.3 Delegate group `file-copy-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`) — pass TESTING.md + Coverage Map rows for backup-provider
- [x] 5.4 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`) — pass TESTING.md + Coverage Map rows for nbd-bitmap-backup
- [x] 5.5 Delegate group `restore-tests` to @Mr.Tester (scope: `tests/core/test_engine.py`, `tests/cli/test_commands.py`) — pass TESTING.md + Coverage Map rows for restore-command
- [x] 5.6 Review all @Mr.Tester reports and fix any source-level bugs discovered during test authoring
- [x] 5.7 Re-delegate any groups affected by source fixes (repeat 5.2–5.6 as needed)
- [x] 5.8 Verify all groups pass and coverage matches test-plan.md: `poetry run pytest tests/core/test_pipeline.py tests/core/test_engine.py tests/modules/backup/test_copy.py tests/modules/backup/test_bitmap.py tests/cli/test_commands.py -v`

## 6. Regression & Final Verification

- [x] 6.1 Run the full non-integration test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e" -v`
- [x] 6.2 Verify the fix resolves the user's original issue: `poetry run python -m qsnap check --deep` on the VM `3.Projects_opencode` should NOT produce "Missing 'image' field in chain entry 0" CRITICAL error
- [x] 6.3 Verify the NBD compression fix: run `qsnap list` or dry-run on a target with `compress = true` and check that no WARNING "compress=True ignored for NBD-based FULL backup" is emitted
- [x] 6.4 Run ruff lint + format one final time: `ruff check . && ruff format .`