# Parsing Utilities

## Purpose
Shared parsing functions for `virsh domblklist` output, snapshot/backup names, and timestamps. Pure functions with no side effects (except `parse_timestamp` which reads file `mtime` as a fallback). Located in `qsnap/utils/parsing.py` with complementary NBD utilities in `qsnap/utils/nbd.py`.

## Requirements

### Requirement: parse_domblklist_disks — all disks
The system SHALL provide a `parse_domblklist_disks(stdout: str) -> list[tuple[str, str]]` function that returns a list of `(target, source_path)` tuples for all disks in `virsh domblklist` output.

#### Scenario: Parse multi-disk domblklist output
- **WHEN** the output contains `vda` and `vdb` data rows
- **THEN** it returns `[("vda", "/path/vda.qcow2"), ("vdb", "/path/vdb.qcow2")]`

#### Scenario: Parse empty domblklist output
- **WHEN** domblklist output has no data rows
- **THEN** it returns an empty list `[]`

#### Scenario: Parse domblklist with header lines
- **WHEN** domblklist output contains header lines and separator dashes
- **THEN** header and separator lines are skipped, only data rows are returned

### Requirement: parse_domblklist_path_map — target-to-path mapping
The system SHALL provide a `parse_domblklist_path_map(stdout: str) -> dict[str, str]` function that parses `virsh domblklist` output into a dictionary keyed by disk target device name (e.g. `"vda"`), mapping to source paths.

#### Scenario: Parse domblklist into path map
- **WHEN** the output contains `vda /path/vda.qcow2` and `vdb /path/vdb.qcow2`
- **THEN** it returns `{"vda": "/path/vda.qcow2", "vdb": "/path/vdb.qcow2"}`

#### Scenario: Parse empty output into empty dict
- **WHEN** domblklist output has no data rows
- **THEN** it returns an empty dict `{}`

### Requirement: parse_domblklist_path_for_disk — per-disk lookup
The system SHALL provide a `parse_domblklist_path_for_disk(stdout: str, disk: str) -> str` function that extracts the source path for a specific disk target from `virsh domblklist` output. It SHALL raise `ValueError` when no data row matches the given disk.

#### Scenario: Lookup path for existing disk
- **WHEN** `parse_domblklist_path_for_disk("vda /path/vda.qcow2\nvdb /path/vdb.qcow2", "vdb")` is called
- **THEN** it returns `"/path/vdb.qcow2"`

#### Scenario: Lookup path for non-existent disk raises ValueError
- **WHEN** `parse_domblklist_path_for_disk("vda /path/vda.qcow2", "vdz")` is called
- **THEN** it raises `ValueError` with a message indicating the disk was not found

### Requirement: parse_disk_from_snapshot_name
The system SHALL provide a `parse_disk_from_snapshot_name(name: str) -> str | None` function that extracts the disk target from a snapshot or backup filename. Snapshot names follow the pattern `{vm}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2` (e.g. `myvm.20250713T153123_vda_a1b2c3.qcow2`). The function SHALL return the `{disk}` segment (e.g. `"vda"`), or `None` when the name does not contain a recognizable disk segment. It SHALL anchor on the timestamp pattern so VM names containing dots or underscores are handled correctly.

#### Scenario: Extract disk from snapshot name
- **WHEN** `parse_disk_from_snapshot_name("myvm.20250713T153123_vda_a1b2c3.qcow2")` is called
- **THEN** it returns `"vda"`

#### Scenario: Extract disk from name with dotted VM name
- **WHEN** `parse_disk_from_snapshot_name("3.Projects_opencode.20250713T153123_vdb_a1b2c3.qcow2")` is called
- **THEN** it returns `"vdb"`

#### Scenario: Return None for name without disk segment
- **WHEN** `parse_disk_from_snapshot_name("myvm-old-format.qcow2")` is called
- **THEN** it returns `None`

#### Scenario: Extract disk from FULL backup name
- **WHEN** `parse_disk_from_snapshot_name("myvm.FULL.20250713T153123_vda_a1b2c3.qcow2")` is called
- **THEN** it returns `"vda"`

### Requirement: parse_timestamp
The system SHALL provide a `parse_timestamp(name: str, filepath: Path) -> datetime` function. It SHALL extract the timestamp from the filename using regex-based pattern matching, supporting the unified timestamp format `YYYYMMDDTHHMMSS` (e.g. `20250713T153123`) — seconds resolution, no timezone offset.

The function SHALL search for the pattern using `re.search()`. It SHALL correctly handle:
- VM names containing dots (e.g. `3.Projects_opencode.20250713T153123_vda`)
- The `_{disk}` suffix in snapshot names (e.g. `_vda`, `_vdb`)
- The `_{6hex}` collision-resistant suffix (e.g. `_a1b2c3`)
- Collision suffixes (e.g. `_1` appended to snapshot names)
- FULL backup names (e.g. `vm.FULL.20250713T153123_a1b2c3.qcow2`)

If no timestamp pattern is found, the function SHALL fall back to the file's `mtime`, and finally to `datetime.now()`.

The function SHALL NOT use `split(".")` to extract the timestamp segment, as VM names may contain dots.

#### Scenario: Parse unified-format timestamp from snapshot name with disk suffix
- **WHEN** `parse_timestamp("vm.20250101T120000_vda", Path("/path/vm.20250101T120000_vda.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0, 0)`

#### Scenario: Parse unified-format timestamp from dotted VM name
- **WHEN** `parse_timestamp("3.Projects_opencode.20250713T153123_vda", Path("/path/3.Projects_opencode.20250713T153123_vda.qcow2"))` is called
- **THEN** it returns `datetime(2025, 7, 13, 15, 31, 23)`

#### Scenario: Parse timestamp from FULL backup name with hex suffix
- **WHEN** `parse_timestamp("vm.FULL.20250101T120000_abc123", Path("/path/vm.FULL.20250101T120000_abc123.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0, 0)`

#### Scenario: Parse timestamp with collision suffix
- **WHEN** `parse_timestamp("vm.20250101T120000_vda_abc123_1", Path("/path/vm.20250101T120000_vda_abc123_1.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0, 0)`

#### Scenario: Fall back to file mtime
- **WHEN** the filename has no recognizable timestamp pattern
- **AND** the file exists with mtime `2025-06-15T08:30:00`
- **THEN** it returns the file's mtime

#### Scenario: Fall back to datetime.now when mtime fails
- **WHEN** the filename has no timestamp pattern and the file cannot be stat'd
- **THEN** it returns `datetime.now()`

### Requirement: get_disk_targets — all disks from domain XML
The system SHALL provide a `get_disk_targets(shell: IShell, vm_name: str) -> list[tuple[str, str]]` function in `qsnap/utils/nbd.py` that returns all disk `(target, source_path)` pairs for a VM. It SHALL parse `virsh domblklist --domain <vm> --details` output, filtering for rows whose Device column is `"disk"` (excluding cdrom/floppy devices). It SHALL return an empty list when the command fails or no disks are found.

#### Scenario: Get all disk targets from domblklist --details
- **WHEN** `virsh domblklist --details` returns two rows with Device `"disk"` and one with Device `"cdrom"`
- **THEN** `get_disk_targets` returns two `(target, source_path)` tuples, none for the cdrom

#### Scenario: Command failure returns empty list
- **WHEN** `virsh domblklist --details` fails
- **THEN** `get_disk_targets` returns an empty list `[]`

### Requirement: write_backup_xml with disk parameter
The system SHALL provide a `write_backup_xml(socket_path: str, incremental: str | None = None, disk: str | None = None) -> Path` function that writes a libvirt pull-model backup XML to a temp file. When `disk` is non-`None`, the XML SHALL include a `<disks>` element restricting the export to that single disk target (e.g. `<disk name='vda'/>`). When `disk` is `None`, all disks are exported. When `incremental` is non-`None`, an `<incremental>` element SHALL be included.

#### Scenario: Backup XML with disk filter
- **WHEN** `write_backup_xml("/tmp/sock", disk="vda")` is called
- **THEN** the generated XML contains `<disks><disk name='vda'/></disks>` and no `<incremental>` element

#### Scenario: Backup XML with incremental and disk filter
- **WHEN** `write_backup_xml("/tmp/sock", incremental="chk-123", disk="vdb")` is called
- **THEN** the generated XML contains both `<incremental>chk-123</incremental>` and `<disks><disk name='vdb'/></disks>`

#### Scenario: Backup XML without disk filter exports all disks
- **WHEN** `write_backup_xml("/tmp/sock")` is called with no `disk` argument
- **THEN** the generated XML contains no `<disks>` element
