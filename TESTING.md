# qsnap — TESTING.md

## Philosophy

Tests mirror the production hierarchy. If `modules/snapshot/external.py` exists, its tests live at `tests/modules/snapshot/test_external.py`. Every layer gets its own test suite, and every ABC gets a mock. Tests follow the same paradigm: factory injection, result objects, isolated dependencies.

---

## Directory Structure

```
tests/
├── conftest.py                     # Shared fixtures: mock_shell, mock_factory, etc.
├── __init__.py
│
├── interfaces/                     # Contract tests — verify that implementations obey ABCs
│   ├── test_snapshot_provider.py
│   ├── test_backup_provider.py
│   ├── test_retention_engine.py
│   ├── test_change_detector.py
│   ├── test_state_manager.py
│   ├── test_lifecycle_manager.py
│   ├── test_factory.py
│   ├── test_config.py
│   └── test_shell.py
│
├── config/                         # Unit tests: parser, resolver, facade
│   ├── test_parser.py              # Lexical + syntax of .toml files
│   ├── test_resolver.py            # Option inheritance (global → vm → target)
│   ├── test_model.py               # Immutability of VMConfig, TargetConfig, etc.
│   ├── test_facade.py              # ConfigFacade integration (parser + resolver)
│   └── test_fixtures.py            # Fixture .toml file validation
│
├── core/                           # Unit tests: Core orchestration
│   ├── test_engine.py              # Core.run(), Core._execute_pipeline()
│   ├── test_pipeline.py            # Step ordering, error handling per step
│   ├── test_full_anchor.py         # Count-based FULL backup decision
│   ├── test_full_verification_pipeline.py  # FULL backup verification tiers (M1/M2/M3)
│   ├── test_deferred.py            # Deferred snapshot threshold logic
│   ├── test_fork.py                # Fork/snapshot-creation logic
│   ├── test_retention_policy.py    # Count-based retention policy
│   ├── test_schedule_summary.py    # Schedule summary output
│   ├── test_state_check.py        # State consistency checks
│   ├── test_validation.py          # Config validation
│   └── test_list_commands.py       # List/info CLI commands
│
├── modules/                        # Unit tests: individual domain modules
│   ├── snapshot/
│   │   └── test_external.py        # ExternalSnapshotProvider specifics
│   │
│   ├── backup/
│   │   ├── test_bitmap.py          # BitmapBackupProvider (NBD pull-model)
│   │   ├── test_bitmap_incremental.py  # Bitmap incremental transfer
│   │   └── test_full_verification.py  # verify_full_backup() utility
│   │
│   ├── retention/
│   │   └── test_time_based.py      # Retention logic with fixed timestamps
│   │
│   ├── change/
│   │   ├── test_allocation.py      # AllocationSizeDetector
│   │   └── test_map_detector.py    # Map-based change detector
│   │
│   └── lifecycle/
│       ├── test_blockcommit.py     # BlockCommitManager
│       └── test_qemu_img_commit.py # qemu-img commit wrapper
│
├── factory/                        # Unit tests: DefaultVMModuleFactory
│   └── test_default.py             # Returns correct types for each config variant
│
├── state/                          # Unit tests: StateManager
│   └── test_manager.py             # Read/write allocation, timestamps, JSON format
│
├── cli/                            # Unit tests: CLI dispatch
│   ├── test_commands.py            # Each command maps to correct Core method
│   ├── test_app.py                 # Argument parsing, exit codes
│   ├── test_format.py              # Output formatting
│   ├── test_thin_layer.py         # CLI is thin (no business logic)
│   └── test_tree.py                # Tree view output
│
├── utils/                          # Unit tests: pure-function utilities
│   ├── test_locking.py             # Lockfile acquire/release
│   ├── test_shell.py               # IShell wrappers, timeout handling
│   ├── test_time.py                # Timestamp formatting
│   ├── test_parsing.py             # Rate-limit/timestamp parsing
│   └── test_retry.py               # Retry backoff logic
│
├── mocks/                          # Mock implementations of every ABC
│   ├── __init__.py
│   ├── mock_factory.py             # MockVMModuleFactory — returns all mocks
│   ├── mock_shell.py               # MockShell — predefined command outputs
│   ├── mock_modules.py             # Consolidated domain mocks: MockSnapshotProvider,
│   │                                #   MockBackupProvider, MockBitmapBackupProvider,
│   │                                #   MockRetentionEngine, MockChangeDetector,
│   │                                #   MockLifecycleManager
│   ├── mock_config.py              # MockConfigFacade — in-memory config
│   └── mock_state.py               # InMemoryStateManager
│
├── models/                         # Unit tests: data models
│   └── test_results.py             # Result dataclass immutability/fields
│
├── systemd/                        # Unit tests: systemd integration
│   └── test_units.py               # .service/.timer unit generation
│
├── fixtures/                       # Static test data
│   ├── configs/                    # .toml files for parser tests
│   │   ├── minimal.toml            # One VM, one target
│   │   ├── multi_vm.toml           # Multiple VMs
│   │   ├── inheritance.toml        # Option cascading
│   │   ├── invalid.toml            # Malformed config
│   │   ├── chain_length.toml       # Count-based chain_length retention
│   │   ├── keep_generations.toml    # Count-based keep_generations retention
│   │   ├── deferred_thresholds.toml  # Deferred snapshot thresholds
│   │   ├── safety_fields.toml      # Fault-tolerance safety controls
│   │   ├── global_fields.toml      # Global config defaults
│   │   └── deprecated_fields.toml  # Deprecated field handling
│   ├── shell_outputs/              # Canned virsh/qemu-img output
│   │   ├── domblklist.txt
│   │   ├── snapshot_list.txt
│   │   ├── qemu_img_info.json
│   │   └── backing_chain.txt
│   └── timestamps/                 # Fixed timestamp sets for retention tests
│       ├── count_set.json
│       └── mixed_set.json
│
├── integration/                    # Integration: real virsh/qemu-img on test VMs
│   ├── __init__.py
│   ├── conftest.py                 # Setup/teardown test VM, cleanup snapshots
│   ├── test_nbd_full_backup.py     # NBD pull-model FULL backup
│   └── test_stale_state_recovery.py  # Stale state self-healing
│
├── stress/                         # Stress: large chains, concurrent access
│   ├── __init__.py
│   ├── conftest.py                 # stress_env fixture (disposable VM, 512M disk)
│   ├── test_long_chain.py          # 50+ snapshots, blockcommit tail
│   └── test_concurrent.py          # Lockfile prevents parallel runs
│
└── e2e/                            # End-to-end: from config to restored VM
    ├── __init__.py
    ├── conftest.py                 # e2e_vm fixture (disposable VM + TOML config)
    ├── test_from_config.py         # Parse config → run pipeline → verify results
    └── test_restore.py             # Take backup, restore to a new VM, boot it
```

---

## Test Categories

### 1. Unit Tests (`config/`, `modules/`, `factory/`, `state/`, `cli/`, `utils/`)

**Scope:** Single class or function in complete isolation.

**Rules:**
- Zero real I/O. All external calls go through mocked `IShell`.
- Dependencies injected via constructor; test provides mocks.
- Core-level tests use `MockVMModuleFactory`.
- Every public method gets at least one happy-path and one error-path test.

**Example — retention engine unit test (pure logic, no I/O):**

```python
def test_retention_chain_length():
    engine = TimeBasedRetention()
    policy = RetentionPolicy(chain_length=24, keep_generations=1)
    items = load_timestamp_fixture("count_set.json")  # 48 snapshots
    result = engine.evaluate(items, policy, datetime.now())
    assert len(result.keep) == 24
    assert len(result.remove) == 24
```

**Example — snapshot module unit test (mocked shell):**

```python
def test_create_snapshot_returns_result_on_virsh_timeout(mock_shell):
    mock_shell.expect("virsh snapshot-create-as").raises(subprocess.TimeoutExpired(...))
    provider = ExternalSnapshotProvider(vm_config=make_vm_config(), shell=mock_shell)
    result = provider.create("test-snap", "vda", Path("/tmp/snap.qcow2"))
    assert not result.success
    assert "timed out" in result.error
```

### 2. Mock Tests (`mocks/`)

**Scope:** Verify that mocks correctly implement their ABC.

**Rules:**
- Every mock MUST pass `isinstance(mock, ABC)`.
- Every mock method must return a valid result type (never `None`).
- `MockVMModuleFactory` must return the correct interface for every `create_*` call.
- Domain mocks are consolidated in `mock_modules.py` (not split across
  `mock_snapshot.py`, `mock_backup.py`, etc.).  `mock_config.py` provides
  a `MockConfigFacade` for in-memory config in Core tests.

```python
def test_mock_factory_returns_interface_types():
    factory = MockVMModuleFactory()
    assert isinstance(factory.create_snapshot_provider(make_vm_config()), ISnapshotProvider)
    assert isinstance(factory.create_backup_provider(make_vm_config(), make_target()), IBackupProvider)
    assert isinstance(factory.create_retention_engine(policy), IRetentionEngine)
```

### 3. Contract Tests (`interfaces/`)

**Scope:** Verify that ALL concrete implementations satisfy their ABC.

**Rules:**
- Parametrize over all concrete classes that implement an interface.
- Test that required methods exist, accept correct arguments, and return correct result types.
- Adding a new implementation must pass existing contract tests without changes.

```python
@pytest.mark.parametrize("provider_cls", [ExternalSnapshotProvider, MockSnapshotProvider])
def test_snapshot_provider_contract_create_returns_result(provider_cls):
    provider = provider_cls(...)
    result = provider.create("name", "disk", Path("/tmp"))
    assert isinstance(result, SnapshotResult)
    assert isinstance(result.success, bool)
```

### 4. Integration Tests (`integration/`)

**Scope:** Real `virsh` and `qemu-img` calls against a disposable test VM.

**Rules:**
- Require a running libvirt daemon. Marked with `@pytest.mark.integration`.
- Fixture creates a tiny throwaway VM (256M qcow2 disk, 256MB RAM).
- Fixture destroys the VM and removes all snapshot files after test.
- Run only when explicitly requested (`-m integration`).
- `conftest.py` provides a `test_vm` fixture; tests include
  `test_nbd_full_backup.py` (NBD pull-model) and
  `test_stale_state_recovery.py` (stale state self-healing).

```python
@pytest.mark.integration
def test_create_external_snapshot(test_vm):
    provider = ExternalSnapshotProvider(test_vm.config, shell=RealShell())
    result = provider.create("int-test-snap", "vda", test_vm.snapshot_path)
    assert result.success
    assert os.path.exists(result.path)
    # Verify backing chain:
    chain = provider.get_backing_chain()
    assert len(chain) >= 2
```

### 5. Stress Tests (`stress/`)

**Scope:** System behavior under load.

**Rules:**
- Long snapshot chains (50+), rapid creation/deletion cycles.
- Concurrent access: two processes trying to acquire lockfile.
- Disk-full scenarios (simulated via `qemu-img` allocation or `fallocate`).
- Marked with `@pytest.mark.stress`; excluded from normal runs.
- `conftest.py` provides a `stress_env` fixture (disposable VM with
  a 512M disk — larger than integration's 256M for chain depth).

### 6. End-to-End Tests (`e2e/`)

**Scope:** Complete user journey from config to restored VM.

**Rules:**
- Start with a `.toml` config file and a running VM.
- Run `qsnap run` (via Core).
- Verify snapshots exist on source, backups exist on target.
- Restore VM from backup, boot it, verify it works.
- Marked with `@pytest.mark.e2e`; excluded from normal runs.
- `conftest.py` provides an `e2e_vm` fixture (disposable VM + writes
  a minimal TOML config file referencing it).

---

## Testing Paradigm (mirrors production)

| Production | Test Equivalent |
|---|---|
| `DefaultVMModuleFactory` creates modules | `MockVMModuleFactory` creates mock modules |
| `IShell` wraps `subprocess` | `MockShell` returns fixture outputs |
| `IStateManager` writes to JSON files | `InMemoryStateManager` stores in `dict` |
| `Core` coordinates pipeline | `test_pipeline.py` verifies step order |
| Modules implement ABC directly (no Core inheritance) | Tests mock the ABC; no `ModuleTestCase(Core)` base needed |
| Result objects carry success/error | Tests assert on `.success`, `.error`, `.path` |

---

## Running Tests

All test commands use `poetry run` to execute within the project's virtual environment.

```bash
# Unit + mock + contract (fast, no I/O):
poetry run pytest tests/ -m "not integration and not stress and not e2e"

# Integration (needs libvirt):
poetry run pytest tests/integration/ -m integration

# Stress (needs libvirt + patience):
poetry run pytest tests/stress/ -m stress

# End-to-end (needs libvirt + disposable VM):
poetry run pytest tests/e2e/ -m e2e

# Everything:
poetry run pytest tests/ -m ""

# Coverage:
poetry run pytest tests/ --cov=qsnap --cov-report=html
```

Markers are registered in `pyproject.toml` under `[tool.pytest.ini_options]`.
`--strict-markers` is enabled — unregistered markers will cause a test
collection error.

---

## Adding a New Module: Test Checklist

When implementing a new module (e.g. `modules/snapshot/internal.py`):

1. ✅ Create `tests/modules/snapshot/test_internal.py`
2. ✅ Update `MockVMModuleFactory` to optionally return `MockInternalSnapshotProvider`
3. ✅ Add the new provider class to contract test parametrization in `tests/interfaces/test_snapshot_provider.py`
4. ✅ Add a `.toml` fixture exercising the new snapshot type in `tests/fixtures/configs/`
5. ✅ Verify: `pytest tests/modules/snapshot/ tests/interfaces/ -v`
