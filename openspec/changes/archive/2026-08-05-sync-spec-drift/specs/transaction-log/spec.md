## ADDED Requirements

### Requirement: ActionRecord action to transaction-log type mapping

`TransactionWriter.write()` SHALL map `ActionRecord.action` to the btrbk-compatible log `type` via the fixed mapping (`_TYPE_MAP` in `qsnap/utils/transaction.py`): `snapshot_create` → `snapshot`, `snapshot_delete` → `delete_snapshot`, `backup_transfer` → `backup`, `backup_full` → `backup_full`, `backup_delete` → `delete_backup`. For `action == "error"`, the `type` SHALL be `error` and the `status` SHALL be `ERROR` (the error message is carried in the `parent_url` field as `# <error_message>`). Any other action value SHALL fall back to `type` `unknown` with `status` `success`. Snapshot actions (`snapshot_create`, `snapshot_delete`) SHALL store their path in `source_url`; backup actions (`backup_transfer`, `backup_full`, `backup_delete`) SHALL store their path in `target_url`; unused URL fields SHALL be `-`.

#### Scenario: Snapshot create maps to btrbk type snapshot
- **WHEN** `TransactionWriter.write()` is called with `ActionRecord(action="snapshot_create", path=<p>)`
- **THEN** the log line's type is `snapshot`, status is `success`, and `source_url` is `<p>`

#### Scenario: Backup transfer maps to btrbk type backup
- **WHEN** `TransactionWriter.write()` is called with `ActionRecord(action="backup_transfer", path=<p>)`
- **THEN** the log line's type is `backup`, status is `success`, and `target_url` is `<p>`

#### Scenario: Error records use type error
- **WHEN** `TransactionWriter.write()` is called with `ActionRecord(action="error", error="disk full")`
- **THEN** the log line's type is `error`, status is `ERROR`, and `parent_url` is `# disk full`

#### Scenario: Unknown action falls back to unknown
- **WHEN** `TransactionWriter.write()` is called with an action not in the mapping and not `error`
- **THEN** the log line's type is `unknown` and status is `success`
