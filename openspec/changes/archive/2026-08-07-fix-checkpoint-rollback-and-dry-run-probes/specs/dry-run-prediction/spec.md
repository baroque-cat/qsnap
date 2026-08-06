## MODIFIED Requirements

### Requirement: FULL backup prediction with size estimate

In dry-run mode, when the per-disk FULL decision determines a FULL would be created, Core SHALL log an INFO prediction containing the disk target, the transfer method, the VM running state, and an estimated standalone size computed read-only as the sum of `actual-size` over the source snapshot's backing chain (`qemu-img info --force-share --backing-chain --output=json`). The chain-size estimation logic SHALL be shared with `Core.fork()` via a single helper. When the source snapshot file does not exist (a simulated snapshot), the estimate SHALL fall back to the disk's `base_image` backing chain, which exists by pre-flight validation and which a real FULL would export plus a near-zero fresh overlay. The same fallback SHALL apply to the dry-run free-space gate estimate so prediction and gate never disagree. Estimation probe failures SHALL NOT log above DEBUG. When the estimation command fails (including the fallback), the prediction SHALL still be emitted with size unknown.

#### Scenario: FULL prediction carries chain size estimate
- **WHEN** dry-run predicts a FULL for disk `vda` sourced from a snapshot whose backing chain sums to 1 GiB of `actual-size`
- **THEN** the prediction log includes the disk, method, VM state, and an approximate size of 1 GiB

#### Scenario: Estimation failure degrades gracefully
- **WHEN** the `qemu-img info --backing-chain` call fails during dry-run
- **THEN** the FULL prediction is still logged, with the size marked unknown
- **AND** the pipeline does not abort

#### Scenario: First-run dry-run falls back to base_image
- **WHEN** dry-run predicts a FULL sourced from a simulated snapshot whose file does not yet exist
- **THEN** the estimate is computed from the disk's `base_image` backing chain
- **AND** the prediction carries a numeric size estimate (not "size unknown") when `base_image` is readable

#### Scenario: Simulated-path probe does not log ERROR
- **WHEN** dry-run estimates a FULL size and the source snapshot file does not exist
- **THEN** no log record above DEBUG is emitted for the simulated-path probe
- **AND** no "Cannot estimate FULL size" WARNING is emitted solely because the simulated file is absent
