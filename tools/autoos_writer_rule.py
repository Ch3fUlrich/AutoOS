"""Pre-spawn writer guard: R-scale judgement + writer allow-list (0007 P1)."""
from __future__ import annotations
import re
TASK_TYPES = ("ops", "code", "docs", "infra")
R_LEVELS = ("R0", "R1", "R2", "R3")
_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
_FLOOR = {"ops": "R2", "infra": "R2", "code": "R1", "docs": "R0"}
# R3 auth/permissions/sessions/secrets/ACL on paths and diff added lines.
# Word boundaries: 'author'/'authority' never match (word char follows h).
_R3 = re.compile(r"auth/|(?:^|/)auth\.py\b|\bauth(?:n|z)?\b|\bpermissions?\b"
                 r"|\bacls?\b|\bsecrets?\b|\.env\b|\bsessions?\b", re.I)
_OPS = re.compile(r"\.ya?ml$|\bcompose\b|\.service\b|\bsystemd\b|\bcron|\bmail"
                  r"|\bplaybooks?/|\bansible|\bhosts\b|\binventory\b", re.I)
def _added(diff_text):
    lines = (diff_text or "").splitlines()
    plus = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
    return plus if plus or any(l.startswith(("@@", "diff ", "---")) for l in lines) else lines
def _checked(task_type, r_level=None):
    if task_type is not None and task_type not in TASK_TYPES:
        raise ValueError("unknown task_type %r" % (task_type,))
    if r_level is not None and r_level not in R_LEVELS:
        raise ValueError("unknown r_level %r" % (r_level,))
def required_r_level(task_type, paths, diff_text=""):
    """Explicit task_type floors the level; path/diff hints only ever raise
    a mislabelled card, never lower it."""
    _checked(task_type)
    seen = [str(p) for p in (paths or [])]
    if _R3.search("\n".join(seen)) or any(_R3.search(l) for l in _added(diff_text)):
        content = "R3"
    elif any(_OPS.search(p) for p in seen):
        content = "R2"
    elif not seen or all(p.lower().endswith(".md") for p in seen):
        content = "R0"
    else:
        content = "R1"
    floor = _FLOOR.get(task_type, "R0")
    return content if _RANK[content] >= _RANK[floor] else floor
def _writers(registry):
    policy = registry.get("policy") if isinstance(registry, dict) else None
    writers = policy.get("writers") if isinstance(policy, dict) else None
    return writers if isinstance(writers, dict) else {}
def r2_chain(registry):
    """Available R2 writer legs in registry order (available:false skipped)."""
    legs = _writers(registry).get("R2") or []
    return [l for l in legs if isinstance(l, dict) and l.get("available") is not False]
def writer_allowed(model_id, r_level, task_type, registry):
    """Sub-40 ids (containment on the normalized id) only at R0/R1, never on
    ops; R2/R3 only for an available chain leg; anything may do R0/R1."""
    _checked(task_type, r_level)
    norm = str(model_id or "").strip().lower()
    subs = _writers(registry).get("sub40_models") or []
    if any(isinstance(s, str) and s and s.lower() in norm for s in subs):
        return r_level in ("R0", "R1") and task_type != "ops"
    if r_level in ("R0", "R1"):
        return True
    return any(isinstance(l.get("model"), str) and l["model"]
               and l["model"].lower() in norm for l in r2_chain(registry))
