"""The lane child environment's NAME lists: one source of truth.

`oc_l1.env_is_allowed` applies them, `oc_l1_serve._child_env` builds the child's
environment from it, and `oc_l1` re-exports every name (tests and callers read
`oc_l1.LANE_ENV_ALLOW` etc.). Names only - no value is ever read here.
"""

# --- the lane child environment (Sonnet final REJECT 2026-10-08, finding 2) ---
#
# `start` handed the lane `dict(os.environ)`, so every credential the launcher's
# shell happened to export - a GitHub token, a provider key, a database password -
# sat in /proc/<pid>/environ where the lane, the MCP server it starts and every
# worker it spawns could read it. A lane is a model session: it gets the variables
# a session legitimately needs and nothing else.
#
# The rule is one allowlist of NAMES plus one allowlist of NAME PREFIXES (the
# families a real session needs: git's own internals, XDG, locale, the fleet's
# AUTOOS_* knobs), minus every credential-shaped name inside those families.
# LANE_ENV_ALLOW beats the credential pattern - the two gateway names below are
# the rendered config's own `{env:...}` references, so the lane cannot reach its
# model without them, and they are the only credentials a lane is ever handed.
# Anything else a lane needs is declared by NAME in the lane's `child_env`; the
# value is inherited, never stored in a lane config file.

# Windows system locations: fixed paths, never credentials. A child that cannot
# resolve %SystemDrive% creates a literal `%SystemDrive%` directory under its cwd
# (seen on the recycle drill). os.environ upper-cases every name on Windows, so
# the exact-case allowlist alone would never match SYSTEMDRIVE: oc_l1_serve's
# _child_env forwards this family by case-insensitive match, on Windows only.
LANE_ENV_WINDOWS = ("SystemRoot", "SystemDrive", "ProgramData", "ProgramFiles",
                    "ProgramFiles(x86)", "CommonProgramFiles")
LANE_ENV_ALLOW = LANE_ENV_WINDOWS + (
    # finding the binaries and writing to a home
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "USERPROFILE", "WINDIR", "COMSPEC",
    "PATHEXT", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS",
    # locale, terminal, timezone and colour, so output matches the launcher's
    "LANG", "LANGUAGE", "TERM", "TZ", "PAGER", "EDITOR", "VISUAL",
    "NO_COLOR", "FORCE_COLOR", "COLORTERM",
    # reaching the gateway through a proxy, and trusting its certificate
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY", "WAYLAND_DISPLAY",
    # the gateway pair: the rendered config references them as {env:...} and the
    # lane has no other way to its model. NAMES only - the values stay in the
    # launcher's environment.
    "AUTOOS_OMNIROUTE_URL", "AUTOOS_OMNIROUTE_KEY",
)
LANE_ENV_ALLOW_PREFIXES = (
    "LC_", "XDG_", "GIT_", "OPENCODE_", "NODE_", "PYTHON", "SSL_", "OPENSSL_",
    "AUTOOS_", "SESSION_", "OMNIGRAPH_", "OMNIROUTE_",  # non-secret names, see below
)
# A name ending in one of these holds a credential, whatever family it sits in.
LANE_ENV_CREDENTIAL_SUFFIXES = ("_PW", "_PWD", "_KEY", "_KEYFILE", "_APIKEY",
                                "_TOKEN", "_SECRET", "_PASSWORD", "_PASSPHRASE",
                                "_CREDENTIAL")
LANE_ENV_CREDENTIAL_WORDS = ("PASSWORD", "PASSPHRASE", "SECRET", "CREDENTIAL",
                             "TOKEN", "APIKEY")


def forward_windows_names(env, environ):
    """Copy the LANE_ENV_WINDOWS names the launcher has into `env`, by presence.

    Matching is case-insensitive (Windows spells SYSTEMDRIVE however it likes and
    os.environ upper-cases it); the child gets the canonical spelling. A name the
    lane already carries keeps its own entry, an empty value is skipped, and a
    name the launcher lacks is never invented. System paths, not credentials
    (WINDIR precedent in LANE_ENV_ALLOW).
    """
    have = {n.upper() for n in env}
    for want in LANE_ENV_WINDOWS:
        if want.upper() in have:
            continue
        for name, value in environ.items():
            if name.upper() == want.upper() and value:
                env[want] = value
                break
