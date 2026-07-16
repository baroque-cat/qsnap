## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b feat/init-foundation`
- [x] 1.2 Initialize poetry project: `poetry init --name qsnap --description "QEMU/KVM snapshot and backup orchestration tool"` with Python >= 3.11
- [x] 1.3 Configure pyright (strict mode), ruff, black in `pyproject.toml`
- [x] 1.4 Create package directory structure: `qsnap/` with subdirs `models/`, `interfaces/`, `config/`, `shell/`, `state/`, `core/`, `factory/`, `retention/` — each with `__init__.py`
- [x] 1.5 Create `qsnap/py.typed` (PEP 561 marker)

## 2. Tier 0 — Data Layer (Result Types + Config Dataclasses)

- [x] 2.1 Implement `qsnap/models/results.py` — all immutable result dataclasses: `SnapshotResult`, `BackupResult`, `CommitResult`, `RetentionResult`, `ShellResult`, `ChangeResult`. All `@dataclass(frozen=True)`. Fields: `success: bool`, error: str | None, plus type-specific fields per spec `result-types`.
- [x] 2.2 Implement `qsnap/models/config.py` — immutable config dataclasses: `GlobalConfig`, `VMConfig`, `TargetConfig`, `RetentionPolicy`. All `@dataclass(frozen=True)`. VMConfig.targets uses defensive copy on construction. Refer to spec `config-model` for required and optional fields.
- [x] 2.3 Create `qsnap/models/__init__.py` re-exporting all public symbols from results and config.

## 3. Tier 1 — ABC Interfaces

- [x] 3.1 Implement `qsnap/interfaces/shell.py` — `IShell(ABC)` with `run(cmd: list[str], timeout: int) → ShellResult`
- [x] 3.2 Implement `qsnap/interfaces/state.py` — `IStateManager(ABC)` with `get_last_allocation(vm_name)`, `set_last_allocation(vm_name, alloc)`, `record_snapshot(vm_name, info)`, `get_snapshots(vm_name)`
- [x] 3.3 Implement `qsnap/interfaces/config.py` — `IConfigFacade(ABC)` with `get_global() → GlobalConfig`, `get_vms() → list[VMConfig]`, `get_vm(name) → VMConfig`
- [x] 3.4 Implement `qsnap/interfaces/snapshot.py` — `ISnapshotProvider(ABC)` with `create(vm_config, snapshot_name, disk, snapshot_path)`, `list(vm_config)`, `delete(snapshot)`
- [x] 3.5 Implement `qsnap/interfaces/backup.py` — `IBackupProvider(ABC)` with `transfer_missing(vm_config, target, snapshots)`, `list(target)`, `delete(backup)`
- [x] 3.6 Implement `qsnap/interfaces/retention.py` — `IRetentionEngine(ABC)` with `evaluate(items: list, policy: RetentionPolicy, now: datetime) → RetentionResult`. NOTE: does NOT inherit from Core — pure function.
- [x] 3.7 Implement `qsnap/interfaces/change.py` — `IChangeDetector(ABC)` with `has_changed(vm_config) → ChangeResult`
- [x] 3.8 Implement `qsnap/interfaces/lifecycle.py` — `ILifecycleManager(ABC)` with `blockcommit(vm_config, snapshots_to_merge) → CommitResult`
- [x] 3.9 Implement `qsnap/interfaces/factory.py` — `IVMModuleFactory(ABC)` with `create_snapshot_provider(vm_config)`, `create_backup_provider(vm_config, target)`, `create_retention_engine(policy)`, `create_change_detector(mode)`, `create_lifecycle_manager()`
- [x] 3.10 Create `qsnap/interfaces/__init__.py` re-exporting all ABCs

## 4. Tier 2 — Concrete Implementations

- [x] 4.1 Implement `qsnap/shell/subprocess_shell.py` — `SubprocessShell(IShell)`. Wraps `subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)`. Logs every command at DEBUG level (command, timeout, returncode, duration). Returns `ShellResult`. Refer to spec `shell-abstraction`.
- [x] 4.2 Implement `qsnap/state/json_manager.py` — `JsonStateManager(IStateManager)`. Persists per-VM state as JSON in `{state_dir}/{vm_name}.json`. Uses atomic write pattern (write to .tmp, then os.rename). Refer to spec `state-management`.
- [x] 4.3 Implement `qsnap/retention/time_based.py` — `TimeBasedRetention(IRetentionEngine)`. Pure function: no I/O, no Core inheritance, no side effects. Consumes constructor: `__init__(self, policy: RetentionPolicy)`. Implements `evaluate()` with hourly/daily/weekly/monthly/yearly bucketing per btrbk algorithm. Refer to spec `retention-engine`.
- [x] 4.4 Implement `qsnap/config/facade.py` — `ConfigFacade(IConfigFacade)`. Parses TOML with stdlib `tomllib`. Resolves option inheritance: global → per-VM → per-target (VM overrides globals, target overrides VM). Constructs frozen dataclasses. Validates required fields (`name`, `base_image`, `snapshot_dir`). Refer to spec `config-parsing`.

## 5. Tier 3 — Core Orchestrator

- [x] 5.1 Implement `qsnap/core/__init__.py` — `Core` class. Constructor receives `IConfigFacade`, `IVMModuleFactory`, `IStateManager`, `IShell` via DI. Methods:
  - `run(vm_filter=None)` — iterates VMs, calls `_execute_pipeline()` for each
  - `snapshot(vm_filter=None)` — only snapshot steps
  - `backup(vm_filter=None)` — only backup steps
  - `prune(vm_filter=None)` — only retention steps
  - `dry_run = True/False` property — when True, logs actions but no mutation
  - `_execute_pipeline(vm_config)` — executes steps: (1) change detection, (2) snapshot creation, (3) snapshot retention + lifecycle, (4) per-target backup + retention + cleanup. Error in one VM does not prevent others. Refer to spec `core-orchestrator`.

## 6. Tier 5 — Factory + Mocks

- [x] 6.1 Implement `qsnap/factory/default.py` — `DefaultFactory(IVMModuleFactory)`. Constructor receives `IShell`, `IStateManager`. Methods for modules not yet implemented raise `NotImplementedError("ModuleName not yet implemented")`. Retention engine factory method works immediately (TimeBasedRetention is pure, no Core dependency). Refer to spec `module-factory`.
- [x] 6.2 Implement `tests/mocks/mock_shell.py` — `MockShell(IShell)`. Returns preconfigured `ShellResult` objects. Supports `.expect("command pattern").returns(ShellResult(...))` and `.expect("command pattern").raises(exception)`.
- [x] 6.3 Implement `tests/mocks/mock_state.py` — `InMemoryStateManager(IStateManager)`. Stores state in dict. All methods fully functional.
- [x] 6.4 Implement `tests/mocks/mock_config.py` — `MockConfigFacade(IConfigFacade)`. Returns preconfigured `GlobalConfig`, `list[VMConfig]`, and per-VM `VMConfig`.
- [x] 6.5 Implement `tests/mocks/mock_factory.py` — `MockVMModuleFactory(IVMModuleFactory)`. Returns mock instances for all `create_*` methods that satisfy `isinstance(result, ABC)`.
- [x] 6.6 Implement remaining mocks: `MockSnapshotProvider`, `MockBackupProvider`, `MockRetentionEngine`, `MockChangeDetector`, `MockLifecycleManager` — each satisfying their ABC and returning valid result types.
- [x] 6.7 Create `tests/conftest.py` — shared pytest fixtures: `mock_shell`, `mock_state`, `mock_config`, `mock_factory`, `make_vm_config()`, `make_target()`.
- [x] 6.8 Create `tests/fixtures/configs/minimal.toml` — one VM, one target.
- [x] 6.9 Create `tests/fixtures/configs/multi_vm.toml` — two VMs.
- [x] 6.10 Create `tests/fixtures/configs/inheritance.toml` — option cascading (global, VM override, target override, target inherit).
- [x] 6.11 Create `tests/fixtures/configs/invalid.toml` — malformed TOML + missing required fields.
- [x] 6.12 Create `tests/fixtures/timestamps/` — `hourly_set.json`, `daily_set.json`, `mixed_set.json` with fixed datetime sets for retention tests.

## 7. Testing

<!--
  ⚠️ TEST ORCHESTRATION PROTOCOL — READ BEFORE DELEGATING ⚠️

  This section contains the test orchestration plan. The implementing agent MUST:

  1. Read `openspec/changes/init-foundation/test-plan.md` Delegation Groups section (groups listed below)
  2. For EACH group below, launch ONE @Mr.Tester subagent IN PARALLEL (all groups in a single message)
  3. Each @Mr.Tester subagent MUST receive:
     - The group's scope (directory or file list from test-plan.md)
     - The group's scenario list from the Coverage Map
     - **CRITICAL: `/home/openuser/vm/qsnap/TESTING.md` — the project's testing paradigm document.** The tester MUST follow the conventions in TESTING.md: test directory structure, mock-first philosophy, factory injection pattern, zero real I/O for unit tests, fixture file structure, and the rule that every ABC gets a mock with isinstance verification.
     - Instruction: "Write ONLY these specific tests. Report source-level bugs, do NOT fix them yourself."
  4. After all testers return: review their reports, fix any reported source-level bugs
  5. Re-delegate any groups affected by source fixes
  6. Repeat until all groups pass

  The six delegation groups from test-plan.md:
-->

- [x] 7.1 Read `openspec/changes/init-foundation/test-plan.md` Delegation Groups section

- [x] 7.2 Delegate group `config-model-and-results` to @Mr.Tester
  - **Scope:** `tests/config/test_model.py`, `tests/models/test_results.py` (15 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus spec files `config-model` and `result-types`
  - **Instruction:** "Write tests for immutable dataclasses. Verify frozen=True on all. Report source bugs, don't fix them."

- [x] 7.3 Delegate group `shell-and-state` to @Mr.Tester
  - **Scope:** `tests/utils/test_shell.py`, `tests/state/test_manager.py` (8 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus spec files `shell-abstraction` and `state-management`
  - **Instruction:** "Write unit tests for SubprocessShell (real echo/true/false commands, timeout simulation) and JsonStateManager (temp dir, atomic write). Report source bugs, don't fix them."

- [x] 7.4 Delegate group `config-parsing` to @Mr.Tester
  - **Scope:** `tests/config/test_parser.py`, `tests/config/test_resolver.py`, `tests/config/test_facade.py` (9 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus spec `config-parsing`, plus fixture `.toml` files from `tests/fixtures/configs/`
  - **Instruction:** "Write tests for TOML parsing, option inheritance resolution, and ConfigFacade integration. Use fixture .toml files. Report source bugs, don't fix them."

- [x] 7.5 Delegate group `retention-engine` to @Mr.Tester
  - **Scope:** `tests/modules/retention/test_time_based.py` (5 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus spec `retention-engine`, plus fixture JSON files from `tests/fixtures/timestamps/`
  - **Instruction:** "Write pure-function tests for TimeBasedRetention. No mocking needed — these are deterministic unit tests. Report source bugs, don't fix them."

- [x] 7.6 Delegate group `core-orchestrator` to @Mr.Tester
  - **Scope:** `tests/core/test_engine.py`, `tests/core/test_pipeline.py` (10 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus spec `core-orchestrator`
  - **Instruction:** "Write tests for Core orchestration using MockVMModuleFactory. Verify pipeline ordering, error isolation, dry-run, and command separation (snapshot/backup/prune). All dependencies are mocks. Report source bugs, don't fix them."

- [x] 7.7 Delegate group `factory-and-interfaces` to @Mr.Tester
  - **Scope:** `tests/factory/test_default.py`, `tests/interfaces/test_config.py`, `tests/interfaces/test_shell.py`, `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_retention_engine.py`, `tests/interfaces/test_factory.py`, `tests/mocks/test_mock_factory.py`, `tests/mocks/test_mock_shell.py`, `tests/mocks/test_mock_state.py`, `tests/mocks/test_mock_config.py` (13 scenarios)
  - **Pass to @Mr.Tester:** TESTING.md from the project root, plus specs `module-factory`, `shell-abstraction`, `state-management`, `config-parsing`, `retention-engine`
  - **Instruction:** "Write contract tests (isinstance checks), factory creation tests, and mock verification tests. Every mock must pass isinstance against its ABC. Report source bugs, don't fix them."

- [x] 7.8 Review all @Mr.Tester reports. Fix any source-level bugs discovered during testing.

- [x] 7.9 Re-delegate any groups affected by source fixes (repeat until all pass).

- [x] 7.10 Verify: `pytest tests/ -m "not integration and not stress and not e2e"` passes with 100% green. All coverage map scenarios covered.

- [x] 7.11 Verify: `poetry run ruff check .` and `poetry run black --check .` pass with zero issues.
