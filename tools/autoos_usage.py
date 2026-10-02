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
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import autoos_resolver as resolver  # tools/ is on sys.path for every caller

DEFAULT_GATEWAY = "http://127.0.0.1:20128"
PAGE_LIMIT = 500
MAX_PAGES = 20
TIMEOUT_S = 15
# T1-CREDIT-FIX-10 M1 (D-250): the gateway container the read-only CLI helper
# runs in. The helper (`/app/bin/cli/api.mjs`' apiFetch) authenticates with its
# own machine-derived loopback token inside the container, so this transport
# never sees a key at all.
HELPER_CONTAINER = "autoos-omniroute"
HELPER_API_MODULE = "/app/bin/cli/api.mjs"
# T1-CREDIT-FIX-10 M2 (D-240): the local USD cap an UNMEASURED paid leg is
# held to. Tighter than the provider's own monthly cap: while spend cannot be
# measured, the last-resort leg is refused at this line instead of the full
# cap. Overridable per registry (`policy.paid_local_cap_usd`).
PAID_LOCAL_CAP_DEFAULT_USD = 20.0
# T1-CREDIT-FIX-10 M4 (D-253): the prepaid balance floor and snapshot
# freshness for the provider-balance paid meter. A fresh snapshot below the
# floor refuses the leg; a snapshot older than the window is history, not a
# meter, and the exhausted check skips it.
BALANCE_EXHAUSTED_USD = 3.0
BALANCE_FRESH_S = 3 * 3600
# T1-CREDIT-FIX-14 (D-274): tolerated clock skew for balance `fetched_at`
# stamps. A reading stamped later than now + this window is not a fresh
# snapshot from a slow clock -- it is an untrustworthy ordering that would
# let a future line pose as the latest reading -- so it marks the ledger
# STALE instead. Stamps within the window count normally (fresh). A
# far-future stamp therefore keeps the paid guard refusing (fail closed,
# acceptable) until the stamp ages into the past, when the normal meter
# resumes; the skew is a trust bound, not a grace period.
BALANCE_CLOCK_SKEW_S = 300
BALANCE_LEDGER_REL = os.path.join("routing", "provider-balances.jsonl")
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

# T1-CREDIT-FIX-5 (D-220): a `credit` grant keeps room below its total so the
# last calls of a draining trial do not land on a card that is already over the
# vendor's limit. `MANUAL_CREDIT_SPEND_MAX_AGE_DAYS` is how long the operator's
# dated manual reading is trusted before it is (correctly) treated as unknown.
CREDIT_HARD_STOP_MARGIN_USD = 20.0
MANUAL_CREDIT_SPEND_MAX_AGE_DAYS = 7

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

    0 is not a price (brief FREEKEYS-1b item 3, rev-freekeys1 finding 3): every
    grant row shipped by FREEKEYS-1 carries `price_in`/`price_out` of 0, and a
    table that reads those as $0/token reports a draining $10 grant as untouched
    money. Dropping them here makes the same model count as `models_unpriced`,
    which is the gap `autoos_resolver.credit_leg_priced` refuses a credit leg on.

    T1-CREDIT-FIX-6 (D-220): a model id served at two prices at once also lands
    here once per provider spelling -- `models.<id>.provider_prices.<provider>`
    is emitted under both `<provider>/<model>` and, when the provider declares
    one, `<omniroute_id>/<model>` (the spelling call-log rows carry), so an
    exact `price_for` hit bills the provider's own price. The bare model id
    stays absent (its row is 0), so the free provider's rows keep counting as
    unpriced instead of borrowing the paid provider's price.
    """
    prices = {}
    for model_id, model in (registry.get("models") or {}).items():
        if not isinstance(model, dict):
            continue
        try:
            price_in = float(model.get("price_in"))
            price_out = float(model.get("price_out"))
        except (TypeError, ValueError):
            continue
        if price_in > 0.0 and price_out > 0.0:
            prices[model_id] = (price_in, price_out)
        scoped = model.get("provider_prices")
        if not isinstance(scoped, dict):
            continue
        for provider_id, entry in scoped.items():
            if not isinstance(entry, dict):
                continue
            try:
                scoped_in = float(entry.get("price_in"))
                scoped_out = float(entry.get("price_out"))
            except (TypeError, ValueError):
                continue
            if not (scoped_in > 0.0 and scoped_out > 0.0):
                continue
            pair = (scoped_in, scoped_out)
            prices["%s/%s" % (provider_id, model_id)] = pair
            provider = (registry.get("providers") or {}).get(provider_id)
            alias = provider.get("omniroute_id") if isinstance(provider, dict) else None
            if alias and alias != provider_id:
                prices["%s/%s" % (alias, model_id)] = pair
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


def price_for(model, prices, provider=None, registry=None):
    """The (price_in, price_out) row for a call-log model, or None.

    The gateway reports its own spelling - prefixed with the connection, e.g.
    meta-api/muse-spark-1.3-contributor - while the registry keys the bare model
    id, so an exact-only lookup would price every real call as free. The exact
    string is tried first because a registry id may legitimately contain a slash
    (groq spells gpt-oss-120b as openai/gpt-oss-120b, cerebras does not).

    T1-CREDIT-FIX-6 (D-220): when the table misses and the caller names the
    row's `provider` with its `registry`, the provider-scoped
    `provider_prices` entry is tried last -- a bare model spelling under a
    billed provider (a vertex_ai row carrying just `gemini-3.8-flash`) prices
    at the provider's price instead of reading as free. The table still wins
    on any hit, so callers without a registry see exactly the old behavior.
    """
    if not model or not prices:
        return None
    if model in prices:
        return prices[model]
    if "/" in model:
        tail = model.rsplit("/", 1)[1]
        if tail in prices:
            return prices[tail]
    if provider and isinstance(registry, dict):
        models = registry.get("models") or {}
        model_id = None
        if model in models:
            model_id = model
        elif "/" in model and model.rsplit("/", 1)[1] in models:
            model_id = model.rsplit("/", 1)[1]
        if model_id is not None:
            pair = resolver.leg_price(model_id, provider, registry)
            if pair is not None:
                return pair
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


def spend_warn_usd(registry, provider=SPEND_PROVIDER):
    """The USD line at which a provider's spend guard WARNS (brief FREEKEYS-1,
    D-132/D-141): `providers.<id>.monthly_warn_fraction` x its `monthly_cap_usd`.

    A row that declares no fraction keeps the legacy DSGUARD line (SPEND_WARN_USD),
    so the paid rows written before this field existed report exactly what they
    reported before. ValueError when a fraction is declared on a row with no cap --
    that pairing is what registry.py check already rejects, and reading it here
    would silently warn at 0.0 on every call."""
    entry = ((registry or {}).get("providers") or {}).get(provider) or {}
    fraction = entry.get("monthly_warn_fraction") if isinstance(entry, dict) else None
    if fraction is None:
        return float(SPEND_WARN_USD)
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction < 1:
        raise ValueError("providers.%s.monthly_warn_fraction is not a fraction in (0, 1)"
                         % provider)
    return monthly_cap_usd(registry, provider) * float(fraction)


def credit_hard_stop_margin_usd(registry, provider):
    """The reserve a `credit` grant keeps below its total, in USD (T1-CREDIT-FIX-5).

    `providers.<id>.credit_hard_stop_margin_usd` (default
    `CREDIT_HARD_STOP_MARGIN_USD`), so a MEASURED spend refuses the leg at
    `monthly_cap_usd - margin` instead of at 100 % of the grant. Paid rows and
    any margin that would zero the cap keep the old refuse-at-cap behaviour; a
    malformed margin is flagged by `registry.py check` and reads as 0 here
    rather than turning a guard into a crash."""
    entry = ((registry or {}).get("providers") or {}).get(provider) or {}
    if not isinstance(entry, dict) or entry.get("tier") != "credit":
        return 0.0
    margin = entry.get("credit_hard_stop_margin_usd", CREDIT_HARD_STOP_MARGIN_USD)
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or margin < 0:
        return 0.0
    cap = monthly_cap_usd(registry, provider)
    if margin >= cap:
        # A margin that would reach or pass the whole grant is no guard at all;
        # fall back to refusing at the cap (registry.py check already flags it).
        return 0.0
    return float(margin)


def spend_guard(registry, provider, spend_usd):
    """(state, note) for one provider's spend guard: "ok" below the warn line,
    "warn" at the warn line (80 % of a credit grant by default) and "refuse" at
    the hard stop -- `monthly_cap_usd` for a paid row, or `monthly_cap_usd`
    minus `credit_hard_stop_margin_usd` for a `credit` grant (T1-CREDIT-FIX-5).

    The refuse half is the same rule deepseek_call.py applies today (WS-DSCALL):
    at or above the cap the call does not happen. This is the shared reading of
    the pair so a `credit` provider's guard is data with a consumer, not a
    comment; a caller that has no price on file for the provider's models still
    sees 0 spend here, which is why the grant's own balance is what FREEKEYS-2
    must check as well.
    """
    if isinstance(spend_usd, bool) or not isinstance(spend_usd, (int, float)) \
            or not math.isfinite(spend_usd):
        # T1-CREDIT-FIX-7 R4: a non-finite figure (nan/inf -- a poisoned
        # ledger, never a measurement) is `unknown`, not `ok`: nan compared
        # False against every line and used to read as a healthy grant.
        return "unknown", ("%s spend %r is not a measurement - spend "
                           "unmeasured, leg kept (fail open)"
                           % (provider, spend_usd))
    cap = monthly_cap_usd(registry, provider)
    warn = spend_warn_usd(registry, provider)
    margin = credit_hard_stop_margin_usd(registry, provider)
    hard_stop = cap - margin
    if spend_usd >= hard_stop:
        if margin:
            return "refuse", ("%s spend $%.2f is at or above the $%.2f hard stop "
                              "($%.2f grant less the $%.2f margin, "
                              "providers.%s.credit_hard_stop_margin_usd)"
                              % (provider, spend_usd, hard_stop, cap, margin, provider))
        return "refuse", ("%s spend $%.2f is at or above the $%.2f cap "
                          "(providers.%s.monthly_cap_usd)" % (provider, spend_usd, cap, provider))
    if spend_usd >= warn:
        return "warn", ("%s spend $%.2f reached the $%.2f warn line "
                        "(providers.%s.monthly_warn_fraction)" % (provider, spend_usd, warn, provider))
    return "ok", ("%s spend $%.2f of a $%.2f cap" % (provider, spend_usd, cap))


def credit_guard_providers(registry):
    """The provider ids whose `tier` is `credit` -- the finite operator grants.

    Read from the data, never a list of names, so a grant the operator funds gets
    guarded the moment its row lands (and `registry.py check` already refuses a
    `credit` row that cannot state its own cap)."""
    return sorted(pid for pid, entry in ((registry or {}).get("providers") or {}).items()
                  if isinstance(entry, dict) and entry.get("tier") == "credit")


def _today_date(today=None):
    """`today` as a `datetime.date`, accepting None (UTC now), a date or an aware/
    naive datetime -- the injectable clock M1's tests drive without sleeping."""
    if today is None:
        return datetime.datetime.now(datetime.timezone.utc).date()
    if isinstance(today, datetime.datetime):
        if today.tzinfo is not None:
            today = today.astimezone(datetime.timezone.utc)
        return today.date()
    return today


def _manual_age_days(as_of, today=None):
    """Whole days from `as_of` (YYYY-MM-DD) to `today`; callers have validated
    the format already, so a parse error here is their bug, not this one's."""
    parsed = datetime.datetime.strptime(as_of.strip(), "%Y-%m-%d").date()
    return (_today_date(today) - parsed).days


def manual_credit_spend(registry, provider, today=None):
    """(spend_usd, as_of) from the registry's dated manual figure, or (None, None).

    `providers.<id>.credit_spent_usd` + `credit_spent_as_of` is the operator's
    dated reading of a grant's billed spend, for when the gateway call-log
    ledger cannot be read. Both or neither: a figure with no date cannot age
    and a date with no figure judges nothing, so a half-present or malformed
    pair reads as absent here (tools/registry.py flags it; the plan never
    crashes on it).

    A reading is only trusted while it is fresh (T1-CREDIT-FIX-5 M1): older
    than `MANUAL_CREDIT_SPEND_MAX_AGE_DAYS` days it reads as absent, and a date
    more than one day in the future (timezone skew tolerance) is not a reading
    at all. A stale reading that read as measured is exactly the fail-open the
    403 made dangerous. `today` is injectable so tests need no clock and no
    sleep."""
    entry = ((registry or {}).get("providers") or {}).get(provider) or {}
    figure = entry.get("credit_spent_usd")
    as_of = entry.get("credit_spent_as_of")
    if figure is None and as_of is None:
        return None, None
    # Reject non-finite values (NaN, Infinity, -Infinity)
    if (isinstance(figure, bool) or not isinstance(figure, (int, float))
            or figure < 0 or not math.isfinite(figure)
            or not isinstance(as_of, str) or not as_of.strip()):
        return None, None
    # Validate YYYY-MM-DD format
    try:
        datetime.datetime.strptime(as_of.strip(), "%Y-%m-%d")
    except ValueError:
        return None, None
    age = _manual_age_days(as_of, today)
    if age > MANUAL_CREDIT_SPEND_MAX_AGE_DAYS or age < -1:
        return None, None
    return float(figure), as_of.strip()


def spend_failure_note(exc):
    """The one-line reason a spend figure is unmeasured, from the failure.

    A gateway 403 (or 401) names the manage key, because a 15-byte revoked or
    wrong-scoped key 403s every call-log read and the spend then reads as $0 --
    which downstream used to print as `credit exhausted ... $0.00/$cap`. An
    OSError is the key file itself (missing, empty, unreadable). Anything else
    keeps its type name only: an error text can carry the gateway URL or the
    home path, and this note is printed into plans and reports (AGENTS.md
    rule 1). The HTTP contract is `fetch_window`'s: 401 with no credential,
    403 when the key lacks the 'manage' scope."""
    if isinstance(exc, UsageError):
        text = str(exc)
        if "HTTP 403" in text:
            return "manage key rejected (403) - spend unmeasured"
        if "HTTP 401" in text:
            return "manage key rejected (401) - spend unmeasured"
        return "credit grant unreadable (%s) - spend unmeasured" % type(exc).__name__
    if isinstance(exc, OSError):
        return "manage key unreadable (%s) - spend unmeasured" % type(exc).__name__
    return "credit grant unreadable (%s) - spend unmeasured" % type(exc).__name__


def credit_guards_unreadable(registry, failure):
    """``{provider id: guard}`` when even the manual fallback cannot be built.

    Pure last resort: no pricing math, only best-effort cap reads, so it cannot
    raise. Every grant reads `unknown` (fail open), never `refuse`.
    """
    out = {}
    try:
        providers = credit_guard_providers(registry)
    except Exception:
        return out  # a malformed registry names no grant; never raise
    for provider in providers:
        cap = warn = 0.0
        try:
            cap = monthly_cap_usd(registry, provider)
            warn = spend_warn_usd(registry, provider)
        except Exception:
            pass  # a grant with no readable cap cannot be judged, only kept openly
        out[provider] = {"provider": provider, "state": "unknown",
                         "spend_usd": 0.0, "spend_unknown": True,
                         "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                         "hard_stop_usd": hard_stop_usd(registry, provider,
                                                          cap),
                         "window_limited": credit_window_limited(registry,
                                                                 provider),
                         "note": "credit spend unknown %s: %s"
                                 % (provider, failure or "no call-log rows")}
    return out


def credit_started_date(registry, provider, today=None):
    """The UTC date a grant started billing (`providers.<id>.credit_started`,
    YYYY-MM-DD), or None.

    T1-CREDIT-FIX-7 R2: the gateway call-log window defaults to the calendar
    month, but a grant cap covers the WHOLE grant -- without a start date,
    September spend reads as $0 on October 1st. A present, well-formed, past
    (or today) date extends the measured window back to the grant's start;
    anything else (absent, malformed, impossible calendar date, in the
    future) reads as absent -- the month-to-date window stays, and the guard
    notes say so loudly instead of passing a partial window off as a total.
    `today` is injectable so tests need no clock."""
    entry = ((registry or {}).get("providers") or {}).get(provider)
    raw = entry.get("credit_started") if isinstance(entry, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None  # malformed or impossible date; registry.py flags it
    if parsed > _today_date(today):
        return None  # a grant that starts tomorrow has no measured spend yet
    return parsed


def credit_window_limited(registry, provider, today=None):
    """True when a `credit` grant has no usable `credit_started`.

    Carried on every guard as `window_limited` so the resolver's loud line
    and the report renderer can name the month-to-date limit without
    re-deriving the date rule (single home: `credit_started_date`)."""
    entry = ((registry or {}).get("providers") or {}).get(provider)
    if not isinstance(entry, dict) or entry.get("tier") != "credit":
        return False
    return credit_started_date(registry, provider, today) is None


def _window_suffix(registry, provider, today=None):
    """The loud month-to-date caveat, or "" when the window covers the grant."""
    if credit_window_limited(registry, provider, today):
        return " (window: month-to-date only; set credit_started)"
    return ""


def _since_or_month_start(since, today=None):
    """The spend window start: the caller's `since`, else this month's start.

    `today` (a date/datetime/None) is the injectable clock tests drive;
    without it the wall clock is read once here, not per provider."""
    if since is not None:
        return since
    if today is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(today, datetime.datetime):
        now = today if today.tzinfo is not None else today.replace(
            tzinfo=datetime.timezone.utc)
    else:
        now = datetime.datetime.combine(today, datetime.time.min,
                                        tzinfo=datetime.timezone.utc)
    return month_start(now)


def _effective_since(registry, provider, since, today=None):
    """The window start a grant is measured over: `since` extended back to
    `credit_started` when the grant declares one (T1-CREDIT-FIX-7 R2)."""
    start = credit_started_date(registry, provider, today)
    if start is None:
        return since
    start_dt = datetime.datetime.combine(start, datetime.time.min,
                                         tzinfo=datetime.timezone.utc)
    return min(since, start_dt)


def hard_stop_usd(registry, provider, cap):
    """The spend figure that refuses a leg: the grant less its
    `credit_hard_stop_margin_usd` (T1-CREDIT-FIX-7 R6: the reason names this
    effective threshold, not the cap). Best-effort -- a margin that cannot
    be read refuses at the cap rather than raising."""
    try:
        margin = credit_hard_stop_margin_usd(registry, provider)
    except Exception:
        margin = 0.0
    if not isinstance(margin, float):
        margin = 0.0
    return float(cap) - margin


def _unmeasured_guard(registry, provider, note):
    """One `unknown` guard that never raises: best-effort cap reads only."""
    cap = warn = 0.0
    try:
        cap = monthly_cap_usd(registry, provider)
        warn = spend_warn_usd(registry, provider)
    except Exception:
        pass  # a grant with no readable cap cannot be judged, only kept openly
    return {"provider": provider, "state": "unknown",
            "spend_usd": 0.0, "spend_unknown": True,
            "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
            "hard_stop_usd": hard_stop_usd(registry, provider, cap),
            "window_limited": credit_window_limited(registry, provider),
            "note": note}


def credit_guards(registry, rows, since=None, failure=None, today=None,
                complete=True):
    """``{provider id: guard}`` for every `credit` provider, from recorded usage rows.

    Field semantics (T1-CREDIT-FIX): `credit_usd` is the trial grant TOTAL in
    USD -- what the vendor funded. `spent` is what the grant already billed
    (the ledger figure below, or the dated manual figure). Remaining =
    credit_usd minus spent. The leg is exhausted only when spent reaches
    `monthly_cap_usd` (which equals `credit_usd` -- refuse at 100 % of the
    grant), so `$0 spent of $N` is an intact grant, never an exhausted one.

    This is the builder that feeds the resolver's leg filter (brief FREEKEYS-1b
    items 2 and 4): one figure per grant, produced by `paid_spend` over the same
    rows the report prints and judged by `spend_guard` against the provider's own
    `monthly_cap_usd`, so what blocks a leg and what the ledger reports are never
    two different numbers. `rows` are the gateway's call-log rows -- the only
    spend ledger in this repo (no separate ledger or cost store exists; the rows
    priced client-side against the registry ARE the ledger); a grant with no
    rows of its own is at $0, and a grant whose models carry no price is reported
    with `models_unpriced` above 0 -- which is exactly the state the resolver
    refuses the leg for, because $0 here would otherwise read as untouched money.

    `rows=None` means the ledger could not be read at all (gateway down, key
    missing or rejected): the figure falls back to the registry's dated manual
    spend (`manual_credit_spend`), and when no manual figure exists the guard
    reads `unknown` -- fail OPEN with a `spend unknown` note, because a prepaid
    trial grant that is truly spent rejects at the provider (402/429) and the
    combo falls through, while fail-closed dropped the whole trial tier on a
    403. `failure` names the cause for the note; it is never a silent $0.00.

    T1-CREDIT-FIX-7 R2: the window a grant is measured over starts at
    `providers.<id>.credit_started` (YYYY-MM-DD) when the grant declares a
    usable one, else at `since` (default: this month's start). The cap covers
    the whole grant, so without a start date the window is month-to-date
    only -- and every guard note then says so loudly
    ("(window: month-to-date only; set credit_started)") instead of passing
    a partial window off as a total. `today` is the injectable clock the
    window math reads instead of the wall clock.

    Each guard is ``{"provider", "state", "spend_usd", "spend_unknown",
    "cap_usd", "warn_usd", "models_unpriced", "note"}``, with `state` from
    `spend_guard` (`ok` below the warn line, `warn` at it, `refuse` at the hard
    stop -- the cap, or the cap less a `credit` grant's
    `credit_hard_stop_margin_usd`), `manual` for a still-valid dated fallback
    figure (never `ok`; T1-CREDIT-FIX-5 M1) or `unknown` when nothing
    measurable exists. `spend_unknown` is True only for the unknown state, so a
    reader can tell "measured $0" from "unmeasured".
    """
    if since is None:
        since = _since_or_month_start(None, today)
    prices = prices_from_registry(registry)
    out = {}
    for provider in credit_guard_providers(registry):
        if rows is None:
            manual, as_of = manual_credit_spend(registry, provider, today)
            if manual is not None:
                state, _note = spend_guard(registry, provider, manual)
                # T1-CREDIT-FIX-5 M1: a dated manual figure is not a measurement.
                # Below the hard stop it reads `manual` (never `ok`), and the note
                # carries its age so a reader can judge how stale the reading is.
                if state != "refuse":
                    state = "manual"
                note = ("manual figure $%.2f as of %s (age %d d)"
                        % (manual, as_of, _manual_age_days(as_of, today)))
                out[provider] = {
                    "provider": provider, "state": state,
                    "spend_usd": manual, "spend_unknown": False,
                    "cap_usd": monthly_cap_usd(registry, provider),
                    "hard_stop_usd": hard_stop_usd(
                        registry, provider,
                        monthly_cap_usd(registry, provider)),
                    "warn_usd": spend_warn_usd(registry, provider),
                    "models_unpriced": 0,
                    "window_limited": credit_window_limited(
                        registry, provider, today),
                    "note": note}
                continue
            out[provider] = {
                "provider": provider, "state": "unknown",
                "spend_usd": 0.0, "spend_unknown": True,
                "cap_usd": monthly_cap_usd(registry, provider),
                    "hard_stop_usd": hard_stop_usd(
                        registry, provider,
                        monthly_cap_usd(registry, provider)),
                "warn_usd": spend_warn_usd(registry, provider),
                "models_unpriced": 0,
                "window_limited": credit_window_limited(
                    registry, provider, today),
                "note": "credit spend unknown %s: %s - leg kept (fail open: "
                        "a spent prepaid grant rejects at the provider and "
                        "the combo falls through)%s"
                        % (provider, failure or "no call-log rows",
                           _window_suffix(registry, provider, today))}
            continue
        window_start = _effective_since(registry, provider, since, today)
        try:
            spend = paid_spend(rows, prices, registry, window_start,
                               provider=provider)
            state, note = spend_guard(registry, provider, spend["spend_usd"])
            note += _window_suffix(registry, provider, today)
            unreadable = int(spend.get("rows_unreadable") or 0)
            if unreadable and state != "refuse":
                # T1-CREDIT-FIX-8 C3: unreadable rows are not silent $0 --
                # the grant reads unknown (fail open), naming the COUNT of
                # unreadable rows (never their contents) beside the readable
                # figure that still added up. Measured spend already at or
                # over the hard stop still refuses: it beats unknown.
                state = "unknown"
                note = ("credit spend unknown %s: %d unreadable row(s) "
                        "(readable spend $%.2f) - leg kept (fail open: a "
                        "spent prepaid grant rejects at the provider and "
                        "the combo falls through)%s"
                        % (provider, unreadable, spend["spend_usd"],
                           _window_suffix(registry, provider, today)))
            if not complete and state != "refuse":
                # T1-CREDIT-FIX-9 T1: a fetch cut at the page cap is not a
                # measurement -- the September $300 row behind the cap reads
                # as ok/$0 without this. Measured spend already at or over
                # the hard stop still refuses: it beats unknown.
                state = "unknown"
                note = ("credit spend unknown %s: fetch truncated at page "
                        "cap (readable spend $%.2f) - leg kept (fail open: "
                        "a spent prepaid grant rejects at the provider and "
                        "the combo falls through)%s"
                        % (provider, spend["spend_usd"],
                           _window_suffix(registry, provider, today)))
        except ValueError:
            # Config errors (a grant that cannot state its cap) still raise:
            # the usage report exits 3 on them (pinned by T1-CREDIT-FIX-2
            # C3) and the plan falls back to `unknown` for the map. Only
            # UNFORESEEN per-provider bugs degrade to a single-grant
            # `unknown` below.
            raise
        except Exception as exc:  # noqa: BLE001 - one bad grant, not the map
            # T1-CREDIT-FIX-7 R4: a provider whose own figure cannot be built
            # (a cap it cannot state, a pricing bug) degrades to `unknown`
            # for THAT grant only -- fail open, named -- instead of raising
            # and turning every grant into `guard error` downstream.
            out[provider] = _unmeasured_guard(
                registry, provider,
                "credit guard unreadable (%s) - spend unmeasured, leg kept "
                "(fail open)" % type(exc).__name__)
            continue
        out[provider] = {"provider": provider, "state": state,
                         "spend_usd": spend["spend_usd"],
                         "spend_unknown": state == "unknown",
                         "cap_usd": monthly_cap_usd(registry, provider),
                         "hard_stop_usd": hard_stop_usd(
                             registry, provider,
                             monthly_cap_usd(registry, provider)),
                         "warn_usd": spend_warn_usd(registry, provider),
                         "models_unpriced": spend["models_unpriced"],
                         "window_limited": credit_window_limited(
                             registry, provider, today),
                         "note": note}
    return out


def month_start(now):
    """The first instant of `now`'s UTC calendar month - the spend window's default."""
    return now.astimezone(datetime.timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


def fetch_cutoff(registry, now=None):
    """The oldest instant the gateway fetch must reach: this month's start
    extended back to the earliest usable `credit_started` over every
    `credit` grant (T1-CREDIT-FIX-8 C1).

    `fetch_window` stops paging at the first page holding a row older than
    its cutoff, so a month-start cutoff fetches September rows only by luck
    of pagination -- a grant started 2026-09-01 then reads $150 on one page
    size and $450 on another. Fetching from the earliest grant start reaches
    every grant's rows on every page size; each grant is still FILTERED by
    its own window (`_effective_since` in `credit_guards`, month-to-date for
    paid), so a leg is never refused on money spent elsewhere. Single home:
    both fetch callers read this, never a bare `month_start`."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    base = month_start(now)
    earliest = base
    for provider in credit_guard_providers(registry or {}):
        try:
            started = credit_started_date(registry, provider, now)
        except (ValueError, TypeError, AttributeError):
            # T1-CREDIT-FIX-9 T8: a malformed credit_started is not fetchable
            # depth -- the grant stays month-to-date (window_limited, loud
            # suffix) instead of silently dropping the date. Only the
            # expected parse/shape errors are caught; anything else
            # propagates.
            continue
        if started is None:
            continue
        start_dt = datetime.datetime.combine(
            started, datetime.time.min, tzinfo=datetime.timezone.utc)
        if start_dt < earliest:
            earliest = start_dt
    return earliest


def _provider_spellings(provider, registry=None):
    """Every namespace a provider's call-log rows may carry, lowercased.

    T1-CREDIT-FIX-7 R1: the gateway bills under the connection id
    (`providers.<id>.omniroute_id` -- `vertex` for `vertex_ai`), not the
    registry id, and model spellings carry the same namespace
    (`vertex/...`, `ovh/...` via `model_prefix`). All three are read from
    the data, never a name list, so a renamed connection is covered the
    moment its row lands. Without a registry only the id itself matches
    (the old behaviour exactly)."""
    spellings = {str(provider).strip().lower()}
    entry = ((registry or {}).get("providers") or {}).get(provider)
    if isinstance(entry, dict):
        for key in ("omniroute_id", "model_prefix"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                spellings.add(val.strip().lower())
    return spellings


def is_spend_row(row, provider=SPEND_PROVIDER, registry=None):
    """True when the gateway billed this row to the watched provider.

    Matches the registry id, its `omniroute_id`/`model_prefix`, and rows
    with no provider of their own whose model is namespaced `<any of those>/...`
    -- a `vertex/...` model billed under either spelling is vertex_ai's money
    either way. A row whose provider field names a DIFFERENT (non-empty,
    non-matching) provider never counts via its model namespace
    (T1-CREDIT-FIX-8 C2: an `openrouter` row for `deepseek/...` is OpenRouter's
    money -- billing it to DeepSeek held the paid leg on spend elsewhere). A
    bare model under another provider (`gemini-3.8-flash` via AI-Studio) and a
    lookalike namespace (`vertexish/...`) match nothing: the comparison is
    exact per namespace, never a substring, case-insensitive on both sides."""
    if not isinstance(row, dict):
        return False
    spellings = _provider_spellings(provider, registry)
    prov = str(row.get("provider") or "").strip().lower()
    if prov in spellings:
        return True
    if prov:
        return False
    model = row.get("model")
    if isinstance(model, str) and "/" in model:
        if model.split("/", 1)[0].strip().lower() in spellings:
            return True
    return False


def cache_read_price_for(model, registry):
    """USD per token for a row's `cacheRead` tokens, or None.

    T1-CREDIT-FIX-10 M4 (D-253): reads the model-level `price_cache_read`
    (10 % of `price_in` on the priced paid models) with the same resolution
    as `price_for` -- the exact model id first, then the tail after the last
    `/` (a `deepseek/...` gateway spelling resolves to the bare registry
    row). Anything absent, non-numeric or non-positive reads as unpriced
    (None): the row's whole input then bills at `price_in`, the old
    behaviour exactly.
    """
    if not model or not isinstance(registry, dict):
        return None
    models = registry.get("models")
    if not isinstance(models, dict):
        return None
    entry = models.get(model)
    if entry is None and "/" in model:
        entry = models.get(model.rsplit("/", 1)[1])
    if not isinstance(entry, dict):
        return None
    value = entry.get("price_cache_read")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


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
    unreadable = 0
    unpriced = set()
    for r in rows:
        if not is_spend_row(r, provider, registry):
            continue
        ts = row_timestamp(r)
        if ts is not None and ts < since:
            continue
        tokens = r.get("tokens") if isinstance(r, dict) else None
        # T1-CREDIT-FIX-7 R4: a row with present-but-unmeasurable token
        # fields degrades only itself -- skipped and counted, never billed
        # as $0 and never aborting the whole guard map.
        tin, tout, readable = _readable_tokens(tokens)
        if not readable:
            unreadable += 1
            continue
        calls += 1
        tokens_in += tin
        tokens_out += tout
        pair = price_for(r.get("model"), prices, provider=provider,
                         registry=registry)
        if not pair:
            if r.get("model"):
                unpriced.add(r.get("model"))
            continue
        # No parseable timestamp means an in-flight row: bill it at full price.
        factor = resolver.price_factor(provider, window_source, ts) if ts else 1.0
        # T1-CREDIT-FIX-10 M4 (D-253): a row's `cacheRead` tokens bill at the
        # model's `price_cache_read`, the REST of the input at `price_in`.
        # Real call-log row has tokens {in 51131, out 3608, cacheRead 23793, ...} and 179.4M cacheRead of 184.4M in total, so `in` INCLUDES cacheRead (bill in-cacheRead at price_in, cacheRead at price_cache_read).
        # The cached count is clamped into [0, in] -- a row that claims more
        # cached tokens than input tokens bills the input as fully cached,
        # never negative.
        cached = 0
        if isinstance(tokens, dict):
            cached = _as_int(tokens.get("cacheRead"))
            cached = min(max(cached, 0), tin)
        cache_price = cache_read_price_for(r.get("model"), registry)
        if cache_price is not None and cached:
            spend += ((tin - cached) * pair[0] + cached * cache_price
                      + tout * pair[1]) * factor
        else:
            spend += (tin * pair[0] + tout * pair[1]) * factor

    spend = round(spend, 6)
    warnings = []
    # The warn line is the provider's own guard when it declares one (a credit
    # grant warns at 80 % of what the operator funded); a row with no fraction
    # keeps the legacy DSGUARD line, so deepseek reports exactly what it did.
    warn_usd = spend_warn_usd(registry, provider)
    if spend >= warn_usd:
        warnings.append("%s spend $%.2f since %s reached the %.2f USD warn line"
                        % (SPEND_PROVIDER_LABEL, spend,
                           since.strftime("%Y-%m-%dT%H:%M:%SZ"), warn_usd))
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
        "threshold_usd": warn_usd,
        "balance_usd": balance,
        "balance_threshold_usd": BALANCE_FLOOR_USD,
        "models_unpriced": len(unpriced),
        "rows_unreadable": unreadable,
        "price_source": price_source,
        "complete": bool(complete),
        "warnings": warnings,
    }


def paid_local_cap_usd(registry):
    """USD cap an UNMEASURED paid leg is held to (T1-CREDIT-FIX-10 M2, D-240).

    `policy.paid_local_cap_usd`, default `PAID_LOCAL_CAP_DEFAULT_USD` when
    absent. Total: a malformed value reads as the default (loudly flagged by
    `registry.py check`), never a crash on the routing path.
    """
    policy = (registry or {}).get("policy")
    raw = policy.get("paid_local_cap_usd", PAID_LOCAL_CAP_DEFAULT_USD) \
        if isinstance(policy, dict) else PAID_LOCAL_CAP_DEFAULT_USD
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
            or not math.isfinite(raw) or raw <= 0:
        return float(PAID_LOCAL_CAP_DEFAULT_USD)
    return float(raw)


def local_paid_estimate(rows, registry, since, provider=SPEND_PROVIDER):
    """Best-effort month spend over LOCAL rows (T1-CREDIT-FIX-10 M2, D-240).

    The single home for the D-240 estimate: the same rows the usage report
    reads (the gateway call log IS the ledger -- there is no second local
    store), priced by the same `paid_spend` (registry list price via
    `leg_price`/`provider_prices`, 10 % cache billing included), over
    whatever rows are at hand -- even a truncated page. A run record without
    token counts adds $0 and is counted in `unpriced_runs`. Never raises on
    row shapes (it reuses the total readers); returns
    `{"provider", "spend_usd", "unpriced_runs"}`.
    """
    prices = prices_from_registry(registry)
    spend = paid_spend(rows or [], prices, registry, since, provider=provider)
    unpriced = 0
    for r in (rows or []):
        if not is_spend_row(r, provider, registry):
            continue
        ts = row_timestamp(r)
        if ts is not None and ts < since:
            continue
        tokens = r.get("tokens") if isinstance(r, dict) else None
        if not isinstance(tokens, dict) \
                or (tokens.get("in") is None and tokens.get("out") is None):
            unpriced += 1
    return {"provider": provider, "spend_usd": spend["spend_usd"],
            "unpriced_runs": unpriced}


def apply_paid_local_cap(registry, guards, rows, since):
    """Hold UNMEASURED paid guards to the local cap (T1-CREDIT-FIX-10 M2).

    For every `tier == paid` guard in state `unknown` (measured spend is
    unavailable -- gateway down, truncated fetch, unreadable rows): the
    local estimate over `rows` decides. At or above `paid_local_cap_usd`
    the leg is REFUSED with the D-240 reason (the state flips to `refuse`,
    so the resolver's paid block refuses it); below, the leg is kept with
    the exact D-240 kept line appended to the existing note (which keeps
    naming the underlying cause: fetch truncation, unreadable rows, or the
    D-212 last resort). Measured states (`ok`/`warn`/`refuse`/`manual`) and
    `guard error` are untouched -- measured spend governs, and an unforeseen
    bug keeps its own state. Returns `guards` (mutated in place).

    Every guard this touches carries `local_estimate: True`, so the balance
    overlay can rank it as UNKNOWN (D-240/D-253: the estimate applies only
    while spend is unmeasured; measured spend governs).
    """
    for pid, guard in (guards or {}).items():
        if not isinstance(guard, dict):
            continue
        if guard.get("state") != "unknown" or not guard.get("spend_unknown"):
            continue
        entry = ((registry or {}).get("providers") or {}).get(pid)
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        try:
            monthly_cap_usd(registry, pid)
        except Exception:
            continue  # a paid row with no readable cap is not guarded at all
        local_cap = paid_local_cap_usd(registry)
        est = local_paid_estimate(rows, registry, since, pid)
        amount = est["spend_usd"]
        # The D-240 line stays terminal in the note (and the whole note when
        # there is nothing else to say), so the kept/refused line reads
        # exactly as specified; an unpriced-runs count, when nonzero, precedes
        # it rather than trailing it.
        unpriced = "" if not est["unpriced_runs"] else \
            "; unpriced runs: %d" % est["unpriced_runs"]
        if amount >= local_cap:
            guard["state"] = "refuse"
            guard["spend_unknown"] = False
            guard["spend_usd"] = amount
            guard["local_estimate"] = True
            guard["note"] = ("%spaid spend unmeasured - local estimate $%.2f "
                             ">= $%g cap (D-240)"
                             % (("unpriced runs: %d; " % est["unpriced_runs"]
                                 if est["unpriced_runs"] else ""),
                                amount, local_cap))
        else:
            guard["spend_usd"] = amount
            guard["local_estimate"] = True
            guard["note"] = ("%s%s; paid spend unmeasured - leg kept, local "
                             "estimate $%.2f of $%g (D-240)"
                             % (guard.get("note") or "spend unmeasured",
                                unpriced, amount, local_cap))
    return guards


def balance_ledger_path(env=None):
    """Path of the provider-balance ledger: one JSON line per reading.

    T1-CREDIT-FIX-10 M4 (D-253): the ledger lives in the spawner state dir --
    the same contract as `autoos_clients.state_dir` (`AUTOOS_STATE_DIR`, else
    the repository's git-ignored `logs/`), under `routing/` beside the track
    record. Never under the repo tree elsewhere (AGENTS.md rule 1 keeps
    machine readings out of tracked files; `logs/` is git-ignored).
    """
    env = os.environ if env is None else env
    base = (env or {}).get("AUTOOS_STATE_DIR")
    if not base:
        base = str(ROOT / "logs")
    return os.path.join(base, BALANCE_LEDGER_REL)


def _match_balance_provider(plan, registry):
    """Registry provider id for a balance `plan` name, or None.

    The gateway names the display plan (`DeepSeek`); the registry names the
    id (`deepseek`). The match is exact case-insensitive over the id and the
    provider's own spellings (`omniroute_id`, `model_prefix`) -- the same
    namespaces `is_spend_row` bills under -- so `Vertex AI` never reads as
    `vertex_ai` and an unknown plan matches nothing instead of someone's
    money.
    """
    if not isinstance(plan, str) or not plan.strip():
        return None
    want = plan.strip().lower()
    providers = (registry or {}).get("providers") or {}
    for pid, entry in providers.items():
        names = {str(pid).lower()}
        if isinstance(entry, dict):
            for key in ("omniroute_id", "model_prefix"):
                val = entry.get(key)
                if isinstance(val, str) and val.strip():
                    names.add(val.strip().lower())
        if want in names:
            return pid
    return None


def parse_provider_limits(payload, registry=None):
    """Credit-balance readings from a provider-limits payload.

    T1-CREDIT-FIX-10 M4 (D-253): reads ``caches.<id>.quotas.credits_usd``
    (`{remaining, toppedUpBalance, grantedBalance, currency}`) with the
    provider from `plan` and the snapshot time from `fetchedAt`. Entries
    without a `credits_usd` quota (model quotas, spend meters, null plans)
    carry no prepaid balance and yield no reading. Never raises: a
    mis-shaped entry is skipped, and an unreadable payload reads as no
    readings. Returns a list of
    `{"cache", "provider", "remaining", "currency", "fetched_at"}`.
    """
    out = []
    try:
        caches = (payload or {}).get("caches") or {}
    except Exception:
        return out
    if not isinstance(caches, dict):
        return out
    for cid, entry in caches.items():
        if not isinstance(entry, dict):
            continue
        pid = _match_balance_provider(entry.get("plan"), registry)
        if pid is None:
            continue
        try:
            quotas = entry.get("quotas") or {}
            credits = quotas.get("credits_usd") or {}
        except Exception:
            continue
        if not isinstance(credits, dict):
            continue
        remaining = credits.get("remaining")
        if isinstance(remaining, bool) \
                or not isinstance(remaining, (int, float)) \
                or not math.isfinite(remaining):
            continue
        fetched_at = entry.get("fetchedAt")
        out.append({"cache": cid, "provider": pid,
                    "remaining": float(remaining),
                    "currency": credits.get("currency")
                    if isinstance(credits.get("currency"), str) else None,
                    "fetched_at": fetched_at
                    if isinstance(fetched_at, str) else None})
    return out


def load_balance_readings(path):
    """Every well-formed balance reading; a missing file is empty, malformed
    lines are skipped (the track record's `load` contract, one home per
    shape -- this one carries provider/remaining/fetched_at). A line keyed
    `fetchedAt` (the gateway's spelling) normalises to `fetched_at` on load,
    so hand-written seeds and recorded lines dedupe against each other.
    A line whose `remaining` is non-numeric or non-finite is skipped too
    (never crashing the overlay). STALE/clear markers (T1-CREDIT-FIX-14,
    D-274 -- lines carrying a `stale` key) are NOT readings and are never
    returned here; read them with `load_balance_stale`. Skipped lines are
    not counted anywhere --
    callers that need the exact surviving set assert on the returned list
    (and on the guard the overlay builds from it), not on a count."""
    readings = []
    try:
        # errors="replace" (D-274 rework): a non-UTF-8 ledger file is garbage
        # input, not a crash -- the undecodable line is skipped like any bad
        # line, so the read never raises and the guard fails closed downstream.
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return readings
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or "stale" in obj:
                continue
            if not isinstance(obj.get("provider"), str):
                continue
            stamped = obj.get("fetched_at")
            if not isinstance(stamped, str):
                stamped = obj.get("fetchedAt")
            if not isinstance(stamped, str):
                continue
            remaining = obj.get("remaining")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
                continue
            if not math.isfinite(float(remaining)):
                continue
            obj = dict(obj)
            obj["fetched_at"] = stamped
            readings.append(obj)
    return readings


def record_balance_readings(path, readings):
    """Append every new reading; dedupe on (provider, fetched_at).

    Returns the number of lines added. Total on malformed input, loud on an
    unwritable file (OSError propagates: a meter that cannot record must not
    silently govern).
    """
    seen = {(r.get("provider"), r.get("fetched_at"))
            for r in load_balance_readings(path)}
    fresh = []
    for r in (readings or []):
        if not isinstance(r, dict):
            continue
        key = (r.get("provider"), r.get("fetched_at"))
        if key in seen:
            continue
        seen.add(key)
        fresh.append(r)
    if not fresh:
        return 0
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in fresh:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            seen.add((r.get("provider"), r.get("fetched_at")))
    return len(fresh)


def _stale_state(path):
    """(global_since, {provider: since}) STALE markers in effect (D-274).

    One scan, one home for marker semantics: a `{"stale": true}` line with
    no `provider` stales every paid guard (the pre-rework global marker,
    still written for total read failures); one WITH a `provider` stales
    only that provider (a successful parse that carried no reading for a
    provider with a series). A `{"stale": false}` line clears its own scope
    only -- a global clear never clears a per-provider mark and vice versa.
    Undecodable bytes are replaced, never raised (see `load_balance_readings`).
    """
    global_since = None
    per = {}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None, {}
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or "stale" not in obj:
                continue
            pid = obj.get("provider")
            if not isinstance(pid, str) or not pid:
                pid = None
            if obj.get("stale") is True:
                since_val = obj.get("since")
                if not isinstance(since_val, str):
                    # CREDIT-16 C1 (D-274): a `stale: true` marker whose
                    # `since` is not a string is still STALE (fail closed),
                    # with a placeholder since -- never skipped (fail open).
                    since_val = "unknown"
                if pid is None:
                    if global_since is None:
                        global_since = since_val
                else:
                    per.setdefault(pid, since_val)
            elif obj.get("stale") is False:
                if pid is None:
                    global_since = None
                else:
                    per.pop(pid, None)
    return global_since, per


def load_balance_stale(path, provider=None):
    """The ledger's STALE `since` timestamp, or None when not stale.

    T1-CREDIT-FIX-14 (D-274): the provider-balance ledger fails closed. A
    failed balance read appends a `{"stale": true, "since": <UTC iso>}`
    marker (see `_mark_balance_stale`); the next successful read appends
    `{"stale": false, ...}` to clear it. Markers are ledger control lines,
    never readings (`load_balance_readings` skips them). A missing or
    unreadable file is not stale (None); malformed lines are skipped.

    Rework (per-provider STALE): a marker may carry a `provider` id, in
    which case it stales only that provider -- clearing needs a successful
    reading for THAT provider. With `provider` given, a global marker still
    wins (it stales everyone); without one, an old global marker with no
    provider id counts as stale for every paid guard until a reading
    arrives, else the earliest in-effect per-provider `since` is returned.
    """
    glob, per = _stale_state(path)
    if provider is not None:
        if glob is not None:
            return glob
        return per.get(provider)
    if glob is not None:
        return glob
    if not per:
        return None
    return sorted(per.values())[0]


def _utc_iso_z(moment):
    """Aware UTC datetime -> `YYYY-MM-DDTHH:MM:SSZ` (ledger stamp spelling)."""
    return moment.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _mark_balance_stale(path, candidate_since, now=None, provider=None):
    """Record STALE, keeping the first failure's `since` (D-274).

    Appends `{"stale": true, "since": ...}` (plus `provider` when marking
    one provider's missed read) only when that scope is not already stale,
    so retries never move the window forward; returns the `since` the
    caller's refuse reason must name (the persisted one, or the candidate
    when this call marks it). A marker write failure (OSError)
    propagates -- the caller still refuses for that call, fail closed."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    glob, per = _stale_state(path)
    if provider is None:
        if glob is not None:
            return glob
    elif per.get(provider) is not None:
        return per[provider]
    # CREDIT-16 C3 (D-274): a per-provider marker created while a GLOBAL
    # marker is active keeps the global since (not now) -- one window.
    if provider is not None and glob is not None and not candidate_since:
        since = glob
    else:
        since = candidate_since or _utc_iso_z(now)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    marker = {"since": since, "stale": True}
    if provider is not None:
        marker["provider"] = provider
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(marker, sort_keys=True) + "\n")
    return since


def _clear_balance_stale(path, now=None, provider=None):
    """Clear STALE after a successful read (D-274); idempotent.

    Appends one `{"stale": false, ...}` clear marker (scoped to `provider`
    when given, else global) only when that scope is currently stale, so
    running the successful read twice writes the marker once and never
    touches the readings. Returns True when a marker was written. A clear
    write failure (OSError) propagates -- the caller refuses for that call,
    fail closed."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    glob, per = _stale_state(path)
    if provider is None:
        if glob is None:
            return False
    elif per.get(provider) is None:
        return False
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    marker = {"at": _utc_iso_z(now), "stale": False}
    if provider is not None:
        marker["provider"] = provider
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(marker, sort_keys=True) + "\n")
    return True


def _stale_paid_refuse(registry, provider, guard, since, detail):
    """Every paid guard while the ledger is STALE: `refuse` (D-274).

    `spend_usd` is carried as is; the note IS the reason (`balance stale
    since <ts> (D-274)` plus the detail), so the resolver surfaces it
    verbatim and no paid leg is selectable. Never governs from the last
    good reading."""
    refused = dict(guard) if isinstance(guard, dict) else {}
    refused["provider"] = provider
    refused["state"] = "refuse"
    refused["note"] = ("balance stale since %s (D-274) - %s"
                       % (since, detail))
    return refused


def _mark_missing_provider_reads(registry, guards, path, readings, ledger,
                                 since, now, all_readings=None):
    """STALE the paid providers this successful parse did NOT read (D-274).

    A parse that yields readings is a successful read -- but only for the
    providers it names. A paid provider with an in-month balance series
    (`since`-filtered, the same window the meter judges) and no reading in
    this payload had its read FAIL: its marker is recorded (first failure's
    `since` kept) and its guard refuses, while every other provider is
    unaffected. A paid provider with no series at all and no reading is
    untouched (no history to distrust -- behaves as before). Returns the
    refused provider ids so the caller never merges a meter over them.
    A marker write failure (OSError) propagates -- fail closed."""
    # T1-CREDIT-FIX-14 rework 2 MAJOR-1 (D-274): only a STAMPED reading
    # counts as read -- an entry with a missing or garbage fetchedAt parses
    # but is dropped on load, so it must not shield its provider here.
    fresh_ids = {r.get("provider") for r in (readings or [])
                 if isinstance(r, dict) and _reading_ts(r) is not None}
    series_ids = set()
    for r in (ledger or []):
        if not isinstance(r, dict):
            continue
        ts = _reading_ts(r)
        if ts is not None and ts >= since:
            series_ids.add(r.get("provider"))
    # T1-CREDIT-FIX-14 rework 3 MAJOR (D-274): a persisted per-provider
    # marker is honoured even after that provider's in-month series ages
    # out (month rollover) -- only that provider's own usable reading
    # clears its marker, so a marked provider missing from this payload
    # stays refused with its ORIGINAL since, series or no series.
    # CREDIT-16 (D-274): `readings` is the FRESH set (see
    # `overlay_balance_guards`); `all_readings` (when given) is the full
    # stamped payload, so a provider present-but-not-fresh (replayed/old
    # stamp or past-month) refuses as an old reading, not as missing.
    # CREDIT-17: a provider present in the payload but with an unparseable
    # or missing stamp is present-but-unusable -- it must be treated as a
    # failed read and staled (if paid with no series), not ignored.
    glob_before, already_stale = _stale_state(path)
    if all_readings is None:
        present_ids = set(fresh_ids)
    else:
        # Present = any entry in the payload, regardless of stamp validity.
        # Fresh = only entries with a valid stamp (fresh_ids).
        # A provider present but not fresh (invalid/missing stamp or old
        # stamp) must be treated as a failed read.
        present_ids = {r.get("provider") for r in (all_readings or [])
                       if isinstance(r, dict) and r.get("provider")}
    newly = []
    for pid, guard in (guards or {}).items():
        if not isinstance(guard, dict):
            continue
        entry = ((registry or {}).get("providers") or {}).get(pid)
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        if pid in fresh_ids:
            continue
        # CREDIT-16 R1 (D-274): a provider PRESENT in the payload but NOT
        # fresh (old/past-month stamp) never governs -- it stales even with
        # no in-month series. Only a provider absent from the payload AND
        # without series stays untouched.
        if pid not in series_ids and pid not in already_stale and pid not in present_ids:
            continue
        since_ts = _mark_balance_stale(path, None, now, pid)
        # CREDIT-16 C4 (D-274): when global and per-provider markers both
        # apply, the reason names the OLDEST applicable since.
        _glob_now, _per_now = _stale_state(path)
        candidates = [since_ts]
        if glob_before is not None:
            candidates.append(glob_before)
        per_now = _per_now.get(pid)
        if per_now is not None:
            candidates.append(per_now)
        try:
            since_ts = sorted(candidates)[0]
        except TypeError:
            pass
        if pid in present_ids:
            detail = "old reading for provider balance, paid leg refused"
        else:
            detail = "no provider balance reading, paid leg refused"
        guards[pid] = _stale_paid_refuse(
            registry, pid, guard, since_ts,
            detail)
        newly.append(pid)
    return newly


def refuse_paid_on_overlay_error(registry, guards, type_name):
    """Every paid guard refuses: the balance overlay itself raised (D-274).

    The plan call site fails closed on ANY overlay exception -- expected or
    not. `type_name` is the exception TYPE NAME only, never the message
    (which can carry paths or gleamed gateway text). Credit (trial grant)
    guards are untouched -- their fail-open contract stands. Total: never
    raises. Returns `guards` (mutated in place)."""
    for pid, guard in (guards or {}).items():
        if not isinstance(guard, dict):
            continue
        entry = ((registry or {}).get("providers") or {}).get(pid)
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        refused = dict(guard)
        refused["provider"] = pid
        refused["state"] = "refuse"
        refused["spend_unknown"] = False
        refused["note"] = ("balance overlay failed (%s) (D-274) - "
                           "paid leg refused" % (type_name,))
        guards[pid] = refused
    return guards


def refuse_paid_without_balance_read(registry, guards, env, now=None):
    """Every paid guard refuses: no balance read was possible (D-274).

    The plan call site consults the ledger OUTSIDE the rows gate: when the
    call-log rows read failed the overlay never ran, so a persisted STALE
    marker would never be consulted and -- with the helper down -- no fresh
    read was even attempted. Either way every `tier == paid` guard refuses
    with the stale reason (STALE beats the D-240 local estimate), and a
    first-failure STALE marker is recorded with the current stamp when none
    persists, so the `since` the reason names is stable across retries. A
    marker write failure still refuses for this call (fail closed). Credit
    (trial grant) guards are untouched. No paid guards: no write, no change.
    Returns `guards` (mutated in place)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    entries = (registry or {}).get("providers") or {}
    if not any(isinstance(guard, dict)
               and isinstance(entries.get(pid), dict)
               and entries[pid].get("tier") == "paid"
               for pid, guard in (guards or {}).items()):
        return guards
    path = balance_ledger_path(env)
    try:
        since_ts = _mark_balance_stale(path, None, now)
    except OSError:
        since_ts = (load_balance_stale(path) or _utc_iso_z(now))
    for pid, guard in (guards or {}).items():
        if not isinstance(guard, dict):
            continue
        entry = entries.get(pid)
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        guards[pid] = _stale_paid_refuse(
            registry, pid, guard, since_ts,
            "provider balance unreadable, paid leg refused")
    return guards


def _reading_ts(reading):
    """Aware UTC datetime of a reading's `fetched_at`; None when unparseable."""
    if not isinstance(reading, dict):
        return None
    return row_timestamp({"timestamp": reading.get("fetched_at")})


def balance_month_spend(readings, provider, since):
    """(spend, topups) for one provider over readings at/after `since`.

    T1-CREDIT-FIX-10 M4 (D-253): the month's measured spend is the sum of
    DECREASES between consecutive in-month readings (oldest first); an
    increase is a top-up -- excluded from spend and reported separately, so a
    refill never reads as negative spend. Unparseable timestamps are skipped,
    never interpolated.
    """
    mine = [(ts, float(r.get("remaining") or 0.0))
            for r in (readings or [])
            for ts in [_reading_ts(r)]
            if isinstance(r, dict) and r.get("provider") == provider
            and ts is not None and ts >= since]
    mine.sort(key=lambda pair: pair[0])
    spend = topups = 0.0
    for (_, prev), (_, cur) in zip(mine, mine[1:]):
        delta = prev - cur
        if delta >= 0:
            spend += delta
        else:
            topups -= delta
    return round(spend, 6), round(topups, 6)


def balance_paid_guard(registry, provider, readings, since, now=None):
    """The paid guard from the provider-balance series, or None.

    T1-CREDIT-FIX-10 M4 (D-253): with two or more in-month readings the
    scheduled balance IS the paid meter -- `balance_month_spend` judged by
    `spend_guard` against the provider's own cap, so the $20 alert and the
    $25 refuse are the registry's numbers, not second ones. A fresh snapshot
    (within `BALANCE_FRESH_S`) below `BALANCE_EXHAUSTED_USD` refuses with
    the exhausted reason regardless of the month figure, so routing falls
    back per the leg order. A top-up in-month is logged, not billed. Fewer
    than two readings is not a spend meter (None -- the caller falls back to
    the call ledger, then D-240), but a fresh snapshot below the floor still
    refuses on its own.
    The note always names `measured via provider balance`. None on a
    provider that cannot state its cap either (ValueError is a config error
    the caller already handles, not a figure).
    T1-CREDIT-FIX-14 (D-274): a reading stamped later than now +
    `BALANCE_CLOCK_SKEW_S` is future-dated -- never fresh, never the latest
    reading. Any such reading makes the ledger stale: `refuse` with the
    `balance stale since <ts> (D-274)` reason naming that reading's stamp.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    mine = [r for r in (readings or [])
            if isinstance(r, dict) and r.get("provider") == provider
            and _reading_ts(r) is not None and _reading_ts(r) >= since]
    if not mine:
        return None
    horizon = now + datetime.timedelta(seconds=BALANCE_CLOCK_SKEW_S)
    future = sorted(
        (r for r in mine if _reading_ts(r) > horizon),
        key=_reading_ts)
    if future:
        # The future stamp is untrustworthy ordering, not a snapshot: it
        # must not satisfy `fresh` below and must not become `latest`, so
        # the whole provider series reads stale. Spend is carried from the
        # trustworthy readings alone.
        trusted = [r for r in mine if _reading_ts(r) <= horizon]
        if len(trusted) >= 2:
            spend, topups = balance_month_spend(trusted, provider, since)
        else:
            spend, topups = 0.0, 0.0
        try:
            cap = monthly_cap_usd(registry, provider)
            warn = spend_warn_usd(registry, provider)
        except (ValueError, TypeError, AttributeError):
            cap, warn = 0.0, 0.0
        guard = {"provider": provider, "state": "refuse",
                 "spend_usd": spend, "spend_unknown": False,
                 "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                 "note": ("balance stale since %s (D-274) - future-dated "
                          "provider balance reading, paid leg refused"
                          % future[0].get("fetched_at"))}
        if topups > 0:
            guard["note"] += "; top-up +$%.2f" % topups
        return guard
    try:
        cap = monthly_cap_usd(registry, provider)
        warn = spend_warn_usd(registry, provider)
    except (ValueError, TypeError, AttributeError):
        return None
    latest = max(mine, key=_reading_ts)
    latest_ts = _reading_ts(latest)
    latest_remaining = latest.get("remaining")
    fresh = latest_ts is not None and \
        (now - latest_ts).total_seconds() <= BALANCE_FRESH_S
    # The exhausted check runs on ANY fresh snapshot -- even the first
    # sighting: a prepaid balance of $2.50 refuses the leg whether or not a
    # month series exists yet. A stale snapshot is history, not a meter, and
    # never refuses on its own.
    if fresh and isinstance(latest_remaining, (int, float)) \
            and not isinstance(latest_remaining, bool) \
            and float(latest_remaining) < BALANCE_EXHAUSTED_USD:
        spend, topups = balance_month_spend(readings, provider, since) \
            if len(mine) >= 2 else (0.0, 0.0)
        guard = {"provider": provider, "state": "refuse",
                 "spend_usd": spend, "spend_unknown": False,
                 "cap_usd": cap, "warn_usd": warn, "models_unpriced": 0,
                 "note": ("paid %s balance exhausted ($%.2f left) - spend "
                          "refused [measured via provider balance]"
                          % (provider, float(latest_remaining)))}
        if topups > 0:
            guard["note"] += "; top-up +$%.2f" % topups
        return guard
    if len(mine) < 2:
        return None
    spend, topups = balance_month_spend(readings, provider, since)
    try:
        state, note = spend_guard(registry, provider, spend)
    except (ValueError, TypeError, AttributeError):
        return None
    latest = max(mine, key=_reading_ts)
    bal_line = ("provider balance $%.2f remaining (fetched %s)"
                % (float(latest.get("remaining") or 0.0),
                   latest.get("fetched_at")))
    note = "%s; %s [measured via provider balance]" % (note, bal_line)
    if topups > 0:
        note += "; top-up +$%.2f" % topups
    guard = {"provider": provider, "state": state, "spend_usd": spend,
             "spend_unknown": False, "cap_usd": cap, "warn_usd": warn,
             "models_unpriced": 0, "note": note}
    return guard


def _balance_guard_rank(state):
    """Worse-wins rank for the ledger/balance merge (T1-CREDIT-FIX-11 R2).

    refuse > warn > ok > unknown (unknown-like states rank lowest, so a
    measured figure always governs over unmeasured -- the same ordering the
    call-ledger guards already use where measured spend at/over the cap
    beats unknown/truncated/unreadable).
    """
    order = {"refuse": 4, "warn": 3, "ok": 2, "unknown": 1, "manual": 1,
             "guard error": 1}
    return order.get(state, 0)


def overlay_balance_guards(registry, guards, gateway, helper_fetch_fn,
                           env, since, now=None):
    """Overlay the provider-balance paid meter on a guard map.

    T1-CREDIT-FIX-10 M4 (D-253): one read-only `GET /api/usage/provider-limits`
    through the helper transport; every new reading is recorded in the
    git-ignored state-dir ledger (dedupe on provider+fetchedAt), and each
    `tier == paid` guard with an in-month balance series is replaced by
    `balance_paid_guard` (decreases billed, top-ups logged, fresh exhausted
    snapshots refused). A paid guard with no series keeps its ledger figure,
    named `measured via call ledger`; an unmeasured (D-240) one is untouched.
    T1-CREDIT-FIX-14 (D-274): the ledger FAILS CLOSED. ANY failed read --
    helper `UsageError`/`OSError`/`ValueError`, status != 200, unparseable
    or non-dict body, or a 200 that yields no readings -- marks the ledger
    STALE (a control marker in the same git-ignored ledger file; the first
    failure's `since` is kept while stale persists) and every `tier ==
    paid` guard in the map becomes `refuse` with the `balance stale since
    <ts> (D-274)` reason -- no paid leg selectable, never governed from the
    last good reading. STALE beats the D-240 local estimate: the estimate
    is a fallback only while spend is unmeasured with no stale logic
    involved; a stale ledger refuses instead of keeping the capped estimate.
    The next successful read (200, parseable dict, >= 1 reading) clears
    STALE and the normal balance/ledger logic resumes. A ledger write
    failure (OSError) refuses for that call, fail closed, never open.
    A future-dated `fetched_at` (later than now + `BALANCE_CLOCK_SKEW_S`)
    is stale, never fresh. A successful parse stales per provider: a paid
    provider with a series but no reading in this payload is refused on its
    own mark (cleared only by that provider's reading); the other providers
    are unaffected. Returns `guards` (mutated in place).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    path = balance_ledger_path(env)

    def _refuse_stale(detail_since, detail):
        try:
            since_ts = _mark_balance_stale(path, detail_since, now)
        except OSError:
            since_ts = (detail_since or load_balance_stale(path)
                        or _utc_iso_z(now))
        for pid, guard in (guards or {}).items():
            if not isinstance(guard, dict):
                continue
            entry = ((registry or {}).get("providers") or {}).get(pid)
            if not isinstance(entry, dict) or entry.get("tier") != "paid":
                continue
            guards[pid] = _stale_paid_refuse(
                registry, pid, guard, since_ts, detail)
        return guards

    try:
        status, body = helper_fetch_fn(
            gateway.rstrip("/") + "/api/usage/provider-limits", None, TIMEOUT_S)
    except (UsageError, OSError, ValueError):
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    if status != 200:
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    try:
        payload = json.loads(
            body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, UnicodeDecodeError):
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    if not isinstance(payload, dict):
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    readings = parse_provider_limits(payload, registry)
    # T1-CREDIT-FIX-14 rework 2 MAJOR-1 (D-274): a reading without a
    # parseable stamp is unusable (the loader drops it) -- a payload with NO
    # usable reading at all is an empty payload: STALE, never clearing.
    usable = [r for r in (readings or [])
              if isinstance(r, dict) and _reading_ts(r) is not None]
    if not usable:
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    try:
        # CREDIT-16 A (D-274): previous latest per provider BEFORE append.
        # A payload reading is FRESH only when its stamp is NOT older than
        # that previous latest AND in the current month (stamp >= since).
        # Equal stamp is fresh (idempotent re-read). Future never fresh.
        prev_latest = {}
        for _r in load_balance_readings(path):
            if not isinstance(_r, dict):
                continue
            _ts = _reading_ts(_r)
            _pid = _r.get("provider")
            if _ts is None or not isinstance(_pid, str):
                continue
            if _pid not in prev_latest or _ts > prev_latest[_pid]:
                prev_latest[_pid] = _ts
        record_balance_readings(path, readings)
    except OSError:
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    ledger = load_balance_readings(path)
    horizon = now + datetime.timedelta(seconds=BALANCE_CLOCK_SKEW_S)
    # CREDIT-16 A: fresh set shields from STALE and clears markers.
    fresh_usable = []
    for _r in usable:
        if not isinstance(_r, dict):
            continue
        _ts = _reading_ts(_r)
        _pid = _r.get("provider")
        if _ts is None or not isinstance(_pid, str):
            continue
        if _ts > horizon:
            continue
        if _ts < since:
            continue
        _prev = prev_latest.get(_pid)
        if _prev is not None and _ts < _prev:
            continue
        fresh_usable.append(_r)
    # CREDIT-16 C5 (D-274): a far-future stamp stales THAT provider only
    # (per-provider marker), never the whole ledger.
    _future_by_pid = {}
    for _r in ledger:
        if not isinstance(_r, dict):
            continue
        _ts = _reading_ts(_r)
        _pid = _r.get("provider")
        if _ts is None or not isinstance(_pid, str):
            continue
        if _ts > horizon:
            _future_by_pid.setdefault(_pid, []).append(_r)
    _future_saved = {}
    for _pid in sorted(_future_by_pid):
        _entry = ((registry or {}).get("providers") or {}).get(_pid)
        if not isinstance(_entry, dict) or _entry.get("tier") != "paid":
            continue
        _lst = sorted(_future_by_pid[_pid], key=_reading_ts)
        _stamp = _lst[0].get("fetched_at")
        if not isinstance(_stamp, str):
            _stamp = None
        try:
            _since_ts = _mark_balance_stale(path, _stamp, now, _pid)
        except OSError:
            try:
                _since_ts = (load_balance_stale(path, _pid)
                             or _stamp or _utc_iso_z(now))
            except Exception:
                _since_ts = _stamp or _utc_iso_z(now)
        _glob_now, _per_now = _stale_state(path)
        _cands = [_since_ts]
        if _glob_now is not None:
            _cands.append(_glob_now)
        if _per_now.get(_pid) is not None:
            _cands.append(_per_now[_pid])
        try:
            _since_ts = sorted(_cands)[0]
        except TypeError:
            pass
        _g = guards.get(_pid)
        if isinstance(_g, dict):
            guards[_pid] = _stale_paid_refuse(
                registry, _pid, _g, _since_ts,
                "future-dated provider balance reading, paid leg refused")
            _future_saved[_pid] = guards[_pid]
    try:
        newly_stale = _mark_missing_provider_reads(
            registry, guards, path, fresh_usable, ledger, since, now,
            readings)
    except OSError:
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    for _pid, _g in _future_saved.items():
        guards[_pid] = _g
        if _pid not in newly_stale:
            newly_stale.append(_pid)
    try:
        # Only a FRESH reading clears: an old/past-month replay never
        # clears global or per-provider STALE (CREDIT-16 A).
        if fresh_usable:
            _clear_balance_stale(path, now)
            for r in fresh_usable:
                if isinstance(r, dict) and isinstance(r.get("provider"), str):
                    _clear_balance_stale(path, now, r["provider"])
    except OSError:
        return _refuse_stale(None, "provider balance unreadable, "
                                   "paid leg refused")
    for pid, guard in (guards or {}).items():
        if not isinstance(guard, dict):
            continue
        entry = ((registry or {}).get("providers") or {}).get(pid)
        if not isinstance(entry, dict) or entry.get("tier") != "paid":
            continue
        if pid in newly_stale:
            continue  # per-provider STALE above governs, never the old figure
        balanced = balance_paid_guard(registry, pid, ledger, since, now)
        if balanced is not None:
            # T1-CREDIT-FIX-11 R2: the worse of ledger and balance governs
            # (refuse > warn > ok > unknown), spend is the max, the note
            # names both sources when they differ -- a flat $0 balance must
            # never overwrite a $30 ledger refuse with ok/$0.
            # T1-CREDIT-FIX-12 N1 (D-240/D-253): a ledger guard carrying the
            # `local_estimate` marker (set by `apply_paid_local_cap`) ranks
            # as UNKNOWN here -- the estimate applies only while spend is
            # unmeasured, so any measured balance guard wins, its spend
            # governs alone, and its note stands alone. A REAL measured
            # ledger refuse/warn still outranks a balance ok (R2 stands).
            is_estimate = bool(guard.get("local_estimate"))
            ledger_rank = 1 if is_estimate else _balance_guard_rank(
                guard.get("state"))
            if _balance_guard_rank(balanced.get("state")) >= ledger_rank:
                winner, loser = balanced, guard
            else:
                winner, loser = guard, balanced
            try:
                spend_ledger = float(guard.get("spend_usd"))
            except (TypeError, ValueError):
                spend_ledger = 0.0
            try:
                spend_bal = float(balanced.get("spend_usd"))
            except (TypeError, ValueError):
                spend_bal = 0.0
            if not math.isfinite(spend_ledger):
                spend_ledger = 0.0
            if not math.isfinite(spend_bal):
                spend_bal = 0.0
            if is_estimate:
                spend = spend_bal
            else:
                spend = max(spend_ledger, spend_bal)
            note_winner = winner.get("note") or ""
            note_loser = loser.get("note") or ""
            if is_estimate and winner is balanced:
                note = note_winner
            elif note_winner != note_loser and note_winner and note_loser:
                note = "%s; %s" % (loser.get("note"), winner.get("note"))
                # Keep the worse state's note first when the ledger wins.
                if winner is guard:
                    note = "%s; %s" % (guard.get("note"), balanced.get("note"))
            else:
                note = note_winner or note_loser
            merged = dict(winner)
            merged["spend_usd"] = spend
            merged["spend_unknown"] = winner.get("state") in ("unknown", "guard error")
            merged["note"] = note
            try:
                merged["models_unpriced"] = max(int(guard.get("models_unpriced") or 0), int(balanced.get("models_unpriced") or 0))
            except (TypeError, ValueError):
                pass
            guards[pid] = merged
        elif guard.get("state") in ("ok", "warn", "refuse") \
                and "(D-240)" not in (guard.get("note") or "") \
                and "measured via call ledger" not in (guard.get("note") or ""):
            guard["note"] = "%s [measured via call ledger]" % guard.get("note")
    return guards


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


def helper_fetch(url, headers=None, timeout=None, container=HELPER_CONTAINER,
                 _run=None):
    """(status, body bytes) via the gateway's own CLI helper, read-only.

    T1-CREDIT-FIX-10 M1 (D-250): the second fetch transport, next to
    `urllib_fetch`. It runs ``docker exec -w /app <container> node
    --input-type=module -e <fixed script>`` where the fixed script GETs the
    URL's own path+query through ``apiFetch`` (which authenticates inside
    the container) and prints ``<status>\\n<body>``. The return shape is the
    same (status, body bytes) page `fetch_window` consumes, so either
    transport feeds the same paging loop.

    Only the two fixed ``/api/usage/`` paths the code itself builds are
    fetched (``fetch_window``'s call-logs page, the provider-limits balance
    read), only with method GET (the method is hardcoded in the script,
    never taken from the caller). `headers` is
    accepted so this is a drop-in for the HTTP fetch signature, and IGNORED:
    the helper handles auth inside the container, so no caller key is ever
    read, printed or passed -- the subprocess gets no `env` override and the
    script carries the path only. Failures raise `UsageError` naming the
    failure TYPE only (a message could carry a gateway URL or a home path);
    `FileNotFoundError` (no docker binary) reads as the helper being
    unavailable, which the caller treats as "fall back", not "measured $0".

    `_run` is the injectable subprocess runner (tests fake it; production
    passes none and gets `subprocess.run`). Tests never touch the real
    container through this function.
    """
    del headers  # accepted for signature parity only; never read or passed
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception as exc:  # noqa: BLE001 - total: malformed URL, type only
        raise UsageError("gateway helper cannot parse the request URL (%s)"
                         % type(exc).__name__)
    try:
        # T1-CREDIT-FIX-13 (D-269): WHITELIST BUILD. The forwarded
        # path+query is assembled from whitelist constants plus validated
        # ints -- never from the caller string. The RAW path (before any
        # decoding) must equal one of the two exact strings, so encoded,
        # dot-dot, trailing-slash and case variants cannot smuggle in.
        raw = url if isinstance(url, str) else ""
        for char in raw:
            # Any control (< 0x20), DEL/non-ASCII (>= 0x7f), or structural
            # char rejects first: urlsplit strips \t\r\n and splits off
            # #fragments, so only the raw string still shows them all.
            # '%' is refused outright, so no percent (single- or
            # double-encoded) form of a rejected shape can pass.
            if ord(char) < 0x20 or ord(char) >= 0x7f or char in "%\\#;":
                raise ValueError("bad request character")
        path = parts.path or ""
        if path == "/api/usage/provider-limits":
            # No query at all: a bare trailing '?' leaves parts.query
            # empty, so the raw string (whose scheme/host never hold '?')
            # is what proves no query was sent.
            if "?" in raw or parts.query or parts.fragment:
                raise ValueError("unexpected query")
            path_query = "/api/usage/provider-limits"
        elif path == "/api/usage/call-logs":
            # Hand split on '&' only (';' never separates here): each
            # field must be key=value exactly once, with exactly the
            # allowed key set. Strict ^[0-9]{1,9}$ ints (no bool, sign,
            # space or exponent) and excludeTests exactly '1'.
            seen = {}
            for field in (parts.query or "").split("&"):
                key, eq, value = field.partition("=")
                if not eq or not key or key in seen:
                    raise ValueError("bad query field")
                seen[key] = value
            if set(seen) != {"limit", "offset", "excludeTests"}:
                raise ValueError("bad query keys")
            if re.fullmatch(r"[0-9]{1,9}", seen["limit"]) is None:
                raise ValueError("bad limit")
            if re.fullmatch(r"[0-9]{1,9}", seen["offset"]) is None:
                raise ValueError("bad offset")
            if seen["excludeTests"] != "1":
                raise ValueError("bad excludeTests")
            path_query = ("/api/usage/call-logs?limit=%s&offset=%s"
                          "&excludeTests=1" % (seen["limit"], seen["offset"]))
        else:
            raise ValueError("non-usage path")
    except ValueError as exc:
        raise UsageError("gateway helper refuses a non-usage path (%s)"
                         % type(exc).__name__)
    script = ("import { apiFetch } from %s;\n"
              "const r = await apiFetch(%s, { method: 'GET' });\n"
              "const t = await r.text();\n"
              "process.stdout.write(String(r.status) + '\\n' + t);\n"
              % (json.dumps(HELPER_API_MODULE), json.dumps(path_query)))
    argv = ["docker", "exec", "-w", "/app", container,
            "node", "--input-type=module", "-e", script]
    run = _run or subprocess.run
    try:
        proc = run(argv, capture_output=True,
                   timeout=timeout or TIMEOUT_S)
    except FileNotFoundError:
        raise UsageError("gateway helper unavailable (FileNotFoundError)")
    except OSError as exc:
        raise UsageError("gateway helper unavailable (%s)"
                         % type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - timeout shapes vary; type only
        raise UsageError("gateway helper failed (%s)" % type(exc).__name__)
    if getattr(proc, "returncode", 1) != 0:
        raise UsageError("gateway helper failed (CalledProcessError)")
    out = getattr(proc, "stdout", b"") or b""
    if isinstance(out, str):
        out = out.encode("utf-8", "replace")
    head, sep, body = out.partition(b"\n")
    try:
        status = int(head.decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        status = -1
    if not sep or status < 0:
        raise UsageError("gateway helper returned an unreadable page "
                         "(ValueError)")
    return status, body


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
    """int(value), 0 when it is not a readable count -- never raises.

    T1-CREDIT-FIX-7 R4: `int()` raises OverflowError on +/-inf (and huge
    floats), which used to escape `paid_spend`, abort `credit_guards`, and
    degrade EVERY grant to `guard error`. Non-finite floats and absurd
    magnitudes (beyond 2**62 tokens -- at $1e-06/token that is $4.6M, not a
    measurement) are not counts, so they read as 0 here; `paid_spend`
    additionally skips such a row outright via `_readable_tokens`."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if abs(n) > 2 ** 62:
        return 0
    return n


def _readable_tokens(tokens):
    """(tin, tout, readable): token ints plus whether the row is billable.

    A row whose `in`/`out` fields are present but unmeasurable (non-finite,
    overflowing, or absurdly huge -- the shapes `_as_int` maps to 0) is not
    a $0 row: `paid_spend` skips it and counts it as unreadable instead of
    billing a drained grant as untouched money. Missing/None/unparseable
    fields keep the old read-as-0 behaviour and stay counted."""
    if tokens is None:
        return 0, 0, True
    if not isinstance(tokens, dict):
        # T1-CREDIT-FIX-9 T3: a present-but-non-dict `tokens` (a list, a
        # string) is unmeasured, never a $0 row.
        return 0, 0, False
    tin = tokens.get("in")
    tout = tokens.get("out")
    if not _token_field_readable(tin) or not _token_field_readable(tout):
        return 0, 0, False
    return _as_int(tin), _as_int(tout), True


def _token_field_readable(value):
    """False only for a present-but-unmeasurable token field."""
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, (list, tuple, dict, set)):
        # T1-CREDIT-FIX-9 T3: a list is not a count -- unreadable, never $0.
        return False
    try:
        n = int(value)
    except OverflowError:
        return False
    except (TypeError, ValueError):
        # T1-CREDIT-FIX-9 T3: a string that int() cannot parse ('1e30',
        # 'inf', 'nan', 'abc') is unmeasured, never $0; numeric strings
        # that int() parses ('123') stay readable. Non-string
        # unparseables keep the old read-as-0 behaviour.
        if isinstance(value, str):
            return False
        return True
    if n < 0:
        # T1-CREDIT-FIX-8 C3: a negative count is not a credit -- billed, it
        # would subtract spend and read a drained grant as funded.
        return False
    return abs(n) <= 2 ** 62


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
                 spend=None, credit_guards=None):
    """The `--json` shape. `credit_guards` is `credit_guards()` for the same rows,
    so one fetch answers both the money spent and the grants that gate the next
    call (brief FREEKEYS-1b item 2: the warn belongs in the daily usage line, not
    only in the router's head)."""
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
    if credit_guards is not None:
        report["credit_guards"] = credit_guards
    return report


def render_credit_guards(guards):
    """One line per `credit` grant, and only the states that need a reader.

    `ok` prints nothing: a grant with money left is the normal case, and a report
    that narrates it teaches nobody to ignore the section. `warn` names the
    provider and the figure because that is the last line before the router stops
    sending work there; `refuse` says the legs are already dropped, so a reader
    who wonders why a cheap model was skipped has the answer on the same page.
    """
    lines = []
    for provider in sorted(guards or {}):
        guard = guards[provider]
        if guard.get("state") == "unknown" or guard.get("spend_unknown"):
            # Unmeasured, never "$0.00": a bare zero reads as an intact grant
            # and once printed as `credit exhausted ... $0.00/$cap`.
            # T1-CREDIT-FIX-7 R2: when the grant declares no `credit_started`
            # the (absent) measurement covers month-to-date only -- say so.
            line = ("credit SPEND UNKNOWN %s - %s; its legs stay available "
                    "(fail open) until spend is measured"
                    % (provider, guard.get("note") or "spend unmeasured"))
            if guard.get("window_limited") and "month-to-date" not in line:
                line += " (window: month-to-date only; set credit_started)"
            lines.append(line)
        elif guard.get("state") == "manual":
            # T1-CREDIT-FIX-5 M1: a dated manual figure is not measured spend.
            # Name it (with its age, in the note) so it never reads as live.
            lines.append("credit MANUAL %s - %s; not measured spend"
                         % (provider, guard.get("note") or "manual figure"))
        elif guard.get("state") == "warn":
            lines.append("credit WARN %s $%.2f of a $%.2f grant (warn line $%.2f)"
                         % (provider, guard["spend_usd"], guard["cap_usd"],
                            guard["warn_usd"]))
            # T1-CREDIT-FIX-9 T2: a window-limited warn grant names its
            # month-to-date limit beside the figure.
            if guard.get("window_limited"):
                lines.append("%s credit spend measured month-to-date only "
                             "(set credit_started)" % provider)
        elif guard.get("state") == "refuse":
            lines.append("credit EXHAUSTED %s $%.2f of a $%.2f cap - its legs are "
                         "dropped by the resolver until the month rolls over"
                         % (provider, guard["spend_usd"], guard["cap_usd"]))
        elif guard.get("state") == "ok":
            # T1-CREDIT-FIX-9 T2: an ok grant prints nothing by default, but
            # a window-limited one must still name its month-to-date limit.
            if guard.get("window_limited"):
                lines.append("%s credit spend measured month-to-date only "
                             "(set credit_started)" % provider)
        if guard.get("models_unpriced"):
            lines.append("credit UNPRICED %s: %d model(s) billed with no price on "
                         "file - the guard cannot see this spend"
                         % (provider, guard["models_unpriced"]))
    return lines


def render_spend_text(spend):
    """The paid-spend section: the figure, its caveats, then the WARN lines."""
    lines = ["", "paid spend (%s) since %s" % (SPEND_PROVIDER_LABEL, spend["since"]),
             "  %d calls, %d tokens in, %d tokens out -> $%.4f (priced from %s)"
             % (spend["calls"], spend["tokens_in"], spend["tokens_out"],
                spend["spend_usd"], spend["price_source"] or "no readable registry")]
    if spend["models_unpriced"]:
        lines.append("  %d model(s) had no price on file and count as 0"
                     % spend["models_unpriced"])
    if int(spend.get("rows_unreadable") or 0) > 0:
        # T1-CREDIT-FIX-9 T4: unreadable rows are skipped spend, never $0 --
        # name the count beside the readable figure.
        lines.append("  %d unreadable row(s) skipped"
                     % int(spend.get("rows_unreadable") or 0))
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
    credit_lines = render_credit_guards(report.get("credit_guards"))
    if credit_lines:
        lines.append("")
        lines.append("credit grants (a tier credit leg is dropped at its cap)")
        lines.extend("  " + line for line in credit_lines)
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


def render_lines(report, dims):
    """One flat line per group of the FIRST --by dimension.

    D-102 CLAUDEBUDGET (S2) item 3, operator 2026-09-28: the daily report to
    routing-00 is one line per provider, so an orchestrator can paste it without
    cutting a table out of terminal formatting. `key=value` pairs, never a column
    layout -- the widths move with the longest provider name and nothing
    downstream can grep a padded column.
    """
    dim = dims[0]
    lines = []
    for group in report["by"].get(dim, []):
        cost = group.get("cost_in", 0.0) + group.get("cost_out", 0.0)
        lines.append("%s=%s calls=%d ok=%d errors=%d tokens_in=%d "
                     "tokens_out=%d cost_usd=%.4f"
                     % (dim, group["key"], group["calls"], group["ok"],
                        group["errors"], group["tokens_in"],
                        group["tokens_out"], cost))
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
    ap.add_argument("--lines", action="store_true",
                    help="one flat key=value line per group of the first --by "
                         "dimension instead of tables (D-102: the daily "
                         "per-provider report). Implies --cost, because a budget "
                         "line with the cost missing reads the same as a free "
                         "provider.")
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
        print("autoos-usage: manage key file missing or empty - create a manage-scoped "
              "key in the OmniRoute dashboard and save it there (mode 600) - spend unmeasured",
              file=sys.stderr)
        return 3

    gateway = (env.get("AUTOOS_OMNIROUTE_URL") or DEFAULT_GATEWAY).rstrip("/")
    fetch = fetch or urllib_fetch
    # T1-CREDIT-FIX-8 C1: the fetch reaches the earliest grant start, not
    # just month-start -- one home (`fetch_cutoff`), read by both fetch
    # callers. The registry loads here (it is needed for the cutoff and again
    # below); `read_registry` returns {} when unreadable, never raises.
    priced = args.cost or args.lines or spend_on
    registry = read_registry(args.registry) if priced else {}
    fetch_depths = [cutoff]
    if spend_on:
        fetch_depths.append(spend_cutoff)
    if priced:
        fetch_depths.append(fetch_cutoff(registry, now))
    try:
        rows, pages, truncated = fetch_window(fetch, gateway, key,
                                              min(fetch_depths))
    except UsageError as e:
        # Print only the exception type name, not the message which may contain URLs
        print("autoos-usage: spend unmeasured (%s)" % type(e).__name__, file=sys.stderr)
        text = str(e)
        # The distinct manage-key line (T1-CREDIT-FIX): a 403 is the observed
        # failure -- a short/revoked/wrong-scoped key the gateway refuses --
        # and the spend it leaves behind is unmeasured, never $0.00.
        if "HTTP 403" in text:
            print("autoos-usage: manage key rejected (403) - spend unmeasured",
                  file=sys.stderr)
        elif "HTTP 401" in text:
            print("autoos-usage: manage key rejected (401) - spend unmeasured",
                  file=sys.stderr)
        return 3

    prices = prices_from_registry(registry)
    price_source = price_source_name(args.registry) if priced else None
    spend = paid_spend(rows, prices, registry, spend_cutoff, balance=balance,
                       price_source=price_source, complete=not truncated) if spend_on else None
    # The same rows answer every grant the registry funds, so the report a reader
    # uses to decide "is there money left" is the report the router reads to decide
    # whether to send work there at all (brief FREEKEYS-1b item 2).
    guarded = credit_guard_providers(registry)
    guards = None
    if guarded:
        try:
            guards = credit_guards(registry, rows, spend_cutoff, today=now,
                                   complete=not truncated)
        except Exception as e:
            # A grant that cannot state its own cap (ValueError) must read as
            # unmeasured, never as a traceback: the documented exit code is 3.
            # Type name only -- a message can carry a path or key (rule 1).
            print("autoos-usage: credit guard unreadable (%s) - spend unmeasured"
                  % type(e).__name__, file=sys.stderr)
            return 3
    show_cost = args.cost or args.lines
    report = build_report(rows, dims, cutoff, pages, truncated,
                          prices=prices if show_cost else None,
                          price_source=price_source if show_cost else None,
                          spend=spend, credit_guards=guards)
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.lines:
        print(render_lines(report, dims))
    else:
        print(render_text(report, dims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
