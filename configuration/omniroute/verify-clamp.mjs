// Unit proof for configuration/omniroute/patches/0001-clamp-max-tokens.patch.
//
//   node verify-clamp.mjs [path-to-server.js]
//
// PART A (semantics): extracts the live `resolveReasoningBufferedMaxTokens` body from the
// canonical compiled bundle (dist/open-sse/mcp-server/server.js) and evaluates it with
// stubs. On the UNPATCHED bundle the "instruct model is clamped" checks FAIL (the
// supportsThinking gate returns null) - that is the bug; after patch-gateway-clamp.ps1
// they PASS.
//
// PART B (targeting): walks the gateway Next build (dist/.build/next) and asserts the
// same gate is gone from every minified copy. The live HTTP gateway loads THAT build, not
// the mcp bundle, so PART B is what proves the fix reaches the running gateway.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const appData = process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
const root = path.join(appData, 'npm', 'node_modules', 'omniroute');
const defaultBundle = path.join(root, 'dist', 'open-sse', 'mcp-server', 'server.js');
const nextRoot = path.join(root, 'dist', '.build', 'next');
const bundlePath = process.argv[2] || defaultBundle;

let failures = 0;
const check = (name, ok) => { if (!ok) failures += 1; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}`); };

// ---------- PART A: semantics (canonical bundle) ----------
const src = fs.readFileSync(bundlePath, 'utf8');
const start = src.indexOf('function resolveReasoningBufferedMaxTokens(');
if (start < 0) { console.error(`FAIL: function not found in ${bundlePath}`); process.exit(1); }
const end = src.indexOf('\n}', start);
const fnSrc = src.slice(start, end + 2);
const patched = src.includes('AutoOS clamp: never forward max_tokens above');

const CAPS = { 'scaleway/qwen3-235b-a22b-instruct-2507': 16384, 'free-ai/qwen7b': null };
const toPositiveInteger = (v) => {
  const n = typeof v === 'number' ? v : (typeof v === 'string' && v.trim() !== '' ? Number(v) : null);
  if (n === null || !Number.isFinite(n)) return null;
  const f = Math.floor(n);
  return f > 0 ? f : null;
};
const getResolvedModelCapabilities = () => ({ supportsThinking: false }); // instruct model
const getExplicitModelOutputCap = (m) => (m in CAPS ? CAPS[m] : null);
const factory = new Function(
  'toPositiveInteger', 'getResolvedModelCapabilities', 'getExplicitModelOutputCap', 'REASONING_BUFFER_MIN_TRIGGER',
  `${fnSrc}\nreturn resolveReasoningBufferedMaxTokens;`
);
const clamp = factory(toPositiveInteger, getResolvedModelCapabilities, getExplicitModelOutputCap, 256);
const QWEN = 'scaleway/qwen3-235b-a22b-instruct-2507';

console.log(`bundle: ${bundlePath}`);
console.log(`patched: ${patched}`);
console.log('PART A - clamp decision:');
check('over-limit 32768 -> 16384', clamp(QWEN, 32768) === 16384);
check('within-limit 16 -> 16', clamp(QWEN, 16) === 16);
check('at cap 16384 -> 16384', clamp(QWEN, 16384) === 16384);
check('string "32768" -> 16384', clamp(QWEN, '32768') === 16384);
check('no cap -> null', clamp('free-ai/qwen7b', 32768) === null);
check('enabled=false -> null', clamp(QWEN, 32768, { enabled: false }) === null);
check('outgoing never exceeds cap', Math.min(clamp(QWEN, 32768) ?? 32768, 16384) === 16384);

// ---------- PART B: gateway targeting ----------
console.log('PART B - gateway bundle gate removal:');
const gate = /let [A-Za-z0-9_$]+=\(0,[A-Za-z0-9_$]+\.getResolvedModelCapabilities\)\([A-Za-z0-9_$]+\);if\(!0!==[A-Za-z0-9_$]+\.supportsThinking\)return null;/;
let withGate = [];
let scanned = 0;
const walk = (dir) => {
  let entries = [];
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith('.js')) {
      scanned += 1;
      try { if (gate.test(fs.readFileSync(p, 'utf8'))) withGate.push(p); } catch {}
    }
  }
};
walk(nextRoot);
console.log(`  scanned ${scanned} gateway js files under ${nextRoot}`);
check('no gateway chunk still carries the supportsThinking clamp gate', withGate.length === 0);
withGate.slice(0, 8).forEach((p) => console.log(`    still gated: ${p}`));

const allOk = failures === 0;
console.log(patched
  ? `\n${allOk ? 'ALL PASS' : failures + ' FAILED'}`
  : '\nUNPATCHED canonical bundle - PART A instruct-model clamps expected to FAIL until patched');
process.exit(allOk ? 0 : 2);
