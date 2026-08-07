# State Management — delta

## ADDED Requirements

### Requirement: Incremental dependency keys are key-format agnostic

`record_incremental_dependency(target_path, incremental_name, full_name)` SHALL accept
incremental names in both legacy format (snapshot names, e.g.
`vm.20260807T152956_vda_ec1148`) and backup-name format (freeze-ts names, e.g.
`vm.20260808T031542_vda_a1b2c3`). `get_incremental_dependencies()` SHALL return all recorded
incrementals for a FULL regardless of key format, and chain-length counting in Core SHALL
count entries without inspecting their format. No migration pass is required: mixed
generations coexist in `_dependencies.json` until legacy records expire through generation
rotation.

#### Scenario: Mixed-generation dependencies counted together

- **WHEN** a FULL has 3 legacy snapshot-keyed incrementals and 2 backup-named incrementals
- **THEN** `get_incremental_dependencies(target, full_name)` returns all 5 names
- **AND** the chain-length decision sees count 5

#### Scenario: Legacy records expire naturally

- **WHEN** retention deletes an old generation containing legacy-keyed dependencies
- **THEN** those dependency records are removed with the generation
- **AND** no explicit migration step is ever required
