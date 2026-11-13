#!/usr/bin/env python3
"""NIM-FREE-CATALOG: real urllib and CLI on loopback only.

Fixtures are trimmed and adapted from public recordings; adaptations listed in
each file's _fixture note.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "nim-free-catalog.py"
FIXTURES = ROOT / "tests" / "fixtures" / "nim-free-catalog"
spec = importlib.util.spec_from_file_location("nim_free_catalog_under_test", TOOL)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)
ORG = "qc69jvmznzxy/"
COUNTS = {"label": 5, "attribute_only": 1, "total": 6, "callable": 3}


class FakeCatalog:
    """Canned pages; record every URL/query/header, never mock urlopen."""

    def __init__(self):
        self.payloads = {name: json.loads((FIXTURES / (name + ".json")).read_text())
                         for name in ("label", "all-0", "all-1", "models")}
        self.script = {}
        self.requests = []

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = None
                if parsed.path == "/v1/models":
                    name = "models"
                elif parsed.path == "/v2/search/catalog/resources/ENDPOINT":
                    query = json.loads(parse_qs(parsed.query)["q"][0])
                    label = {"field": "nimType", "value": "nim_type_preview"}
                    name = "label" if label in query["filters"] else "all-%d" % query["page"]
                else:
                    self.send_error(404)
                    return
                fake.requests.append((name, query, dict(self.headers)))
                queued = fake.script.get(name, [])
                status, payload = queued.pop(0) if queued else (200, fake.payloads[name])
                if status == "timeout":
                    threading.Event().wait(0.15)
                    status = 200
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                truncated = status == "truncated"
                try:
                    self.send_response(200 if truncated else status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw) + int(truncated)))
                    if status == 302:
                        self.send_header("Location", "/v1/models")
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshot = Path(self.tmp.name) / "snapshot.json"
        self.fake = FakeCatalog()
        self.base = self.fake.start()
        self.addCleanup(self.fake.stop)
        self.env = {"AUTOOS_NIM_CATALOG_BASE_URL": self.base,
                    "AUTOOS_NIM_V1_BASE_URL": self.base}

    def run_tool(self, *extra, today="2026-11-13"):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = catalog.main(["--snapshot", str(self.snapshot), *extra], today=today)
        return code, out.getvalue(), err.getvalue()

    def read_snapshot(self):
        return json.loads(self.snapshot.read_bytes())

    def rows(self):
        return {r["slug"]: r for r in self.read_snapshot()["rows"]}

    def seed_snapshot(self):
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(self.read_snapshot()["counts"], COUNTS)
        self.assertEqual(len(self.read_snapshot()["rows"]), 6)
        self.fake.requests.clear()

    def assert_error_preserves(self, *flags):
        before = self.snapshot.read_bytes()
        self.assertTrue(self.read_snapshot()["rows"])
        code, out, err = self.run_tool(*flags)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")  # No fabricated removed report on fetch errors.
        self.assertTrue(err)
        self.assertEqual(before, self.snapshot.read_bytes())
        return err


class CatalogTests(CatalogCase):
    def test_dedupe_pagination_reasons_and_counts(self):
        code, out, err = self.run_tool()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("added: " + ORG + "bnr", out)
        self.assertIn("removed: (none)", out)
        snapshot = self.read_snapshot()
        self.assertEqual(snapshot["counts"], COUNTS)
        ids = [r["resource_id"] for r in snapshot["rows"]]
        self.assertEqual(ids, sorted(set(ids)))
        rows = self.rows()
        self.assertEqual(rows["body-pose"]["match_reason"], "label")
        self.assertEqual(rows["bnr"]["match_reason"], "attribute")
        self.assertEqual(rows["glm-5-3-flash"]["match_reason"], "label+attribute")
        self.assertEqual([r[0] for r in self.fake.requests],
                         ["label", "all-0", "all-1", "models"])
        for name, query, headers in self.fake.requests:
            # Accept is deliberate; urllib adds the four transport headers below.
            # Exact equality excludes Authorization, Cookie, X-API-Key and extras.
            self.assertEqual({h.lower() for h in headers},
                             {"accept", "host", "user-agent", "accept-encoding", "connection"})
            if query is not None:
                self.assertEqual(query["query"], "orgName:qc69jvmznzxy")
                self.assertEqual(query["pageSize"], 100)
                self.assertEqual(query["fields"], [])
                self.assertEqual(query["orderBy"], [{"field": "score", "value": "DESC"}])
                expected = [{"field": "resourceType", "value": "endpoint"},
                            {"field": "isPublic", "value": "true"}]
                if name == "label":
                    expected += [{"field": "nimType", "value": "nim_type_preview"}]
                self.assertEqual(query["filters"], expected)

    def test_v1_join_normalisation_and_no_match(self):
        self.assertEqual(self.run_tool()[0], 0)
        rows = self.rows()
        self.assertEqual(rows["glm-5-3-flash"]["v1_id"], "z-ai/glm-5.3-flash")
        slug = "llama-3_1-nemotron-safety-guard-8b-v3"
        self.assertEqual(rows[slug]["v1_id"], "nvidia/" + slug.replace("_", "."))
        self.assertIsNone(rows["body-pose"]["v1_id"])
        self.assertEqual(catalog.normalise_slug("glm-5-3"), catalog.normalise_slug("glm-5.3"))
        self.assertNotEqual(catalog.normalise_slug("ab"), catalog.normalise_slug("a-b"))

    def test_ambiguous_join_never_guesses(self):
        self.fake.payloads["models"]["data"] += [{"id": "z-ai/glm-5_3-flash"}]
        self.assertEqual(self.run_tool()[0], 0)
        row = self.rows()["glm-5-3-flash"]
        self.assertIsNone(row["v1_id"])
        self.assertIs(row["v1_ambiguous"], True)
        self.assertEqual(self.read_snapshot()["counts"]["callable"], 2)

    def test_duplicate_v1_id_is_not_ambiguous(self):
        data = self.fake.payloads["models"]["data"]
        data.append(copy.deepcopy(data[0]))
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(self.read_snapshot()["counts"], COUNTS)

    def test_raw_labels_classification_and_missing_publisher(self):
        resources = self.fake.payloads["label"]["results"][1]["resources"]
        resources[0]["labels"] = [{"key": "general", "values": ["chat"],
                                   "unresolvedValues": ["unrecognised"]}]
        self.assertEqual(self.run_tool()[0], 0)
        rows = self.rows()
        self.assertIsNone(rows["body-pose"]["publisher"])
        self.assertEqual(rows["body-pose"]["use_class"], "other")
        self.assertEqual(rows["bnr"]["use_class"], "speech")
        self.assertEqual(rows["glm-5-3-flash"]["use_class"], "chat")
        self.assertEqual(rows["nemotron-3_5-embed-1b"]["use_class"], "embedding")
        self.assertEqual(rows["nemotron-3_5-rerank-8b"]["use_class"], "rerank")
        self.assertEqual(rows["llama-3_1-nemotron-safety-guard-8b-v3"]["use_class"], "safety")

    def test_first_seen_kept_last_seen_moves_only_dates_change(self):
        self.assertEqual(self.run_tool()[0], 0)
        first = self.read_snapshot()
        self.assertEqual(self.run_tool(today="2026-11-14")[0], 0)
        second = self.read_snapshot()
        self.assertEqual(second["generated"], "2026-11-14")
        for row in second["rows"]:
            self.assertEqual(row["first_seen"], "2026-11-13")
            self.assertEqual(row["last_seen"], "2026-11-14")
            row["last_seen"] = "2026-11-13"
        second["generated"] = "2026-11-13"
        self.assertEqual(first, second)

    def test_removed_row_dropped_and_printed_added_row_printed(self):
        self.assertEqual(self.run_tool()[0], 0)
        previous = self.read_snapshot()
        previous["rows"].pop(0)
        gone = copy.deepcopy(previous["rows"][0])
        gone.update(resource_id=ORG + "dropped", slug="dropped")
        previous["rows"].append(gone)
        self.snapshot.write_text(json.dumps(previous))
        code, out, err = self.run_tool()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("added: " + ORG + "bnr", out)
        self.assertIn("removed: " + ORG + "dropped", out)
        self.assertNotIn("dropped", self.rows())

    def test_deterministic_bytes_and_stable_keys(self):
        self.assertEqual(self.run_tool()[0], 0)
        first = self.snapshot.read_bytes()
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(first, self.snapshot.read_bytes())
        self.assertTrue(first.endswith(b"\n"))
        self.assertNotIn(b"\r", first)
        self.assertEqual(list(self.read_snapshot()),
                         ["schema", "generated", "source", "counts", "rows"])
        rows = self.read_snapshot()["rows"]
        self.assertTrue(all(set(row) == set(rows[0]) for row in rows))
        self.assertTrue(all(row["v1_ambiguous"] is False for row in rows))

    def test_check_exit_1_missing_snapshot_and_no_write(self):
        code, out, err = self.run_tool("--check", "--json")
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(len(json.loads(out)["added"]), 6)
        self.assertFalse(self.snapshot.exists())

    def test_check_exit_0_ignores_dates_and_metadata_writes_nothing(self):
        self.assertEqual(self.run_tool()[0], 0)
        before = self.snapshot.read_bytes()
        self.fake.payloads["models"]["data"] = []
        code, out, err = self.run_tool("--check", "--json", today="2026-11-14")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["added"], [])
        self.assertEqual(json.loads(out)["removed"], [])
        self.assertEqual(before, self.snapshot.read_bytes())

    def test_check_exit_1_existing_snapshot_writes_nothing(self):
        self.snapshot.write_text(json.dumps({"schema": 1, "rows": [
            {"resource_id": ORG + "gone", "first_seen": "2026-11-01"}]}))
        before = self.snapshot.read_bytes()
        code, out, err = self.run_tool("--check", "--json")
        self.assertEqual((code, err), (1, ""))
        report = json.loads(out)
        self.assertEqual(report["removed"], [ORG + "gone"])
        self.assertEqual(report["counts"], COUNTS)
        self.assertEqual(before, self.snapshot.read_bytes())

    def test_transient_500_retried_successfully(self):
        self.fake.script["models"] = [(500, {})]
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(sum(r[0] == "models" for r in self.fake.requests), 2)

    def test_label_query_also_paginates(self):
        label = self.fake.payloads["label"]
        page = copy.deepcopy(label)
        page["results"] = [page["results"][1]]
        page["results"][0]["resources"] = page["results"][0]["resources"][3:]
        label["results"][1]["resources"] = label["results"][1]["resources"][:3]
        label["resultPageTotal"] = page["resultPageTotal"] = 2
        self.fake.script["label"] = [(200, label), (200, page)]
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual(self.read_snapshot()["counts"], COUNTS)
        self.assertEqual([r[1]["page"] for r in self.fake.requests if r[0] == "label"], [0, 1])

    def test_use_class_table_precedence_vision_image_and_other(self):
        original = self.fake.payloads["label"]["results"][1]["resources"][1]
        for raw, expected in ((["body pose"], "vision"), (["diffusion models"], "image"),
                              (["chat", "content safety"], "safety"),
                              (["chat", "vision language model"], "chat"),
                              (["chat", "usecase_text_to_embedding"], "embedding"),
                              (["chat", "reranking"], "rerank"),
                              (["unmapped label"], "other")):
            with self.subTest(raw=raw):
                resource = copy.deepcopy(original)
                resource["labels"] = [{"key": "general", "unresolvedValues": raw}]
                self.fake.payloads["label"]["results"][1]["resources"][1] = resource
                self.assertEqual(self.run_tool()[0], 0)
                self.assertEqual(self.rows()["glm-5-3-flash"]["use_class"], expected)

    def test_tags_keep_only_raw_values_matching_any_rule_sorted(self):
        resource = self.fake.payloads["label"]["results"][1]["resources"][1]
        resource["labels"] = [
            {"key": "GeNeRaL", "unresolvedValues": ["VLM", "Chat", "chat", "VLM",
                                                   "content safety", "unknown"],
             "values": ["body pose"]},
            {"key": "unrelated", "unresolvedValues": ["reranking"]}]
        self.assertEqual(self.run_tool()[0], 0)
        row = self.rows()["glm-5-3-flash"]
        self.assertEqual(row["tags"], ["Chat", "VLM", "chat", "content safety"])
        self.assertEqual(row["use_class"], "safety")

    def test_returning_resource_gets_new_first_seen(self):
        self.assertEqual(self.run_tool()[0], 0)
        bnr = self.fake.payloads["all-1"]["results"][0]["resources"].pop()
        for name in ("all-0", "all-1"):
            self.fake.payloads[name]["resultTotal"] = 5
        self.assertEqual(self.run_tool(today="2026-11-14")[0], 0)
        self.assertNotIn("bnr", self.rows())
        self.fake.payloads["all-1"]["results"][0]["resources"].append(bnr)
        for name in ("all-0", "all-1"):
            self.fake.payloads[name]["resultTotal"] = 6
        self.assertEqual(self.run_tool(today="2026-11-15")[0], 0)
        self.assertEqual(self.rows()["bnr"]["first_seen"], "2026-11-15")
        self.assertEqual(self.rows()["body-pose"]["first_seen"], "2026-11-13")

    def test_malformed_previous_snapshot_is_error(self):
        for raw in (b"broken", b'{"schema":2,"rows":[]}', b'{"schema":1,"rows":[{}]}'):
            with self.subTest(raw=raw):
                self.snapshot.write_bytes(raw)
                code, out, err = self.run_tool()
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err)
                self.assertEqual(self.snapshot.read_bytes(), raw)

    def test_cli_json_and_env_overrides(self):
        proc = subprocess.run([sys.executable, str(TOOL), "--snapshot", str(self.snapshot),
                               "--json"], env=dict(os.environ, **self.env),
                              capture_output=True, text=True, timeout=10, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["counts"], COUNTS)
        self.assertEqual(report["removed"], [])
        self.assertEqual(len(report["added"]), 6)
        for _, _, headers in self.fake.requests:
            self.assertEqual({h.lower() for h in headers},
                             {"accept", "host", "user-agent", "accept-encoding", "connection"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
