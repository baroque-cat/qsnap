# Backup Summary — Delta Spec

## MODIFIED Requirements

### Requirement: Dry-run summary table

In dry-run mode (`qsnap -n run`), the summary table SHALL show predicted actions (what WOULD happen) using the same format. The header SHALL include `Dryrun: YES`. The footer SHALL include `NOTE: Dryrun was active, none of the operations above were actually executed!`.

The summary SHALL render the planned actions from `PipelineResult.predictions` (capability `action-audit-trail`) as a per-VM section with per-disk rows, reusing the action line formatting (symbol and `[disk]` prefix) defined for real runs. Every predicted mutation — snapshot creation, blockcommit, FULL creation, incremental transfer, backup deletion — SHALL appear with its VM and disk context. When `PipelineResult.predictions` is empty, no planned-actions section SHALL be rendered.

#### Scenario: Dry-run summary header
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table header contains `Dryrun: YES`

#### Scenario: Dry-run summary footer
- **WHEN** `qsnap -n run` completes
- **THEN** the summary table footer contains the dry-run disclaimer note

#### Scenario: Dry-run shows predicted actions per VM and disk
- **WHEN** `qsnap -n run` completes for a VM with disks `vda` and `vdb` and predictions include a snapshot creation for each disk plus a FULL for `vda`
- **THEN** the summary shows one row per prediction, each prefixed with its disk (`[vda]` / `[vdb]`)
- **AND** no actual mutation was executed

#### Scenario: Dry-run with empty predictions
- **WHEN** `qsnap -n run` completes and nothing would change (all gates closed)
- **THEN** the summary still shows `Dryrun: YES` and the disclaimer
- **AND** no planned-actions rows are rendered
