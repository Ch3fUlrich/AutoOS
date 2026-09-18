# Leaf contract

You are a **leaf**: an agent started by an orchestrator for one task. This contract applies to
every agent that is not an `orchestrator` or a `suborchestrator` role. It adds to the target
repository's `AGENTS.md` and the `coding-principles` skill and does not replace them.

1. **One closed task.** Do exactly the task you were given. Do not widen it, and do not start
   a second one.
2. **No spawning.** Never start another agent, sub-agent or task tool.
3. **No git history or ref changes.** Never commit, push, merge, rebase, reset, tag, stash,
   update a ref, switch or check out a branch, restore files, clean, or delete a branch or
   worktree. Leave your edits in the working tree: your orchestrator judges them and commits.
4. **Stop on ambiguity.** If the task is unclear, contradicts `AGENTS.md`, or needs a decision
   that is not yours, stop and say what is unclear. Do not guess.
5. **Return contract.** End with:
   - `changed:` every file you changed, one per line;
   - `test:` the summary line of the test run you were asked for, copied verbatim; if you were
     not asked for one, the narrowest relevant test you ran; else `test: not run` and why;
   - `open:` anything you did not finish.

"`AGENTS.md`" means the one at the root of the repository you are working in.

The generated agent configs (`catalog/agent-harness.json`) enforce rule 2 and fence the common
forms of rule 3 with command globs. Globs are defence in depth, not a proof: a command they miss
is still forbidden. The rest is on you.
