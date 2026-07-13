## ADDED Requirements

### Requirement: Per-disk change detection
`IChangeDetector.has_changed()` SHALL accept an optional `disk: str` parameter. When provided, change detection SHALL be scoped to that specific disk. When omitted, change detection SHALL apply to the first discovered disk (backward-compatible).

#### Scenario: Per-disk change detection for vdb
- **WHEN** `detector.has_changed(vm_config, disk="vdb")` is called
- **THEN** allocation comparison uses the `vdb` disk path from `virsh domblklist`

#### Scenario: Backward-compatible no-disk call
- **WHEN** `detector.has_changed(vm_config)` is called without `disk`
- **THEN** the first disk from `virsh domblklist` is used (existing behaviour)
