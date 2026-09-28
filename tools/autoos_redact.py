"""One home for the secret patterns (SPAWNREDACT, lesson inbox
2026-09-27T18:58:28Z: a worker's REPORT printed a found secret verbatim to the
caller's terminal, its log and its commit message).

Two consumers, one pattern set:

* ``tools/hostexec/audit.py`` redacts a *stored* argv list -> ``redact_argv``.
* ``tools/autoos-agent.py`` redacts *text streams* a worker produced ->
  ``Redactor``, which is line-oriented so a spawner can keep streaming
  (a PEM private key spans lines, so it carries a little state).

The value shapes are the hostexec ones (review F12/F13: bearer tokens,
``api_key:``/``KEY=value`` carriers, ``user:pass@`` URLs, known key prefixes)
plus the forms a worker's report actually contains: the vendor key prefixes
(``sk-``, ``sk-or``, ``sk-ant``, ``ghp_``/``gho_``/``github_pat_``, ``AIza``,
``xox[bp]-``, ``glpat-``), ``key|token|secret|password = value`` assignments,
PEM private-key blocks, and the exact values of the secret-named variables the
spawner injects into the child env (``AUTOOS_OMNIROUTE_KEY`` and friends) - a
key with an unknown shape still has to be masked when the spawner itself is the
one that handed it over.

Matching cost is part of the contract (REDACTFIX item 1): ``Redactor`` runs in
the spawner's output pump thread, once per line of a stream whose line length
the worker chooses. Every pattern here is linear in the line length: the
assignment scan - the one shape that cannot be written as a single regex
without two unbounded quantifiers overlapping the keyword literals, which cost
104 s for a 200 KB line and hung the run - walks the line once and decides
keyword membership in Python.

Stdlib only, no I/O, no logging: both consumers run in places where importing
this must never fail.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Sequence

# hostexec's stored-argv mask (its tests pin the exact text); the spawner's
# text streams use a marker that cannot collide with prose or markdown.
MASK = "***"
TEXT_MASK = "[autoos:redacted]"

# An injected env value shorter than this is too generic to mask as a literal
# (it would eat ordinary output).
MIN_LITERAL_LEN = 12

# Backstop on the assignment scan (REDACTFIX item 1). The scan itself is linear,
# so this is not what keeps a long line affordable - it bounds what one line can
# cost *at all* in the pump thread. The name and value walks are Python
# character loops (the only per-carrier cost that does not run at C speed), so
# the worst case - one line that is a single enormous token with a `=` in it -
# scales at roughly 0.1 s per MiB here; 4 MiB keeps that under half a second
# while leaving every real report line (a base64 dump, a one-line JSON log)
# inside the scan. Above the cap the assignment carriers of that line are not
# scanned, and only that line: the bearer/URL/prefix/PEM patterns and the
# injected-literal scan still run over the whole line.
ASSIGNMENT_SCAN_CAP = 4 * 1024 * 1024

# ─── names and values ──────────────────────────────────────────────────────

_SECRET_NAME_RE = re.compile(r"(?i)(token|secret|password|passwd|bearer|api[_-]?key|credential|key)")
_SECRET_VALUE_FLAGS = ("--token", "--password", "--passwd", "--secret", "--key",
                       "--api-key", "--bearer", "-u", "-p")
_SECRET_PREFIX_RE = re.compile(r"^(sk-|ghp_|gho_|github_pat_|aiza|xox|glpat-)", re.IGNORECASE)
_BEARER_IN_TOKEN_RE = re.compile(r"(?i)bearer\s+\S+")
_API_KEY_IN_TOKEN_RE = re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)\S+")
_URL_USERPASS_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s:@]+@")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")

# A vendor key appearing anywhere in a line of prose or code. The 6-char tail
# is what keeps "sk-" in "the sk- prefix list" from masking a whole report.
_PREFIX_TOKEN_RE = re.compile(
    r"\b(?:sk-(?:or-|ant-)?|ghp_|gho_|github_pat_|AIza|xox[bp]-|glpat-)[A-Za-z0-9_./\-]{6,}")
# ``api_key = v``, ``TOKEN: v``, ``my-secret  = v``: a secret-NAMED left-hand
# side with a value. The name must be a bare word-ish token, so prose such as
# "the key paths are listed" (no ``:``/``=``) is left alone. This subsumes the
# hostexec ``x-api-key: v`` carrier, so the text path applies only it - two
# overlapping subs would count one secret twice and re-mask its own marker.
#
# Not one regex (REDACTFIX item 1 rewrote it): the name, the separator and the
# value in a single expression means the engine has to try every way of
# splitting a keyword-padded run before it can conclude there is no carrier.
# ``_SEPARATOR_RE`` finds the separators (a charset scan, linear), the name run
# in front of each is walked back over characters that cannot themselves be a
# separator - so the walks of one line never overlap and sum to the line length
# - and Python checks the keyword set, which is what the old
# ``(?:[a-z0-9_][a-z0-9_.\-]*)?(?:key|token|...)`` prefix group meant: any of
# these words anywhere in the name run names it secret.
_SEPARATOR_RE = re.compile(r"[=:]")
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_ASSIGNMENT_KEYWORDS = ("key", "token", "secret", "password", "passwd", "credential")

_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


def sanitize_text(s: str) -> str:
    """Strip C0/C1 control characters (tab kept) so a stored/rendered field
    can never inject a terminal escape or a fake extra log line.

    Note: 0x0a (LF) and 0x0d (CR) are C0 controls too and MUST be stripped --
    only 0x09 (tab) is kept."""
    return _CONTROL_RE.sub("", s)


def secret_env_values(env) -> list[str]:
    """The literal values worth masking in a worker's output: the secret-named
    entries (*KEY*/*TOKEN*/*SECRET*/*PASSWORD*/*BEARER*/*CREDENTIAL*) of the env
    the spawner hands the child, at least MIN_LITERAL_LEN characters long.

    Case-insensitive on the NAME, exact on the VALUE (these are matched by
    plain substring replacement, never by a regex built from them).
    """
    out = []
    for name, value in (env or {}).items():
        if not isinstance(value, str) or len(value) < MIN_LITERAL_LEN:
            continue
        if _SECRET_NAME_RE.search(name or ""):
            out.append(value)
    return out


# ─── text streams ──────────────────────────────────────────────────────────

def redact_values(text: str, values: Sequence[str], mask: str) -> tuple[str, int]:
    """Replace each injected literal, and return how many were replaced."""
    hits = 0
    for value in values:
        if not value:
            continue
        n = text.count(value)
        if n:
            text = text.replace(value, mask)
            hits += n
    return text, hits


def _assignment_sub(text: str) -> tuple[str, int]:
    """Mask the value of every secret-NAMED ``name = value`` carrier, in one
    linear pass."""
    out: list[str] = []
    hits = 0
    pos = 0
    n = len(text)
    for sep in _SEPARATOR_RE.finditer(text):
        if sep.start() < pos:
            continue                      # inside a value that is already masked
        head = sep.start()
        while head > pos and text[head - 1].isspace():
            head -= 1
        start = head
        while start > pos and text[start - 1] in _NAME_CHARS:
            start -= 1
        if start == head:
            continue                      # nothing named, nothing to judge
        if not any(k in text[start:head].lower() for k in _ASSIGNMENT_KEYWORDS):
            continue
        val = sep.end()
        while val < n and text[val].isspace():
            val += 1
        end = val
        while end < n and not text[end].isspace():
            end += 1
        if end == val:
            continue                      # `key = ` with no value masks nothing
        out.append(text[pos:val])
        out.append(TEXT_MASK)
        pos = end
        hits += 1
    if not hits:
        return text, 0
    out.append(text[pos:])
    return "".join(out), hits


def _apply_line_patterns(text: str) -> tuple[str, int]:
    """The per-line value shapes, in the order that keeps hostexec's pinned
    behaviour (bearer first, then the named carriers, then bare prefixes)."""
    hits = 0
    text, n = _BEARER_IN_TOKEN_RE.subn("Bearer " + TEXT_MASK, text)
    hits += n
    if len(text) <= ASSIGNMENT_SCAN_CAP:
        text, n = _assignment_sub(text)
        hits += n
    text, n = _URL_USERPASS_RE.subn(r"\1" + TEXT_MASK + ":" + TEXT_MASK + "@", text)
    hits += n
    text, n = _PREFIX_TOKEN_RE.subn(TEXT_MASK, text)
    hits += n
    return text, hits


class Redactor:
    """Line-oriented redactor for a stream the caller reads as it arrives.

    ``text()`` may be fed a whole report or one line at a time; a PEM block
    that spans lines is masked as it passes (the BEGIN line becomes the marker,
    the body is swallowed until the END line). ``count`` is how many secrets
    have been masked so far, so the spawner can tell the caller it did
    something instead of silently altering the worker's output.
    """

    def __init__(self, values: Iterable[str] = ()):
        self.values: list[str] = []
        self.count = 0
        self._in_pem = False
        self.add_values(values)

    def add_values(self, values: Iterable[str]) -> None:
        for value in values or ():
            if isinstance(value, str) and len(value) >= MIN_LITERAL_LEN and value not in self.values:
                self.values.append(value)

    def add_env(self, env) -> None:
        self.add_values(secret_env_values(env))

    def reset_count(self) -> None:
        self.count = 0

    def text(self, chunk: str) -> str:
        if not chunk:
            return chunk
        out = []
        for line in chunk.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            out.append(self._one_line(body) + line[len(body):])
        return "".join(out)

    def _one_line(self, line: str) -> str:
        if self._in_pem:
            if _PEM_END_RE.search(line):
                self._in_pem = False
            return ""
        begin = _PEM_BEGIN_RE.search(line)
        if begin:
            # A whole key on one line (a log record of it) is masked in place;
            # an open BEGIN starts the swallow until the matching END line.
            self.count += 1
            self._in_pem = not _PEM_END_RE.search(line, begin.end())
            return TEXT_MASK
        # The injected literals go first: they are the values we KNOW are
        # secret, and masking them whole before the patterns run means a
        # pattern can never mask the middle of one and leave its ends visible.
        masked, hits = redact_values(line, self.values, TEXT_MASK)
        masked, n = _apply_line_patterns(masked)
        self.count += hits + n
        return masked


# ─── argv (hostexec's stored form) ─────────────────────────────────────────

def _argv_text(tok: object) -> str:
    """One argv element as text. Elements are usually str; MCP JSON can also
    carry a number/bool/null, which policy refuses as argv-caps but which
    the audit must still render -- a refused call is always recorded, so
    redaction may never raise on one. json.dumps gives a stable,
    JSON-faithful spelling (`123`, `true`, `null`)."""
    if isinstance(tok, str):
        return tok
    try:
        return json.dumps(tok, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(tok)


def redact_argv(argv: Sequence[object], values: Sequence[str] = ()) -> list[str]:
    """Best-effort credential redaction for the STORED/rendered argv:
    `KEY=value`-shaped names that look secret (*TOKEN*|*SECRET*|*PASSWORD*|
    *KEY*), `Bearer <token>` separate or inside one token
    (`Authorization: Bearer x`), common `--token`/`--password`/... flags
    with a separate value, mysql-style `-pSECRET` and `-uUSER:PASS`
    attached, `x-api-key: <v>`, `user:pass@` in URLs, known secret
    prefixes (sk-, ghp_, gho_, github_pat_, AIza, xox, glpat-) and the exact
    values of the injected keys in `values`. hostexec's `argv_sha256`
    (``audit.hash_argv``) hashes THIS redacted form -- deliberately not the
    raw one, because a digest of a raw secret is offline-guessable -- so a
    secret's value never reaches, or influences, the stored record."""
    out: list[str] = []
    mask_next = False
    for tok_raw in argv:
        tok = sanitize_text(_argv_text(tok_raw))
        if mask_next:
            out.append(MASK)
            mask_next = False
            continue
        if tok == "Bearer":
            out.append(tok)
            mask_next = True
            continue
        if _SECRET_PREFIX_RE.match(tok):
            out.append(MASK)
            continue
        # Single-token carriers inside a larger token.
        redacted = _BEARER_IN_TOKEN_RE.sub("Bearer " + MASK, tok)
        redacted = _API_KEY_IN_TOKEN_RE.sub(r"\1" + MASK, redacted)
        redacted = _URL_USERPASS_RE.sub(r"\1" + MASK + ":" + MASK + "@", redacted)
        redacted, _n = redact_values(redacted, values, MASK)
        if redacted != tok:
            out.append(redacted)
            continue
        if "=" in tok:
            name, _, _value = tok.partition("=")
            bare = name.lstrip("-")
            if _SECRET_NAME_RE.search(bare):
                out.append(f"{name}={MASK}")
                continue
            out.append(tok)
            continue
        if tok.startswith("-p") and len(tok) > 2 and not tok.startswith("--"):
            out.append("-p" + MASK)
            continue
        if tok.startswith("-u") and len(tok) > 2 and not tok.startswith("--"):
            # Attached -uUSER is just a username (keep, e.g. -uroot);
            # -uUSER:PASS carries a secret (mask).
            if ":" in tok[2:]:
                out.append("-u" + MASK)
            else:
                out.append(tok)
            continue
        if tok in _SECRET_VALUE_FLAGS:
            out.append(tok)
            mask_next = True
            continue
        out.append(tok)
    return out
