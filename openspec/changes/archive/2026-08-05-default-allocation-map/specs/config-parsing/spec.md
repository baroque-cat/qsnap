## ADDED Requirements

### Requirement: change_detection_mode parse-time default
`ConfigFacade` SHALL parse the optional `[[vm]]` key `change_detection_mode`. When the key is absent, the parsed `VMConfig.change_detection_mode` SHALL equal the spec default `"allocation-map"` (matching the `VMConfig` dataclass default). When the key is present, its string value SHALL be passed through unchanged.

#### Scenario: Absent key parses to allocation-map default
- **WHEN** `ConfigFacade` parses a TOML file whose `[[vm]]` section does not set `change_detection_mode`
- **THEN** the resulting `VMConfig.change_detection_mode` equals `"allocation-map"`

#### Scenario: Explicit allocation-size is preserved
- **WHEN** `ConfigFacade` parses a TOML file whose `[[vm]]` section sets `change_detection_mode = "allocation-size"`
- **THEN** the resulting `VMConfig.change_detection_mode` equals `"allocation-size"`
