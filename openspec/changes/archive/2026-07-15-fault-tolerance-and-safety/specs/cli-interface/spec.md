## ADDED Requirements

### Requirement: list config shows per-VM safety settings
`qsnap list config` SHALL display per-VM safety columns: `blockcommit_deep_verify` (ON/OFF) and `snapshot_deep_verify` (ON/OFF). Global safety settings (`auto_cleanup`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule`) SHALL be shown in a header or summary section.

#### Scenario: list config shows OFF for default deep verify
- **WHEN** `qsnap list config` is executed and no VM has deep verify enabled
- **THEN** each VM shows `blockcommit_deep_verify: OFF`, `snapshot_deep_verify: OFF`

#### Scenario: list config shows ON for enabled deep verify
- **WHEN** VM "critical-db" has `blockcommit_deep_verify = true`
- **THEN** `qsnap list config` shows `blockcommit_deep_verify: ON` for that VM

### Requirement: qsnap check reports safety configuration status
`qsnap check` output SHALL include a summary of current safety configuration: whether `auto_cleanup`, chain verification, and deep check schedule are active.

#### Scenario: check output shows disabled safety features
- **WHEN** `deep_check_schedule = "off"` and `qsnap check` is executed
- **THEN** output includes "Deep check schedule: OFF" or equivalent

### Requirement: qsnap check --deep provides per-image results
`qsnap check --deep` SHALL run `qemu-img check --output=json` on every snapshot and backup. See `specs/deep-verification-circuit/spec.md`.

#### Scenario: Deep check exit code
- **WHEN** all images pass with 0 corruptions
- **THEN** exit code is 0
- **WHEN** any image has corruptions > 0 but is readable
- **THEN** exit code is still 0 (WARNING, non-fatal)
- **WHEN** any image is unreadable
- **THEN** exit code is 1
