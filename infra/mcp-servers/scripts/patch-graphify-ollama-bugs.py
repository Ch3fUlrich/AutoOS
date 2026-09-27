#!/usr/bin/env python3
"""Patch known graphify bugs that surface when using a noisy local Ollama backend.

Local models (especially smaller ones like the ones used for a free/offline
graphify setup) occasionally return malformed JSON for a semantic-extraction
chunk: a bare string where a node/edge dict is expected, or a numeric ID
where a string ID is expected. graphify v0.9.4 (the latest release on PyPI
as of this writing) does not defensively guard against this and crashes
instead of just dropping the malformed entry. See mcp-servers/README.md
("Graphify + local Ollama - known gotchas") for the full story.

This script finds every graphify install in the uv cache (uv may extract the
same wheel into multiple content-addressed cache dirs depending on the
resolved environment for each target repo) and applies three defensive,
behavior-preserving patches:

1. __main__.py: skip non-dict entries when merging per-chunk semantic
   results into the aggregate node/edge lists (this is the actual root
   cause - a malformed response with `"nodes": "some string"` gets
   naively `.extend()`-ed, injecting one single-character string per
   character into the nodes list).
2. __main__.py: apply the same filter before writing the per-chunk
   semantic cache, so future runs actually get incremental caching
   instead of silently failing to write the cache every time.
3. ids.py: coerce `normalize_id`'s input to `str` before running
   unicodedata.normalize, so a stray non-string ID doesn't crash the
   graph-build step.

Idempotent - safe to run repeatedly (e.g. from init-graphify-projects.ps1
before every extraction) and safe to run before graphify's uv cache entry
even exists yet for a given repo (in which case it just finds nothing).

Also clears any stale __pycache__/*.pyc next to a patched file, since uv
cache directories can be hard-linked across resolutions - editing the
source in place updates every hard-linked copy, but each copy's compiled
bytecode is a separate derived artifact that needs its own invalidation.
"""

import glob
import os
import subprocess
import sys


def uv_cache_dir() -> str:
    try:
        out = subprocess.run(
            ["uv", "cache", "dir"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        # Fall back to uv's documented default locations.
        if sys.platform == "win32":
            return os.path.expandvars(r"%LOCALAPPDATA%\uv\cache")
        return os.path.expanduser("~/.cache/uv")


PATCHES_MAIN = [
    (
        '        _sem_extracted: set[str] = {\n'
        '            n.get("source_file", "") for n in sem_result.get("nodes", [])\n'
        '        } | {\n'
        '            e.get("source_file", "") for e in sem_result.get("edges", [])\n'
        '        }\n',
        '        _sem_extracted: set[str] = {\n'
        '            n.get("source_file", "") for n in sem_result.get("nodes", []) if isinstance(n, dict)\n'
        '        } | {\n'
        '            e.get("source_file", "") for e in sem_result.get("edges", []) if isinstance(e, dict)\n'
        '        }\n',
    ),
    (
        '                sem_result["nodes"].extend(fresh.get("nodes", []))\n'
        '                sem_result["edges"].extend(fresh.get("edges", []))\n'
        '                sem_result["hyperedges"].extend(fresh.get("hyperedges", []))\n',
        '                sem_result["nodes"].extend(n for n in fresh.get("nodes", []) if isinstance(n, dict))\n'
        '                sem_result["edges"].extend(e for e in fresh.get("edges", []) if isinstance(e, dict))\n'
        '                sem_result["hyperedges"].extend(h for h in fresh.get("hyperedges", []) if isinstance(h, dict))\n',
    ),
    (
        '                    _save_semantic_cache(\n'
        '                        fresh.get("nodes", []),\n'
        '                        fresh.get("edges", []),\n'
        '                        fresh.get("hyperedges", []),\n'
        '                        root=out_root,\n'
        '                    )\n',
        '                    _save_semantic_cache(\n'
        '                        [n for n in fresh.get("nodes", []) if isinstance(n, dict)],\n'
        '                        [e for e in fresh.get("edges", []) if isinstance(e, dict)],\n'
        '                        [h for h in fresh.get("hyperedges", []) if isinstance(h, dict)],\n'
        '                        root=out_root,\n'
        '                    )\n',
    ),
]

PATCHES_IDS = [
    (
        '    s = unicodedata.normalize("NFKC", s)\n',
        '    s = unicodedata.normalize("NFKC", str(s))\n',
    ),
]


def find_graphify_files(cache_root: str, filename: str) -> list[str]:
    patterns = [
        os.path.join(cache_root, "archive-v0", "*", "Lib", "site-packages", "graphify", filename),
        os.path.join(cache_root, "archive-v0", "*", "lib", "python*", "site-packages", "graphify", filename),
        os.path.join(cache_root, "archive-v0", "*", "graphify", filename),
    ]
    found: set[str] = set()
    for pattern in patterns:
        found.update(glob.glob(pattern))
    return sorted(found)


def apply_patches(path: str, patches: list[tuple[str, str]]) -> tuple[bool, int]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    changed = False
    already = 0
    for old, new in patches:
        if new in content:
            already += 1
            continue
        if old in content:
            content = content.replace(old, new)
            changed = True
        else:
            print(f"  WARN: expected pattern not found in {path} (graphify version may have changed - patch may be stale)")
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return changed, already


def clear_pycache(source_path: str) -> None:
    pycache_dir = os.path.join(os.path.dirname(source_path), "__pycache__")
    if not os.path.isdir(pycache_dir):
        return
    stem = os.path.splitext(os.path.basename(source_path))[0]
    for pyc in glob.glob(os.path.join(pycache_dir, f"{stem}.*.pyc")):
        os.remove(pyc)


def main() -> None:
    cache_root = uv_cache_dir()
    main_files = find_graphify_files(cache_root, "__main__.py")
    ids_files = find_graphify_files(cache_root, "ids.py")

    if not main_files and not ids_files:
        print("No graphify installs found in the uv cache yet - nothing to patch.")
        print("(This is normal on first run before `uv run --with graphifyy[ollama] graphify ...` has been invoked once.)")
        return

    for f in main_files:
        changed, already = apply_patches(f, PATCHES_MAIN)
        clear_pycache(f)
        status = "patched" if changed else f"already patched ({already}/3)"
        print(f"__main__.py: {f} -> {status}")

    for f in ids_files:
        changed, already = apply_patches(f, PATCHES_IDS)
        clear_pycache(f)
        status = "patched" if changed else f"already patched ({already}/1)"
        print(f"ids.py: {f} -> {status}")


if __name__ == "__main__":
    main()
