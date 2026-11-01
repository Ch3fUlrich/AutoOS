"""The pre-push gate's two push-time refusals (AO-PREPUSH-HOOK-GITENV-2).

Kept beside ``tools/prepush.py`` (which hands in every function it needs, so
there is no import cycle and a test that patches the gate's seams still bites)
because that file is baselined and may only shrink (AO-400LOC).

1. FIXTURE IDENTITY. Incident 2026-10-12: a test ran ``git config user.name
   risk-test`` in a process that inherited ``GIT_DIR``, and the fixture
   identity landed in the REAL repo's config, where every later commit was
   authored by it. ``identity_gate`` refuses (exit 1) while the repo-local
   ``user.email``/``user.name`` — local scope, plus worktree scope when
   ``extensions.worktreeConfig`` is on — matches a fixture pattern: an email
   host of ``example.invalid`` (or a subdomain), or ``risk-test`` in either
   value. A config git cannot read fails closed with exit 2. The message names
   the key, the scope and the pattern; it never prints the config. Not skipped
   by ``AUTOOS_PREPUSH_OVERRIDE``: a polluted identity is repo damage, not a
   check an orchestrator ran another way.

2. PER-PUSHED-SHA GATING. A push of 952af69d from a checkout at d3f7ab06
   printed ``green ... for d3f7ab06``: the hook gated the checkout's HEAD, not
   what was pushed. With ``--hook-stdin`` the shim passes git's ref lines
   (``<local ref> <local sha> <remote ref> <remote sha>``) and ``hook_gate``
   certifies each pushed sha: a zero sha (deletion) is reported and skipped; the
   sha that is the checkout's HEAD runs the gate as ever; any other sha is
   refused unless the record store already holds a green record binding that
   sha and its tree (``ready_gate``). The override does not reach a sha that is
   not HEAD: nothing was run for it and there is no checkout to run it in.
"""
import os
import re
import subprocess

#: The exit codes of ``tools/prepush.py`` (``REFUSED`` / ``COULD_NOT_RUN``);
#: tests/test_prepush_hook.py pins them equal.
REFUSED = 1
COULD_NOT_RUN = 2
FIXTURE_HOST = "example.invalid"
FIXTURE_TOKEN = "risk-test"
IDENTITY_KEYS = ("user.email", "user.name")
ZERO_SHA = re.compile(r"0+\Z")


class ConfigUnreadable(Exception):
    """git could not answer a config query (not merely "the key is unset")."""


def fixture_pattern(key, value):
    """The fixture pattern ``value`` of config ``key`` matches, or None."""
    low = (value or "").strip().lower()
    if FIXTURE_TOKEN in low:
        return FIXTURE_TOKEN
    if key == "user.email" and "@" in low:
        host = low.rpartition("@")[2].rstrip(".>")
        if host == FIXTURE_HOST or host.endswith("." + FIXTURE_HOST):
            return FIXTURE_HOST
    return None


def _config(repo, env, *args):
    """``git config <args>`` stdout lines; ``[]`` when the key is unset (exit
    1); ConfigUnreadable for anything else."""
    try:
        proc = subprocess.run(["git", "-C", str(repo), "config"] + list(args),
                              capture_output=True, text=True, env=env)
    except OSError as exc:
        raise ConfigUnreadable("git did not start: %s" % exc)
    if proc.returncode == 1:
        return []
    if proc.returncode != 0:
        first = (proc.stderr.strip().splitlines() or [""])[0][:200]
        raise ConfigUnreadable("git config %s exited %d: %s"
                               % (" ".join(args), proc.returncode, first))
    return proc.stdout.splitlines()


def identity_findings(repo, env=None):
    """``[(scope, key, pattern)]`` for every fixture identity in the repo's
    local (and, if enabled, worktree) config. Raises ConfigUnreadable."""
    env = dict(os.environ if env is None else env)
    scopes = ["--local"]
    if "true" in _config(repo, env, "--local", "--bool", "--get",
                         "extensions.worktreeConfig"):
        scopes.append("--worktree")
    found = []
    for scope in scopes:
        for key in IDENTITY_KEYS:
            for value in _config(repo, env, scope, "--get-all", key):
                pattern = fixture_pattern(key, value)
                if pattern:
                    found.append((scope[2:], key, pattern))
    return found


def identity_gate(repo):
    """None when the identity is clean, else the exit code (message printed)."""
    try:
        found = identity_findings(repo)
    except ConfigUnreadable as exc:
        print("prepush: could not read this repo's local git config (%s) -- "
              "failing closed, exit 2: a fixture identity cannot be ruled out."
              % exc)
        return COULD_NOT_RUN
    if not found:
        return None
    for scope, key, pattern in found:
        print("prepush: REFUSED -- the %s git config sets %s to a test-fixture "
              "value (matches %r). A test run from a worktree or a hook wrote "
              "it into this repo (AO-PREPUSH-HOOK-GITENV-2); every commit made "
              "here is authored by it." % (scope, key, pattern))
    print("prepush: remove it, e.g. `git config --%s --unset-all %s`, then push "
          "again. Nothing else was checked." % (found[0][0], found[0][1]))
    return REFUSED


def parse_ref_lines(text):
    """``[(local_ref, local_sha, remote_ref, remote_sha)]`` from git's pre-push
    stdin. Blank lines are ignored; a line that is not four fields is an error
    (ValueError) -- guessing at it could certify the wrong sha."""
    rows = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("expected '<local ref> <local sha> <remote ref> "
                             "<remote sha>', got %d field(s)" % len(fields))
        rows.append(tuple(fields))
    return rows


def hook_gate(repo, base, text, head_sha, resolve_sha, ready_gate, gate):
    """Certify every sha git says is being pushed. Returns the exit code.

    ``head_sha``/``resolve_sha``/``ready_gate``/``gate`` are the gate's own
    functions of those names, passed in by ``prepush.main``."""
    try:
        rows = parse_ref_lines(text)
    except ValueError as exc:
        print("prepush: the hook's stdin is not git's ref list (%s) -- cannot "
              "tell what is being pushed, so nothing is certified." % exc)
        return COULD_NOT_RUN
    head = None
    run_head = False
    refused = 0
    seen = set()
    for local_ref, local_sha, remote_ref, _ in rows:
        if ZERO_SHA.match(local_sha):
            print("prepush: %s -> %s is a ref deletion (zero sha): nothing to "
                  "certify, skipped" % (local_ref, remote_ref))
            continue
        sha = resolve_sha(repo, local_sha)
        if sha in seen:
            continue
        seen.add(sha)
        if head is None:
            head = head_sha(repo)
        if sha == head:
            run_head = True
            continue
        ok, why = ready_gate(repo, sha)
        if ok:
            print("%s -- pushed as %s, not this checkout's HEAD" % (why, local_ref))
            continue
        refused += 1
        print("prepush: REFUSED %s (%s -> %s): this checkout is at %s, not at "
              "the sha being pushed, and no green gate record binds that sha "
              "and its tree -- run prepush on a checkout of %s first.\n  %s"
              % (sha, local_ref, remote_ref, head, sha, why))
    if refused:
        print("prepush: push refused -- %d of %d pushed sha(s) were never "
              "certified%s" % (refused, len(seen),
                               "; the checkout's HEAD was not run" if run_head
                               else ""))
        return REFUSED
    if run_head:
        return gate(repo, base)
    print("prepush: nothing to run -- %s" % (
        "every pushed sha carries a green record" if seen
        else "no ref being pushed carries a commit"))
    return 0


def run(repo, base, text, head_sha, resolve_sha, ready_gate, gate):
    """The gate's push-time entry: identity first (always), then the checkout's
    HEAD (``text`` is None: a manual run) or the pushed shas (``text`` is the
    hook's stdin)."""
    rc = identity_gate(repo)
    if rc is not None:
        return rc
    if text is None:
        return gate(repo, base)
    return hook_gate(repo, base, text, head_sha, resolve_sha, ready_gate, gate)
