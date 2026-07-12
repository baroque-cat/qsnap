## 1. Git & Environment

- [ ] 1.1 Create a new git branch for this change: `git checkout -b <branch-name>`
- [ ] 1.2 Run the full test suite to establish a passing baseline before making changes

## 2. <!-- Implementation Group Name -->

- [ ] 2.1 <!-- Task description -->
- [ ] 2.2 <!-- Task description -->

## 3. Testing

<!-- Reference group names from test-plan.md Delegation Groups section.
     Launch @Mr.Tester subagents IN PARALLEL (all in one message). -->

- [ ] 3.1 Read `test-plan.md` Delegation Groups section
- [ ] 3.2 Delegate group `<group-name-1>` to @Mr.Tester
- [ ] 3.3 Delegate group `<group-name-2>` to @Mr.Tester
- [ ] 3.4 Review @Mr.Tester reports and fix any source-level bugs discovered
- [ ] 3.5 Re-delegate any groups affected by source fixes
- [ ] 3.6 Verify all groups pass and coverage matches `test-plan.md`

<!--
  TEST ORCHESTRATION PROTOCOL (followed by the apply phase agent):

  1. Read test-plan.md → Delegation Groups section
  2. For EACH group listed, launch one @Mr.Tester subagent with:
     - The group's scope (file paths)
     - The group's scenario list from Coverage Map
     - Instruction: "Write or fix ONLY these specific tests. Report source bugs, don't fix them."
  3. Launch ALL groups IN PARALLEL (single message)
  4. After all testers return: fix any reported source bugs, re-delegate affected groups
  5. Repeat until all groups pass
-->
