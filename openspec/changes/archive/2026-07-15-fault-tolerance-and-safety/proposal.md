## Why

qsnap runs as a background service via systemd timer. It operates on live, production VMs. Yet the current pipeline has no defense against stale partial files from crashed runs, no corrupted-state-file recovery, no pre/post-commit chain integrity verification, no retry logic for transient backup failures, and no separate deep-verification circuit. These gaps mean a single power loss, disk hiccup, or network blip can leave the system in an unrecoverable state without any warning. Production-readiness demands closing these gaps before real-world deployment.

## What Changes

- **T0/T1 automatic safety net**: stale `.tmp`/`.partial` cleanup, state file corruption recovery with rotation, backing chain integrity checks before and after every blockcommit — all run on every pipeline invocation, cost < 1 second
- **T2 deferred deep verification**: optional `qemu-img check` after blockcommit — only on shut-off VMs (deferred operations), not on the live fast-path
- **T3 separate deep-verification circuit**: dedicated `qsnap-check.timer` for weekly `qemu-img check` on all images — never blocks the hourly pipeline
- **Backup transfer retry**: exponential backoff (configurable per-target) for transient errors (connection refused, timeout, broken pipe) — non-transient errors (disk full, permission denied) are never retried
- **Config changes**: 8 new config fields across global/VM/target levels, all with safe defaults. Simple operations ON by default; heavy operations OFF by default with transparency (`qsnap list` shows what's disabled)
- **Config example updated**: all existing-but-hidden fields (`preserve_min`, `rate_limit`, `full_every`, etc.) now documented, plus all new safety fields
- **Systemd timer**: new `qsnap-check.timer` + `qsnap-check.service` for weekly deep verification

## Capabilities

### New Capabilities
- `state-recovery`: corrupted state file detection and recovery (rename to `.broken`, start fresh with warning)
- `pre-flight-cleanup`: removal of `.tmp`, `.partial`, and stale NBD socket files at pipeline startup
- `chain-integrity-verification`: pre-blockcommit check that backing chain is intact (all files exist, formats match, no broken references, no cycles) and post-blockcommit check that the chain actually shortened
- `backup-retry`: exponential backoff retry for backup transfer operations on transient errors, configurable per-target
- `deep-verification-circuit`: separate weekly systemd timer for `qsnap check --deep` — `qemu-img check --output=json` on all images plus cross-state audit

### Modified Capabilities
- `config-model`: add `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule` to GlobalConfig; `blockcommit_deep_verify`, `snapshot_deep_verify` to VMConfig; `backup_retry_max`, `backup_retry_base` to TargetConfig
- `config-parsing`: parse new fields with defaults; resolve `backup_retry_max`/`backup_retry_base` inheritance (target-level, no global default needed — target has its own default of 3/"2s")
- `env-validation`: add stale-file cleanup step to `_validate_environment` (runs before any other pipeline operation)
- `state-management`: add `_load` corruption recovery (JSON decode error → rename + empty state); add state file rotation (`vm.json` → `vm.json.1` → `vm.json.2`) on save
- `core-orchestrator`: integrate pre-commit chain verify before blockcommit; post-commit chain verify after blockcommit; retry wrapper around backup transfer; pre-flight cleanup; deferred deep verify for shut-off VMs
- `lifecycle-manager`: accept optional `deep_verify: bool = False` flag on `BlockCommitManager.blockcommit()` — when True, run `qemu-img check` after commit
- `backup-provider`: `FileCopyBackupProvider.transfer_missing` and `BitmapBackupProvider.transfer_missing` integrated with retry logic (retry is a Core concern, not a provider concern — but providers return structured errors enabling Core to decide retryability)
- `cli-interface`: `qsnap list config` shows effective safety settings per VM (ON/OFF for deep verify); `qsnap check` reports stale files and state corruption; new `qsnap check --deep` weekly output
- `systemd-integration`: add `qsnap-check.service` and `qsnap-check.timer` for weekly deep verification

## Impact

- **Config model**: 8 new fields across `GlobalConfig` (+5), `VMConfig` (+2), `TargetConfig` (+2). All frozen, all with documented defaults, no breaking changes.
- **Config example**: `qsnap.toml.example` expanded from 78 lines to ~130 lines with all safety options documented.
- **Core pipeline**: new `_preflight_cleanup()`, `_verify_backing_chain()`, `_wrap_with_retry()` steps. Pipeline order preserved; new steps are insertions, not reorderings.
- **State files**: `state_backup_count` adds rotation; `_load` adds corruption recovery. Backward-compatible — clean state files load identically.
- **Systemd**: new `qsnap-check.timer` + `qsnap-check.service` (weekly by default, configurable via `deep_check_schedule`).
- **No API changes**: CLI interface extended (new `qsnap list config` columns, new timer service), existing commands unchanged.
