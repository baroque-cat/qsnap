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
│   └── test_factory.py
│
├── config/                         # Unit tests: parser, resolver, facade
│   ├── test_parser.py              # Lexical + syntax of .conf files
│   ├── test_resolver.py            # Option inheritance (volume → subvolume → target)
│   ├── test_model.py               # Immutability of VMConfig, TargetConfig, etc.
│   └── test_facade.py              # ConfigFacade integration (parser + resolver)
│
├── core/                           # Unit tests: Core orchestration
│   ├── test_engine.py              # Core.run(), Core._execute_pipeline()
│   ├── test_pipeline.py            # Step ordering, error handling per step
│   └── test_errors.py              # Error hierarchy, error formatting
│
├── modules/                        # Unit tests: individual domain modules
│   ├── snapshot/
│   │   ├── test_base.py            # SnapshotModule(Core) base behavior
│   │   └── test_external.py        # ExternalSnapshotProvider specifics
│   │
│   ├── backup/
│   │   ├── test_base.py
│   │   ├── test_copy.py            # CopyBackupProvider (XFS target)
│   │   └── test_raw.py             # RawBackupProvider (future)
│   │
│   ├── retention/
│   │   ├── test_base.py
│   │   └── test_time_based.py      # Retention logic with fixed timestamps
│   │
│   ├── change/
│   │   ├── test_base.py
│   │   ├── test_allocation.py      # AllocationSizeDetector
│   │   ├── test_always.py
│   │   └── test_ondemand.py
│   │
│   └── lifecycle/
│       ├── test_base.py
│       └── test_blockcommit.py
│
├── factory/                        # Unit tests: DefaultVMModuleFactory
│   └── test_default.py             # Returns correct types for each config variant
│
├── state/                          # Unit tests: StateManager
│   └── test_manager.py             # Read/write allocation, timestamps, JSON format
│
├── cli/                            # Unit tests: CLI dispatch
│   ├── test_commands.py            # Each command maps to correct Core method
│   └── test_app.py                 # Argument parsing, exit codes
│
├── utils/                          # Unit tests: pure-function utilities
│   ├── test_locking.py             # Lockfile acquire/release
│   ├── test_shell.py               # IShell wrappers, timeout handling
│   └── test_time.py                # Timestamp formatting
│
├── mocks/                          # Mock implementations of every ABC
│   ├── __init__.py
│   ├── mock_factory.py             # MockVMModuleFactory — returns all mocks
│   ├── mock_shell.py               # MockShell — predefined command outputs
│   ├── mock_snapshot.py
│   ├── mock_backup.py
│   ├── mock_retention.py
│   ├── mock_change.py
│   ├── mock_lifecycle.py
│   └── mock_state.py               # InMemoryStateManager
│
├── fixtures/                       # Static test data
│   ├── configs/                    # .conf files for parser tests
│   │   ├── minimal.conf            # One VM, one target
│   │   ├── multi_vm.conf           # Multiple VMs
│   │   ├── inheritance.conf        # Option cascading
│   │   └── invalid.conf            # Malformed config
│   ├── shell_outputs/              # Canned virsh/qemu-img output
│   │   ├── domblklist.txt
│   │   ├── snapshot_list.txt
│   │   ├── qemu_img_info.json
│   │   └── backing_chain.txt
│   └── timestamps/                 # Fixed timestamp sets for retention tests
│       ├── hourly_set.json
│       ├── daily_set.json
│       └── mixed_set.json
│
├── integration/                    # Integration: real virsh/qemu-img on test VMs
│   ├── conftest.py                 # Setup/teardown test VM, cleanup snapshots
│   ├── test_snapshot_create.py     # Actually create and verify external snapshots
│   ├── test_blockcommit.py         # Actually commit and verify chain reduction
│   ├── test_backup_transfer.py     # Actually copy + rebase to another path
│   └── test_full_pipeline.py       # Run → snapshot → backup → prune, end-to-end
│
├── stress/                         # Stress: large chains, concurrent access
│   ├── test_long_chain.py          # 50+ snapshots, blockcommit tail
│   └── test_concurrent.py          # Lockfile prevents parallel runs
│
└── e2e/                            # End-to-end: from config to restored VM
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
def test_retention_hourly_policy():
    engine = TimeBasedRetention(policy=RetentionPolicy(hourly=24, daily=0, weekly=0))
    items = load_timestamp_fixture("hourly_set.json")  # 48 snapshots, 1 per hour
    decision = engine.evaluate(items)
    assert len(decision.keep) == 24
    assert decision.keep[0].timestamp > decision.keep[-1].timestamp
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

```python
def test_mock_factory_returns_interface_types():
    factory = MockVMModuleFactory()
    assert isinstance(factory.create_snapshot_provider(make_vm_config()), ISnapshotProvider)
    assert isinstance(factory.create_backup_provider(make_vm_config(), make_target()), IBackupProvider)
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
- Fixture creates a tiny throwaway VM (e.g., Alpine Linux qcow2, 256MB RAM).
- Fixture destroys the VM and removes all snapshot files after test.
- Run only when explicitly requested (`-m integration`).

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

### 6. End-to-End Tests (`e2e/`)

**Scope:** Complete user journey from config to restored VM.

**Rules:**
- Start with a `.conf` file and a running VM.
- Run `qsnap run` (via Core).
- Verify snapshots exist on source, backups exist on target.
- Restore VM from backup, boot it, verify it works.

---

## Testing Paradigm (mirrors production)

| Production | Test Equivalent |
|---|---|
| `DefaultVMModuleFactory` creates modules | `MockVMModuleFactory` creates mock modules |
| `IShell` wraps `subprocess` | `MockShell` returns fixture outputs |
| `IStateManager` writes to JSON files | `InMemoryStateManager` stores in `dict` |
| `Core` coordinates pipeline | `test_pipeline.py` verifies step order |
| Module inherits from `Core` | Test class can inherit from a `ModuleTestCase(Core)` base |
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

# Everything:
poetry run pytest tests/ -m ""

# Coverage:
poetry run pytest tests/ --cov=qsnap --cov-report=html
```

---

## Adding a New Module: Test Checklist

When implementing a new module (e.g. `modules/snapshot/internal.py`):

1. ✅ Create `tests/modules/snapshot/test_internal.py`
2. ✅ Update `MockVMModuleFactory` to optionally return `MockInternalSnapshotProvider`
3. ✅ Add the new provider class to contract test parametrization in `tests/interfaces/test_snapshot_provider.py`
4. ✅ Add a `.conf` fixture exercising the new snapshot type in `tests/fixtures/configs/`
5. ✅ Verify: `pytest tests/modules/snapshot/ tests/interfaces/ -v`
