#!/usr/bin/env python3
"""v-activate-clamp-evidence.py — byte-exact accounting of the scaleway-qwen clamp edit
in the two built chunks, streamed from the tarball in memory (no Temp extraction).

For each chunk it reports:
  * tarball/live sizes and the raw delta,
  * CRLF counts in each,
  * the CRLF-normalised delta (removing \r) — i.e. the substantive change,
  * the inserted run (common prefix/suffix on the CRLF-normalised bytes).
"""
import os
import sys
import tarfile

TARBALL = r"C:\Users\mauls\AppData\Local\Temp\opencode\packbackups\omniroute-3.8.50.tgz"
LIVE = r"C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute"
CHUNKS = [
    "dist/.build/next/server/chunks/_0o8_5h8._.js",
    "dist/.build/next/server/chunks/_0t1t5fj._.js",
]


def main():
    out = []
    for rel in CHUNKS:
        with tarfile.open(TARBALL, "r:gz") as tf:
            m = tf.getmember("package/" + rel)
            tar = tf.extractfile(m).read()
        with open(os.path.join(LIVE, rel.replace("/", os.sep)), "rb") as fh:
            live = fh.read()
        tar_crlf, live_crlf = tar.count(b"\r\n"), live.count(b"\r\n")
        tar_n = tar.replace(b"\r\n", b"\n")
        live_n = live.replace(b"\r\n", b"\n")
        # inserted run on the LF-normalised bytes
        p = 0
        while p < min(len(tar_n), len(live_n)) and tar_n[p] == live_n[p]:
            p += 1
        s = 0
        while (s < min(len(tar_n), len(live_n)) - p
               and tar_n[len(tar_n) - 1 - s] == live_n[len(live_n) - 1 - s]):
            s += 1
        ins = live_n[p:len(live_n) - s] if s else live_n[p:]
        del_seg = tar_n[p:len(tar_n) - s] if s else tar_n[p:]
        out.append(f"=== {rel} ===")
        out.append(f"  tarball bytes={len(tar)}  live bytes={len(live)}  raw delta={len(live)-len(tar)}")
        out.append(f"  CRLF pairs: tarball={tar_crlf}  live={live_crlf}  diff={live_crlf-tar_crlf}")
        out.append(f"  after CRLF-normalise: tarball={len(tar_n)}  live={len(live_n)}  delta={len(live_n)-len(tar_n)}")
        out.append(f"  inserted segment ({len(ins)} B): {ins[:400]!r}")
        out.append(f"  deleted segment ({len(del_seg)} B): {del_seg[:400]!r}")
        out.append("")
    text = "\n".join(out)
    print(text)
    dest = os.path.join(os.environ["TEMP"], "opencode", "v-activate", "clamp-evidence.txt")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"wrote {dest}")


if __name__ == "__main__":
    sys.exit(main())
