# Size Estimation — delta

## ADDED Requirements

### Requirement: Recovered-delta size estimation

The system SHALL estimate the size of a recovered delta as the sum of the `actual-size`
values (via `qemu-img info --output=json`) of every overlay in the copy set S (capability
`bitmap-loss-recovery`): the overlay active at the checkpoint freeze plus all overlays
created after it. The estimate is an upper bound — it includes pre-freeze writes inside the
oldest copied layer and blocks that will shadow identically — and SHALL be presented as
approximate (`~` prefix) in logs, predictions, and the free-space gate. The estimator SHALL
be a read-only function operating through `IShell`. When any layer's size cannot be
determined, the estimate SHALL fall back to the FULL chain-sum estimate (conservative).

#### Scenario: Estimate sums the copy set

- **WHEN** the copy set is {layer active at freeze (21 GiB actual), next overlay (300 MiB
  actual)}
- **THEN** the recovered-delta estimate is approximately 21.3 GiB
- **AND** it is displayed with the approximate marker

#### Scenario: Unreadable layer falls back to FULL estimate

- **WHEN** `qemu-img info` fails for any layer of the copy set
- **THEN** the estimator returns the FULL chain-sum estimate instead

#### Scenario: Estimate feeds the free-space gate in dry-run

- **WHEN** dry-run predicts a recovered delta
- **THEN** the free-space gate prediction uses the recovered-delta estimate
- **AND** no mutation occurs
