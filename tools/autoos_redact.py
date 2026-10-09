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
PEM and PGP-armored private-key blocks, and the exact values of the
secret-named variables the
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

# The label may run past `PRIVATE KEY` to the ` BLOCK` suffix an armored PGP
# secret keyring carries: RFC 4880 armour frames a key with the same BEGIN/END
# markers as a PEM key, and the old pattern required the label to END at `KEY`,
# so a whole armored keyring printed in the clear (AO-REDACT-SPAN P3). A PUBLIC
# key label still does not match — only a PRIVATE label is secret.
_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")
# What the lines AFTER an open BEGIN may be: a base64 body line, or a header
# line — the PEM encryption headers (`Proc-Type:`, `DEK-Info:`) and the PGP
# armour headers. Anything else is not key material, so PEM mode ends and that
# line is processed like every other line (AO-REDACT-SPAN: the old blanket
# swallow ate a report that merely MENTIONED a BEGIN marker, VERDICT line
# included).
#
# A body line is `[ \t]*[A-Za-z0-9+/=]*[ \t]*` — so the empty line, a
# whitespace-only line, an indented line and the ragged LAST line of a real key
# ("YQ==", "abc=", "===") all count as body, and so does the armour's `=XXXX`
# CRC line. The old `{16,}` fullmatch called every one of those "not key
# material", ended PEM mode early and printed the rest of a real private key —
# the END marker and everything after it — in plaintext (AO-REDACT-SPAN rework,
# attacker review).
#
# Written as strip-then-core-fullmatch rather than that one expression: the two
# `[ \t]*` flanks make the engine re-scan the whitespace run on every backtrack
# (quadratic on a 200 KB line, which is exactly the cost the pump thread may not
# pay), while `strip` + a single character class are two linear C-speed walks.
#
# KNOWN LIMIT (A), pinned by `tests/test_autoos_redact_span.py`: the alphabet is
# STANDARD base64, all RFC 7468 permits in a PEM body. A line written in the
# URL-safe alphabet (`-` and `_` where `+` and `/` belong) is NOT body, so it
# ends PEM mode and the lines after it are ordinary lines. Deliberate: a body
# that strays from the PEM alphabet is not a PEM block, and swallowing on
# anything-and-everything is the over-eating this shape replaced. The cost is
# that a base64url-encoded key handed over under an open marker is masked only
# by the per-line value patterns, never by the span.
_PEM_BODY_RE = re.compile(r"[A-Za-z0-9+/=]*")
# One scan for both markers, built from the two grammars above so a label
# widened later is widened in the scan and in the pair walk at once. Named
# groups say which kind matched; the patterns have no groups of their own.
_PEM_MARKER_RE = re.compile("(?P<begin>%s)|(?P<end>%s)"
                            % (_PEM_BEGIN_RE.pattern, _PEM_END_RE.pattern))
# The characters RFC 7468 permits in a PEM body — what a key's own text is made
# of, and therefore what may never sit unmasked in front of a marker. `-` and `_`
# are NOT here: limit (A) says base64url is not PEM, and a hyphen is what prose
# and a quoted marker have in common ("not-a-key-----BEGIN"), while a run that
# reaches back over a hyphen would eat the sentence.
_PEM_ALPHABET_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                                "0123456789+/=")

# `Version:`/`Comment:`/`MessageID:`/`SessionKey:`/`Label:`/`Arc:` are the
# RFC 4880 armour headers a secret keyring puts between its BEGIN line and its
# body, one blank line ahead of it (a blank line is body-shaped already, so the
# separator needs no rule of its own).
_PEM_HEADER_RE = re.compile(
    r"[ \t]*(?:Proc-Type|DEK-Info|Version|Comment|Message-?ID|SessionKey|Label|Arc):")


def _is_pem_body_line(line: str) -> bool:
    """True for a line that is base64 body, blank, or whitespace-only — see
    ``_PEM_BODY_RE`` for the shape and the reason it is not one regex."""
    return _PEM_BODY_RE.fullmatch(line.strip(" \t")) is not None


def _glue_start(line: str, pos: int, floor: int) -> int:
    """Start of the PEM-alphabet run that ends at ``pos`` (a marker glued to key
    text), never before ``floor`` — AO-REDACT-SPAN P4.

    Returns ``pos`` when the character in front of the marker is whitespace or
    not key material: prose that quotes a marker is separated from it, so what
    sits in front of the ``-----`` is not body and stays visible. Walking from
    ``pos`` with a monotonic floor keeps the runs of one line non-overlapping, so
    the whole line still costs one linear pass."""
    i = pos
    while i > floor and line[i - 1] in _PEM_ALPHABET_CHARS:
        i -= 1
    return i


# A worker's verdict is how the caller learns the outcome; it is never swallowed
# and it always closes an open PEM block. Leading indent is tolerated (a verdict
# inside a fenced block or a markdown list is still a verdict).
_VERDICT_LINE_RE = re.compile(r"[ \t]*VERDICT:")

# Backstop for an open BEGIN whose END never arrives: at most this many lines
# (body AND blanks/headers) are swallowed before PEM mode is given up on. A real
# RSA-2048/4096 key body is 6-30 lines, so the cap is far above any genuine
# block; every swallowed line counts, blanks included, because a blank line is
# body and a key copy-pasted out of a terminal is full of them.
#
# 256 rather than 200: an armored PGP secret keyring is not one key body but a
# primary key plus its subkeys, each with its own signatures and CRC line, so
# the room a single RSA-4096 needs is the floor of what a real block costs, not
# the ceiling. The headroom is paid only in lines a report could lose, and the
# shape still ends the mode at the first non-body line, so an ordinary report
# never walks into it.
#
# COST OF THE PERMISSIVE BODY SHAPE (accepted, attacker review): a report that
# MENTIONS an unclosed BEGIN and is followed by one or two lines that are
# alnum-only — a bare word, a hash, `MERGE_OK`-without-underscore — looks like
# key body, so those lines are swallowed. Only those lines: the first line that
# is not body-shaped ends PEM mode, and a VERDICT line ends it and survives.
PEM_BODY_CAP = 256


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

    ``text()`` may be fed a whole report or one line at a time; a PEM block or a
    PGP armored one that spans lines is masked as it passes: every BEGIN..END
    *span* on the line becomes the marker (a second and third block on the same
    line are masked too), the key-alphabet text GLUED in front of a marker goes
    with it, and the lines under an open BEGIN are swallowed while they look like
    key body — base64, blank, indented, a PEM encryption header or a PGP armour
    header — until the END line, the first line that is not, ``PEM_BODY_CAP``
    lines, or a VERDICT line. A line that closes an open BEGIN is masked in full
    (fail closed): under an open marker there is no safe way to tell prose from
    the key's own last body line, and a lost line is recoverable while a printed
    key is not. A report that merely mentions a BEGIN marker therefore still
    reaches the caller, verdict included
    (AO-REDACT-SPAN; the body shape itself is the rework — a short last line, a
    blank or an indent inside a real key used to end the block early and print
    its tail in plaintext).

    ``count`` is how many secrets have been masked so far, so the spawner can
    tell the caller it did something instead of silently altering the worker's
    output. One key counts once: the END line closes the block without adding.
    """

    def __init__(self, values: Iterable[str] = ()):
        self.values: list[str] = []
        self.count = 0
        self._in_pem = False
        self._pem_bodies = 0
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

    def _exit_pem(self) -> None:
        self._in_pem = False
        self._pem_bodies = 0

    def _masked(self, line: str) -> str:
        """The per-line masking of a line that is not key body: the injected
        literals go first (they are the values we KNOW are secret, and masking
        them whole before the patterns run means a pattern can never mask the
        middle of one and leave its ends visible), then the value shapes."""
        masked, hits = redact_values(line, self.values, TEXT_MASK)
        masked, n = _apply_line_patterns(masked)
        self.count += hits + n
        return masked

    def _mask_markers(self, line: str):
        """Mask every marker shape ``line`` carries, in one left-to-right walk
        (AO-REDACT-SPAN P4). Returns the masked text, or ``None`` when the line
        holds no marker at all and the caller masks it as ordinary text.

        * a complete ``BEGIN..END`` pair masks from the marker to the end of the
          closing marker — and the walk keeps going, so a second and third block
          on the same line are masked too; the old single search stopped at the
          first pair and printed the rest of the line's keys in the clear;
        * an unclosed ``BEGIN`` masks to the end of the line and opens the
          body-only swallow;
        * key text GLUED in front of any marker (no whitespace between the
          base64 and the ``-----``) is masked with it, from the start of that
          run — which for a line that begins with body means the whole line.

        Each pair and each open BEGIN counts one key; a stray ``END`` nobody is
        waiting for masks its own glued run without counting, since the BEGIN it
        closes was never in this stream.
        """
        out: list[str] = []
        pos = 0
        opened = False
        for marker in _PEM_MARKER_RE.finditer(line):
            if marker.start() < pos:
                continue                      # inside a span already masked
            start = _glue_start(line, marker.start(), pos)
            if marker.group("begin") is not None:
                self.count += 1
                closing = _PEM_END_RE.search(line, marker.end())
                if closing:
                    stop = closing.end()
                else:
                    stop = len(line)          # the rest of the line may be body
                    opened = True
            else:
                stop = marker.end()
            out.append(line[pos:start])
            out.append(TEXT_MASK)
            pos = stop
            if opened:
                break
        if pos == 0:
            return None
        out.append(line[pos:])
        if opened:
            self._in_pem = True
            self._pem_bodies = 0
        return self._masked("".join(out))

    def _one_line(self, line: str) -> str:
        if _VERDICT_LINE_RE.match(line):
            # Never swallowed, and it ends a block: the caller's one mandatory
            # line must survive an unterminated PEM marker.
            #
            # KNOWN LIMIT (B), pinned by `tests/test_autoos_redact_span.py`:
            # because the verdict is passed through rather than dropped, key
            # text a worker APPENDS to it after an open BEGIN survives — a
            # bare base64 run is not a shape any per-line pattern masks. The
            # trade is deliberate and one-sided: a report that lost its verdict
            # to a swallow is useless to the caller that reads it (the original
            # AO-REDACT-SPAN failure), while a worker that puts key material on
            # its own verdict line has already broken the rule it was told to
            # report with. What the per-line patterns do name on that line —
            # bearer tokens, `key=`/`token:` carriers, vendor prefixes, the
            # injected literals — is still masked there.
            self._exit_pem()
            return self._masked(line)
        if self._in_pem:
            closing = _PEM_END_RE.search(line)
            if closing:
                # Fail closed (AO-REDACT-SPAN P4): a line that closes an open
                # BEGIN is part of the key's own text, and the last body line of
                # a wrapped or single-line key sits GLUED to this marker with no
                # whitespace to tell it from prose — so the whole line is masked
                # and the text that marker was glued to cannot ride through. One
                # lost report line is recoverable; one printed key is not.
                self._exit_pem()
                end = closing.end()
                while (nxt := _PEM_END_RE.search(line, end)):
                    end = nxt.end()          # the rightmost END on the line
                if _PEM_BEGIN_RE.search(line, end):
                    # A line that closes this key and opens the NEXT one (a
                    # one-line log record of two keys) is still masked in full,
                    # but the second key's body goes on underneath, so the
                    # swallow has to restart rather than print its tail.
                    self._in_pem = True
                    self.count += 1
                return TEXT_MASK
            if (self._pem_bodies < PEM_BODY_CAP
                    and (_is_pem_body_line(line) or _PEM_HEADER_RE.match(line))):
                self._pem_bodies += 1
                return ""
            self._exit_pem()              # not key body: this line is a normal line
        masked = self._mask_markers(line)
        return self._masked(line) if masked is None else masked


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
