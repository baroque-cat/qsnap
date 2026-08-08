# Per-Chain Retention — delta

## ADDED Requirements

### Requirement: Immediate retirement of recovery-superseded generations

When a bitmap-loss recovery (capability `bitmap-loss-recovery`) produces a FULL fallback, the generation that the dead checkpoint referenced SHALL be retired immediately after the new FULL passes verification, regardless of `keep_generations`. The retirement SHALL delete the superseded FULL and all its incremental dependents. The verify-before-delete gates SHALL still apply to the deleted FULL (M1 always, M2 per `full_verify_before_delete`): a corrupt superseded FULL SHALL be blocked from deletion and reported CRITICAL, preserving it for operator review. Recovered-delta recoveries SHALL NOT retire any generation — the existing chain remains the foundation. Outside the recovery path, `keep_generations` semantics SHALL remain unchanged.

#### Scenario: Recovery FULL retires the old generation immediately

- **WHEN** a recovery FULL passes verification while `keep_generations` is 2
- **THEN** the superseded generation (old FULL plus its incrementals) is deleted in the same run
- **AND** the verify-before-delete gates were applied to the deleted FULL

#### Scenario: Corrupt superseded FULL is preserved

- **WHEN** the superseded FULL fails the M1 or configured M2 gate at retirement time
- **THEN** it is NOT deleted
- **AND** a CRITICAL log directs the operator to deep verification

#### Scenario: Recovered delta retires nothing

- **WHEN** recovery completes via the recovered-delta path
- **THEN** no backup generation is deleted by the recovery itself

#### Scenario: Normal retention unaffected

- **WHEN** no recovery occurred in a run
- **THEN** generation retention follows `keep_generations` exactly as before
