# Best practice for using a code agents
Based on [this](https://boristane.com/blog/how-i-use-claude-code/?utm_source=tldrnewsletter)

**The Workflow in One Sentence:**
> Read deeply, write a plan, annotate the plan until it’s right, then let Claude execute the whole thing without stopping, checking types along the way.

# Flowchart
```bash
flowchart LR
    R[Research] --> P[Plan]
    P --> A[Annotate]
    A -->|repeat 1-6x| A
    A --> T[Todo List]
    T --> I[Implement]
    I --> F[Feedback & Iterate]
```

# Steps to execute
## Phase 1: Research
1. let the agent research the code base intensely (use phrasing like "deep", "in depth", "intricacies", "specificities", "go through everything")
2. create a detailed research.md (to check the agent’s understanding and to have a reference for the next phase)

### Examples messages to agent for research:
#### 1
> read this folder in depth, understand how it works deeply, what it does and all its specificities. when that’s done, write a detailed report of your learnings and findings in research.md

#### 2
> study the notification system in great details, understand the intricacies of it and write a detailed research.md document with everything there is to know about how notifications work

#### 3
> go through the task scheduling flow, understand it deeply and look for potential bugs. there definitely are bugs in the system as it sometimes runs tasks that should have been cancelled. keep researching the flow until you find all the bugs, don’t stop until all the bugs are found. when you’re done, write a detailed report of your findings in research.md


## Phase 2: Plan, Annotate, Todo List

### Planning
create detailed implementation plan in plan.md

#### Examples messages to agent for planning:
##### 1
> I want to build a new feature <name and description> that extends the system to perform <business outcome>. write a detailed plan.md document outlining how to implement this. include code snippets

##### 2
> the list endpoint should support cursor-based pagination instead of offset. write a detailed plan.md for how to achieve this. read source files before suggesting changes, base the plan on the actual codebase

### Annotation Cycle
Add inline notes directly into the document.
These notes correct assumptions, reject approaches, add constraints, or provide domain knowledge that Claude doesn’t have.

After adding notes message to agent:
> I added a few notes to the document, address all the notes and update the document accordingly. don’t implement yet

```bash
flowchart TD
    W[Claude writes plan.md] --> R[I review in my editor]
    R --> N[I add inline notes]
    N --> S[Send Claude back to the document]
    S --> U[Claude updates plan]
    U --> D{Satisfied?}
    D -->|No| R
    D -->|Yes| T[Request todo list]
```

#### Examples of notes to add:
- “use drizzle:generate for migrations, not raw SQL” — domain knowledge Claude doesn’t have
- “no — this should be a PATCH, not a PUT” — correcting a wrong assumption
- “remove this section entirely, we don’t need caching here” — rejecting a proposed approach
- “the queue consumer already handles retries, so this retry logic is redundant. remove it and just let it fail” — explaining why something should change
- “this is wrong, the visibility field needs to be on the list itself, not on individual items. when a list is public, all items are public. restructure the schema section accordingly” — redirecting an entire section of the plan

### Todo List
Creates a checklist as a progress tracker for the implementation phase as a granular task breakdown.

Before implementing message to agent:
> add a detailed todo list to the plan, with all the phases and individual tasks necessary to complete the plan - don’t implement yet


## Phase 3: Implementation
refined standard prompt with all cruical instructions:

> implement it all. when you’re done with a task or phase, mark it as completed in the plan document. do not stop until all tasks and phases are completed. do not add unnecessary comments or jsdocs, do not use any or unknown types. continuously run typecheck to make sure you’re not introducing new issues.

#### Explanation of instructions:
- “implement it all”: do everything in the plan, don’t cherry-pick
- “mark it as completed in the plan document”: the plan is the source of truth for progress
- “do not stop until all tasks and phases are completed”: don’t pause for confirmation mid-flow
- “do not add unnecessary comments or jsdocs”: keep the code clean
- “do not use any or unknown types”: maintain strict typing
- “continuously run typecheck”: catch problems early, not at the end

### Feedback & Iterate
After implementation, review the code and provide feedback for improvements or corrections. Short feedback loops are crucial. Code references are also very helpful to point. Narrowing scope after a revert almost always produces better results than trying to incrementally fix a bad approach.


```bash
flowchart LR
    I[Claude implements] --> R[I review / test]
    R --> C{Correct?}
    C -->|No| F[Terse correction]
    F --> I
    C -->|Yes| N{More tasks?}
    N -->|Yes| I
    N -->|No| D[Done]
```

#### Examples of feedback:
- “You didn’t implement the deduplicateByTitle function.”
- “You built the settings page in the main app when it should be in the admin app, move it.”

If interactive mode:
- "wider"
- "still cropped"
- "there's a 2px gap"
