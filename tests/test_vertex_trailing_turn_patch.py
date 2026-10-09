#!/usr/bin/env python3
"""tests/test_vertex_trailing_turn_patch.py — the Vertex trailing-turn guard.

Hermetic (D-852): the patcher runs on fixture *text* written into a temporary
directory, never on the live package, never through the container runtime and
never over the network. The per-shape assertions evaluate the patched snippet
with node, which reads only the fixture file.

The fixture text reproduces the two call-site shapes omniroute 3.8.50 compiles
into its 6 Gemini chunks (verified byte-for-byte against the installed package's
chunk text, read-only):

  * an expression site inside a `return a,b,c` sequence, whose object literal
    closes as `...,f}` and whose register call closes as `},null)`;
  * a statement site `x.contents = merge(x.contents ?? []);let _=t.tools`.

Both shapes are carried per chunk, with the variable families f/o and m/s.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH_TOOL = ROOT / "tools" / "apply-vertex-patch.py"
REAPPLY_PS1 = ROOT / "tools" / "vertex-trailing-turn-reapply.ps1"

# The 6 chunk names the patcher table claims, split by variable family.
FO_CHUNKS = ["_08_y1bx._.js", "_18ct13i._.js", "_1j_edf1._.js", "_1luyz1c._.js"]
MS_CHUNKS = ["_15ose6x._.js", "_1xkpq2s._.js"]
ALL_CHUNKS = FO_CHUNKS + MS_CHUNKS
SITE_COUNT = len(ALL_CHUNKS) * 2

# The guard omniroute 3.8.50-autoos3 shipped (v1): one conditional pop that
# refuses to touch a single-turn contents array. Frozen here as literal text so
# the upgrade path is proved against the shipped bytes, not against whatever the
# patcher's own table says.
V1_EXPR = ('{v}.contents.length>1&&"model"==={v}.contents[{v}.contents.length-1].role'
           '&&{v}.contents.pop()')
V1_STMT = ('if({v}.contents.length>1&&"model"==={v}.contents[{v}.contents.length-1].role)'
           '{v}.contents.pop();')

# The guard omniroute 3.8.50-autoos4 shipped (v2): pop every trailing model turn,
# refill an emptied contents with one synthetic "Continue." user turn. Frozen here
# as literal shipped bytes too, so the v2 -> v3 upgrade is proved against what is
# really on a patched machine, not against the patcher's own table.
V2_BODY = ('for(;{v}.contents.length&&"model"==={v}.contents[{v}.contents.length-1].role;)'
           '{v}.contents.pop();'
           '{v}.contents.length||{v}.contents.push({role:"user",parts:[{text:"Continue."}]})')
V2_EXPR = '(()=>{' + V2_BODY + '})()'
V2_STMT = V2_BODY + ';'


def _family(expr_var, stmt_var, decl):
    """The two merge call sites of one chunk, in the two shipped states."""
    e_head = "mergeConsecutiveSameRoleContents)(%s.contents)," % expr_var
    e_tail = "%s},null)" % expr_var
    s_head = "mergeConsecutiveSameRoleContents)(%s.contents??[]);" % stmt_var
    s_tail = decl
    pristine = (
        "// hermetic Vertex trailing-turn fixture - shape of omniroute 3.8.50 chunk text\n"
        "const u={mergeConsecutiveSameRoleContents:(c)=>(Array.isArray(c)?c:[]),"
        "buildChangedToolNameMap:()=>null,buildGeminiTools:(t)=>({tools:t})};\n"
        "const handlers={};\n"
        "function register(name,fn,fallback){handlers[name]=fn}\n"
        'register("%s-to-gemini",function(e,t,n,r=null){'
        'let %s={contents:(n&&n.contents)||[],systemInstruction:n&&n.systemInstruction};'
        'let E=(0,u.buildChangedToolNameMap)(t);'
        'return E&&(%s._toolNameMap=E),%s.contents=(0,u.%s%s;\n'
        'register("%s-to-gemini",function(e,t,n,r=null){'
        'let %s={contents:(n&&n.contents)||[],systemInstruction:n&&n.systemInstruction};'
        '%s.contents=(0,u.%s%s,I=(0,u.buildGeminiTools)(%s);'
        '%s._tools=I;return %s},null);\n'
        "module.exports={handlers};\n"
    ) % (expr_var, expr_var, expr_var, expr_var, e_head, e_tail,
         stmt_var, stmt_var, stmt_var, s_head, s_tail, decl.split()[1][0], stmt_var, stmt_var)
    v1 = (pristine
          .replace(e_head + e_tail, e_head + V1_EXPR.format(v=expr_var) + "," + e_tail)
          .replace(s_head + s_tail, s_head + V1_STMT.format(v=stmt_var) + s_tail))
    v2 = (pristine
          .replace(e_head + e_tail, e_head + V2_EXPR.replace("{v}", expr_var) + "," + e_tail)
          .replace(s_head + s_tail, s_head + V2_STMT.replace("{v}", stmt_var) + s_tail))
    return {
        "expr_var": expr_var,
        "stmt_var": stmt_var,
        "text": {"pristine": pristine, "v1": v1, "v2": v2},
    }


FAMILIES = {"fo": _family("f", "o", "let _=t.tools"), "ms": _family("m", "s", "let A=t.tools")}


def chunk_text(chunk, state):
    fam = FAMILIES["fo"] if chunk in FO_CHUNKS else FAMILIES["ms"]
    return fam["text"][state]


def write_tree(tmp: Path, state):
    """A fake compiled-chunks directory with all 6 chunks in one state."""
    for chunk in ALL_CHUNKS:
        (tmp / chunk).write_text(chunk_text(chunk, state), encoding="utf-8")
    return tmp


def run_patcher(chunks_dir: Path):
    proc = subprocess.run([sys.executable, str(PATCH_TOOL), str(chunks_dir)],
                          capture_output=True, text=True)
    return proc


def summary(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("Done: ")]
    return lines[-1] if lines else ""


def shapes_report(chunks_dir: Path):
    """Evaluate the patched fixture text in node and return every shape result."""
    driver = chunks_dir / "shapes_driver.cjs"
    driver.write_text(r"""
const dir = __dirname;
const out = {};
const shapes = {
  lone_model:      { contents: [{ role: "model", parts: [{ text: "prefill" }] }] },
  model_tail:      { contents: [{ role: "user", parts: [{ text: "hi" }] },
                                { role: "model", parts: [{ text: "a" }] }] },
  model_tail_two:  { contents: [{ role: "user", parts: [{ text: "hi" }] },
                                { role: "model", parts: [{ text: "a" }] },
                                { role: "model", parts: [{ text: "b" }] }] },
  all_model:       { contents: [{ role: "model", parts: [{ text: "a" }] },
                                { role: "model", parts: [{ text: "b" }] }] },
  ends_user:       { contents: [{ role: "user", parts: [{ text: "one" }] },
                                { role: "model", parts: [{ text: "a" }] },
                                { role: "user", parts: [{ text: "two" }] }] },
  empty:           { contents: [] },
  lone_model_sys:  { contents: [{ role: "model", parts: [{ text: "prefill" }] }],
                     systemInstruction: { parts: [{ text: "be terse" }] } },
  d908_single:     { contents: [
      { role: "user", parts: [{ text: "do it" }] },
      { role: "model", parts: [{ thoughtSignature: "sig", functionCall: { name: "x", args: {} } }] },
      { role: "user", parts: [{ functionResponse: { name: "x", response: {} } },
                              { text: "the notice" }] }] },
  d908_parallel:   { contents: [
      { role: "user", parts: [{ text: "do both" }] },
      { role: "model", parts: [{ functionCall: { name: "a" } }] },
      { role: "user", parts: [{ functionResponse: { name: "a" } },
                              { functionResponse: { name: "b" } },
                              { text: "notice" }] }] },
  fr_only:         { contents: [
      { role: "user", parts: [{ text: "go" }] },
      { role: "model", parts: [{ functionCall: { name: "x" } }] },
      { role: "user", parts: [{ functionResponse: { name: "x" } }] }] },
  text_only:       { contents: [
      { role: "user", parts: [{ functionResponse: { name: "x" } }] },
      { role: "model", parts: [{ text: "ok" }] },
      { role: "user", parts: [{ text: "just text" }] }] },
  mixed_mid:       { contents: [
      { role: "user", parts: [{ functionResponse: { name: "x" } }, { text: "mid" }] },
      { role: "user", parts: [{ text: "later" }] },
      { role: "model", parts: [{ text: "trailing" }] }] },
  mixed_final_model: { contents: [
      { role: "user", parts: [{ functionResponse: { name: "x" } }, { text: "mid" }] },
      { role: "model", parts: [{ text: "trailing" }] }] },
  d908_sys:        { contents: [
      { role: "user", parts: [{ text: "hi" }] },
      { role: "model", parts: [{ functionCall: { name: "x" } }] },
      { role: "user", parts: [{ functionResponse: { name: "x" } }, { text: "notice" }] }],
      systemInstruction: { parts: [{ text: "be terse" }] } },
};
for (const chunk of process.argv.slice(2)) {
  const { handlers } = require(`${dir}/${chunk}`);
  for (const [name, handler] of Object.entries(handlers)) {
    const key = `${chunk}:${name}`;
    out[key] = {};
    for (const [shape, input] of Object.entries(shapes)) {
      let result;
      try {
        result = handler(null, { tools: [] }, JSON.parse(JSON.stringify(input)));
      } catch (err) {
        out[key][shape] = { threw: String(err && err.message || err) };
        continue;
      }
      out[key][shape] = {
        roles: (result.contents || []).map((c) => c.role),
        contents: result.contents,
        systemInstruction: result.systemInstruction || null,
      };
    }
    const again = handler(null, { tools: [] },
                         { contents: JSON.parse(JSON.stringify(out[key].d908_single.contents)) });
    out[key].d908_single_twice = {
      roles: (again.contents || []).map((c) => c.role),
      contents: again.contents,
    };
  }
}
process.stdout.write(JSON.stringify(out));
""", encoding="utf-8")
    proc = subprocess.run(["node", str(driver)] + ALL_CHUNKS,
                          capture_output=True, text=True, cwd=str(chunks_dir))
    if proc.returncode != 0:
        raise RuntimeError(f"node shape harness failed: {proc.stderr[:2000]}")
    return json.loads(proc.stdout)


CONTINUE_TURN = {"role": "user", "parts": [{"text": "Continue."}]}
TS_FIND = "result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);"
# The .ts block the previous reapply script wrote, byte for byte (em dash and all),
# so the upgrade path is proved against what is really on a patched machine.
TS_V1_BLOCK = TS_FIND + "\n\n" + "\n".join([
    "  // Strip trailing model turn — Vertex AI rejects requests ending with a model",
    '  // turn ("Requests ending with a model turn are not supported." 400). The',
    "  // existing stripTrailingAssistantForProvider in contextManager.ts only handles",
    "  // plain-text trailing assistant messages (no tool_calls), and even that runs on",
    "  // tb.messages before Gemini translation. The Antigravity executor handles this",
    "  // correctly via stripTrailingAntigravityAssistantTurn; this mirrors that for the",
    "  // standard Vertex/Gemini executor path. Guard: never strip contents down to empty.",
    "  if (result.contents.length > 1) {",
    "    const lastContent = result.contents[result.contents.length - 1];",
    '    if (lastContent.role === "model") {',
    "      result.contents.pop();",
    "    }",
    "  }",
])
# The .ts block autoos4 shipped (v2), byte for byte, as the v2 -> v3 upgrade
# anchor. Frozen literal, never derived from the patcher's table.
TS_V2_BLOCK = TS_FIND + "\n\n" + "\n".join([
    "  // Pop every trailing model turn — Vertex AI rejects a request whose contents",
    '  // end with a model turn ("Requests ending with a model turn are not supported."',
    "  // 400). The v1 guard skipped a contents of ONE model turn, which is the body that",
    "  // reproduces the 400, and popping can leave the array empty, which Vertex rejects",
    "  // too: an emptied contents gets one synthetic user turn instead of a refused call,",
    "  // because refusing is the failure this patch exists to remove. The popped model",
    "  // text is dropped, not re-appended — the model continues from systemInstruction",
    '  // and the history that is left. A body already ending on "user" is never touched.',
    "  while (result.contents.length > 0) {",
    "    const lastContent = result.contents[result.contents.length - 1];",
    '    if (lastContent.role !== "model") {',
    "      break;",
    "    }",
    "    result.contents.pop();",
    "  }",
    "  if (result.contents.length === 0) {",
    '    result.contents.push({ role: "user", parts: [{ text: "Continue." }] });',
    "  }",
])
# The vendor's own openai-to-gemini.ts carries a `result.contents.length > 1` that
# has nothing to do with this patch (sanity run over a real copy of the file, lane
# VERTEX-GUARD 2026-10-09: openai-to-gemini.ts:2160). Carrying such a block in the
# fixture means an assertion like "no length > 1 left in the file" cannot pass by
# accident: only naming OUR block proves the upgrade happened.
TS_VENDOR_TAIL = (
    "\n\n  // Convert tools\n"
    "  if (result.contents.length > 1) {\n"
    "    const first = result.contents[0];\n"
    "    if (first.role === \"user\" && first.parts.length === 0) {\n"
    "      result.contents.shift();\n"
    "    }\n"
    "  }\n"
)
NODE_AVAILABLE = shutil.which("node") is not None
PWSH_AVAILABLE = shutil.which("pwsh") is not None


class TreeStates(unittest.TestCase):
    """The patcher's three-way decision on real fixture text."""

    def test_pristine_tree_patches_every_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            proc = run_patcher(Path(tmp))
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual(f"Done: {SITE_COUNT} patched, 0 skipped, 0 errors", summary(proc))

    def test_second_run_skips_every_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            proc = run_patcher(Path(tmp))
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual(f"Done: 0 patched, {SITE_COUNT} skipped, 0 errors", summary(proc))

    def test_v1_guard_tree_upgrades_every_site(self):
        """The shipped autoos3 guard must be replaced, not silently skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v1")
            proc = run_patcher(Path(tmp))
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual(f"Done: {SITE_COUNT} patched, 0 skipped, 0 errors", summary(proc))
            self.assertNotIn("SKIP", proc.stdout)

    def test_v2_guard_tree_upgrades_every_site(self):
        """The shipped autoos4 guard must be replaced too (v2 -> v3, D-908)."""
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v2")
            proc = run_patcher(Path(tmp))
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual(f"Done: {SITE_COUNT} patched, 0 skipped, 0 errors", summary(proc))
            self.assertNotIn("SKIP", proc.stdout)

    def test_v2_upgrade_installs_the_v3_split(self):
        """The upgrade replaces the v2 bytes with v3, not just re-anchors them."""
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v2")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            for chunk in ALL_CHUNKS:
                text = (Path(tmp) / chunk).read_text(encoding="utf-8")
                self.assertIn('text:"Noted."', text, chunk)
                self.assertIn('text:"Continue."', text, chunk)

    def test_a_v3_tree_is_skipped(self):
        """v3 present -> SKIP: the patcher's own output is its skip anchor."""
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            proc = run_patcher(Path(tmp))
            self.assertEqual(f"Done: 0 patched, {SITE_COUNT} skipped, 0 errors", summary(proc))

    def test_upgraded_tree_skips_on_the_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v1")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            proc = run_patcher(Path(tmp))
            self.assertEqual(f"Done: 0 patched, {SITE_COUNT} skipped, 0 errors", summary(proc))

    def test_no_site_keeps_the_v1_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v1")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            for chunk in ALL_CHUNKS:
                text = (Path(tmp) / chunk).read_text(encoding="utf-8")
                # The same string the omniroute.Dockerfile build gate asserts absent.
                self.assertNotIn('contents.length>1&&"model"===', text, chunk)
                self.assertIn('text:"Continue."', text, chunk)

    def test_every_report_line_is_ascii(self):
        """The patcher's stdout has to survive a Windows console code page.

        A non-ASCII print can raise UnicodeEncodeError halfway through a run, and
        the build stage then reports a patch failure it cannot name.
        """
        outs = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for state in ("pristine", "v1", "v2"):
                tree = root / state
                tree.mkdir()
                write_tree(tree, state)
                outs.append(run_patcher(tree).stdout)   # PATCH branch
                outs.append(run_patcher(tree).stdout)   # SKIP branch
            broken = root / "broken"
            broken.mkdir()
            write_tree(broken, "pristine")
            (broken / FO_CHUNKS[0]).write_text("const nothing = 1;\n", encoding="utf-8")
            (broken / MS_CHUNKS[0]).unlink()            # file-not-found branch
            outs.append(run_patcher(broken).stdout)     # ERROR branches
        for line in "\n".join(outs).splitlines():
            try:
                line.encode("ascii")
            except UnicodeEncodeError:
                self.fail(f"non-ASCII report line: {line!r}")

    def test_unknown_site_text_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            broken = Path(tmp) / MS_CHUNKS[0]
            broken.write_text("const nothing = 1;\n", encoding="utf-8")
            proc = run_patcher(Path(tmp))
            self.assertNotEqual(0, proc.returncode)
            self.assertIn(f"ERROR {MS_CHUNKS[0]}", proc.stdout)
            self.assertIn("2 errors", summary(proc), "both sites of that chunk refused")

    def test_missing_chunk_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            (Path(tmp) / FO_CHUNKS[0]).unlink()
            proc = run_patcher(Path(tmp))
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("file not found", proc.stdout)

    def test_backup_holds_the_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "v1")
            self.assertEqual(0, run_patcher(Path(tmp)).returncode)
            backups = list(Path(tmp).glob(f"{FO_CHUNKS[0]}.autoos-backup-*"))
            self.assertEqual(1, len(backups), "one backup per chunk per run")
            self.assertIn("contents.length>1", backups[0].read_text(encoding="utf-8"),
                          "the backup is the pre-upgrade text")

    def test_doubled_anchor_is_an_error_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), "pristine")
            target = Path(tmp) / FO_CHUNKS[0]
            text = target.read_text(encoding="utf-8")
            target.write_text(text + text.replace("module.exports", ""), encoding="utf-8")
            proc = run_patcher(Path(tmp))
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("ERROR", proc.stdout)


@unittest.skipUnless(NODE_AVAILABLE, "node not on PATH")
class Shapes(unittest.TestCase):
    """What the patched snippet actually does to a request body."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        write_tree(Path(self._tmp.name), "pristine")
        self.assertEqual(0, run_patcher(Path(self._tmp.name)).returncode)
        self.report = shapes_report(Path(self._tmp.name))
        self.assertEqual(len(ALL_CHUNKS) * 2, len(self.report),
                         "every chunk exposes both call sites")

    def tearDown(self):
        self._tmp.cleanup()

    def test_lone_model_turn_becomes_a_continue_user_turn(self):
        for key, shapes in self.report.items():
            got = shapes["lone_model"]
            self.assertEqual(["user"], got["roles"], key)
            self.assertEqual([CONTINUE_TURN], got["contents"], key)

    def test_lone_model_turn_drops_the_prefill_text(self):
        for key, shapes in self.report.items():
            self.assertNotIn("prefill", json.dumps(shapes["lone_model"]["contents"]), key)

    def test_history_ending_on_model_keeps_the_user_history(self):
        for key, shapes in self.report.items():
            got = shapes["model_tail"]
            self.assertEqual(["user"], got["roles"], key)
            self.assertEqual("hi", got["contents"][0]["parts"][0]["text"], key)

    def test_multiple_trailing_model_turns_are_all_popped(self):
        for key, shapes in self.report.items():
            got = shapes["model_tail_two"]
            self.assertEqual(["user"], got["roles"], key)

    def test_all_model_history_falls_back_to_the_continue_turn(self):
        for key, shapes in self.report.items():
            self.assertEqual([CONTINUE_TURN], shapes["all_model"]["contents"], key)

    def test_body_ending_on_user_is_untouched(self):
        for key, shapes in self.report.items():
            got = shapes["ends_user"]
            self.assertEqual(["user", "model", "user"], got["roles"], key)
            self.assertEqual([{"text": "one"}, {"text": "a"}, {"text": "two"}],
                             [{"text": p["text"]} for c in got["contents"] for p in c["parts"]], key)

    def test_empty_contents_gets_the_continue_turn(self):
        for key, shapes in self.report.items():
            self.assertEqual([CONTINUE_TURN], shapes["empty"]["contents"], key)

    def test_system_instruction_survives_the_strip(self):
        for key, shapes in self.report.items():
            got = shapes["lone_model_sys"]
            self.assertEqual([CONTINUE_TURN], got["contents"], key)
            self.assertEqual({"parts": [{"text": "be terse"}]}, got["systemInstruction"], key)

    def _raw_report(self, state):
        """Evaluate a fixture tree in a shipped state WITHOUT running the patcher."""
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(Path(tmp), state)
            return shapes_report(Path(tmp))

    # --- D-908: the shape that ends on a user turn yet still 400s ------------------

    def test_v2_guard_leaves_a_mixed_user_turn_untouched(self):
        """The bug v3 exists to fix, pinned against the shipped v2 bytes.

        Vertex 400s a body whose LAST turn is a user turn that mixes a
        functionResponse with text (D-908); v2 never looks inside a turn, so the
        mixed turn survives byte for byte.
        """
        report = self._raw_report("v2")
        for key, shapes in report.items():
            got = shapes["d908_single"]
            self.assertEqual(["user", "model", "user"], got["roles"], key)
            last = got["contents"][-1]
            self.assertTrue(any("functionResponse" in p for p in last["parts"]), key)
            self.assertTrue(any("text" in p for p in last["parts"]), key)
            self.assertNotIn("Noted.", json.dumps(got["contents"]), key)

    def test_v3_splits_a_mixed_final_user_turn(self):
        for key, shapes in self.report.items():
            got = shapes["d908_single"]
            self.assertEqual(["user", "model", "user", "model", "user"], got["roles"], key)
            self.assertEqual([{"text": "do it"}], got["contents"][0]["parts"], key)
            self.assertEqual([{"thoughtSignature": "sig", "functionCall": {"name": "x", "args": {}}}],
                             got["contents"][1]["parts"], key)
            self.assertEqual([{"functionResponse": {"name": "x", "response": {}}}],
                             got["contents"][2]["parts"], key)
            self.assertEqual([{"text": "Noted."}], got["contents"][3]["parts"], key)
            self.assertEqual([{"text": "the notice"}], got["contents"][4]["parts"], key)

    def test_v3_splits_a_parallel_mixed_user_turn(self):
        for key, shapes in self.report.items():
            got = shapes["d908_parallel"]
            self.assertEqual(["user", "model", "user", "model", "user"], got["roles"], key)
            self.assertEqual([{"functionResponse": {"name": "a"}}, {"functionResponse": {"name": "b"}}],
                             got["contents"][2]["parts"], key)
            self.assertEqual([{"text": "Noted."}], got["contents"][3]["parts"], key)
            self.assertEqual([{"text": "notice"}], got["contents"][4]["parts"], key)

    def test_function_response_only_turn_is_untouched(self):
        for key, shapes in self.report.items():
            got = shapes["fr_only"]
            self.assertEqual(["user", "model", "user"], got["roles"], key)
            self.assertEqual([{"functionResponse": {"name": "x"}}], got["contents"][2]["parts"], key)
            self.assertNotIn("Noted.", json.dumps(got["contents"]), key)

    def test_text_only_turn_is_untouched(self):
        for key, shapes in self.report.items():
            got = shapes["text_only"]
            self.assertEqual(["user", "model", "user"], got["roles"], key)
            self.assertEqual([{"text": "just text"}], got["contents"][2]["parts"], key)
            self.assertNotIn("Noted.", json.dumps(got["contents"]), key)

    def test_v3_splits_a_mixed_mid_history_turn(self):
        for key, shapes in self.report.items():
            got = shapes["mixed_mid"]
            self.assertEqual(["user", "model", "user", "user"], got["roles"], key)
            self.assertEqual([{"text": "Noted."}], got["contents"][1]["parts"], key)
            self.assertEqual("user", got["roles"][-1], key)

    def test_a_trailing_model_behind_a_mixed_turn_is_still_popped(self):
        for key, shapes in self.report.items():
            got = shapes["mixed_final_model"]
            self.assertEqual(["user", "model", "user"], got["roles"], key)
            self.assertEqual("user", got["roles"][-1], key)
            self.assertIn("Noted.", json.dumps(got["contents"]), key)

    def test_the_v3_split_is_idempotent(self):
        for key, shapes in self.report.items():
            once = shapes["d908_single"]
            twice = shapes["d908_single_twice"]
            self.assertEqual(once["roles"], twice["roles"], key)
            self.assertEqual(once["contents"], twice["contents"], key)

    def test_system_instruction_survives_the_v3_split(self):
        for key, shapes in self.report.items():
            got = shapes["d908_sys"]
            self.assertEqual({"parts": [{"text": "be terse"}]}, got["systemInstruction"], key)
            self.assertEqual("user", got["roles"][-1], key)

    def test_no_shape_throws(self):
        for key, shapes in self.report.items():
            for shape, got in shapes.items():
                self.assertNotIn("threw", got, f"{key}/{shape}")

    def test_patched_chunks_parse_as_javascript(self):
        for chunk in ALL_CHUNKS:
            proc = subprocess.run(["node", "--check", str(Path(self._tmp.name) / chunk)],
                                  capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, f"{chunk}: {proc.stderr[:500]}")


class ScriptAgreement(unittest.TestCase):
    """One guard, two scripts: the .ps1 carries the chunk table byte for byte."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        self.patcher = __import__("apply-vertex-patch")

    def test_the_patcher_table_names_the_six_chunks(self):
        self.assertEqual(sorted(ALL_CHUNKS), sorted({s["chunk"] for s in self.patcher.SITES}))

    def test_the_reapply_script_carries_every_anchor_and_replacement(self):
        ps1 = REAPPLY_PS1.read_text(encoding="utf-8-sig")
        for site in self.patcher.SITES:
            self.assertIn(site["chunk"], ps1, site["chunk"])
            self.assertIn(site["new"], ps1, f"{site['chunk']} replacement")
            for form, _, _ in site["forms"]:
                self.assertIn(form, ps1, f"{site['chunk']} form")

    def test_the_reapply_script_carries_the_v3_ts_block(self):
        """The .ts site is v3 too; v2 and v1 live on only as upgrade anchors."""
        ps1 = REAPPLY_PS1.read_text(encoding="utf-8-sig")
        self.assertIn("while (result.contents.length > 0) {", ps1)
        self.assertIn('text: "Continue."', ps1)
        self.assertIn('text: "Noted."', ps1)
        # Once, in the template the v1 -> v3 upgrade matches against.
        self.assertEqual(1, ps1.count("Guard: never strip contents down to empty."))

    def test_the_v1_guard_is_only_ever_an_upgrade_anchor(self):
        """Nothing in the pristine compiled text carries the v1 guard.

        The build gate asserts the v1 bytes are *absent* after patching, which is
        only a sound contract if a pristine chunk never contains them — otherwise
        the build would fail on vendor text this patch has nothing to do with.
        """
        for chunk in ALL_CHUNKS:
            self.assertNotIn('contents.length>1&&"model"===', chunk_text(chunk, "pristine"),
                             chunk)


@unittest.skipUnless(PWSH_AVAILABLE, "pwsh not on PATH")
class ReapplyScript(unittest.TestCase):
    """tools/vertex-trailing-turn-reapply.ps1 over a fake omniroute package tree."""

    SITES = SITE_COUNT + 1  # the 12 chunk sites plus the .ts source site

    def _package(self, tmp: Path, state):
        pkg = tmp / "omniroute"
        chunks = pkg / "dist" / ".build" / "next" / "server" / "chunks"
        chunks.mkdir(parents=True)
        write_tree(chunks, state)
        (pkg / "package.json").write_text('{"name":"omniroute","version":"3.8.50"}',
                                          encoding="utf-8")
        ts_dir = pkg / "open-sse" / "translator" / "request"
        ts_dir.mkdir(parents=True)
        head = {"v1": TS_V1_BLOCK, "v2": TS_V2_BLOCK}.get(state, TS_FIND)
        (ts_dir / "openai-to-gemini.ts").write_text(head + TS_VENDOR_TAIL, encoding="utf-8")
        return pkg

    def _run(self, pkg: Path):
        return subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File",
                               str(REAPPLY_PS1), "-Path", str(pkg), "-NoBackup"],
                              capture_output=True, text=True)

    def test_pristine_package_patches_all_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = self._package(Path(tmp), "pristine")
            proc = self._run(pkg)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn(f"Done: {self.SITES} patched, 0 skipped, 0 errors", proc.stdout)

    def test_pristine_package_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = self._package(Path(tmp), "pristine")
            self.assertEqual(0, self._run(pkg).returncode)
            proc = self._run(pkg)
            self.assertIn(f"Done: 0 patched, {self.SITES} skipped, 0 errors", proc.stdout)

    def test_v1_package_upgrades_all_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = self._package(Path(tmp), "v1")
            proc = self._run(pkg)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn(f"Done: {self.SITES} patched, 0 skipped, 0 errors", proc.stdout)
            ts_text = (pkg / "open-sse" / "translator" / "request" /
                       "openai-to-gemini.ts").read_text(encoding="utf-8")
            # Asserted by name, never by "no length > 1 anywhere": the vendor block
            # that comes with the file keeps its own `result.contents.length > 1`.
            self.assertNotIn("Guard: never strip contents down to empty.", ts_text)
            self.assertNotIn(TS_V1_BLOCK, ts_text)
            self.assertIn('text: "Continue."', ts_text)
            self.assertEqual(1, ts_text.count("Continue."), "one block, not two")
            self.assertIn("result.contents.shift();", ts_text, "vendor text untouched")
            for chunk in ALL_CHUNKS:
                self.assertIn('text:"Continue."',
                              (pkg / "dist" / ".build" / "next" / "server" / "chunks" /
                               chunk).read_text(encoding="utf-8"))

    def test_v2_package_upgrades_all_sites(self):
        """The shipped autoos4 package upgrades to v3, split and all."""
        with tempfile.TemporaryDirectory() as tmp:
            pkg = self._package(Path(tmp), "v2")
            proc = self._run(pkg)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn(f"Done: {self.SITES} patched, 0 skipped, 0 errors", proc.stdout)
            ts_text = (pkg / "open-sse" / "translator" / "request" /
                       "openai-to-gemini.ts").read_text(encoding="utf-8")
            self.assertNotIn(TS_V2_BLOCK, ts_text, "the v2 block is gone")
            self.assertIn('text: "Noted."', ts_text)
            self.assertIn('text: "Continue."', ts_text)
            self.assertEqual(1, ts_text.count("Continue."), "one block, not two")
            for chunk in ALL_CHUNKS:
                text = (pkg / "dist" / ".build" / "next" / "server" / "chunks" /
                        chunk).read_text(encoding="utf-8")
                self.assertIn('text:"Noted."', text, chunk)
                self.assertIn('text:"Continue."', text, chunk)

    def test_unknown_package_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = self._package(Path(tmp), "pristine")
            (pkg / "dist" / ".build" / "next" / "server" / "chunks" / FO_CHUNKS[0]
             ).write_text("const nothing = 1;\n", encoding="utf-8")
            proc = self._run(pkg)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("ERROR", proc.stdout)

    def test_the_script_encoding_contract(self):
        # Windows PowerShell 5.1 decodes a BOM-less file as the active code page, so
        # .gitattributes makes the BOM content for *.ps1; and a pure-ASCII body is
        # read identically under 5.1 and 7 on any code page.
        raw = REAPPLY_PS1.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "missing the UTF-8 BOM")
        raw[3:].decode("ascii")


class Hermetic(unittest.TestCase):
    """D-852: this lane never reaches the container runtime or the network."""

    FILES = [PATCH_TOOL, REAPPLY_PS1, Path(__file__),
             ROOT / "configuration" / "omniroute" / "vertex-trailing-turn-README.md",
             ROOT / "configuration" / "docker" / "ai-stack" / "omniroute.Dockerfile"]

    def test_only_the_dockerfile_build_stage_mentions_the_runtime(self):
        for path in self.FILES:
            if path.name == "omniroute.Dockerfile":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(?:.*\s)?docker\s+\w", text, re.MULTILINE),
                              f"{path.name} runs a container command")

    def test_the_patcher_never_opens_a_socket(self):
        text = PATCH_TOOL.read_text(encoding="utf-8")
        for banned in ("urllib", "requests", "socket", "subprocess"):
            self.assertNotIn(banned, text, f"{banned} in the patcher")


if __name__ == "__main__":
    unittest.main()
