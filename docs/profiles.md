# Profiles & detection

## What gets detected

Detection is pure inspection — it never installs, downloads or writes. That is
what makes `--dry-run` meaningful and lets the tests run against synthetic
machines instead of the one they happen to be on.

| Detected | Used for |
|---|---|
| OS, version, build | Warning on unsupported builds |
| Architecture (`x64` / `arm64` / `armhf`) | Hiding components that have no build for it |
| Model / board | Recognising a Raspberry Pi |
| CPU cores, RAM | Choosing the suggested profile |
| Free disk | Warning before large installs |
| Elevation / sudo | Warning that machine-wide packages will be skipped |
| Display present | Hiding the desktop category on headless boxes |
| WSL2 / container | Warning that systemd and desktop packages behave differently |
| Package managers, git, node, docker | Skipping what is already there |
| Virtualisation firmware flag | Warning that Docker and WSL2 will not start without it |

## Profiles

A profile is only a **starting set of ticks**. Everything stays editable in the
menu afterwards.

| Profile | Windows | Linux | macOS | Intended for |
|---|:---:|:---:|:---:|---|
| `workstation` | ✅ | ✅ | ✅ | A machine you sit in front of |
| `ai-coding` | ✅ | ✅ | ✅ | Development box: editors, agents, containers, shell |
| `light` | ✅ | ✅ | ✅ | Raspberry Pi 5 and similar — Claude Code, Tailscale, Herdr |
| `server` | — | ✅ | — | Headless: shell, networking, containers, no GUI |
| `custom` | ✅ | ✅ | ✅ | Nothing pre-ticked |

`server` does not exist on Windows or macOS, so a headless machine there is
offered `ai-coding` instead of a profile its catalog does not define.

## How the suggestion is picked

Deliberately conservative — a small board is never handed a desktop install it
cannot use.

```
Raspberry Pi                      -> light
container                         -> server        (Linux only)
arm64 (Windows)                   -> light
RAM <= 5 GB (Linux) / < 6 GB (Win) -> light
headless                          -> server (Linux) / ai-coding (Windows, macOS)
RAM >= 16 GB and >= 8 cores       -> workstation
otherwise                         -> ai-coding
```

The RAM cut sits **below** 6 GB on purpose: an 8 GB machine reports about
7.4 GB usable, and `light` is meant for Pi-class hardware, not a modest laptop.

## Hidden vs. offered

A component that cannot work on the detected machine is **hidden**, not shown
and later failed. Offering a choice that cannot succeed is worse than not
offering it.

- `arch` on a component hides it on other architectures — for example
  Antigravity is x64-only, so it never appears on a Pi.
- `requiresDisplay` on a category hides the whole group on a headless box.

Check what your machine is actually offered:

```bash
./setup.sh --list
```
