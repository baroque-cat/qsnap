## MODIFIED Requirements

### Requirement: Shared timestamp parser

The system SHALL provide a `parse_timestamp(name: str, filepath: Path) -> datetime` function in `qsnap/utils/parsing.py`. It SHALL extract the timestamp from the filename using regex-based pattern matching, supporting all three configured timestamp formats:
- `long-iso`: `%Y%m%dT%H%M%S%z` (e.g. `20250713T153123+0200`)
- `long`: `%Y%m%dT%H%M` (e.g. `20250713T1531`) — default
- `short`: `%Y%m%d` (e.g. `20250713`)

The function SHALL search for timestamp patterns using `re.search()` in order of specificity (long-iso first, then long, then short), so that longer patterns are not shadowed by shorter ones. The function SHALL correctly handle:
- VM names containing dots (e.g. `3.Projects_opencode.20250713T1531_vda`)
- The `_{disk}` suffix in snapshot names (e.g. `_vda`, `_vdb`)
- Collision suffixes (e.g. `_1` appended to snapshot names)
- FULL backup names (e.g. `vm.FULL.20250713.qcow2`)

If no timestamp pattern is found, the function SHALL fall back to the file's `mtime`, and finally to `datetime.now()`.

The function SHALL NOT use `split(".")` to extract the timestamp segment, as VM names may contain dots.

#### Scenario: Parse long-format timestamp from snapshot name with disk suffix
- **WHEN** `parse_timestamp("vm.20250101T1200_vda", Path("/path/vm.20250101T1200_vda.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0)`

#### Scenario: Parse long-format timestamp from dotted VM name
- **WHEN** `parse_timestamp("3.Projects_opencode.20250713T1531_vda", Path("/path/3.Projects_opencode.20250713T1531_vda.qcow2"))` is called
- **THEN** it returns `datetime(2025, 7, 13, 15, 31)`

#### Scenario: Parse short-format timestamp
- **WHEN** `parse_timestamp("vm.20250101_vda", Path("/path/vm.20250101_vda.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 0, 0)`

#### Scenario: Parse long-iso-format timestamp with timezone offset
- **WHEN** `parse_timestamp("vm.20250101T120000+0200_vda", Path("/path/vm.20250101T120000+0200_vda.qcow2"))` is called
- **THEN** it returns a timezone-aware `datetime` corresponding to `2025-01-01T12:00:00+0200`

#### Scenario: Parse timestamp from FULL backup name
- **WHEN** `parse_timestamp("vm.FULL.20250101", Path("/path/vm.FULL.20250101.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 0, 0)`

#### Scenario: Parse timestamp with collision suffix
- **WHEN** `parse_timestamp("vm.20250101T1200_vda_1", Path("/path/vm.20250101T1200_vda_1.qcow2"))` is called
- **THEN** it returns `datetime(2025, 1, 1, 12, 0)`

#### Scenario: Fall back to file mtime
- **WHEN** the filename has no recognizable timestamp pattern
- **AND** the file exists with mtime `2025-06-15T08:30:00`
- **THEN** it returns the file's mtime

#### Scenario: Long-iso pattern takes priority over long
- **WHEN** `parse_timestamp("vm.20250101T120000+0200_vda", ...)` is called
- **THEN** the long-iso pattern (`%Y%m%dT%H%M%S%z`) is matched first
- **AND** the long pattern (`%Y%m%dT%H%M`) is NOT matched (would give wrong result)
