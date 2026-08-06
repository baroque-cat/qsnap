## MODIFIED Requirements

### Requirement: Change detection via allocation-size comparison
The system SHALL determine whether a VM disk has changed by comparing the current allocation-size of the active image with the per-disk last recorded value from `IStateManager.get_last_allocation(vm_name, disk)`. The current allocation-size SHALL be determined via `qemu-img info --output=json --force-share` on the active image, whose path is obtained via `virsh domblklist`.

The default `change_detection_mode` in `VMConfig` SHALL be `"allocation-map"`. The `"allocation-size"` mode SHALL remain available as an explicit configuration option. The config parser SHALL apply the same default when the `change_detection_mode` key is absent from TOML.

#### Scenario: Allocation has grown — changes detected
- **WHEN** `IStateManager.get_last_allocation("myvm", "vda")` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 131072`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=65536, current_allocation=131072, disk="vda")`

#### Scenario: Allocation unchanged — no changes
- **WHEN** `IStateManager.get_last_allocation("myvm", "vda")` returns 65536
- **AND** `qemu-img info` on the active image returns `actual-size: 65536`
- **THEN** the module returns `ChangeResult(changed=False, last_allocation=65536, current_allocation=65536, disk="vda")`

#### Scenario: First run — no previous state for this disk
- **WHEN** `IStateManager.get_last_allocation("myvm", "vdb")` returns `None`
- **THEN** the module returns `ChangeResult(changed=True, last_allocation=0, current_allocation=0, disk="vdb")`
- **AND** this guarantees the first snapshot for this disk is created

#### Scenario: virsh or qemu-img command fails
- **WHEN** `virsh domblklist` or `qemu-img info` returns an error
- **THEN** the module returns `ChangeResult(changed=True)` with `disk` set to the queried disk (fail-safe: rather create an unnecessary snapshot than miss changes)

#### Scenario: Default change detection mode is allocation-map
- **WHEN** a `VMConfig` is constructed without an explicit `change_detection_mode`
- **THEN** `vm_config.change_detection_mode` equals `"allocation-map"`

#### Scenario: Config parsing applies the allocation-map default
- **WHEN** `ConfigFacade` parses a TOML `[[vm]]` section without a `change_detection_mode` key
- **THEN** the parsed `VMConfig.change_detection_mode` equals `"allocation-map"`
- **AND** `DefaultFactory.create_change_detector(vm_config.change_detection_mode)` returns `MapChangeDetector`

#### Scenario: Explicit allocation-size still works
- **WHEN** a `VMConfig` is constructed with `change_detection_mode = "allocation-size"`
- **THEN** `vm_config.change_detection_mode` equals `"allocation-size"`
- **AND** `DefaultFactory.create_change_detector("allocation-size")` returns `AllocationSizeDetector`
