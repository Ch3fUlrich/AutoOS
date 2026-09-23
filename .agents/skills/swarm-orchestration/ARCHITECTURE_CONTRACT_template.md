# ARCHITECTURE_CONTRACT.md

## Metadata
- task_id:
- title:
- status: DRAFT
- owner: @architect
- created_at:
- updated_at:
- parent_task:
- related_tasks:
- risk_level:
- execution_mode:
  - single-engineer
  - best-of-3
  - best-of-5
- target_model_profile:
- reviewer_model_profile:

## Objective
- One-sentence goal:
- Why this change exists:
- Expected outcome:

## System Context
- Affected subsystem:
- Upstream dependencies:
- Downstream consumers:
- Related modules:
- Deployment/runtime context:
- User-visible impact:
- Operational impact:

## Scope

### In Scope
- file/module:
- file/module:
- behavior:
- tests:
- docs:

### Out of Scope
- file/module:
- API:
- infrastructure:
- refactors not required for acceptance:

## Interfaces

### Inputs
- function/API/interface:
- argument types:
- payload/schema:
- preconditions:

### Outputs
- function/API/interface:
- return types:
- emitted events:
- side effects:
- postconditions:

## Invariants
- must preserve:
- must preserve:
- performance constraint:
- compatibility constraint:
- security constraint:

## Forbidden Changes
- do not modify:
- do not rename:
- do not add dependency:
- do not change public interface unless explicitly listed in scope:
- do not refactor unrelated files:

## Implementation Notes
- preferred approach:
- rejected alternatives:
- required libraries/framework versions:
- relevant documentation sources:
- migration notes:
- rollback notes:

## Dependency and Codebase Context
- dependency graph summary:
- relevant symbols/classes/functions:
- modules with high coupling:
- modules with high churn:
- known historical defects:

## Verification Contract

### Required Commands
- test:
- lint:
- typecheck:
- integration:
- e2e:
- benchmark/perf:
- security/static analysis:

### Acceptance Gates
- [ ] tests pass
- [ ] lint passes
- [ ] typecheck passes
- [ ] no forbidden files modified
- [ ] architecture invariants preserved
- [ ] reviewer validated implementation quality
- [ ] no unresolved blocking defect remains

## Cacheable Context
Stable context allowed to be reused across turns:
- approved tool inventory
- stable role instructions
- repository invariants
- this approved architecture contract
- stable project conventions

Volatile context not treated as stable cache by default:
- raw terminal logs
- transient stack traces
- exploratory notes
- temporary hypotheses
- partial diffs
- intermediate reviewer commentary

## Cache Rules
- preserve wording and ordering of stable context blocks where possible
- avoid changing tool inventory mid-task unless necessary
- invalidate contract cache when interfaces, dependencies, or acceptance criteria change
- if explicit cache control is supported, place cache breakpoints after:
  - tool definitions
  - role/system instructions
  - approved architecture contract

## Contract Hash
- contract_hash:
- invalidates_on:
  - interface change
  - dependency change
  - acceptance criteria change
  - scope change
  - execution tool change affecting implementation

## Checkpoint Scope
- checkpoint_file:
- branch:
- worktree:
- lock_files:
- recovery_priority:
  1. checkpoint file
  2. branch/worktree state
  3. task artifact state
  4. conversation context

## Resume Instructions
1. Load checkpoint file.
2. Verify `contract_hash` matches current contract state.
3. Inspect branch and worktree status.
4. Review last completed step and next step.
5. Re-run any required verification invalidated by new edits.
6. Continue only within current scope.
7. If scope drift is required, stop and return to architect.

## Handoff to Engineer
- assigned_agent:
- branch_name:
- worktree_path:
- allowed_files:
- required_files:
- blocked_files:
- exact objective:
- completion definition:
- verification commands:
- checkpoint expectations:
- escalation rule:

## Review Contract
Reviewer must check:
- architecture fit
- completeness
- regression risk
- edge cases
- security implications
- test quality
- scope compliance
- code quality and maintainability

Reviewer output must include:
- status: APPROVED | REJECTED
- blocking issues:
- non-blocking issues:
- required fixes:
- evidence:
- files reviewed:
- commands run:

## Best-of-N Arbitration
Only complete when enabled by architect.

### Trigger Reason
- reason:
- risk_signals:

### Candidate Comparison
- candidate_a:
- candidate_b:
- candidate_c:
- candidate_d:
- candidate_e:

### Arbitration Criteria
- verification pass rate
- contract compliance
- minimal scope violation
- code quality
- maintainability
- performance
- security

### Winner
- selected_candidate:
- rationale:
- required follow-up fixes:

## Observability
- sentry_project:
- sentry_issue_links:
- datadog_services:
- logs/trace references:
- production signals to watch after merge:

## Sign-off
- architect:
- engineer:
- reviewer:
- final_status:
- completion_time: