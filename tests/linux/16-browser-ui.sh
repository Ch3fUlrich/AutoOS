# shellcheck shell=bash
# sourced by tests/run-tests.sh; shares its harness and globals
# shellcheck disable=SC2034,SC2154

# ─── Browser UI payload ─────────────────────────────────────────────────────
describe "browser UI"

if it "classify handles edge cases correctly"; then
    failures="$(python3 - <<'PY'
import sys
sys.path.insert(0, './lib/linux')
from serve import classify

tests = [
    # Happy paths
    ("+ ok line", "ok"),
    ("! warn line", "warn"),
    ("x err line", "err"),
    ("> step line", "step"),
    ("run: cmd", "muted"),
    ("would run: cmd", "muted"),
    ("would do thing", "muted"),

    # Edge cases
    ("   + padded", "ok"),
    ("\t! tabbed", "warn"),
    ("", ""),
    ("    ", ""),
    ("unknown format", ""),
    ("x", ""),
]

bad = []
for line, expected in tests:
    res = classify(line)
    if res != expected:
        bad.append(f"classify({repr(line)}) == {repr(res)} != {repr(expected)}")
if bad:
    print("\n".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "every component has a homepage link"; then
    missing="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
# OS catalogs only: llm-models.json is a model catalogue, not components.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    for grp in json.load(open(p, encoding="utf-8")).get("categories", []):
        for c in grp.get("components", []):
            if not c.get("homepage"):
                bad.append(p + ":" + c["id"])
print(" ".join(bad))
PY
)"
    assert_eq "$missing" ""
fi

if it "a busy port moves the server on instead of failing"; then
    # The socket is opened in a walk, not a single bind, so an already-taken
    # 8777 costs a line of output rather than the whole run.
    if grep -q "for candidate in range(PORT, PORT + 20)" lib/linux/serve.py &&
       grep -q "already in use - serving on" lib/linux/serve.py; then pass
    else fail "serve.py does not walk past a busy port"; fi
fi

if it "the server answers a heartbeat the page can poll"; then
    if grep -q '"/api/ping"' lib/linux/serve.py &&
       grep -q "/api/ping" web/index.html; then pass
    else fail "no heartbeat endpoint, or nothing polling it"; fi
fi

if it "a page whose server has gone tears itself down"; then
    if grep -q "function serverGone" web/index.html &&
       grep -q "window.close" web/index.html; then pass
    else fail "the page would sit there looking live after its server went"; fi
fi

if it "the output can be copied without selecting it by hand"; then
    ok=1
    # innerText returns nothing while the Output card is collapsed, so the text
    # has to be read off the child elements instead.
    for marker in 'id="copyLog"' "function copyLog" "function logText" "execCommand"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "the log cannot be copied in one click"; fi
fi

if it "a verify command resolves to the file that will run"; then
    . lib/linux/detect.sh
    if launch_hint "Git" "git" "git --version" && [[ "$LAUNCH_HOW" == "run  git" && -x "$LAUNCH_PATH" ]]; then
        pass
    else fail "git resolved to how=$LAUNCH_HOW path=$LAUNCH_PATH"; fi
fi

if it "a component that is not here reports nothing rather than guessing"; then
    # A blank is honest. A plausible-looking path that does not exist is worse
    # than saying nothing, because it reads like a fact.
    . lib/linux/detect.sh
    if launch_hint "AutoOS Nonesuch XYZ" "autoos-nonesuch-xyz" "autoos-nonesuch-xyz"; then
        fail "invented how=$LAUNCH_HOW path=$LAUNCH_PATH"
    elif [[ -z "$LAUNCH_HOW" && -z "$LAUNCH_PATH" ]]; then pass
    else fail "left stale values behind: $LAUNCH_HOW / $LAUNCH_PATH"; fi
fi

if it "the report says where things landed"; then
    if grep -q "Where to find them" setup.sh && grep -q "launch_hint" setup.sh; then pass
    else fail "the report never says where anything went"; fi
fi

if it "a page whose scripts are blocked says so"; then
    # Everything on the page is driven by one inline script. Without it the
    # header would sit at "connecting..." for ever and explain nothing.
    if grep -q "<noscript>" web/index.html &&
       grep -q "JavaScript is blocked" web/index.html; then pass
    else fail "no usable noscript fallback"; fi
fi

if it "a non-URL homepage is rejected"; then
    tmp="$(mktemp)"
    cat >"$tmp" <<'JSON'
{"categories":[{"id":"x","name":"X","components":[
  {"id":"thing","name":"Thing","description":"d","provider":"apt","package":"p",
   "homepage":"not-a-url"}]}]}
JSON
    out="$(catalog_validate "$tmp" 2>&1)"; rc=$?
    rm -f "$tmp"
    if [[ $rc -ne 0 && "$out" == *"homepage"* ]]; then pass
    else fail "expected a homepage complaint, got rc=$rc: $out"; fi
fi

if it "the dependency graph the UI draws has no orphan requirements"; then
    # The browser resolves dependencies client-side, so every `requires` must
    # name a component that is actually shipped to it.
    bad="$(python3 - 2>&1 <<'PY'
import json, glob
bad = []
# OS catalogs only: llm-models.json is a model catalogue, not components.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    d = json.load(open(p, encoding="utf-8"))
    ids = {c["id"] for g in d.get("categories", []) for c in g.get("components", [])}
    for g in d.get("categories", []):
        for c in g.get("components", []):
            for r in c.get("requires", []):
                if r not in ids:
                    bad.append(p + ":" + c["id"] + "->" + r)
print(" ".join(bad))
PY
)"
    assert_eq "$bad" ""
fi

if it "the web page ships the dependency visualisation"; then
    ok=1
    for marker in "Install order" "chip-req" "chip-auto" "chip-locked" "renderTiers" "lockedBy"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "web/index.html is missing dependency-UI markers"; fi
fi

if it "external links open safely"; then
    # target=_blank without rel=noopener hands the opener to the target page.
    if grep -q 'target="_blank" rel="noopener noreferrer"' web/index.html; then pass
    else fail "external links must carry rel=noopener noreferrer"; fi
fi

if it "the theme can be switched, and still starts from the OS preference"; then
    # The three-button Auto/Light/Dark group is one toggle now. What matters is
    # unchanged: the user can override, and an un-overridden page follows the OS.
    ok=1
    for marker in 'id="themeToggle"' 'id="themeIcon"' 'function applyTheme' \
                  'function currentThemeIsDark' 'prefers-color-scheme: dark'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    # "auto" must survive as the stored default, or a fresh page picks a theme
    # for the user instead of asking their OS.
    grep -q 'PREF.get("theme", "auto")' web/index.html ||
        { ok=0; echo "missing: auto as the stored default" >&2; }
    grep -q 'removeAttribute("data-theme")' web/index.html ||
        { ok=0; echo "missing: auto clears the stamp so the OS decides" >&2; }
    if (( ok )); then pass; else fail "the theme toggle is incomplete"; fi
fi

if it "every colour token is defined on bare :root, not only behind a theme"; then
    # A token defined only inside a media query or [data-theme] block is undefined
    # in the un-stamped "auto" state, which is what renders one theme on another.
    missing="$(python3 - 2>&1 <<'PY'
import re, io
css = io.open("web/index.html", encoding="utf-8").read()
base = css.split(":root{", 1)[1].split("}", 1)[0]
declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", base))
used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
print(" ".join(sorted(used - declared)))
PY
)"
    assert_eq "$missing" ""
fi

if it "the components view can switch between list and grid"; then
    ok=1
    for marker in 'data-layout-choice="list"' 'data-layout-choice="grid"' 'data-layout="grid"' 'id="cols"'; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "layout switcher markers missing"; fi
fi

if it "the big cards are collapsible"; then
    n="$(grep -c 'details class="card"' web/index.html || true)"
    if [[ "$n" -ge 4 ]]; then pass; else fail "expected >=4 collapsible cards, found $n"; fi
fi

if it "the selected profile shows what it actually installs"; then
    # The summary used to be inline in each profile button; it is one full-width
    # detail panel below a row of chips now. Same job: the selected profile has
    # to say what you are about to install without leaving the card.
    ok=1
    for marker in "profile-chip" "profileDetail" "renderProfileSummary" "psum-grid" "psum-card"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "profile summary markers missing"; fi
fi

if it "the profile summary numbers each component with its install step"; then
    ok=1
    for marker in "step-badge" "psum-steps" "stepMap"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "install-order numbering missing from the summary"; fi
fi

if it "the summary and the install order share one numbering function"; then
    # Two independent numbering schemes would drift; there must be exactly one.
    n="$(grep -c "function stepMap" web/index.html || true)"
    assert_eq "$n" "1"
fi

if it "the component list shows the same step number"; then
    grep -q "chip-step" web/index.html && pass || fail "no step chip in the component list"
fi

if it "system, profile, components and install order share one tab"; then
    # They were three separate tabs; collapsing them into Overview is the point.
    if grep -q 'id="tab-deps"' web/index.html || grep -q 'id="tab-components"' web/index.html; then
        fail "a separate components or install-order tab is still present"
    else
        missing=""
        for card in cardSystem cardProfile cardCatalog cardOrder; do
            grep -q "id=\"$card\"" web/index.html || missing+="$card "
        done
        assert_eq "$missing" ""
    fi
fi

if it "navigation stays bounded and every section has a panel"; then
    # This began as "only two tabs remain" against a tab strip. The strip is a
    # dropdown now, and the point was never the number two: it was that sections
    # do not sprawl and that each one actually leads somewhere.
    n="$(grep -c 'role="menuitemradio"' web/index.html || true)"
    panels="$(grep -c '<section role="region" id="panel-' web/index.html || true)"
    if [ "$n" -le 5 ] && [ "$n" -eq "$panels" ]; then
        pass
    else
        fail "$n menu items and $panels panels (expected equal, and at most 5)"
    fi
fi

if it "the core stack is available on all three platforms"; then
    # These carry the same id in every catalog on purpose: a product that exists
    # everywhere but is filed under two different ids reports itself as
    # single-platform, which is exactly what docker-desktop/nerd-fonts did.
    missing="$(python3 - 2>&1 <<'PY'
import json, glob, collections
have = collections.defaultdict(set)
# OS catalogs only: llm-models.json is keyed by model id, not component id.
for p in ("catalog/windows.json", "catalog/linux.json", "catalog/macos.json"):
    plat = p.replace("catalog", "").strip("/\\").replace(".json", "")
    for g in json.load(open(p, encoding="utf-8"))["categories"]:
        for c in g["components"]:
            have[c["id"]].add(plat)
core = ["claude-code", "git", "nodejs", "docker", "tailscale",
        "handy", "vscode", "herdr", "agent-skills", "nerd-font",
        "zed", "litellm", "opencode-cli", "omniroute", "openhands-docker"]
bad = [c for c in core if have[c] != {"windows", "linux", "macos"}]
print(" ".join(bad))
PY
)"
    assert_eq "$missing" ""
fi

if it "the page labels components that are not on every platform"; then
    ok=1
    for marker in "platformChip" "chip-plat" "PLATFORM_NAME"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "platform label markers missing"; fi
fi

if it "the payload carries the platform list"; then
    grep -q "component_platforms" lib/linux/serve.py && pass || fail "serve.py does not compute platforms"
fi

if it "Handy is offered on every platform"; then
    n="$(grep -l '"id": "handy"' catalog/windows.json catalog/linux.json catalog/macos.json | wc -l)"
    assert_eq "$n" "3"
fi

if it "the UI shows installed applications and allows filtering"; then
    # The header pill is gone; the inventory has its own section with its own
    # search, and the catalog keeps its installed filter. Both still have to work.
    ok=1
    for marker in "installedList" "installedSearch" "renderInstalledTab" \
                  "filterInstalled" "installedCount" "filterInstalledOnly" \
                  'data-installed' "✓ installed"; do
        grep -q "$marker" web/index.html || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "web/index.html is missing installed-app markers"; fi
fi

if it "the payload carries installed component flags"; then
    ok=1
    for marker in '"installed":' '"installed applications"' "installed_ids"; do
        grep -q "$marker" lib/linux/serve.py || { ok=0; echo "missing: $marker" >&2; }
    done
    if (( ok )); then pass; else fail "serve.py does not report installed components"; fi
fi

# ─── USB creation UI (Task 11) ──────────────────────────────────────────────
# The human partner's original request: a button on the action bar that pops
# a dialog asking which OS to put on a stick. Assertions here stay light
# (existence, the [hidden] trap, module-level shape, the guard refusal) —
# the real proof this renders and behaves is Playwright driving the live
# page, not more grep here.

if it "the action bar offers usb creation"; then
    grep -q 'id="createUsb"' web/index.html && pass || fail "no create-usb button"
fi

if it "the usb dialog is hidden by the hidden attribute, not only by a class"; then
    # AGENTS.md §6: an author `display:` rule beats the browser's own
    # [hidden]{display:none}. This shipped twice in one afternoon already
    # (the section menu, the header progress bar) - assert on whitespace-
    # normalised CSS so a reformat cannot silently disable this guard.
    css="$(tr -s ' \t\n' ' ' < web/index.html)"
    [[ "$css" == *'.usb-dialog[hidden] {display:none'* || "$css" == *'.usb-dialog[hidden]{display:none'* ]] \
        && pass || fail "usb dialog will render open on every load"
fi

if it "rufus never reaches the browser's usb engine list (it needs a human at a GUI)"; then
    out="$(python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_images_response
data = usb_images_response()
print("rufus" if any(e["id"] == "rufus" for e in data["engines"]) else "ok")
PY
)"
    [[ "$out" == "ok" ]] && pass || fail "rufus (interactive: true) was offered in the browser"
fi

if it "the create-usb button is disabled when the server is not elevated"; then
    grep -q 'elevated' web/index.html && pass || fail "page never reads the elevated flag"
fi

# `usb_create_response(body: dict) -> tuple[int, dict]` is a MODULE-LEVEL
# function. `Handler` subclasses BaseHTTPRequestHandler and its helpers all
# take `self`, so `Handler._usb_create_body(dict)` would pass the dict AS
# self and raise TypeError - the repo already solves this correctly:
# classify() is module-level for exactly this reason (serve.py, near the
# top). AUTOOS_FAKE_LSBLK is the same synthetic-fixture mechanism the "usb
# safety" tests below use (AGENTS.md §5: never touch a real disk in tests) -
# it reaches usb_create_response's own subprocess call because os.environ is
# inherited, not replaced.
if it "the usb create endpoint refuses an unguarded device"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb root_is_sda)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response          # module-level, NOT a Handler method
print(usb_create_response({"device": "/dev/sda", "image": "ubuntu-desktop-lts"}))
PY
)"
    assert_contains "$out" "root filesystem"
fi

if it "the usb create endpoint refuses a device smaller than the image over the HTTP path (finding F12)"; then
    # Finding F12: usb_create_response used to call usb_guard with no
    # AUTOOS_IMAGE_BYTES at all, so the size check compared against 0 and a
    # device smaller than the image still got a 202. tiny_stick is 4 GB;
    # ubuntu-desktop-lts declares sizeGb: 6 in catalog/images.json.
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb tiny_stick)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response
print(usb_create_response({"device": "/dev/sdb", "image": "ubuntu-desktop-lts", "engine": "ventoy"}))
PY
)"
    assert_contains "$out" "400"
    assert_contains "$out" "too small"
fi

if it "the usb create endpoint refuses a second write while one is already running"; then
    out="$(AUTOOS_FAKE_LSBLK="$(fake_usb good_stick)" python3 - <<'PY'
import sys; sys.path.insert(0, "lib/linux")
from serve import usb_create_response, RUN, LOCK
with LOCK:
    RUN["running"] = True
try:
    print(usb_create_response({"device": "/dev/sdb", "image": "ubuntu-desktop-lts", "engine": "ventoy"}))
finally:
    with LOCK:
        RUN["running"] = False
PY
)"
    assert_contains "$out" "already in progress"
fi

