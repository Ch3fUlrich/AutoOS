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

function checkGit(args) {
  let i = 0;
  const n = args.length;
  while (i < n) {
    const a = args[i];
    if (a === "--") break;
    if (a.startsWith("-")) {
      const bare = a.split("=")[0];
      if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--exec-path"].includes(bare)) {
        i += 2;
      } else {
        i += 1;
      }
      continue;
    }
    break;
  }
  if (i >= n) return null;
  const sub = args[i];
  const rest = args.slice(i + 1);
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

      if (process.env.AUTOOS_GUARD_ROLE === "orchestrator") {
        const reason = orchestratorDenialReason(command);
        if (reason) {
          // The canary recognises a denial only by the "bash-guard: DENIED"
          // marker, and this throw runs before the python guard, so the role
          // denial must carry that marker verbatim (D-665).
          throw new Error(`bash-guard: DENIED - orchestrator role: ${reason}`);
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
