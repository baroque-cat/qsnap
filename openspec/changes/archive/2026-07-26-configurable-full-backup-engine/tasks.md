## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b configurable-full-backup-engine`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. Config Model — New Fields

Reference: `specs/config-model/spec.md`, `design.md` D1-D5

- [x] 2.1 Add `full_transfer_engine: str = "qemu-img-convert"` field to `GlobalConfig` dataclass in `qsnap/models/config.py` (after `compression_type` field, line ~74)
- [x] 2.2 Add `convert_parallel: int = 4` field to `GlobalConfig` (after `full_transfer_engine`)
- [x] 2.3 Add `convert_out_of_order: bool = True` field to `GlobalConfig` (after `convert_parallel`)
- [x] 2.4 Add the same three fields to `TargetConfig` dataclass (after `compression_type` field, line ~149)
- [x] 2.5 Verify both dataclasses remain `@dataclass(frozen=True)` — immutability is mandatory

## 3. Config Facade — Parsing, Validation, Inheritance

Reference: `specs/config-model/spec.md`, `design.md` D1

- [x] 3.1 Add parsing for `full_transfer_engine` in `ConfigFacade._parse()` (after `compression_type` parsing, ~line 107): validate value is `"qemu-img-convert"` or `"libnbd"`, raise `ConfigError` on invalid value
- [x] 3.2 Add parsing for `convert_parallel` in `_parse()`: validate integer in range 1-8, raise `ConfigError` on out-of-range
- [x] 3.3 Add parsing for `convert_out_of_order` in `_parse()`: parse as bool
- [x] 3.4 Pass `global_cfg.full_transfer_engine`, `global_cfg.convert_parallel`, `global_cfg.convert_out_of_order` from `_build_vm()` to `_build_target()` (after line 323, alongside existing `global_cfg.compress` etc.)
- [x] 3.5 Resolve `full_transfer_engine` in `_build_target()`: `tgt_raw.get("full_transfer_engine", global_full_transfer_engine)` (same pattern as `compression_type` at line 451)
- [x] 3.6 Resolve `convert_parallel` in `_build_target()`: `tgt_raw.get("convert_parallel", global_convert_parallel)`
- [x] 3.7 Resolve `convert_out_of_order` in `_build_target()`: `tgt_raw.get("convert_out_of_order", global_convert_out_of_order)`
- [x] 3.8 Add the three new fields to the `TargetConfig(...)` constructor call in `_build_target()` (lines 514-526)

## 4. IBackupProvider Interface — New Parameters

Reference: `specs/backup-provider/spec.md`, `design.md` D1

- [x] 4.1 Add `full_transfer_engine: str = "qemu-img-convert"`, `convert_parallel: int = 4`, `convert_out_of_order: bool = True` keyword parameters to `IBackupProvider.create_full_backup()` in `qsnap/interfaces/backup.py` (line 46)
- [x] 4.2 Add the same three keyword parameters to `IBackupProvider.transfer_missing()` (line 15)
- [x] 4.3 Update the default `create_full_backup()` implementation (which raises `NotImplementedError`) to accept the new parameters

## 5. BitmapBackupProvider — Engine Selection and Configurable Flags

Reference: `specs/qemu-img-convert-full-backup/spec.md`, `specs/nbd-bitmap-backup/spec.md`, `design.md` D2-D6

- [x] 5.1 Add `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` parameters to `BitmapBackupProvider.create_full_backup()` signature (lines 1103-1288)
- [x] 5.2 Add the same three parameters to `BitmapBackupProvider.transfer_missing()` signature (lines 201-520)
- [x] 5.3 Add `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` parameters to `_full_pull_lifecycle()` signature (lines 614-628)
- [x] 5.4 Add engine-selection branch in `_full_pull_lifecycle()`: when `full_transfer_engine == "qemu-img-convert"`, call `_qemu_img_convert_transfer()` (current path); when `full_transfer_engine == "libnbd"`, call new `_full_transfer_via_libnbd()` method
- [x] 5.5 Add `parallel` and `out_of_order` parameters to `_qemu_img_convert_transfer()` signature (lines 524-612)
- [x] 5.6 Replace hardcoded `cmd.extend(['-m', '4', '-W', '-p'])` (line 580) with: `cmd.extend(['-m', str(parallel)])`, conditional `if out_of_order: cmd.append('-W')`, always `cmd.append('-p')`
- [x] 5.7 Pass `parallel=convert_parallel` and `out_of_order=convert_out_of_order` from `_full_pull_lifecycle()` to `_qemu_img_convert_transfer()`
- [x] 5.8 Create new `_full_transfer_via_libnbd()` method: (1) get virtual size via `INbdClient.get_size()` (running VM) or `_query_virtual_size()` (stopped VM), (2) create empty qcow2 via `qemu-img create -f qcow2 [-o compression_type=<type>] <tmp_file> <virtual_size>`, (3) call `_start_write_server(target_file=tmp_file, write_socket=<socket>, pid_file=<pid>, compress=compress)`, (4) call `_transfer(socket_path=<source>, write_socket=<write_socket>, disk_target=<disk>, meta_contexts=["base:allocation"], zero_skip=True, compress=compress, compression_type=compression_type, stall_timeout=stall_timeout)`, (5) return `(transfer_error, bytes_transferred)`
- [x] 5.9 Pass `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` from `create_full_backup()` to `_full_pull_lifecycle()` in both call sites (running VM at ~line 1200, stopped VM at ~line 1248)
- [x] 5.10 Pass the same three parameters from `transfer_missing()` FULL branch (line 334) to `_full_pull_lifecycle()`

## 6. Core Orchestrator — Pass-Through

Reference: `specs/backup-provider/spec.md`, `design.md` D1

- [x] 6.1 Pass `full_transfer_engine=target.full_transfer_engine`, `convert_parallel=target.convert_parallel`, `convert_out_of_order=target.convert_out_of_order` to `provider.create_full_backup()` call in `Core._backup_target()` (lines 2854-2862)
- [x] 6.2 Pass the same three fields to `provider.transfer_missing()` call in `_transfer_with_retry()` (lines 2698-2704)

## 7. Config Example — Documentation

Reference: `qsnap.toml.example`

- [x] 7.1 Add documentation for `full_transfer_engine`, `convert_parallel`, `convert_out_of_order` in the target section of `qsnap.toml.example` (after `compression_type` documentation, ~line 180)

## 8. Testing

**CRITICAL TEST ORCHESTRATION PROTOCOL:**

The implementing agent (Mr. Programmer) MUST delegate ALL test work to specialized @Mr.Tester subagents. The implementing agent MUST NOT write tests directly.

**MANDATORY:** Before delegating any test work, the implementing agent MUST read `/home/openuser/vm/qsnap/TESTING.md` and pass its contents to EVERY @Mr.Tester subagent. TESTING.md defines the project's test architecture, categories, mock strategy, and rules. Every tester MUST follow this paradigm.

**MANDATORY:** Each @Mr.Tester subagent MUST be given:
1. The TESTING.md document (test architecture, categories, and rules)
2. The group's scope (file paths from test-plan.md)
3. The group's scenario list from the Coverage Map in `test-plan.md`
4. Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
5. Instruction: "Search for and DELETE any old tests related to rsync, FileCopy, file_copy, rate_limit, nbd_full_export, or copy_base — these are remnants of removed backup strategies. A grep has confirmed zero such references currently exist, but verify this remains true."
6. Instruction: "Write NEW tests for all new functionality. For integration tests, the environment has FULL access to libvirt and qemu — write REAL integration tests with actual VMs, not just mocks."
7. Instruction: "Follow the mock strategy from TESTING.md: custom mock classes implementing ABCs (MockShell with .expect().returns(), MockVMModuleFactory, InMemoryStateManager, MockConfigFacade). NO pytest-mock — use unittest.mock.patch only for spying and datetime freezing."

Reference: `test-plan.md` Delegation Groups section. Launch ALL @Mr.Tester subagents IN PARALLEL (single message, multiple tool calls).

- [x] 8.1 Read `test-plan.md` Delegation Groups section
- [x] 8.2 Delegate group `config-unit` to @Mr.Tester (scope: `tests/config/test_model.py`, `tests/config/test_parser.py`, `tests/config/test_resolver.py` — 19 scenarios, all NEW)
- [x] 8.3 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py`, `tests/modules/backup/test_bitmap_incremental.py` — 33 scenarios, NEW + MODIFY)
- [x] 8.4 Delegate group `mocks-unit` to @Mr.Tester (scope: `tests/mocks/mock_modules.py`, `tests/mocks/test_mock_validity.py` — 5 scenarios, MODIFY + NEW)
- [x] 8.5 Delegate group `contracts` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py` — 6 scenarios, MODIFY)
- [x] 8.6 Delegate group `core-unit` to @Mr.Tester (scope: `tests/core/test_pipeline.py` — 6 scenarios, NEW)
- [x] 8.7 Delegate group `conftest` to @Mr.Tester (scope: `tests/conftest.py`, `tests/config/test_fixtures.py` — 7 scenarios, MODIFY + NEW)
- [x] 8.8 Delegate group `config-fixtures` to @Mr.Tester (scope: `tests/fixtures/configs/engine_config.toml`, `tests/config/test_fixtures.py` — 4 scenarios, NEW)
- [x] 8.9 Delegate group `integration` to @Mr.Tester (scope: `tests/integration/test_full_backup.py` — 3 scenarios, MODIFY — REAL integration tests with libvirt/qemu access)
- [x] 8.10 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 8.11 Re-delegate any groups affected by source fixes
- [x] 8.12 Verify all groups pass and coverage matches `test-plan.md`: `poetry run pytest tests/ -m "not integration and not stress and not e2e"` then `poetry run pytest tests/integration/ -m integration`

## 9. Linting & Type Checking

- [x] 9.1 Run ruff linter: `poetry run ruff check qsnap/ tests/`
- [x] 9.2 Run ruff formatter: `poetry run ruff format qsnap/ tests/`
- [x] 9.3 Run pyright type checker: `poetry run pyright qsnap/`
- [x] 9.4 Fix any linting or type errors

## 10. Final Verification

- [x] 10.1 Run full test suite (unit + mock + contract): `poetry run pytest tests/ -m "not integration and not stress and not e2e"`
- [x] 10.2 Run integration tests: `poetry run pytest tests/integration/ -m integration`
- [x] 10.3 Verify `qsnap.toml.example` is valid: parse it with ConfigFacade
