## MODIFIED Requirements

### Requirement: GlobalConfig contains snapshot_preserve_min and target_preserve_min

`GlobalConfig` SHALL contain fields `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None`.

#### Scenario: Defaults are None
- **WHEN** `GlobalConfig` is constructed with no preserve_min keys
- **THEN** `snapshot_preserve_min` and `target_preserve_min` are both `None`

### Requirement: VMConfig contains snapshot_preserve_min and target_preserve_min

`VMConfig` SHALL contain fields `snapshot_preserve_min: str | None = None` and `target_preserve_min: str | None = None`.

#### Scenario: VM inherits from global
- **WHEN** global sets `snapshot_preserve_min = "3h"` and VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `"3h"`

### Requirement: TargetConfig contains target_preserve_min, full_every, and full_compress

`TargetConfig` SHALL contain fields `target_preserve_min: str | None = None`, `full_every: str = "0d"`, and `full_compress: bool = False`.

#### Scenario: Target inherits from VM
- **WHEN** VM sets `target_preserve_min = "6h"` and target omits it
- **THEN** `TargetConfig.target_preserve_min` resolves to `"6h"`

#### Scenario: full_every disabled by default
- **WHEN** target omits `full_every`
- **THEN** `TargetConfig.full_every` is `"0d"`
