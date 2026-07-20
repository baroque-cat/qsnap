## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fix-bitmap-incremental-via-xml`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Fix Incremental Checkpoint via XML (Critical Bug Fix)

**Specs:** `nbd-bitmap-backup`, `shared-utilities` (delta specs)
**Design:** D1 — Incremental checkpoint via XML element, not CLI flag

- [x] 2.1 Modify `write_backup_xml()` in `qsnap/utils/nbd.py` to accept an optional `incremental: str | None = None` parameter. When non-None, include `<incremental>{incremental}</incremental>` in the XML before the `<server>` element. Update the docstring.
- [x] 2.2 In `qsnap/modules/backup/bitmap.py`, remove the `_write_backup_xml` static method (lines 552-565). Add `write_backup_xml` to the import from `qsnap.utils.nbd`. Update the call site at line 161 to `write_backup_xml(socket_path, incremental=prior)`.
- [x] 2.3 In `qsnap/modules/backup/bitmap.py`, remove the `--incremental` CLI flag extension at lines 172-173 (`backup_cmd.extend(["--incremental", prior])`). The checkpoint is now in the XML, not the CLI.
- [x] 2.4 Verify that `nbd_full_export()` in `qsnap/utils/nbd.py` still calls `write_backup_xml(socket_path)` without the `incremental` parameter (FULL exports never use `<incremental>`).
- [x] 2.5 Run unit tests for bitmap module: `poetry run pytest tests/modules/backup/test_bitmap.py -v` — expect failures in tests that assert `--incremental` CLI flag (these will be fixed in the testing phase).

## 3. Update qemu-img rebase Flag (-F → -B)

**Specs:** `backup-provider` (delta spec)
**Design:** D3 — Update qemu-img rebase -F to -B

- [x] 3.1 In `qsnap/modules/backup/file_copy.py`, change `-F` to `-B` in the rebase command at lines 293-302 (rebase to FULL anchor).
- [x] 3.2 In `qsnap/modules/backup/file_copy.py`, change `-F` to `-B` in the rebase command at lines 343-352 (rebase without FULL anchor).
- [x] 3.3 In `qsnap/core/__init__.py`, change `-F` to `-B` in the rebase command at line 818 (restore rebase). Note: restore rebase does NOT use `-F qcow2` — only `-b` — verify if `-B` is needed here.
- [x] 3.4 Run unit tests for file-copy module: `poetry run pytest tests/modules/backup/test_copy.py -v` — expect failures in tests that assert `-F` (will be fixed in testing phase).

## 4. Remove Double-Recording of FULL Backups

**Specs:** `backup-provider` (delta spec)
**Design:** D4 — Remove double-recording of FULL backups in bitmap mode

- [x] 4.1 In `qsnap/modules/backup/bitmap.py`, remove the `self._state.record_full_backup()` call in `create_full_backup()` (lines 418-424). State recording is Core's responsibility after post-create verification.
- [x] 4.2 Add deduplication logic to `JsonStateManager._load_full_backups()` in `qsnap/state/json_manager.py`: on load, remove entries with duplicate `(name, target_path)` tuples, keeping the first. Log an INFO for each removed duplicate.
- [x] 4.3 Verify that `Core._backup_target()` in `qsnap/core/__init__.py` still calls `record_full_backup()` after post-create verification (line 2384) — this is the single recording point now.

## 5. Add verify="full" Guard for Bitmap Mode

**Specs:** `backup-verification` (delta spec)
**Design:** D5 — verify="full" guard for bitmap incremental

- [x] 5.1 In `qsnap/config/facade.py`, add a guard after the existing `verify="hash"` downgrade for bitmap mode: when `incremental_mode == "bitmap"` and `verify == "full"`, log a WARNING and downgrade to `"metadata"`. Warning text: "verify='full' is not supported in bitmap mode (incremental NBD exports contain only dirty blocks; qemu-img compare will always mismatch against source with backing chain). Downgrading to verify='metadata'."
- [x] 5.2 Verify the existing `verify="hash"` guard for bitmap mode still works (lines 436-443).

## 6. Add Orphaned Checkpoint Detection

**Specs:** `orphan-checkpoint-detection` (new spec)
**Design:** D6 — Orphaned checkpoint detection

- [x] 6.1 Add `orphan_checkpoints: list[str]` field to `StateCheckResult` in `qsnap/models/results.py` (default: empty list).
- [x] 6.2 In `qsnap/core/__init__.py`, extend `check_state()` to detect orphaned checkpoints: for each VM in config, call `BitmapBackupProvider.list_checkpoints(vm_name)`, parse the `qsnap-{hash}-{snapshot}` naming, compute configured target hashes via `_target_hash(str(target.path))`, and flag any checkpoint whose hash doesn't match. Add orphans to `StateCheckResult.orphan_checkpoints`.
- [x] 6.3 Make the detection non-fatal: if `virsh checkpoint-list` fails, log a WARNING and continue to the next VM.
- [x] 6.4 Update the CLI output of `qsnap check --state` in `qsnap/cli/commands.py` to display orphaned checkpoints (if any) under an "Orphaned Checkpoints" section.
- [x] 6.5 Import `BitmapBackupProvider` in `check_state()` only for the checkpoint listing — use a lightweight approach (the provider's `list_checkpoints` method only needs `IShell`, not `IStateManager`).

## 7. Update README

- [x] 7.1 In `README.md`, update the libvirt version requirements section: document that the `<incremental>` XML element is the correct mechanism for incremental NBD backup (not a CLI flag).
- [x] 7.2 Document the bitmap mode limitations: single-disk only, `verify="metadata"` recommended, checkpoints live in libvirt (not in state files).
- [x] 7.3 Add a troubleshooting section for orphaned checkpoints: how to detect (`qsnap check --state`) and clean up (`virsh checkpoint-delete --metadata`).
- [x] 7.4 Update the `qsnap.toml.example` comments: fix the stale "file-copy (default)" comment to reflect that "bitmap" is the default since the 2026-07-19 change.

## 8. Testing

**CRITICAL — Test delegation protocol:**
The main programmer agent MUST delegate ALL test writing/modification to @Mr.Tester subagents. The programmer SHALL NOT write tests directly. For EACH delegation group below, the programmer MUST:

1. Launch a @Mr.Tester subagent with the group's scope and scenario list
2. **MANDATORY**: Pass the file `/home/openuser/vm/qsnap/TESTING.md` to each @Mr.Tester as essential context — it defines the testing philosophy, categories, mock patterns, and rules
3. **MANDATORY**: Inform each @Mr.Tester that a real libvirt/virsh/qemu environment is available (libvirt 12.5.0, QEMU 11.0.2) for integration tests — they can use the existing `test_vm` fixture
4. Launch ALL groups IN PARALLEL (single message, multiple @Mr.Tester calls)
5. After all testers return: fix any reported source bugs, re-delegate affected groups
6. Repeat until all groups pass

**Reference:** Read `test-plan.md` Delegation Groups section for full details.

- [x] 8.1 Read `test-plan.md` Delegation Groups section
- [x] 8.2 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`, 14 scenarios, 4 MODIFY + 2 NEW). **MUST pass TESTING.md.** Critical: 3 tests assert `--incremental` CLI flag — MUST be changed to assert `<incremental>` in backup XML.
- [x] 8.3 Delegate group `file-copy-unit` to @Mr.Tester (scope: `tests/modules/backup/test_copy.py`, 11 scenarios, 1 MODIFY + 2 NEW). **MUST pass TESTING.md.** Critical: 1 test asserts `-F qcow2` — MUST be changed to `-B qcow2`.
- [x] 8.4 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_state_check.py`, 7 NEW scenarios). **MUST pass TESTING.md.** All new tests for orphaned checkpoint detection.
- [x] 8.5 Delegate group `nbd-utils-unit` to @Mr.Tester (scope: `tests/utils/test_nbd.py` NEW file, 5 NEW scenarios). **MUST pass TESTING.md.** Tests for `write_backup_xml` with/without `incremental` parameter.
- [x] 8.6 Delegate group `config-unit` to @Mr.Tester (scope: `tests/config/test_facade.py`, 5 NEW scenarios). **MUST pass TESTING.md.** Tests for `verify="full"` and `verify="hash"` auto-downgrade in bitmap mode.
- [x] 8.7 Delegate group `state-unit` to @Mr.Tester (scope: `tests/state/test_manager.py`, 3 NEW scenarios). **MUST pass TESTING.md.** Tests for FULL backup deduplication on load.
- [x] 8.8 Delegate group `bitmap-integration` to @Mr.Tester (scope: `tests/integration/test_bitmap_integration.py` + `tests/integration/test_nbd_full_backup.py`, 6 scenarios, 1 MODIFY + 5 NEW). **MUST pass TESTING.md.** Real virsh/qemu integration tests — `test_vm` fixture available.
- [x] 8.9 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 8.10 Re-delegate any groups affected by source fixes
- [x] 8.11 Verify all unit tests pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 8.12 Verify integration tests pass: `poetry run pytest tests/integration/ -m integration`
- [x] 8.13 Verify coverage matches `test-plan.md` — every spec scenario has at least one test

## 9. Spec Sync & Documentation

- [x] 9.1 Sync delta specs to main specs: run `openspec sync` or manually update `openspec/specs/` with the delta changes
- [x] 9.2 Verify the change is complete: `openspec status --change fix-bitmap-incremental-via-xml`
- [x] 9.3 Run `openspec validate` to ensure spec consistency
- [x] 9.4 Update `AGENTS.md` if any paradigm-level changes were made (unlikely — no ABC changes)

## 10. Final Verification

- [x] 10.1 Run the complete test suite: `poetry run pytest tests/ -m ""`
- [x] 10.2 Run ruff linter: `poetry run ruff check qsnap/ tests/`
- [x] 10.3 Run ruff formatter: `poetry run ruff format --check qsnap/ tests/`
- [x] 10.4 Run pyright type checker: `poetry run pyright qsnap/`
- [x] 10.5 Verify no Cyrillic characters in source files: `grep -rn '[А-я]' qsnap/ tests/` (should return nothing)
- [x] 10.6 Manual smoke test: run `qsnap run` on a test VM with bitmap mode and verify FULL→incremental flow works
