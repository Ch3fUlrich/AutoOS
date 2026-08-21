# Architecture

## Layout

```
setup.ps1              Windows entry point   — PowerShell 5.1+, no dependencies
setup.sh               Linux/macOS entry     — bash 4+, python3 for JSON only
catalog/*.json         WHAT can be installed (data)
lib/windows/*.psm1     HOW it happens on Windows
lib/linux/*.sh         HOW it happens on Linux and macOS
web/index.html         browser UI, served by --serve
tests/                 both suites, no framework required
Windows/ansible/       remote fleet provisioning — NOT used by setup.ps1
Linux/ubuntu_autoinstall/  unattended Ubuntu install profile
third_party/           vendored code under its own licence — never edit
```

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

## Why no framework

Every dependency is one more thing that has to exist on a machine where nothing
exists yet. No TUI library, no test framework, no JSON tool beyond the `python3`
that Debian, Ubuntu and macOS already ship. That constraint is the product, not
an aesthetic.
