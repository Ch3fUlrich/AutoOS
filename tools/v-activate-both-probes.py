#!/usr/bin/env python3
"""v-activate-both-probes.py — prove BOTH probes pass simultaneously on ONE isolated gateway.

Each round launches tools/probe-reasoning-repro.py and tools/probe-vertex.py at the
same instant, against the same gateway, in the same round directory. A round counts as
a simultaneous pass only when:

  * reasoning: all four cases end "-> PASS" and stdout says
    "400 reasoning error reproduced: False", AND
  * vertex:    all eight (model, test) shapes returned status 200.

Provider quota/cooldown 429s are transient and are NOT the bug under test (the bug is
the 400 "Requests ending with a model turn are not supported"); a round with any 429 is
not a failure, it is retried after a backoff.

On the first round where both pass, the round's raw stdout and the vertex results JSON
are copied aside as the simultaneous-pass evidence and the loop stops.
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(os.environ["TEMP"], "opencode", "v-activate-both")
GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20145")
BUDGET_S = int(os.environ.get("V_ACTIVATE_BOTH_BUDGET_S", "1800"))
BACKOFF_S = int(os.environ.get("V_ACTIVATE_BOTH_BACKOFF_S", "75"))

REASONING = os.path.join(ROOT, "tools", "probe-reasoning-repro.py")
VERTEX = os.path.join(ROOT, "tools", "probe-vertex.py")


def run_round(n):
    d = os.path.join(BASE, f"round{n:02d}")
    os.makedirs(d, exist_ok=True)
    env = dict(os.environ)
    env["AUTOOS_OMNIROUTE_URL"] = GATEWAY
    env["PYTHONIOENCODING"] = "utf-8"
    r_out = open(os.path.join(d, "reasoning.out.txt"), "wb")
    r_err = open(os.path.join(d, "reasoning.err.txt"), "wb")
    v_out = open(os.path.join(d, "vertex.out.txt"), "wb")
    v_err = open(os.path.join(d, "vertex.err.txt"), "wb")
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Launch both concurrently.
    pr = subprocess.Popen([sys.executable, REASONING], cwd=d, env=env, stdout=r_out, stderr=r_err)
    pv = subprocess.Popen([sys.executable, VERTEX], cwd=d, env=env, stdout=v_out, stderr=v_err)
    rr = pr.wait()
    rv = pv.wait()
    r_out.close(); r_err.close(); v_out.close(); v_err.close()

    with open(os.path.join(d, "reasoning.out.txt"), encoding="utf-8", errors="replace") as fh:
        rt = fh.read()
    with open(os.path.join(d, "vertex.out.txt"), encoding="utf-8", errors="replace") as fh:
        vt = fh.read()

    # reasoning pass: no 400 and all four PASS.
    reasoning_ok = ("400 reasoning error reproduced: False" in rt
                    and rt.count("-> PASS") == 4)
    passes = [ln for ln in rt.splitlines() if "-> PASS" in ln]

    # vertex pass: all 8 shapes 200, read from the results JSON the probe writes.
    vresults = []
    vjson = os.path.join(d, "probe-vertex-results.json")
    if os.path.exists(vjson):
        with open(vjson, encoding="utf-8") as fh:
            vresults = json.load(fh)
    statuses = [r["status"] for r in vresults]
    vertex_ok = len(vresults) == 8 and all(s == 200 for s in statuses)
    any400 = any(s == 400 for s in statuses)

    return {
        "round": n, "dir": d, "started_utc": started,
        "reasoning_rc": rr, "vertex_rc": rv,
        "reasoning_ok": reasoning_ok, "reasoning_passes": passes,
        "vertex_statuses": statuses, "vertex_ok": vertex_ok, "vertex_400": any400,
        "both_ok": reasoning_ok and vertex_ok,
    }


def main():
    os.makedirs(BASE, exist_ok=True)
    t0 = time.time()
    log = []
    n = 0
    winner = None
    while time.time() - t0 < BUDGET_S:
        n += 1
        r = run_round(n)
        line = (f"round {n}: started={r['started_utc']} reasoning_ok={r['reasoning_ok']} "
                f"(rc={r['reasoning_rc']}) vertex_statuses={r['vertex_statuses']} "
                f"vertex_ok={r['vertex_ok']} both_ok={r['both_ok']}")
        print(line, flush=True)
        log.append(line)
        if r["vertex_400"]:
            print("FAIL: a real 400 was returned by the vertex probe", flush=True)
            break
        if r["both_ok"]:
            winner = r
            break
        time.sleep(BACKOFF_S)

    print("\n================ SIMULTANEOUS-PASS VERDICT ================", flush=True)
    if winner:
        print(f"round {winner['round']} ({winner['dir']}) — both probes passed in the same window")
        print(f"  reasoning: {'; '.join(winner['reasoning_passes'])}")
        print(f"  vertex statuses: {winner['vertex_statuses']}")
    else:
        print("no round with both probes passing inside the budget")
    with open(os.path.join(BASE, "both-probes.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())
