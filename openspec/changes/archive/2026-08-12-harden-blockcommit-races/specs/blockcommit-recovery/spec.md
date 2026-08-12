# Blockcommit Recovery — delta

## MODIFIED Requirements

### Requirement: Per-disk chain verification reports broken file
`Core._verify_backing_chain(vm_config, disk)` SHALL verify the integrity of one disk's backing chain. `ChainVerifyResult` SHALL include a `broken_file: Path | None` field and a `disk: str` field. When chain verification fails due to a missing file, `broken_file` SHALL be set to the absolute path of the missing file REGARDLESS of the file's depth in the chain. When verification fails for other reasons (non-qcow2, cycle), `broken_file` SHALL be `None`. The result is diagnostic: it feeds the abort message (spec: broken chain aborts the VM pipeline) and the `check` command output; verification itself does not raise.

The broken-file walk (`_find_broken_chain_file`) SHALL be bounded dynamically by `max(64, measured_chain_length + 2)`, where the measured length comes from the failing scan's parsed chain when available and otherwise from the state snapshot count of the disk plus 8. The walk SHALL therefore always be able to reach the base image of any chain qsnap itself can describe; a fixed iteration cap that truncates the walk is not permitted.

#### Scenario: Broken file reported on missing file
- **WHEN** `qemu-img info --backing-chain` for disk `vda` fails because `s2.qcow2` does not exist
- **THEN** `ChainVerifyResult(success=False, broken_file=Path("/path/to/s2.qcow2"), disk="vda")` is returned

#### Scenario: No broken file on other failures
- **WHEN** chain verification for disk `vda` fails due to a cyclic reference
- **THEN** `ChainVerifyResult(success=False, broken_file=None, disk="vda")` is returned
- **AND** the caller aborts the VM pipeline (broken chain needs operator intervention)

#### Scenario: Broken file beyond depth 64 is still identified
- **WHEN** the backing chain is 73 layers deep and the missing file is layer 70 (counting from the active layer)
- **THEN** `broken_file` is set to the absolute path of the missing layer-70 file, not `None`

#### Scenario: Walk bound scales with measured chain length
- **WHEN** the failing scan parsed a chain of length 90 before detecting the break
- **THEN** the broken-file walk is allowed at least 92 iterations
