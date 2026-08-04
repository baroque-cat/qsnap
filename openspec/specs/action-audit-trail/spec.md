# Action Audit Trail

## Purpose

Provides an immutable audit trail of pipeline actions (snapshot creation, deletion, backup transfer, FULL creation, backup deletion, errors) via the `ActionRecord` frozen dataclass. Accumulated by Core during `_run_pipeline()` in `self._actions` and carried in `PipelineResult.actions`. Consumed by the CLI summary formatter (`qsnap.cli.summary.format_summary`) and the optional transaction log writer (`qsnap.utils.transaction.TransactionWriter`).

## Requirements

### Requirement: ActionRecord dataclass

The system SHALL provide an `ActionRecord` frozen dataclass in `qsnap/models/results.py` with fields: `action: str` (one of `"snapshot_create"`, `"snapshot_delete"`, `"backup_transfer"`, `"backup_full"`, `"backup_delete"`, `"error"`), `vm_name: str`, `name: str`, `path: Path`, `size: int = 0`, `duration: float = 0.0`, `error: str | None = None`, and `disk: str | None = None`. The `disk` field identifies the disk target (e.g. `"vda"`) the action applies to. Every disk-scoped action record SHALL carry its disk; VM-level records (e.g. `action="error"` for a whole-VM failure) SHALL carry `disk=None`.

#### Scenario: ActionRecord is immutable
- **WHEN** an `ActionRecord` is constructed with `action="snapshot_create"`, `vm_name="testvm"`, `name="testvm.20260701T1200_vda"`
- **THEN** all fields are accessible but cannot be mutated (frozen dataclass)

#### Scenario: ActionRecord size and duration default to zero
- **WHEN** an `ActionRecord` is constructed with only required fields
- **THEN** `record.size` is `0` and `record.duration` is `0.0`

#### Scenario: ActionRecord carries disk
- **WHEN** an `ActionRecord` is constructed with `action="snapshot_create"`, `vm_name="testvm"`, `name="testvm.20260701T120000_vda_a1b2c3"`, `disk="vda"`
- **THEN** `record.disk` is `"vda"`

#### Scenario: VM-level error record has no disk
- **WHEN** an `ActionRecord` is constructed with `action="error"` for a whole-VM failure and no `disk` argument
- **THEN** `record.disk` is `None`

### Requirement: ActionRecord accumulation in Core

Core SHALL maintain `self._actions: list[ActionRecord]` as an in-memory list cleared at the start of each `_run_pipeline()` call. After each pipeline step that produces a side-effect, Core SHALL append the corresponding `ActionRecord`. Every disk-scoped action record SHALL be appended with its `disk` field populated from the disk being processed.

#### Scenario: Core clears actions at start of run
- **WHEN** `_run_pipeline()` begins execution
- **THEN** `self._actions` is reset to an empty list

#### Scenario: Core appends action on snapshot create
- **WHEN** `_create_snapshot()` successfully creates a snapshot via `provider.create()` for disk `disk.target`
- **AND** the snapshot is recorded in state via `record_snapshot()`
- **THEN** an `ActionRecord(action="snapshot_create", name=<snapshot_name>, path=<snapshot_path>, size=<allocation>, duration=<elapsed>, disk=<disk.target>)` is appended to `self._actions`

#### Scenario: Core appends action on snapshot delete (blockcommit)
- **WHEN** `_blockcommit_snapshots()` successfully merges snapshots of a disk and post-commit cleanup calls `remove_snapshot()`
- **THEN** for each removed snapshot, an `ActionRecord(action="snapshot_delete", name=<snapshot_name>, path=<snapshot_path>, disk=<disk>)` is appended, where `<disk>` is the disk target of the blockcommit group

#### Scenario: Core appends action on backup transfer
- **WHEN** `_backup_target()` successfully transfers an incremental via `transfer_missing()`
- **THEN** for each transferred `BackupResult(success=True)`, an `ActionRecord(action="backup_transfer", name=<name>, path=<target_path>, size=<bytes_transferred>, duration=<elapsed>, disk=<BackupResult.disk>)` is appended

#### Scenario: Core appends action on FULL backup creation
- **WHEN** `_backup_target()` successfully creates and verifies a FULL backup for a disk
- **THEN** an `ActionRecord(action="backup_full", name=<full_name>, path=<target_path>, size=<file_size>, disk=<disk>)` is appended

#### Scenario: Core appends action on backup deletion
- **WHEN** `_cleanup_backups()` successfully deletes a backup file via `provider.delete()`
- **THEN** an `ActionRecord(action="backup_delete", name=<backup_name>, path=<target_path>, disk=<backup.disk>)` is appended

#### Scenario: Core appends error action on failure
- **WHEN** any pipeline step produces a failure (snapshot creation fail, transfer fail, verification fail)
- **THEN** an `ActionRecord(action="error", vm_name=<vm>, name=<name>, path=<path>, error=<error_message>)` is appended, with `disk` populated when the failure is scoped to a single disk and `None` for whole-VM failures

#### Scenario: Core does not append actions in dry-run for mutations
- **WHEN** `self._dry_run` is `True` and `_create_snapshot()` is called
- **THEN** no `ActionRecord` is appended (snapshot was not actually created)
- **AND** the dry-run prediction is logged via existing dry-run INFO messages

### Requirement: PipelineResult carries actions

`PipelineResult` SHALL have a field `actions: list[ActionRecord]`, populated from `self._actions` at the end of `_run_pipeline()`. The field SHALL be present in both `run()` and `snapshot()` pipeline paths.

#### Scenario: PipelineResult includes actions after successful run
- **WHEN** `core.run()` completes with 2 snapshots created, 1 blockcommitted, 3 backups transferred
- **THEN** `result.actions` contains 6 `ActionRecord` entries in pipeline execution order

#### Scenario: PipelineResult includes error actions
- **WHEN** `core.run()` completes with 1 backup transfer failure
- **THEN** `result.actions` contains an `ActionRecord(action="error", ...)` for the failed transfer
