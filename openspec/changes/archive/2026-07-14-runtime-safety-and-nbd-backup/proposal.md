## Why

qsnap has a complete feature set (P0-P2 all implemented), but three critical gaps remain before it can be trusted in production: (1) zero pre-flight validation means a missing directory or unavailable target crashes the pipeline mid-execution; (2) AppArmor/SELinux block `virsh blockcommit` on running VMs, and qsnap has no mitigation — snapshots accumulate indefinitely; (3) backups are never verified after transfer — a silently corrupted `cp` or incomplete `qemu-img convert` produces unrecoverable data loss. Additionally, the bitmap backup provider needs replacement: the current `qemu-img convert --bitmap` approach crashes VMs under certain QEMU versions, and the native `virsh backup-begin` pull-model NBD is the correct API.

## What Changes

- **Pre-flight environment validation** — before pipeline execution, verify: directories exist, binaries are in PATH, VMs are defined in libvirt, targets are reachable for `ondemand` mode. Fail fast with a clear error.
- **AppArmor/SELinux deferred operations** — when `virsh blockcommit` is blocked by MAC, record the deferred snapshots in `IStateManager` and retry automatically when the VM next shuts down. Never crash. Never silently skip.
- **Automatic backup verification** — after every `transfer_missing()`, verify the target file: `"metadata"` (default, checks `qemu-img info` consistency — milliseconds), `"full"` (pairs with `qemu-img compare` — heavy, configurable), or `"off"`. Configurable per-target via `verify` field.
- **BitmapBackupProvider v2 via `virsh backup-begin`** — replace the current `qemu-img convert --bitmap` checkpoint approach with the native libvirt pull-model NBD backup API. No VM crashes. Incremental dirty-block extraction over NBD. Checkpoint lifecycle managed by libvirt.
- **`--quiesce` snapshot support** — use guest-agent filesystem freeze for application-consistent snapshots. Opt-in via `snapshot_quiesce = true` in VM config.
- **`qemu-img commit` fallback lifecycle manager** — alternative to `virsh blockcommit` for offline VMs on systems where virsh blockcommit fails (no libvirt dependency, works without AppArmor issues).
- **`snapshot_preserve_min = "latest"` / `target_preserve_min = "latest"`** — retention policy parity with btrbk: keep only the latest snapshot/backup when `preserve_min` is `"latest"`.
- **Informational improvements** — `qsnap list snapshots --tree` for backing-chain visualization, `--long` CLI flag parity with btrbk, README.md.
- **`qemu-img map` change detection strategy** — new `IChangeDetector` implementation using allocated-region comparison instead of single integer `actual-size`. More precise: detects zero-fill/trim operations that change allocation map but not total size.

## Capabilities

### New Capabilities

- `env-validation`: Pre-flight environment validation — verifies directories, binaries, VM existence, and target reachability before pipeline execution.
- `deferred-operations`: AppArmor/SELinux-aware deferred work queue — records blocked blockcommit operations in IStateManager and retries when VM shuts down.
- `backup-verification`: Automatic post-transfer backup verification at configurable levels (metadata/full/off), per-target.
- `nbd-bitmap-backup`: Native `virsh backup-begin` pull-model NBD backup as replacement for current checkpoint-based bitmap approach.
- `quiesce-snapshot`: Application-consistent snapshots via guest-agent filesystem freeze.
- `offline-commit`: `qemu-img commit`-based lifecycle manager for offline VMs without libvirt dependency.
- `map-change-detection`: Change detection via `qemu-img map --output=json` allocated-region comparison.
- `tree-listing`: Tree-format backing-chain visualization in `qsnap list snapshots --tree`.

### Modified Capabilities

- `config-model`: Add `TargetConfig.verify` field (`"metadata"` | `"full"` | `"off"`, default `"metadata"`). Add `VMConfig.snapshot_quiesce` field (bool, default `false`). Add support for `"latest"` value in `RetentionPolicy.preserve_min`.
- `core-orchestrator`: Add `_validate_environment()` called before pipeline. Add deferred operations check/execute in `_execute_snapshot_steps`. Add verification step in `_backup_target` after transfer. Wire `snapshot_quiesce` to `ExternalSnapshotProvider.create()`.
- `backup-provider`: `BitmapBackupProvider` replaced with NBD-based implementation. `FileCopyBackupProvider` gains post-transfer verification step.
- `lifecycle-manager`: New `QemuImgCommitManager` implementing `ILifecycleManager`. `BlockCommitManager` gains AppArmor/SELinux error detection returning deferred `CommitResult`.
- `change-detection`: New `MapChangeDetector` implementing `IChangeDetector`. Factory `create_change_detector(mode)` gains `"allocation-map"` branch.
- `state-management`: `IStateManager` gains `deferred_operations` methods: `get_deferred_operations()`, `add_deferred_blockcommit()`, `clear_deferred_operations()`.
- `snapshot-provider`: `ExternalSnapshotProvider.create()` accepts optional `quiesce: bool = False` parameter, passing `--quiesce` to virsh.
- `cli-interface`: New `restore` subcommand (btrbk parity), `--tree` flag on `list snapshots`, `--long` / `-L` global flag parity.
- `retention-engine`: `preserve_min` parsing supports `"latest"` value — keeps only the most recent item.
- `shell-abstraction`: `IShell.run()` gains optional `check: bool = False` parameter — returns `ShellResult` without logging command failure as error (for pre-flight checks).

## Impact

- **Source**: `qsnap/core/__init__.py` (validation, deferred ops, verification step, NBD backup integration), `qsnap/cli/commands.py` + `qsnap/cli/app.py` (new CLI flags), `qsnap/models/config.py` + `qsnap/models/results.py` (new fields + types), `qsnap/modules/backup/bitmap.py` (full rewrite to NBD), `qsnap/modules/backup/file_copy.py` (verification step), `qsnap/modules/lifecycle/blockcommit_manager.py` (error detection), `qsnap/modules/lifecycle/qemu_img_commit.py` (new), `qsnap/modules/change/map_detector.py` (new), `qsnap/modules/snapshot/external.py` (--quiesce), `qsnap/factory/default.py` (new branches), `qsnap/interfaces/state.py` (new methods), `qsnap/interfaces/shell.py` (check parameter), `qsnap/retention/time_based.py` ("latest" support), `qsnap/state/json_manager.py` (deferred ops persistence)
- **Tests**: New test files for env-validation, deferred-operations, backup-verification, nbd-bitmap-backup, quiesce-snapshot, offline-commit, map-change-detection. Modifications to existing tests for config-model, core-orchestrator, backup-provider, lifecycle-manager, change-detection, state-management, snapshot-provider, cli-interface, retention-engine, shell-abstraction.
- **No breaking changes**: All new config fields have defaults preserving current behavior. `BitmapBackupProvider` is replaced but factory falls back to `FileCopyBackupProvider` on unsupported QEMU versions.
- **New file**: `README.md` in project root.
