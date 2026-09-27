#!/usr/bin/env python3
"""Catalog lint: no two components in one catalog file install the same thing.

`lib/linux/catalog.sh` (and its Windows/macOS counterparts) reject a duplicate
component *id* when the catalog loads, but nothing rejects two entries that
carry the same `provider` + `package` under different ids. That shape is
almost never intentional: two ids would both claim the same package, so
`--only`/profile resolution could install it twice, detection would report
both "installed", and the menu would show the same software twice. It is
exactly the kind of drift the catalog's single-source-of-truth rule exists to
prevent.

This module is deliberately a *pure-function* lint (no install, no network),
plus self-tests on synthetic catalogs so the aggregate run cannot pass just
because the detector is wired to always return "no duplicates". Run:

    python3 -m pytest -q tests/test_catalog_uniqueness.py
    python3 tests/test_catalog_uniqueness.py
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "catalog"
# The per-OS component catalogs. Any other `catalog/*.json` (schemas, image
# lists, model registries, ...) has no `categories` and is skipped by
# `component_catalogs`.
CATALOG_FILES = ("linux.json", "windows.json", "macos.json")


def component_catalogs():
    """Every catalog document that actually holds installable components.

    Keyed off the file shape, not a hard-coded list of names, so a future
    `catalog/<os>.json` is covered without touching this test.
    """
    found = []
    for path in sorted(CATALOG_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("categories"), list):
            found.append((path, data))
    return found


def duplicate_packages(data):
    """Map each duplicated `(provider, package, arch, cask, source)` to the ids that share it.

    A component with no `package` (a `script`/`custom` entry that installs by
    running a function) has nothing to deduplicate and is ignored, as is a
    non-string package. Components with the same package under *different*
    providers are not a conflict: `apt`'s `foo` and a `script`'s `foo` are
    different install paths.

    Legitimate splits with different `arch`, `cask` (macOS), or `source`
    (winget) are also not conflicts: they target different platforms or
    installation methods.

    Returns `{key: [id, ...]}`, empty when the catalog is clean.
    """
    seen = {}
    for category in data.get("categories", []):
        for component in category.get("components", []):
            package = component.get("package")
            if not isinstance(package, str) or not package:
                continue
            provider = component.get("provider")
            arch = component.get("arch")
            cask = component.get("cask")
            source = component.get("source")
            # Normalize: None/empty -> empty tuple for arch (which is a list), False -> None for cask
            arch_key = tuple(sorted(arch)) if isinstance(arch, list) else ()
            cask_key = cask if cask else None
            source_key = source if source else None
            key = (provider, package, arch_key, cask_key, source_key)
            seen.setdefault(key, []).append(component.get("id"))
    return {key: ids for key, ids in seen.items() if len(ids) > 1}


def synthetic(components):
    """A minimal catalog document wrapping `components` under one category."""
    return {"categories": [{"id": "fake", "components": components}]}


class DetectorSelfTests(unittest.TestCase):
    """Prove the detector can fail; otherwise the aggregate test is assert(true)."""

    def test_reports_a_duplicate_provider_package(self):
        data = synthetic([
            {"id": "a", "provider": "apt", "package": "ripgrep"},
            {"id": "b", "provider": "apt", "package": "ripgrep"},
            {"id": "c", "provider": "apt", "package": "fd-find"},
        ])
        self.assertEqual(
            duplicate_packages(data),
            {("apt", "ripgrep", (), None, None): ["a", "b"]},
        )

    def test_does_not_report_the_same_package_under_different_providers(self):
        data = synthetic([
            {"id": "a", "provider": "apt", "package": "foo"},
            {"id": "b", "provider": "snap", "package": "foo"},
        ])
        self.assertEqual(duplicate_packages(data), {})

    def test_ignores_components_without_a_package(self):
        data = synthetic([
            {"id": "a", "provider": "script"},
            {"id": "b", "provider": "script", "package": ""},
        ])
        self.assertEqual(duplicate_packages(data), {})

    def test_does_not_report_same_package_different_arch(self):
        data = synthetic([
            {"id": "a", "provider": "apt", "package": "foo", "arch": ["x64"]},
            {"id": "b", "provider": "apt", "package": "foo", "arch": ["arm64"]},
        ])
        self.assertEqual(duplicate_packages(data), {})

    def test_does_not_report_same_package_different_cask(self):
        data = synthetic([
            {"id": "a", "provider": "brew", "package": "foo", "cask": True},
            {"id": "b", "provider": "brew", "package": "foo", "cask": False},
        ])
        self.assertEqual(duplicate_packages(data), {})

    def test_does_not_report_same_package_different_source(self):
        data = synthetic([
            {"id": "a", "provider": "winget", "package": "foo", "source": "msstore"},
            {"id": "b", "provider": "winget", "package": "foo", "source": "winget"},
        ])
        self.assertEqual(duplicate_packages(data), {})

    def test_covers_every_component_catalog(self):
        # Guard against the discovery function silently returning nothing
        # (e.g. the shape check drifting): every named OS catalog must be
        # found, and none may be empty.
        found = {path.name: data for path, data in component_catalogs()}
        for name in CATALOG_FILES:
            self.assertIn(name, found, f"{name} was not recognised as a component catalog")
            count = sum(len(c.get("components", [])) for c in found[name]["categories"])
            self.assertGreater(count, 0, f"{name} yielded no components")


class RealCatalogTests(unittest.TestCase):
    def test_no_catalog_file_duplicates_a_provider_package(self):
        problems = []
        for path, data in component_catalogs():
            for (provider, package), ids in sorted(duplicate_packages(data).items()):
                problems.append(f"{path.name}: {provider}/{package} shared by {ids}")
        self.assertEqual(problems, [], "duplicate provider+package in catalog:\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
