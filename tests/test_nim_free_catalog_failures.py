#!/usr/bin/env python3
"""Fail-closed NIM reads against populated snapshots, using loopback HTTP only."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from test_nim_free_catalog import COUNTS, CatalogCase, catalog


class CatalogFailureTests(CatalogCase):
    def test_wrong_publisher_never_joins_same_slug(self):
        self.fake.payloads["models"]["data"][0]["id"] = "another/glm-5.3-flash"
        self.assertEqual(self.run_tool()[0], 0)
        self.assertIsNone(self.rows()["glm-5-3-flash"]["v1_id"])
        self.assertEqual(self.read_snapshot()["counts"]["callable"], 2)

    def test_no_publisher_never_joins_same_slug(self):
        row = self.fake.payloads["label"]["results"][1]["resources"][1]
        row["labels"] = [label for label in row["labels"] if label["key"] != "publisher"]
        self.assertEqual(self.run_tool()[0], 0)
        self.assertIsNone(self.rows()["glm-5-3-flash"]["v1_id"])

    def test_publisher_uses_slug_normalisation(self):
        row = self.fake.payloads["label"]["results"][1]["resources"][1]
        row["labels"][1]["values"] = ["Z_Ai"]
        self.fake.payloads["models"]["data"][0]["id"] = "z.ai/glm-5.3-flash"
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(self.rows()["glm-5-3-flash"]["v1_id"], "z.ai/glm-5.3-flash")

    def test_empty_query_two_preserves_populated_snapshot(self):
        self.seed_snapshot()
        self.fake.payloads["all-0"].update(resultTotal=0, resultPageTotal=0, results=[])
        self.assertIn("empty", self.assert_error_preserves().lower())

    def test_query_two_missing_label_resource_is_inconsistent(self):
        self.seed_snapshot()
        # Keep its own totals consistent: query 2 is complete but not a superset.
        self.fake.payloads["all-0"]["results"][0]["resources"].pop(0)
        for name in ("all-0", "all-1"):
            self.fake.payloads[name]["resultTotal"] = 5
        self.assertIn("inconsistent", self.assert_error_preserves().lower())

    def test_failed_replace_preserves_bytes_and_cleans_temp(self):
        self.seed_snapshot()
        before = self.snapshot.read_bytes()

        def fail_replace(source, destination):
            self.assertEqual(source.parent, self.snapshot.parent)
            self.assertEqual(destination, self.snapshot)
            self.assertNotEqual(source, destination)
            self.assertNotEqual(source.read_bytes(), before)
            self.assertEqual(json.loads(source.read_bytes())["generated"], "2026-11-14")
            raise OSError("replace refused")

        with patch.object(catalog.os, "replace", side_effect=fail_replace) as replace:
            code, _, err = self.run_tool(today="2026-11-14")
        self.assertEqual((code, "replace refused" in err), (2, True))
        replace.assert_called_once()
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(list(self.snapshot.parent.iterdir()), [self.snapshot])

    def test_500_retried_once_then_error_preserves_snapshot(self):
        self.seed_snapshot()
        for flags in ((), ("--check",)):
            self.fake.script["all-1"] = [(500, {}), (500, {})]
            err = self.assert_error_preserves(*flags)
            self.assertIn(self.base + "/v2/search/catalog/resources/ENDPOINT?q=", err)
            self.assertIn("500", err)
        self.assertEqual(sum(r[0] == "all-1" for r in self.fake.requests), 4)

    def test_400_not_retried(self):
        self.seed_snapshot()
        self.fake.script["label"] = [(400, {})]
        self.assertIn("400", self.assert_error_preserves())
        self.assertEqual(len(self.fake.requests), 1)

    def test_timeout_retried_once_then_preserves_snapshot(self):
        self.seed_snapshot()
        self.fake.script["label"] = [("timeout", {}), ("timeout", {})]
        with patch.object(catalog, "TIMEOUT_SECONDS", 0.05):
            err = self.assert_error_preserves()
        self.assertIn("timeout", err.lower())
        self.assertIn(self.base, err)
        self.assertEqual(len(self.fake.requests), 2)

    def test_empty_label_is_error_not_everything_removed(self):
        self.seed_snapshot()
        self.fake.payloads["label"].update(resultTotal=0, resultPageTotal=0, results=[])
        self.assertIn("empty", self.assert_error_preserves().lower())

    def test_parse_errors_and_incomplete_pages_preserve_snapshot(self):
        self.seed_snapshot()
        for payload in (b"not json", {}, {"data": [{"id": None}]}):
            with self.subTest(payload=payload):
                self.fake.script["models"] = [(200, payload)]
                self.assert_error_preserves("--check", "--json")
        self.fake.payloads["all-1"]["results"][0]["resources"] = []
        self.assert_error_preserves()

    def test_truncated_http_body_preserves_snapshot(self):
        self.seed_snapshot()
        self.fake.script["models"] = [("truncated", {"data": []})]
        err = self.assert_error_preserves()
        self.assertIn(self.base + "/v1/models", err)
        self.assertEqual(sum(r[0] == "models" for r in self.fake.requests), 1)

    def test_redirect_not_followed(self):
        self.seed_snapshot()
        self.fake.script["label"] = [(302, {})]
        self.assertIn("302", self.assert_error_preserves())
        self.assertEqual(len(self.fake.requests), 1)

    def test_catalog_http_200_error_object_or_missing_results_preserves(self):
        self.seed_snapshot()
        for payload in ({"error": "unavailable"},
                        {"resultTotal": 5, "resultPageTotal": 1}):
            with self.subTest(payload=payload):
                self.fake.script["label"] = [(200, payload)]
                self.assertIn("HTTP 200", self.assert_error_preserves())

    def test_models_500_twice_preserves_snapshot(self):
        self.seed_snapshot()
        self.fake.script["models"] = [(500, {}), (500, {})]
        err = self.assert_error_preserves()
        self.assertIn(self.base + "/v1/models: HTTP 500", err)
        self.assertEqual(sum(r[0] == "models" for r in self.fake.requests), 2)

    def test_null_attributes_are_empty_not_a_fetch_error(self):
        for name in ("label", "all-0"):
            for group in self.fake.payloads[name]["results"]:
                for resource in group["resources"]:
                    if resource["resourceId"].endswith("/body-pose"):
                        resource["attributes"] = None
        code, _, err = self.run_tool()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.rows()["body-pose"]["match_reason"], "label")
        self.assertEqual(self.read_snapshot()["counts"], COUNTS)

    def test_more_than_50_pages_refused_before_page_two(self):
        self.seed_snapshot()
        for name in ("label", "all-0"):
            with self.subTest(query=name):
                payload = dict(self.fake.payloads[name], resultPageTotal=51)
                self.fake.script[name] = [(200, payload)]
                self.assertIn("50", self.assert_error_preserves())
                self.assertEqual(sum(r[0] == name for r in self.fake.requests), 1)
                self.fake.requests.clear()

    def test_either_test_override_requires_explicit_snapshot(self):
        self.seed_snapshot()
        before = self.snapshot.read_bytes()
        for name in self.env:
            for flags in ([], ["--check"]):
                with self.subTest(override=name, flags=flags), \
                        patch.dict(os.environ, {}, clear=True):
                    os.environ[name] = self.base
                    out, err = io.StringIO(), io.StringIO()
                    with patch.object(catalog, "DEFAULT_SNAPSHOT", self.snapshot), \
                            patch.object(catalog, "snapshot_of",
                                         side_effect=AssertionError("must not fetch")), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = catalog.main(flags)
                    self.assertEqual(code, 2)
                    self.assertIn("--snapshot", err.getvalue())
                    self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_output_encoding_or_flush_error_precedes_any_write(self):
        self.seed_snapshot()
        before = self.snapshot.read_bytes()

        class BrokenFlush(io.StringIO):
            def flush(self):
                raise OSError("stdout flush refused")

        for stream in (io.TextIOWrapper(io.BytesIO(), encoding="ascii"), BrokenFlush()):
            with self.subTest(stream=type(stream).__name__):
                resource = self.fake.payloads["all-1"]["results"][0]["resources"][-1]
                resource["resourceId"] += "\N{GREEK SMALL LETTER ALPHA}"
                err = io.StringIO()
                with patch.dict(os.environ, self.env), contextlib.redirect_stdout(stream), \
                        contextlib.redirect_stderr(err):
                    code = catalog.main(["--snapshot", str(self.snapshot)])
                self.assertEqual(code, 2)
                self.assertTrue(err.getvalue())
                self.assertEqual(self.snapshot.read_bytes(), before)
                self.assertEqual(list(self.snapshot.parent.iterdir()), [self.snapshot])

    def test_serialisation_failure_preserves_bytes_and_leaves_no_temp(self):
        self.seed_snapshot()
        before = self.snapshot.read_bytes()
        with patch.object(catalog, "serialise", side_effect=ValueError("cannot serialise")):
            self.assertEqual(self.run_tool()[0], 2)
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(list(self.snapshot.parent.iterdir()), [self.snapshot])


if __name__ == "__main__":
    unittest.main(verbosity=2)
