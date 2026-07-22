## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b remove-rsync-filecopy`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` (baseline must be green; note that the suite will go temporarily RED after section 2–4 code removal and is brought back to green by the delegated test cleanup in section 6 — this is expected for a removal change)
- [x] 1.3 Read `openspec/changes/remove-rsync-filecopy/proposal.md`, `design.md`, `test-plan.md`, and all 16 spec deltas under `openspec/changes/remove-rsync-filecopy/specs/`. Also read `AGENTS.md` (architecture paradigm — DI + ABC interfaces must remain intact) and `TESTING.md` (testing paradigm — binding for all test work)

## 2. Pre-removal verification (no code changes yet)

- [x] 2.1 Verify no stray callers of deleted helpers outside the known inventory (test-plan R1): `rg "verify_backup" qsnap/ tests/` — every hit must be in `qsnap/utils/verification.py`, `qsnap/modules/backup/file_copy.py`, or files marked DELETE/MODIFY in test-plan.md; report any unexpected hit before proceeding
- [x] 2.2 Verify `rg "parse_rate_limit|rate_limit_to_kib" qsnap/ tests/` hits only `qsnap/utils/parsing.py`, `qsnap/config/facade.py`, `qsnap/modules/backup/file_copy.py`, `tests/utils/test_parsing.py`
- [x] 2.3 Verify `rg "FileCopyBackupProvider|file_copy" qsnap/ tests/` hits match the test-plan inventory (production: `file_copy.py`, `factory/default.py`, `core/__init__.py`, docstrings; tests: files marked DELETE/MODIFY)

## 3. Code removal — provider, helpers, config fields

- [x] 3.1 Delete `qsnap/modules/backup/file_copy.py` entirely (design D1). Clean any re-exports in `qsnap/modules/backup/__init__.py`
- [x] 3.2 Delete `verify_backup()` from `qsnap/utils/verification.py` (design D6). Keep `verify_full_backup()` and `verify_bitmap_incremental()` untouched
- [x] 3.3 Delete `parse_rate_limit()` and `rate_limit_to_kib()` from `qsnap/utils/parsing.py`
- [x] 3.4 Remove the `rate_limit` parameter from `IBackupProvider.transfer_missing()` in `qsnap/interfaces/backup.py` (design D4) and from `BitmapBackupProvider.transfer_missing()` in `qsnap/modules/backup/bitmap.py`; remove the stale rsync/`--bwlimit` docstring reference in `bitmap.py` (~line 180)
- [x] 3.5 Remove config fields (design per `config-model` delta): `rate_limit` from `GlobalConfig`; `rate_limit`, `copy_base`, `incremental_mode` from `TargetConfig` in `qsnap/models/config.py`
- [x] 3.6 Update `qsnap/config/facade.py` (per `config-parsing` delta): stop parsing `incremental_mode`, `rate_limit`, `copy_base`; if any of them is present in TOML, log a deprecation WARNING naming the field and ignore the value (design D3 — same mechanism as the existing `full_every` deprecation). Remove the mode-dependent `verify` default resolution — the default is always `"metadata"` with no mode-dependence and no auto-downgrade

## 4. Factory & Core changes

- [x] 4.1 Rewrite `DefaultFactory.create_backup_provider()` in `qsnap/factory/default.py` (per `module-factory` delta, design D2): delete the `FileCopyBackupProvider` import and both branches; always return `BitmapBackupProvider(self._shell, self._state, LibnbdClient())`; libvirt < 7.2 → `RuntimeError` with an actionable "libvirt >= 7.2 required" message (no fallback); missing libnbd → existing `RuntimeError(MISSING_LIBNBD_ERROR)` (unchanged)
- [x] 4.2 Update `Core._validate_environment()` in `qsnap/core/__init__.py` (per `env-validation` delta, design D5): delete the unconditional `which rsync` check and its error branch; make the libnbd importability check unconditional (it currently keys on `incremental_mode == "bitmap"` targets — that field no longer exists); dry-run keeps WARNING-only behavior
- [x] 4.3 Remove `rate_limit` plumbing in `qsnap/core/__init__.py`: drop the parameter from the `transfer_missing()` call sites (including `_transfer_with_retry()`); delete any `rate_limit`-derived locals/logging
- [x] 4.4 Reword the pre-flight truncated-qcow2 cleanup comment/log in `Core._preflight_cleanup()` from "truncated rsync artifact" to "truncated transfer artifact" (design D8 — behavior unchanged, per `pre-flight-cleanup` delta)
- [x] 4.5 Verify `qsnap/interfaces/__init__.py` and other re-export surfaces no longer reference removed symbols; run `ruff check` and `pyright` to catch dangling imports now (full gate runs in section 7)

## 5. Docs & example config

- [x] 5.1 Update `AGENTS.md`: pipeline pseudocode — replace "transfer missing snapshots (rsync only — design D3)" with the NBD dirty-block transfer wording; architecture diagram — remove `FileCopyBackupProvider`; shell-abstraction section — drop `rsync` from the `run_with_stall_detection` command examples
- [x] 5.2 Update `README.md`: remove `incremental_mode`/`rate_limit`/`copy_base` from option tables and examples; remove the completed "Migration from rsync to NBD" section; remove rsync from Requirements and dry-run check lists; add a short BREAKING note (single NBD/libnbd provider; libvirt >= 7.2 and python3-libnbd are hard requirements; incremental backups require a running VM; removed TOML keys are warned-and-ignored)
- [x] 5.3 Update `TESTING.md`: remove the `test_copy.py` reference from the directory tree
- [x] 5.4 Update `qsnap.toml.example` (per `config-parsing` delta): remove `incremental_mode`, `rate_limit`, `copy_base` from documented fields

## 6. Testing (delegated — removal-first)

**MANDATORY DELEGATION PROTOCOL FOR THE MAIN PROGRAMMER AGENT:** For EVERY tester subagent you launch in this section, you MUST include in its prompt: (a) the group's scope and its Coverage Map rows / Test Modifications entries from `test-plan.md`; (b) the instruction that this is a REMOVAL-FIRST change — the primary job is deleting and adjusting old rsync/file-copy tests, NOT writing new tests (NEW tests are limited to exactly those marked NEW in test-plan.md: `test_factory_always_returns_bitmap_backup_provider`, `test_factory_old_libvirt_raises_runtime_error`, `test_removed_fields_trigger_deprecation_warnings`, `test_create_full_backup_stopped_vm_returns_error`, `test_libnbd_missing_hard_failure`, `test_dry_run_downgrades_libnbd_missing_to_warning`); and (c) **the full verbatim contents of `/home/openuser/vm/qsnap/TESTING.md`** — paste the document into every tester prompt and instruct the tester that all test work MUST comply with the TESTING.md paradigm (tests mirror production hierarchy, custom mock classes per ABC, contract tests parametrized over implementations, registered pytest markers, no pytest-mock, MockShell `.expect().returns()` style). No tester subagent may start without TESTING.md in its prompt.

- [x] 6.1 Read `test-plan.md` — Coverage Map, Delegation Groups, Test Modifications, Risks & Edge Cases
- [x] 6.2 Delegate group `backup-modules` to @Mr.Tester (scope: `tests/modules/backup/*`, `tests/utils/test_verification.py`, `tests/utils/test_verification_bitmap.py`, `tests/utils/test_hash.py` — headliners: DELETE `test_copy.py` and `test_verification.py` wholesale; NEW stopped-VM FULL error test) — with TESTING.md per the protocol above
- [x] 6.3 Delegate group `core-suite` to @Mr.Tester (scope: `tests/core/*`, `tests/conftest.py`, `tests/cli/test_commands.py` — headliners: conftest fixture cleanup, rsync validation-test deletions, bitmap-only pipeline test, NEW dry-run libnbd warning test) — with TESTING.md per the protocol above
- [x] 6.4 Delegate group `config-suite` to @Mr.Tester (scope: `tests/config/*`, `tests/fixtures/configs/*`, `tests/utils/test_parsing.py`, `tests/utils/test_nbd.py` — headliners: removed-field test deletions, fixture TOML updates incl. `deprecated_fields.toml`, NEW deprecation-warnings test) — with TESTING.md per the protocol above
- [x] 6.5 Delegate group `factory-interfaces-mocks` to @Mr.Tester (scope: `tests/factory/*`, `tests/interfaces/*`, `tests/mocks/*` — headliners: factory hard-gate tests, contract-test signature update, mock signature updates) — with TESTING.md per the protocol above
- [x] 6.6 Delegate group `integration-suite` to @Mr.Tester (scope: `tests/integration/*` — headliners: DELETE `test_verification.py`, rewrite `test_nbd_full_backup.py` and `test_stale_state_recovery.py` onto `BitmapBackupProvider`, `verify_backup` → `verify_bitmap_incremental` in retry tests, NEW `test_libnbd_missing_hard_failure`) — with TESTING.md per the protocol above
- [x] 6.7 Launch 6.2–6.6 IN PARALLEL (single message). Review all @Mr.Tester reports; fix any source-level bugs they surface (testers report bugs, they do not fix source)
- [x] 6.8 Re-delegate any groups whose files were affected by source fixes from 6.7
- [x] 6.9 Verify all five groups pass and the final test inventory matches `test-plan.md` (deletions done, KEEP files untouched and green, the six NEW tests exist and pass)

## 7. Verification gates & closure

- [x] 7.1 `poetry run ruff check qsnap/ tests/` and `poetry run ruff format --check qsnap/ tests/` — clean
- [x] 7.2 `poetry run pyright` (strict) — clean
- [x] 7.3 `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — fully green; then integration subset if a libvirt host is available: `poetry run pytest tests/integration/ -m integration`
- [x] 7.4 Protective-skeleton run (test-plan R5): `pytest tests/modules/backup/test_bitmap.py tests/modules/backup/test_bitmap_incremental.py tests/modules/backup/test_full_verification.py tests/utils/test_verification_bitmap.py tests/utils/test_nbd.py tests/core/test_full_verification_pipeline.py` — all green
- [x] 7.5 rg gates (test-plan R8): `rg -i "rsync" qsnap/` → zero hits; `rg "FileCopyBackupProvider|file_copy" qsnap/ tests/` → zero hits; `rg "rate_limit\b|copy_base\b|incremental_mode\b" qsnap/` → hits only inside the deprecation-WARNING handler in `config/facade.py` (and `deprecated_fields.toml` under `tests/fixtures/configs/`)
