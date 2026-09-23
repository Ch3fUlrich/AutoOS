"""Load the ``cao`` block of ``handoff.config.json`` plus provider secrets.

Secrets live OUTSIDE the config on purpose: ``handoff.config.json`` is committed,
``secrets/api_keys.conf`` never is (see ``secrets/README.md``). A pool whose key
is missing is *unavailable*, not an error — routing ladders skip it exactly as
they skip a quota-cooling pool, so an absent key degrades instead of raising.
That symmetry is deliberate: one code path handles "no credentials yet" and
"limit reached", and neither can take a run down.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SECRETS = Path("secrets/api_keys.conf")


def find_secrets(start) -> Path:
    """Locate the provider secrets file. First hit wins:

    1. ``$AUTOOS_SECRETS`` (a file path);
    2. ``~/.config/autoos/api_keys.conf`` (the user scope, on every OS);
    3. legacy: ``secrets/api_keys.conf``, walking up from ``start`` to the git
       root, with a one-line notice on stderr.

    The legacy file sits at the REPOSITORY root, while handoff.config.json may
    live in a subdirectory (the skill folder, an adopting repo's config dir).
    Resolving it relative to the config's directory reports "NO CREDENTIALS" for
    a key that is present — a false negative that makes a working pool look dead.
    """
    env = os.environ.get("AUTOOS_SECRETS")
    if env:
        # An explicit path is a promise: falling through would silently use a different
        # key and label it "legacy" (D4; found by review, 2026-09-18).
        if not Path(env).is_file():
            raise FileNotFoundError(f"AUTOOS_SECRETS points to a missing file: {env}")
        return Path(env)
    user = Path.home() / ".config" / "autoos" / "api_keys.conf"
    if user.is_file():
        return user
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        found = candidate / DEFAULT_SECRETS
        if found.is_file():
            print(f"notice: using legacy secrets file {found}; move it to "
                  "~/.config/autoos/api_keys.conf (AUTOOS_SECRETS overrides)",
                  file=sys.stderr)
            return found
        if (candidate / ".git").exists():
            break  # git root reached; do not escape the repository
    return here / DEFAULT_SECRETS


def load_secrets(path) -> dict:
    """Parse ``key=value`` lines. Blank lines and ``#`` comments are ignored.

    Only the FIRST ``=`` splits, so a value containing ``=`` survives verbatim —
    several providers issue keys with padding characters.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


@dataclass
class CaoConfig:
    max_depth: int
    server: str
    worktree_root: str
    levels: list
    pools: dict
    routing: dict
    review_pairing: str = "cross-family"
    resume: dict = field(default_factory=dict)
    repo_root: Path = field(default_factory=lambda: Path("."))

    def __repr__(self) -> str:
        # Deliberately narrow: this object is logged on preflight, and a default
        # dataclass repr would print every pool's configuration into the log.
        return f"CaoConfig(max_depth={self.max_depth}, pools={sorted(self.pools)})"


class ConfigMissing(FileNotFoundError):
    """No handoff.config.json. Actionable, not a stack trace."""


def load_config(repo_root) -> CaoConfig:
    root = Path(repo_root)
    cfg_file = root / "handoff.config.json"
    if not cfg_file.is_file():
        example = root / "handoff.config.example.json"
        hint = (
            f"copy {example.name} to handoff.config.json and edit it"
            if example.is_file()
            else "run the batch runner's -Init, or copy handoff.config.example.json"
        )
        raise ConfigMissing(f"no {cfg_file} — {hint}")
    raw = json.loads(cfg_file.read_text(encoding="utf-8"))
    if "cao" not in raw:
        raise ValueError(
            f"{cfg_file} has no 'cao' block; copy one from "
            "handoff.config.example.json"
        )
    c = raw["cao"]
    return CaoConfig(
        max_depth=int(c.get("maxDepth", 3)),
        server=c.get("server", "http://localhost:9889"),
        # Deliberately NOT expanded here. On Windows this value is consumed by a
        # command that runs inside WSL, and expanding $HOME on the driving host
        # produced "C:\Users\<you>/cao-worktrees" for a Linux shell. The
        # distro's login shell expands it correctly; cao.worktree.resolve_root
        # expands it for local operations.
        worktree_root=c.get("worktreeRoot", "$HOME/cao-worktrees"),
        levels=c.get("levels", []),
        pools=c.get("pools", {}),
        routing=c.get("routing", {}),
        review_pairing=c.get("reviewPairing", "cross-family"),
        resume=c.get("resume", {}),
        repo_root=root.resolve(),
    )


def pool_available(cfg: CaoConfig, pool: str, secrets: dict) -> bool:
    """Can this pool be dispatched to at all?

    A pool with no ``apiKeyEnv`` authenticates through its own CLI (``claude``,
    ``agy`` both hold their own session), so it is available by definition.
    """
    spec = cfg.pools.get(pool)
    if spec is None:
        return False
    if not spec.get("apiKeyEnv"):
        return True
    return bool(secrets.get(pool))
