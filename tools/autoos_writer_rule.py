"""Pre-spawn writer guard: R-scale judgement + writer allow-list (0007 P1)."""
from __future__ import annotations

import re

TASK_TYPES = ("ops", "code", "docs", "infra")
R_LEVELS = ("R0", "R1", "R2", "R3")
_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
_FLOOR = {"ops": "R2", "infra": "R2", "code": "R1", "docs": "R0"}
# R3 auth/permissions/sessions/secrets/ACL on paths and diff added lines.
# Separators are non-letters: '_' and digits never join a token, so
# 'auth_utils'/'session_store' match while 'author'/'authority'/'authorize'
# (a letter follows 'auth') never do. Lookarounds (?<![a-z0-9])...(?![a-z])
# with re.I express exactly that: '_' passes both sides, a trailing digit
# passes, 'oauth'/'password'/'api_key' are their own tokens.
# camelCase/PascalCase is split first ('userAuth' -> 'user_Auth'), so the same
# lowercase rule also catches 'userAuth'/'SessionStore'/'PermissionCheck'/
# 'apiKey' while all-lowercase 'author'/'authority'/'authorize-docs' still miss.
_R3 = re.compile(r"auth/|(?<![a-z0-9])oauth(?![a-z])"
                 r"|(?<![a-z0-9])auth(?:n|z)?(?![a-z])"
                 r"|(?<![a-z0-9])permissions?(?![a-z])"
                 r"|(?<![a-z0-9])acls?(?![a-z])"
                 r"|(?<![a-z0-9])secrets?(?![a-z])"
                 r"|(?<![a-z0-9])sessions?(?![a-z])"
                 r"|(?<![a-z0-9])passwords?(?![a-z])"
                 r"|(?<![a-z0-9])api[_-]?keys?(?![a-z])"
                 r"|(?<![a-z0-9])credentials?(?![a-z])"
                 r"|(?<![a-z0-9])tokens?(?![a-z])"
                 r"|(?<![a-z0-9])passwd(?![a-z])"
                 r"|(?<![a-z0-9])ssh[_-]?keys?(?![a-z])"
                 r"|(?<![a-z0-9])id_rsa(?![a-z])"
                 r"|(?<![a-z0-9])authorized[_-]?keys?(?![a-z])"
                 r"|(?<![a-z0-9])private[_-]?keys?(?![a-z])"
                 r"|(?<![a-z0-9])keystores?(?![a-z])"
                 r"|\.env(?![a-z])|\.pem(?![a-z])|(?:^|/)auth\.py(?![a-z])", re.I)
_CAMEL_BOUND = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_OPS = re.compile(r"\.ya?ml$|\.tf$|\bcompose\b|\.service\b|\bsystemd\b|\bcron|\bmail"
                  r"|\bplaybooks?/|\bansible|\bhosts\b|\binventory\b"
                  r"|(?:^|/)ops/|\bdeploy|\binfra|\bterraform", re.I)


def _r3_hit(text):
    """True when text carries an R3 token, camel-split first (round 3)."""
    return bool(_R3.search(text) or _R3.search(_CAMEL_BOUND.sub("_", text)))


def _added(diff_text):
    lines = (diff_text or "").splitlines()
    plus = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++ ")]
    return plus if plus or any(l.startswith(("@@", "diff ", "---")) for l in lines) else lines


def _checked(task_type, r_level=None):
    if task_type is not None and (not isinstance(task_type, str) or task_type not in TASK_TYPES):
        raise ValueError("unknown task_type %r" % (task_type,))
    if r_level is not None and r_level not in R_LEVELS:
        raise ValueError("unknown r_level %r" % (r_level,))


def required_r_level(task_type, paths, diff_text=""):
    """Explicit task_type floors the level; path/diff hints only ever raise
    a mislabelled card, never lower it.

    task_type=None (no explicit type) floors at R1 for non-docs work: an
    empty path list reads R1, docs-only (.md) still reads R0, and R2/R3
    content hints still raise.
    """
    _checked(task_type)
    seen = [str(p) for p in (paths or [])]
    if _r3_hit("\n".join(seen)) or any(_r3_hit(l) for l in _added(diff_text)):
        content = "R3"
    elif any(_OPS.search(p) for p in seen):
        content = "R2"
    elif not seen or all(p.lower().endswith(".md") for p in seen):
        content = "R0"
    else:
        content = "R1"
    if task_type is None:
        if content in ("R2", "R3"):
            return content
        if seen and all(p.lower().endswith(".md") for p in seen):
            return "R0"
        return "R1"
    floor = _FLOOR.get(task_type, "R0")
    return content if _RANK[content] >= _RANK[floor] else floor


def _writers(registry):
    policy = registry.get("policy") if isinstance(registry, dict) else None
    writers = policy.get("writers") if isinstance(policy, dict) else None
    return writers if isinstance(writers, dict) else {}


def _chain(registry, key):
    """Available writer legs for one key in registry order (available:false skipped)."""
    legs = _writers(registry).get(key) or []
    return [l for l in legs if isinstance(l, dict) and l.get("available") is not False]


def r2_chain(registry):
    """Available R2 writer legs in registry order (available:false skipped)."""
    return _chain(registry, "R2")


def r3_chain(registry):
    """Available R3 writer legs in registry order (available:false skipped)."""
    return _chain(registry, "R3")


def _canonical(model_id):
    """The bare model name: after the last '/' without any ':tag' suffix."""
    return str(model_id or "").strip().lower().split("/")[-1].split(":")[0]


def _leg_prefixes(client):
    """Accepted 'provider/' spellings for a chain leg's client.

    Consistent with policy.writers legs ({client, model} pairs) and the
    registry's own vertex_ai <-> vertex alias (providers.vertex_ai id,
    gateway connection 'vertex'): a vertex leg answers to both spellings.
    Every other client answers to its own id (plus '_'/'-' variants).
    """
    c = str(client or "").strip().lower()
    if c in ("vertex", "vertex_ai"):
        return ("vertex/", "vertex_ai/")
    return tuple({c + "/", c.replace("-", "_") + "/", c.replace("_", "-") + "/"})


def _chain_match(model_id, legs):
    """A leg with a client matches only a provider-prefixed id for that
    client (exact bare model after the prefix, optional ':tag' stripped);
    a bare id or another provider's id never matches. A leg without a
    client falls back to the old exact bare-name match."""
    base = str(model_id or "").strip().lower().split(":")[0]
    for leg in legs:
        model = leg.get("model")
        if not (isinstance(model, str) and model):
            continue
        want = _canonical(model)
        if not want:
            continue
        client = leg.get("client")
        if isinstance(client, str) and client.strip():
            for prefix in _leg_prefixes(client):
                if base.startswith(prefix):
                    rest = base[len(prefix):]
                    if rest and "/" not in rest and rest == want:
                        return True
        elif _canonical(model_id) == want:
            return True
    return False


def writer_allowed(model_id, r_level, task_type, registry, ledger_path=None):
    """Sub-40 ids (containment on the normalized id) only at R0/R1, never on
    ops; R2 only for an available R2 chain leg, R3 only for an available R3
    leg (no R3 key falls back to the R2 chain; an explicit empty R3 list
    still denies); anything may do R0/R1.

    Fail closed: with policy.writers or sub40_models absent/empty every R2/R3
    request and every ops request is denied.

    With ledger_path given, a model demoted() flags for this task_type is
    denied at every r_level. The path itself fails closed: a symlink
    (broken or not), a directory (or any non-file), or an unreadable file
    denies; only a path that does not exist at all reads as an empty
    (not-demoted) ledger.
    """
    _checked(task_type, r_level)
    if ledger_path is not None:
        import os as _os
        try:
            if not _os.path.lexists(ledger_path):
                pass  # absent path: empty ledger, not demoted
            elif _os.path.islink(ledger_path) or not _os.path.isfile(ledger_path):
                return False  # symlink (broken or not), directory, ...: deny
            else:
                with open(ledger_path, "rb"):
                    pass  # readability probe: unreadable denies
                import autoos_writer_ledger as _ledger
                if _ledger.demoted(model_id, task_type, path=ledger_path):
                    return False
        except Exception:
            return False
    writers = _writers(registry)
    subs = writers.get("sub40_models")
    if not writers or not isinstance(subs, list) or not subs:
        if r_level in ("R2", "R3") or task_type == "ops":
            return False
        subs = subs if isinstance(subs, list) else []
    norm = str(model_id or "").strip().lower()
    if any(isinstance(s, str) and s and s.lower() in norm for s in subs):
        return r_level in ("R0", "R1") and task_type != "ops"
    if r_level in ("R0", "R1"):
        return task_type != "ops" or bool(writers and subs)
    if r_level == "R3":
        if "R3" not in writers:
            return _chain_match(model_id, _chain(registry, "R2"))
        return _chain_match(model_id, _chain(registry, "R3"))
    return _chain_match(model_id, _chain(registry, "R2"))
