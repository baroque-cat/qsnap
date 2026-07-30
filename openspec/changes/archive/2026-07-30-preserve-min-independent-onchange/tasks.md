## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b preserve-min-independent-onchange`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Dataclass Changes

Reference: `specs/config-model/spec.md`, `specs/count-based-retention/spec.md`, design decisions D1, D6.

- [x] 2.1 Add `preserve_min: int = 0` field to `RetentionPolicy` in `qsnap/models/config.py` (third field, default 0 = inactive). The retention engine (`TimeBasedRetention`) ignores this field — it is transport-only.
- [x] 2.2 Add `snapshot_preserve_min: int = 0` field to `GlobalConfig` in `qsnap/models/config.py` (default 0 = inactive, immutable frozen dataclass).
- [x] 2.3 Add `snapshot_preserve_min: int | None = None` field to `VMConfig` in `qsnap/models/config.py` (None = inherits from global, resolved by ConfigFacade).
- [x] 2.4 Update `qsnap/config/facade.py` to parse and resolve `snapshot_preserve_min` via option inheritance (global → VM). Validate non-negative integer; raise `ConfigError` on negative values.
- [x] 2.5 Update `qsnap/models/config.py` `__all__` exports if needed for the new fields.
- [x] 2.6 Run `poetry run ruff check qsnap/models/config.py qsnap/config/facade.py` and `poetry run pyright qsnap/models/config.py qsnap/config/facade.py` to verify linting and type checking pass.

## 3. Snapshot Preserve-Min Post-Processing

Reference: `specs/snapshot-preserve-min/spec.md`, design decisions D1, D2.

- [x] 3.1 In `Core._evaluate_snapshot_retention()` (`qsnap/core/__init__.py`), add a preserve_min post-processing filter AFTER the existing oldest-prefix filter. Logic: if `preserve_min > 0` and `len(final_remove) > len(snapshots) - preserve_min`, trim `final_remove` to the oldest `max(0, len(snapshots) - preserve_min)` items and move the trimmed (newest excess) items from `remove` to `keep`.
- [x] 3.2 Construct `RetentionPolicy` with `preserve_min=vm_config.snapshot_preserve_min or 0` in `_evaluate_snapshot_retention()`.
- [x] 3.3 Ensure the preserve_min filter does NOT affect target/backup retention (`_evaluate_backup_retention`) — it is snapshot-only.
- [x] 3.4 Run `poetry run ruff check qsnap/core/__init__.py` and `poetry run pyright qsnap/core/__init__.py`.

## 4. Independent Target Onchange Gate

Reference: `specs/independent-target-onchange/spec.md`, `specs/change-detection/spec.md`, design decisions D3, D4, D5.

- [x] 4.1 Replace `Core._should_backup_onchange()` (`qsnap/core/__init__.py`) with source-disk-based change detection. New logic: create a change detector via `factory.create_change_detector(vm_config.change_detection_mode)`, call `detector.has_changed(vm_config)` to obtain `ChangeResult.current_allocation`, compare `current_allocation` against `state.get_last_backup_allocation(target_path)`. For allocation-size mode: `changed = current > last`. For allocation-map mode: `changed = current != last`. When `last` is `None` (first run): return True.
- [x] 4.2 In `Core._backup_target()`, after a successful backup transfer (FULL or incremental), call `state.set_last_backup_allocation(str(target.path), change_result.current_allocation)` to update the per-target baseline. Do NOT update the baseline if the backup fails or if the gate skipped transfer.
- [x] 4.3 Remove the `provider.list(target)` call from the onchange gate — the gate no longer depends on snapshot names or target file listing.
- [x] 4.4 Ensure the retention-separation behavior is preserved: when the gate returns False (disk unchanged), still run `_evaluate_backup_retention()` and `_cleanup_backups()`.
- [x] 4.5 Run `poetry run ruff check qsnap/core/__init__.py` and `poetry run pyright qsnap/core/__init__.py`.

## 5. Mock & Factory Updates

Reference: `test-plan.md` Group `mocks`.

- [x] 5.1 Update `MockChangeDetector` in `tests/mocks/mock_modules.py` to accept a configurable `current_allocation` parameter so Core tests can control the value returned by `has_changed()`.
- [x] 5.2 Update `MockVMModuleFactory.create_change_detector()` in `tests/mocks/mock_factory.py` to return a `MockChangeDetector` that Core tests can configure.

## 6. Testing

Reference: `test-plan.md` Delegation Groups section. Launch @Mr.Tester subagents IN PARALLEL (all in one message).

**CRITICAL — TEST ORCHESTRATION PROTOCOL:**

The main programmer agent MUST pass the following document to EACH @Mr.Tester subagent:
- `/home/openuser/vm/qsnap/TESTING.md` — the testing philosophy and paradigm document. This defines test categories, mock strategy, fixtures, and rules. Every tester MUST read and follow this paradigm.

Each @Mr.Tester subagent receives:
1. The TESTING.md document (path above)
2. The group's scope (file paths from test-plan.md)
3. The group's scenario list from the Coverage Map in test-plan.md
4. Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
5. For integration test groups: "You have full access to libvirt and qemu. Use the `test_vm` fixture from `tests/integration/conftest.py`. Mark tests with `@pytest.mark.integration`."

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The TESTING.md document at /home/openuser/vm/qsnap/TESTING.md
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
     - For integration groups: "You have full access to libvirt and qemu."
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->

- [x] 6.1 Read `test-plan.md` Delegation Groups section
- [x] 6.2 Delegate group `config-model` to @Mr.Tester (scope: `tests/config/test_model.py`). Pass TESTING.md. Scenarios: 7 (RetentionPolicy three fields, GlobalConfig/VMConfig snapshot_preserve_min).
- [x] 6.3 Delegate group `config-inheritance` to @Mr.Tester (scope: `tests/config/test_resolver.py`, `tests/config/test_fixtures.py`). Pass TESTING.md. Scenarios: 5 (snapshot_preserve_min inheritance + validation).
- [x] 6.4 Delegate group `core-pipeline` to @Mr.Tester (scope: `tests/core/test_pipeline.py`, `tests/core/test_preserve.py`). Pass TESTING.md. Scenarios: 26 (onchange gate rewrite + preserve_min filter). This group includes DELETE operations for 12 old Approach B tests.
- [x] 6.5 Delegate group `integration-onchange` to @Mr.Tester (scope: `tests/integration/test_onchange.py`). Pass TESTING.md. Full rewrite — DELETE all 4 old Approach B integration tests.
- [x] 6.6 Delegate group `integration-preserve-min` to @Mr.Tester (scope: `tests/integration/test_preserve_min.py` — NEW file). Pass TESTING.md. Full libvirt/qemu access. Scenarios: 6 (real blockcommit, source-disk onchange, per-target baseline).
- [x] 6.7 Delegate group `interfaces` to @Mr.Tester (scope: `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_change_detector.py`). Pass TESTING.md. Scenarios: 3 (last_backup_allocation contract).
- [x] 6.8 Delegate group `mocks` to @Mr.Tester (scope: `tests/mocks/mock_modules.py`, `tests/mocks/mock_factory.py`). Pass TESTING.md. MockChangeDetector configurability.
- [x] 6.9 Review all @Mr.Tester reports and fix any source-level bugs discovered
- [x] 6.10 Re-delegate any groups affected by source fixes
- [x] 6.11 Verify all groups pass: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` for unit/mock/contract tests
- [x] 6.12 Verify integration tests pass: `poetry run pytest tests/integration/ -m integration`
- [x] 6.13 Verify coverage matches test-plan.md Coverage Map — every scenario has a corresponding test

## 7. Final Verification

- [x] 7.1 Run full linting: `poetry run ruff check qsnap/ tests/`
- [x] 7.2 Run full type checking: `poetry run pyright qsnap/`
- [x] 7.3 Run full test suite: `poetry run pytest tests/ -m ""`
- [x] 7.4 Verify no old Approach B test remnants exist: `grep -r "provider.list" tests/core/test_pipeline.py` should return nothing in onchange-gate context
- [x] 7.5 Verify `snapshot_preserve_min` is documented in config examples if any exist
- [x] 7.6 Commit all changes with a descriptive message
