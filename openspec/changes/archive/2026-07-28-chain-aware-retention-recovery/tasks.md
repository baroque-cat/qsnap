## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b chain-aware-retention-recovery`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Per-Chain Retention + Auto-Recovery (Phase 1 — CRITICAL)

**Specs**: `per-chain-retention`, `auto-recovery`, `cascade-deletion` (modified), `startup-state-validation` (modified)
**Design**: design.md decisions D1, D2, D3
**Why**: Per-chain retention eliminates the cascade-deletion ghost-retention bug. Auto-recovery is mandatory — per-chain grouping cannot resolve broken-chain files without it.

- [x] 2.1 Add `_group_backups_by_chain()` method to Core (`qsnap/core/__init__.py`). Groups backups by chain via `_resolve_chain_full_anchor()`. Returns `(chains: dict[str, list[SnapshotInfo]], orphans: list[SnapshotInfo])`. See spec `per-chain-retention` requirement "Chain grouping via backing chain walk".
- [x] 2.2 Rewrite `_evaluate_backup_retention()` for per-chain evaluation (`qsnap/core/__init__.py`). Create chain-level `RetentionItem`s (one per chain, using FULL timestamp), pass to engine, expand results to individual items. Orphans go to remove list. See spec `per-chain-retention` requirement "Per-chain backup retention evaluation".
- [x] 2.3 Simplify `_cleanup_backups()` — remove ghost-retention, cascade-deletion, and `_build_backing_refs()` call (`qsnap/core/__init__.py`). Delete entire chains atomically. Add post-cleanup chain integrity verification. See spec `per-chain-retention` requirements "Cleanup deletes entire chains atomically" and "Post-cleanup chain integrity verification". See spec `cascade-deletion` REMOVED requirements.
- [x] 2.4 Extend `_validate_state_at_startup()` for auto-recovery (`qsnap/core/__init__.py`). Detect broken backup chains via `qemu-img info --backing-chain`, delete broken files, clean state, force FULL if no valid FULL remains. See spec `auto-recovery` requirements and `startup-state-validation` ADDED requirements.
- [x] 2.5 Add `_force_full_targets: set[str]` to `Core.__init__()` and check in `_backup_target()` (`qsnap/core/__init__.py`). When set, force `should_full = True` regardless of bucket strategy. See spec `auto-recovery` requirement "Force FULL creation when no valid FULL remains".
- [x] 2.6 Add oldest-prefix post-processing for snapshot retention in `_evaluate_snapshot_retention()` (`qsnap/core/__init__.py`). Clip remove list to contiguous oldest prefix. Move non-prefix remove items to keep (chain gap fillers). See spec `snapshot-oldest-prefix` requirements.

## 3. Checkpoint Lifecycle Fixes (Phase 2 — HIGH)

**Specs**: `nbd-bitmap-backup` (modified)
**Design**: design.md decisions D5, D6
**Why**: `--metadata`-only delete leaves QEMU dirty bitmaps that collide on retry. UUID suffix prevents name collisions.

- [x] 3.1 Change `_delete_checkpoint_best_effort()` to use full `checkpoint-delete` (not `--metadata`) with fallback (`qsnap/modules/backup/bitmap.py`). See spec `nbd-bitmap-backup` requirement "Full checkpoint deletion (not metadata-only)".
- [x] 3.2 Add UUID suffix to `_new_checkpoint_name()` via `secrets.token_hex(3)` (`qsnap/modules/backup/bitmap.py`). Update `_parse_checkpoint_timestamp()` regex to handle the new format. See spec `nbd-bitmap-backup` requirement "UUID suffix in checkpoint names".
- [x] 3.3 Add "Bitmap already exists" collision detection and retry in `transfer_missing()` (`qsnap/modules/backup/bitmap.py`). Catch error, call `_force_cleanup_checkpoints()`, regenerate name, retry. See spec `nbd-bitmap-backup` requirement "'Bitmap already exists' collision recovery".
- [x] 3.4 Add `_force_cleanup_checkpoints()` method (`qsnap/modules/backup/bitmap.py`). Force-delete ALL qsnap checkpoints for VM+target using full delete with fallback. See spec `nbd-bitmap-backup` requirement "Force cleanup deletes all qsnap checkpoints".

## 4. Temporal Mismatch Detection (Phase 3 — MEDIUM)

**Specs**: `nbd-bitmap-backup` (modified)
**Design**: design.md — temporal mismatch root cause
**Why**: NBD export always reads current live disk. Snapshots predating the checkpoint get wrong baseline → 15 GiB transfer.

- [x] 4.1 Add temporal cross-check in `transfer_missing()` after `prior` is determined (`qsnap/modules/backup/bitmap.py`). Skip snapshots whose timestamp predates the newest checkpoint's creation time. See spec `nbd-bitmap-backup` requirement "Temporal mismatch detection".
- [x] 4.2 Add size-based sanity check in `_copy_dirty_blocks()` after transfer (`qsnap/modules/backup/bitmap.py`). Warn when transferred bytes exceed 10x snapshot allocation. See spec `nbd-bitmap-backup` requirement "Size-based sanity check for temporal mismatch".

## 5. Blockcommit Recovery (Phase 4 — HIGH)

**Specs**: `blockcommit-recovery`, `chain-integrity-verification` (modified)
**Design**: design.md decision D7
**Why**: Broken snapshot chain causes entire blockcommit to be skipped → snapshots stuck forever. Partial blockcommit + auto-rebase allows progress.

- [x] 5.1 Extend `ChainVerifyResult` with `broken_file: str | None` field (`qsnap/models/results.py`). See spec `blockcommit-recovery` requirement "ChainVerifyResult reports broken file".
- [x] 5.2 Update `_verify_backing_chain()` to report `broken_file` when verification fails due to missing file (`qsnap/core/__init__.py`). See spec `chain-integrity-verification` MODIFIED requirement "Pre-commit backing chain integrity verification".
- [x] 5.3 Add partial blockcommit + auto-rebase in `_blockcommit_snapshots()` (`qsnap/core/__init__.py`). Replace all-or-nothing `return` with `_split_at_break()` + `_auto_rebase_stuck()`. See spec `blockcommit-recovery` requirement "Partial blockcommit on broken chain".
- [x] 5.4 Add `_split_at_break()` and `_auto_rebase_stuck()` methods to Core (`qsnap/core/__init__.py`). Split to_merge into committable/stuck. Rebase stuck snapshots via `qemu-img rebase -u`. Remove stale state entry. See spec `blockcommit-recovery` requirement "Auto-rebase for stuck snapshots".

## 6. Testing

**CRITICAL — Test Delegation Protocol:**

The lead programmer agent (@Mr.Programmer) MUST delegate ALL test writing to @Mr.Tester subagents. The lead programmer SHALL NOT write tests directly.

**MANDATORY — TESTING.md Transfer:**

For EVERY @Mr.Tester delegation, the lead programmer MUST include the following instruction in the task prompt:

> "Read and follow the testing paradigm described in `/home/openuser/vm/qsnap/TESTING.md`. This document defines the test architecture, categories, directory structure, mock strategy, and rules for the qsnap project. All tests MUST comply with this paradigm."

The lead programmer MUST pass TESTING.md to EACH test agent — no exceptions.

**Integration Tests — Full libvirt/qemu Access:**

The project has FULL access to libvirt and qemu for integration tests. Integration tests use real `virsh` and `qemu-img` commands against disposable test VMs. Mark integration tests with `@pytest.mark.integration`. See TESTING.md §4 for integration test rules.

- [x] 6.1 Read `test-plan.md` Delegation Groups section and `TESTING.md` testing paradigm
- [x] 6.2 Delegate group `G1` (per-chain retention unit tests) to @Mr.Tester — scope: `tests/core/test_pipeline.py`. **MUST include TESTING.md instruction.**
- [x] 6.3 Delegate group `G2` (integration: auto-recovery & production incident) to @Mr.Tester — scope: `tests/integration/test_auto_recovery.py` (NEW file). **MUST include TESTING.md instruction.** These are real libvirt/qemu integration tests.
- [x] 6.4 Delegate group `G3` (checkpoint lifecycle unit tests) to @Mr.Tester — scope: `tests/modules/backup/test_bitmap.py`. **MUST include TESTING.md instruction.**
- [x] 6.5 Delegate group `G5` (temporal mismatch unit tests) to @Mr.Tester — scope: `tests/modules/backup/test_bitmap_incremental.py`. **MUST include TESTING.md instruction.**
- [x] 6.6 Delegate group `G6` (integration: blockcommit recovery) to @Mr.Tester — scope: `tests/integration/test_blockcommit_recovery.py` (NEW file). **MUST include TESTING.md instruction.** These are real libvirt/qemu integration tests.
- [x] 6.7 Delegate group `G7` (chain integrity verification updates) to @Mr.Tester — scope: `tests/core/test_pipeline.py` (existing + new). **MUST include TESTING.md instruction.**
- [x] 6.8 Delegate group `G9` (ChainVerifyResult model test) to @Mr.Tester — scope: `tests/models/test_results.py`. **MUST include TESTING.md instruction.**
- [x] 6.9 Apply test modifications from `test-plan.md` § Test Modifications — delegate removal/modification of existing cascade-deletion and ghost-retention tests to @Mr.Tester. Groups G4 (existing startup validation) and G8 (contract tests) need verification only.
- [x] 6.10 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 6.11 Re-delegate any groups affected by source fixes
- [x] 6.12 Verify all groups pass and coverage matches `test-plan.md`: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` for unit tests, `poetry run pytest tests/integration/ -m integration` for integration tests

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. Read TESTING.md → testing paradigm, categories, rules
  3. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths from test-plan.md)
     - The group's scenario list from Coverage Map
     - Instruction: "Read and follow /home/openuser/vm/qsnap/TESTING.md. Write or fix ONLY these specific tests. Report source bugs, don't fix them."
     - For integration groups (G2, G6): "These are real libvirt/qemu integration tests. Use @pytest.mark.integration. Create disposable test VMs per TESTING.md §4."
  4. Launch ALL groups IN PARALLEL (single message) where possible
  5. After all testers return: fix any reported source bugs, re-delegate affected groups
  6. Repeat until all groups pass
-->

## 7. Final Verification

- [x] 7.1 Run full unit test suite: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 7.2 Run integration tests: `poetry run pytest tests/integration/ -m integration`
- [x] 7.3 Verify no ghost-retention or cascade-deletion code remains in `_cleanup_backups()`
- [x] 7.4 Verify per-chain retention works end-to-end: create FULL + incrementals, run pipeline, verify chain integrity
- [x] 7.5 Verify auto-recovery: simulate broken chain, run pipeline, verify auto-deletion + fresh FULL creation
- [x] 7.6 Run linter: `poetry run ruff check qsnap/ tests/`
- [x] 7.7 Run type checker: `poetry run pyright qsnap/`
