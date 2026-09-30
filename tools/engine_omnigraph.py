#!/usr/bin/env python3
"""Omnigraph-as-one-graph engine adapter for MEMSPEC memory facade.

Build-only skeleton: explicit guard refuses real execution until the engine
phase. No Omnigraph graph loading or queries are performed — the adapter
simply validates the call signature and returns a refusal stub.

Spec §9: this adapter sits behind the same facade interface as the other
two candidates, so callers never see which engine runs.
"""

# Build-only guard: real engines cannot run in build-only mode
_BUILD_ONLY_MODE = True


def _refuse_build_only(method_name: str) -> dict:
    """Return a refusal dict for build-only mode — no engine execution."""
    return {
        "error": f"build-only mode: {method_name} cannot execute real engine "
                 "until the engine phase (P1 host gate / pids dip / L1-main word). "
                 "Set BUILD_ONLY_MODE=False to enable real execution.",
    }


# ─── the five facade methods — identical signatures to MemoryFacade ──────

def recall(query: str, project: str | None = None, k: int = 10) -> dict:
    """Compact lines for a query — or the full entity when the query IS an id."""
    return _refuse_build_only("recall")


def context_pack(project: str, role: str) -> dict:
    """The graph half of the session-start bundle (spec:88-91)."""
    return _refuse_build_only("context_pack")


def remember(kind: str, title: str, body: str, project: str,
             links=None, source: str | None = None,
             domain: str | None = None, visibility: str = "project") -> dict:
    """Upsert one fact under a canonical `kind:slug` id."""
    return _refuse_build_only("remember")


def link(a: str, rel: str, b: str, source: str | None = None) -> dict:
    """Idempotent on (a, rel, b): a repeat call is a no-op (spec:67)."""
    return _refuse_build_only("link")


def supersede(old: str, new: str, reason: str, source: str | None = None) -> dict:
    """Close the old fact (`valid_to`), keep its history readable (spec:68)."""
    return _refuse_build_only("supersede")