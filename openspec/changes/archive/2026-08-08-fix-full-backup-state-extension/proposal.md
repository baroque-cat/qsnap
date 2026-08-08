# Proposal: fix-full-backup-state-extension

## Why

Commit `0811599` ("orthogonalize snapshots and backups") regressed the FULL backup state
recording: `Core._backup_target()` now passes `result.snapshot_name` (a stem without the
`.qcow2` extension) to `record_full_backup()`, where the pre-regression code passed
`f"{full_name}.qcow2"`. Since `JsonStateManager` derives `FullBackupInfo.path` as
`Path(target_path) / name`, every recorded FULL now points at a nonexistent extensionless
path. All existence-based consumers (`_detect_phantom_fulls`, startup validation, the
`_backup_target` phantom filter, target consistency check, `reconcile`) therefore classify
every real FULL as a phantom: state records are destroyed on startup and by `reconcile`,
incremental dependency linkage is cascaded away, onchange baselines are reset, and a new
FULL is created on **every run** instead of a delta — a self-perpetuating cycle that
exhausts target storage. This violates the archived design decision D3
(`2026-07-27-fix-broken-backing-chain`: "Change `record_full_backup` to store stem instead
of `.qcow2` — rejected").

## What Changes

- Restore the `.qcow2` extension at the sole production recording call site
  (`Core._backup_target()`, `qsnap/core/__init__.py:5262`): record
  `f"{result.snapshot_name}.qcow2"`.
- Enforce the extension invariant defensively inside `JsonStateManager.record_full_backup()`
  (append `.qcow2` to `name` if missing, derive `path` from the normalized name) so no
  future caller can regress the format. `set_last_full_backup()` inherits the fix via
  delegation.
- Add an idempotent load-time migration in `JsonStateManager._load_full_backups()`:
  stem-format entries left by the buggy version are normalized to the extended form
  (`name` and `path` fields checked independently), **before** the existing dedup pass,
  and persisted back on the next write. Pre-existing production state (already extended)
  requires zero migration.
- Make `JsonStateManager.remove_full_backup()` name-format tolerant: normalize the lookup
  name before exact matching so both stem callers (`_cleanup_backups` passes
  `BackupInfo.name` from `provider.list()`, which is a stem) and extended callers
  (state-derived `full.name`) remove the same record. No call-site changes required.
- Update `InMemoryStateManager` (test mock) to mirror the same normalization contract, so
  unit tests can no longer reproduce the bug faithfully.
- Remove the integration-test workarounds that paper over the bug
  (`_normalize_full_state` in `tests/integration/test_check_targets.py`,
  `_align_recorded_full_with_disk` in `tests/integration/test_preserve_min.py`) and update
  the affected integration tests to assert the corrected behavior.
- Correct the conflicting spec example in `periodic-full-backup` (stem name in the
  `record_full_backup` scenario) and specify the `.qcow2` name invariant explicitly.

Not breaking: no ABC signature changes (`IStateManager`, `IBackupProvider` untouched), no
factory branch changes, no config schema changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `state-management`: specifies the `.qcow2` name invariant for `_full_backups.json`
  entries, defensive normalization in `record_full_backup()`, idempotent load-time
  migration of stem-format entries in `_load_full_backups()`, and name-format-tolerant
  matching in `remove_full_backup()`.
- `periodic-full-backup`: specifies that Core records the FULL backup name WITH the
  `.qcow2` extension (derived from the provider's stem-form `BackupResult.snapshot_name`),
  and corrects the stem example in the recording scenario.

## Impact

- **Affected modules:** `qsnap/state/json_manager.py` (write, load, remove paths),
  `qsnap/core/__init__.py` (one call site), `tests/mocks/mock_state.py` (mock contract),
  `tests/state/test_manager.py`, `tests/integration/test_check_targets.py`,
  `tests/integration/test_preserve_min.py`, plus new regression tests.
- **State schema migration path:** only `_full_backups.json` is affected. Entries written
  before the regression already carry `.qcow2` names and need no migration. Entries written
  by the buggy version (stem names) are normalized idempotently on load. Downgrade to the
  pre-regression binary is byte-compatible. `_dependencies.json`, `_target_state.json`,
  per-VM `{vm}.json`, libvirt checkpoints, and target files are untouched (verified
  orthogonal: restore never reads `_full_backups.json`; checkpoints embed no file names).
- **Builds on archived changes:** `2026-07-27-fix-broken-backing-chain` (design D3 —
  normalization policy), `2026-08-08-orthogonalize-snapshots-and-backups` (the change whose
  call-site rewrite introduced the regression).
- **No impact on:** ABC interfaces, `IVMModuleFactory` branches, config parsing, CLI
  surface, locking, snapshot world.
