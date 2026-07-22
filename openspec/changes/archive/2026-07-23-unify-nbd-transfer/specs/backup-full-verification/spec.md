## MODIFIED Requirements

### Requirement: M3 — Content comparison tier

The M3 tier runs `qemu-img compare -q --force-share <source> <target>` — a chain-traversing byte-level content comparison. M3 is triggered by `verify_mode = "compare"` (was `"hash"`). The `"hash"` and `"full"` values are deprecated and treated as `"compare"`. M3 is available at the post-create lifecycle point (controlled by `GlobalConfig.full_verify_after_create`) and in `TargetConfig.verify` for post-transfer verification. A WARNING is logged when comparing a live source (the guest may write during the comparison).

#### Scenario: M3 triggered by compare mode

- **WHEN** `verify_mode = "compare"` and M1+M2 pass
- **THEN** `qemu-img compare -q --force-share <source> <target>` is executed
- **AND** the comparison traverses both backing chains

#### Scenario: Deprecated hash triggers compare

- **WHEN** `verify_mode = "hash"` (deprecated)
- **THEN** a WARNING is logged
- **AND** M3 is triggered (same as `"compare"`)
