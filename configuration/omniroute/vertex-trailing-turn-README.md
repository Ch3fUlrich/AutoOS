# Vertex/Gemini Turn-Shape Fix

## Description

This fix addresses two Vertex AI 400s seen through the OmniRoute gateway with Gemini
models:

- **"Requests ending with a model turn are not supported"** when a translated request's
  `contents` array ends on a `role:"model"` turn (or is empty). An agent that finished its
  turn and was waiting to be continued died on the very next call.
- **A signed tool call whose last turn is a `user` turn that MIXES a `functionResponse`
  part with a `text` part** is refused with that same "ends with a model turn" message,
  although the body ends on `user` (D-908). This is the misreport the v2 guard could not
  see: it only ever looked at the role of the last turn, never inside a turn.

## Root cause

The gateway reaches Vertex through `openaiToGeminiBase`, which merges OpenAI-style
messages into Gemini `contents` with `mergeConsecutiveSameRoleContents`. Nothing after
that merge looks at the role of the **last** content, and nothing splits a `user` turn
whose parts have been merged from a tool result plus subsequent user text.

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

**The D-908 shape.** A live capture of a signed tool call shows the upstream `contents`
as `user[text]`, `model[thoughtSignature + functionCall]`, `user[functionResponse,
text(notice)]` — and Vertex answers 400 "Requests ending with a model turn are not
supported". The body ends on `user`, so the v2 trailing-run strip does nothing. Live
probes (all 200 unless noted) pinned the fix:

- inserting a synthetic assistant text turn between the last tool result and the user text
  -> 200 for a single tool call **and** for a parallel one (two `functionResponse` parts);
- folding the text into the last tool result -> 200 single but **400 parallel**.

The decision was therefore a **split shape**: a mixed user turn is broken apart, not
merged or folded.

So the fix belongs at the translation boundary, at every
`mergeConsecutiveSameRoleContents` call site. In OmniRoute 3.8.50 those are 12 sites over
6 compiled chunks — 2 per chunk, an expression site (`...,f.contents=merge(f.contents),f}`)
and a statement site (`o.contents=merge(o.contents??[]);let _=t.tools`) — and one site in
the `open-sse/translator/request/openai-to-gemini.ts` source. That sentence is a measurement
of one release, not a contract on the next: `tools/vertex-patch-gate.py` counts the sites on
disk instead of trusting it, which is what the count in it is a floor for.

## The three guards, and why v1 and v2 were each incomplete

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

**v2 (lane VERTEX-GUARD, 2026-10-09)** pops the whole trailing model run and refills an
emptied array:

```js
for (; contents.length && contents[contents.length - 1].role === "model"; ) contents.pop();
contents.length || contents.push({ role: "user", parts: [{ text: "Continue." }] });
```

v2 fixed the trailing-turn family completely, but it still never looks inside a turn, so
the D-908 mixed turn survived untouched.

**v3 (lane VERTEX-LIVE, 2026-10-09, D-908)** splits every mixed user turn first, then runs
the v2 pop/refill unchanged:

```js
contents = (Array.isArray(contents) ? contents : []).reduce((a,c)=>{
  const q=c&&Array.isArray(c.parts)?c.parts:[];
  if(!c||"object"!=typeof c||(c.role!=="user"&&c.role!=="model"))return a;
  if(c.role!=="user"){a.push(c);return a}
  const r=q.filter(p=>p&&p.functionResponse),x=q.filter(p=>!p||!p.functionResponse);
  if(r.length&&x.length){a.push({role:"user",parts:r});
    a.push({role:"model",parts:[{text:"Noted."}]});
    a.push({role:"user",parts:x})}else{a.push(c)}
  return a
},[]);
for (; contents.length && contents[contents.length - 1].role === "model"; ) contents.pop();
contents.length || contents.push({ role: "user", parts: [{ text: "Continue." }] });
```

A `user` turn that carries at least one part with a `functionResponse` **and** at least one
part without becomes, in original order: `user`[the functionResponse parts],
`model`[`{text:"Noted."}`], `user`[the other parts]. A text-only turn and a
functionResponse-only turn are left exactly as they were. Because the split ends on a
`user` turn, the final contents end on `user`, and the synthetic `model` turn sits
mid-history where the pop can never reach it.

The split is deliberately no less tolerant than v2 on a malformed body, because v3 is
the first guard that looks *inside* a turn and so the first that dereferences `parts`.
Non-array `contents` is treated as an empty array (and so is refilled with the Continue
turn, exactly as v2 refills an emptied one); a null or non-object entry, or a turn whose
`role` is neither `user` nor `model`, is skipped rather than dereferenced; and a turn's
`parts` is read only when it is a real array, so `parts: null` or a missing `parts` are
tolerated. A `functionResponse` part inside a `role:"model"` turn is not a mixed *user*
turn: it passes through untouched and keeps v2 trailing-pop semantics. All of this is
pinned by `RawShapes` in `tests/test_vertex_trailing_turn_patch.py`, which runs the
patcher's own guard text over these raw shapes.

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
- **The split's `"Noted."` turn is invented too.** Vertex needs a `model` turn between the
  two `user` turns (two consecutive same-role turns would be re-merged by the gateway's own
  merge function on the next call). It carries no content of consequence; the function
  results and the user's text are preserved byte for byte.
- **A body whose last turn is already `user` is untouched** unless that turn is mixed, and
  `systemInstruction` is not part of `contents`, so it is unaffected either way. Shapes are
  asserted per case in `tests/test_vertex_trailing_turn_patch.py` (`Shapes`: lone model
  turn, model tail behind a user history, several model turns in a row, all-model history,
  user tail, empty contents, `systemInstruction`, and the D-908 single/parallel split).

## Tools

| tool | what it patches |
|---|---|
| `tools/apply-vertex-patch.py <chunks-dir>` | the 12 compiled-chunk sites only |
| `tools/vertex-trailing-turn-reapply.ps1` | those 12 **plus** the `.ts` source site (13) |
| `tools/vertex-patch-gate.py <chunks-dir>` | nothing — it counts the sites, v3 guards, refills and splits in **every** chunk on disk and fails a build whose tree is not fully guarded |

Both patchers decide every site in exactly three states and refuse anything else:

```
the v3 guard is present                       -> SKIP   (idempotent)
v2 text, v1 text, or pristine text            -> PATCH  (install, or upgrade in place)
anything else                                 -> ERROR  (exit 1; nothing is guessed)
```

The upgrade forms are tried newest first (v2, then v1, then pristine), and the reason the
order is load-bearing is the `.ts` site, not the chunks. A chunk site is matched on its
whole anchor — head *and* tail together — so a chunk holds exactly one of the states and
the order decides nothing: the guard sits between the head and the tail, so no two forms
overlap. The `.ts` site is the one where order matters: its pristine anchor
`result.contents = mergeConsecutiveSameRoleContents(result.contents ?? []);` is the first
line of every block, so matching it first would splice the v3 block in above the v1/v2
text it is meant to remove — one site carrying both guards, the broken one still running.
The chunk table is kept in the same order because the two tables are asserted byte-identical
(`test_the_reapply_script_carries_every_anchor_and_replacement`), so there is one rule to
remember instead of two. A site whose anchor matches more than once is reported as an error
rather than half-patched, because a chunk whose text moved is a chunk this table no longer
describes.

`npm update omniroute` (or a `FROM` bump of the gateway base image) reverts every patch;
re-run the script, or rebuild the image, afterwards. Each modified file is copied to
`<file>.autoos-backup-<timestamp>` first (AGENTS.md rule 5).

## Where the patch actually ships

The runtime image has a read-only rootfs and no python, so the patch runs at **build**
time: `configuration/docker/ai-stack/omniroute.Dockerfile` copies the chunks into a
`python:3.12-slim` stage, runs `apply-vertex-patch.py` twice, and hands the tree to
`tools/vertex-patch-gate.py`, which stops the build unless the v3 guard count equals the
call-site count, the refill count and the split count each equal it too, at least 12 call
sites exist, no v1 guard text survives anywhere in the tree, and both run summaries account
for exactly that many sites (run 2 patching anything at all is a failed build: the guard
must be idempotent).

The gate counts **every** `*.js` chunk on disk, never the 6 names the patcher's table
carries. That was the gate's own defect (lane VERTEX-GUARD, gate 06c19a): the stage used to
grep `Done: 12 patched` and count refills over a hard-coded chunk list, so a `FROM` bump that
added a 13th `mergeConsecutiveSameRoleContents` call site in a new chunk — or renamed one of
the 6 — shipped a gateway unpatched at that site while every number still matched, because
the check had only ever counted the files it named. A gate that reads the tree has nothing to
know in advance: chunk names, chunk count and site count come from the text, and a mismatch
fails the build with the counts and the offending file printed, which is what a human then
adds to `tools/apply-vertex-patch.py`.

The counts stayed **12** for v3 because the anchors did not move — only the guard inserted
at them changed. That is also why the patcher's report alone is never enough: the v1 and v2
patchers report `12 patched` too, and so does the v3 patcher on a tree with a 13th site it
cannot see. The gate therefore judges the guard's own bytes (the split marker and the refill
marker) as well as the arithmetic, so a tree left on the v2 guard fails as `split=0` rather
than passing on matching numbers.

## Impact

- **Trailing-turn family:** a translated Vertex/Gemini request never ends on a `role:"model"`
  turn and never ends on an empty `contents` array.
- **D-908 mixed-turn family:** a `user` turn that mixes a `functionResponse` with text on the
  signed tool path (trace in `logs/briefs/evidence/vertex-capture-d908.md`) is split into
  `user[functionResponse]`, `model["Noted."]`, `user[text]`.

Nothing else is touched: the patch is inside the OpenAI-to-Gemini translator, so other
providers' requests never reach it.

## Compatibility

- OmniRoute v3.8.50 (verified against the npm tarball, against
  `autoos/omniroute:3.8.50-autoos3` (v1), and against `autoos/omniroute:3.8.50-autoos4` (v2))
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
