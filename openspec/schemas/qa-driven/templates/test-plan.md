# QA Strategy & Test Plan

## Coverage Map

<!-- Trace every spec scenario to a concrete test. Each row is one test case.
     Use `openspec/specs/<capability>/spec.md` scenario names. -->

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| `<capability>` | `<requirement>` | `<scenario from spec>` | `<path/to/test/file.ts>` | `<test function name>` | `<group-name>` |

## Delegation Groups

<!-- Non-overlapping groups for parallel @Mr.Tester execution.
     Each group = one @Mr.Tester subagent.
     Groups MUST NOT share any test files — one file belongs to exactly one group. -->

### Group: <!-- kebab-case name, e.g. auth-unit, export-int -->

**Scope:** <!-- directory or file list -->

<!-- For each test in this group, list the file, scenario count, and action -->

| Test File | Scenarios | Action |
|---|---|---|
| `<path>` | `<count>` | `<NEW / MODIFY>` |

### Group: <!-- second group name -->

**Scope:** <!-- directory or file list -->

| Test File | Scenarios | Action |
|---|---|---|
| `<path>` | `<count>` | `<NEW / MODIFY>` |

## Test Modifications

<!-- Existing tests that need updating due to this change. Reference the spec scenario or design decision. -->

| File | Change | Reason |
|---|---|---|
| `<path>` | `<what changes>` | `<why: e.g., "New requirement: Token revocation">` |

## Risks & Edge Cases

<!-- Scenarios from design.md Risks section that need dedicated test coverage -->

- **[Risk]** `<risk description from design.md>` → `<proposed test file or scenario>`
