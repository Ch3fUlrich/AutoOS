#!/usr/bin/env python3
"""v-activate-run-probes.py — run both probes against ONE isolated gateway.

Both probes are launched concurrently (same instance, same wall-clock window) and
their full stdout/stderr is saved. The gateway URL comes from AUTOOS_OMNIROUTE_URL.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(os.environ["TEMP"], "opencode", "v-activate-run")
GATEWAY = os.environ.get("AUTOOS_OMNIROUTE_URL", "http://127.0.0.1:20145")

PROBES = [
    ("reasoning", os.path.join(ROOT, "tools", "probe-reasoning-repro.py")),
    ("vertex", os.path.join(ROOT, "tools", "probe-vertex.py")),
]


def main():
    os.makedirs(RUN, exist_ok=True)
    env = dict(os.environ)
    env["AUTOOS_OMNIROUTE_URL"] = GATEWAY
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"gateway={GATEWAY}")
    print(f"run dir={RUN}")
    procs = {}
    for name, path in PROBES:
        out = open(os.path.join(RUN, f"{name}.out.txt"), "wb")
        err = open(os.path.join(RUN, f"{name}.err.txt"), "wb")
        start = time.strftime("%H:%M:%S", time.localtime())
        procs[name] = (path, subprocess.Popen([sys.executable, path], cwd=RUN, env=env,
                                              stdout=out, stderr=err), out, err, start)
        print(f"started {name}: {path} at {start}")
    codes = {}
    for name, (path, p, out, err, start) in procs.items():
        rc = p.wait()
        out.close()
        err.close()
        codes[name] = rc
        print(f"finished {name}: rc={rc} at {time.strftime('%H:%M:%S', time.localtime())} "
              f"(started {start})")
    for name, _ in PROBES:
        print(f"\n{'='*30} {name}.out.txt {'='*30}")
        with open(os.path.join(RUN, f"{name}.out.txt"), encoding="utf-8", errors="replace") as fh:
            print(fh.read())
        with open(os.path.join(RUN, f"{name}.err.txt"), encoding="utf-8", errors="replace") as fh:
            tail = fh.read().strip()
        if tail:
            print(f"--- {name}.err.txt ---")
            print(tail[-2000:])
    print(f"\nreturn codes: {codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
