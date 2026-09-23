# secrets/

Provider API keys for the CAO orchestration lane. **Nothing in here is committed
except this file and `api_keys.conf.example`** — `.gitignore` uses `/secrets/*`
plus explicit negations, so `git add secrets/` stages the example and refuses
`api_keys.conf` even when named directly.

## Where the keys are read from

First existing file wins:

1. `$AUTOOS_SECRETS` (a file path);
2. `~/.config/autoos/api_keys.conf`, the user scope, on every OS (inside WSL that is
   the distro's home: the CAO probe reads the key there);
3. legacy: this folder's `api_keys.conf`, read with a one-line notice to move it.
   `cao/config.py:find_secrets` walks up to the repository root; the
   `deepseek_*review.sh` scripts also reach the main checkout through
   `--git-common-dir` from a worktree.

Users: `cao/config.py:find_secrets`, `cao/probe.py:secrets_lookup` and both
`deepseek_*review.sh` scripts. The scripts also honour `$DEEPSEEK_SECRETS` first, and in
them an explicitly set variable pointing at a missing file is an error rather than a
silent switch to another file. New keys belong in the user scope; this folder remains
only as the fallback.

## Format

`api_keys.conf` is `key=value`, one per line. Keys are pool names from the `cao`
block of `handoff.config.json`:

```
deepseek=sk-...
```

Blank lines and `#` comments are ignored. Values are never logged, never echoed
into a brief, and never written into a generated profile — the loader exports
them into the child process environment and nothing else reads them.

## Adding a provider

1. Add `<pool>=<key>` to `~/.config/autoos/api_keys.conf`.
2. Reference the pool in `cao.pools` with its `apiKeyEnv`.
3. Run `python setup_cao.py --check` — it reports the pool as available without
   printing the value.

A pool whose key is missing is marked `unavailable` and every routing ladder
skips it, exactly as if it were quota-cooling. An absent key is never an error.

## If a key leaks

Rotate it at the provider first, then replace it in the secrets file. Rewriting git history is
not a substitute for rotation — assume any key that reached a commit is burned.
