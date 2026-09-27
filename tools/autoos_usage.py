#!/usr/bin/env python3
"""Usage report from the OmniRoute gateway: calls, ok/errors and tokens by
provider, combo, lane and model. The operator-facing name is the subcommand:

    autoos-agent.py usage --since 1h [--by provider,combo,lane,model] [--json]

It pages GET /api/usage/call-logs with the manage-scoped key the ai-stack
installer left on the host, aggregates the rows client-side and prints one
table per --by dimension (sorted by calls, descending), or JSON with --json.

Response shape it is built against (OmniRoute release/v3.8.51, verified at the
tag - build to this, not to a guess):

- GET /api/usage/call-logs (src/app/api/usage/call-logs/route.ts:228) returns
  NextResponse.json(filtered) (route.ts:280): a JSON ARRAY of call-log rows.
- Query params the route reads (route.ts:236-248): status, model, provider,
  account, apiKey, combo, search, correlationId, limit, offset,
  excludeTests=1. There is NO `since` param (getCallLogs supports
  filter.since at src/lib/usage/callLogs.ts:988-991 but the route never parses
  it), so this tool pages by offset until a page's oldest row is older than
  --since.
- Sort order: in-memory active/completed entries first, then newest-first by
  timestamp (route.ts:218-225); persisted rows come from SQL
  `ORDER BY cl.timestamp DESC LIMIT @__limit OFFSET @__offset`
  (callLogs.ts:1016, default limit 200 at callLogs.ts:1014). The offset
  applies to persisted rows only, which is why this tool advances offset by
  the page limit and dedupes rows by `id` (active rows re-appear on every
  page).
- Row fields (mapSummaryRow, callLogs.ts:462-507), camelCase: id, timestamp
  (ISO string), method, path, status (number), model, requestedModel,
  provider, providerDisplay, account, connectionId, duration, tokens
  (NESTED object {in, out, cacheRead, cacheWrite, reasoning, compressed},
  callLogs.ts:475-482), apiKeyId, apiKeyName, comboName, comboStepId,
  comboExecutionKey, error (string|null), correlationId, sessionTag
  (callLogs.ts:505 - OR3 sets it to "<orchestrator-worktree>/<lane>").
- Error predicate (route.ts:49-51): Number(status) >= 400 OR truthy error
  field. Ok (route.ts:52-53): 200 <= status < 300. This report counts a row
  as error if the error predicate holds, else ok if the ok predicate holds,
  else neither (e.g. an in-flight row with status 0).
- Auth (route.ts:230 -> src/lib/api/requireManagementAuth.ts): a Bearer
  manage-scoped key; 401 with no credential, 403 when the key lacks the
  'manage' scope. The host keeps that key in
  ${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}/manage.key
  (read the same way as configuration/omniroute/apply.sh).

Grouping: lane is the row's sessionTag, "(untagged)" when empty; combo is
comboName, "(none)" when empty; provider/model "(unknown)" when empty. Rows
with a timestamp older than --since are dropped; rows without a parseable
timestamp are kept (they are almost always in-flight rows).

Exit codes: 0 ok; 2 bad input (--since/--by); 3 gateway unreachable, HTTP
error (401/403 named as auth failures), a non-array response, or the key file
missing/empty. The failure line names the cause and never the key.

HTTP is urllib (stdlib) only; tests inject `fetch`, never the live gateway.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_GATEWAY = "http://127.0.0.1:20128"
PAGE_LIMIT = 500
MAX_PAGES = 20
TIMEOUT_S = 15
DIMENSIONS = ("provider", "combo", "lane", "model")
UNTAGGED_LANE = "(untagged)"


class UsageError(Exception):
    """A one-line, key-free failure cause (exit 3)."""


def parse_since(text, now):
    """ISO-8601 (UTC assumed when naive) or relative Nm/Nh/Nd -> aware UTC datetime."""
    s = str(text).strip()
    m = re.fullmatch(r"(\d+)([mhd])", s.lower())
    if m:
        n = int(m.group(1))
        delta = {"m": datetime.timedelta(minutes=n),
                 "h": datetime.timedelta(hours=n),
                 "d": datetime.timedelta(days=n)}[m.group(2)]
        return now - delta
    iso = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError:
        raise ValueError("not ISO-8601 or a relative window: %r" % text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def row_timestamp(row):
    """Parse a row's timestamp; None when missing/unparseable."""
    if not isinstance(row, dict):
        return None
    raw = row.get("timestamp")
    if not raw:
        return None
    s = str(raw).strip()
    s = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def key_file_path(env):
    if env.get("AUTOOS_AI_STACK_CONFIG"):
        base = env["AUTOOS_AI_STACK_CONFIG"]
    else:
        base = os.path.join(env.get("XDG_CONFIG_HOME")
                            or os.path.join(env.get("HOME", ""), ".config"),
                            "autoos", "ai-stack")
    return os.path.join(base, "manage.key")


def read_manage_key(path):
    with open(path, "r", encoding="utf-8") as fh:
        key = fh.read().strip()
    if not key:
        raise OSError("empty key file")
    return key


def auth_headers(key):
    return {"Authorization": "Bearer %s" % key}


def urllib_fetch(url, headers, timeout):
    """(status, body bytes). Transport errors propagate as URLError/OSError."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:  # an HTTP status is a response, not a transport failure
        return e.code, e.read()


def fetch_window(fetch, gateway, key, cutoff):
    """Page the gateway until the rows are older than the cutoff.

    Returns (rows, pages, truncated): rows deduped by id, pages fetched, and
    truncated=True when the MAX_PAGES cap stopped the walk with more rows
    likely unread. Raises UsageError on any gateway failure.
    """
    rows = []
    seen = set()
    pages = 0
    truncated = True
    while pages < MAX_PAGES:
        url = ("%s/api/usage/call-logs?limit=%d&offset=%d&excludeTests=1"
               % (gateway, PAGE_LIMIT, pages * PAGE_LIMIT))
        try:
            status, body = fetch(url, auth_headers(key), TIMEOUT_S)
        except (urllib.error.URLError, OSError) as e:
            raise UsageError("gateway unreachable at %s (%s)"
                             % (gateway, getattr(e, "reason", e)))
        if status in (401, 403):
            raise UsageError("gateway refused the manage key (HTTP %d): the key is "
                             "missing, revoked or lacks the 'manage' scope" % status)
        if status != 200:
            raise UsageError("gateway returned HTTP %d for /api/usage/call-logs" % status)
        try:
            page = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
        except (ValueError, UnicodeDecodeError):
            raise UsageError("gateway returned a non-JSON response")
        if not isinstance(page, list):
            raise UsageError("gateway returned an unexpected response "
                             "(expected a JSON array of call-log rows)")
        for r in page:
            rid = r.get("id") if isinstance(r, dict) else None
            if rid is not None:
                if rid in seen:
                    continue
                seen.add(rid)
            rows.append(r)
        pages += 1
        stamps = [ts for ts in (row_timestamp(r) for r in page) if ts is not None]
        if not page or len(page) < PAGE_LIMIT:
            truncated = False  # the tail: fewer persisted rows than the limit
            break
        if stamps and min(stamps) < cutoff:
            truncated = False  # reached rows older than --since; the rest are older still
            break
    return rows, pages, truncated


def _group_key(dim, row):
    if not isinstance(row, dict):
        return "(unknown)"
    if dim == "provider":
        return row.get("provider") or "(unknown)"
    if dim == "combo":
        return row.get("comboName") or "(none)"
    if dim == "lane":
        return row.get("sessionTag") or UNTAGGED_LANE
    return row.get("model") or "(unknown)"


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _add_row(group, row):
    group["calls"] += 1
    status = row.get("status") if isinstance(row, dict) else None
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = None
    # Error predicate per route.ts:49-51 wins over ok (route.ts:52-53).
    if (status is not None and status >= 400) or (isinstance(row, dict) and row.get("error")):
        group["errors"] += 1
    elif status is not None and 200 <= status < 300:
        group["ok"] += 1
    tokens = row.get("tokens") if isinstance(row, dict) else None
    tokens = tokens if isinstance(tokens, dict) else {}
    group["tokens_in"] += _as_int(tokens.get("in"))
    group["tokens_out"] += _as_int(tokens.get("out"))


def _new_group(key):
    return {"key": key, "calls": 0, "ok": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0}


def aggregate(rows, dims):
    """{dim: [group, ...]} sorted by calls desc, ties by key asc."""
    by = {}
    for dim in dims:
        groups = {}
        for r in rows:
            key = _group_key(dim, r)
            groups.setdefault(key, _new_group(key))
            _add_row(groups[key], r)
        by[dim] = sorted(groups.values(), key=lambda g: (-g["calls"], g["key"]))
    return by


def totals(rows):
    t = _new_group("(all)")
    for r in rows:
        _add_row(t, r)
    del t["key"]
    return t


def build_report(rows, dims, cutoff, pages, truncated):
    kept = [r for r in rows
            if not (row_timestamp(r) is not None and row_timestamp(r) < cutoff)]
    return {
        "since": cutoff.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": pages,
        "truncated": truncated,
        "totals": totals(kept),
        "by": aggregate(kept, dims),
    }


def render_text(report, dims):
    t = report["totals"]
    lines = ["usage since %s - %d calls, %d ok, %d errors, %d tokens in, %d tokens out (%d page%s)"
             % (report["since"], t["calls"], t["ok"], t["errors"],
                t["tokens_in"], t["tokens_out"], report["pages"], "s" if report["pages"] != 1 else "")]
    if report["truncated"]:
        lines.append("note: stopped at the %d-page cap; older rows may be missing - narrow --since"
                     % MAX_PAGES)
    for dim in dims:
        lines.append("")
        lines.append(dim)
        entries = report["by"].get(dim, [])
        if not entries:
            lines.append("  (no rows)")
            continue
        width = min(40, max([len(dim)] + [len(str(e["key"])) for e in entries]))
        lines.append("%-*s  %6s %6s %6s %10s %10s"
                     % (width, "", "calls", "ok", "errors", "tokens_in", "tokens_out"))
        for e in entries:
            lines.append("%-*s  %6d %6d %6d %10d %10d"
                         % (width, str(e["key"])[:width], e["calls"], e["ok"],
                            e["errors"], e["tokens_in"], e["tokens_out"]))
    return "\n".join(lines)


def main(argv=None, *, fetch=None, env=None, now=None):
    ap = argparse.ArgumentParser(
        prog="autoos-agent.py usage",
        description="Usage report from the OmniRoute gateway: calls, ok/errors and "
                    "tokens by provider, combo, lane and model.")
    ap.add_argument("--since", required=True,
                    help="window start: ISO-8601 UTC or a relative window like 30m, 6h, 2d")
    ap.add_argument("--by", default="provider,combo,lane",
                    help="comma-separated dimensions from: %s (default: %%(default)s)"
                         % ",".join(DIMENSIONS))
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON instead of tables")
    args = ap.parse_args(argv)

    dims = [d.strip() for d in args.by.split(",") if d.strip()]
    bad = [d for d in dims if d not in DIMENSIONS]
    if bad or not dims:
        print("autoos-usage: unknown --by dimension(s): %s (choose from %s)"
              % (", ".join(bad) if bad else "(empty)", ", ".join(DIMENSIONS)), file=sys.stderr)
        return 2

    env = os.environ if env is None else env
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        cutoff = parse_since(args.since, now)
    except ValueError:
        print("autoos-usage: bad --since %r (ISO-8601 UTC or a relative window like 30m, 6h, 2d)"
              % args.since, file=sys.stderr)
        return 2

    path = key_file_path(env)
    try:
        key = read_manage_key(path)
    except OSError:
        print("autoos-usage: manage key file missing or empty: %s - create a manage-scoped "
              "key in the OmniRoute dashboard and save it there (mode 600)" % path, file=sys.stderr)
        return 3

    gateway = (env.get("AUTOOS_OMNIROUTE_URL") or DEFAULT_GATEWAY).rstrip("/")
    fetch = fetch or urllib_fetch
    try:
        rows, pages, truncated = fetch_window(fetch, gateway, key, cutoff)
    except UsageError as e:
        print("autoos-usage: %s" % e, file=sys.stderr)
        return 3

    report = build_report(rows, dims, cutoff, pages, truncated)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report, dims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
