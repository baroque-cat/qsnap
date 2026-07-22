## Why

After the NBD unification (`2026-07-23-unify-nbd-transfer`) and rsync removal (`2026-07-22-remove-rsync-filecopy`), the codebase retains dead config fields, stale spec descriptions, a factory-pattern violation in Core, a verification-field discrepancy, scaffolding duplication between two FULL-backup code paths, and several minor bugs. These remnants create confusion for maintainers, allow silent false-negative verification, and violate the AGENTS.md paradigm. This change cleans up all identified dead code, fixes the bugs, and synchronizes specs with implementation reality — before new features (offline backup, FS freeze/thaw, fork unification) are layered on top.

## What Changes

### Dead Code Removal

- **Remove `full_verify_before_rebase` config field** — parsed, validated, stored in `GlobalConfig`, but never consumed by Core (zero grep hits). Remove from `ConfigFacade`, `GlobalConfig` dataclass, and all validation logic.
- **Remove `snapshot_deep_verify` config field** — parsed, stored in `VMConfig`, displayed in CLI status, but never passed to any lifecycle manager. Remove from `ConfigFacade`, `VMConfig` dataclass, and CLI status output.
- **Remove dead `compression_type` parameter from `_start_write_server()`** — accepted but never referenced in the method body. The compress driver auto-detects from the qcow2 header.
- **Remove dead `"hash mismatch"` retry pattern** from `is_retryable()` in `utils/retry.py` — the hash verification code path was deleted; this error string can never be produced.
- **Remove stale `backup-hash-verification` spec** — describes `content_hash` as existing, but it was fully removed. The spec should be deleted or rewritten as a historical record.

### Bug Fixes

- **Fix `corruptions` vs `errors` vs `leaks` discrepancy** — `verification.py` M2 checks `errors` and `leaks` but misses `corruptions`; `blockcommit_manager.py` and `qemu_img_commit.py` deep_verify check `corruptions` but miss `errors` and `leaks`. Both locations must check all three fields from `qemu-img check --output=json`.
- **Fix `"check"` mode unreachable for bitmap incrementals** — `verify_bitmap_incremental()` accepts `"check"` in its docstring, but `ConfigFacade` only allows `"off"`, `"metadata"`, `"compare"` for `TargetConfig.verify`. Either add `"check"` to the allowed values or remove the dead codepath from `verify_bitmap_incremental()`.
- **Fix Core directly instantiating `BitmapBackupProvider`** in `_detect_orphan_checkpoints()` — violates the factory pattern (AGENTS.md: "Every module instantiation goes through the factory. This is non-negotiable."). Route through `IVMModuleFactory`.
- **Fix `disk="vda"` hardcoded fallback** in `Core._resolve_disks()` — when `domblklist` fails or returns no disks, it falls back to `["vda"]`. Replace with a proper error result instead of a silent hardcoded default.
- **Add `snapshot_create` validation in `ConfigFacade`** — any string value silently defaults to `"always"` behavior. Add explicit validation for `{"always", "onchange", "ondemand"}`.
- **Implement compress driver validation in `Core._validate_environment()`** — 2 tests are currently skipped because the feature is not implemented. Add a check that `qemu-nbd --image-opts driver=compress` is supported.

### Scaffolding Deduplication

- **Extract shared FULL-pull lifecycle helper** — `transfer_missing()` full-pull (lines 321-409) and `create_full_backup()` (lines 969-1183) share ~200 lines of near-identical scaffolding: `qemu-img create`, `_start_write_server`, `write_backup_xml`/`write_checkpoint_xml`, `virsh backup-begin`, `_transfer`, `_terminate_qemu_nbd`, `mv .tmp → final`, `finally` cleanup. Extract a private `_full_pull_lifecycle()` helper that both methods call.

### Spec Synchronization

- **Sync `backup-provider` spec** — still describes `qemu-img convert` as the data transfer mechanism, conflicting with `nbd-bitmap-backup` spec which says "No `qemu-img convert` SHALL be used in the data path." Update to reflect the unified `_transfer()` engine.
- **Sync `periodic-full-backup` spec** — still describes `_should_create_bucket_full` as a Core method, but it was extracted to `IBucketFullStrategy` / `BucketFullStrategy`. Update to reflect the factory-delegated strategy pattern.
- **Rewrite or remove `backup-hash-verification` spec** — describes removed `content_hash`/`file_sha256`. Either delete the spec or rewrite as a historical record of what was removed and why.

## Capabilities

### New Capabilities

(None — this change introduces no new capabilities.)

### Modified Capabilities

- `backup-full-verification`: Remove `full_verify_before_rebase` requirement (dead config field). Fix M2 to check `corruptions` in addition to `errors` and `leaks`.
- `backup-verification`: Fix `"check"` mode — either add it to allowed `TargetConfig.verify` values or remove the dead codepath from `verify_bitmap_incremental()`.
- `backup-hash-verification`: Rewrite spec to document that `content_hash`/`file_sha256` were removed and the spec is retained as a historical record only. No new requirements.
- `backup-provider`: Sync spec with unified `_transfer()` engine — remove `qemu-img convert` references from the data path description.
- `core-orchestrator`: Fix factory violation in `_detect_orphan_checkpoints()`. Fix `disk="vda"` hardcoded fallback. Remove `snapshot_deep_verify` references.
- `config-model`: Remove `full_verify_before_rebase` and `snapshot_deep_verify` fields. Add `snapshot_create` validation. Remove `compression_type` parameter from `_start_write_server` interface description.
- `deep-verification-circuit`: Fix deep_verify to check `errors` and `leaks` in addition to `corruptions` in lifecycle managers.
- `env-validation`: Add compress driver availability check to `_validate_environment()`.
- `shared-utilities`: Remove dead `"hash mismatch"` retry pattern from `is_retryable()`.
- `periodic-full-backup`: Sync spec with `bucket-full-strategy` — remove `_should_create_bucket_full` as a Core method, reflect factory-delegated strategy.
- `nbd-bitmap-backup`: Remove `compression_type` parameter from `_start_write_server` spec description. Document the scaffolding deduplication helper.

## Impact

**Affected code:**
- `qsnap/core/__init__.py` — factory violation fix, `disk="vda"` fallback fix, dead config removal, scaffolding dedup
- `qsnap/modules/backup/bitmap.py` — scaffolding dedup (`_full_pull_lifecycle`), dead parameter removal
- `qsnap/utils/verification.py` — M2 field fix (`corruptions`)
- `qsnap/modules/lifecycle/blockcommit_manager.py` — deep_verify field fix (`errors`, `leaks`)
- `qsnap/modules/lifecycle/qemu_img_commit.py` — deep_verify field fix (`errors`, `leaks`)
- `qsnap/utils/retry.py` — dead pattern removal
- `qsnap/config/facade.py` — dead field removal, `snapshot_create` validation
- `qsnap/models/config.py` — dead field removal from `GlobalConfig` and `VMConfig`
- `qsnap/cli/commands.py` — remove `snapshot_deep_verify` from status output

**Affected specs:** 11 specs (listed above in Modified Capabilities)

**Affected tests:**
- Remove tests for `full_verify_before_rebase` and `snapshot_deep_verify`
- Un-skip the 2 compress driver validation tests (implement the feature)
- Update tests for `verify_bitmap_incremental` `"check"` mode
- Add tests for `corruptions`/`errors`/`leaks` field checking
- Add tests for `snapshot_create` validation
- Add tests for factory-routed orphan checkpoint detection

**Migration:** State files with `content_hash` are already read-tolerant (silently ignored). No state migration needed. Config files with `full_verify_before_rebase` or `snapshot_deep_verify` will get deprecation warnings if we keep the deprecation path, or will be silently ignored if we remove the fields outright.
