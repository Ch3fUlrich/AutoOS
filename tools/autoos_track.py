"""The routing track record: one line per finished run (spec §5.6).

Append-only ``logs/routing/track-record.jsonl`` in the git-ignored overlay -
machine data, never the registry (spec §3.3). Pure stdlib: no clock, no
network, no state of its own, so the resolver can replay it and the suites can
pin it.

``p_success`` is the Beta estimate the scorer uses: observations for the exact
route win, then observations for the route class, then the seed prior. A
missing prior fails closed - an unmeasured (class, bucket) is never silently
treated as reliable.
"""
from __future__ import annotations

import json
import os

# The record's exact schema. A missing key and an unknown key are both errors;
# so is a value outside its set. These are the values autoos-agent.py writes.
FIELDS = ("route", "class", "served_leg", "bucket", "effort", "tokens_in",
          "tokens_out", "cost", "latency_s", "gate", "failure_class")
CLASSES = ("free", "cheap", "mid", "frontier")
BUCKETS = ("unknown", "S0", "S1", "S2", "S3", "S4")
EFFORTS = ("unknown", "none", "low", "medium", "high", "xhigh", "max")
GATES = ("pass", "fail")
FAILURES = (None, "logic", "capability")


def _text(name, value):
    if not isinstance(value, str) or not value:
        raise ValueError("track record %s=%r: expected a non-empty string" % (name, value))
    return value


def _number(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("track record %s=%r: expected a number" % (name, value))
    if value < 0:
        raise ValueError("track record %s=%r: must not be negative" % (name, value))
    return value


def _choice(name, value, allowed):
    if value not in allowed:
        raise ValueError("track record %s=%r: expected one of %s"
                         % (name, value, ", ".join(repr(a) for a in allowed)))
    return value


def validate(entry: dict) -> dict:
    """Return a checked copy of ``entry``; ValueError on any missing/unknown field."""
    if not isinstance(entry, dict):
        raise ValueError("track record: expected an object, got %s" % type(entry).__name__)
    missing = [k for k in FIELDS if k not in entry]
    if missing:
        raise ValueError("track record: missing key(s): %s" % ", ".join(missing))
    extra = [k for k in entry if k not in FIELDS]
    if extra:
        raise ValueError("track record: unknown key(s): %s" % ", ".join(sorted(extra)))
    return {
        "route": _text("route", entry["route"]),
        "class": _choice("class", entry["class"], CLASSES),
        "served_leg": _text("served_leg", entry["served_leg"]),
        "bucket": _choice("bucket", entry["bucket"], BUCKETS),
        "effort": _choice("effort", entry["effort"], EFFORTS),
        "tokens_in": _number("tokens_in", entry["tokens_in"]),
        "tokens_out": _number("tokens_out", entry["tokens_out"]),
        "cost": _number("cost", entry["cost"]),
        "latency_s": _number("latency_s", entry["latency_s"]),
        "gate": _choice("gate", entry["gate"], GATES),
        "failure_class": _choice("failure_class", entry["failure_class"], FAILURES),
    }


def record(path: str, entry: dict) -> None:
    """Validate ``entry`` and append it as one sorted-key JSON line.

    Parent directories are created. A bad entry raises ValueError and writes
    nothing.
    """
    obj = validate(entry)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True) + "\n")


def load(path: str) -> list:
    """Every well-formed record; a missing file is empty, malformed lines skipped."""
    records = []
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return records
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def _prior(priors: dict, route_class: str, bucket: str) -> tuple:
    try:
        prior = priors[route_class][bucket]
        return prior["alpha"], prior["beta"]
    except (KeyError, TypeError):
        raise ValueError("no seed prior for class=%r bucket=%r" % (route_class, bucket))


def p_success(records: list, route: str, route_class: str, bucket: str,
              effort: str, priors: dict) -> tuple:
    """Return ``(p, source)`` for a route at a bucket and effort.

    Observations are counted for ``(route, bucket, effort)``; with none they
    fall back to ``(class, bucket, effort)``; with none there, to the
    class/bucket prior. ``source`` is ``"route"`` | ``"class"`` | ``"prior"``.
    A missing prior raises ValueError (fail closed).
    """
    alpha, beta = _prior(priors, route_class, bucket)

    def matching(pred):
        return [r for r in records
                if r.get("bucket") == bucket and r.get("effort") == effort and pred(r)]

    observed = matching(lambda r: r.get("route") == route)
    if observed:
        source = "route"
    else:
        observed = matching(lambda r: r.get("class") == route_class)
        source = "class" if observed else "prior"
    passes = sum(1 for r in observed if r.get("gate") == "pass")
    failures = sum(1 for r in observed if r.get("gate") == "fail")
    return (alpha + passes) / (alpha + beta + passes + failures), source
