## MODIFIED Requirements

### Requirement: State serialization format

`JsonStateManager` SHALL persist snapshot and backup metadata in JSON files. The `content_hash` field SHALL NOT be serialized — it is removed from the state schema. Deserialization SHALL be read-tolerant: old state files containing `content_hash` SHALL load without error (the field is silently ignored via `if "content_hash" in d`). New state files SHALL NOT contain the field.

#### Scenario: New state file has no content_hash

- **WHEN** a snapshot is recorded in state
- **THEN** the JSON file does NOT contain a `content_hash` key

#### Scenario: Old state file with content_hash loads fine

- **WHEN** an old state file containing `content_hash` is loaded
- **THEN** no error is raised
- **AND** the `content_hash` value is silently ignored
- **AND** all other fields are loaded normally
