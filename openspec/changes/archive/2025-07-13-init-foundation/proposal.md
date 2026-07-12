## Why

qsnap needs a strictly-typed, testable, DI-driven foundation before any domain logic can be written. Without immutable config dataclasses, ABC interfaces, shell abstraction, state management, and a Core orchestrator, no module (Snapshot, Backup, Retention, ChangeDetector) can be built. This foundation establishes the architectural spine defined in AGENTS.md and enables test-first development from day one.

## What Changes

- Introduce **immutable config dataclasses**: `GlobalConfig`, `VMConfig`, `TargetConfig`, `RetentionPolicy` — all `frozen=True`, constructed by ConfigFacade, never mutated by modules
- Introduce **result types**: `SnapshotResult`, `BackupResult`, `CommitResult`, `RetentionResult`, `ShellResult`, `ChangeResult` — every fallible operation returns a result, never raises for expected failures
- Introduce **ConfigFacade**: parses TOML configuration, resolves option inheritance, produces immutable dataclasses, implements `IConfigFacade` ABC
- Introduce **shell abstraction**: `IShell` ABC + `SubprocessShell` concrete — all virsh/qemu-img calls go through this. Enables timeout enforcement, structured logging, and full mockability
- Introduce **state management**: `IStateManager` ABC + `JsonStateManager` concrete — persists cross-run data (allocation sizes, snapshot lists, last-run timestamps) under `/var/lib/qsnap/state/`
- Introduce **retention engine**: `IRetentionEngine` ABC — pure function (no Core inheritance, no I/O), evaluates which snapshots/backups to keep based on retention policy
- Introduce **Core orchestrator**: hosts the pipeline, injects dependencies, coordinates modules via ABC interfaces, owns execution order
- Introduce **module factory**: `IVMModuleFactory` ABC + `DefaultFactory` — creates all module instances per-VM, enables DI and mockability
- **Project scaffold**: poetry/pyproject.toml, pyright config, ruff config, black config

## Capabilities

### New Capabilities
- `config-model`: Immutable configuration dataclasses (`GlobalConfig`, `VMConfig`, `TargetConfig`, `RetentionPolicy`) with frozen fields, used as the single source of truth throughout the system
- `result-types`: Result dataclasses (`SnapshotResult`, `BackupResult`, `CommitResult`, `RetentionResult`, `ShellResult`, `ChangeResult`) for all fallible operations, replacing exceptions for expected failures
- `config-parsing`: `ConfigFacade` — TOML config parser that resolves option inheritance and produces immutable `VMConfig`/`TargetConfig` dataclasses
- `shell-abstraction`: `IShell` ABC + `SubprocessShell` — wraps subprocess calls to virsh/qemu-img with timeout, structured output, and full mockability
- `state-management`: `IStateManager` ABC + `JsonStateManager` — cross-run persistence of allocation sizes, snapshot lists, and timestamps
- `retention-engine`: `IRetentionEngine` ABC — pure-function retention policy evaluation (no Core inheritance, no I/O)
- `core-orchestrator`: `Core` class — pipeline runner, dependency injection host, coordinates all modules via ABC interfaces
- `module-factory`: `IVMModuleFactory` ABC + `DefaultFactory` — creates module instances per VM+target, enables DI and test mock injection

### Modified Capabilities
- _(none — this is the initial foundation, no existing capabilities to modify)_

## Impact

- **New source tree**: `qsnap/` package with `config/`, `core/`, `interfaces/`, `state/`, `shell/`, `retention/`, `models/`, `factory/`
- **New config format**: `/etc/qsnap/qsnap.toml` (TOML, replacing the conceptual btrbk-like .conf)
- **New state directory**: `/var/lib/qsnap/state/` (JSON files per VM)
- **Build system**: poetry with pyright (strict mode), ruff, black
- **Zero runtime dependencies on existing code**: this is the first code in the project
- **Test infrastructure**: pytest with mock implementations for every ABC, fixture configs, fixture shell outputs, fixture timestamp sets
