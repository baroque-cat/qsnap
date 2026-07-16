## Why

The current backup model has a fundamental architectural flaw: `full_every` is a standalone interval-based config option disconnected from the retention policy. This creates orphan FULL backups, lacks dependency tracking between FULLs and incrementals, and copies `base.qcow2` to targets unnecessarily. Additionally, `cp` is used as a fallback transfer mechanism alongside `rsync`, creating two code paths. The system also lacks runtime size estimation, leaving operators blind to projected storage growth.

## What Changes

- **BREAKING**: Remove `full_every` and `full_compress` from `TargetConfig`. FULL backup creation is now driven by the retention policy's highest active bucket level, not a separate interval.
- **BREAKING**: FULL creation trigger: the first snapshot entering a new period of the highest active bucket (yearly > monthly > weekly > daily > hourly) becomes a FULL backup via `qemu-img convert`. If no buckets are active and `preserve_min="all"`, no FULL is created (chain grows indefinitely).
- **BREAKING**: `full_compress` renamed to `compress` (target-level, default `true`). Applies to all FULL backups on that target.
- **BREAKING**: `copy_base` option added to `TargetConfig` (default `false`). When `false`, `base.qcow2` is never copied to the target — the first backup is always a FULL.
- Dependency-aware cascade deletion: a FULL is only deleted when it falls out of ALL retention buckets AND no incremental in the keep-set references it. Orphaned incrementals (whose FULL was deleted) are cascade-deleted.
- Replace ALL `cp` usage in `FileCopyBackupProvider.transfer_missing()` with `rsync`. Remove cp fallback. `rsync` becomes a hard requirement validated in `_validate_environment()`.
- Size estimation logging: on every pipeline run (including dry-run), log projected target size based on VM allocated size, average incremental size from state history, and retention policy bucket counts.
- New CLI command: `qsnap estimate [vm]` — standalone size projection without pipeline execution.
- Config validation: forbid `preserve_min != "all"` when all bucket counts are zero (would break the chain without a FULL anchor).
- README.md fully updated to reflect new FULL model, rsync requirement, size estimation, and removed config options.

## Capabilities

### New Capabilities
- `size-estimation`: Runtime storage projection — calculates projected target size from VM allocated size, churn history, and retention policy. Logs on every run; also exposed via `qsnap estimate` CLI command.
- `cascade-deletion`: Dependency-aware FULL backup deletion — tracks which incrementals reference which FULLs, prevents deletion of FULLs with active dependents, cascade-deletes orphaned incrementals.

### Modified Capabilities
- `periodic-full-backup`: FULL creation logic changes from `full_every` interval to bucket-driven trigger based on highest active retention bucket. `full_every` and `full_compress` config options removed. `compress` and `copy_base` options added.
- `backup-provider`: `FileCopyBackupProvider` now uses `rsync` exclusively (no `cp` fallback). First backup to a target is always FULL. `copy_base=false` prevents base.qcow2 duplication. Dependency tracking recorded after each incremental rebase.
- `retention-engine`: No logic changes to `TimeBasedRetention.evaluate()` (remains pure function). Clarification that dependency-aware deletion is handled by Core, not the retention engine.
- `env-validation`: `rsync` availability check becomes a hard error (pipeline aborts if not found), replacing the current soft warning.
- `restore-command`: Restore from target updated to handle new chain structure (FULL anchor → incrementals).
- `config-model`: `TargetConfig` loses `full_every` and `full_compress`, gains `compress` and `copy_base`. `GlobalConfig` gains `compress` default.
- `config-parsing`: Parsing updated for new/removed fields. New validation rule for `preserve_min` without buckets.
- `cli-interface`: New `estimate` subcommand added.
- `schedule-summary`: `schedule_summary()` enhanced with real size projections (not just bucket counts).

## Impact

- **Config**: Breaking change for users with `full_every` or `full_compress` in their TOML — these keys are removed. Migration: `full_every` is ignored (FULLs now automatic), `full_compress` → `compress`.
- **State**: `_full_backups.json` structure changes from `dict[str, dict]` (single FULL per target) to `dict[str, list[dict]]` (multiple FULLs). New `_dependencies.json` for incremental→FULL tracking. Auto-migration on load.
- **Code**: `file_copy.py` (rsync only, FULL logic, dependency recording), `core/__init__.py` (bucket-driven FULL, cascade deletion, size estimation, env validation), `json_manager.py` (multi-FULL tracking, dependencies), `config.py` (model changes), `facade.py` (parsing, validation), `cli/` (estimate command).
- **Dependencies**: `rsync` becomes a hard system requirement (was optional).
- **README**: Full rewrite of Full Backups section, Configuration Reference, Example Configurations, Requirements.
