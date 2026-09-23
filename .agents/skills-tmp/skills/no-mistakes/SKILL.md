---
name: no-mistakes
description: Pre-push validation proxies for keeping main branches clean.
source: https://github.com/kunchenguid/no-mistakes
license: MIT
---

# No Mistakes (Pre-Push Validation)

This skill introduces the concept of an isolated local validation loop *before* code is exposed to the broader team or human reviewers.

## 1. The Local Proxy

Instead of pushing directly to a shared branch and triggering expensive cloud CI, the Engineer agent executes a local validation proxy:
1.  Runs linters, type-checkers, and unit tests locally.
2.  Captures `stderr`/`stdout`.
3.  Automatically feeds failures back into a fast-iteration LLM loop to fix them silently.

## 2. Keeping `main` Clean

The primary invariant: **No code is pushed or PR opened until the local proxy passes.**

This prevents the Reviewer agent (or human reviewers) from wasting time on syntax errors, missing imports, or broken tests. If the proxy loop fails continuously beyond a set budget, the agent halts and escalates, leaving the broken state entirely local.
