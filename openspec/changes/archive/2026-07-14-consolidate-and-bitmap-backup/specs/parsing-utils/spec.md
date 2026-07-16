## ADDED Requirements

### Requirement: Shared domblklist path parser
The system SHALL provide a `parse_domblklist_path(stdout: str) -> str` function in `qsnap/utils/parsing.py` that extracts the source path (last column) of the first data row from `virsh domblklist` output.

#### Scenario: Parse domblklist output with one disk
- **WHEN** `parse_domblklist_path("vda /path/to/image.qcow2\n")` is called
- **THEN** it returns `"/path/to/image.qcow2"`

#### Scenario: Parse domblklist with multiple lines
- **WHEN** domblklist output contains header lines and one data line
- **THEN** it skips header lines and returns the first data row's source path

#### Scenario: Parse empty domblklist output
- **WHEN** domblklist output has no data rows
- **THEN** it raises `ValueError` with a descriptive message

### Requirement: Shared domblklist target parser
The system SHALL provide a `parse_domblklist_target(stdout: str) -> str` function in `qsnap/utils/parsing.py` that extracts the target device name (first column) from `virsh domblklist` output.

#### Scenario: Parse target name from domblklist
- **WHEN** `parse_domblklist_target("vda /path/to/image.qcow2\n")` is called
- **THEN** it returns `"vda"`

### Requirement: Shared domblklist all-disks parser
The system SHALL provide a `parse_domblklist_disks(stdout: str) -> list[tuple[str, str]]` function that returns a list of `(target, source_path)` tuples for all disks.

#### Scenario: Parse multi-disk domblklist output
- **WHEN** the output contains `vda` and `vdb` data rows
- **THEN** it returns `[("vda", "/path/vda.qcow2"), ("vdb", "/path/vdb.qcow2")]`

### Requirement: Shared timestamp parser
The system SHALL provide a `parse_timestamp(name: str, filepath: Path) -> datetime` function in `qsnap/utils/parsing.py`. It SHALL attempt to parse `%Y%m%dT%H%M%S` from the filename suffix, fall back to file `mtime`, and finally to `datetime.now()`.

#### Scenario: Parse long-format timestamp from filename
- **WHEN** `parse_timestamp("vm.20250101T120000.qcow2", Path("/path/vm.20250101T120000.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0, 0)`

#### Scenario: Fall back to file mtime
- **WHEN** the filename has no recognizable timestamp
- **AND** the file exists with mtime `2025-06-15T08:30:00`
- **THEN** it returns the file's mtime

### Requirement: Modules use shared parsers
`ExternalSnapshotProvider`, `AllocationSizeDetector`, and `FileCopyBackupProvider` SHALL import from `qsnap.utils.parsing` instead of defining their own `_parse_domblklist_path`, `_parse_domblklist_target`, and `_parse_timestamp` helpers.

#### Scenario: ExternalSnapshotProvider uses shared parser
- **WHEN** inspecting `qsnap/modules/snapshot/external.py`
- **THEN** it imports `parse_domblklist_path` and `parse_timestamp` from `qsnap.utils.parsing`
- **THEN** no module-level `_parse_*` functions remain in the file
