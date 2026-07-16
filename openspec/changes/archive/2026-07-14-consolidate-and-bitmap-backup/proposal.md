## Why

qsnap v0.1-beta is architecturally sound but has critical correctness gaps (hardcoded disk name, silently swallowed rebase errors) and a fundamental efficiency ceiling: `FileCopyBackupProvider` copies entire qcow2 files even when only megabytes have changed. The qcow2 specification defines dirty tracking bitmaps that enable copy-only-changed-blocks semantics. Coupled with the gaps found during deep exploration, the next iteration must harden correctness, support multi-disk VMs, and deliver true incremental backups — the "btrbk effect" via `checkpoint-create` + `qemu-img` bitmap integration.

## What Changes

### P1 — Hardening & Feature Completion

- **Fix hardcoded `disk = "vda"`**: Resolve active disk path dynamically via `virsh domblklist` (as `ExternalSnapshotProvider.list()` already does). Extend to iterate over **all** disks when a VM has multiple (`vda`, `vdb`, …).
- **Stop swallowing `qemu-img rebase` errors**: `FileCopyBackupProvider.transfer_missing()` currently catches JSON parse errors with bare `pass` and reports success. This must return `BackupResult(success=False)` on rebase failure so broken incremental chains are surfaced.
- **Extract shared parsers** (`_parse_domblklist_path`, `_parse_timestamp`) into `qsnap/utils/parsing.py` to eliminate duplication across `snapshot/external.py`, `change/allocation_detector.py`, `backup/file_copy.py`.
- **Implement `EXIT_BACKUP_ABORT`**: When any backup task fails, `Core` must return exit code 10, matching btrbk semantics.
- **Print schedule for backup retention**: `--print-schedule` currently only evaluates snapshot retention. Extend to show per-target backup keep/remove decisions.
- **Fill missing test fixtures**: Create `daily_set.json` and `mixed_set.json` timestamp fixtures referenced by existing retention tests.

### P2 — Dirty Bitmap Incremental Backup (BitmapBackupProvider)

- **New `BitmapBackupProvider`**: An `IBackupProvider` implementation using `virsh checkpoint-create-as` + `qemu-img convert --bitmap` to copy only dirty blocks between checkpoints. This replaces whole-file copy for incremental targets.
- **New `incremental_mode` field on `TargetConfig`**: `"file-copy"` (current, default) or `"bitmap"` (new). No **BREAKING** — defaults preserve current behaviour.
- **New `qsnap restore` command**: Copies backup chain back to a target directory, runs `qemu-img rebase` to restore local backing paths, and optionally invokes `virsh define` + `virsh start`. **Note:** full restore workflow spans across P3 resilience improvements; this change delivers the file-level restore core.
- **New `qsnap check --deep` flag**: Extends the existing `check` command with `qemu-img check` on each snapshot/backup to detect bitrot and qcow2 structure corruption (not just missing backing files).
- **Support `snapshot_create = "ondemand"`**: Skip snapshot creation when no target is reachable (matching btrbk semantics); currently parsed but not enforced by Core.

## Capabilities

### New Capabilities

- `bitmap-backup-provider`: Create `BitmapBackupProvider` (implements `IBackupProvider`) using `virsh checkpoint-create-as` and `qemu-img convert --bitmap` for block-level incremental backups of dirty bitmaps. Includes `incremental_mode` on `TargetConfig`.
- `restore-command`: New `qsnap restore` CLI command that copies backup files to a destination, rebuilds backing chains via `qemu-img rebase`, and produces a VM definition.
- `parsing-utils`: Shared parsing helpers (`parse_domblklist_path`, `parse_timestamp`) extracted from duplicated implementations into `qsnap/utils/parsing.py`.

### Modified Capabilities

- `config-model`: `TargetConfig` gains `incremental_mode: str = "file-copy"` field. `VMConfig` gains optional `disks` field for multi-disk support (defaults to auto-discovery via `domblklist` when absent).
- `backup-provider`: `IBackupProvider` contract unchanged, but `DefaultFactory` gains a branch selecting `BitmapBackupProvider` when `target.incremental_mode == "bitmap"`.
- `core-orchestrator`: `_create_snapshot` iterates all disks instead of hardcoded `"vda"`. `_backup_target` selects provider via factory. `print_schedule` extended to evaluate backup retention. `check` gains `--deep` mode using `qemu-img check`. `EXIT_BACKUP_ABORT` wired into `PipelineResult`.
- `change-detection`: `IChangeDetector` constructor now accepts optional `disk` parameter (to support per-disk change tracking in multi-disk setups).
- `cli-interface`: New `restore` subcommand. `check` gains `--deep` flag. `backup` command accepts optional `--incremental-mode` override.

## Impact

- **Source files**: `core/__init__.py`, `factory/default.py`, `models/config.py`, `modules/backup/file_copy.py`, `modules/snapshot/external.py`, `modules/change/allocation_detector.py`, `cli/app.py`, `cli/commands.py`, `cli/errors.py`, `interfaces/backup.py`, `interfaces/change.py`
- **New files**: `modules/backup/bitmap.py` (BitmapBackupProvider), `utils/parsing.py` (shared parsers)
- **New commands**: `qsnap restore`, `qsnap check --deep`
- **Dependencies**: no new external runtime dependencies; `qemu-img` >= 5.1 required for bitmap support (already available on any system with qcow2 backing chain support)
- **Breaking changes**: None. Defaults preserve v0.1 behavior.
