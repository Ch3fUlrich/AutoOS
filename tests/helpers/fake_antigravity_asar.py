#!/usr/bin/env python3
"""Write a minimal Electron asar archive for the Antigravity installer tests.

usage: fake_antigravity_asar.py <out> <version> [noicon|nestedicon]

The archive holds package.json (name antigravity, the given version) and, unless
"noicon" is given, icon.png (the PNG signature plus filler) - at the root of the
archive, or under resources/ with "nestedicon". The layout is the
one asar documents and the installer reads:

    <uint32 4> <uint32 header pickle size> <uint32 payload size> <uint32 json length>
    <json header, padded to 4 bytes> <file data>

with every file's "offset" counted from 8 + header pickle size. Nothing here
touches the network or the machine: it only writes <out>.
"""
import json
import struct
import sys


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, version = argv[1], argv[2]
    options = argv[3:]
    package = json.dumps({"name": "antigravity", "productName": "Antigravity", "version": version}).encode()
    files = [("package.json", package)]
    if "noicon" not in options:
        where = "resources/icon.png" if "nestedicon" in options else "icon.png"
        files.append((where, b"\x89PNG\r\n\x1a\n" + b"fake icon\n"))
    header, blob, offset = {"files": {}}, b"", 0
    for path, data in files:
        node, parts = header, path.split("/")
        for part in parts[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        node["files"][parts[-1]] = {"size": len(data), "offset": str(offset)}
        blob += data
        offset += len(data)
    text = json.dumps(header, separators=(",", ":")).encode()
    padded = text + b"\0" * (-len(text) % 4)
    pickle_size = 8 + len(padded)          # payload size field + string length field + padded json
    with open(out, "wb") as f:
        f.write(struct.pack("<4I", 4, pickle_size, pickle_size - 4, len(text)))
        f.write(padded)
        f.write(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
