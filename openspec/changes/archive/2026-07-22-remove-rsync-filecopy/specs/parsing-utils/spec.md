## MODIFIED Requirements

### Requirement: Modules use shared parsers
`ExternalSnapshotProvider` and `AllocationSizeDetector` SHALL import from `qsnap.utils.parsing` instead of defining their own `_parse_domblklist_path`, `_parse_domblklist_target`, and `_parse_timestamp` helpers.

#### Scenario: ExternalSnapshotProvider uses shared parser
- **WHEN** inspecting `qsnap/modules/snapshot/external.py`
- **THEN** it imports `parse_domblklist_path` and `parse_timestamp` from `qsnap.utils.parsing`
- **THEN** no module-level `_parse_*` functions remain in the file
