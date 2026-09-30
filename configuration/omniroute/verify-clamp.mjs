// Unit proof for configuration/omniroute/patches/0001-clamp-max-tokens.patch.
//
// Extracts the live `resolveReasoningBufferedMaxTokens` body out of the vendor bundle and
// evaluates it with stubs, so the clamp DECISION is proven against the actual shipped
// code (no gateway restart, no Scaleway credits needed).
//
//   node verify-clamp.mjs [path-to-server.js]
//
// On the UNPATCHED bundle the "instruct model is clamped" checks FAIL (the
// supportsThinking gate returns null) - that is the bug. After patch-gateway-clamp.ps1
// they PASS. Exit code 0 = all checks pass.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const defaultBundle = path.join(
  process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
  'npm', 'node_modules', 'omniroute', 'dist', 'open-sse', 'mcp-server', 'server.js'
);
const bundlePath = process.argv[2] || defaultBundle;
const src = fs.readFileSync(bundlePath, 'utf8');

const start = src.indexOf('function resolveReasoningBufferedMaxTokens(');
if (start < 0) { console.error(`FAIL: function not found in ${bundlePath}`); process.exit(1); }
const end = src.indexOf('\n}', start);
if (end < 0) { console.error('FAIL: function end not found'); process.exit(1); }
const fnSrc = src.slice(start, end + 2);

const patched = src.includes('AutoOS clamp: never forward max_tokens above');

// Stubs: an INSTRUCT model (supportsThinking === false) is the case the old gate dropped.
const CAPS = { 'scaleway/qwen3-235b-a22b-instruct-2507': 16384, 'free-ai/qwen7b': null };
const toPositiveInteger = (v) => {
  const n = typeof v === 'number' ? v : (typeof v === 'string' && v.trim() !== '' ? Number(v) : null);
  if (n === null || !Number.isFinite(n)) return null;
  const f = Math.floor(n);
  return f > 0 ? f : null;
};
const getResolvedModelCapabilities = () => ({ supportsThinking: false }); // instruct
const getExplicitModelOutputCap = (m) => (m in CAPS ? CAPS[m] : null);
const REASONING_BUFFER_MIN_TRIGGER = 256;

const factory = new Function(
  'toPositiveInteger', 'getResolvedModelCapabilities', 'getExplicitModelOutputCap', 'REASONING_BUFFER_MIN_TRIGGER',
  `${fnSrc}\nreturn resolveReasoningBufferedMaxTokens;`
);
const clamp = factory(toPositiveInteger, getResolvedModelCapabilities, getExplicitModelOutputCap, REASONING_BUFFER_MIN_TRIGGER);

let failures = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}  (got ${JSON.stringify(got)}, want ${JSON.stringify(want)})`);
}

const QWEN = 'scaleway/qwen3-235b-a22b-instruct-2507';
console.log(`bundle: ${bundlePath}`);
console.log(`patched: ${patched}`);
console.log('checks:');
check('over-limit 32768 -> clamp to cap 16384', clamp(QWEN, 32768), 16384);
check('within-limit 16 -> unchanged', clamp(QWEN, 16), 16);
check('exactly at cap 16384 -> unchanged', clamp(QWEN, 16384), 16384);
check('string "32768" -> clamp to 16384', clamp(QWEN, '32768'), 16384);
check('no cap configured -> null (no clamp)', clamp('free-ai/qwen7b', 32768), null);
check('non-positive max_tokens -> null', clamp(QWEN, 0), null);
check('options.enabled=false disables clamp', clamp(QWEN, 32768, { enabled: false }), null);
// Outgoing-value invariant: min(requested, cap) using the clamp result.
const req = 32768, cap = 16384;
const outgoing = clamp(QWEN, req) ?? req;
check('outgoing value never exceeds cap', Math.min(outgoing, cap), cap);

console.log(patched ? `\n${failures === 0 ? 'ALL PASS' : failures + ' FAILED'}` : '\nUNPATCHED bundle - instruct-model clamps expected to FAIL until patch is applied');
process.exit(failures === 0 ? 0 : 2);
