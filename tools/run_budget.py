#!/usr/bin/env python3
"""tools/run_budget.py — Mechanical cost guard for AutoOS runs and daily budgets.

Sources:
  run  --rows <file>   a JSON array / {"rows": [...]} / NDJSON row export
  run  --gateway       recent rows from the OmniRoute gateway
  day  --rows <f>...   one or more row exports (an optional --gateway adds a second source)

All money math is exact (decimal.Decimal on the per-token rates and integer
token counts); the six-decimal numbers in the JSON output are display only.
The verdicts are decided on the unrounded value, so an estimate of $2.0000001
stops even though it displays as 2.0.

Row files: a UTF-8 BOM is accepted. An empty file or `[]` is a valid 0 rows.
A malformed NDJSON line is skipped and counted in `bad_rows`. A missing or
unreadable file, a directory, malformed JSON, or a file whose every line is
malformed exits 2 with a one-line message and prints no verdict.

Tag matching (`run --tag`): the tag matches the full sessionTag, or its first
segment (the lane), or its last segment (the run id).

Google-paid classification: a row counts when its provider says so
(case-insensitive): a provider containing "vertex", a provider equal to
"gemini" / "google" / "vertex_ai" (including gateway spellings such as
"vertex-gemini-..." and "gemini-3.8-flash"), or — with an empty provider — a
model starting with "gemini-", "vertex/", "gemini/" or "google/". Never:
antigravity/agy/ovh/ovhcloud/openrouter/jules.
"""

import argparse
import datetime
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = REPO_ROOT / "catalog" / "ai-registry.json"
DEFAULT_PRICES_PATH = REPO_ROOT / "configuration" / "google-prices.json"

# Both the tools dir and the repo root must be importable so the gateway fetch
# below works when this file is run as `python tools/run_budget.py`.
for _p in (str(REPO_ROOT / "tools"), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_RUN_FLAG = 10
EXIT_RUN_ROTATE = 11
EXIT_RUN_STOP = 12
EXIT_DAY_WARN = 20
EXIT_DAY_BLOCK = 21

RUN_MAX_UNCACHED = 1_000_000
RUN_MAX_USD = Decimal("2.00")
RUN_ROTATE_LAST_INPUT = 150_000
RUN_FLAG_LAST3_INPUT = 60_000
DAY_DEFAULT_WARN_USD = 20.0
DAY_DEFAULT_BUDGET_USD = 25.0

CENT = Decimal("0.000001")
UNPRICED_DEFAULT_MODEL = "gemini-3.8-flash"
# Last resort when the prices file has no priced Gemini Flash model at all:
# the highest known Flash rate, per token. The published Flash list prices are
# expected to double from 2027; that is a known limit, not code.
WORST_CASE_FLASH_RATES = {"price_in": 7.5e-07, "price_cache_read": 1.875e-07, "price_out": 3.75e-06}

# Providers that are never Google-paid: internal quotas or separate billing.
EXCLUDED_PROVIDERS = ("antigravity", "agy", "ovh", "ovhcloud", "openrouter", "jules")


class RowFileError(Exception):
    """A rows file cannot be used at all; carries the one-line stderr message."""


class PriceFileError(Exception):
    """The price table is malformed or self-contradictory; one-line stderr message."""


class GatewayError(Exception):
    """The gateway fetch failed; carries the one-line stderr message."""


def parse_timestamp(val):
    """Parse ISO-8601 or epoch timestamp into UTC datetime; None if unparseable."""
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        num = float(val) / 1000.0 if float(val) > 1e11 else float(val)
        try:
            return datetime.datetime.fromtimestamp(num, tz=datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    s = str(val).strip()
    if not s:
        return None
    s = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        dt = datetime.datetime.fromisoformat(s)
        return dt.astimezone(datetime.timezone.utc) if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        pass
    try:
        num = float(s)
        num = num / 1000.0 if num > 1e11 else num
        return datetime.datetime.fromtimestamp(num, tz=datetime.timezone.utc)
    except Exception:
        return None


def normalize_provider_alias(prov):
    p = (prov or "").strip().lower()
    if p in ("vertex", "vertex_ai"):
        return "vertex"
    if p in ("gemini", "google_ai_studio", "ai_studio", "google"):
        return "gemini"
    if p in ("antigravity", "agy"):
        return "antigravity"
    if p in ("ovh", "ovhcloud"):
        return "ovh"
    return p


def google_paid_provider(row):
    """Returns 'vertex' or 'gemini' if row is a Google-paid leg, else None.

    See the module docstring for the exact provider/model spellings.
    """
    if not isinstance(row, dict):
        return None
    raw_prov = str(row.get("provider") or "").strip().lower()
    model = str(row.get("model") or "").strip().lower()
    if raw_prov in EXCLUDED_PROVIDERS or model.startswith(("antigravity/", "agy/", "ovh/")):
        return None
    if "vertex" in raw_prov:
        return "vertex"
    if raw_prov in ("gemini", "google", "vertex_ai") or raw_prov.startswith("gemini-"):
        return "gemini"
    if not raw_prov or raw_prov in ("unknown", "(unknown)"):
        if model.startswith("vertex/"):
            return "vertex"
        if model.startswith(("gemini-", "gemini/", "google/")):
            return "gemini"
    return None


def _check_price_pair(entry, where):
    """per_million and per_token must agree when both are present.

    Only per_token is used for the math, so a file where the two disagree would
    silently cost whatever per_token says; that edit must fail loudly instead.
    """
    for field in ("price_in", "price_out", "price_cache_read"):
        obj = entry.get(field) or {}
        if not isinstance(obj, dict):
            continue
        pm, pt = obj.get("per_million"), obj.get("per_token")
        if pm is None or pt is None:
            continue
        try:
            pm_f, pt_f = float(pm), float(pt)
        except (TypeError, ValueError):
            raise PriceFileError("%s %s: per_million/per_token are not numbers" % (where, field))
        if math.isfinite(pm_f) and math.isfinite(pt_f):
            if abs(pm_f - pt_f * 1e6) > 1e-6 * max(abs(pm_f), abs(pt_f * 1e6), 1e-12):
                raise PriceFileError(
                    "%s %s: per_million %s disagrees with per_token %s" % (where, field, pm, pt))


def load_price_table(registry_path=None, prices_path=None):
    reg_path = Path(registry_path) if registry_path else DEFAULT_REGISTRY_PATH
    fb_path = Path(prices_path) if prices_path else DEFAULT_PRICES_PATH
    fallback, registry = {}, {}
    if fb_path.is_file():
        try:
            with fb_path.open("r", encoding="utf-8") as f:
                fallback = json.load(f)
        except (OSError, ValueError) as e:
            raise PriceFileError("price file %s is unreadable or not JSON (%s)" % (fb_path, type(e).__name__))
    if not isinstance(fallback, dict):
        raise PriceFileError("price file %s must be a JSON object" % fb_path)
    models = fallback.get("models")
    if isinstance(models, dict):
        for model, entry in models.items():
            if not isinstance(entry, dict):
                continue
            providers = entry.get("providers")
            if not isinstance(providers, dict):
                continue
            for prov, p_entry in providers.items():
                if isinstance(p_entry, dict):
                    _check_price_pair(p_entry, "%s/%s" % (model, prov))
    if reg_path.is_file():
        try:
            with reg_path.open("r", encoding="utf-8") as f:
                registry = json.load(f)
        except (OSError, ValueError) as e:
            raise PriceFileError("registry %s is unreadable or not JSON (%s)" % (reg_path, type(e).__name__))
    return {"fallback": fallback, "registry": registry if isinstance(registry, dict) else {}}


def _price_float(obj, key="per_token"):
    try:
        v = float(obj.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def get_price(model, provider=None, table=None):
    if table is None:
        table = load_price_table()
    raw_model, raw_prov = str(model or "").strip(), str(provider or "").strip()
    bare_model, inferred_prov = raw_model, raw_prov
    if "/" in bare_model:
        parts = bare_model.split("/")
        if parts[0].lower() in ("vertex", "gemini", "google", "antigravity", "agy", "ovh"):
            inferred_prov = inferred_prov or parts[0]
            bare_model = parts[-1]
        elif parts[0].lower() == "omniroute" and len(parts) > 1:
            sub = parts[1]
            if sub.startswith("vertex-"):
                inferred_prov = inferred_prov or "vertex"
                bare_model = sub[len("vertex-"):]
            elif sub.startswith("gemini-"):
                inferred_prov = inferred_prov or "gemini"
                bare_model = sub[len("gemini-"):]
    prov = normalize_provider_alias(inferred_prov)

    # 1. Registry check: prefer complete prices (in, out, cache_read > 0)
    reg_models = (table.get("registry") or {}).get("models") or {}
    reg_entry = reg_models.get(bare_model) or reg_models.get(raw_model)
    if isinstance(reg_entry, dict):
        scoped = (reg_entry.get("provider_prices") or {}).get(inferred_prov or prov)
        active = scoped if isinstance(scoped, dict) else reg_entry
        p_in = _price_float(active, "price_in")
        p_out = _price_float(active, "price_out")
        p_cache = _price_float(active, "price_cache_read") or _price_float(active, "price_cached_in")
        if p_in > 0.0 and p_out > 0.0 and p_cache > 0.0:
            return {"price_in": p_in, "price_out": p_out, "price_cache_read": p_cache, "unverified": False, "source": "registry"}

    # 2. Fallback table check: configuration/google-prices.json
    fb_models = (table.get("fallback") or {}).get("models") or {}
    fb_entry = fb_models.get(bare_model) or fb_models.get(raw_model)
    if isinstance(fb_entry, dict):
        providers = fb_entry.get("providers") or {}
        p_entry = providers.get(prov) or providers.get("gemini") or providers.get("vertex")
        if isinstance(p_entry, dict):
            in_obj = p_entry.get("price_in") or {}
            out_obj = p_entry.get("price_out") or {}
            cache_obj = p_entry.get("price_cache_read") or {}
            unverified = bool(cache_obj.get("unverified") or in_obj.get("unverified") or out_obj.get("unverified"))
            return {
                "price_in": _price_float(in_obj),
                "price_out": _price_float(out_obj),
                "price_cache_read": _price_float(cache_obj),
                "unverified": unverified,
                "source": "google-prices.json",
            }
    return {"price_in": 0.0, "price_out": 0.0, "price_cache_read": 0.0, "unverified": False, "source": "unpriced"}


def default_google_prices(table=None):
    """Worst-case rate for a Google-paid row whose model has no price row.

    An unpriced model must never be under-counted, so instead of the AI
    Studio default it is priced at the highest known Gemini Flash rate among
    the rows of the prices file that are Google-paid Flash models: highest
    price_in, highest price_out and highest cache-read, each taken
    independently. Only when the file has no priced Flash model at all do the
    built-in worst-case constants stand in (marked unverified).
    """
    best = {"price_in": 0.0, "price_out": 0.0, "price_cache_read": 0.0}
    if table is not None:
        fb_models = (table.get("fallback") or {}).get("models") or {}
        for model, entry in fb_models.items():
            if not isinstance(entry, dict) or "flash" not in str(model).lower():
                continue
            for p_entry in (entry.get("providers") or {}).values():
                if not isinstance(p_entry, dict):
                    continue
                for field in ("price_in", "price_out", "price_cache_read"):
                    v = _price_float(p_entry.get(field) or {})
                    if v > best[field]:
                        best[field] = v
    return {
        "price_in": best["price_in"] or WORST_CASE_FLASH_RATES["price_in"],
        "price_out": best["price_out"] or WORST_CASE_FLASH_RATES["price_out"],
        "price_cache_read": best["price_cache_read"] or WORST_CASE_FLASH_RATES["price_cache_read"],
        "unverified": True,
        "source": "unpriced-default",
    }


def _coerce_token(value):
    """Return (int_value, bad). Missing is 0 and fine; a non-numeric value
    (garbage string, NaN, inf, list, ...) coerces to 0 and flags the row."""
    if value is None:
        return 0, False
    if isinstance(value, bool):
        return 0, True
    if isinstance(value, int):
        return max(value, 0), False
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return 0, True
        return max(int(value), 0), False
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            return 0, True
        if math.isnan(f) or math.isinf(f):
            return 0, True
        return max(int(f), 0), False
    return 0, True


def _status_ok(status):
    """True when the row's status means success, or the field is absent."""
    if status is None:
        return True
    if isinstance(status, bool):
        return False
    if isinstance(status, (int, float)):
        return status == 200
    return str(status).strip() == "200"


def read_rows_from_file(path):
    """Read row lines from a JSON or NDJSON file.

    Returns (rows, bad_lines). Raises RowFileError when the file cannot be
    used at all: missing, unreadable, a directory, or a file that yields no
    usable rows at all (malformed JSON, a JSON document without a row list,
    or every NDJSON line malformed). An empty file or a top-level [] is a
    valid 0 rows. Malformed NDJSON lines are skipped and counted, not aborting.
    """
    p = Path(path)
    if p.is_dir():
        raise RowFileError("rows path is a directory: %s" % p)
    if not p.is_file():
        raise RowFileError("rows file not found: %s" % p)
    try:
        with p.open("r", encoding="utf-8-sig") as f:
            content = f.read()
    except OSError:
        raise RowFileError("rows file unreadable: %s" % p)
    content = content.strip()
    if not content:
        return [], 0
    rows, bad = [], 0
    try:
        doc = json.loads(content)
    except ValueError:
        # Not one JSON document: treat it as NDJSON. A document-like file that
        # is malformed everywhere ends up with zero rows and is rejected below
        # as "no usable lines", so a mistyped path can never yield a verdict.
        doc = None
    if doc is not None:
        # One JSON document, not NDJSON: it must carry a row list (a top-level
        # array, or an object with a "rows" list).
        items = doc.get("rows") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            raise RowFileError("rows file has no row list: %s" % p)
        for r in items:
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
        if not rows and bad:
            raise RowFileError("rows file has no usable lines: %s" % p)
        return rows, bad
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        elif isinstance(obj, list):
            rows.extend(r for r in obj if isinstance(r, dict))
        else:
            bad += 1
    if not rows and bad:
        raise RowFileError("rows file has no usable lines: %s" % p)
    return rows, bad


def read_rows_from_files(paths):
    rows, bad = [], 0
    for p in paths:
        r, b = read_rows_from_file(p)
        rows.extend(r)
        bad += b
    return rows, bad


def fetch_gateway_rows(fetch=None, gateway=None, key=None, cutoff=None, env=None):
    """Fetch recent call-log rows from the gateway.

    Returns (rows, truncated); truncated is True when paging hit the page cap
    with more rows likely unread. Raises GatewayError (one line) on failure.
    """
    import tools.autoos_usage as autoos_usage
    e = os.environ if env is None else env
    gw = (gateway or e.get("AUTOOS_OMNIROUTE_URL") or autoos_usage.DEFAULT_GATEWAY).rstrip("/")
    if key is None:
        try:
            key = autoos_usage.read_manage_key(autoos_usage.key_file_path(e))
        except OSError:
            key = ""
    f = fetch or autoos_usage.urllib_fetch
    cut = cutoff or (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2))
    try:
        rows, _pages, truncated = autoos_usage.fetch_window(f, gw, key, cut)
    except autoos_usage.UsageError as e2:
        raise GatewayError(str(e2))
    return rows, truncated


def group_rows(rows):
    groups = {}
    for r in rows:
        prov = str(r.get("provider") or "unknown").strip()
        stag = str(r.get("sessionTag") or "").strip()
        lane = stag.split("/")[0] if "/" in stag else (stag or "untagged")
        groups.setdefault(f"{prov}|{lane}|{stag or 'norun'}", []).append(r)
    return groups


def tag_matches(row_tag, want_tag):
    """True when want_tag is the full sessionTag, its first segment (lane) or
    its last segment (run id)."""
    if not want_tag:
        return True
    r_tag = str(row_tag or "").strip()
    w_tag = str(want_tag).strip()
    if not r_tag or not w_tag:
        return False
    parts = r_tag.split("/")
    return r_tag == w_tag or parts[0] == w_tag or parts[-1] == w_tag


def filter_rows_by_tag(rows, tag):
    return [r for r in rows if tag_matches(r.get("sessionTag"), tag)] if tag else list(rows)


def _round6(d):
    return float(d.quantize(CENT))


def _unpriced_list(counter):
    return [{"model": m, "count": n} for m, n in sorted(counter.items())]


def _row_cost_dec(r, prices, unpriced):
    """Exact-decimal cost of one row; registers unpriced Google-paid models."""
    prov = google_paid_provider(r)
    pr = get_price(r.get("model"), prov if prov else r.get("provider"), prices)
    if pr["source"] == "unpriced" and prov:
        # A Google-paid row whose model has no price row would otherwise be
        # priced at $0: price it at the highest known Gemini Flash rate and
        # say so.
        pr = default_google_prices(prices)
        name = str(r.get("model") or "").strip() or "(unknown model)"
        unpriced[name] = unpriced.get(name, 0) + 1
    return prov, pr


def evaluate_run(rows, tag=None, prices=None):
    """Per-run gate for the calls matching `tag`.

    `calls` counts every matching row. The rotate/flag checks use only the
    calls that carry input tokens: a row whose status is not 200 (when the
    status field is present) or whose input tokens are 0 cannot trigger
    rotate or flag, even as the trailing row. `bad_rows` counts rows with a
    missing or garbage timestamp or a non-numeric token field; those rows are
    still priced at their coerced values.
    """
    matching = filter_rows_by_tag(rows, tag)
    matching.sort(key=lambda r: (parse_timestamp(r.get("timestamp")) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)).timestamp())

    total_in = total_cache = total_uncached = total_out = 0
    est = Decimal(0)
    has_unverified = False
    calls_input = []
    bad_rows = 0
    unpriced = {}

    for r in matching:
        toks = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
        tin, b_in = _coerce_token(toks.get("in"))
        tout, b_out = _coerce_token(toks.get("out"))
        tcache, b_cache = _coerce_token(toks.get("cacheRead"))
        ts = parse_timestamp(r.get("timestamp"))
        if b_in or b_out or b_cache or ts is None:
            bad_rows += 1
        cached = min(tcache, tin)
        uncached = tin - cached

        if _status_ok(r.get("status")) and tin > 0:
            calls_input.append(tin)

        total_in += tin
        total_cache += cached
        total_uncached += uncached
        total_out += tout

        _prov, pr = _row_cost_dec(r, prices, unpriced)
        est += (Decimal(str(pr["price_in"])) * uncached
                + Decimal(str(pr["price_cache_read"])) * cached
                + Decimal(str(pr["price_out"])) * tout)
        if pr["unverified"] and (cached > 0 or uncached > 0 or tout > 0):
            has_unverified = True

    max_input = max(calls_input) if calls_input else 0
    last3 = calls_input[-3:]
    # est stays exact; only the displayed value is rounded to 6 decimals.
    est_usd = _round6(est)

    if total_uncached > RUN_MAX_UNCACHED or est > RUN_MAX_USD:
        verdict = "stop"
    elif calls_input and calls_input[-1] >= RUN_ROTATE_LAST_INPUT:
        verdict = "rotate"
    elif len(calls_input) >= 3 and all(v > RUN_FLAG_LAST3_INPUT for v in last3):
        verdict = "flag"
    else:
        verdict = "ok"

    return {
        "calls": len(matching),
        "input": total_in,
        "cache_read": total_cache,
        "uncached": total_uncached,
        "output": total_out,
        "est_usd": est_usd,
        "max_input_per_call": max_input,
        "last3_input": list(last3),
        "bad_rows": bad_rows,
        "unpriced_models": _unpriced_list(unpriced),
        "unpriced_default_used": bool(unpriced),
        "verdict": verdict,
        "unverified": has_unverified,
    }


def evaluate_day(rows, day_str=None, budget=DAY_DEFAULT_BUDGET_USD, warn=DAY_DEFAULT_WARN_USD, prices=None):
    """Daily Google-paid spend gate. Rows without a parseable timestamp are
    not counted toward this day (we cannot date them) but are reported in
    `bad_rows` instead of being dropped silently."""
    if not day_str:
        day_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    by_provider = {}
    total = Decimal(0)
    has_unverified = False
    bad_rows = 0
    unpriced = {}
    for r in rows:
        prov = google_paid_provider(r)
        if not prov:
            continue
        ts = parse_timestamp(r.get("timestamp"))
        if ts is None:
            # No date means we cannot place this row in the day: not counted
            # toward today, but reported instead of silently dropped.
            bad_rows += 1
            continue
        if ts.strftime("%Y-%m-%d") != day_str:
            continue
        toks = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
        tin, b_in = _coerce_token(toks.get("in"))
        tout, b_out = _coerce_token(toks.get("out"))
        tcache, b_cache = _coerce_token(toks.get("cacheRead"))
        if b_in or b_out or b_cache:
            bad_rows += 1
        cached = min(tcache, tin)
        uncached = tin - cached
        _p, pr = _row_cost_dec(r, prices, unpriced)
        cost = (Decimal(str(pr["price_in"])) * uncached
                + Decimal(str(pr["price_cache_read"])) * cached
                + Decimal(str(pr["price_out"])) * tout)
        total += cost
        by_provider[prov] = by_provider.get(prov, Decimal(0)) + cost
        if pr["unverified"] and (cached > 0 or uncached > 0 or tout > 0):
            has_unverified = True

    # Exact comparison: an unrounded total of 24.999999999 must warn, not read
    # as the rounded 24.999999 and slip under the budget.
    if total >= Decimal(str(budget)):
        verdict = "block"
    elif total >= Decimal(str(warn)):
        verdict = "warn"
    else:
        verdict = "ok"
    return {
        "day": day_str,
        "usd": _round6(total),
        "by_provider": {k: _round6(v) for k, v in sorted(by_provider.items())},
        "verdict": verdict,
        "unverified": has_unverified,
        "budget": budget,
        "bad_rows": bad_rows,
        "unpriced_models": _unpriced_list(unpriced),
        "unpriced_default_used": bool(unpriced),
    }


def _unpriced_note(res, what):
    names = ", ".join(m["model"] for m in res["unpriced_models"])
    total = sum(m["count"] for m in res["unpriced_models"])
    print("run_budget: %s: %d call(s) on unpriced model(s) %s priced at the "
          "AI Studio %s default rates" % (what, total, names, UNPRICED_DEFAULT_MODEL), file=sys.stderr)


def main(argv=None, fetch=None):
    parser = argparse.ArgumentParser(prog="run_budget.py", description="AutoOS cost guard budget tool")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--registry", default=None)
    run_p.add_argument("--prices", default=None)
    run_p.add_argument("--rows", default=None)
    run_p.add_argument("--gateway", action="store_true")
    run_p.add_argument("--tag", default=None,
                       help="Session tag filter: matches the full sessionTag, "
                            "its first segment (lane) or its last segment (run id).")

    day_p = sub.add_parser("day")
    day_p.add_argument("--registry", default=None)
    day_p.add_argument("--prices", default=None)
    day_p.add_argument("--rows", nargs="+", default=[])
    day_p.add_argument("--gateway", action="store_true",
                       help="Also pull recent rows from the gateway, in addition to the --rows files.")
    day_p.add_argument("--budget", type=float, default=DAY_DEFAULT_BUDGET_USD)
    day_p.add_argument("--warn", type=float, default=DAY_DEFAULT_WARN_USD)
    day_p.add_argument("--day", default=None)

    args = parser.parse_args(argv)

    if args.command == "day" and args.day:
        try:
            datetime.date.fromisoformat(args.day)
        except ValueError:
            print("run_budget: --day must be YYYY-MM-DD", file=sys.stderr)
            return EXIT_BAD_INPUT

    try:
        prices = load_price_table(args.registry, args.prices)
    except PriceFileError as e:
        print("run_budget: %s" % e, file=sys.stderr)
        return EXIT_BAD_INPUT

    if args.command == "run":
        truncated = False
        file_bad = 0
        try:
            if not args.rows and not args.gateway:
                raise RowFileError("neither --rows nor --gateway specified")
            if args.rows:
                rows, file_bad = read_rows_from_file(args.rows)
            elif args.gateway:
                rows, truncated = fetch_gateway_rows(fetch=fetch)
            else:
                rows = []
        except RowFileError as e:
            print("run_budget: %s" % e, file=sys.stderr)
            return EXIT_BAD_INPUT
        except GatewayError as e:
            print("run_budget: gateway: %s" % e, file=sys.stderr)
            return EXIT_BAD_INPUT
        res = evaluate_run(rows, tag=args.tag, prices=prices)
        res["truncated"] = truncated
        res["bad_rows"] += file_bad
        print(json.dumps(res, indent=2))
        if res["unpriced_default_used"]:
            _unpriced_note(res, "run")
        return {"stop": EXIT_RUN_STOP, "rotate": EXIT_RUN_ROTATE, "flag": EXIT_RUN_FLAG}.get(res["verdict"], EXIT_OK)

    if args.command == "day":
        truncated = False
        file_bad = 0
        try:
            if not args.rows and not args.gateway:
                raise RowFileError("neither --rows nor --gateway specified")
            if args.rows:
                rows, file_bad = read_rows_from_files(args.rows)
            else:
                rows = []
            if args.gateway:
                gw_rows, gw_trunc = fetch_gateway_rows(fetch=fetch)
                rows.extend(gw_rows)
                truncated = gw_trunc
        except RowFileError as e:
            print("run_budget: %s" % e, file=sys.stderr)
            return EXIT_BAD_INPUT
        except GatewayError as e:
            print("run_budget: gateway: %s" % e, file=sys.stderr)
            return EXIT_BAD_INPUT
        res = evaluate_day(rows, day_str=args.day, budget=args.budget, warn=args.warn, prices=prices)
        res["truncated"] = truncated
        res["bad_rows"] += file_bad
        print(json.dumps(res, indent=2))
        if res["unpriced_default_used"]:
            _unpriced_note(res, "day")
        return {"block": EXIT_DAY_BLOCK, "warn": EXIT_DAY_WARN}.get(res["verdict"], EXIT_OK)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
