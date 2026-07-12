# QA Strategy & Test Plan

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `config-model` | GlobalConfig dataclass | GlobalConfig is immutable | `tests/config/test_model.py` | `test_global_config_immutable` | config-model-and-results |
| `config-model` | GlobalConfig dataclass | GlobalConfig default values | `tests/config/test_model.py` | `test_global_config_defaults` | config-model-and-results |
| `config-model` | VMConfig dataclass | VMConfig with required fields | `tests/config/test_model.py` | `test_vm_config_required_fields` | config-model-and-results |
| `config-model` | VMConfig dataclass | VMConfig with targets | `tests/config/test_model.py` | `test_vm_config_with_targets` | config-model-and-results |
| `config-model` | TargetConfig dataclass | TargetConfig with incremental enabled | `tests/config/test_model.py` | `test_target_config_incremental` | config-model-and-results |
| `config-model` | RetentionPolicy dataclass | RetentionPolicy with hourly and daily limits | `tests/config/test_model.py` | `test_retention_policy_hourly_daily` | config-model-and-results |
| `config-model` | RetentionPolicy dataclass | RetentionPolicy defaults | `tests/config/test_model.py` | `test_retention_policy_defaults` | config-model-and-results |
| `result-types` | SnapshotResult dataclass | Successful snapshot result | `tests/models/test_results.py` | `test_snapshot_result_success` | config-model-and-results |
| `result-types` | SnapshotResult dataclass | Failed snapshot result | `tests/models/test_results.py` | `test_snapshot_result_failure` | config-model-and-results |
| `result-types` | BackupResult dataclass | Successful backup transfer | `tests/models/test_results.py` | `test_backup_result_success` | config-model-and-results |
| `result-types` | CommitResult dataclass | Successful blockcommit | `tests/models/test_results.py` | `test_commit_result_success` | config-model-and-results |
| `result-types` | RetentionResult dataclass | Retention policy keeps some, removes others | `tests/models/test_results.py` | `test_retention_result_keep_remove` | config-model-and-results |
| `result-types` | ShellResult dataclass | Successful shell command | `tests/models/test_results.py` | `test_shell_result_success` | config-model-and-results |
| `result-types` | ShellResult dataclass | Failed shell command | `tests/models/test_results.py` | `test_shell_result_failure` | config-model-and-results |
| `result-types` | ChangeResult dataclass | VM disk has grown | `tests/models/test_results.py` | `test_change_result_disk_grown` | config-model-and-results |
| `config-parsing` | IConfigFacade ABC | ConfigFacade implements IConfigFacade | `tests/interfaces/test_config.py` | `test_config_facade_is_iconfigfacade` | factory-and-interfaces |
| `config-parsing` | TOML file parsing | Minimal valid config | `tests/config/test_parser.py` | `test_parse_minimal_valid_config` | config-parsing |
| `config-parsing` | TOML file parsing | Missing required VM field | `tests/config/test_parser.py` | `test_parse_missing_required_field_raises` | config-parsing |
| `config-parsing` | TOML file parsing | Invalid TOML syntax | `tests/config/test_parser.py` | `test_parse_invalid_toml_raises` | config-parsing |
| `config-parsing` | Option inheritance from global to per-VM to per-target | VM overrides global retention policy | `tests/config/test_resolver.py` | `test_vm_overrides_global_retention` | config-parsing |
| `config-parsing` | Option inheritance from global to per-VM to per-target | Target inherits VM retention when not overridden | `tests/config/test_resolver.py` | `test_target_inherits_vm_retention` | config-parsing |
| `config-parsing` | Option inheritance from global to per-VM to per-target | Target overrides VM retention | `tests/config/test_resolver.py` | `test_target_overrides_vm_retention` | config-parsing |
| `config-parsing` | Multiple VMs from a single config | Config with two VMs | `tests/config/test_facade.py` | `test_facade_multiple_vms` | config-parsing |
| `config-parsing` | VM lookup by name | Lookup existing VM | `tests/config/test_facade.py` | `test_facade_get_vm_existing` | config-parsing |
| `config-parsing` | VM lookup by name | Lookup non-existent VM | `tests/config/test_facade.py` | `test_facade_get_vm_nonexistent_raises` | config-parsing |
| `shell-abstraction` | IShell ABC | IShell is an ABC | `tests/interfaces/test_shell.py` | `test_ishell_is_abstract` | factory-and-interfaces |
| `shell-abstraction` | SubprocessShell implements IShell | Successful command execution | `tests/utils/test_shell.py` | `test_subprocess_shell_success` | shell-and-state |
| `shell-abstraction` | SubprocessShell implements IShell | Command timeout | `tests/utils/test_shell.py` | `test_subprocess_shell_timeout` | shell-and-state |
| `shell-abstraction` | SubprocessShell implements IShell | Command not found | `tests/utils/test_shell.py` | `test_subprocess_shell_command_not_found` | shell-and-state |
| `shell-abstraction` | Structured logging of shell commands | Command is logged | `tests/utils/test_shell.py` | `test_subprocess_shell_logs_command` | shell-and-state |
| `state-management` | IStateManager ABC | IStateManager is an ABC | `tests/interfaces/test_state_manager.py` | `test_istate_manager_is_abstract` | factory-and-interfaces |
| `state-management` | JsonStateManager implements IStateManager | Write and read allocation size | `tests/state/test_manager.py` | `test_write_read_allocation` | shell-and-state |
| `state-management` | JsonStateManager implements IStateManager | Missing state file returns None | `tests/state/test_manager.py` | `test_missing_state_returns_none` | shell-and-state |
| `state-management` | JsonStateManager implements IStateManager | Record and list snapshots | `tests/state/test_manager.py` | `test_record_and_list_snapshots` | shell-and-state |
| `state-management` | Atomic file writes | Atomic write | `tests/state/test_manager.py` | `test_atomic_write_pattern` | shell-and-state |
| `retention-engine` | IRetentionEngine ABC | IRetentionEngine is a standalone ABC | `tests/interfaces/test_retention_engine.py` | `test_iretention_engine_standalone_no_core` | factory-and-interfaces |
| `retention-engine` | TimeBasedRetention implements IRetentionEngine | Hourly retention with 24h policy | `tests/modules/retention/test_time_based.py` | `test_hourly_retention_24h` | retention-engine |
| `retention-engine` | TimeBasedRetention implements IRetentionEngine | preserve_min keeps all recent items | `tests/modules/retention/test_time_based.py` | `test_preserve_min_keeps_recent` | retention-engine |
| `retention-engine` | TimeBasedRetention implements IRetentionEngine | Daily retention identifies first snapshot of each day | `tests/modules/retention/test_time_based.py` | `test_daily_retention_first_per_day` | retention-engine |
| `retention-engine` | TimeBasedRetention implements IRetentionEngine | preserve_min "all" keeps everything | `tests/modules/retention/test_time_based.py` | `test_preserve_min_all_keeps_everything` | retention-engine |
| `retention-engine` | Retention engine is deterministic | Deterministic output | `tests/modules/retention/test_time_based.py` | `test_evaluate_is_deterministic` | retention-engine |
| `core-orchestrator` | Core initialization with dependency injection | Core receives all dependencies at construction | `tests/core/test_engine.py` | `test_core_init_stores_dependencies` | core-orchestrator |
| `core-orchestrator` | Core.run() executes the full pipeline | run with all VMs | `tests/core/test_engine.py` | `test_core_run_all_vms` | core-orchestrator |
| `core-orchestrator` | Core.run() executes the full pipeline | run with filter matching one VM | `tests/core/test_engine.py` | `test_core_run_with_filter` | core-orchestrator |
| `core-orchestrator` | Pipeline step order | Pipeline with always mode | `tests/core/test_pipeline.py` | `test_pipeline_always_mode_creates_snapshot` | core-orchestrator |
| `core-orchestrator` | Pipeline step order | Pipeline with onchange mode, no changes | `tests/core/test_pipeline.py` | `test_pipeline_onchange_no_changes_skips_snapshot` | core-orchestrator |
| `core-orchestrator` | Error isolation between VMs | One VM fails, others succeed | `tests/core/test_pipeline.py` | `test_error_isolation_between_vms` | core-orchestrator |
| `core-orchestrator` | Core.snapshot() runs only snapshot steps | snapshot command skips backup | `tests/core/test_pipeline.py` | `test_snapshot_command_skips_backup` | core-orchestrator |
| `core-orchestrator` | Core.backup() runs only backup steps | (no explicit scenario — requirement coverage) | `tests/core/test_pipeline.py` | `test_backup_command_skips_snapshot` | core-orchestrator |
| `core-orchestrator` | Core.prune() runs only retention steps | (no explicit scenario — requirement coverage) | `tests/core/test_pipeline.py` | `test_prune_command_only_retention` | core-orchestrator |
| `core-orchestrator` | Dry-run mode | Dry-run logs planned actions | `tests/core/test_pipeline.py` | `test_dry_run_logs_no_mutation` | core-orchestrator |
| `module-factory` | IVMModuleFactory ABC | IVMModuleFactory defines all creation methods | `tests/interfaces/test_factory.py` | `test_ivmmodulefactory_defines_all_creation_methods` | factory-and-interfaces |
| `module-factory` | Factory returns ABC interface types | Factory method return type contract | `tests/mocks/test_mock_factory.py` | `test_mock_factory_returns_interface_types` | factory-and-interfaces |
| `module-factory` | DefaultFactory receives IShell and IStateManager | DefaultFactory holds shell and state references | `tests/factory/test_default.py` | `test_default_factory_stores_shell_and_state` | factory-and-interfaces |
| `module-factory` | Unimplemented factory methods raise NotImplementedError | Calling create_lifecycle_manager before it exists | `tests/factory/test_default.py` | `test_default_factory_unimplemented_raises_notimplementederror` | factory-and-interfaces |
| `mock-verification` | MockShell implements IShell | MockShell passes isinstance check | `tests/mocks/test_mock_shell.py` | `test_mock_shell_is_ishell` | factory-and-interfaces |
| `mock-verification` | InMemoryStateManager implements IStateManager | InMemoryStateManager passes isinstance check | `tests/mocks/test_mock_state.py` | `test_inmemory_state_is_istatemanager` | factory-and-interfaces |
| `mock-verification` | MockConfigFacade implements IConfigFacade | MockConfigFacade passes isinstance check | `tests/mocks/test_mock_config.py` | `test_mock_config_is_iconfigfacade` | factory-and-interfaces |

## Delegation Groups

### Group: config-model-and-results
**Scope:** `tests/config/test_model.py`, `tests/models/test_results.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_model.py` | 7 | `NEW` |
| `tests/models/test_results.py` | 8 | `NEW` |

### Group: shell-and-state
**Scope:** `tests/utils/test_shell.py`, `tests/state/test_manager.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/utils/test_shell.py` | 4 | `NEW` |
| `tests/state/test_manager.py` | 4 | `NEW` |

### Group: config-parsing
**Scope:** `tests/config/test_parser.py`, `tests/config/test_resolver.py`, `tests/config/test_facade.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/config/test_parser.py` | 3 | `NEW` |
| `tests/config/test_resolver.py` | 3 | `NEW` |
| `tests/config/test_facade.py` | 3 | `NEW` |

### Group: retention-engine
**Scope:** `tests/modules/retention/test_time_based.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/modules/retention/test_time_based.py` | 5 | `NEW` |

### Group: core-orchestrator
**Scope:** `tests/core/test_engine.py`, `tests/core/test_pipeline.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/core/test_engine.py` | 3 | `NEW` |
| `tests/core/test_pipeline.py` | 7 | `NEW` |

### Group: factory-and-interfaces
**Scope:** `tests/factory/test_default.py`, `tests/interfaces/test_config.py`, `tests/interfaces/test_shell.py`, `tests/interfaces/test_state_manager.py`, `tests/interfaces/test_retention_engine.py`, `tests/interfaces/test_factory.py`, `tests/mocks/test_mock_factory.py`, `tests/mocks/test_mock_shell.py`, `tests/mocks/test_mock_state.py`, `tests/mocks/test_mock_config.py`

| Test File | Scenarios | Action |
|---|---|---|
| `tests/factory/test_default.py` | 2 | `NEW` |
| `tests/interfaces/test_config.py` | 1 | `NEW` |
| `tests/interfaces/test_shell.py` | 1 | `NEW` |
| `tests/interfaces/test_state_manager.py` | 1 | `NEW` |
| `tests/interfaces/test_retention_engine.py` | 1 | `NEW` |
| `tests/interfaces/test_factory.py` | 1 | `NEW` |
| `tests/mocks/test_mock_factory.py` | 1 | `NEW` |
| `tests/mocks/test_mock_shell.py` | 1 | `NEW` |
| `tests/mocks/test_mock_state.py` | 1 | `NEW` |
| `tests/mocks/test_mock_config.py` | 1 | `NEW` |

## Test Modifications

All tests are new; no existing tests to modify. This is the initial foundation change — the project has zero existing code. Every test file listed in the Delegation Groups section is created from scratch.

## Risks & Edge Cases

- **[Risk]** Over-engineering the ABC layer before any module exists → Only test defined method signatures; verify each ABC has exactly the abstract methods specified in its spec, no more. Contract tests in `tests/interfaces/` parametrize over concrete implementations and assert method existence + return type, not speculative future methods.
- **[Risk]** ConfigFacade to Core tight coupling — if Core depends on ConfigFacade's specific dataclass shapes, any config model change breaks Core → Core tests in `tests/core/` must inject `MockConfigFacade` (implementing `IConfigFacade`), never `ConfigFacade` directly. Add an assertion in `test_core_init_stores_dependencies` that the config dependency is used only via `IConfigFacade` methods (`get_global`, `get_vms`, `get_vm`).
- **[Risk]** TOML inheritance resolution is hand-rolled — merging global defaults with per-VM and per-target overrides has edge cases (None vs missing key, list merge vs replace) → Create a dedicated `tests/fixtures/configs/inheritance.conf` fixture covering: global-only, VM-override, target-override, and target-inherits-VM. Add explicit tests in `test_resolver.py` for None vs missing key distinction (a key explicitly set to `None` vs a key absent from the TOML section).
- **[Risk]** Frozen dataclasses with mutable sub-fields — `VMConfig.targets` is `list[TargetConfig]`; the list itself is not frozen, only the `VMConfig` object is → Add a test in `test_model.py` that verifies `VMConfig.targets` returns the list and that appending to it does NOT mutate the original `VMConfig` instance's internal list (defensive copy on construction). Document this trade-off in the test docstring.
- **[Risk]** JsonStateManager file corruption on crash mid-write → The `test_atomic_write_pattern` test must verify: (1) a `.tmp` file is created during write, (2) the final state file appears atomically via `os.rename`, (3) no partial JSON is ever observable by a concurrent reader. Simulate a crash by mocking `os.rename` to raise mid-operation and asserting the target file is unchanged.
- **[Risk]** Unimplemented factory methods raise NotImplementedError with unclear messages → The `test_default_factory_unimplemented_raises_notimplementederror` test must call every `create_*` method on `DefaultFactory` (parametrized) and assert that `NotImplementedError` is raised with a message containing the module name. This guards against silent stubs returning `None`.
- **[Risk]** Retention engine determinism under boundary conditions (items at exact hour/day boundaries, empty item list, single item) → Add boundary tests in `test_time_based.py`: empty list returns empty keep/remove, single item is always kept, items exactly at midnight are assigned to the correct day bucket.
- **[Risk]** Core pipeline error isolation swallows errors silently → The `test_error_isolation_between_vms` test must assert that the error for the failing VM is captured in a result object or logged (not silently swallowed), and that the return value of `core.run()` includes per-VM status indicating which succeeded and which failed.
- **[Risk]** Dry-run mode still mutates state via IStateManager → The `test_dry_run_logs_no_mutation` test must assert that `IStateManager.set_last_allocation` and `record_snapshot` are never called on the mock state manager when dry-run is active, and that `IShell.run` is never called with mutating virsh/qemu-img commands.
