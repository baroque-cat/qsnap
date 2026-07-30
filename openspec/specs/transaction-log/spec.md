# Transaction Log

## Purpose

Provides an optional structured, machine-readable transaction log file in btrbk-compatible format. Controlled by the `GlobalConfig.transaction_log` config field. Each `ActionRecord` produced during a pipeline run is appended as one line. A final `finished success` line marks the end of the run. The log is skipped in dry-run mode.

## Requirements

### Requirement: Transaction log config field

`GlobalConfig` SHALL support an optional `transaction_log: str | None` field. When `None` (default), no transaction log is written. When set to an absolute path, the system SHALL append one line per `ActionRecord` to that file in btrbk-compatible space-separated format.

#### Scenario: transaction_log not configured
- **WHEN** `transaction_log` is `None` or not present in the config
- **THEN** no transaction log file is created or written to

#### Scenario: transaction_log path is absolute
- **WHEN** `transaction_log = "/var/log/qsnap/transactions.log"` in the config
- **THEN** the system validates the path is absolute and writable (or logs a WARNING if the directory does not exist)

### Requirement: Transaction log file format

Each line in the transaction log SHALL follow the btrbk-compatible format: `localtime type status target_url source_url parent_url`. Fields SHALL be separated by a single space. Undefined fields SHALL use `-` as placeholder. The `type` field SHALL use qsnap action types: `snapshot`, `delete_snapshot`, `backup`, `backup_full`, `delete_backup`. The `status` field SHALL be `success` or `ERROR`.

#### Scenario: Snapshot creation log line
- **WHEN** a snapshot `testvm.20260718T140000_vda_a1b2c3.qcow2` is successfully created at `/var/lib/libvirt/snapshots/testvm/`
- **THEN** the log line is: `<localtime> snapshot success - /var/lib/libvirt/snapshots/testvm/testvm.20260718T140000_vda_a1b2c3.qcow2 - -`

#### Scenario: Snapshot deletion log line
- **WHEN** snapshot `testvm.20260718T080000_vda_a1b2c3` is blockcommitted and removed
- **THEN** the log line is: `<localtime> delete_snapshot success - /var/lib/libvirt/snapshots/testvm/testvm.20260718T080000_vda_a1b2c3.qcow2 - -`

#### Scenario: Backup transfer log line
- **WHEN** incremental backup `testvm.20260718T140000_vda_a1b2c3.qcow2` is transferred to `/snapshots/backup/vm/testvm/`
- **THEN** the log line is: `<localtime> backup success /snapshots/backup/vm/testvm/testvm.20260718T140000_vda_a1b2c3.qcow2 /var/lib/libvirt/snapshots/testvm/testvm.20260718T140000_vda_a1b2c3.qcow2 -`

#### Scenario: FULL backup log line
- **WHEN** FULL backup `testvm.FULL.20260718T000000_a1b2c3.qcow2` is created at `/snapshots/backup/vm/testvm/`
- **THEN** the log line is: `<localtime> backup_full success /snapshots/backup/vm/testvm/testvm.FULL.20260718T000000_a1b2c3.qcow2 /var/lib/libvirt/snapshots/testvm/testvm.20260718T140000_vda_a1b2c3.qcow2 -`

#### Scenario: Error log line
- **WHEN** a backup transfer fails
- **THEN** the log line uses `status=ERROR` and includes the error message in the `parent_url` field as `# <error>`

#### Scenario: Finished log line
- **WHEN** `qsnap run` completes
- **THEN** a final log line is written: `<localtime> finished success - - -`

#### Scenario: Transaction log not written in dry-run
- **WHEN** `qsnap -n run` is executed
- **THEN** no transaction log lines are written (even if `transaction_log` is configured)

### Requirement: TransactionWriter as stateless utility

The transaction log writer SHALL be implemented as a `TransactionWriter` class in `qsnap/utils/transaction.py` with a static method `write(path: Path, record: ActionRecord) -> None`. It SHALL append a single line to the file. It SHALL NOT buffer lines in memory. It SHALL accept only `Path` and `ActionRecord` — no knowledge of Core, pipeline, or config.

#### Scenario: Writer appends to existing file
- **WHEN** `TransactionWriter.write(path, record)` is called with a path to an existing file
- **THEN** one line is appended to the file
- **AND** existing content is preserved

#### Scenario: Writer creates file if it does not exist
- **WHEN** `TransactionWriter.write(path, record)` is called with a path to a non-existent file
- **THEN** the file is created with one line

#### Scenario: Writer has no dependency on Core
- **WHEN** the `TransactionWriter` class is inspected
- **THEN** it has no import from `qsnap.core`, `qsnap.config`, or `qsnap.modules`
