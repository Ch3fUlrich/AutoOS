import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function getRepoRoot() {
  if (process.env.AUTOOS_REPO_ROOT) {
    return process.env.AUTOOS_REPO_ROOT;
  }
  // configuration/opencode/plugins/bash-guard/index.mjs -> four levels up is the repo root
  const root4 = path.resolve(__dirname, "../../../..");
  if (fs.existsSync(path.join(root4, "tools", "hooks", "bash_guard.py"))) {
    return root4;
  }
  const root3 = path.resolve(__dirname, "../../..");
  if (fs.existsSync(path.join(root3, "tools", "hooks", "bash_guard.py"))) {
    return root3;
  }
  return root4;
}

function runProcess(pythonBin, guardPath, payloadJson, timeoutMs = 5000) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(pythonBin, [guardPath], {
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      resolve({ spawnError: err });
      return;
    }

    const pid = child.pid;
    let stderr = "";
    let stdout = "";
    let timedOut = false;
    let settled = false;

    const timer = setTimeout(() => {
      timedOut = true;
      if (pid) {
        try {
          process.kill(pid);
        } catch (_) {}
      }
      if (!settled) {
        settled = true;
        resolve({ timedOut: true });
      }
    }, timeoutMs);

    child.on("error", (err) => {
      clearTimeout(timer);
      if (!settled) {
        settled = true;
        resolve({ spawnError: err });
      }
    });

    child.stdout?.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      if (!settled) {
        settled = true;
        resolve({ code, stdout, stderr, timedOut });
      }
    });

    try {
      child.stdin.end(payloadJson, "utf8");
    } catch (_) {}
  });
}

function isPilotScopedPath(p) {
  let t = p.trim().replace(/^["']+|["']+$/g, "");
  if (!t || t === "-" || t === "/dev/null") {
    return true;
  }
  if (t === ".oc-pilot" || t.startsWith(".oc-pilot/")) {
    return true;
  }
  if (t.includes("/.oc-pilot/") || t.endsWith("/.oc-pilot")) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Orchestrator role (AUTOOS_GUARD_ROLE=orchestrator)
//
// The orchestrator coordinates the repo but must not modify it: every shell
// write is limited to .oc-pilot/. These scanners are heuristics in the same
// spirit as tools/hooks/bash_guard.py (which still runs afterwards, so the
// legacy incident rules keep applying in orchestrator mode too):
//
// - output redirections (single or double, noclobber, file-descriptor forms,
//   ampersand form, quoted destinations) to anything outside .oc-pilot/
// - tee, sed in-place, cp, mv, rm, mkdir, touch, ln, dd, find mutation
//   actions, xargs
// - inline python (-c) file writes whose target is outside .oc-pilot/ or
//   cannot be determined
// - git: a strict allow list (read-only subcommands plus cherry-pick,
//   merge --ff-only, push); every other subcommand is denied
// - nested sh/bash -c strings are audited recursively (depth cap 3)
//
// Unknown commands are allowed (the read-only default); anything that looks
// like a write whose target cannot be determined is denied.

const ORCH_ENV_ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=/;
const ORCH_KEYWORDS = new Set([
  "do", "done", "if", "then", "else", "elif", "fi", "while", "until",
  "case", "esac", "for", "select", "in", "function", "coproc", "{", "}", "!",
]);
const ORCH_GIT_ALLOW = new Set([
  "fetch", "log", "show", "diff", "status", "branch", "cherry-pick", "push",
]);
const ORCH_GIT_DENY = new Set(["apply", "am", "commit", "reset", "rebase"]);
const ORCH_PY_MOD_FUNCS = {
  os: {
    first: ["remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "truncate"],
    last: ["symlink", "link"],
    all: ["rename", "replace"],
  },
  shutil: {
    first: ["rmtree"],
    last: ["copy", "copy2", "copyfile", "copytree", "symlink", "link"],
    all: ["move"],
  },
};
const ORCH_PATH_METHODS = [
  "write_text", "write_bytes", "mkdir", "unlink", "touch", "rmdir", "rename", "replace",
];

function isRedirectWord(t) {
  const c = t.replace(/^&/, "").replace(/^\d+/, "");
  return c === ">" || c === ">>" || c === ">|" || c.startsWith(">&");
}

function baseName(w) {
  const norm = w.replace(/\\/g, "/");
  const i = norm.lastIndexOf("/");
  return i === -1 ? norm : norm.slice(i + 1);
}

function skipLeading(words, idx) {
  // Skip env assignments, shell keywords and wrapper commands (sudo, env,
  // nice, timeout, command, nohup, time, exec, setsid, builtin) the same way
  // tools/hooks/bash_guard.py does, so `sudo rm /` is still audited as `rm`.
  const n = words.length;
  while (idx < n) {
    const w = words[idx];
    if (isRedirectWord(w)) { idx += 1; continue; }
    if (ORCH_ENV_ASSIGN.test(w)) { idx += 1; continue; }
    if (ORCH_KEYWORDS.has(w)) { idx += 1; continue; }
    const base = baseName(w);
    if (base === "sudo") {
      idx += 1;
      while (idx < n) {
        const opt = words[idx];
        if (opt === "--") { idx += 1; break; }
        if (["-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U"].includes(opt)) { idx += 2; continue; }
        if (opt.startsWith("-")) { idx += 1; continue; }
        break;
      }
      continue;
    }
    if (base === "env") {
      idx += 1;
      while (idx < n) {
        const opt = words[idx];
        if (opt === "--") { idx += 1; break; }
        if (opt === "-u") { idx += 2; continue; }
        if (opt.startsWith("-")) { idx += 1; continue; }
        if (ORCH_ENV_ASSIGN.test(opt)) { idx += 1; continue; }
        break;
      }
      continue;
    }
    if (base === "nice") {
      idx += 1;
      while (idx < n) {
        const opt = words[idx];
        if (opt === "--") { idx += 1; break; }
        if (opt === "-n") { idx += 2; continue; }
        if (opt.startsWith("-")) { idx += 1; continue; }
        break;
      }
      continue;
    }
    if (base === "timeout") {
      idx += 1;
      while (idx < n) {
        const opt = words[idx];
        if (opt === "--") { idx += 1; break; }
        if (opt === "-s" || opt === "-k" || opt === "--signal" || opt === "--kill-after") { idx += 2; continue; }
        if (opt.startsWith("-")) { idx += 1; continue; }
        break;
      }
      if (idx < n && !words[idx].startsWith("-")) { idx += 1; }
      continue;
    }
    if (["command", "nohup", "time", "exec", "setsid", "builtin"].includes(base)) {
      idx += 1;
      while (idx < n && words[idx].startsWith("-")) {
        if (words[idx] === "--") { idx += 1; break; }
        idx += 1;
      }
      continue;
    }
    break;
  }
  return idx;
}

function matchParen(s, openIdx) {
  // Index of the ")" matching the "(" at openIdx, quote/escape aware, or -1.
  let depth = 1;
  let j = openIdx + 1;
  const n = s.length;
  while (j < n && depth > 0) {
    const d = s[j];
    if (d === "'") {
      const k = s.indexOf("'", j + 1);
      j = k === -1 ? n : k + 1;
      continue;
    }
    if (d === '"') {
      let k = j + 1;
      while (k < n) {
        if (s[k] === "\\" && k + 1 < n) { k += 2; continue; }
        if (s[k] === '"') break;
        k += 1;
      }
      j = k + 1;
      continue;
    }
    if (d === "\\" && j + 1 < n) { j += 2; continue; }
    if (d === "(") depth += 1;
    else if (d === ")") depth -= 1;
    j += 1;
  }
  return depth === 0 ? j - 1 : -1;
}

function collectSegments(command) {
  // Split a command string into top-level simple-command segments on ";",
  // "|", "&" and newlines outside quotes. Command substitutions and subshells
  // ($( ... ), ( ... ), backticks) are walked recursively, because a write in
  // `x=$(cp a /tmp/f)` is a real write. The `>|` and `&>` redirect operators
  // are not pipe/background separators.
  const out = [];
  const walk = (s, depth) => {
    if (depth > 8 || !s) return;
    let buf = "";
    const flush = () => {
      const t = buf.replace(/[ \t\r\n]+/g, " ").trim();
      buf = "";
      if (t) out.push(t);
    };
    let i = 0;
    const n = s.length;
    while (i < n) {
      const c = s[i];
      if (c === "'") {
        const j = s.indexOf("'", i + 1);
        buf += s.slice(i, j === -1 ? n : j + 1);
        i = j === -1 ? n : j + 1;
        continue;
      }
      if (c === '"') {
        // Command substitutions inside double quotes still execute, so walk
        // them; the quoted text itself is only data for the outer command.
        let j = i + 1;
        while (j < n) {
          const d = s[j];
          if (d === "\\" && j + 1 < n) { j += 2; continue; }
          if (d === '"') break;
          if (d === "`") {
            let k = j + 1;
            while (k < n && s[k] !== "`") {
              if (s[k] === "\\" && k + 1 < n) k += 1;
              k += 1;
            }
            walk(s.slice(j + 1, k), depth + 1);
            j = k + 1;
            continue;
          }
          if (d === "$" && s[j + 1] === "(") {
            const k = matchParen(s, j + 1);
            walk(s.slice(j + 2, k === -1 ? n : k), depth + 1);
            j = k === -1 ? n : k + 1;
            continue;
          }
          j += 1;
        }
        buf += s.slice(i, j < n ? j + 1 : n);
        i = j < n ? j + 1 : n;
        continue;
      }
      if (c === "\\" && i + 1 < n) { buf += s.slice(i, i + 2); i += 2; continue; }
      if (c === "#") {
        if (buf.trim() === "" || /[\s;|&(]$/.test(buf)) {
          const j = s.indexOf("\n", i);
          i = j === -1 ? n : j;
          continue;
        }
      }
      if (c === "`") {
        let j = i + 1;
        while (j < n && s[j] !== "`") {
          if (s[j] === "\\" && j + 1 < n) j += 1;
          j += 1;
        }
        flush();
        walk(s.slice(i + 1, j === n ? n : j), depth + 1);
        i = j === n ? n : j + 1;
        continue;
      }
      if (c === "$" && s[i + 1] === "(") {
        const j = matchParen(s, i + 1);
        flush();
        walk(s.slice(i + 2, j === -1 ? n : j), depth + 1);
        i = j === -1 ? n : j + 1;
        continue;
      }
      if (c === "(") {
        const j = matchParen(s, i);
        flush();
        walk(s.slice(i + 1, j === -1 ? n : j), depth + 1);
        i = j === -1 ? n : j + 1;
        continue;
      }
      if (c === ";" || c === "\n") { flush(); i += 1; continue; }
      if (c === "|") {
        if (buf.endsWith(">") || buf.endsWith(">>")) { buf += c; i += 1; continue; }
        flush(); i += 1; continue;
      }
      if (c === "&") {
        if (s[i + 1] === ">") { buf += c; i += 1; continue; }
        flush(); i += 1; continue;
      }
      buf += c;
      i += 1;
    }
    flush();
  };
  walk(command, 0);
  return out;
}

function tokenizeSegment(seg) {
  // Quote-aware word split; adjacent quoted/unquoted parts concatenate into
  // one word, so `> "outside.txt"` yields the words ">" and "outside.txt".
  const words = [];
  let cur = "";
  let started = false;
  let i = 0;
  const n = seg.length;
  const push = () => {
    if (started) {
      words.push(cur);
      cur = "";
      started = false;
    }
  };
  while (i < n) {
    const c = seg[i];
    if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      push();
      i += 1;
      continue;
    }
    started = true;
    if (c === "'") {
      const j = seg.indexOf("'", i + 1);
      cur += j === -1 ? seg.slice(i + 1) : seg.slice(i + 1, j);
      i = j === -1 ? n : j + 1;
      continue;
    }
    if (c === '"') {
      let j = i + 1;
      let part = "";
      while (j < n && seg[j] !== '"') {
        if (seg[j] === "\\" && j + 1 < n && ['"', "\\", "$", "`", "\n"].includes(seg[j + 1])) {
          part += seg[j + 1];
          j += 2;
          continue;
        }
        part += seg[j];
        j += 1;
      }
      cur += part;
      i = j < n ? j + 1 : n;
      continue;
    }
    if (c === "\\" && i + 1 < n) {
      cur += seg[i + 1];
      i += 2;
      continue;
    }
    cur += c;
    i += 1;
  }
  push();
  return words;
}

function redirectWriteTargets(command) {
  // Every output-redirect destination in the command, in order. Quote-aware:
  // a `>` inside quotes is data, and a quoted destination (`> "f"`) is read
  // as the unquoted path. Dup redirections (`>&2`, `1>&2`) and process
  // substitution (`>(...)`) carry no file target.
  const targets = [];
  const n = command.length;
  let i = 0;
  while (i < n) {
    const c = command[i];
    if (c === "'") {
      const j = command.indexOf("'", i + 1);
      i = j === -1 ? n : j + 1;
      continue;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < n) {
        if (command[j] === "\\" && j + 1 < n) { j += 2; continue; }
        if (command[j] === '"') break;
        j += 1;
      }
      i = j < n ? j + 1 : n;
      continue;
    }
    if (c === "\\" && i + 1 < n) { i += 2; continue; }
    if (c !== ">") { i += 1; continue; }
    let opEnd = i;
    while (opEnd + 1 < n && command[opEnd + 1] === ">") opEnd += 1;
    if (opEnd + 1 < n && command[opEnd + 1] === "|") opEnd += 1; // noclobber
    if (opEnd + 1 < n && command[opEnd + 1] === "&") { i = opEnd + 1; continue; } // dup
    let j = opEnd + 1;
    while (j < n && (command[j] === " " || command[j] === "\t")) j += 1;
    let target = "";
    let end = j;
    if (j < n && (command[j] === "'" || command[j] === '"')) {
      const q = command[j];
      const k = command.indexOf(q, j + 1);
      target = k === -1 ? command.slice(j + 1) : command.slice(j + 1, k);
      end = k === -1 ? n : k + 1;
    } else {
      while (j < n && !/[ \t;|&<>\n()]/.test(command[j])) {
        target += command[j];
        j += 1;
      }
      end = j;
    }
    if (target) targets.push(target);
    i = end;
  }
  return targets;
}

function checkSed(args) {
  let inPlace = false;
  let afterDd = false;
  let explicitExprs = 0;
  const files = [];
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (afterDd) { files.push(a); continue; }
    if (a === "--") { afterDd = true; continue; }
    if (a.startsWith("-")) {
      if (a === "-i" || a.startsWith("-i") || a === "--in-place" || a.startsWith("--in-place=")) {
        inPlace = true;
        continue;
      }
      if (a === "-e" || a === "--expression") {
        k += 1; // the expression value
        explicitExprs += 1;
        continue;
      }
      continue;
    }
    files.push(a);
  }
  if (!inPlace) return null;
  // Without -e the first operand is the script; the rest are the target files.
  const candidates = explicitExprs === 0 && files.length > 0 ? files.slice(1) : files;
  for (const f of candidates) {
    if (f === "-" || isRedirectWord(f)) continue;
    if (!isPilotScopedPath(f)) {
      return `sed -i edits ${f} (outside .oc-pilot/)`;
    }
  }
  return null;
}

function checkCopyMove(cmd, args) {
  let targetDir = null;
  const nonFlags = [];
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (a === "-t" || a === "--target-dir") {
      targetDir = k + 1 < args.length ? args[k + 1] : null;
      k += 1;
      continue;
    }
    if (a.startsWith("-") || isRedirectWord(a)) continue;
    nonFlags.push(a);
  }
  const checks = [];
  if (targetDir !== null) {
    checks.push(targetDir);
  } else if (nonFlags.length) {
    checks.push(nonFlags[nonFlags.length - 1]);
  }
  // mv also deletes its source(s), which is a mutation of that path.
  if (cmd === "mv" && nonFlags.length >= 2) {
    checks.push(nonFlags[0]);
  }
  for (const d of checks) {
    if (!isPilotScopedPath(d)) {
      return `${cmd} writes outside .oc-pilot/ (${d})`;
    }
  }
  return null;
}

function gitSubcommand(args) {
  // git's subcommand and the arguments after it, skipping the global options
  // that carry a value (`git -C dir status`). Null when no subcommand follows.
  // The skipped option tokens travel back as `opts`: a caller whose rule is
  // "these verbs are read-only" must also see `-c` / `-C`, which are how a
  // read-only verb is handed a program to run (`git -c core.fsmonitor=... status`).
  const n = args.length;
  let i = 0;
  const opts = [];
  while (i < n) {
    const a = args[i];
    if (a === "--") break;
    if (a.startsWith("-")) {
      opts.push(a);
      const bare = a.split("=")[0];
      const takesValue = ["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--exec-path"].includes(bare);
      // `--git-dir=x` carries its own value; only the bare spelling eats the
      // next token.
      if (takesValue && !a.includes("=")) {
        i += 2;
      } else {
        i += 1;
      }
      continue;
    }
    break;
  }
  if (i >= n) return { sub: null, rest: [], opts };
  return { sub: args[i], rest: args.slice(i + 1), opts };
}

function checkGit(args) {
  const g = gitSubcommand(args);
  if (g === null || g.sub === null) return null;
  const sub = g.sub;
  const rest = g.rest;
  if (ORCH_GIT_DENY.has(sub)) {
    return `git ${sub} is a mutation command; orchestrators may not use it`;
  }
  if (sub === "checkout") {
    if (rest.length > 0) {
      return "git checkout writes to the worktree; orchestrators may not use it";
    }
    return null;
  }
  if (sub === "merge") {
    return rest.includes("--ff-only")
      ? null
      : "git merge without --ff-only can create a merge commit; orchestrators may not use it";
  }
  if (ORCH_GIT_ALLOW.has(sub)) return null;
  return `git ${sub} is not on the orchestrator allow list`;
}

function shellCString(args) {
  // The string argument of a `sh/bash ... -c <string>` invocation, or null.
  for (let k = 0; k < args.length; k++) {
    const a = args[k];
    if (a === "--") break;
    if (a.startsWith("--")) {
      if (a === "--rcfile") k += 1;
      continue;
    }
    if (a.startsWith("-")) {
      if (a.length > 1 && a.includes("c")) {
        return k + 1 < args.length ? args[k + 1] : null;
      }
      if (a === "-o") { k += 1; continue; }
      continue;
    }
    break;
  }
  return null;
}

function readCallArgs(code, openIdx) {
  // Text between the "(" at openIdx and its matching ")", quote aware.
  let depth = 1;
  let i = openIdx + 1;
  const n = code.length;
  while (i < n && depth > 0) {
    const c = code[i];
    if (c === "'" || c === '"') {
      const j = code.indexOf(c, i + 1);
      i = j === -1 ? n : j + 1;
      continue;
    }
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    i += 1;
  }
  if (depth !== 0) return null;
  return code.slice(openIdx + 1, i - 1);
}

function splitTopLevelCommas(s) {
  const parts = [];
  let cur = "";
  let depth = 0;
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (c === "'" || c === '"') {
      const j = s.indexOf(c, i + 1);
      if (j === -1) { cur += s.slice(i); i = n; continue; }
      cur += s.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    if (c === "," && depth === 0) {
      parts.push(cur);
      cur = "";
      i += 1;
      continue;
    }
    cur += c;
    i += 1;
  }
  parts.push(cur);
  return parts.map((p) => p.trim()).filter((p) => p !== "");
}

function stringLiteralOf(part) {
  if (!part) return null;
  const t = part.trim();
  let q = null;
  if (t.startsWith('"')) q = '"';
  else if (t.startsWith("'")) q = "'";
  else return null;
  let i = 1;
  const n = t.length;
  while (i < n) {
    if (q === '"' && t[i] === "\\" && i + 1 < n) { i += 2; continue; }
    if (t[i] === q) return t.slice(1, i);
    i += 1;
  }
  return null;
}

function receiverPathLiteral(code, dotIdx) {
  // For `Path('lit').method(...)`, the string literal inside Path(...).
  // dotIdx is the index of the "." before the method name.
  let i = dotIdx - 1;
  while (i >= 0 && (code[i] === " " || code[i] === "\t")) i -= 1;
  if (i < 0 || code[i] !== ")") return null;
  const closeIdx = i;
  let depth = 1;
  i -= 1;
  while (i >= 0 && depth > 0) {
    const c = code[i];
    if (c === "'" || c === '"') {
      const j = code.lastIndexOf(c, i - 1);
      if (j < 0) return null;
      i = j - 1;
      continue;
    }
    if (c === ")") depth += 1;
    else if (c === "(") depth -= 1;
    i -= 1;
  }
  if (depth !== 0) return null;
  i += 1; // index of the matching "("
  let k = i - 1;
  while (k >= 0 && (code[k] === " " || code[k] === "\t")) k -= 1;
  let e = k;
  while (e >= 0 && /[A-Za-z0-9_.]/.test(code[e])) e -= 1;
  if (code.slice(e + 1, k + 1) !== "Path") return null;
  const parts = splitTopLevelCommas(code.slice(i + 1, closeIdx));
  return parts.length ? stringLiteralOf(parts[0]) : null;
}

function checkPythonCode(code) {
  if (/\b(os\s*\.\s*system|os\s*\.\s*popen)\s*\(/.test(code)) {
    return "python -c calls os.system/popen, which the guard cannot audit";
  }
  if (/\bsubprocess\s*\.\s*(call|run|Popen|check_call|check_output|getoutput|getstatusoutput)\s*\(/.test(code)) {
    return "python -c spawns a subprocess, which the guard cannot audit";
  }

  const openRe = /\bopen\s*\(/g;
  let m;
  while ((m = openRe.exec(code)) !== null) {
    const argsText = readCallArgs(code, m.index + m[0].length - 1);
    if (argsText === null) continue;
    const parts = splitTopLevelCommas(argsText);
    if (!parts.length) continue;
    const pathLit = stringLiteralOf(parts[0]);
    let modeRaw = null;
    let hasMode = false;
    for (let p = 1; p < parts.length; p++) {
      const t = parts[p];
      const kw = t.match(/^mode\s*=\s*(.+)$/);
      if (kw) { modeRaw = kw[1]; hasMode = true; }
      else if (p === 1 && !t.includes("=")) { modeRaw = t; hasMode = true; }
    }
    if (!hasMode) continue; // a single-argument open() reads
    const mode = stringLiteralOf(modeRaw);
    if (mode === null) {
      return "python -c open() carries a mode the guard cannot audit";
    }
    if (!/[wax+]/.test(mode)) continue; // read-only mode
    if (pathLit === null) {
      return "python -c open() writes to a path the guard cannot audit";
    }
    if (!isPilotScopedPath(pathLit)) {
      return `python -c open() writes to ${pathLit} (outside .oc-pilot/)`;
    }
  }

  for (const name of ORCH_PATH_METHODS) {
    const re = new RegExp("\\." + name + "\\s*\\(", "g");
    let m2;
    while ((m2 = re.exec(code)) !== null) {
      const recv = receiverPathLiteral(code, m2.index);
      if (recv === null) {
        return `python -c .${name}() writes to a path the guard cannot audit`;
      }
      if (!isPilotScopedPath(recv)) {
        return `python -c .${name}() writes to ${recv} (outside .oc-pilot/)`;
      }
      if (name === "rename" || name === "replace") {
        const argsText = readCallArgs(code, m2.index + m2[0].length - 1);
        if (argsText !== null) {
          const parts = splitTopLevelCommas(argsText);
          const lit = parts.length ? stringLiteralOf(parts[0]) : null;
          if (lit === null) {
            return `python -c .${name}() carries a target the guard cannot audit`;
          }
          if (!isPilotScopedPath(lit)) {
            return `python -c .${name}() writes to ${lit} (outside .oc-pilot/)`;
          }
        }
      }
    }
  }

  for (const [mod, groups] of Object.entries(ORCH_PY_MOD_FUNCS)) {
    for (const group of ["first", "last", "all"]) {
      for (const fn of groups[group]) {
        const re = new RegExp("\\b" + mod + "\\s*\\.\\s*" + fn + "\\s*\\(", "g");
        let m3;
        while ((m3 = re.exec(code)) !== null) {
          const argsText = readCallArgs(code, m3.index + m3[0].length - 1);
          if (argsText === null) continue;
          const lits = splitTopLevelCommas(argsText).map(stringLiteralOf);
          let targets;
          if (group === "first") targets = lits.slice(0, 1);
          else if (group === "last") targets = lits.slice(-1);
          else targets = lits;
          targets = targets.filter((t) => t !== null);
          if (!targets.length) {
            return `python -c ${mod}.${fn}() carries a path the guard cannot audit`;
          }
          for (const t of targets) {
            if (!isPilotScopedPath(t)) {
              return `python -c ${mod}.${fn}() writes to ${t} (outside .oc-pilot/)`;
            }
          }
        }
      }
    }
  }

  return null;
}

function checkPythonArgs(args) {
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--") break;
    if (args[i] === "-c" && i + 1 < args.length) {
      return checkPythonCode(args[i + 1]);
    }
  }
  return null;
}

function checkCommandByName(cmd, args, depth) {
  if (cmd === "tee") {
    for (const a of args) {
      if (a.startsWith("-") || isRedirectWord(a)) continue;
      if (!isPilotScopedPath(a)) {
        return `tee writes to ${a} (outside .oc-pilot/)`;
      }
    }
    return null;
  }
  if (cmd === "sed") {
    return checkSed(args);
  }
  if (cmd === "cp" || cmd === "mv") {
    return checkCopyMove(cmd, args);
  }
  if (cmd === "rm" || cmd === "mkdir" || cmd === "touch") {
    for (const a of args) {
      if (a.startsWith("-") || isRedirectWord(a)) continue;
      if (!isPilotScopedPath(a)) {
        return `${cmd} touches ${a} (outside .oc-pilot/)`;
      }
    }
    return null;
  }
  if (cmd === "ln") {
    const nonFlags = args.filter((a) => !a.startsWith("-") && !isRedirectWord(a));
    if (nonFlags.length) {
      const dest = nonFlags[nonFlags.length - 1];
      if (!isPilotScopedPath(dest)) {
        return `ln creates ${dest} (outside .oc-pilot/)`;
      }
    }
    return null;
  }
  if (cmd === "dd") {
    for (const a of args) {
      if (a.startsWith("of=")) {
        const t = a.slice(3);
        if (!isPilotScopedPath(t)) {
          return `dd writes to ${t} (outside .oc-pilot/)`;
        }
      }
    }
    return null;
  }
  if (cmd === "find") {
    for (const a of args) {
      if (["-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"].includes(a)) {
        return `find ${a} modifies files the guard cannot scope`;
      }
    }
    return null;
  }
  if (cmd === "xargs") {
    return "xargs runs a command (default rm) with arguments the guard cannot audit";
  }
  if (cmd === "git") {
    return checkGit(args);
  }
  if (/^python\d*\.?\d*$/.test(cmd)) {
    return checkPythonArgs(args);
  }
  if (["bash", "sh", "zsh", "dash", "ksh"].includes(cmd)) {
    const inner = shellCString(args);
    if (inner === null) return null;
    if (depth + 1 > 3) return "nested shell -c too deep to audit";
    return orchestratorDenialReason(inner, depth + 1);
  }
  return null;
}

function checkCommandSegment(seg, depth) {
  const words = tokenizeSegment(seg);
  const idx = skipLeading(words, 0);
  if (idx >= words.length) return null;
  const cmd = baseName(words[idx]);
  return checkCommandByName(cmd, words.slice(idx + 1), depth);
}

function orchestratorDenialReason(command, depth = 0) {
  for (const target of redirectWriteTargets(command)) {
    if (!isPilotScopedPath(target)) {
      return `output redirect to ${target} (outside .oc-pilot/)`;
    }
  }
  for (const seg of collectSegments(command)) {
    const reason = checkCommandSegment(seg, depth);
    if (reason) return reason;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Orchestrator-L2 role (AUTOOS_GUARD_ROLE=l2) - AO-L2-LAUNCH merge criterion 1
//
// An L2 coordinates a phase and writes NOTHING: every change it wants is a
// tier-3 run it spawns over the autoos-agent MCP. `orchestrator` scopes writes
// to .oc-pilot/; this role has no writable scope at all, so the test is a
// CLOSED LIST of command heads rather than a write-target:
//
// - `git status|log|diff|show`, ls, cat, rg, head, tail, wc, pwd
// - no output redirection, to anywhere - not even .oc-pilot/ or /dev/null
// - no stdin redirection and no command or process substitution: each of those
//   feeds or runs something the list never approved (this is also what keeps
//   the canary's own probe denied under the new role)
// - every segment of a pipe is audited, so `ls | tee f` is refused as `tee`
// - the head is a bare allow-listed WORD: `/tmp/evil/cat` and `./cat` are
//   whatever the lane wrote there, so a head with a separator in it is refused
// - no wrapper either (sudo, doas, su, env, exec, command, nice, timeout, time,
//   stdbuf, nohup): `sudo cat` is a second user's `cat`, and a head that is only
//   a wrapper (`env`, `printenv`, `set`, `export`, `declare`) is a dump of the
//   lane's own environment - both refused, not skipped past
// - no backslash in any form: `\<newline>` is a line continuation to bash and a
//   flattened escape space to the tokenizer, which is how `/etc/passwd` once
//   reached the path rules as `" /etc/passwd"`
// - a path operand is allowed only as a repo-relative path it cannot escape:
//   absolute, `~`-led, `$`/backtick/glob-expanded, `..`-segmented or `//`-spaced
//   is refused, and the secret files that live inside the repo (`.env*`,
//   `*.key`, `*.pem`, `.p12/.pfx`, `api-keys.yml`, `*credentials*`, `auth.json`,
//   `.netrc`, `.npmrc`, `.pypirc`, `id_*` keys, anything under `.ssh/`, `.aws/`)
//   with it. Each path a token carries is checked - whole, stripped, and per
//   `:` and `/` segment, so `git show HEAD:.env` is read as `.env` - and an
//   operand that RESOLVES outside the checkout (a symlink) is refused too, as is
//   one the resolver cannot answer
// - an option's value is an operand: `--opt=path` is checked whatever option it
//   hangs off, and a value-taking option whose job is to open a file
//   (`rg --file/--ignore-file/-f/--path-separator`) is refused outright
// - a flag that hands the read a program, a repository, a pager or a symlink
//   walk is refused, inside a short bundle (`rg -uz`) as much as spelled out
// - a flag that makes the read look at files it was never pointed at is refused
//   the same way: rg discovers its own inputs, so the ignore rules are what keep
//   a recursive search off this checkout's gitignored secrets (`rg --hidden KEY`
//   printed ./.env), and git accepts an abbreviation of a long option, so
//   `--outpu` is `--output` (round-5 findings 1 and 2)
//
// Anything else is denied, fail closed.
// ---------------------------------------------------------------------------

const L2_READ_HEADS = new Set(["ls", "cat", "rg", "head", "tail", "wc", "pwd"]);
const L2_GIT_SUBCOMMANDS = new Set(["status", "log", "diff", "show"]);
const L2_LIST_TEXT = "git status|log|diff|show, ls, cat, rg, head, tail, wc, pwd";

// Sonnet final REJECT (criterion b): the head list proves WHICH program runs,
// never WHAT IT RUNS. These are the switches that hand a read-only command a
// program, a repository or an environment of the caller's choosing:
//
// - git global options (the tokens before the subcommand, which `gitSubcommand`
//   used to walk past and throw away): `-c` / `--config-env` write config that
//   git then acts on (`core.fsmonitor`, `core.pager`, `credential.helper`),
//   `-C` / `--git-dir` / `--work-tree` / `--exec-path` / `--namespace` /
//   `--super-prefix` point the read at another repo or another git binary, and
//   `-p` / `--paginate` pipes the output through a shell (`core.pager`).
//   Matched on the option NAME before any `=`, so `--no-pager` stays allowed.
// - git per-verb switches in the rest: `--ext-diff` (and its short `-x`) runs
//   the external diff driver, `--textconv` (`-a`) runs the textconv filter,
//   `--no-index` compares two working-tree paths - arbitrary files. Every long
//   option on the git lists is refused under its ABBREVIATIONS too (`--ext-d`,
//   `--textc`, `--outpu`), because git resolves them - a denied spelling is not
//   a denied flag.
// - rg: `--pre` / `--pre-glob` pipe every file through a program,
//   `--hostname-bin` runs a binary, `--search-zip` / `-z` decompress with it.
// - rg ignore-flag and glob-flag spellings (`--hidden` / `-.`, the `-u` cluster
//   and `--unrestricted`, `--no-ignore*`, `-g` / `--glob` / `--iglob`): rg picks
//   its own inputs, so these are how a read-only head reaches the gitignored
//   secret files this checkout holds.
// - /proc/self/*, /proc/<pid>/*, /proc/*/environ: the lane's own environment -
//   server password, gateway keys - is readable there, so no read head may
//   name one. `/proc/cpuinfo` and friends stay readable.
// - an environment-assignment prefix (`GIT_PAGER=x git log`) reaches the same
//   knobs with no flag at all: the prefix IS the payload.
const L2_GIT_GLOBAL_DENY = new Set([
  "-c", "--config-env", "-C", "--git-dir", "--work-tree", "--exec-path",
  "--namespace", "--super-prefix", "--paginate", "-p",
]);
const L2_GIT_REST_DENY = new Set([
  // "--output" is refused by name above (it is a write, not a read knob); it is
  // on this list too so the abbreviation rule below sees it - `git log --outp=f`
  // is the same write under a shorter spelling.
  "--output", "--ext-diff", "--textconv", "--no-index", "-x", "-a",
]);
const L2_RG_DENY = new Set([
  "--pre", "--pre-glob", "--hostname-bin", "--search-zip", "-z", "--follow", "-L",
]);

// Sonnet round-5 REJECT finding 1 (MED-HIGH): rg DISCOVERS the files it reads, so
// the operand rules above never see them, and the ignore rules are the only thing
// that kept a recursive search off the gitignored secrets sitting in the checkout.
// `--hidden` / `-.` add the dot files, `-u` (`--unrestricted`, and its `-uu`,
// `-uuu` clusters) drops the ignore files, `--no-ignore*` drops one class of them,
// and a glob picks a named file in by hand. All of them printed ./.env.
const L2_RG_IGNORE_DENY = new Set([
  "--hidden", "-.", "--unrestricted", "-u", "--glob", "-g", "--iglob",
]);
// ripgrep's `--no-ignore` family takes a qualifier: --no-ignore-vcs, -parent,
// -dot, -global, -files. Each one is the same read under a longer name.
const L2_RG_IGNORE_DENY_PREFIXES = ["no-ignore"];
const L2_RG_IGNORE_WHY = "makes a recursive search read files it never named - the " +
  "gitignored secrets in this checkout (.env, *.key) are exactly the dot files and " +
  "ignored files these flags switch on";
const L2_PROC_RE = /(^|\/)proc\/(self|thread-self|[0-9]+|\*)\//;

// Sonnet round-4 REJECT finding 3: a value-taking option is how a read command
// opens a file the operand list never saw. `--file` / `--ignore-file` / `-f`
// hand rg a PATTERN FILE (so a path, whatever it is named), `--path-separator`
// changes how every following path is read, and `--pre*` is on the list too so
// the deny does not depend on the flag-bundle check running first.
const L2_RG_PATH_OPTS = new Set([
  "file", "ignore-file", "pre", "pre-glob", "path-separator",
]);
const L2_RG_PATH_SHORTS = new Set(["f"]);

// Sonnet round-4 REJECT finding 7: `skipLeading` is shared with the orchestrator
// role, where a wrapper must be skipped so `sudo rm /` is still audited as `rm`.
// An L2 has no such rule to preserve - its list is of HEADS, and a wrapper is a
// different process around the read (`sudo cat` is root's `cat`, `time ls` is a
// shell builtin wearing a name). The lane needs none of them, so the tokens the
// skip walked past are audited as heads of their own.
const L2_WRAPPER_HEADS = new Set([
  "sudo", "doas", "su", "env", "exec", "command", "nice", "timeout", "time",
  "stdbuf", "nohup",
]);

// Sonnet round-3 REJECT: `skipLeading` skips the `env` WRAPPER on purpose, so a
// bare `env` — no wrapped command at all, just a dump of the environment it was
// launched with — walked past it and had no head to audit. The lane's environment
// carries the server password and the gateway keys. These are the names whose
// whole job is to read or set that environment; they are denied wherever
// `skipLeading` left them, prefix or head.
const L2_ENV_DUMP_HEADS = new Set(["env", "printenv", "set", "export", "declare"]);

function splitOptionDeny(deny) {
  // A short flag hides inside a bundle (`rg -uz` is `rg -u -z`), so an exact
  // token match on "-z" never saw it. Split the deny list by spelling and let
  // the caller test a bundle character by character.
  const long = new Set();
  const short = new Set();
  for (const d of deny) {
    if (d.startsWith("--")) long.add(d.slice(2));
    else if (d.startsWith("-")) short.add(d.slice(1));
  }
  return { long, short };
}

function l2OptionDenial(tokens, deny, label, opts) {
  // A short flag hides inside a bundle (`rg -uz` is `rg -u -z`), so an exact
  // token match on "-z" never saw it. Split the deny list by spelling and let
  // the caller test a bundle character by character.
  //
  // opts.prefixes extend the deny list to every LONG name starting with one of
  // them (ripgrep's `--no-ignore` family), and opts.abbreviate denies every long
  // option that is itself an unambiguous-looking PREFIX of a denied name: git
  // accepts `--outpu` for `--output`, `--ext-d` for `--ext-diff` and `--textc`
  // for `--textconv`, so an exact-match table was a hole, not a rule (round-5
  // finding 2). Three characters is the floor git itself needs to be picky, and
  // it keeps a one- or two-letter prefix from swallowing unrelated options.
  const { long, short } = splitOptionDeny(deny);
  const longNames = [...long];
  const prefixes = (opts && opts.prefixes) || [];
  const abbreviate = !!(opts && opts.abbreviate);
  const why = (opts && opts.why) ||
    "hands the read-only command a program, a repository, a pager or a symlink walk to run";
  for (const t of tokens) {
    if (t.startsWith("--")) {
      const name = t.slice(2).split("=")[0];
      if (long.has(name)) {
        return `${label} ${t} ${why}`;
      }
      const prefixed = prefixes.find((p) => name.startsWith(p));
      if (prefixed !== undefined) {
        return `${label} ${t} (--${prefixed}* spelling) ${why}`;
      }
      if (abbreviate && name.length >= 3) {
        const abbreviated = longNames.find((d) => d.startsWith(name));
        if (abbreviated !== undefined) {
          return `${label} ${t} is an abbreviation of the denied long option ` +
                 `--${abbreviated}; git accepts it as one`;
        }
      }
      continue;
    }
    if (t.length > 1 && t.startsWith("-")) {
      for (const ch of t.slice(1)) {
        if (short.has(ch)) {
          return `${label} ${t} (bundle flag -${ch}) ${why}`;
        }
      }
    }
  }
  return null;
}

function l2ProcDenial(tokens, head) {
  for (const t of tokens) {
    if (t.startsWith("-")) continue;
    if (L2_PROC_RE.test(t)) {
      return `${head} ${t} reads a live process entry - the lane's own environment (` +
             "server password, gateway keys) is readable there";
    }
  }
  return null;
}

// Sonnet round-4 REJECT finding 5: the secret list was five patterns long and
// case-sensitive, so `.NETRC`, `id_ed25519`, `.aws/credentials`, `auth.json`,
// `.git-credentials`, `.npmrc`, `.pypirc`, `secrets.yaml`, `x.p12` and
// `srv.PEM` were all readable. Every name below is matched case-insensitively;
// `.ssh` and `.aws` match as a PATH SEGMENT, because what an L2 must not open is
// the directory's contents under any file name.
const L2_SECRET_PREFIXES = [".env", "id_rsa", "id_ed25519", "id_ecdsa"];
const L2_SECRET_SUFFIXES = [".key", ".pem", ".p12", ".pfx"];
const L2_SECRET_NAMES = new Set([
  "api-keys.yml", "credentials", "auth.json", ".git-credentials", ".npmrc",
  ".pypirc", ".netrc",
]);
const L2_SECRET_DIRS = new Set([".ssh", ".aws"]);

function l2SecretOperandReason(head, t) {
  // Repo-relative is not the same as safe: the secrets a lane must never read
  // sit inside the repo, git-ignored, exactly where the work happens.
  const lower = t.toLowerCase();
  const base = baseName(lower);
  const secret = L2_SECRET_PREFIXES.some((p) => base.startsWith(p))
    || L2_SECRET_SUFFIXES.some((s) => base.endsWith(s))
    || L2_SECRET_NAMES.has(base)
    || /^secrets\.ya?ml$/.test(base)
    || (base.includes("credentials") && base.endsWith(".json"))
    || lower.split("/").some((seg) => L2_SECRET_DIRS.has(seg));
  if (!secret) return null;
  return `${head} ${t} reads a secret file - an L2 needs none of the lane's credentials; ` +
         "only the .example templates are safe to open";
}

function l2PathOutsideRepoReason(head, t) {
  // An allow rule, not a spelling list: naming /proc/self/environ left
  // /proc/./self/environ, //proc//self//environ, /proc/$$/environ, a globbed
  // /proc, ~/.claude.json and ~/.config/opencode/auth.json all readable. A path
  // an L2 may read is one the shell cannot move outside the checkout: relative,
  // no expansion (a `$`, a backtick, a glob, a leading `~`), no `..` segment and
  // no `//` (which makes a doubled separator read as a single one).
  // Consequence: the absolute path is denied whatever it points at, and a glob
  // character is denied even in a search pattern - fail closed both times.
  const why = t.startsWith("/") ? "it is absolute"
    : t.startsWith("~") ? "a leading ~ expands outside the checkout"
    : t.includes("//") ? "a doubled separator hides a segment from the check"
    : t.split("/").includes("..") ? "a .. segment leaves the checkout"
    : /[$`]/.test(t) ? "the shell expands it"
    : /[*?[\]{}]/.test(t) ? "a glob expands it"
    : null;
  if (!why) return null;
  return `${head} ${t}: path outside repo - ${why}; an L2 reads repo-relative ` +
         "paths inside its own lane checkout only";
}

function l2OperandCandidates(t) {
  // Sonnet round-4 findings 1 and 4: the path a token carries is not always the
  // whole token. `HEAD:.env` is git's rev:path spec, `dir/.env/x` hides the file
  // in a middle segment, and the flattened line continuation handed the checks a
  // word whose path began after a space. Each spelling is checked, so an escape
  // has to be found by every rule at once rather than by the one that reads the
  // token as written.
  const out = [];
  const add = (part) => {
    if (typeof part !== "string") return;
    const v = part.trim();
    if (v && !out.includes(v)) out.push(v);
  };
  add(t);
  for (const piece of t.split(":")) add(piece);
  for (const piece of t.split("/")) add(piece);
  return out;
}

let l2CheckoutRootCache = null;

function l2CheckoutRoot() {
  // The lane's working directory IS its checkout - an L2 reads repo-relative
  // paths inside it and nothing else. realpath, so a checkout reached through a
  // symlinked mount is compared on the same footing as its operands. Resolved
  // once: a `git rev-parse --show-toplevel` would put a subprocess, whose own
  // read of the repository the guard cannot audit, on the security path.
  if (l2CheckoutRootCache === null) {
    let root = path.resolve(process.cwd());
    try {
      root = fs.realpathSync(root);
    } catch (_) {
      root = path.resolve(process.cwd());
    }
    l2CheckoutRootCache = root;
  }
  return l2CheckoutRootCache;
}

function l2SymlinkOperandReason(head, t) {
  // Sonnet round-4 finding 6: a repo-relative NAME is not a repo-relative FILE.
  // `escape -> /etc/shadow` sits inside the checkout, passes every spelling rule
  // above, and reads the shadow file. Anything the resolver cannot answer is
  // refused too - a loop or a denied directory is not a pass.
  let abs;
  try {
    abs = path.resolve(process.cwd(), t);
  } catch (_) {
    return `${head} ${t}: the guard cannot resolve the operand and fails closed`;
  }
  let real;
  try {
    real = fs.realpathSync(abs);
  } catch (err) {
    const code = err && err.code;
    if (code === "ENOENT" || code === "ENOTDIR") return null;
    return `${head} ${t}: the guard cannot resolve the operand (${code || "error"}) ` +
           "and fails closed";
  }
  const root = l2CheckoutRoot();
  if (root === "/") {
    // A lane whose working directory is the filesystem root has no checkout to
    // stay inside, so there is nothing here to prove the read is scoped.
    return `${head} ${t}: ${real} cannot be proved inside the lane checkout - the working ` +
           "directory is the filesystem root; a symlink may not carry a read the list " +
           "never approved";
  }
  const prefix = root.endsWith("/") ? root : root + "/";
  if (real === root || real.startsWith(prefix)) return null;
  return `${head} ${t}: ${real} is outside the lane checkout - a symlink may not carry ` +
         "a read the list never approved";
}

function l2PathOperandReason(head, t, resolve) {
  const candidates = l2OperandCandidates(t);
  for (const cand of candidates) {
    const outside = l2PathOutsideRepoReason(head, cand);
    if (outside) return outside;
  }
  for (const cand of candidates) {
    const secret = l2SecretOperandReason(head, cand);
    if (secret) return secret;
  }
  // An option's value is not the file the command opens under that name, so the
  // resolver runs on operands only.
  if (resolve === false) return null;
  return l2SymlinkOperandReason(head, t);
}

function l2OptionOperandReason(head, t, next) {
  // Sonnet round-4 finding 3: the operand loop skipped every token that began
  // with "-", so the path rode in as an OPTION VALUE - `rg --file=/etc/passwd`,
  // `rg --ignore-file=/etc/shadow`, `rg -f/etc/passwd`. A `=` value is
  // path-checked whatever option it hangs off; a value-taking option that names
  // a file for this head is refused even when its value reads as harmless.
  let name = null;
  let value = null;
  let shortCh = null;
  let glued = null;
  if (t.startsWith("--")) {
    const eq = t.indexOf("=");
    name = eq === -1 ? t.slice(2) : t.slice(2, eq);
    value = eq === -1 ? null : t.slice(eq + 1);
  } else {
    shortCh = t.length > 1 ? t[1] : null;
    if (t[2] === "=") {
      value = t.slice(3);
    } else if (t.length > 2) {
      glued = t.slice(2);
    }
  }
  const takesPath = head === "rg"
    && ((name !== null && L2_RG_PATH_OPTS.has(name))
        || (shortCh !== null && L2_RG_PATH_SHORTS.has(shortCh)));
  if (takesPath) {
    const v = value !== null ? value : (glued !== null ? glued : (next === undefined ? null : next));
    if (v !== null && !v.startsWith("-")) {
      const reason = l2PathOperandReason(head, v, false);
      if (reason) return reason;
    }
    return `${head} ${t} reads a path the guard cannot scope; an L2 may not hand a ` +
           "read-only command an option whose job is to open a file";
  }
  if (value !== null && !value.startsWith("-")) {
    return l2PathOperandReason(head, value, false);
  }
  return null;
}

function l2OperandReason(head, args) {
  const procReason = l2ProcDenial(args, head);
  if (procReason) return procReason;
  for (let k = 0; k < args.length; k++) {
    const t = args[k];
    if (t === "-" || t === "--") continue;
    if (t.startsWith("-")) {
      const optReason = l2OptionOperandReason(head, t, args[k + 1]);
      if (optReason) return optReason;
      continue;
    }
    const reason = l2PathOperandReason(head, t, true);
    if (reason) return reason;
  }
  return null;
}

function stdinOrSubstitutionReason(command) {
  // One pass outside quotes for the operators a head list cannot approve:
  // `<` (any stdin redirection), a backtick, `$(`, and `>(`.
  const n = command.length;
  let i = 0;
  while (i < n) {
    const c = command[i];
    if (c === "'") {
      const j = command.indexOf("'", i + 1);
      i = j === -1 ? n : j + 1;
      continue;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < n) {
        if (command[j] === "\\" && j + 1 < n) { j += 2; continue; }
        if (command[j] === '"') break;
        j += 1;
      }
      i = j < n ? j + 1 : n;
      continue;
    }
    if (c === "\\" && i + 1 < n) { i += 2; continue; }
    if (c === "#" && (i === 0 || /[\s;|&(]/.test(command[i - 1]))) {
      const j = command.indexOf("\n", i);
      i = j === -1 ? n : j;
      continue;
    }
    if (c === "<") {
      const doubled = i + 1 < n && command[i + 1] === "<";
      return doubled
        ? "a here-document or here-string feeds the command data the list never approved"
        : "a stdin redirection reads the command from a file the list never approved";
    }
    if (c === "`") {
      return "a backtick command substitution runs a command the list never approved";
    }
    if (c === "$" && i + 1 < n && command[i + 1] === "(") {
      return "a $(...) command substitution runs a command the list never approved";
    }
    if (c === ">" && i + 1 < n && command[i + 1] === "(") {
      return "a process substitution runs a command the list never approved";
    }
    i += 1;
  }
  return null;
}

function l2ReadOnlyDenialReason(command) {
  // Sonnet round-4 REJECT finding 1 (BLOCKER): `\<newline>` is a line
  // continuation to bash, but collectSegments flattens the newline to a space and
  // leaves the backslash, so tokenizeSegment read `\ ` as an escaped space glued
  // to the next word - `cat \<NL>/etc/passwd` reached the leading-"/" test as the
  // word " /etc/passwd" and was allowed. A backslash also hides a separator, a
  // quote or a glob from the segment split, and the flattened form is what gets
  // checked, not the bytes the shell runs. An L2 has no use for one: every
  // backslash is refused, fail closed (the leading-space half of the hole is
  // closed separately, in l2OperandCandidates).
  if (command.includes("\\")) {
    return "a backslash in the command is a continuation, an escape or a quoting " +
           "trick; the guard reads a flattened form, so an L2 may not use one";
  }
  for (const target of redirectWriteTargets(command)) {
    return `output redirect to ${target} writes a file; an L2 may not write anywhere`;
  }
  const stdinReason = stdinOrSubstitutionReason(command);
  if (stdinReason) return stdinReason;
  for (const seg of collectSegments(command)) {
    const words = tokenizeSegment(seg);
    const idx = skipLeading(words, 0);
    // The wrapper skip leaves a bare `env` with no head to audit, and that head
    // is a dump of the lane's own environment: check the tokens `skipLeading`
    // walked past, and the head it stopped on.
    for (let k = 0; k < words.length && k <= idx; k++) {
      if (L2_ENV_DUMP_HEADS.has(baseName(words[k]))) {
        return `the ${words[k]} command reads or sets the lane's own environment (` +
               "server password, gateway keys); an L2 may not dump it";
      }
    }
    // A VAR=value prefix is the payload, not a wrapper: GIT_PAGER, GIT_DIR and
    // GIT_CONFIG_ENV reach git's config knobs with no git flag at all, so an
    // assignment that `skipLeading` walked past must never become a pass.
    for (let k = 0; k < idx; k++) {
      if (ORCH_ENV_ASSIGN.test(words[k])) {
        return `the environment assignment ${words[k]} sets what the read-only command runs; an L2 may not configure its own tools`;
      }
    }
    // Sonnet round-4 REJECT finding 7: a wrapper is not a pass-through here. The
    // skip exists for the orchestrator role, whose rule is a write TARGET; this
    // role's rule is a closed list of HEADS, and `sudo cat` is a second user's
    // `cat`. Audited up to and including the head, so a bare `sudo` (which the
    // skip leaves with no head at all) is refused too.
    for (let k = 0; k < words.length && k <= idx; k++) {
      const wrapped = baseName(words[k]);
      if (L2_WRAPPER_HEADS.has(wrapped)) {
        return `${wrapped} is a wrapper around the read - an L2 runs the read-only ` +
               `heads bare (${L2_LIST_TEXT}); not: ${seg}`;
      }
    }
    if (idx >= words.length) continue;
    // Sonnet round-4 REJECT finding 2: baseName() proved only that the LAST
    // word looked like `cat`. /tmp/evil/cat and ./cat are whatever the lane
    // wrote there, so the head has to be the bare name, spelled with no
    // separator at all.
    const rawHead = words[idx];
    if (rawHead.includes("/")) {
      return `${rawHead} is not a bare command name; an L2 runs the read-only heads by ` +
             `name (${L2_LIST_TEXT}), never a path to a binary`;
    }
    const head = baseName(rawHead);
    if (head === "git") {
      const args = words.slice(idx + 1);
      const g = gitSubcommand(args);
      // The global options live BEFORE the subcommand and git acts on them
      // however read-only the verb looks: `-c` writes config the verb then
      // executes (core.fsmonitor, core.pager, credential.helper), `-C` and
      // friends choose another repository or git binary.
      const globalReason = l2OptionDenial(g.opts, L2_GIT_GLOBAL_DENY, "git", { abbreviate: true });
      if (globalReason) return globalReason;
      if (g.sub === null || g.sub === undefined) {
        return "git without a subcommand may run any verb";
      }
      if (!L2_GIT_SUBCOMMANDS.has(g.sub)) {
        return `git ${g.sub} is not on the read-only list (${L2_LIST_TEXT})`;
      }
      // `git log --output=f` is a file write wearing a read-only verb.
      const out = g.rest.find((a) => a === "--output" || a.startsWith("--output="));
      if (out) return `git ${g.sub} ${out} writes a file; an L2 may not write anywhere`;
      const restReason = l2OptionDenial(g.rest, L2_GIT_REST_DENY, `git ${g.sub}`, { abbreviate: true });
      if (restReason) return restReason;
      // A pathspec is a path even when it wears a revision's spelling.
      const gOperand = l2OperandReason("git", g.rest);
      if (gOperand) return gOperand;
      continue;
    }
    if (!L2_READ_HEADS.has(head)) {
      return `${head} is not on the read-only list (${L2_LIST_TEXT})`;
    }
    const args = words.slice(idx + 1);
    if (head === "rg") {
      const rgReason = l2OptionDenial(args, L2_RG_DENY, "rg");
      if (rgReason) return rgReason;
      const ignoreReason = l2OptionDenial(args, L2_RG_IGNORE_DENY, "rg", {
        prefixes: L2_RG_IGNORE_DENY_PREFIXES,
        why: L2_RG_IGNORE_WHY,
      });
      if (ignoreReason) return ignoreReason;
    }
    const operandReason = l2OperandReason(head, args);
    if (operandReason) return operandReason;
  }
  return null;
}

export default {
  id: "bash-guard",
  async setup(ctx) {
    await ctx.tool.hook("execute.before", async (e) => {
      // The canary recognises a denial only on the names guarded here; adding
      // or dropping one must be mirrored in tools/oc_l1_canary.py
      // (GUARDED_TOOL_NAMES). tests/test_oc_l1_canary.py pins the two sets.
      if (!e || (e.tool !== "shell" && e.tool !== "bash")) {
        return;
      }

      const command = (e.input && typeof e.input.command === "string")
        ? e.input.command
        : "";

      const role = process.env.AUTOOS_GUARD_ROLE;
      if (role === "orchestrator" || role === "l2") {
        // The canary recognises a denial only by the "bash-guard: DENIED"
        // marker, and this throw runs before the python guard, so the role
        // denial must carry that marker verbatim (D-665).
        const reason = role === "l2" ? l2ReadOnlyDenialReason(command)
                                     : orchestratorDenialReason(command);
        if (reason) {
          const label = role === "l2" ? "l2 read-only" : "orchestrator role";
          throw new Error(`bash-guard: DENIED - ${label}: ${reason}`);
        }
      }

      const repoRoot = getRepoRoot();
      const guardPath = path.join(repoRoot, "tools", "hooks", "bash_guard.py");

      if (!fs.existsSync(guardPath)) {
        process.stderr.write(`bash-guard: guard script missing at ${guardPath}, allowing\n`);
        return;
      }

      const payload = JSON.stringify({
        tool_name: "Bash",
        tool_input: {
          command,
        },
      });

      let res = await runProcess("python3", guardPath, payload, 5000);
      if (res.spawnError) {
        res = await runProcess("python", guardPath, payload, 5000);
      }

      if (res.timedOut) {
        process.stderr.write("bash-guard: timeout (5s) exceeded, allowing\n");
        return;
      }

      if (res.spawnError) {
        process.stderr.write(`bash-guard: spawn error (${res.spawnError.message}), allowing\n`);
        return;
      }

      if (res.code === 2) {
        const msg = (res.stderr || "").trim();
        throw new Error(msg);
      }

      if (res.code === 0) {
        return;
      }

      process.stderr.write(`bash-guard: guard exited with code ${res.code}, allowing\n`);
    });
  },
};
