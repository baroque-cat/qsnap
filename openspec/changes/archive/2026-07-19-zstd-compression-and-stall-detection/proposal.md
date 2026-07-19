## Why

FULL backups of large VM disks (100+ GB) to HDD targets consistently fail because: (1) zlib compression (used by `qemu-img convert -c`) is 22× slower than no compression — 77 MB/s vs 1723 MB/s on tmpfs benchmarks — making a 113 GB disk take 6–11 hours instead of the hardcoded 3600s (1 hour) timeout; (2) the hardcoded timeout kills backups that are progressing correctly but slowly, forcing a full restart and wasting all progress; (3) the size estimation formula (`base_size × 0.3`) is always wrong because it cannot predict the compressibility of real data, providing misleading projections.

## What Changes

- **zstd as default compression algorithm**: Replace zlib with zstd (`-o compression_type=zstd` on `qemu-img convert -c`, `--compress-choice=zstd` on rsync). zstd is 11× faster than zlib (850 MB/s vs 77 MB/s on benchmarks), transitioning backups from CPU-bound to I/O-bound. A new `compression_type` config field (default `"zstd"`, alternative `"zlib"`) allows switching back if needed.
- **Stall detection for long-running transfers**: Add `IShell.run_with_stall_detection()` method that monitors output file growth. If the output file (.tmp/.partial) stops growing for a configurable `stall_timeout` (default 30 minutes), the process is killed. This replaces hardcoded timeouts for data-transfer commands — if data is flowing, the backup runs to completion regardless of duration; if data stops, it is killed. No max timeout (stall detection is sufficient for all realistic scenarios).
- **Remove size estimation**: Delete `_log_size_estimate()` entirely (both dry-run and real mode). Remove the `base_size × 0.3` formula from `schedule_summary()`, `estimate()`, and the pipeline. Log only factual data (base_size, compression_type) — no projections.
- **Configurable stall timeout**: Add `backup_stall_timeout` field to GlobalConfig and TargetConfig (default `"30m"`), inherited via option inheritance.
- **Systemd `TimeoutStartSec=0`**: Disable systemd's default 90s oneshot timeout so qsnap's stall detection is the sole authority.
- **README and AGENTS.md updates**: Document zstd as default, new config fields, removed size estimation, and the stall detection mechanism. Remove outdated warnings about NBD + compression.

## Capabilities

### New Capabilities
- `stall-detection`: IShell method for monitoring output file growth during long-running data transfers (qemu-img convert, rsync). Kills the process if no progress is detected for a configurable stall timeout. Replaces hardcoded timeouts for data-transfer commands.

### Modified Capabilities
- `shell-abstraction`: Add `run_with_stall_detection()` method to IShell ABC and SubprocessShell. Existing `run()` remains unchanged for short commands (virsh, qemu-img info/check/compare).
- `size-estimation`: Remove the `base_size × 0.3` compression factor formula. Remove projected FULL size, projected total size, and estimated delta. Keep only factual base_size logging and `qsnap estimate` CLI command (without projections). Remove design D5 from the pipeline.
- `backup-provider`: Add `compression_type: str = "zstd"` parameter to `create_full_backup()` and `transfer_missing()`. Synchronize compression algorithm across qemu-img convert (zstd via `-o compression_type=zstd`) and rsync (zstd via `--compress-choice=zstd`). Use `run_with_stall_detection()` for data transfers instead of `run()` with hardcoded timeout.
- `live-vm-full-backup`: Add `compression_type` parameter to `nbd_full_export()`. Pass `compression_type` from `create_full_backup()` through to the NBD convert command. Use `run_with_stall_detection()` for the NBD convert step.
- `nbd-bitmap-backup`: Add `compression_type` parameter to incremental NBD transfer. Use `run_with_stall_detection()` for the incremental convert step.
- `config-model`: Add `compression_type: str = "zstd"` to GlobalConfig and TargetConfig. Add `backup_stall_timeout: str = "30m"` to GlobalConfig and TargetConfig. Both inherited via option inheritance (global → VM → target).
- `config-parsing`: Parse `compression_type` and `backup_stall_timeout` from TOML. Validate `compression_type` against `{"zstd", "zlib"}`. Parse `backup_stall_timeout` as duration string.
- `systemd-integration`: Add `TimeoutStartSec=0` to qsnap.service to disable systemd's default oneshot timeout.

## Impact

- **ABC interfaces**: `IShell` gains a new abstract method `run_with_stall_detection()` — all implementations (SubprocessShell, MockShell) must implement it. `IBackupProvider.create_full_backup()` and `IBackupProvider.transfer_missing()` gain `compression_type` parameter.
- **Config dataclasses**: `GlobalConfig` and `TargetConfig` gain `compression_type` and `backup_stall_timeout` fields. Backward compatible — new fields have defaults.
- **Config facade**: Parses two new TOML fields with validation and option inheritance.
- **Core pipeline**: `_log_size_estimate()` step removed (design D5). `schedule_summary()` and `estimate()` simplified.
- **Modules**: `FileCopyBackupProvider`, `BitmapBackupProvider`, `nbd_full_export()` updated to pass `compression_type` and use `run_with_stall_detection()`.
- **Tests**: MockShell must implement `run_with_stall_detection()`. New tests for zstd compression, stall detection, and removed size estimation. Existing size estimation tests must be deleted or rewritten.
- **Documentation**: README, AGENTS.md, TESTING.md updated. qsnap.toml.example updated with new fields.
- **Dependencies**: No new Python dependencies. zstd is provided by qemu-img (built-in) and rsync (built-in since 3.2.0). No pip install needed.
- **Breaking changes**: `IShell` ABC gains a new method (mocks must implement it). Size estimation output format changes (no more projected sizes). Default compression changes from zlib to zstd (existing configs with `compress = true` automatically get zstd).
