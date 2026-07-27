## MODIFIED Requirements

### Requirement: IStateManager tracks incremental-to-FULL dependencies

`IStateManager` SHALL provide `record_incremental_dependency(target_path: str, incremental_name: str, full_name: str)` and `get_incremental_dependencies(target_path: str, full_name: str) -> list[str]`. Dependencies SHALL be persisted across runs.

The `full_name` parameter in `get_incremental_dependencies`, `remove_incremental_dependency`, and `remove_all_incremental_dependencies` SHALL accept both stem form (`vm.FULL.20260727`) and extended form (`vm.FULL.20260727.qcow2`). When the extended form is passed, the implementation SHALL normalize it to stem form before lookup, because the storage format uses stem (as produced by `_resolve_chain_full_anchor`).

#### Scenario: Dependency recorded after rebase
- **WHEN** an incremental is rebased to FULL `vm.FULL.20260701.qcow2`
- **THEN** `get_incremental_dependencies(target_path, "vm.FULL.20260701.qcow2")` includes the incremental's name
- **AND** `get_incremental_dependencies(target_path, "vm.FULL.20260701")` also includes the incremental's name (stem form works too)

#### Scenario: Multiple incrementals depend on same FULL
- **WHEN** three incrementals are rebased to the same FULL
- **THEN** `get_incremental_dependencies()` returns a list of 3 names

#### Scenario: Lookup with stem key finds dependencies stored with stem
- **WHEN** `record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")` is called (stem form)
- **AND** `get_incremental_dependencies(target, "vm.FULL.20260727.qcow2")` is called (extended form)
- **THEN** the method returns `["incr-001"]` (normalization makes both forms equivalent)

#### Scenario: Lookup with extended key finds dependencies stored with stem
- **WHEN** `record_incremental_dependency(target, "incr-001", "vm.FULL.20260727")` is called (stem form)
- **AND** `get_incremental_dependencies(target, "vm.FULL.20260727")` is called (stem form)
- **THEN** the method returns `["incr-001"]`

## ADDED Requirements

### Requirement: Legacy dependency key migration on load

`JsonStateManager._load_dependencies()` SHALL migrate `_dependencies.json` keys from the extended form (with `.qcow2` extension) to the stem form (without `.qcow2`) on load. For each target path's dependency dict, any key ending in `.qcow2` SHALL be renamed to its stem form, preserving the value list. Migration SHALL be idempotent — loading an already-migrated file produces no changes.

#### Scenario: Legacy .qcow2 keys migrated to stem on load
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727.qcow2": ["incr-001"]}}`
- **THEN** on load, the key is migrated to `"vm.FULL.20260727"` (stem form)
- **AND** `get_incremental_dependencies("target", "vm.FULL.20260727")` returns `["incr-001"]`

#### Scenario: Already-migrated file loaded unchanged
- **WHEN** `_dependencies.json` contains `{"target": {"vm.FULL.20260727": ["incr-001"]}}` (stem keys)
- **THEN** on load, no migration occurs and the data is returned as-is

#### Scenario: Mixed keys migrated correctly
- **WHEN** `_dependencies.json` contains both `"vm.FULL.20260727.qcow2"` and `"vm.FULL.20260715"` keys
- **THEN** on load, the `.qcow2` key is migrated to stem form
- **AND** the already-stem key is left unchanged
