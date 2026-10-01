#!/usr/bin/env python3
"""v-activate-vertex-retry.py — drive the VERBATIM probe-vertex.py until every shape
has been observed 200 at least once, or the wall-clock budget runs out.

Provider quota/cooldown 429s are transient and are NOT the bug under test (the bug is
the 400 "Requests ending with a model turn are not supported"). This wrapper:
  * runs tools/probe-vertex.py unchanged (it writes probe-vertex-results.json),
  * merges the per-(model,test) statuses across attempts,
  * backs off 75 s whenever an attempt ends with 429s,
  * stops early when all 8 shapes have been seen 200,
  * reports any 400 (a real failure) immediately.

Everything is appended to a transcript so the raw evidence survives.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(os.environ["TEMP"], "opencode", "v-activate-run")
PROBE = os.path.join(ROOT, "tools", "probe-vertex.py")
GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20145")
BUDGET_S = int(os.environ.get("V_ACTIVATE_VERTEX_BUDGET_S", "1500"))
BACKOFF_S = 75


def main():
    os.makedirs(RUN, exist_ok=True)
    env = dict(os.environ)
    env["AUTOOS_OMNIROUTE_URL"] = GATEWAY
    env["PYTHONIOENCODING"] = "utf-8"
    seen = {}
    attempts = []
    t0 = time.time()
    attempt = 0
    log = []
    while time.time() - t0 < BUDGET_S:
        attempt += 1
        started = time.strftime("%H:%M:%S", time.localtime())
        proc = subprocess.run([sys.executable, PROBE], cwd=RUN, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        with open(os.path.join(RUN, f"vertex.attempt{attempt}.out.txt"), "w", encoding="utf-8") as fh:
            fh.write(proc.stdout or "")
        results = []
        try:
            with open(os.path.join(RUN, "probe-vertex-results.json"), encoding="utf-8") as fh:
                results = json.load(fh)
        except Exception as ex:  # noqa: BLE001
            log.append(f"attempt {attempt}: could not read results json: {ex}")
        statuses = []
        for r in results:
            key = (r["model"], r["test"])
            seen.setdefault(key, set()).add(r["status"])
            statuses.append(r["status"])
        log.append(f"attempt {attempt}: started={started} rc={proc.returncode} "
                   f"statuses={statuses}")
        attempts.append({"attempt": attempt, "started": started, "statuses": statuses})

        bad = [r for r in results if r["status"] == 400]
        if bad:
            log.append("FAIL: a real 400 was returned:")
            for r in bad:
                log.append(f"  {r['model']} | {r['test']} -> {r['body'][:300]}")
            break

        all200 = all(200 in v for v in seen.values()) and len(seen) == 8
        if all200:
            log.append(f"all 8 shapes have been observed 200 (after attempt {attempt})")
            break
        log.append(f"  seen so far: " + ", ".join(
            f"{m}|{t.split(':')[0]}={sorted(s)}" for (m, t), s in sorted(seen.items())))
        time.sleep(BACKOFF_S)

    print("\n".join(log))
    print("\nFINAL MATRIX (statuses observed per shape across all attempts):")
    ok = True
    for (model, test), st in sorted(seen.items()):
        mark = "OK" if 200 in st else "NOT-YET-200"
        if 200 not in st:
            ok = False
        print(f"  {model:26s} {test:52s} {sorted(st)}  {mark}")
    print(f"\nall 8 shapes observed 200: {ok}")
    with open(os.path.join(RUN, "vertex.retry.log"), "a", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n" + json.dumps(attempts, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
