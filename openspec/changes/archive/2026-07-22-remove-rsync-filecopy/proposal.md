## Why

`rsync` serves exactly **one** functional call site in the entire codebase: `FileCopyBackupProvider.transfer_missing()` (`qsnap/modules/backup/file_copy.py`), copying snapshot files for the legacy `incremental_mode = "file-copy"`. Since bitmap mode became the default (`incremental_mode = "bitmap"`, change `7321853`) and bitmap incrementals became backing-chained qcow2 deltas (archived change `2026-07-22-bitmap-dirty-block-transfer`), file-copy retains no unique capability: chain-based restore, retention cascade-deletion, and verification all work identically for bitmap chains. Maintaining two incremental mechanisms doubles the module, spec, test, and config surface for zero functional gain. The project commits to a **single backup mechanism (NBD/libnbd)** — no fallback provider, no support for libvirt < 7.2 or hosts without python3-libnbd. Breaking current configurations is accepted deliberately to simplify the code and its long-term maintenance.

## What Changes

- **BREAKING** — Delete `FileCopyBackupProvider` and `qsnap/modules/backup/file_copy.py` entirely (rsync transfer, rebase-to-anchor logic, offline FULL via direct convert, file-copy FULL via `nbd_full_export`).
- **BREAKING** — Remove the only `rsync` invocation; `rsync` is no longer required in PATH. Remove the unconditional `which rsync` hard check from `Core._validate_environment()`.
- **BREAKING** — Remove config fields: `incremental_mode` (target), `rate_limit` (global + target), `copy_base` (target — parsed but never implemented). If present in TOML, each logs a deprecation WARNING and is ignored (same precedent as removed `full_every`).
- **BREAKING** — Remove `parse_rate_limit()` / `rate_limit_to_kib()` from `qsnap/utils/parsing.py` and the `rate_limit` parameter from `IBackupProvider.transfer_missing()` (interface change — all implementations and mocks update).
- **BREAKING** — `DefaultFactory.create_backup_provider()` no longer falls back to `FileCopyBackupProvider`: libvirt < 7.2 becomes a hard `RuntimeError` (same posture as missing libnbd, design R4); the method always returns `BitmapBackupProvider` (with the existing libnbd hard check).
- Remove `verify_backup()` from `qsnap/utils/verification.py` (file-copy-oriented helper, dead after provider removal) together with its tests; a grep-verification step in tasks confirms no stray callers remain.
- Reword the pre-flight truncated-qcow2 cleanup comment/logic references from "rsync artifact" to "truncated transfer artifact" (behavior unchanged — it is generic).
- Delete the `rate-limit` capability spec entirely; update 15 further specs that reference rsync/file-copy (see Capabilities).
- Tests: delete `tests/modules/backup/test_copy.py` wholesale (~80 tests), delete rsync-specific tests in core/config/integration suites, adjust factory/config/pipeline tests that reference the removed provider or fields. Emphasis is on **removal**, not on writing new tests.
- Docs: update `AGENTS.md` (pipeline pseudocode D3, architecture diagram, shell abstraction wording), `README.md` (drop `file-copy`/`rate_limit`/`copy_base`, remove the completed "Migration from rsync to NBD" section), `TESTING.md` (test_copy.py reference), `qsnap.toml.example`, TOML test fixtures.
- Explicit non-goals: no replacement fallback provider, no offline-VM incremental support, no rate-limiting substitute, no FULL-path changes (bitmap FULL still uses `qemu-img convert` over NBD — a later change), no restore-path changes (existing file-copy chains on targets remain restorable — restore is format-based, not provider-based).

## Capabilities

### New Capabilities

None. This is a pure removal change; post-removal behavior is fully covered by existing specs (`nbd-bitmap-backup`, `nbd-dirty-block-transfer`, `module-factory`, `env-validation` as modified).

### Modified Capabilities

- `rate-limit`: **capability removed entirely** — bandwidth control existed only as `rsync --bwlimit`; no NBD equivalent exists in this change.
- `env-validation`: remove the unconditional pre-flight `rsync` availability hard requirement.
- `backup-provider`: remove all rsync/file-copy transfer requirements (exclusive-rsync transfer, `--bwlimit`, `--partial`, `--compress`, rebase-after-copy, file-existence guard, rsync failure logging/cleanup, FileCopyBackupProvider FULL paths); the capability describes a single NBD/libnbd backup provider.
- `config-model`: remove `incremental_mode`, `rate_limit` (global + target), and `copy_base` fields; `verify` default is no longer mode-dependent.
- `config-parsing`: remove `copy_base` from parsed target fields; removed fields (`incremental_mode`, `rate_limit`, `copy_base`) produce deprecation WARNINGs and are ignored; `qsnap.toml.example` field list updated.
- `module-factory`: remove FileCopy fallback branch; libvirt < 7.2 with bitmap targets is a hard error, not a fallback.
- `backup-verification`: remove rsync/file-copy post-transfer verification scenarios.
- `backup-hash-verification`: remove file-copy (rsync) mode scenarios and the mode-dependent hash default.
- `periodic-full-backup`: `FileCopyBackupProvider` references replaced by the single backup provider; rebase-to-anchor requirements superseded by native backing chains.
- `pre-flight-cleanup`: "truncated rsync artifact" detection reworded to truncated transfer artifact (behavior unchanged).
- `stall-detection`: `rsync` removed from the set of commands covered by shell-level stall detection.
- `live-vm-full-backup`: remove the "no checkpoint for file-copy NBD FULL" scenario (file-copy FULL path deleted).
- `restore-command`: rename the "file-copy backup chain" scenario to a generic backup chain (restore logic unchanged).
- `nbd-bitmap-backup`: drop the stale comparative reference to the file-copy-oriented `verify_backup()` helper (helper deleted).
- `nbd-dirty-block-transfer`: reword the "treated exactly like file-copy chains" comparison (file-copy no longer exists).
- `parsing-utils`: remove the `backup/file_copy.py` reference from the purpose statement.

## Impact

- **Code**: delete `qsnap/modules/backup/file_copy.py`; edit `qsnap/core/__init__.py` (env-validation, `rate_limit` plumbing, cleanup wording), `qsnap/factory/default.py` (single provider, hard errors), `qsnap/config/facade.py` (field removal + deprecation handling), `qsnap/models/config.py` (`GlobalConfig`, `TargetConfig` fields), `qsnap/interfaces/backup.py` (`transfer_missing` signature), `qsnap/utils/parsing.py` (rate-limit helpers), `qsnap/utils/verification.py` (`verify_backup`), `qsnap/modules/backup/bitmap.py` (stale rsync docstring reference), `qsnap/interfaces/__init__.py` / `qsnap/modules/backup/__init__.py` re-exports if present.
- **APIs (BREAKING)**: `IBackupProvider.transfer_missing()` signature; `TargetConfig` / `GlobalConfig` public fields; `DefaultFactory` behavior on libvirt < 7.2 (fallback → hard error).
- **Dependencies**: `rsync` binary no longer required; `python3-libnbd` becomes the sole transfer dependency (hard requirement already enforced for bitmap mode).
- **Tests**: ~85 deletions (whole `tests/modules/backup/test_copy.py`, rsync assertions in `tests/core/test_validation.py`, `tests/integration/test_zstd_backup.py`, `tests/config/test_fixtures.py`, `tests/conftest.py`), ~20 adjustments (factory, config model/facade/resolver, pipeline, bitmap dependency, env-validation, `tests/utils/test_nbd.py` if `verify_backup`-adjacent), plus `tests/mocks/` signature updates.
- **Specs**: 1 capability deleted (`rate-limit`), 15 delta specs; sync to `openspec/specs/` on archive.
- **Docs**: `AGENTS.md`, `README.md`, `TESTING.md`, `qsnap.toml.example`, `tests/fixtures/configs/*.toml` (rate-limit fixtures removed).
- **State/migration**: no `IStateManager` schema change; existing file-copy backup chains on target storage remain restorable (restore reads qcow2 chains, not provider metadata); user TOMLs with removed fields keep working with deprecation WARNINGs.
