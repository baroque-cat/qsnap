# deep-verification-circuit — Delta Spec

## MODIFIED Requirements

### Requirement: VMConfig deep verification fields

`VMConfig` SHALL include `blockcommit_deep_verify: bool` (default `False`). The `snapshot_deep_verify` field is REMOVED — it was parsed and stored but never consumed by any code path. Only `blockcommit_deep_verify` is wired into the lifecycle manager via the `deep_verify` keyword argument on `ILifecycleManager.blockcommit()`.

When `blockcommit_deep_verify = True`, the `deep_verify` keyword SHALL be passed to `manager.blockcommit()` in BOTH the deferred-commit path AND the main blockcommit path in `Core._blockcommit_snapshots()`. Previously, the main path silently omitted the `deep_verify` keyword, causing `blockcommit_deep_verify = True` to only take effect for deferred operations.

#### Scenario: Deep verify defaults to off

- **WHEN** `VMConfig` is constructed without deep verify fields
- **THEN** `blockcommit_deep_verify` is `False`
- **AND** `snapshot_deep_verify` does not exist on the dataclass

#### Scenario: Deep verify enabled for critical VM — main blockcommit path

- **WHEN** `blockcommit_deep_verify = true`
- **AND** Core executes the main blockcommit path in `_blockcommit_snapshots()`
- **THEN** `manager.blockcommit(vm_config, committable, deep_verify=True)` is called
- **AND** the lifecycle manager executes `qemu-img check` on the base image after commit

#### Scenario: Deep verify enabled for critical VM — deferred path

- **WHEN** `blockcommit_deep_verify = true`
- **AND** Core executes a deferred blockcommit
- **THEN** `manager.blockcommit(vm_config, committable, deep_verify=True)` is called
- **AND** behavior is unchanged from the previous deferred path
