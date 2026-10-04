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

export default {
  id: "bash-guard",
  async setup(ctx) {
    await ctx.tool.hook("execute.before", async (e) => {
      if (!e || (e.tool !== "shell" && e.tool !== "bash")) {
        return;
      }

      const command = (e.input && typeof e.input.command === "string")
        ? e.input.command
        : "";

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
