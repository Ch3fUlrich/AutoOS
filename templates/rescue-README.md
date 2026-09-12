# AutoOS rescue stick — AI tooling

This file is installed at the root of the rescue stick by `templates/rescue-bootstrap.sh`. It
describes what that bootstrap actually installs, not the aspirational end state — see
**"What is NOT on this stick yet"** below before assuming a model is ready to use.

## What `rescue-bootstrap.sh` installs

Running `sudo bash rescue-bootstrap.sh` from this stick (or from `/cdrom` inside the live
session) installs, alongside the rest of the "Disaster Recovery" toolkit:

- `ollama` — the local model runtime.
- `oterm` — a terminal UI for chatting with whatever Ollama models are pulled (installed via
  `pip`; see "oterm" below for why it isn't the default scripted entry point).
- `/usr/local/bin/ai` — a small dispatcher over `claude`, `agy` (Antigravity CLI) and `local`
  (Ollama, via the `ollama-chat` wrapper described below).
- `/usr/local/bin/ollama-chat` — the plain, scriptable binary the `local` backend execs.

Every step is idempotent (safe to run twice — a second run reports `skipped`, never
`installed`) and every AI-related step degrades gracefully with no network at all, which is the
condition this stick exists for in the first place.

## What is NOT on this stick yet

**No model weights ship with this bootstrap.** `ollama` and `oterm` are the *runtime* — the
actual `qwen3:4b` / `qwen3:1.7b` / `qwen2.5-coder:7b` model files (2.5–4.7 GB each) are meant to
arrive on a separate model partition built alongside the stick, and that rebuild is still
pending as of this writing. If you have booted this stick and `ollama list` shows nothing, that
is expected, not a bug — pull a model yourself (needs network) or copy one onto this stick's
model partition once it exists.

Similarly, `<stick_root>/rescue/wheels` (oterm's offline pip cache, see below) and
`<stick_root>/rescue/debs` (the rest of the toolkit's offline apt cache) are each *optional*: they
only exist if someone ran `lib/linux/imagecache.sh`'s `imagecache_build` /
`imagecache_wheelhouse_build` against this specific stick ahead of time. Without them,
`rescue-bootstrap.sh` still runs — it just needs a real network connection to install anything
beyond what the live ISO already carries.

## Starting Ollama

```sh
ollama serve &
```

`ollama serve` is a long-running foreground server; the `&` backgrounds it so the rest of this
session stays usable (or run it in a second terminal / `tmux` pane). `ollama` (the CLI) and
`oterm` both talk to this server over `localhost:11434` — nothing else on the stick needs to be
running for either to work.

## Where models live, and pointing `OLLAMA_MODELS` at the stick

Ollama's default model store is `~/.ollama/models` (root's home inside the live session, so
normally `/root/.ollama/models`), which lives on the live session's tmpfs and is **gone on
reboot**. If this stick's model partition is mounted (once it exists — see above), point Ollama
at it instead, before running `ollama serve`:

```sh
export OLLAMA_MODELS=/path/to/model-partition/ollama-models
ollama serve &
```

`OLLAMA_MODELS` must be set in every shell that runs `ollama serve` or `ollama pull` — it is not
persisted across reboots or new shells by this bootstrap. Run `ollama list` after setting it to
confirm which models it actually sees.

## Running a model

One-shot, non-interactive (prints one reply and exits — this is what scripts and the `ai`
dispatcher below use):

```sh
ollama run qwen3:4b "summarize this dmesg output"
```

Interactive chat session:

```sh
ollama run qwen3:4b
```

## Piping diagnostics into a model

Since `ollama run <model>` reads a prompt from its trailing argument OR from stdin, diagnostic
output pipes straight in:

```sh
smartctl -a /dev/sda | ollama run qwen3:4b "what does this SMART report tell you?"
dmesg | tail -200     | ollama run qwen3:4b "anything alarming in this kernel log?"
lsblk -f              | ollama run qwen3:4b "does this partition layout look right for booting?"
```

`qwen3:4b`'s 256K context window is large enough to paste a full `dmesg` or SMART dump in one
go — no need to trim it first.

## `oterm` — the TUI

`oterm` is a full-screen terminal chat client for whatever models Ollama has pulled: persistent
chat sessions, per-session system prompts, and no separate server or web frontend beyond
`ollama serve` itself.

```sh
oterm
```

**`oterm` is interactive only** — it has no documented one-shot or scripted mode, so it cannot be
piped a prompt or called from another script. That is exactly why the `ai` dispatcher's `local`
backend (below) execs `ollama` directly through a small wrapper instead of `oterm`.

## The `ai` dispatcher

`ai` is a tiny wrapper over whichever backend is actually usable, reading its registry from
`/etc/autoos/ai-clients.conf`:

```sh
ai --list                       # show every registered backend and whether it's installed
ai "why did this drive fail?"   # run the default backend (claude, unless overridden)
ai local "why did this drive fail?"   # explicitly use the local Ollama model
ai agy "..."                          # explicitly use Antigravity CLI
```

Backends, in registry order:

| id      | binary         | needs                          |
|---------|----------------|---------------------------------|
| `claude`| `claude`       | Anthropic API key + network     |
| `agy`   | `agy`          | Google account + network (and a prior interactive login for headless use) |
| `local` | `ollama-chat`  | `ollama serve` running + at least one pulled model — **no API key, no network** |

This is the important property for a rescue stick: `claude` and `agy` both need an API key *and*
a network connection, which is exactly what a broken machine typically lacks. When the default
backend's binary is missing, `ai` automatically falls back to the next backend in the registry
that IS installed and says so — so on a machine with no network at all but Ollama already set up,
plain `ai "..."` degrades to the `local` backend with no configuration needed.

`ollama-chat` (the binary behind `local`) is a thin wrapper: it runs
`ollama run <model> "$@"` against `AUTOOS_OLLAMA_MODEL` (default `qwen3:4b`), falling back to
whatever `ollama list` reports first if that tag isn't pulled on this machine.

## Offline package caches consumed by this bootstrap

Two independent, optional caches make `rescue-bootstrap.sh` fully offline-capable when built
ahead of time onto this stick:

- `<stick_root>/rescue/debs` — a local apt repository for the rescue toolkit's `.deb` packages,
  built by `imagecache_build` in `lib/linux/imagecache.sh`.
- `<stick_root>/rescue/wheels` — a `pip download` wheelhouse for `oterm` and its full dependency
  tree, built by `imagecache_wheelhouse_build` in the same file.

Both are additive and idempotent to build, and both are verified for actual coverage after a
build — not just counted — before being reported as complete. Neither is required for
`rescue-bootstrap.sh` to run; without them, the same steps install everything over a real network
connection instead.
