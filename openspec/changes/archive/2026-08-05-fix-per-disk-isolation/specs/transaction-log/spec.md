## ADDED Requirements

### Requirement: Transaction log line format is frozen and disk-aware via paths

The transaction log line format SHALL remain exactly six space-separated fields: `localtime type status target_url source_url parent_url`. No additional field (including a disk column) SHALL be added — the format is a btrbk compatibility contract. The disk an action applies to is already encoded in the `target_url` / `source_url` file paths via the per-disk naming conventions (`{vm}.{ts}_{disk}_{6hex}.qcow2` and `{vm}.FULL.{ts}_{disk}_{6hex}.qcow2`). `TransactionWriter.write()` SHALL NOT read `ActionRecord.disk` to alter the line structure; the disk field may only appear inside the URLs as part of the file names.

#### Scenario: Snapshot line keeps six fields with disk in path
- **WHEN** a snapshot `testvm.20260718T140000_vda_a1b2c3.qcow2` is created and logged
- **THEN** the line has exactly six space-separated fields
- **AND** the disk `vda` appears only inside the `source_url` file name

#### Scenario: Backup transfer line keeps six fields with disk in path
- **WHEN** an incremental backup `testvm.20260718T140000_vdb_d4e5f6.qcow2` is transferred and logged
- **THEN** the line has exactly six space-separated fields
- **AND** the disk `vdb` appears only inside the `target_url` and `source_url` file names

#### Scenario: VM-level error line unchanged
- **WHEN** an `ActionRecord(action="error", disk=None)` is logged
- **THEN** the line uses the same six-field structure with `status=ERROR` and the error in the `parent_url` field as `# <error>`
