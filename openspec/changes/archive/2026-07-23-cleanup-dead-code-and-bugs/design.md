## Context

After the NBD unification (`2026-07-23-unify-nbd-transfer`) and rsync removal (`2026-07-22-remove-rsync-filecopy`), the codebase has residual dead code, stale spec descriptions, a factory-pattern violation, a verification-field discrepancy, scaffolding duplication, and minor bugs. These were identified during a deep-explore audit comparing qsnap's NBD implementation against virtnbdbackup. This change cleans up all identified issues before new features (offline backup, FS freeze/thaw, fork unification) are layered on top.

Current state:
- `full_verify_before_rebase` and `snapshot_deep_verify` are parsed, validated, stored in config dataclasses, but never consumed by Core (zero grep hits).
- `verification.py` M2 checks `errors` and `leaks` from `qemu-img check --output=json` but misses `corruptions`. Lifecycle managers check `corruptions` but miss `errors` and `leaks`.
- `Core._detect_orphan_checkpoints()` directly instantiates `BitmapBackupProvider` via lazy import, bypassing the factory.
- `Core._resolve_disks()` falls back to hardcoded `["vda"]` when `domblklist` fails.
- `transfer_missing()` full-pull and `create_full_backup()` share ~200 lines of near-identical scaffolding.
- `_start_write_server()` accepts `compression_type` parameter but never uses it.
- `is_retryable()` matches `"hash mismatch"` pattern that can never be produced.
- `ConfigFacade` does not validate `snapshot_create` values.
- `Core._validate_environment()` does not check compress driver availability (2 tests skipped).
- Specs `backup-provider`, `backup-hash-verification`, `periodic-full-backup`, `shared-utilities` are out of sync with code.

## Goals / Non-Goals

**Goals:**
- Remove all dead config fields, dead parameters, and dead retry patterns
- Fix the `corruptions`/`errors`/`leaks` verification discrepancy
- Fix the factory-pattern violation in `_detect_orphan_checkpoints()`
- Fix the `disk="vda"` hardcoded fallback
- Add `snapshot_create` validation and compress driver validation
- Extract shared FULL-pull scaffolding into a helper method
- Synchronize all stale specs with implementation reality

**Non-Goals:**
- New features (offline backup, FS freeze/thaw, inc→full fallback, block job check, fork unification) — these are separate changes
- Refactoring the `_transfer()` engine itself (already unified)
- Changing the NBD protocol or `INbdClient` interface
- Adding multi-disk support (separate change)
- Removing the `"hash"`/`"full"` deprecation mapping (keeping for backward compat)

## Decisions

### D1: Remove `full_verify_before_rebase` entirely (not deprecate)

**Decision:** Remove the field from `GlobalConfig`, `ConfigFacade`, and all validation logic. Do NOT keep a deprecation path.

**Rationale:** The field was never wired into Core. No user could have relied on it because it never did anything. A deprecation warning would be misleading — implying the feature worked at some point. Removing outright is cleaner.

**Alternative considered:** Keep the field with a deprecation warning. Rejected — the field never functioned, so there's nothing to deprecate.

### D2: Remove `snapshot_deep_verify` entirely

**Decision:** Remove from `VMConfig`, `ConfigFacade`, and CLI status output. Keep `blockcommit_deep_verify` (it IS wired).

**Rationale:** Same as D1 — the field was parsed but never consumed. Only `blockcommit_deep_verify` is passed to the lifecycle manager.

### D3: Fix verification fields — check all three in both locations

**Decision:** Both `verification.py` M2 and lifecycle manager `deep_verify` SHALL check `errors`, `leaks`, AND `corruptions` from `qemu-img check --output=json`. Any non-zero value among the three fields SHALL fail verification.

**Rationale:** `qemu-img check` returns all three fields. They represent different categories of problems. Checking only a subset allows false negatives. The fix is trivial — add the missing field checks.

**Alternative considered:** Create a shared `check_qemu_img_check_output()` utility. Rejected for now — the logic is 3 lines per location, and a shared utility would add import coupling. Can be extracted later if more callers appear.

### D4: Fix `"check"` mode for bitmap incrementals — add to allowed values

**Decision:** Add `"check"` to the allowed values for `TargetConfig.verify` in `ConfigFacade`. The `verify_bitmap_incremental()` function already supports it — the config facade was the only blocker.

**Rationale:** `"check"` runs `qemu-img check` which is a valid verification mode. It was likely excluded by accident when the config facade was written. The function already handles it. Adding it to the allowed values makes the dead codepath live.

**Alternative considered:** Remove the `"check"` codepath from `verify_bitmap_incremental()`. Rejected — `"check"` is a useful verification mode between `"metadata"` (fast) and `"compare"` (slow).

### D5: Fix factory violation — add `create_backup_provider_for_target()` or route through existing factory method

**Decision:** `Core._detect_orphan_checkpoints()` SHALL obtain the backup provider via `self._factory.create_backup_provider(vm_config, target)` instead of directly instantiating `BitmapBackupProvider`. The orphan detection needs to iterate over all targets, so it will call the factory per-target.

**Rationale:** AGENTS.md: "Every module instantiation goes through the factory. This is non-negotiable." The lazy import was documented as intentional (design D6: "avoid a hard dependency from Core to the bitmap module"), but the factory already provides this decoupling — Core doesn't need to know the concrete type.

**Alternative considered:** Add a new `create_backup_provider_for_vm()` factory method that doesn't require a target. Rejected — the existing `create_backup_provider(vm_config, target)` works fine; orphan detection already iterates targets.

### D6: Fix `disk="vda"` fallback — return error result instead

**Decision:** When `virsh domblklist` fails or returns no disks, `Core._resolve_disks()` SHALL return an empty list and log a WARNING. The caller (`_create_snapshot()`) SHALL skip snapshot creation with an `ActionRecord("error", ...)` when the disk list is empty.

**Rationale:** Hardcoding `"vda"` is a silent failure — if the VM has a different disk target (e.g., `"sda"`, `"vdb"`), the snapshot would be created on the wrong disk or fail cryptically. An empty list + WARNING is safer and more explicit.

**Alternative considered:** Raise `RuntimeError`. Rejected — the pipeline should continue with other VMs, not abort entirely.

### D7: Extract `_full_pull_lifecycle()` helper for scaffolding dedup

**Decision:** Extract a private `_full_pull_lifecycle(self, socket_path, write_socket, pid_file, tmp_file, final_file, disk_target, virtual_size, compress, compression_type, stall_timeout, backup_xml_path, checkpoint_xml_path, vm_config, target)` method on `BitmapBackupProvider`. Both `transfer_missing()` full-pull and `create_full_backup()` SHALL call this helper. The helper handles: `qemu-img create`, `_start_write_server`, `_transfer`, `_terminate_qemu_nbd`, `mv .tmp → final`, and the `finally` cleanup.

**Rationale:** ~200 lines of near-identical code between the two methods. The helper reduces duplication, ensures consistent error handling, and makes future changes (e.g., adding compression to fork) easier.

**Alternative considered:** Merge `create_full_backup()` into `transfer_missing()`. Rejected — they have different callers (Core calls `create_full_backup()` for bucket-driven FULLs, `transfer_missing()` for incremental transfer). Merging would complicate the interface.

### D8: Remove dead `compression_type` parameter from `_start_write_server()`

**Decision:** Remove the `compression_type` parameter from `_start_write_server()`. The compress driver auto-detects the compression algorithm from the qcow2 header (set by `qemu-img create -o compression_type=...`).

**Rationale:** The parameter is accepted but never referenced in the method body. It's dead weight that confuses readers.

### D9: Add `snapshot_create` validation in `ConfigFacade`

**Decision:** `ConfigFacade._build_vm()` SHALL validate that `snapshot_create` is one of `{"always", "onchange", "ondemand"}`. Invalid values SHALL raise `ConfigError`.

**Rationale:** Currently any string silently defaults to `"always"` behavior. This is a silent misconfiguration — a typo like `"on-changed"` would be treated as `"always"`.

### D10: Add compress driver validation in `_validate_environment()`

**Decision:** `Core._validate_environment()` SHALL check that `qemu-nbd --image-opts driver=compress` is supported by running `qemu-nbd --help` and checking for `--image-opts` support. If the compress driver is not available, validation SHALL fail with an actionable error. In dry-run mode, the failure SHALL be a WARNING.

**Rationale:** 2 tests are currently skipped because the feature is not implemented. The compress driver is required for compressed FULL backups — if it's missing, backups silently produce uncompressed files.

### D11: Rewrite `backup-hash-verification` spec as historical record

**Decision:** Rewrite the spec to document that `content_hash`/`file_sha256` were removed in the `unify-nbd-transfer` change and the spec is retained as a historical record only. No new requirements.

**Rationale:** The spec currently describes `content_hash` as existing, which is misleading. Rewriting as a historical record prevents confusion.

### D12: Sync `backup-provider` and `periodic-full-backup` specs

**Decision:** Update `backup-provider` spec to remove `qemu-img convert` references from the data path (already done in `nbd-bitmap-backup` spec). Update `periodic-full-backup` spec to reflect `IBucketFullStrategy` / `BucketFullStrategy` instead of `Core._should_create_bucket_full()`.

**Rationale:** Specs must match implementation reality. Stale specs cause confusion for maintainers and new contributors.

## Risks / Trade-offs

- **[Risk] Removing `full_verify_before_rebase` breaks configs that set it** → Mitigation: The field was never consumed, so removing it changes no runtime behavior. `ConfigFacade` will log a WARNING for unknown keys if the field appears in TOML.

- **[Risk] Adding `"check"` to allowed verify modes changes behavior for configs that previously rejected it** → Mitigation: No config could have used `"check"` before (it was rejected by `ConfigFacade`). The change only enables a previously-blocked mode.

- **[Risk] Factory-routed orphan detection changes the call path** → Mitigation: The factory already creates `BitmapBackupProvider` for backup targets. The orphan detection code will use the same factory method, producing the same concrete type.

- **[Risk] Removing `disk="vda"` fallback may break VMs where domblklist fails** → Mitigation: If `domblklist` fails, the VM likely has bigger problems. The WARNING + skip is safer than silently using the wrong disk.

- **[Risk] Scaffolding dedup introduces a large helper method with many parameters** → Mitigation: The helper is private (`_full_pull_lifecycle`) and only called from two sites. If the parameter list is too long, a dataclass can be introduced later.
