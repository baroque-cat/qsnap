# qsnap — AGENTS.md

## Project

**qsnap** is a QEMU/KVM snapshot and backup orchestration tool for qcow2 images on any filesystem (XFS, ext4, etc.). It manages external disk-only snapshots via `virsh`, detects whether a VM disk has changed (`onchange`), enforces retention policies, performs incremental backups to separate storage, and maintains backing chain integrity via `blockcommit`.

---

## Paradigm: Dependency Injection with ABC Interfaces

### Core Rule

```
IConfigFacade (ABC, read-only config API)
       ▲
       │ single inheritance
       │
ConfigFacade (parses TOML, resolves option inheritance)
       │
       │ injected via DI (no Python inheritance)
       ▼
     Core (orchestrator, factory host, pipeline runner)
       │
       │ Core coordinates modules via IVMModuleFactory (DI, no inheritance)
       │
       ▼
 ┌─────────────────┬──────────┬──────────┬──────────┐
 │                 │          │          │          │
ISnapshot        IBackup   IChange   ILifecycle
Provider         Provider  Detector  Manager
       ▲              ▲         ▲         ▲
       │ implements   │         │         │
       │ (no Core     │         │         │
       │  inheritance)│         │         │
 ┌─────┴──────┐  ┌───┴──────┐  ┌┴──────┐  ┌┴──────────┐
 │External    │  │Bitmap    │  │Alloc  │  │BlockCommit │
 │Snapshot    │  │Backup    │  │Size   │  │Manager     │
 │Provider    │  │Provider  │  │Detect │  │            │
 └────────────┘  └──────────┘  └───────┘  └────────────┘

IRetentionEngine  ← pure function, no Core inheritance, no I/O
       ▲
       │ implements
 ┌─────┴──────────┐
 │TimeBased       │
 │Retention       │
 └────────────────┘
```

- **ConfigFacade is the root of truth** (implements `IConfigFacade`). No module reads the config file or accesses raw config objects. Every module receives a fully resolved, immutable config dataclass as a method parameter.
- **Core is the only coordinator** — receives `IConfigFacade`, `IVMModuleFactory`, `IStateManager`, `IShell` via constructor DI. Modules do not call each other. Core invokes them in sequence via their ABC interfaces.
- **Modules are stateless workers** — they implement their ABC interface directly, do NOT inherit from Core (design D1). State lives in `IStateManager`, injected by Core or the factory. Modules do not hold cross-run state.
- **IRetentionEngine is a pure function** — no Core inheritance, no I/O, no side effects. Deterministic given the same inputs.

### Module Contract

Every domain module MUST:

1. Implement exactly one ABC interface (e.g. `ISnapshotProvider`) — do NOT inherit from Core
2. Accept all dependencies as constructor parameters (typically `IShell`, optionally `IStateManager` — no hidden imports, no global state)
3. Receive config as immutable dataclasses in method parameters (`VMConfig`, `TargetConfig`), not stored as instance state
4. Return result objects (dataclasses or `Result` monads) — never raise exceptions for expected failures

---

## Patterns

### Abstract Factory (`IVMModuleFactory`)

One factory creates ALL module instances for a given VM + target combination. Core holds a reference to the factory interface and calls it per-VM during pipeline execution.

```
factory.create_snapshot_provider(vm_config) → ISnapshotProvider
factory.create_backup_provider(vm_config, target) → IBackupProvider
factory.create_retention_engine(policy) → IRetentionEngine
factory.create_change_detector(mode) → IChangeDetector
factory.create_lifecycle_manager() → ILifecycleManager
```

- The factory is **injected into Core** at construction time (DI).
- Tests inject `MockFactory`; production injects `DefaultFactory`.
- Adding a new snapshot strategy means: (a) implement `ISnapshotProvider`, (b) add a branch in `DefaultFactory`. Nothing else changes.

### Pipeline (Template Method in Core)

Core defines the execution order; modules implement the steps:

```
Core._execute_pipeline(vm):
  1. detector = factory.create_change_detector(...)
  2. if detector.has_changed(vm): snapshot.create(...)
  3. retention.evaluate(snapshots) → keep/remove → lifecycle.commit(...)
  4. for each target:
       for each disk:
         backup = factory.create_backup_provider(vm_config, target)
         if blockjob active on this disk → defer (continue)
         provider.run_backup(vm_config, target, disk, ...)
           → provider decides FULL vs delta (checkpoint discovery)
           → successor checkpoint at freeze point
           → freeze-timestamp name: {vm}.{freeze_ts}_{disk}_{hex6}.qcow2
       retention.evaluate(backups) → keep/remove
       _cleanup_backups() → generation-based deletion:
         FULL with no dependents beyond keep_generations → delete
         Verify-before-delete gate (design D3): old generations NOT deleted
         until new FULL passes M1/M2 verification
```

Modules never know which step they are; Core owns the sequence.

### Orthogonality: Two Worlds, One Bridge

The snapshot world (local `virsh snapshot-create-as`, retention, blockcommit) and the
backup-target world (libvirt checkpoints, NBD dirty-bitmap export, target storage) are
orthogonal — they share **no** data. Core invokes them sequentially under one lock.

- **Snapshot world** — triggered by `snapshot_create = "always"|"onchange"`.  Creates
  point-in-time crash-consistent external snapshots, enforces chain-length limits via
  blockcommit.  State lives in `{vm}.json` under `state_dir`.
- **Backup-target world** — triggered by `backup_create = "always"`.  For each configured
  disk, `IBackupProvider.run_backup(vm_config, target, disk)` creates exactly ONE backup per
  run: a FULL when no qsnap checkpoint exists for this VM+target+disk, otherwise a delta of
  dirty blocks since the newest checkpoint.  State lives in per-target JSON files
  (`_target_state.json`, `_full_backups.json`, `_dependencies.json`).

The provider decides backup kind autonomously (no snapshot data consumed).  Backup files are
named by their own freeze point: `{vm}.{freeze_ts}_{disk}_{hex6}.qcow2` (FULL:
`{vm}.FULL.{freeze_ts}_{disk}_{hex6}.qcow2`).  `BackupInfo` is the model for the target world
— no `SnapshotInfo` appears in the backup provider API.

### Immutable Config Dataclasses

`VMConfig`, `TargetConfig`, `RetentionPolicy`, `GlobalConfig` are `@dataclass(frozen=True)`. They are constructed once by `ConfigFacade` and passed down. Modules cannot mutate config — if a module needs a derived value, it computes and returns it, it does not store it back.

### Result Objects

Every fallible operation returns a result type, not `None` and not an exception:

```python
@dataclass(frozen=True)
class SnapshotResult:
    success: bool
    name: str
    path: Path
    new_allocation: int
    error: str | None  # non-None iff success is False
```

Callers pattern-match or check `.success`. Expected failures (VM not running, disk full, target unreachable) are **never** exceptions.

### State Manager as Separate Interface

`IStateManager` persists cross-run data (last allocation size, timestamps). It is an ABC, injected into Core, passed to modules that need it. Default implementation uses JSON files under `/var/lib/qsnap/state/`. Tests use `InMemoryStateManager`.

### Shell Abstraction

All `virsh`, `qemu-img`, and filesystem calls go through `IShell` (thin wrapper over `subprocess`). This enables:
- Timeout enforcement on every command
- Structured logging of every external call
- Full mockability in tests without touching the real system

`IShell` provides two execution methods:
- `run(cmd, timeout, check)` — fixed-timeout execution for short commands (`virsh`, `qemu-img info`). Kills the process after *timeout* seconds.
- `run_with_stall_detection(cmd, output_file, stall_timeout, check)` — output-growth monitoring for long-running data-transfer commands (`qemu-img convert`). Polls the *output_file* size every 60 seconds; kills the process only when no growth is observed for *stall_timeout* seconds. No maximum timeout — if data flows, the process runs to completion.

---

## Anti-Patterns

### ❌ Modules importing other modules

`ExternalSnapshotProvider` must never `from qsnap.modules.backup import ...`. If two modules need to coordinate, Core mediates.

### ❌ Modules accessing config directly

```python
# WRONG
class ExternalSnapshotProvider:
    def __init__(self, config: IConfigFacade):  # hidden dependency
        self._config = config
    def create(self, vm_config: VMConfig):
        path = self._config.get_vm("...")  # no! Core owns config routing

# CORRECT
class ExternalSnapshotProvider(ISnapshotProvider):
    def __init__(self, shell: IShell):  # only infrastructure deps
        self._shell = shell
    def create(self, vm_config: VMConfig, ...) -> SnapshotResult:
        # vm_config passed as method parameter, not stored
        pass
```

### ❌ Catching broad exceptions in modules

```python
# WRONG
try:
    virsh.snapshot_create(...)
except Exception:  # swallows everything
    pass

# CORRECT
try:
    virsh.snapshot_create(...)
except subprocess.TimeoutExpired:
    return SnapshotResult(success=False, error="virsh timed out")
except FileNotFoundError:
    return SnapshotResult(success=False, error="virsh binary missing")
```

### ❌ Mutable global state

No `global` variables. No module-level caches that persist between pipeline runs. Cross-run state goes through `IStateManager`.

### ❌ Business logic in CLI layer

`cli/commands.py` dispatches to Core methods. It does not parse config, create snapshots, or evaluate retention. It is a thin translation layer: CLI args → Core call → formatted output.

### ❌ Inheritance deeper than 2 levels from Core

```
Core → SnapshotModule → ExternalSnapshot → FancyExternalSnapshot  ← TOO DEEP

Core → ExternalSnapshot   ← acceptable
```

If you need a third level, extract the variation into a **strategy** that is composed, not inherited.

Note: domain modules (`ExternalSnapshotProvider`, `BitmapBackupProvider`, etc.) do NOT inherit Core at all (design D1). They implement their ABC directly. The "2 levels from Core" rule applies if a hierarchy of *module base classes* is ever introduced — in that case, keep it ≤2 deep.

### ❌ Skipping the factory

```python
# WRONG
snap = ExternalSnapshotProvider(vm_config)  # direct instantiation

# CORRECT
snap = self._factory.create_snapshot_provider(vm_config)
```

Every module instantiation goes through the factory. This is non-negotiable — it is what keeps the system testable and the Core unaware of concrete types.

---

## Naming Conventions

| Element | Convention | Example |
|---|---|---|
| ABC interfaces | `I` prefix | `ISnapshotProvider`, `IStateManager` |
| Concrete implementations | Descriptive noun | `ExternalSnapshotProvider`, `AllocationSizeDetector` |
| Result types | `*Result` suffix | `SnapshotResult`, `CommitResult` |
| Private Core methods | `_` prefix | `Core._execute_pipeline()` |

### Locking Contract
- Default lockfile: `/var/lib/qsnap/qsnap.lock` (parent dir auto-created).
- Sentinel `"off"` (`lockfile = "off"` in config or `--lockfile off` on CLI) disables locking.
- Exclusive lock (`fcntl.flock LOCK_EX | LOCK_NB`) is acquired only for **mutating** commands: `run`, `snapshot`, `backup`, `prune`, `reconcile`, `restore`, `fork`.
- **Read-only** commands (`list`, `stats`, `check`, `estimate`) run unlocked.
- Lock contention on a mutating command → exit code 3, message: "Lockfile is held by another qsnap instance".
- Lock is released on normal exit and on process termination (fd closed by kernel).

## Testing

See **[TESTING.md](TESTING.md)** for the full test architecture, categories, and rules.

Summary:

1. Every ABC interface gets at least one mock implementation in `tests/mocks/`.
2. Core is tested with `MockFactory` — zero real virsh/qemu-img calls.
3. Each concrete module is tested in isolation with mocked `IShell`.
4. Config parsing is tested with fixture `.toml` files.
5. Retention engine is tested with fixed timestamp sets (pure logic, no I/O).
