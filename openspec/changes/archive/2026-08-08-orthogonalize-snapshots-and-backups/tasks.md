# Tasks — orthogonalize-snapshots-and-backups

Implementation follows `design.md` decisions D1–D10. Phase 1 (section 2) MUST land as a
deployable commit point before Phase 2 (section 3) begins — Phase 1 unblocks production.
All code, comments, and documentation MUST be in English.

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b orthogonalize-snapshots-and-backups`
- [x] 1.2 Verify all existing tests pass before starting: run the full test suite
      (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. Phase 1 — urgent fixes in the current model (deployable checkpoint)

Transitional mechanisms marked "(transitional, design D5)" exist only between phases and are
removed in section 3. Specs describe the final state; design.md D5 governs the transition.

- [x] 2.1 Core queue rule (transitional, design D5): in `Core._backup_target`, per disk
      compute `anchor_ts` = timestamp of the newest FULL for that disk from
      `get_full_backups()`; build `transfer_list` = snapshots with `timestamp > anchor_ts`;
      this replaces `full_source_names` (spec rationale: design.md D5; removes P1–P3)
- [x] 2.2 Remove the temporal mismatch check: delete the block at
      `qsnap/modules/backup/bitmap.py:294-327` entirely; keep the size-based sanity check
      (spec: `nbd-bitmap-backup` REMOVED requirement)
- [x] 2.3 Convert definitive-failure `break` points in `transfer_missing` to `continue`
      (bitmap.py ~423, ~523, ~565, ~620, ~651 after 2.2); partial-file deletion per failure
      stays; Core aggregates failures and raises `BackupAbortError` only after all disks were
      attempted (spec: `backup-provider` "Immediate deletion…", `core-orchestrator`
      "VM-level failure isolation", "backup_failed WARNING…")
- [x] 2.4 Stopped-VM defer: add `deferred: bool = False` to `BackupResult`
      (`qsnap/models/results.py`); `transfer_missing` checks `is_vm_running` before
      `backup-begin` — stopped + checkpoint exists → `BackupResult(success=True,
      deferred=True)`; Core does NOT update `last_backup_allocation` for deferred results and
      logs INFO "VM stopped — backup deferred" (spec: `backup-provider` "Deferred backup
      result for stopped VMs", `core-orchestrator` "Deferred backups keep the onchange gate
      open")
- [x] 2.5 Startup orphan-checkpoint invariant: in startup validation, for each
      (target, disk), if the newest `qsnap-{hash}-{disk}-*` checkpoint has no backup file
      with `mtime >= checkpoint ts`, delete it best-effort (`checkpoint-delete` with
      `--metadata` fallback) + WARNING; non-fatal (spec: `startup-state-validation` "Orphan
      checkpoint invariant at startup")
- [x] 2.6 Blockjob probe: before `backup-begin`/FULL convert, run
      `virsh blockjob --domain <vm> --path <disk>`; active job → skip this disk's backup for
      the run with INFO, no baseline update, not a failure (spec: `core-orchestrator`
      "Blockjob probe before backup")
- [x] 2.7 Locking hardening: default lockfile `/var/lib/qsnap/qsnap.lock` when unconfigured;
      sentinel `"off"` disables explicitly; exclusive lock only for mutating commands —
      read-only commands run unlocked; exit 3 for writers on contention
      (`qsnap/models/config.py`, `qsnap/config/facade.py`, `qsnap/cli/app.py`,
      `qsnap/locking.py`; spec: `locking`)
- [x] 2.8 Error attribution: `BackupAbortError` message → target + disk(s) + reason(s);
      transfer-failure WARNING reworded to target/disk attribution with per-disk details
      (`qsnap/core/__init__.py`; spec: `core-orchestrator` "BackupAbortError…",
      "backup_failed WARNING…")
- [x] 2.9 Update `qsnap.toml.example`: document the lockfile default and `"off"` sentinel
- [x] 2.10 AGENTS.md phase-1 fixes: remove the misleading `*Module(Core)` naming-table row;
      add the locking contract (default lockfile, EX only for mutating commands, exit 3)
- [x] 2.11 Commit checkpoint: "phase 1: unblock backup pipeline" — this state is deployable
      to production (no data migration; first run transfers the pending delta against the
      existing checkpoint)

## 3. Phase 2 — decoupling (orthogonality paradigm)

- [x] 3.1 Models: add `BackupInfo` frozen dataclass (`name, path, timestamp, disk, is_full`)
      to `qsnap/models/results.py`; add `target: str | None = None` to `ActionRecord`; add
      failed-target/disk fields to `VMRunResult` per `action-audit-trail` and
      `backup-summary` deltas
- [x] 3.2 **BREAKING** interface: rewrite `qsnap/interfaces/backup.py` —
      `run_backup(vm_config, target, disk, *, opts) -> BackupResult`,
      `list(target) -> list[BackupInfo]`, `delete(backup: BackupInfo)`,
      `list_checkpoints`, `target_hash`; remove `transfer_missing` and
      `create_full_backup`; no `SnapshotInfo` anywhere in the API (spec:
      `backup-target-orthogonality`, `backup-provider`)
- [x] 3.3 Provider: implement `run_backup` in `BitmapBackupProvider` — no checkpoint → FULL
      (NBD export running / `qemu-img convert` stopped); checkpoint exists → delta since
      newest checkpoint; successor checkpoint atomic at freeze point; freeze-timestamp naming
      `{vm}.{freeze_ts}_{disk}_{hex6}.qcow2` / `.FULL.` variant; stopped+checkpoint →
      deferred; remove IStateManager dependency and stale-state healing from the provider;
      keep rotation, collision recovery, failure cleanup, stall detection (spec:
      `backup-provider`, `backup-target-orthogonality`, `nbd-bitmap-backup`)
- [x] 3.4 Core backup phase: `_backup_target(vm_config, target)` without snapshot data —
      per-disk loop: onchange gate → blockjob probe → `run_backup(disk)` → audit →
      dependency record → baseline update (success only); `needs_full` counts dependency
      keys in both formats; remove the transitional anchor queue (2.1) and
      `full_source_names`; aggregate per-disk failures, abort VM after all disks (spec:
      `core-orchestrator`, `backup-target-orthogonality`)
- [x] 3.5 State compatibility: `record_incremental_dependency` /
      `get_incremental_dependencies` accept and return mixed legacy snapshot-name keys and
      backup-name keys; chain-length counting key-format agnostic (`qsnap/state/`; spec:
      `state-management`)
- [x] 3.6 CLI restore: `restore --at <timestamp>` with first-point-≥-ts policy + actual-point
      logging + legacy name shim; `Core.restore(name=None, at=None, ...)`; per-disk steps
      preserved (spec: `restore-command`)
- [x] 3.7 CLI listing: `qsnap list restore-points <vm>` — per-target, per-disk freeze points
      from target files only, read-only (spec: `restore-points-listing`)
- [x] 3.8 Dry-run rework: backup predictions from target-internal data (gate state,
      checkpoint presence, FULL/delta decision) — "FULL/delta will be created" per disk with
      base-chain size estimate; remove snapshot-name-based transfer predictions (spec:
      `dry-run-prediction`)
- [x] 3.9 Summary & audit rendering: error lines carry `[disk]` prefix and target
      attribution, never framed as snapshot failures; backup-scoped ActionRecords carry
      `disk` + `target` (`qsnap/cli/summary.py`, `qsnap/cli/format.py`; spec:
      `backup-summary`, `action-audit-trail`)
- [x] 3.10 Factory: `create_backup_provider` constructs the provider without IStateManager
      (`qsnap/factory/default.py`; spec: `backup-provider` "Factory passes INbdClient…")
- [x] 3.11 Simplify the orphan-checkpoint invariant to freeze-ts equality where new-format
      names allow (design D9; spec: `startup-state-validation`)
- [x] 3.12 AGENTS.md full refresh: orthogonality paradigm (two worlds), new pipeline
      (`run_backup`), `BackupInfo`, `restore --at`, deferred results
- [x] 3.13 Size-based sanity check rebase: expected bound = active-layer allocation growth
      from `last_backup_allocation`; skip when no baseline (spec: `nbd-bitmap-backup`
      RENAMED requirement)

## 4. Testing

MANDATORY PROTOCOL FOR THE LEAD PROGRAMMER AGENT: the lead programmer agent delegates test
work to specialized tester agents (@Mr.Tester). When delegating ANY test group, the lead
programmer agent MUST attach the document describing the essence and paradigm of testing —
`/home/openuser/vm/qsnap/TESTING.md` — to EVERY tester's task, together with the group's
scope and scenario list from `test-plan.md`. No tester starts without TESTING.md.

- [ ] 4.1 Read `test-plan.md`: Delegation Groups, Coverage Map, Tests To Delete,
      Integration/Stress/E2E Updates, Risks & Edge Cases
- [ ] 4.2 Delegate group `provider-unit` to @Mr.Tester (scope: tests/modules/backup/*;
      attach TESTING.md + group scenarios + relevant Tests-To-Delete entries)
- [ ] 4.3 Delegate group `models-unit` to @Mr.Tester (scope: tests/models/*; attach
      TESTING.md)
- [ ] 4.4 Delegate group `provider-contract` to @Mr.Tester (scope: tests/interfaces/*;
      attach TESTING.md)
- [ ] 4.5 Delegate group `factory-mocks` to @Mr.Tester (scope: tests/factory/*,
      tests/mocks/*; attach TESTING.md)
- [ ] 4.6 Delegate group `core-unit` to @Mr.Tester (scope: tests/core/*; attach TESTING.md)
- [ ] 4.7 Delegate group `state-utils` to @Mr.Tester (scope: tests/state/*,
      tests/utils/test_parsing.py; attach TESTING.md)
- [ ] 4.8 Delegate group `cli-locking` to @Mr.Tester (scope: tests/utils/test_locking.py,
      tests/cli/*; attach TESTING.md)
- [ ] 4.9 Delegate group `integration-e2e` to @Mr.Tester (scope: tests/integration/*,
      tests/stress/*, tests/e2e/*; attach TESTING.md + Integration/Stress/E2E Updates
      section — existing integration tests must be amended to verify the NEW behavior, and
      the new integration scenarios from test-plan.md must be added)
- [ ] 4.10 Launch all groups IN PARALLEL (single message, one @Mr.Tester per group); each
      tester writes/fixes ONLY its own files and reports source bugs without fixing them
- [ ] 4.11 Execute the Tests-To-Delete list from test-plan.md (delete obsolete tests, apply
      MODIFY-INTO-REPLACEMENT items) as part of the group delegations
- [ ] 4.12 Review @Mr.Tester reports and fix any source-level bugs discovered
- [ ] 4.13 Re-delegate any groups affected by source fixes (again with TESTING.md attached)
- [ ] 4.14 Verify all groups pass and coverage matches `test-plan.md`:
      `poetry run pytest tests/ -m "not integration and not stress and not e2e"`, then
      `poetry run pytest tests/integration/ -m integration`, `tests/stress/ -m stress`,
      `tests/e2e/ -m e2e` where libvirt is available

## 5. Validation & Wrap-up

- [ ] 5.1 `openspec validate --change orthogonalize-snapshots-and-backups` passes
- [ ] 5.2 Full fast suite green; ruff + pyright clean (`ruff check qsnap tests`,
      `ruff format --check`, `pyright`)
- [ ] 5.3 Manual production verification script documented in the change README note:
      deploy → first run transfers pending delta against existing checkpoint →
      `qsnap check --state` clean → `qsnap -n run` predicts delta/FULL from target data
- [ ] 5.4 Confirm zero data migration: existing backup files, checkpoints, and state files
      untouched and readable (mixed-generation chain resolution test from test-plan.md)
