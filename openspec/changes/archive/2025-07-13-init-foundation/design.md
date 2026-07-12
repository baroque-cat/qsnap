## Context

qsnap is a new QEMU/KVM snapshot and backup orchestration tool for qcow2 images on any filesystem (XFS, ext4), inspired by btrbk. The project has zero existing code — this change lays the architectural foundation.

The strict paradigm from AGENTS.md mandates:

```
IConfigFacade → ConfigFacade → Core → Modules (Snapshot, Backup, Retention, ...)
                                      ↑
                               IVMModuleFactory (DI)
```

Before any domain module can be written, the spine must exist: immutable config dataclasses, ABC interfaces, ConfigFacade, IShell, IStateManager, Core orchestrator, and the factory.

## Goals / Non-Goals

**Goals:**
- Establish the immutable config model (GlobalConfig, VMConfig, TargetConfig, RetentionPolicy) as @dataclass(frozen=True)
- Define all ABC interfaces (IConfigFacade, IShell, IStateManager, ISnapshotProvider, IBackupProvider, IRetentionEngine, IChangeDetector, ILifecycleManager, IVMModuleFactory)
- Implement ConfigFacade (TOML parser → frozen dataclasses with option inheritance)
- Implement SubprocessShell (IShell wrapper over subprocess with timeout)
- Implement JsonStateManager (IStateManager with JSON files)
- Implement Core (pipeline runner, DI host, coordinates modules via ABCs)
- Implement DefaultFactory (IVMModuleFactory — skeleton, real modules added later)
- Set up project scaffold (poetry, pyright strict, ruff, black)
- Achieve testable state: Core can be unit-tested with MockFactory before any module exists

**Non-Goals:**
- Any domain module implementation (Snapshot, Backup, ChangeDetector, LifecycleManager)
- CLI layer
- Integration tests (require real libvirt)
- Config file schema validation beyond basic field presence
- SSH/remote targets
- Multiple disks per VM
- Raw backup targets

## Decisions

### D1: TOML for configuration format

**Chosen:** TOML (parsed with stdlib `tomllib` since Python 3.11, or `tomli` backport).

**Alternatives considered:**
- Custom btrbk-like syntax → better UX for btrbk users, but requires writing and maintaining a parser. Rejected: not worth the cost at this stage.
- YAML → whitespace-sensitive, ambiguous typing (the "Norway problem"). Rejected.
- JSON → not human-friendly for configuration. Rejected.

**Rationale:** TOML is widely understood (pyproject.toml, Cargo.toml), has a standard parser in Python stdlib, and maps naturally to the nested section structure with `[[vm]]` and `[[vm.target]]` array-of-tables syntax. Option inheritance (global → per-VM → per-target) is implemented on top of parsed TOML dicts.

### D2: Synchronous subprocess for IShell

**Chosen:** Synchronous `subprocess.run()`.

**Alternatives considered:**
- asyncio + asyncio.subprocess → adds complexity with no benefit. virsh/qemu-img are blocking processes. VMs are processed sequentially. Rejected.

**Rationale:** Simplicity. No event loop, no async/await propagation through the call stack. Timeout enforced via `subprocess.run(timeout=N)`.

### D3: Retention as pure function outside Core hierarchy

**Chosen:** IRetentionEngine is a standalone ABC. Its implementations do NOT inherit from Core.

**Alternatives considered:**
- RetentionEngine(Core) → would satisfy AGENTS.md "all modules inherit Core" rule, but Core provides nothing Retention needs (no IShell, no state). Rejected as over-engineering.

**Rationale:** `evaluate(items, policy, now) → RetentionResult` is a pure function: no I/O, no side effects, fully deterministic. Making it inherit Core would be ceremonial. AGENTS.md updated to allow this exception.

### D4: Frozen dataclasses for all config and results

**Chosen:** `@dataclass(frozen=True)` for GlobalConfig, VMConfig, TargetConfig, RetentionPolicy, and all Result types.

**Alternatives considered:**
- Pydantic BaseModel → powerful validation but heavier dependency, mutable by default. Rejected for now; can be added later if validation complexity grows.
- NamedTuple → works but less ergonomic for nested structures and optional fields. Rejected.

**Rationale:** Immutability is a hard requirement from AGENTS.md. Frozen dataclasses enforce it at runtime. Type hints provide editor support and pyright validation. Simple, zero-dependency.

### D5: Constructor-based dependency injection

**Chosen:** All dependencies passed via `__init__` parameters. No DI framework.

**Alternatives considered:**
- dependency-injector / inject → third-party magic, harder to trace. Rejected.
- Service locator pattern → hidden dependencies, hard to test. Rejected.

**Rationale:** Explicit constructor injection is the simplest form of DI, fully compatible with AGENTS.md rules ("accept all dependencies as constructor parameters"). Core receives IConfigFacade + IVMModuleFactory + IStateManager + IShell at construction. Factory receives IShell + IStateManager at construction and passes them to module constructors.

### D6: Project layout

```
qsnap/
├── __init__.py
├── models/          # Tier 0: Result + Config dataclasses
│   ├── __init__.py
│   ├── results.py   # SnapshotResult, BackupResult, etc.
│   └── config.py    # GlobalConfig, VMConfig, TargetConfig, RetentionPolicy
├── interfaces/      # Tier 1: ABCs
│   ├── __init__.py
│   ├── config.py    # IConfigFacade
│   ├── shell.py     # IShell
│   ├── state.py     # IStateManager
│   ├── snapshot.py  # ISnapshotProvider
│   ├── backup.py    # IBackupProvider
│   ├── retention.py # IRetentionEngine
│   ├── change.py    # IChangeDetector
│   ├── lifecycle.py # ILifecycleManager
│   └── factory.py   # IVMModuleFactory
├── config/          # Tier 2: ConfigFacade
│   ├── __init__.py
│   └── facade.py    # ConfigFacade(IConfigFacade)
├── shell/           # Tier 2: SubprocessShell
│   ├── __init__.py
│   └── subprocess_shell.py
├── state/           # Tier 2: JsonStateManager
│   ├── __init__.py
│   └── json_manager.py
├── core/            # Tier 3: Core orchestrator
│   └── __init__.py
├── factory/         # Tier 5: DefaultFactory
│   ├── __init__.py
│   └── default.py
├── retention/       # Tier 2: RetentionEngine (standalone, no Core)
│   ├── __init__.py
│   └── time_based.py
└── py.typed         # PEP 561 marker
```

## Risks / Trade-offs

- **Risk: Over-engineering the ABC layer before any module exists.** Mitigation: only define method signatures needed by the pipeline; don't guess future requirements. ABCs are thin — mostly single-method.

- **Risk: ConfigFacade → Core tight coupling.** If Core depends on ConfigFacade's specific dataclass shapes, any config model change breaks Core. Mitigation: IConfigFacade is the contract; Core only accesses config through it.

- **Risk: TOML inheritance resolution is hand-rolled.** Merging global defaults with per-VM and per-target overrides has edge cases (None vs missing key, list merge vs replace). Mitigation: extensive unit tests with inheritance.conf fixture covering all combinations.

- **Risk: Frozen dataclasses with mutable sub-fields.** `VMConfig.targets` is `list[TargetConfig]`. The list itself is not frozen — only the VMConfig object is. Mitigation: document in code; Python's immutable patterns for nested structures are verbose (tuple vs list). Accept this trade-off for now; can add `tuple[...]` + custom `__init__` later.

- **Risk: JsonStateManager file corruption on crash mid-write.** Mitigation: atomic write pattern (write to .tmp, then os.rename).

## Open Questions

1. Should `ConfigFacade` validate that `snapshot_dir` and `target` paths exist on the filesystem at parse time, or defer to runtime? **Decision deferred to implementation — start with deferred validation (simpler, less I/O at parse time).**
2. Should `DefaultFactory` raise if a `create_*` method is called before the corresponding module is implemented, or return a stub? **Decision: raise NotImplementedError with a clear message until modules are added.**
