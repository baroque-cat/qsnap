## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b vm-level-backup-engine-options`
- [x] 1.2 Verify all existing tests pass before starting: run `poetry run pytest tests/ -m "not integration and not stress and not e2e"` to establish a passing baseline

## 2. Config Model — VMConfig engine option fields (design D1, specs/config-model)

- [x] 2.1 Add six frozen fields to `VMConfig` in `qsnap/models/config.py`: `compress: bool = True`, `compression_type: str = "zstd"`, `convert_parallel: int = 4`, `convert_out_of_order: bool = True`, `backup_stall_timeout: str = "30m"`, `verify: str = "metadata"` — placed with the other optional fields, Sphinx-style docstrings in English
- [x] 2.2 Fix the misleading `TargetConfig` docstring (`qsnap/models/config.py` ~lines 157-164): document the real inheritance chain global → VM → target (the VM hop now exists), and document the new `VMConfig` fields as VM-level defaults for target inheritance
- [x] 2.3 Verify: `poetry run pyright qsnap/models/config.py` clean; `poetry run pytest tests/config/test_model.py -v` (pre-existing failures here are expected until group G3 lands)

## 3. Config Parsing — VM-level parsing & inheritance (design D2/D4, specs/config-parsing)

- [x] 3.1 In `ConfigFacade._build_vm` (`qsnap/config/facade.py`), parse the six options from `vm_raw` with global fallback: `compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout` inherit from `global_cfg.*`; `verify` falls back to `"metadata"` (no global key). Apply the same validation as global/target level (`compression_type ∈ {zstd, zlib}`, `convert_parallel` int 1-8, `backup_stall_timeout` via `parse_stall_timeout()`, `verify ∈ {off, metadata, check, compare}` with the deprecated `"hash"`/`"full"` → `"compare"` mapping + WARNING). Every `ConfigError` message SHALL name the VM
- [x] 3.2 Store the six resolved values in the `VMConfig` construction inside `_build_vm`
- [x] 3.3 Rewire `_build_target` invocation (`facade.py:491-495`): pass the VM-resolved values instead of `global_cfg.*`; rename `_build_target` signature parameters `global_compress` → `vm_compress`, `global_compression_type` → `vm_compression_type`, `global_backup_stall_timeout` → `vm_backup_stall_timeout`, `global_convert_parallel` → `vm_convert_parallel`, `global_convert_out_of_order` → `vm_convert_out_of_order`; add a `vm_verify: str` fallback parameter and resolve `verify` as `tgt_raw.get("verify", vm_verify)` (keep the deprecated-value mapping at target level unchanged)
- [x] 3.4 Verify: `poetry run pytest tests/config/ -m "not integration"` — inheritance behavior changes are expected to be caught by new tests in section 7

## 4. Unknown-key rejection (design D3, specs/config-parsing "Unknown config key rejection")

- [x] 4.1 Define module-level `frozenset` whitelists in `qsnap/config/facade.py`: `_GLOBAL_KEYS`, `_VM_KEYS`, `_DISK_KEYS`, `_TARGET_KEYS`, enumerating every key parsed at each level plus structural keys (`vm` at top level; `disk`, `target` inside `[[vm]]`)
- [x] 4.2 Include ALL deprecated-but-tolerated keys in the whitelists so they keep warn-and-ignore behavior: global `snapshot_preserve`, `target_preserve`, `target_preserve_min`, `preserve_day_of_week`, `rate_limit`, `full_every` (if global-level), and target-level `incremental`, `incremental_mode`, `rate_limit`, `copy_base`, `full_every`, `full_compress` (required by existing fixtures — see test-plan.md Section 4)
- [x] 4.3 After building each table, compute `unknown = set(raw) - whitelist` and raise `ConfigError` naming the table (with VM name / target path context) and each offending key; run validation AFTER `[global]` section unwrapping (`facade.py:68-72`)
- [x] 4.4 Add the cross-level hint: when an unknown key is recognized at another level, append a hint to the error message (e.g. target-only key found in `[[vm]]` → "did you mean to set it in [[vm.target]]?"; global-level `verify` → point to `[[vm]]`/`[[vm.target]]`)
- [x] 4.5 Verify: `poetry run pytest tests/config/ -m "not integration"` and confirm every fixture in `tests/fixtures/configs/` still parses

## 5. Test suite refactoring — deletions & fixture cleanup (test-plan.md Sections 3 & 5)

- [x] 5.1 Delete the five obsolete tests from test-plan.md Section 5: `tests/config/test_parser.py::test_parse_target_compress`, `tests/config/test_parser.py::test_full_every_deprecation_warning`, `tests/config/test_facade.py::test_target_compress_parsed`, `tests/config/test_facade.py::test_vm_chain_length_overrides_global`, `tests/config/test_facade.py::test_target_chain_length_overrides_vm` (each is fully subsumed by a surviving canonical test — reasons documented in test-plan.md Section 5)
- [x] 5.2 Fix `tests/fixtures/configs/full_backup.toml`: remove `full_verify_after_create = "compare"` from the `[[vm.target]]` block (global-only key; would fail the strict whitelist)
- [x] 5.3 Verify: `poetry run pytest tests/config/ -m "not integration"` still green after deletions

## 6. Documentation corrections (design D5)

- [x] 6.1 `qsnap.toml.example`: fix inheritance comments (~lines 81, 224, 235) to state the real global → VM → target chain; add commented VM-level examples for all six options under a `[[vm]]` block; note the `free_space_factor` estimate caveat when switching compression algorithms
- [x] 6.2 `qsnap/modules/backup/bitmap.py`: fix stale docstrings at ~line 21 and ~lines 774-776 claiming `-m 4`/`-W` are always included (they are parameterized)
- [x] 6.3 `README.md`: add the six options to the VM Keys table (~lines 166-180)
- [x] 6.4 Verify `qsnap.toml.example` still parses (existing `test_example_config_parseable` guard)

## 7. Testing — delegate groups from test-plan.md

MANDATORY DELEGATION RULE: the lead programmer agent orchestrating this section MUST pass the testing paradigm document `TESTING.md` (repo root) to EVERY @Mr.Tester subagent it delegates to, together with the group's scope and scenario list from test-plan.md. No tester may start without having received `TESTING.md`. Each tester writes or fixes ONLY the tests of its own group and reports source bugs instead of fixing them. Launch all groups IN PARALLEL (single message), respecting the implementation order noted in test-plan.md ("Implementation order for parallel delegation").

- [x] 7.1 Read `test-plan.md` Delegation Groups section (G1–G6) and Coverage Map
- [x] 7.2 Delegate group `config-parsing-unit` (G1; scope: `tests/config/test_parser.py`, `tests/config/test_resolver.py`) to @Mr.Tester — hand over `TESTING.md` + G1 scenario list — **16 new tests, all passing**
- [x] 7.3 Delegate group `config-unknown-keys-unit` (G2; scope: NEW `tests/config/test_unknown_keys.py`) to @Mr.Tester — hand over `TESTING.md` + G2 scenario list — **8 test functions (30 cases), all passing**
- [x] 7.4 Delegate group `config-model-unit` (G3; scope: `tests/config/test_model.py`) to @Mr.Tester — hand over `TESTING.md` + G3 scenario list — **3 new tests, 92 total passing**
- [x] 7.5 Delegate group `config-fixtures-unit` (G4; scope: `tests/config/test_fixtures.py`, NEW `tests/fixtures/configs/vm_engine_options.toml`) to @Mr.Tester — hand over `TESTING.md` + G4 scenario list — **3 new tests, new fixture, all passing**
- [x] 7.6 Delegate group `facade-resolution-unit` (G5; scope: `tests/config/test_facade.py`) to @Mr.Tester — hand over `TESTING.md` + G5 scenario list — **1 new test, passing**
- [x] 7.7 Delegate group `integration-vm-options` (G6; scope: `tests/integration/test_full_backup.py`, `tests/integration/test_incremental_backup.py`) to @Mr.Tester — hand over `TESTING.md` + G6 scenario list + test-plan.md Section 6 amendments — **2 new tests + 11 existing, 13 passing**
- [x] 7.8 Review @Mr.Tester reports and fix any source-level bugs discovered (source fixes belong to the programmer agent, never to testers) — **no source bugs found**
- [x] 7.9 Re-delegate any groups affected by source fixes (again with `TESTING.md` attached) — **not needed**
- [x] 7.10 Verify all groups pass and coverage matches `test-plan.md`: `poetry run pytest tests/config/ -m "not integration and not stress and not e2e"` (232 passed) and `poetry run pytest tests/integration/test_full_backup.py tests/integration/test_incremental_backup.py -m integration` (13 passed)

## 8. Final verification

- [x] 8.1 Full fast suite green: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — **1661 passed, 1 xpassed**
- [x] 8.2 Lint & types clean: `poetry run ruff check qsnap/ tests/` (pre-existing warnings only), `poetry run ruff format --check qsnap/ tests/` (all formatted), `poetry run pyright` (pre-existing errors only)
- [x] 8.3 Scenario traceability check: every scenario in `specs/config-parsing/spec.md` and `specs/config-model/spec.md` of this change maps to at least one passing test (per test-plan.md Coverage Map) — **59/59 scenarios covered by G1-G6**
- [x] 8.4 Validate the change: `openspec validate vm-level-backup-engine-options` — **valid**
