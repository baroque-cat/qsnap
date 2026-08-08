# Tasks: recover-lost-checkpoint-bitmaps

**Note to the implementing agent (lead programmer):** all code, comments, and documentation
MUST be written in English. Follow AGENTS.md (DI with ABC interfaces, result objects,
modules never call each other, Core is the only coordinator) and TESTING.md for every test
decision. Reference `proposal.md` for scope, `design.md` for decisions D1–D12,
`specs/*/spec.md` for normative requirements, and `test-plan.md` for verification.

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b recover-lost-checkpoint-bitmaps`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite
      (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. Models, Interfaces, and State Schema

- [x] 2.1 Add `kind: str` field (`"full" | "delta" | "recovered_delta"`) to `BackupResult`
      in `qsnap/models/results.py` with a backward-compatible default; add a frozen
      `BaselineAssessment` result dataclass (status enum `no_checkpoint | healthy | dead |
      unknown`, newest checkpoint name, gate outcome + failed-gate reason, size estimate)
      (spec: `backup-provider` "Backup results carry the backup kind",
      `checkpoint-bitmap-health-probe` "Baseline assessment exposed on IBackupProvider")
- [x] 2.2 Add read-only `assess_baseline(vm_config, target, disk) -> BaselineAssessment` to
      `IBackupProvider` in `qsnap/interfaces/backup.py` (BREAKING — design D10)
- [x] 2.3 Add `get_boot_id/set_boot_id` and `get_last_commit_ts/set_last_commit_ts` to
      `IStateManager` in `qsnap/interfaces/state.py` (spec: `state-management` deltas)
- [x] 2.4 Implement the four new methods in `JsonStateManager` (`qsnap/state/json_manager.py`):
      optional per-VM `boot_id` field and optional per-disk `last_commit_ts` map; legacy
      state files without the fields load as `None`; atomic writes and rotation unchanged
- [x] 2.5 Implement the four methods in `tests/mocks/mock_state.py`
      (`InMemoryStateManager`) and update `tests/mocks/mock_modules.py`
      (`MockBitmapBackupProvider.assess_baseline`, `run_backup` sets `kind`)
      (TESTING.md paradigm: every ABC gets a mock)

## 3. Bitmap Health Probe and Assessment (provider)

- [x] 3.1 Implement `_probe_checkpoint_bitmap()` in `qsnap/modules/backup/bitmap.py`:
      running VM → `virsh qemu-monitor-command` `query-named-block-nodes` via `IShell`
      (bounded timeout); stopped VM → `qemu-img info -U --backing-chain --output=json`;
      return `HEALTHY | DEAD | UNKNOWN`; never raise (design D1/D2, spec:
      `checkpoint-bitmap-health-probe`)
- [x] 3.2 Add QMP/qemu-img fixture outputs under `tests/fixtures/shell_outputs/`
      (`qmp_block_nodes_healthy.json`, `qmp_block_nodes_bitmap_missing.json`,
      `qmp_block_nodes_bitmap_inconsistent.json`, `qmp_error.json`,
      `qemu_img_info_backing_chain_with_bitmaps.json`,
      `qemu_img_info_backing_chain_no_bitmaps.json`) (test-plan.md §5.1)
- [x] 3.3 Implement `assess_baseline()` on `BitmapBackupProvider` combining discovery,
      probe, gates, and size estimate; read-only, zero mutations (design D10)
- [x] 3.4 Implement `estimate_recovered_delta_size(shell, layers)` in `qsnap/utils/space.py`
      (sum of `actual-size`, FULL chain-sum fallback on unreadable layers; spec:
      `size-estimation` delta)

## 4. Recovery Engine (provider)

- [x] 4.1 Implement gates G1–G3 (G1 via `last_commit_ts` marker — absent marker fails;
      G2 live-chain-vs-state match; G3 overlay readability) and the copy-set computation
      (overlay active at freeze + overlays created after; fallback: all overlays above
      `base_image`) (design D4/D5, spec: `bitmap-loss-recovery`)
- [x] 4.2 Implement the recovered-delta lifecycle: successor checkpoint via
      `virsh checkpoint-create` (fallback: backup-begin FULL-XML form if checkpoint-create
      fails — design D6), `qemu-img create -b <newest target backup>`, write-server,
      per-layer read-only copy of ALL data+zero extents oldest→newest (holes skipped),
      publish, chain-to-FULL + `qemu-img check` verification; on failure rollback
      (delete successor checkpoint, remove tmp) and fall back to FULL in the same run
      (design D6/D7)
- [x] 4.3 Wire the decision tree into `run_backup`: probe after `_select_newest`; HEALTHY →
      delta as today; DEAD → WARNING + gates → recovered delta or FULL; UNKNOWN → attempt
      delta (design D2, spec: `nbd-bitmap-backup` MODIFIED discovery requirement)
- [x] 4.4 Implement `_is_inconsistent_checkpoint_error` reactive backstop: on "checkpoint
      inconsistent" from `backup-begin`, delete exactly the named checkpoint, retry once
      (recovered delta if gates pass else FULL); leave `_is_collision_error` unchanged
      (design D9)
- [x] 4.5 Implement the FULL-branch ordering inside recovery: dead checkpoint deletion and
      generation retirement only after the new FULL passes verification; set
      `BackupResult.kind` on every return path (design D8/D11)

## 5. Core Orchestration

- [x] 5.1 Record `boot_id` in state after each fully successful run; read it during
      recovery for WARNING wording (design D3, spec: `state-management` delta)
- [x] 5.2 Record per-disk `last_commit_ts` after every successful blockcommit and
      `qemu-img commit` (both `_blockcommit_one_disk` success path and deferred-drain
      commits) (spec: `state-management` delta)
- [x] 5.3 Extend `_check_orphan_checkpoint`: DEAD-bitmap checkpoints are orphans even with
      a covering file; add the missing dry-run guard (dry-run predicts only); real runs
      delete best-effort with WARNING (design D12, spec: `startup-state-validation`
      MODIFIED invariant)
- [x] 5.4 Implement immediate retirement of the recovery-superseded generation in
      `_cleanup_backups` (recovery flag bypasses `keep_generations`; verify-before-delete
      M1/M2 gates on the deleted FULL unchanged) (spec: `per-chain-retention` delta)
- [x] 5.5 Dry-run parity in `_backup_target`: consume `assess_baseline`; execute the
      blockjob probe and read-only startup checks in dry-run; emit the four prediction
      wordings (FULL / delta / recovered-delta with gates OK / FULL with failed gate
      reason) preceded by the crash WARNING; zero mutations (design D10, spec:
      `dry-run-prediction` MODIFIED requirements)
- [x] 5.6 Exit-code semantics: successful recovery → WARNING + exit 0; only exhausted
      recovery (delta attempt and FULL fallback both fail) raises `BackupAbortError`
      (design D11)
- [x] 5.7 Render `kind == "recovered_delta"` distinctly in `qsnap/cli/summary.py` action
      and prediction output (spec: `backup-provider` "Recovered delta is auditable")

## 6. Testing

**Test orchestration protocol for the lead programmer agent:** read `test-plan.md`
(Delegation Groups + Coverage Map) and delegate test implementation to dedicated tester
subagents, one per group, launched IN PARALLEL (single message). **MANDATORY: the lead
programmer agent MUST attach the testing-paradigm document `/home/openuser/vm/qsnap/TESTING.md`
to the brief of EVERY delegated tester**, together with the group scope, the group's
scenario list from the Coverage Map, and the instruction "write or fix ONLY these specific
tests; report source-level bugs, do not fix them". Testers must also follow test-plan.md
§3b (Tests To Remove — currently: no deletions, amendments only) and §5 (synthetic and
integration simulation design).

- [x] 6.1 Read `test-plan.md` Delegation Groups section
- [x] 6.2 Delegate group `provider-unit-recovery` to a tester subagent (scope:
      `tests/modules/backup/test_bitmap_recovery.py` (NEW), `test_bitmap.py`,
      `test_bitmap_incremental.py`, `test_bitmap_convert.py`, `tests/utils/test_space.py`,
      `tests/utils/test_extents.py`) — brief MUST include TESTING.md
- [x] 6.3 Delegate group `core-pipeline-recovery` to a tester subagent (scope:
      `tests/core/test_recovery_pipeline.py` (NEW), `test_pipeline.py`,
      `test_dry_run_prediction.py`, `test_engine.py`, `test_full_verification_pipeline.py`,
      `test_bitmap_dependency.py`, `test_enospc_isolation.py`) — brief MUST include TESTING.md
- [x] 6.4 Delegate group `state-and-contracts` to a tester subagent (scope:
      `tests/state/test_recovery_state.py` (NEW), `tests/state/test_manager.py`,
      `tests/interfaces/test_backup_provider.py`, `tests/interfaces/test_state_manager.py`,
      `tests/mocks/*`, `tests/models/test_results.py`) — brief MUST include TESTING.md
- [x] 6.5 Delegate group `dry-run-parity` to a tester subagent (scope:
      `tests/core/test_dry_run_recovery_prediction.py` (NEW), `tests/cli/test_summary.py`,
      `tests/integration/test_dry_run.py`) — brief MUST include TESTING.md
- [x] 6.6 Delegate group `integration-recovery` to a tester subagent (scope:
      `tests/integration/test_bitmap_loss_recovery.py` (NEW) per test-plan.md §5.2
      mechanisms (c) deterministic bitmap removal and (b) kill -9 power-cut, plus
      amendments to `test_full_backup.py`, `test_incremental_backup.py`,
      `test_auto_recovery.py`, `test_startup_validation.py`, `test_verify_before_delete.py`,
      `test_backup_retry_max_zero.py`) — brief MUST include TESTING.md
- [x] 6.7 Delegate group `e2e-recovery` to a tester subagent (scope:
      `tests/e2e/test_from_config.py` — `test_config_to_restore_after_recovered_delta`) —
      brief MUST include TESTING.md
- [x] 6.8 Review all tester reports and fix any source-level bugs discovered (production
      code only; testers do not fix source)
- [x] 6.9 Re-delegate any groups affected by source fixes (again with TESTING.md attached)
- [x] 6.10 Verify all groups pass and coverage matches `test-plan.md`:
      `poetry run pytest tests/ -m "not integration and not stress and not e2e"`, then
      `poetry run pytest tests/integration/ -m integration` and
      `poetry run pytest tests/e2e/ -m e2e` where libvirt is available

## 7. Verification & Wrap-up

- [x] 7.1 Two-run incident replay acceptance check (unit level): first run heals with
      WARNING + exit 0 (recovered delta or FULL), second run is a clean delta with zero
      warnings (spec: `bitmap-loss-recovery` "No infinite failure loop")
- [x] 7.2 Dry-run parity acceptance check: on a dead-checkpoint system, dry-run predicts
      the exact recovery outcome and mutates nothing (checkpoint set, state, targets
      byte-identical) (spec: `dry-run-prediction`)
- [x] 7.3 Run `poetry run ruff check qsnap tests` and `poetry run ruff format --check`,
      then `pyright` (strict) — all clean
- [x] 7.4 Validate the change artifacts: `openspec validate recover-lost-checkpoint-bitmaps`
- [x] 7.5 Confirm no Cyrillic characters in any source or test file; all code, comments,
      logs, and docs are English
