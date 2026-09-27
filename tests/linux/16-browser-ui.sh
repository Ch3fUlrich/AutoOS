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
# OS catalogs only (the AI registry holds models, not components).
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
# OS catalogs only (the AI registry holds models, not components).
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
# OS catalogs only (the AI registry is keyed by model id, not component id).
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

# ─── Logins and keys: write-only secrets (GET/POST /api/secrets) ────────────
# The browser UI can SET a value in the git-ignored configuration/api-keys.yml
# but can never read one back. These cases drive the module-level functions
# directly - Handler helpers all take `self`, so a Handler-level call would pass
# the body AS self and raise TypeError; classify() and usb_create_response set
# the same precedent. AUTOOS_KEYS_FILE points every write at a throwaway file,
# so the repository's real, git-ignored api-keys.yml is never opened.

if it "the browser UI writes a key 0600 and backs up the old file"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, tempfile, pathlib
sys.path.insert(0, "lib/linux")
from serve import secrets_post_response

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-sec-"))
keys = root / "api-keys.yml"
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
bad = []

code, body = secrets_post_response({"id": "qoder_pat", "value": "test-value-not-real"}, "127.0.0.1")
if code != 200 or not keys.exists():
    bad.append("missing-file write: code=%s body=%s" % (code, body))
if keys.exists():
    mode = keys.stat().st_mode & 0o777
    if mode != 0o600:
        bad.append("new file mode %o, want 600" % mode)
    if "test-value-not-real" not in keys.read_text(encoding="utf-8"):
        bad.append("value not stored")
if list(root.glob("api-keys.yml.autoos-backup-*")):
    bad.append("a backup was made when there was nothing to back up")

before = "# note\nqoder_pat: 'old'\ngroq: 'keep' # trailing\n"
keys.write_text(before, encoding="utf-8")
code, body = secrets_post_response({"id": "qoder_pat", "value": "test-value-replaced"}, "127.0.0.1")
text = keys.read_text(encoding="utf-8")
backups = sorted(root.glob("api-keys.yml.autoos-backup-*"))
if code != 200:
    bad.append("update code=%s body=%s" % (code, body))
if "test-value-replaced" not in text or "old" in text:
    bad.append("value was not replaced in place")
if "# note" not in text or "groq: 'keep' # trailing" not in text:
    bad.append("neighbouring lines were lost")
if len(backups) != 1:
    bad.append("expected 1 backup, got %d" % len(backups))
else:
    if backups[0].read_text(encoding="utf-8") != before:
        bad.append("backup is not the original bytes")
    bmode = backups[0].stat().st_mode & 0o777
    if bmode != 0o600:
        bad.append("backup mode %o, want 600" % bmode)
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI treats setting the same value again as a no-op"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, tempfile, pathlib
sys.path.insert(0, "lib/linux")
from serve import secrets_post_response

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-sec-"))
keys = root / "api-keys.yml"
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
bad = []

secrets_post_response({"id": "groq", "value": "test-value-same"}, "127.0.0.1")
before = keys.read_bytes()
n0 = len(list(root.glob("api-keys.yml.autoos-backup-*")))

code, body = secrets_post_response({"id": "groq", "value": "test-value-same"}, "127.0.0.1")
n1 = len(list(root.glob("api-keys.yml.autoos-backup-*")))
if code != 200 or not body.get("unchanged"):
    bad.append("identical value: code=%s body=%s" % (code, body))
if keys.read_bytes() != before:
    bad.append("an identical value rewrote the file")
if n1 != n0:
    bad.append("an identical value made a backup (%d -> %d)" % (n0, n1))
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI rejects a bad secret id or value and writes nothing"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, tempfile, pathlib, hashlib
sys.path.insert(0, "lib/linux")
from serve import secrets_post_response

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-sec-"))
keys = root / "api-keys.yml"
keys.write_text("groq: 'test-value-existing'\n", encoding="utf-8")
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
bad = []

cases = [
    {"id": "not_a_key", "value": "x"},
    {"id": "groq", "value": ""},
    {"id": "groq", "value": "has\nnewline"},
    {"id": "groq", "value": "has\rreturn"},
    {"id": "groq", "value": "has\x00nul"},
    {"id": "groq", "value": "  padded"},
    {"id": "groq", "value": "REPLACE_WITH_your_key"},
    {"id": "groq", "value": "has'quote"},
    {"id": "groq", "value": 'has"double'},
    {"id": "groq", "value": 123},
    {"id": "groq", "value": "x" * 4097},
    {},
]
digest = hashlib.sha256(keys.read_bytes()).hexdigest()
for body in cases:
    code, payload = secrets_post_response(body, "127.0.0.1")
    if code != 400:
        bad.append("accepted %r with code %s" % (body, code))
    if hashlib.sha256(keys.read_bytes()).hexdigest() != digest:
        bad.append("the file changed after rejecting %r" % (body,))
        break
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI sets secrets only from loopback unless opted in"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, tempfile, pathlib, hashlib
sys.path.insert(0, "lib/linux")
from serve import secrets_post_response

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-sec-"))
keys = root / "api-keys.yml"
keys.write_text("groq: 'test-value-existing'\n", encoding="utf-8")
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
os.environ.pop("AUTOOS_SERVE_REMOTE_SECRETS", None)
bad = []

digest = hashlib.sha256(keys.read_bytes()).hexdigest()
code, payload = secrets_post_response({"id": "groq", "value": "test-value-remote"}, "10.1.2.3")
if code != 403:
    bad.append("non-loopback got %s, want 403" % code)
if hashlib.sha256(keys.read_bytes()).hexdigest() != digest:
    bad.append("a refused remote write changed the file")

os.environ["AUTOOS_SERVE_REMOTE_SECRETS"] = "1"
code, payload = secrets_post_response({"id": "groq", "value": "test-value-remote"}, "10.1.2.3")
if code != 200:
    bad.append("opted-in remote got %s, want 200" % code)
del os.environ["AUTOOS_SERVE_REMOTE_SECRETS"]
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI reports set/missing without ever sending a value"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, tempfile, pathlib, json
sys.path.insert(0, "lib/linux")
from serve import secrets_payload, provider_status, SECRET_KEYS

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
root = pathlib.Path(tempfile.mkdtemp(prefix="autoos-sec-"))
keys = root / "api-keys.yml"
keys.write_text(
    "groq: 'test-value-present'\n"
    "qoder_pat: REPLACE_WITH_your_token\n",
    encoding="utf-8")
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
bad = []

payload = secrets_payload("127.0.0.1")
by_id = {s["id"]: s for s in payload["secrets"]}
for key in ("id", "name", "group", "configured", "apply"):
    if key not in by_id["groq"]:
        bad.append("row is missing %r" % key)
if by_id["groq"]["configured"] is not True:
    bad.append("a real value read as missing")
if by_id["qoder_pat"]["configured"] is not False:
    bad.append("a REPLACE_WITH placeholder read as configured")
if by_id["opencode_password"]["configured"] is not False:
    bad.append("an absent key read as configured")
if set(by_id) != set(SECRET_KEYS):
    bad.append("the payload drifted from the SECRET_KEYS allowlist")
if payload["file"] != "configuration/api-keys.yml":
    bad.append("file field = %r" % payload["file"])
if payload["writable"] is not True:
    bad.append("a loopback payload is not writable")
if "test-value-present" in json.dumps(payload):
    bad.append("the payload leaked a value")
if secrets_payload("10.1.2.3")["writable"] is not False:
    bad.append("a non-loopback payload claims writable")

keys.unlink()
if any(s["configured"] for s in secrets_payload("127.0.0.1")["secrets"]):
    bad.append("a missing keys file still reports configured keys")

for row in provider_status():
    if not set(("id", "name", "configured")) <= set(row):
        bad.append("provider_status lost the shape the STATE.providers fallback needs")
        break
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI sets values write-only through /api/secrets"; then
    failures="$(python3 - 2>&1 <<'PY'
import re, pathlib
html = pathlib.Path("web/index.html").read_text(encoding="utf-8")
bad = []

start = html.find("logins and keys, write-only (GET/POST /api/secrets)")
end = html.find("AI services: live status + the existing setup actions", start)
if start < 0 or end < 0:
    print("could not locate the secrets frontend block")
    raise SystemExit(0)
block = html[start:end]

if 'data-target-card="cardProviders"' not in html:
    bad.append("no pill targets cardProviders")
if 'id="cardProviders"' not in html:
    bad.append("no card id=cardProviders")
if "Logins and keys" not in html:
    bad.append("the card/pill was not renamed to Logins and keys")
if not all(name in block for name in ("renderProviders", "providerRow", "saveSecret")):
    bad.append("renderProviders/providerRow/saveSecret is missing")
if 'api("/api/secrets")' not in block:
    bad.append("the page does not call /api/secrets")
if "STATE.providers" not in block:
    bad.append("no fallback to STATE.providers for an older server")
if 'method: "POST"' not in block:
    bad.append("the save path is not a POST")
if 'type="password"' not in block:
    bad.append("the input is not type=password")
if 'autocomplete="new-password"' not in block:
    bad.append("the input invites the browser to autofill a saved secret")
if "d.error" not in block:
    bad.append("a server refusal (e.g. 403) is not shown to the reader")
# Strip comments before the persistence/logging check: the block's own comment
# says a value never reaches localStorage, and a bare mention is not a call.
code_only = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
code_only = re.sub(r"//[^\n]*", "", code_only)
if any(tok in code_only for tok in ("localStorage", "sessionStorage", "document.cookie")):
    bad.append("the secrets block persists to storage or cookies")
if "console." in code_only:
    bad.append("the secrets block logs")

# The only write into an element's .value is the post-save clear.
for m in re.finditer(r"\.value\s*=\s*([^;\n]+)", block):
    if m.group(1).strip() not in ('""', "''"):
        bad.append("a value is written back into the DOM: %s" % m.group(0).strip())
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

if it "the browser UI refuses a key file that is not git-ignored"; then
    failures="$(python3 - 2>&1 <<'PY'
import os, sys, subprocess, tempfile, pathlib
sys.path.insert(0, "lib/linux")
from serve import secrets_post_response

for var in ("GIT_DIR", "GIT_WORK_TREE"):
    os.environ.pop(var, None)
repo = pathlib.Path(tempfile.mkdtemp(prefix="autoos-secrepo-"))
subprocess.run(["git", "init", "-q", str(repo)], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
keys = repo / "api-keys.yml"
os.environ["AUTOOS_KEYS_FILE"] = str(keys)
bad = []

code, payload = secrets_post_response({"id": "groq", "value": "test-value-tracked"}, "127.0.0.1")
if code != 409:
    bad.append("an unignored in-repo key file got %s, want 409" % code)
if keys.exists():
    bad.append("the refused write still created the file")

(repo / ".gitignore").write_text("api-keys.yml\n", encoding="utf-8")
code, payload = secrets_post_response({"id": "groq", "value": "test-value-ignored"}, "127.0.0.1")
if code != 200 or not keys.exists():
    bad.append("an ignored in-repo key file got %s (exists=%s)" % (code, keys.exists()))
print("; ".join(bad))
PY
)"
    if [[ -z "$failures" ]]; then pass; else fail "$failures"; fi
fi

