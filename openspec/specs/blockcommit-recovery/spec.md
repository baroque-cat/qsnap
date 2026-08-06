# Blockcommit Recovery

## Purpose

Diagnoses broken snapshot chains before blockcommit and aborts the affected VM's pipeline so an operator can intervene. Chain verification (`_verify_backing_chain`) reports the broken file for the CRITICAL abort message and for the `check` command. No automatic recovery is attempted — partial blockcommit and auto-rebase were removed as too risky to run unattended.

## Requirements
### Requirement: Per-disk chain verification reports broken file
`Core._verify_backing_chain(vm_config, disk)` SHALL verify the integrity of one disk's backing chain. `ChainVerifyResult` SHALL include a `broken_file: Path | None` field and a `disk: str` field. When chain verification fails due to a missing file, `broken_file` SHALL be set to the absolute path of the missing file. When verification fails for other reasons (non-qcow2, cycle), `broken_file` SHALL be `None`. The result is diagnostic: it feeds the abort message (spec: broken chain aborts the VM pipeline) and the `check` command output; verification itself does not raise.

#### Scenario: Broken file reported on missing file
- **WHEN** `qemu-img info --backing-chain` for disk `vda` fails because `s2.qcow2` does not exist
- **THEN** `ChainVerifyResult(success=False, broken_file=Path("/path/to/s2.qcow2"), disk="vda")` is returned

#### Scenario: No broken file on other failures
- **WHEN** chain verification for disk `vda` fails due to a cyclic reference
- **THEN** `ChainVerifyResult(success=False, broken_file=None, disk="vda")` is returned
- **AND** the caller aborts the VM pipeline (broken chain needs operator intervention)

### Requirement: Broken chain aborts the VM pipeline
When `_verify_backing_chain(vm_config, disk)` returns `ChainVerifyResult(success=False)` before blockcommit, Core SHALL emit a CRITICAL log and raise `RuntimeError`, aborting the remaining steps of this VM (spec: `core-orchestrator`, VM-level failure isolation). The CRITICAL message SHALL include the broken file path when known (`Break at: {broken_file}`) and the remediation hint to run `qsnap check --deep`. No partial blockcommit, auto-rebase, or other automatic recovery SHALL be attempted — a broken chain is an operator matter.

#### Scenario: Broken chain aborts with diagnostics
- **WHEN** disk `vda`'s chain is `base -> s1 -> s2(broken) -> s3 -> active` and retention marks s1 for removal
- **THEN** a CRITICAL log is emitted with `Break at: <path to s2>` and the `qsnap check --deep` hint
- **AND** `RuntimeError` is raised, aborting the remaining steps of this VM
- **AND** no blockcommit or rebase is executed for any disk of this VM

#### Scenario: Other VMs are unaffected
- **WHEN** the pipeline aborts on a broken chain for "vm1"
- **THEN** "vm2" is processed normally

