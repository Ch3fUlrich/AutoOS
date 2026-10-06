[0m
> build · muse-spark-1.3-contributor-free
1. Unhandled timeout crash blocks watcher: `text=True, cwd=REPO, stdin=subprocess.DEVNULL, timeout=400)` raises TimeoutExpired out of start_lane with no try/except.
2. Non-atomic lane config rewrite can corrupt on crash: `json.dump(d, open(p, "w"), indent=2)` truncates oc-l1.json in place unlike heartbeat tmp+replace.
3. Fallback inherits primary limit, 1M primary mis-renders 128k fallback at 1M: `"limit": dict(limit),` used for both mid and fid legs.
4. Unvalidated limit shape passes through to client: `limit = m.get("limit") if isinstance(m.get("limit"), dict) else MODEL_LIMIT` renders any dict without context/output check.
5. External_directory flat allow map mismatches opencode permission schema: `cfg["permission"]["external_directory"] = {p: "allow" for p in ext}` needs schema doc link/test against real config.json.
6. Start backoff stalls whole watcher cycle and stale heartbeat: `time.sleep(60)` blocks refresh_heartbeat/gate handling for up to 120s inside start_lane.
7. Silent silent/dead flap on None newest: `data["state"] = "dead" if not alive else ("silent" if newest and now - newest > 600 else "live")` marks never-talked live session live forever.
VERDICT: REQUEST_CHANGES: fix timeout handling, atomic config write, and fallback limit split.
