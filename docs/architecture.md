# Architecture

## Why it works this way

Post-install automation usually fails in the same three ways. Each design
decision here is aimed at one of them.

| The usual failure | What AutoOS does instead |
|---|---|
| **The tool needs tools.** A setup script that needs WSL, Ansible, Python packages and a TUI library cannot run on the machine it is meant to set up. | **No dependencies.** PowerShell 5.1 and bash are already there. On Linux, `python3` is used only to read JSON. No test framework, no menu library. |
| **All or nothing.** Rigid blocks — "install the dev bundle" — mean taking software you do not want, or editing the script. | **A checkbox per component.** Profiles are just a starting set of ticks. Dependencies resolve themselves. |
| **It runs once, then rots.** Re-running reinstalls, half-fails, or overwrites your config. | **Safe to run twice.** A second run reports `skipped`. Every file it edits is backed up first, and `--undo` puts them back. |

The rules further down enforce this, and each is covered by a test.

## Layout

| Directory | Contains |
|---|---|
| `setup.ps1` / `setup.sh` | The two entry points. Everything else is called by these. |
| [`catalog/`](../catalog/) | **What** can be installed — data only. See [the catalog](catalog.md). |
| `lib/windows/` · `lib/linux/` | **How** it happens: detect, catalog, install, ui, state, serve. |
| [`web/`](../web/) | The browser UI served by `--serve`. See [Browser UI](web-ui.md). |
| [`tests/`](../tests/) | Both suites, no framework needed. See [Testing](testing.md). |
| [`Windows/ansible/`](../Windows/ansible/) | Provisioning *other* machines over the network. See [Remote provisioning](remote-provisioning.md). |
| [`Linux/ubuntu_autoinstall/`](../Linux/ubuntu_autoinstall/) | Unattended Ubuntu install profile. See [unattended Ubuntu install](remote-provisioning.md#unattended-ubuntu-install). |
| [`third_party/`](../third_party/) | Vendored code under its own licence. Never edited. |
| [`docs/`](README.md) | The documentation index. |

### Modules

| Windows | Linux/macOS | Responsibility |
|---|---|---|
| `AutoOS.Ui.psm1` | `ui.sh` | Colour, layout, prompts, the checkbox menu |
| `AutoOS.Detect.psm1` | `detect.sh` | Machine inspection; no side effects |
| `AutoOS.Catalog.psm1` | `catalog.sh` | Load, validate, filter, resolve dependencies |
| `AutoOS.Install.psm1` | `install.sh` | Provider dispatch and post-install steps |
| `AutoOS.State.psm1` | (in `install.sh`) | Replay, verification, undo |
| `AutoOS.Serve.psm1` | `serve.sh` + `serve.py` | The browser UI's local server |

## The pipeline

```
detect -> profile -> select -> plan -> confirm -> execute -> report
```

Each stage is a separate function and independently testable.

- **Detection never installs.** It only reads.
- **Selection never touches the disk.**
- **Execution never asks questions** — everything the user needed to answer was
  answered before the first package was touched.

That ordering is what makes `--dry-run` meaningful, lets the tests run against
synthetic machines, and lets a headless run be fully non-interactive.

## Design rules

These are enforced by tests, not just intentions.

1. **Safe to run twice.** A second run reports `skipped`, never `installed`.
2. **Never overwrite a PATH, profile or config wholesale.** Read, append, write
   back, keep a timestamped backup. There is exactly one code path for PATH
   edits (`Add-AutoOSPathEntry`) because replacing `Path` outright once wiped a
   user's entire environment.
3. **Nothing is installed before you confirm.**
4. **A component that cannot work here is hidden**, not offered and then failed.
5. **The catalog is data.** Adding software never requires a code change.
6. **All output goes through the UI layer** so colour, `NO_COLOR`, non-TTY and
   the log file are handled in one place. A bare `echo` in an installer is a bug
   — it bypasses the log the user will need when something fails.

## Platform traps already paid for

Each of these cost a real debugging session and is now covered by a test.

| Trap | Consequence |
|---|---|
| `.ps1`/`.psm1` without a **UTF-8 BOM** | PowerShell 5.1 decodes as ANSI; box-drawing chars become parse errors |
| `core.autocrlf=true` | Shell scripts check out with CRLF and fail on Linux with "bad interpreter" |
| Tab as a field delimiter in bash | Tab is *IFS whitespace*; runs collapse and every empty field shifts columns left |
| `"$var[2K"` in a PowerShell string | Parsed as an array index, not literal text |
| `Import-Module -Force` inside a module | Removes the caller's global copy of that module |
| `cmd && VAR=1` under `set -e` | Returns non-zero when `cmd` is absent; as a function's last statement it aborts the script |
| `$Profile`, `$args` as parameter names | Shadow PowerShell automatic variables |
| A function that both prints and echoes its result | `$(...)` captures the UI output into the status string |
| `.map(fn)` in JavaScript | Passes `(value, index, array)`; a second parameter with a default silently receives the index |
| `srv.shutdown()` from inside `serve_forever()` | Blocks waiting for the loop it is running on to finish - deadlock |
| A process backgrounded by a non-interactive shell | Inherits `SIGINT` as ignored, and CPython leaves it that way - Ctrl-C does nothing |
| `HttpListener.GetContext()` | Blocks in native code; PowerShell can only act on Ctrl-C between statements |
| A shellcheck `disable` after the first command | Applies to one command, not the file. It must precede every command |
| A comment whose first word is `shellcheck` | Parsed as a directive, not prose |
| `<button>` as a card | Vertically centres its content by default; needs an explicit `flex-direction:column` |

## Why no framework

Every dependency is one more thing that has to exist on a machine where nothing
exists yet. No TUI library, no test framework, no JSON tool beyond the `python3`
that Debian, Ubuntu and macOS already ship. That constraint is the product, not
an aesthetic.
