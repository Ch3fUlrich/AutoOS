"""Compaction must not substitute for a fleet handoff."""
import json

from test_context_guard import GuardCase, usage_line


BOUNDARY = json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": "2026-11-07T00:00:00Z",
                       "compactMetadata": {"trigger": "auto", "preTokens": 280000}})


class Compaction(GuardCase):
    def fleet(self, *lines):
        self.write(json.dumps({"type": "agent-name", "agentName": "builder-0042"}),
                   *lines)

    def test_compaction_below_threshold_without_environment(self):
        self.fleet(BOUNDARY, usage_line(inp=1000))
        text = self.ctx_text(self.hook(FLEET_SESSION_NAME=""))
        self.assertIn("HANDOFF-NOW", text)
        self.assertIn("compaction", text)
        self.assertIn("ready <handoff-path>", text)
        self.assertEqual(json.loads(self.state_file().read_text())["level"], "handoff")

    def test_interactive_compaction_is_silent(self):
        self.write(json.dumps({"type": "custom-title", "customTitle": "interactive"}),
                   BOUNDARY, usage_line(inp=1000))
        self.assertEqual(self.hook().stdout, "")

    def test_low_context_without_boundary_clears_state(self):
        self.fleet(usage_line(inp=280000))
        self.hook()
        with self.transcript.open("a") as fh:
            fh.write(usage_line(inp=1000) + "\n")
        self.assertEqual(self.hook().stdout, "")
        self.assertFalse(self.state_file().exists())

    def test_pending_survives_and_reemits_by_usual_rule(self):
        self.fleet(BOUNDARY, usage_line(inp=2000))
        self.assertIn("HANDOFF-NOW", self.ctx_text(self.hook()))
        self.assertEqual(self.hook().stdout, "")
        self.assertTrue(self.state_file().exists())
        with self.transcript.open("a") as fh:
            fh.write(usage_line(inp=1000) + "\n")
        self.assertIn("compaction", self.ctx_text(self.hook()))

    def test_long_transcript_cursor_does_not_rescan_prefix(self):
        self.fleet(*(['{}'] * 150000), usage_line(inp=1000))
        self.hook()
        size = self.transcript.stat().st_size
        # Replace an already scanned record in place: a prefix rescan would
        # incorrectly discover this new boundary.
        with self.transcript.open("r+b") as fh:
            fh.seek(100)
            fh.write(("\n" + BOUNDARY + "\n").encode())
        with self.transcript.open("a") as fh:
            fh.write(usage_line(inp=1000) + "\n")
        self.assertEqual(self.hook().stdout, "")
        path = self.state_file().parent.with_name("ctx-guard-scan") / self.state_file().with_suffix(".scan").name
        scan = json.loads(path.read_text())
        self.assertGreaterEqual(scan["offset"], size)
