# Vertex/Gemini Trailing Model Turn Strip Fix

## Description

This fix addresses the "Requests ending with a model turn are not supported" 400 error
from Vertex AI when using the OmniRoute gateway with Gemini models. A translated request
whose `contents` array ends on a `role:"model"` turn is refused by Vertex before any
tokens are generated, so an agent that finished its turn and was waiting to be continued
died on the very next call.

## Root cause

The gateway reaches Vertex through `openaiToGeminiBase`, which merges OpenAI-style
messages into Gemini `contents` with `mergeConsecutiveSameRoleContents`. Nothing after
that merge looks at the role of the **last** content.

Two things were believed about the surrounding code and are not true, both measured in a
hermetic replay of the running image (the VERTEX-GUARD lane's replay evidence, kept under
the git-ignored `logs/vbig-replay/`, so read it on the host that ran the replay):

- `stripTrailingAssistantForProvider` in `open-sse/services/contextManager.ts` does not
  help here. It only strips a trailing assistant message that carries **no** `tool_calls`,
  it runs on the OpenAI-shaped messages **before** translation, and the translator does
  not re-check what it produced.
- There is no "provider rejected the request as too long, compact and retry" path in
  3.8.50 to hook a second strip onto: the string `provider rejected the request as too
  long; compacting` appears nowhere in `open-sse/`, `src/`, or any compiled chunk. Size
  driven compaction runs **pre**-translation on the OpenAI messages, so the translator
  strip already re-applies to whatever compaction built.

So the strip belongs at the translation boundary, at every
`mergeConsecutiveSameRoleContents` call site. In OmniRoute 3.8.50 those are 12 sites over
6 compiled chunks — 2 per chunk, an expression site (`...,f.contents=merge(f.contents),f}`)
and a statement site (`o.contents=merge(o.contents??[]);let _=t.tools`) — and one site in
the `open-sse/translator/request/openai-to-gemini.ts` source. That sentence is a measurement
of one release, not a contract on the next: `tools/vertex-patch-gate.py` counts the sites on
disk instead of trusting it, which is what the count in it is a floor for.

## The two guards, and why v1 was wrong

**v1 (lane F1-vertex, 2026-09-30)** popped one trailing model turn, guarded so it could
not empty the array:

```js
if (contents.length > 1 && contents[contents.length - 1].role === "model") contents.pop();
```

`length > 1` is the bug. The body that actually reproduces the 400 is a contents of
**exactly one** model turn — a fresh session whose first assistant turn was a tool call,
which is the shape an unattended lane ends on. `length > 1` declines to touch it
(replay steps 5, 9 and 10: `contents_len 1`, HTTP 400, every time).

**`>= 1` is not the fix either.** It pops the lone turn and sends `contents: []`, which
Vertex rejects as flatly as a model turn (replay step 12).

**v2 (this lane, 2026-10-09)** pops the whole trailing model run and refills an emptied
array:

```js
for (; contents.length && contents[contents.length - 1].role === "model"; ) contents.pop();
contents.length || contents.push({ role: "user", parts: [{ text: "Continue." }] });
```

## The choice, and its trade-off

When the pop empties `contents`, one synthetic user turn — `{role:"user",
parts:[{text:"Continue."}]}` — is appended instead of refusing the request.

- **Why not refuse before dispatch?** Refusing is the failure this patch exists to remove.
  A refused call ends the run: an unattended orchestrator turn dies and the lane has to be
  restarted by hand. A synthetic turn keeps the conversation alive at the cost of one
  invented message.
- **The popped model text is dropped, not re-appended.** The model is asked to continue
  from `systemInstruction` plus whatever history survived, not from its own truncated
  output. Re-appending it would put a model turn back at the end and reproduce the 400.
  The consequence is honest: on a body that was *nothing but* a model turn, that turn's
  text does not reach the provider.
- **A body whose last turn is already `user` is never touched**, and `systemInstruction`
  is not part of `contents`, so it is unaffected either way. Shapes are asserted per case
  in `tests/test_vertex_trailing_turn_patch.py` (`Shapes`: lone model turn, model tail
  behind a user history, several model turns in a row, all-model history, user tail, empty
  contents, `systemInstruction`).

## Tools

| tool | what it patches |
|---|---|
| `tools/apply-vertex-patch.py <chunks-dir>` | the 12 compiled-chunk sites only |
| `tools/vertex-trailing-turn-reapply.ps1` | those 12 **plus** the `.ts` source site (13) |
| `tools/vertex-patch-gate.py <chunks-dir>` | nothing — it counts the sites, guards and refills in **every** chunk on disk and fails a build whose tree is not fully guarded |

Both patchers decide every site in exactly three states and refuse anything else:

```
the v2 guard is present                       -> SKIP   (idempotent)
pristine text, or the v1 guard as shipped     -> PATCH  (install, or upgrade in place)
anything else                                 -> ERROR  (exit 1; nothing is guessed)
```

The v1 form is tried before the pristine form, and the reason is the `.ts` site, not the
chunks. A chunk site is matched on its whole anchor — head *and* tail together — so a chunk
holds exactly one of the two states and the order decides nothing: the v1 guard sits between
the head and the tail, so the two never both match. The `.ts` site is the one where order is
load-bearing: its pristine anchor `result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);`
is the first line of the block v1 wrote, so the pristine find *does* occur inside v1 text,
and matching it first would splice the v2 block in above the `if (result.contents.length > 1)`
it is meant to remove — one site carrying both guards, the broken one still running. The
chunk table is kept in the same order because the two tables are asserted byte-identical, so
there is one rule to remember instead of two. `tests/test_vertex_patch_gate.py` measures both
halves of that claim. A site whose anchor matches more than once is reported as an error
rather than half-patched, because a chunk whose text moved is a chunk this table no longer
describes.

`npm update omniroute` (or a `FROM` bump of the gateway base image) reverts every patch;
re-run the script, or rebuild the image, afterwards. Each modified file is copied to
`<file>.autoos-backup-<timestamp>` first (AGENTS.md rule 5).

## Where the patch actually ships

The runtime image has a read-only rootfs and no python, so the patch runs at **build**
time: `configuration/docker/ai-stack/omniroute.Dockerfile` copies the chunks into a
`python:3.12-slim` stage, runs `apply-vertex-patch.py` twice, and hands the tree to
`tools/vertex-patch-gate.py`, which stops the build unless the v2 guard count equals the
call-site count, the refill count equals it too, at least 12 call sites exist, no v1 guard
text survives anywhere in the tree, and both run summaries account for exactly that many
sites (run 2 patching anything at all is a failed build: the guard must be idempotent).

The gate counts **every** `*.js` chunk on disk, never the 6 names the patcher's table
carries. That was the gate's own defect (lane VERTEX-GUARD, gate 06c19a): the stage used to
grep `Done: 12 patched` and count refills over a hard-coded chunk list, so a `FROM` bump that
added a 13th `mergeConsecutiveSameRoleContents` call site in a new chunk — or renamed one of
the 6 — shipped a gateway unpatched at that site while every number still matched, because
the check had only ever counted the files it named. A gate that reads the tree has nothing to
know in advance: chunk names, chunk count and site count come from the text, and a mismatch
fails the build with the counts and the offending file printed, which is what a human then
adds to `tools/apply-vertex-patch.py`.

The counts stayed **12** for v2 because the anchors did not move — only the guard inserted
at them changed. That is also why the patcher's report alone is never enough: the v1 patcher
reports `12 patched` too, and so does the v2 patcher on a tree with a 13th site it cannot
see. The gate therefore judges the guard's own bytes as well as the arithmetic.

## Impact

Required for every Vertex/Gemini route in OmniRoute. Nothing else is touched: the patch
is inside the OpenAI-to-Gemini translator, so other providers' requests never reach it.

## Compatibility

- OmniRoute v3.8.50 (verified against the npm tarball and against
  `autoos/omniroute:3.8.50-autoos3`, the v1-patched image)
- Node.js 20.x or later
- Windows 10/11 with Windows PowerShell 5.1 or PowerShell 7.x for the `.ps1` (the file is
  UTF-8 **with BOM** and pure ASCII — `.gitattributes` keeps the BOM, and 5.1 decodes a
  BOM-less file as the active code page)

## Notes

On Windows the `.ps1` patches the global OmniRoute package at
`%APPDATA%\npm\node_modules\omniroute`; pass `-Path` for any other install.
The build stage patches the chunks inside the image and the gate judges the same tree.
None of the three reads a credential, opens a network connection, or invokes the container
runtime — `tests/test_vertex_trailing_turn_patch.py` and `tests/test_vertex_patch_gate.py`
run on fixture copies of the chunk and source text only (D-852).
