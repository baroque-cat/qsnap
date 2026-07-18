## Context

The qsnap codebase follows a strict Dependency Injection with ABC interfaces paradigm (AGENTS.md). During rapid feature development (15+ archived changes), several architectural violations accumulated:

1. **Cross-domain import**: `ExternalSnapshotProvider` imports `_file_sha256` from `qsnap.modules.backup.verification` — the snapshot module depending on the backup module
2. **Shared utilities in domain module tree**: `nbd_helper.py` and `verification.py` are pure-function helpers that don't implement any ABC, yet live under `qsnap/modules/backup/` and are imported by Core directly
3. **Exception for expected failure**: `BitmapBackupProvider.__init__` raises `RuntimeError` when libvirt is too old — an expected operational condition
4. **Business logic in orchestrator**: 106 lines of bucket-driven FULL backup strategy live as `@staticmethod`s in Core
5. **Unconsumed config field**: `GlobalConfig.full_verify_before_rebase` exists and is validated but the rebase path hardcodes `"metadata"`
6. **Missing test infrastructure**: `tests/stress/` and `tests/e2e/` don't exist
7. **Stale TESTING.md**: references `test_base.py` files removed under design D1, `.conf` fixtures migrated to `.toml`, mock consolidation not documented

## Goals / Non-Goals

**Goals:**
- Eliminate all cross-domain module imports
- Move stateless utility functions to `qsnap/utils/` where they belong
- Replace `RuntimeError` in `BitmapBackupProvider` constructor with a factory-level guard
- Extract bucket FULL backup strategy into a dedicated module with an ABC interface
- Wire `full_verify_before_rebase` config through to the rebase path
- Create initial stress and e2e test directories with skeleton tests
- Update TESTING.md to match reality

**Non-Goals:**
- No changes to the CLI interface or user-facing behavior
- No changes to `IStateManager` schema or JSON persistence format
- No changes to config model fields (they already exist; we just consume them)
- No new runtime dependencies
- No changes to the pipeline execution order
- No extraction of `restore()` or `fork()` methods from Core (separate change)
- No extraction of `_parse_duration` from Core (right now used only for deferred thresholds)

## Decisions

### Decision 1: Utility extraction to `qsnap/utils/`

**Choice**: Move `_file_sha256`, `is_vm_running`, `nbd_full_export`, `is_libvirt_new_enough`, `_get_first_disk_target`, `verify_backup`, `verify_full_backup` to new files under `qsnap/utils/`.

**Rationale**: These are stateless pure functions that serve multiple callers across module boundaries. They do not implement any ABC and should not live in the domain module tree. `qsnap/utils/` is the established location for cross-cutting utilities (already hosts `parsing.py`, `retry.py`, `time.py`).

**Alternatives considered**:
- `qsnap/shared/` — adds a new top-level package when `utils/` already exists and serves this purpose
- Keep in `qsnap/modules/backup/` but import freely — violates AGENTS.md "modules importing other modules" rule
- Expose through `IBackupProvider` interface — over-abstraction; these are utility functions, not backup operations

**File mapping**:
- `qsnap/modules/backup/verification.py` → `qsnap/utils/verification.py`
- `qsnap/modules/backup/nbd_helper.py` → `qsnap/utils/nbd.py`
- `_file_sha256` extracted to → `qsnap/utils/hash.py` (public name: `file_sha256`)
- `qsnap/modules/backup/__init__.py` updated to re-export from new locations for backward compatibility during transition

### Decision 2: Factory-level gating for BitmapBackupProvider

**Choice**: `DefaultFactory.create_backup_provider()` calls `is_libvirt_new_enough()` before constructing `BitmapBackupProvider`. If libvirt is insufficient, the factory logs a warning and returns `FileCopyBackupProvider`.

**Rationale**: This eliminates the constructor exception violation and makes the fallback logic testable at the factory level. `BitmapBackupProvider.__init__` remains pure — it receives `IShell` only and trusts that the factory only constructs it when appropriate.

**Alternatives considered**:
- Lazy validation in `transfer_missing()` returning `BackupResult(success=False)` — delays error detection, requires callers to handle fallback
- `BitmapBackupProvider.__init__` returns a Result — Python constructors can't return non-self; we'd need a factory function
- Factory method as separate static function — adds unnecessary indirection

### Decision 3: IBucketFullStrategy interface

**Choice**: Create `IBucketFullStrategy` ABC with a single method `should_create_full(target: TargetConfig, policy: RetentionPolicy, all_fulls: list[FullBackupInfo], snapshot_ts: datetime, now: datetime) -> tuple[bool, str]`. `BucketFullStrategy` implements it by extracting the existing `_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, `_period_key` logic. `IVMModuleFactory` gains `create_bucket_full_strategy() -> IBucketFullStrategy`. Core calls `self._factory.create_bucket_full_strategy().should_create_full(...)`.

**Rationale**: This follows the established pattern: every domain concern gets an ABC interface, a concrete implementation, and factory creation. The 106 lines of bucket logic are clearly a separate strategy concern, not orchestration.

**Alternatives considered**:
- Keep in Core as private methods — violates AGENTS.md "business logic in Core" anti-pattern
- Move to `RetentionPolicy` as a method — RetentionPolicy is a frozen dataclass, should not contain logic
- Move to `TimeBasedRetention` — bucket strategy operates on FULL backups, not snapshot retention; different domain

**Interface design**: Returns `tuple[bool, str]` where bool indicates whether to create FULL and str is the bucket level (e.g. "monthly", "weekly"). Only the bucket strategy knows about anchor buckets, F-syntax, and period keys.

### Decision 4: Threading full_verify_before_rebase

**Choice**: `Core._backup_target()` reads `global_config.full_verify_before_rebase` and passes it as a parameter to `FileCopyBackupProvider.transfer_missing()`. The provider uses this value instead of the hardcoded `"metadata"`.

**Rationale**: Minimal change. The config field already exists on `GlobalConfig` and is validated by `ConfigFacade`. Only the consumption point is missing. Threading it as a method parameter follows the existing pattern (config passed as method parameters, not stored).

**Alternatives considered**:
- Store on `TargetConfig` — requires config model change, migration; the setting is per-target conceptually but defined globally
- Store on `FileCopyBackupProvider` instance — violates "modules don't store config" rule
- Add to `IBackupProvider` interface — changes interface for all providers, some of which don't rebase

## Risks / Trade-offs

- **[Risk]** Import path changes break ~15 test files and 3 production files → **Mitigation**: Run `pytest` immediately after each file move; use `rg` to find all import sites before starting
- **[Risk]** `IBucketFullStrategy` adds complexity if it's only ever instantiated as `BucketFullStrategy` → **Accept**: The value is in testability (mock strategy in Core tests) and architectural consistency, not runtime polymorphism
- **[Risk]** Moving `nbd_helper.py` and `verification.py` to `utils/` exposes internals that were previously "package-private" → **Accept**: These functions are already imported by Core and across modules; making them `utils` documents their shared nature
- **[Risk]** `MockVMModuleFactory` and all mock factory instances in tests must add `create_bucket_full_strategy` → **Mitigation**: Add to `MockVMModuleFactory` first, then update all test files in one pass
- **[Trade-off]** Stress and e2e tests are skeletons initially (not comprehensive) → this is acceptable; the goal is to establish the directories and patterns so future changes can add real tests incrementally
