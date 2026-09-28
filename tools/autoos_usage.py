#!/usr/bin/env python3
"""Usage report from the OmniRoute gateway: calls, ok/errors and tokens by
provider, combo, lane, model and run. The operator-facing name is the subcommand:

    autoos-agent.py usage --since 1h [--by provider,combo,lane,model,run] [--json]
    autoos-agent.py usage --since 24h --by provider --cost   # the spend guard
    autoos-agent.py usage --since 1h --by lane,run           # the lane and its spawns
    autoos-agent.py usage --since 1h --spend-since --balance-usd 19.99   # the cap

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
  (callLogs.ts:505 - the row's conversation id, which for an AutoOS spawn is
  the whole `x-omniroute-session-id` header: OR3's
  "<orchestrator-worktree>/<title>", plus "/<run-id>" since D-063).
- Error predicate (route.ts:49-51): Number(status) >= 400 OR truthy error
  field. Ok (route.ts:52-53): 200 <= status < 300. This report counts a row
  as error if the error predicate holds, else ok if the ok predicate holds,
  else neither (e.g. an in-flight row with status 0).
- Auth (route.ts:230 -> src/lib/api/requireManagementAuth.ts): a Bearer
  manage-scoped key; 401 with no credential, 403 when the key lacks the
  'manage' scope. The host keeps that key in
  ${AUTOOS_AI_STACK_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/autoos/ai-stack}/manage.key
  (read the same way as configuration/omniroute/apply.sh).

Grouping: lane is the part of the row's sessionTag before the FIRST '/' - the
session tag and the D-063 `<tag>/<run-id>` value therefore group into the same
lane - "(untagged)" when empty; run is the part after the LAST '/' when it is a
canonical run id, "(no run id)" otherwise; combo is comboName, "(none)" when
empty; provider/model "(unknown)" when empty. Rows
with a timestamp older than --since are dropped; rows without a parseable
timestamp are kept (they are almost always in-flight rows).

Cost (--cost, MUSEAPI step 5): every group and the totals also carry cost_in /
cost_out in USD, priced from catalog/ai-registry.json's price_in/price_out.
Those registry fields are USD PER TOKEN (spec 3.1: the Meta contributor's
$0.10 per 1M is 1e-07), so a cost is tokens * price. --registry points the
lookup at another file (a test fixture). A model the registry does not know
costs 0 and says so by being 0 - the tokens are still counted. Cost is
strictly opt-in: without the flag the JSON has exactly the keys it always had.
An estimate, not an invoice - it prices what the gateway reported it served,
and free tiers are priced 0 in the registry, so a $0 line can mean "free" or
"no price on file".

Paid spend (--spend-since, --balance-usd, DSGUARD): the operator caps DeepSeek
spend at 20 USD a month, so the report can carry a `paid_spend` block for that
one paid provider - tokens * the registry's per-token price * the factor in
providers.deepseek.windows at each row's own timestamp (the same
autoos_resolver.price_factor the router uses to pick a cheap hour). The window
starts at --spend-since, defaulting to the 1st of the current month UTC, and is
read deeper than --since when it reaches further back; the row fetch stops at
the page cap, so an incomplete window reports a floor, never a total. WARN lines
are raised when spend >= 20 USD, or when --balance-usd (a balance the caller
measured at the gateway) is below 5 USD. Off unless one of the two flags is
given: `usage --json` keeps the shape it always had.

Exit codes: 0 ok; 2 bad input (--since/--by/--spend-since/--balance-usd); 3
gateway unreachable, HTTP error (401/403 named as auth failures), a non-array
response, or the key file missing/empty. The failure line names the cause and
never the key.

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
from pathlib import Path

import autoos_resolver as resolver  # tools/ is on sys.path for every caller

DEFAULT_GATEWAY = "http://127.0.0.1:20128"
PAGE_LIMIT = 500
MAX_PAGES = 20
TIMEOUT_S = 15
DIMENSIONS = ("provider", "combo", "lane", "model", "run")
UNTAGGED_LANE = "(untagged)"
NO_RUN_LABEL = "(no run id)"
# The --by table's key column: wide enough for one whole canonical run id.
KEY_MAX = 48

# D-063: the gateway session header carries `<session tag>/<run-id>` — one
# session tag per lane, one conversation per run. So a call-log row's sessionTag
# splits two ways: lane = everything before the FIRST '/', run = the part after
# the LAST '/' when it is a run id. An old tag (`<lane>/<title>`) has no run
# part and groups into the same lane as the new `<lane>/<title>/<run-id>`, which
# is why the lane is the HEAD and not the whole value.
# The run-id shape is tools/autoos-agent.py `mint_run_id`'s own, written twice
# because the spawner delegates `usage` HERE (importing it back would be
# circular); test_the_run_id_shape_matches_the_one_the_spawner_mints pins the two
# copies to each other and test_every_id_the_spawner_mints_is_a_run_to_usage
# checks them against real minted ids.
RUN_ID_SHAPE = re.compile(r"^(\d{8}-\d{6})-([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{6})$")
RUN_ID_SLUG_CAP = 24


def is_run_id(value) -> bool:
    """True for the `<slug>`-capped id shape the spawner mints (same bounds as
    its `is_canonical_run_id`, less the calendar check: a grouping key only has
    to be recognisable, and a stamp with an impossible date is still one run)."""
    match = RUN_ID_SHAPE.match(value or "")
    return bool(match) and len(match.group(2)) <= RUN_ID_SLUG_CAP


def lane_of(session_id) -> str:
    """The lane part of a session id: everything before the first '/'."""
    return (session_id or "").split("/")[0] or UNTAGGED_LANE


def run_of(session_id) -> str:
    """The run part: what follows the LAST '/', but only when it is a run id.

    A tag's tail (`<lane>/<title>`) is a title slug, not an id; calling it a run
    would invent a run per title and split one spawn's rows in two.
    """
    tail = (session_id or "").rsplit("/", 1)[-1]
    return tail if is_run_id(tail) else NO_RUN_LABEL

# DSGUARD: the one paid provider the operator budgets in dollars per month, the
# cap itself, and the balance floor under which the next top-up is late.
SPEND_PROVIDER = "deepseek"
SPEND_PROVIDER_LABEL = "DeepSeek"
SPEND_WARN_USD = 20.0      # the warning line; the hard cap is providers.<id>.monthly_cap_usd
BALANCE_FLOOR_USD = 5.0    # warn before the balance runs out mid-lane

# Registry path for cost lookup
ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "catalog" / "ai-registry.json"


class UsageError(Exception):
    """A one-line, key-free failure cause (exit 3)."""


def price_source_name(path=None):
    """The name the report carries for its price file - repo-relative when the
    file is in the repo, absolute otherwise, None when it cannot be read.

    The name travels with the report: numbers a reader cannot trace are not a
    spend guard, and a report must never claim a registry priced what it could
    not read."""
    resolved = Path(path) if path else REGISTRY_PATH
    if not resolved.is_file():
        return None
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def prices_from_registry(registry):
    """{model id: (price_in, price_out)} in USD PER TOKEN, from a parsed
    registry's `models` section. The prices are already per token - the
    contributor's $0.10 per 1M is 1e-07 here - so multiply by the token count
    and nothing else. A model with no price on file is simply absent, which the
    report shows as a 0 cost rather than a failure: the tokens stay counted.
    """
    prices = {}
    for model_id, model in (registry.get("models") or {}).items():
        if isinstance(model, dict) and model.get("price_in") is not None \
                and model.get("price_out") is not None:
            try:
                prices[model_id] = (float(model["price_in"]), float(model["price_out"]))
            except (TypeError, ValueError):
                pass
    return prices


def load_registry_prices(path=None):
    """The price table read straight from a registry file ({} when unreadable)."""
    return prices_from_registry(read_registry(path))


def read_registry(path=None):
    """The parsed registry as a dict; {} when it cannot be read or parsed.

    Shared by the price table and the price windows: one file, one reader, so
    a spend figure and a routing decision can never be built from two
    differently-broken parses of the same bytes.
    """
    try:
        with (Path(path) if path else REGISTRY_PATH).open(encoding="utf-8") as fh:
            registry = json.load(fh)
    except (OSError, ValueError):
        return {}
    return registry if isinstance(registry, dict) else {}


def price_for(model, prices):
    """The (price_in, price_out) row for a call-log model, or None.

    The gateway reports its own spelling - prefixed with the connection, e.g.
    meta-api/muse-spark-1.3-contributor - while the registry keys the bare model
    id, so an exact-only lookup would price every real call as free. The exact
    string is tried first because a registry id may legitimately contain a slash
    (groq spells gpt-oss-120b as openai/gpt-oss-120b, cerebras does not).
    """
    if not model or not prices:
        return None
    if model in prices:
        return prices[model]
    if "/" in model:
        tail = model.rsplit("/", 1)[1]
        if tail in prices:
            return prices[tail]
    return None


def monthly_cap_usd(registry, provider=SPEND_PROVIDER):
    """providers.<provider>.monthly_cap_usd as a float: the hard monthly cap a paid
    caller must stay under (WS-DSCALL, 2026-09-28). SPEND_WARN_USD stays the
    warning line. ValueError when the cap is absent or not a positive number."""
    entry = ((registry or {}).get("providers") or {}).get(provider) or {}
    cap = entry.get("monthly_cap_usd") if isinstance(entry, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        raise ValueError("providers.%s.monthly_cap_usd is missing or not a positive number"
                         % provider)
    return float(cap)


def month_start(now):
    """The first instant of `now`'s UTC calendar month - the spend window's default."""
    return now.astimezone(datetime.timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


def is_spend_row(row, provider=SPEND_PROVIDER):
    """True when the gateway billed this row to the watched paid provider."""
    if not isinstance(row, dict):
        return False
    return str(row.get("provider") or "").strip().lower() == provider


def paid_spend(rows, prices, registry, since, provider=SPEND_PROVIDER, balance=None,
               price_source=None, complete=True):
    """The DSGUARD block: what this month's paid calls cost, and what to warn about.

    Each row costs tokens * the registry's per-token price * the provider's
    window factor at the row's own timestamp. The factor comes from
    autoos_resolver.price_factor - the same code the router uses to decide when
    to send work to DeepSeek (providers.<id>.windows), so the guard and the
    router can never disagree about what an hour costs. A row whose model has no
    price on file adds 0 and is counted in models_unpriced, so a low figure
    reads as low spend or as a gap, never silently as either.

    The comparison is against the rounded, reported figure: a cap missed by
    1e-15 of float drift is a cap the operator believed was held.
    """
    window_source = {"providers": dict(registry.get("providers") or {})}
    window_source["providers"].setdefault(provider, {})
    spend = 0.0
    calls = tokens_in = tokens_out = 0
    unpriced = set()
    for r in rows:
        if not is_spend_row(r, provider):
            continue
        ts = row_timestamp(r)
        if ts is not None and ts < since:
            continue
        tokens = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
        tin = _as_int(tokens.get("in"))
        tout = _as_int(tokens.get("out"))
        calls += 1
        tokens_in += tin
        tokens_out += tout
        pair = price_for(r.get("model"), prices)
        if not pair:
            if r.get("model"):
                unpriced.add(r.get("model"))
            continue
        # No parseable timestamp means an in-flight row: bill it at full price.
        factor = resolver.price_factor(provider, window_source, ts) if ts else 1.0
        spend += (tin * pair[0] + tout * pair[1]) * factor

    spend = round(spend, 6)
    warnings = []
    if spend >= SPEND_WARN_USD:
        warnings.append("%s spend $%.2f since %s reached the %.0f USD cap"
                        % (SPEND_PROVIDER_LABEL, spend,
                           since.strftime("%Y-%m-%dT%H:%M:%SZ"), SPEND_WARN_USD))
    if balance is not None and balance < BALANCE_FLOOR_USD:
        warnings.append("%s balance $%.2f is below the %.0f USD floor"
                        % (SPEND_PROVIDER_LABEL, balance, BALANCE_FLOOR_USD))
    return {
        "provider": provider,
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "calls": calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "spend_usd": spend,
        "threshold_usd": SPEND_WARN_USD,
        "balance_usd": balance,
        "balance_threshold_usd": BALANCE_FLOOR_USD,
        "models_unpriced": len(unpriced),
        "price_source": price_source,
        "complete": bool(complete),
        "warnings": warnings,
    }


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
        except Exception as e:  # http.client errors etc.: type only, the text may hold a body
            raise UsageError("gateway read failed at %s (%s)" % (gateway, type(e).__name__))
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
        return lane_of(row.get("sessionTag"))
    if dim == "run":
        return run_of(row.get("sessionTag"))
    return row.get("model") or "(unknown)"


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _new_group(key, priced=False):
    group = {"key": key, "calls": 0, "ok": 0, "errors": 0,
             "tokens_in": 0, "tokens_out": 0}
    if priced:
        group["cost_in"] = 0.0
        group["cost_out"] = 0.0
    return group


def _add_row(group, row, prices=None):
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
    tin = _as_int(tokens.get("in"))
    tout = _as_int(tokens.get("out"))
    group["tokens_in"] += tin
    group["tokens_out"] += tout
    if prices is not None:
        # Registry prices are per token, so tokens * price is the USD cost of
        # the row. A model with no price on file adds 0.
        pair = price_for(row.get("model") if isinstance(row, dict) else None, prices)
        if pair:
            group["cost_in"] += tin * pair[0]
            group["cost_out"] += tout * pair[1]


def aggregate(rows, dims, prices=None):
    """{dim: [group, ...]} sorted by calls desc, ties by key asc."""
    priced = prices is not None
    by = {}
    for dim in dims:
        groups = {}
        for r in rows:
            key = _group_key(dim, r)
            groups.setdefault(key, _new_group(key, priced))
            _add_row(groups[key], r, prices)
        by[dim] = sorted(groups.values(), key=lambda g: (-g["calls"], g["key"]))
    return by


def totals(rows, prices=None):
    t = _new_group("(all)", prices is not None)
    for r in rows:
        _add_row(t, r, prices)
    del t["key"]
    return t


def build_report(rows, dims, cutoff, pages, truncated, prices=None, price_source=None,
                 spend=None):
    kept = [r for r in rows
            if not (row_timestamp(r) is not None and row_timestamp(r) < cutoff)]
    report = {
        "since": cutoff.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": pages,
        "truncated": truncated,
        "totals": totals(kept, prices),
        "by": aggregate(kept, dims, prices),
    }
    if prices is not None:
        unpriced = {r.get("model") for r in kept if isinstance(r, dict)
                    and r.get("model") and price_for(r.get("model"), prices) is None}
        report["cost"] = {"source": price_source, "models_unpriced": len(unpriced)}
    if spend is not None:
        report["paid_spend"] = spend
    return report


def render_spend_text(spend):
    """The paid-spend section: the figure, its caveats, then the WARN lines."""
    lines = ["", "paid spend (%s) since %s" % (SPEND_PROVIDER_LABEL, spend["since"]),
             "  %d calls, %d tokens in, %d tokens out -> $%.4f (priced from %s)"
             % (spend["calls"], spend["tokens_in"], spend["tokens_out"],
                spend["spend_usd"], spend["price_source"] or "no readable registry")]
    if spend["models_unpriced"]:
        lines.append("  %d model(s) had no price on file and count as 0"
                     % spend["models_unpriced"])
    if spend["balance_usd"] is not None:
        lines.append("  balance: $%.2f (measured by the caller); warns below $%.2f"
                     % (spend["balance_usd"], spend["balance_threshold_usd"]))
    lines.append("  warns at $%.2f of spend" % spend["threshold_usd"])
    if not spend["complete"]:
        lines.append("  incomplete: the fetch stopped at the %d-page cap, so the figure "
                     "is a floor, not the total" % MAX_PAGES)
    for warning in spend["warnings"]:
        lines.append("  WARN: %s" % warning)
    return lines


def render_text(report, dims):
    """One table per --by dimension. Whether the cost columns appear is a
    property of the report (build_report adds them under --cost), not a second
    flag that could disagree with it."""
    t = report["totals"]
    cost = report.get("cost")
    # (header, width, formatter) - the cost pair rides along only when priced.
    columns = [("calls", 6, "%d"), ("ok", 6, "%d"), ("errors", 6, "%d"),
               ("tokens_in", 10, "%d"), ("tokens_out", 10, "%d")]
    if cost is not None:
        columns += [("cost_in", 10, "%.4f"), ("cost_out", 10, "%.4f")]
    lines = ["usage since %s - %d calls, %d ok, %d errors, %d tokens in, %d tokens out (%d page%s)%s"
             % (report["since"], t["calls"], t["ok"], t["errors"],
                t["tokens_in"], t["tokens_out"], report["pages"],
                "s" if report["pages"] != 1 else "",
                "" if cost is None else
                " - estimated cost: $%.4f" % (t["cost_in"] + t["cost_out"]))]
    if cost is not None:
        lines.append("estimated cost priced from %s; %d model(s) had no price on file "
                     "and count as 0" % (cost["source"] or "no readable registry",
                                         cost["models_unpriced"]))
    if report["truncated"]:
        lines.append("note: stopped at the %d-page cap; older rows may be missing - narrow --since"
                     % MAX_PAGES)
    spend = report.get("paid_spend")
    if spend is not None:
        lines.extend(render_spend_text(spend))
    for dim in dims:
        lines.append("")
        lines.append(dim)
        entries = report["by"].get(dim, [])
        if not entries:
            lines.append("  (no rows)")
            continue
        # A canonical run id is at most 15+1+24+1+6 = 47 characters
        # (mint_run_id: stamp, RUN_ID_SLUG_CAP slug, hex tail), so the key column
        # is capped at one that fits - a truncated run id cannot be pasted into
        # `ps` or a branch name. --json was always the untruncated source.
        width = min(KEY_MAX, max([len(dim)] + [len(str(e["key"])) for e in entries]))
        lines.append("%-*s  %s" % (width, "",
                                     "  ".join(h.rjust(w) for h, w, _f in columns)))
        for e in entries:
            cells = [f % e[h] for h, w, f in columns]
            lines.append("%-*s  %s" % (width, str(e["key"])[:width],
                                         "  ".join(c.rjust(w) for c, (_h, w, _f)
                                                   in zip(cells, columns))))
    return "\n".join(lines)


def main(argv=None, *, fetch=None, env=None, now=None):
    ap = argparse.ArgumentParser(
        prog="autoos-agent.py usage",
        description="Usage report from the OmniRoute gateway: calls, ok/errors and "
                    "tokens by provider, combo, lane, model and run.")
    ap.add_argument("--since", required=True,
                    help="window start: ISO-8601 UTC or a relative window like 30m, 6h, 2d")
    ap.add_argument("--by", default="provider,combo,lane",
                    help="comma-separated dimensions from: %s (default: %%(default)s)"
                         % ",".join(DIMENSIONS))
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON instead of tables")
    ap.add_argument("--cost", action="store_true",
                    help="add estimated cost_in/cost_out (USD) per group, priced from "
                         "the registry's price_in/price_out")
    ap.add_argument("--registry", default=None,
                    help="price source for --cost and the paid-spend section "
                         "(default: catalog/ai-registry.json)")
    ap.add_argument("--spend-since", nargs="?", const="", default=None, metavar="SINCE",
                    help="add the paid-spend section: %s spend since this date (ISO-8601 UTC or a "
                         "relative window; the flag alone means the 1st of this month UTC)"
                         % SPEND_PROVIDER_LABEL)
    ap.add_argument("--balance-usd", default=None, metavar="USD",
                    help="the %s balance the caller measured at the gateway; adds the paid-spend "
                         "section and WARNs below %.0f USD" % (SPEND_PROVIDER_LABEL,
                                                               BALANCE_FLOOR_USD))
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

    # DSGUARD: the spend window usually reaches further back than --since (the
    # month so far, versus the last hour of traffic), so it is resolved here and
    # takes the deeper of the two cutoffs for the fetch below.
    spend_on = args.spend_since is not None or args.balance_usd is not None
    spend_cutoff = balance = None
    if spend_on:
        if args.spend_since:
            try:
                spend_cutoff = parse_since(args.spend_since, now)
            except ValueError:
                print("autoos-usage: bad --spend-since %r (ISO-8601 UTC, a relative window "
                      "like 30m, 6h, 2d, or the flag alone for the 1st of this month)"
                      % args.spend_since, file=sys.stderr)
                return 2
        else:
            spend_cutoff = month_start(now)
        if args.balance_usd is not None:
            try:
                balance = float(args.balance_usd)
            except ValueError:
                print("autoos-usage: bad --balance-usd %r (a number of USD, as measured "
                      "at the gateway)" % args.balance_usd, file=sys.stderr)
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
        rows, pages, truncated = fetch_window(fetch, gateway, key,
                                              min(cutoff, spend_cutoff)
                                              if spend_on else cutoff)
    except UsageError as e:
        print("autoos-usage: %s" % e, file=sys.stderr)
        return 3

    priced = args.cost or spend_on
    registry = read_registry(args.registry) if priced else {}
    prices = prices_from_registry(registry)
    price_source = price_source_name(args.registry) if priced else None
    spend = paid_spend(rows, prices, registry, spend_cutoff, balance=balance,
                       price_source=price_source, complete=not truncated) if spend_on else None
    report = build_report(rows, dims, cutoff, pages, truncated,
                          prices=prices if args.cost else None,
                          price_source=price_source if args.cost else None,
                          spend=spend)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report, dims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
