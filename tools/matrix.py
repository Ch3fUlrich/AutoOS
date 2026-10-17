import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(os.path.dirname(HERE), "logs", "probe-free-20260930.jsonl")

recs = []
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        o = json.loads(line)
    except ValueError:
        continue
    if "leg" in o:
        recs.append(o)

order = [r["leg"] for r in recs]
print("count", len(recs))
for r in recs:
    ack = "200" if r.get("ack_status") == 200 else str(r.get("ack_status"))
    tool = r.get("tool")
    note = r.get("tool_note") if tool != "ok" else ""
    if tool == "ok":
        note = "ok"
    print("| `%s` | %s | %s | %s | %s | %s | %s | %s |" % (
        r["leg"], ack, r.get("ack_ms"), r.get("ack"), tool, r.get("tool_ms"),
        r.get("tool_calls"), (note or "").replace("|", "\\|")[:110]))
