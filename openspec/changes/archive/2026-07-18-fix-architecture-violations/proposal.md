## Why

A deep architecture exploration (2026-07-18) revealed that while the qsnap codebase follows its DI/ABC paradigm with outstanding fidelity (~85-95% AGENTS.md compliance), several violations have accumulated during rapid feature development:

1. A **cross-domain import** exists: `ExternalSnapshotProvider` (snapshot domain) imports `_file_sha256` from `qsnap.modules.backup.verification` (backup domain) — a direct violation of AGENTS.md's core rule: *"ExternalSnapshotProvider must never `from qsnap.modules.backup import ...`"*
2. **Shared utilities** (`nbd_helper.py`, `verification.py`) live under `qsnap/modules/backup/` and are imported by Core directly, bypassing the `IBackupProvider` interface
3. `BitmapBackupProvider` raises `RuntimeError` in its constructor for an expected operational condition (old libvirt), violating the "never raise exceptions for expected failures" rule
4. **106 lines of bucket-driven FULL backup strategy** live as `@staticmethod`s in Core instead of a dedicated module, violating "Core is the only coordinator — business logic goes in modules"
5. `full_verify_before_rebase` config field is defined and validated but **never consumed** — the rebase path hardcodes `"metadata"`
6. `tests/stress/` and `tests/e2e/` directories are entirely missing
7. `TESTING.md` is stale — it references `test_base.py` files that don't exist under design D1, `.conf` fixtures that are now `.toml`, and individual mock files consolidated into `mock_modules.py`

## What Changes

1. **Move `_file_sha256`** from `qsnap/modules/backup/verification.py` to `qsnap/utils/hash.py` — eliminates the cross-domain import in `external.py` (P1)
2. **Move shared utilities** (`nbd_helper.py`, `verification.py`) from `qsnap/modules/backup/` to `qsnap/utils/` — these are pure-function helpers that don't implement any ABC, they don't belong in the domain module tree (P2)
3. **Replace `RuntimeError` in `BitmapBackupProvider`** with a deferred version check: `DefaultFactory` calls `is_libvirt_new_enough()` before constructing the provider; the factory returns a fallback provider if libvirt is insufficient (P2)
4. **Extract bucket FULL backup strategy** from `Core._should_create_bucket_full()` and its companions (`_active_buckets`, `_f_anchor_buckets`, `_period_key`) into a new `IBucketFullStrategy` interface with a `BucketFullStrategy` implementation, instantiated via the factory (P2)
5. **Fix `full_verify_before_rebase`** — pass `GlobalConfig.full_verify_before_rebase` through to `FileCopyBackupProvider.transfer_missing()` so the rebase path reads the configured mode instead of hardcoding `"metadata"` (P3)
6. **Add `tests/stress/` and `tests/e2e/`** with initial test skeletons matching TESTING.md prescriptions (P3)
7. **Update `TESTING.md`** — remove stale `test_base.py` references (not needed under design D1), update fixture extensions `.conf` → `.toml`, document `mock_modules.py` consolidation, align file listing with reality (P3)

## Capabilities

### New Capabilities
- **shared-utilities**: Extraction of stateless helper functions (`_file_sha256`, `is_vm_running`, `nbd_full_export`, `verify_backup`, `verify_full_backup`, `_get_first_disk_target`, `is_libvirt_new_enough`) from `qsnap/modules/backup/` into `qsnap/utils/`, establishing a single home for functions that serve multiple domains
- **bucket-full-strategy**: A dedicated `IBucketFullStrategy` interface and `BucketFullStrategy` implementation extracted from Core's private `@staticmethod`s, created through `IVMModuleFactory.create_bucket_full_strategy()`

### Modified Capabilities
- **snapshot-provider**: `ExternalSnapshotProvider` replaces the cross-domain `from qsnap.modules.backup.verification import _file_sha256` with `from qsnap.utils.hash import file_sha256`
- **backup-provider**: `FileCopyBackupProvider` and `BitmapBackupProvider` replace intra-package imports from `nbd_helper`/`verification` with imports from `qsnap.utils`
- **nbd-bitmap-backup**: `BitmapBackupProvider.__init__` no longer raises `RuntimeError`; version checking moves to `DefaultFactory` (constructor accepts `IShell` only, factory gates on `is_libvirt_new_enough()`)
- **backup-full-verification**: `full_verify_before_rebase` config field is now consumed in the rebase path; hardcoded `"metadata"` replaced with `GlobalConfig.full_verify_before_rebase`
- **core-orchestrator**: `Core` removes direct imports from `qsnap.modules.backup.*`; bucket strategy methods (`_should_create_bucket_full`, `_active_buckets`, `_f_anchor_buckets`, `_period_key`) are extracted to `BucketFullStrategy` and invoked through `self._factory.create_bucket_full_strategy()`
- **module-factory**: `IVMModuleFactory` gains `create_bucket_full_strategy() -> IBucketFullStrategy`; `DefaultFactory` implements it and gains a `_check_bitmap_capable()` guard before constructing `BitmapBackupProvider`

## Impact

- **Affected source files**: `qsnap/modules/snapshot/external.py`, `qsnap/modules/backup/file_copy.py`, `qsnap/modules/backup/bitmap.py`, `qsnap/modules/backup/__init__.py`, `qsnap/core/__init__.py`, `qsnap/factory/default.py`, `qsnap/interfaces/factory.py`, all files in `qsnap/modules/backup/` being moved
- **New files**: `qsnap/utils/hash.py`, `qsnap/utils/nbd.py` (renamed from nbd_helper), `qsnap/utils/verification.py` (moved), `qsnap/interfaces/bucket_strategy.py`, `qsnap/modules/backup/bucket_strategy.py` (or co-located), `tests/stress/test_long_chain.py`, `tests/stress/test_concurrent.py`, `tests/e2e/test_from_config.py`, `tests/e2e/test_restore.py`
- **Interface changes**: `IVMModuleFactory` gains one method — **BREAKING** for all factory implementations and mocks
- **Import path changes**: All consumers of `qsnap.modules.backup.nbd_helper` and `qsnap.modules.backup.verification` must update to `qsnap.utils.nbd` and `qsnap.utils.verification` (Core, CLI composition root, factory)
- **No IStateManager schema changes** — state JSON format is unaffected
- **No config model changes** — `full_verify_before_rebase` field already exists on `GlobalConfig`, just isn't consumed
- **Test impact**: All mock factory implementations must add `create_bucket_full_strategy()`; import paths in 15+ test files change; new stress/e2e test directories created
- **No breaking changes for end users** — all changes are internal refactoring within the qsnap package
