"""tests/ci-shards.py: every Linux test part is in exactly one CI shard (WS-CIPAR)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ci_shards", ROOT / "tests" / "ci-shards.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class ShardMapTests(unittest.TestCase):
    def test_the_repository_map_covers_every_part_exactly_once(self):
        self.assertEqual(cs.problems(cs.read_shards(), cs.parts_on_disk()), [])

    def test_the_matrix_lists_every_shard_with_its_parts(self):
        shards = cs.read_shards()
        matrix = {"include": [{"shard": n, "parts": ",".join(p)} for n, p in shards]}
        parts = [p for row in matrix["include"] for p in row["parts"].split(",")]
        self.assertEqual(sorted(parts), sorted(cs.parts_on_disk()))
        json.dumps(matrix)

    def test_only_trims_the_matrix_to_the_selected_parts(self):
        shards = [("a", ["13"]), ("b", ["33", "17"]), ("c", ["01"])]
        m = cs.matrix_for(shards, "tests/linux/33-documentation.sh 13")
        self.assertEqual(m, {"include": [{"shard": "a", "parts": "13"}, {"shard": "b", "parts": "33"}]})
        self.assertEqual(cs.matrix_for(shards)["include"][1]["parts"], "33,17")
        with self.assertRaises(ValueError):
            cs.matrix_for(shards, "tools/x.py")

    def test_only_naming_an_unknown_part_runs_everything(self):
        shards = [("a", ["13"]), ("b", ["33"])]
        self.assertEqual(cs.matrix_for(shards, "tests/linux/99-gone.sh"), cs.matrix_for(shards))
        self.assertEqual(len(cs.matrix_for(shards, "13 99")["include"]), 2)

    def test_a_missing_part_is_named(self):
        found = cs.problems([("a", ["01"])], {"01", "02"})
        self.assertEqual(found, ["part 02 (tests/linux/02-*.sh) is in no shard"])

    def test_a_part_in_two_shards_is_named(self):
        found = cs.problems([("a", ["01"]), ("b", ["01", "02"])], {"01", "02"})
        self.assertEqual(found, ["part 01 is in shard a and shard b"])

    def test_a_part_that_does_not_exist_is_named(self):
        found = cs.problems([("a", ["01", "99"])], {"01"})
        self.assertEqual(found, ["shard a names part 99, which does not exist"])

    def test_a_duplicate_shard_name_is_named(self):
        found = cs.problems([("a", ["01"]), ("a", ["02"])], {"01", "02"})
        self.assertIn("shard a is defined twice", found)

    def test_a_malformed_line_fails_with_its_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ci-shards.txt"
            p.write_text("# c\na 01, 02\n", encoding="utf-8")
            with self.assertRaises(ValueError) as e:
                cs.read_shards(p)
            self.assertIn(":2:", str(e.exception))


if __name__ == "__main__":
    unittest.main()
