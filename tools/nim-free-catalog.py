#!/usr/bin/env python3
"""Keyless NVIDIA NIM Free Endpoint inventory; never proposes or changes routing.

Only GETs the public NGC catalog and integration /v1/models (not inference).
use_class table: exact, case-insensitive general.unresolvedValues membership;
first matching row wins, no name/display-label inference:
  safety   | content moderation, llm safety, content safety, safety and moderation,
           | guardrail, nemoguard
  embedding| usecase_text_to_embedding, text-to-embedding
  rerank   | reranking
  vision   | vision language model, vlm, computer vision, cv, body pose,
           | video understanding, usecase_image_to_text, image-to-text, ocr
  speech   | usecase_text_to_speech, usecase_speech_to_text, speech enhancement,
           | speaker detection, tts, asr, automatic speech recognition
  image    | image generation, diffusion models
  chat     | playgroundtype_chat, chat, text-to-text, language generation
  other    | everything else

--check compares resource_id sets only, not dates or metadata. A v1 match means
listed, not inference-tested. Override BOTH origins for hermetic local tests via
AUTOOS_NIM_CATALOG_BASE_URL and AUTOOS_NIM_V1_BASE_URL. No credential lookup,
Authorization, proxy environment, or redirects; the only write is the snapshot.
"""
import argparse
from datetime import date, datetime, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CATALOG_BASE = "https://api.ngc.nvidia.com"
V1_BASE = "https://integrate.api.nvidia.com"
CATALOG_PATH = "/v2/search/catalog/resources/ENDPOINT"
ORG = "qc69jvmznzxy"
FILTER = {"field": "nimType", "value": "nim_type_preview"}
BASE_FILTERS = [{"field": "resourceType", "value": "endpoint"},
                {"field": "isPublic", "value": "true"}]
TIMEOUT_SECONDS = 30
DEFAULT_SNAPSHOT = (Path(__file__).resolve().parent.parent /
                    "configuration/omniroute/probes/nim-free-catalog.json")
USE_CLASS_RULES = (
    ("safety", {"content moderation", "llm safety", "content safety",
                "safety and moderation", "guardrail", "nemoguard"}),
    ("embedding", {"usecase_text_to_embedding", "text-to-embedding"}),
    ("rerank", {"reranking"}),
    ("vision", {"vision language model", "vlm", "computer vision", "cv", "body pose",
                "video understanding", "usecase_image_to_text", "image-to-text", "ocr"}),
    ("speech", {"usecase_text_to_speech", "usecase_speech_to_text", "speech enhancement",
                "speaker detection", "tts", "asr", "automatic speech recognition"}),
    ("image", {"image generation", "diffusion models"}),
    ("chat", {"playgroundtype_chat", "chat", "text-to-text", "language generation"}),
)


class CatalogError(Exception):
    """A fetch or parse failure: do not write or report any catalog delta."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalise_slug(slug):
    """Casefold and map each '_' or '.' to '-'; preserve all other characters."""
    return slug.casefold().replace("_", "-").replace(".", "-")


def fetch_json(url):
    """GET with 30 s timeout and exactly one retry on 5xx or timeout only."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for attempt in range(2):
        try:
            with opener.open(Request(url, headers={"Accept": "application/json"}),
                             timeout=TIMEOUT_SECONDS) as response:
                try:
                    return json.load(response)
                except (ValueError, UnicodeError) as exc:
                    raise CatalogError(f"{url}: HTTP {response.status}: invalid JSON") from exc
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if attempt == 0 and 500 <= status < 600:
                continue
            raise CatalogError(f"{url}: HTTP {status}") from exc
        except (URLError, OSError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            timed_out = isinstance(reason, (TimeoutError, socket.timeout))
            if attempt == 0 and timed_out:
                continue
            status = "timeout" if timed_out else "network error"
            raise CatalogError(f"{url}: {status}") from exc
        except HTTPException as exc:
            raise CatalogError(f"{url}: invalid HTTP response ({type(exc).__name__})") from exc


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    require(isinstance(value, str) and bool(value), "expected non-empty string")
    return value


def validate_resource(resource):
    require(isinstance(resource, dict), "invalid resource")
    rid = text(resource.get("resourceId"))
    require(rid.startswith(ORG + "/") and bool(rid.split("/", 1)[1]), "invalid resourceId")
    text(resource.get("displayName"))
    for field in ("labels", "attributes"):
        entries = resource.get(field, [])
        require(isinstance(entries, list), f"invalid {field}")
        for entry in entries:
            require(isinstance(entry, dict), f"invalid {field} entry")
            text(entry.get("key"))
            if field == "attributes":
                text(entry.get("value"))
            else:
                for key in ("values", "unresolvedValues"):
                    values = entry.get(key, [])
                    require(isinstance(values, list), f"invalid label {key}")
                    for value in values:
                        text(value)
    return rid


def catalog_resources(base, labelled):
    """Read every page, dedupe resourceIds and refuse truncated inventories."""
    resources, page, pages, total = {}, 0, 1, None
    while page < pages:
        query = {"query": "orgName:" + ORG, "page": page, "pageSize": 100,
                 "filters": BASE_FILTERS + ([FILTER] if labelled else []),
                 "orderBy": [{"field": "score", "value": "DESC"}], "fields": []}
        url = base + CATALOG_PATH + "?q=" + quote(json.dumps(query, separators=(",", ":")))
        payload = fetch_json(url)
        try:
            require(isinstance(payload, dict), "invalid catalog object")
            count, page_count = payload.get("resultTotal"), payload.get("resultPageTotal")
            require(type(count) is int and count >= 0, "invalid resultTotal")
            require(type(page_count) is int and page_count >= 0, "invalid resultPageTotal")
            if total is None:
                total, pages = count, page_count
            require((count, page_count) == (total, pages), "pagination totals changed")
            require(pages > 0 or total == 0, "invalid page count")
            groups = payload.get("results")
            require(isinstance(groups, list), "invalid results")
            for group in groups:
                require(isinstance(group, dict), "invalid result group")
                rows = group.get("resources")
                require(isinstance(rows, list), "invalid resources")
                for resource in rows:
                    rid = validate_resource(resource)
                    # ENDPOINT is authoritative over its repeated _scored copy.
                    if rid not in resources or group.get("groupValue") == "ENDPOINT":
                        resources[rid] = resource
            page += 1
            if page >= pages:
                require(len(resources) == total, "incomplete catalog pagination")
        except (ValueError, TypeError) as exc:
            raise CatalogError(f"{url}: HTTP 200: {exc}") from exc
    if labelled and not resources:
        raise CatalogError(f"{url}: HTTP 200: empty Free Endpoint result")
    return resources


def v1_index(url):
    payload = fetch_json(url)
    try:
        require(isinstance(payload, dict) and isinstance(payload.get("data"), list),
                "invalid models data")
        index = {}
        for model in payload["data"]:
            require(isinstance(model, dict), "invalid model")
            model_id = text(model.get("id"))
            require("/" in model_id and bool(model_id.rsplit("/", 1)[1]), "invalid model id")
            key = normalise_slug(model_id.rsplit("/", 1)[1])
            index.setdefault(key, set()).add(model_id)
        return index
    except (ValueError, TypeError) as exc:
        raise CatalogError(f"{url}: HTTP 200: {exc}") from exc


def preview(resource):
    return any(a["key"].casefold() == "preview" and a["value"].casefold() == "true"
               for a in resource.get("attributes", []))


def row_of(resource, reason, index, previous, today):
    rid = resource["resourceId"]
    slug = rid.rsplit("/", 1)[1]
    labels = resource.get("labels", [])
    publishers = sorted({v for label in labels if label["key"].casefold() == "publisher"
                         for v in label.get("values", [])})
    raw = {v.casefold() for label in labels if label["key"].casefold() == "general"
           for v in label.get("unresolvedValues", [])}
    use_class = next((name for name, values in USE_CLASS_RULES if raw & values), "other")
    matches = sorted(index.get(normalise_slug(slug), set()))
    row = {"resource_id": rid, "slug": slug, "display_name": resource["displayName"],
           "publisher": publishers[0] if publishers else None, "use_class": use_class,
           "match_reason": reason, "v1_id": matches[0] if len(matches) == 1 else None,
           "first_seen": previous.get(rid, {}).get("first_seen", today), "last_seen": today}
    if len(matches) > 1:
        row["v1_ambiguous"] = True
    return row


def read_previous(path):
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_bytes())
        require(isinstance(payload, dict) and type(payload.get("schema")) is int
                and payload["schema"] == 1, "expected schema 1")
        require(isinstance(payload.get("rows"), list), "invalid snapshot rows")
        rows = {}
        for row in payload["rows"]:
            require(isinstance(row, dict), "invalid snapshot row")
            rid = text(row.get("resource_id"))
            date.fromisoformat(text(row.get("first_seen")))
            require(rid not in rows, "duplicate snapshot resource_id")
            rows[rid] = row
        return rows
    except (ValueError, TypeError) as exc:
        raise CatalogError(f"{path}: invalid snapshot: {exc}") from exc


def snapshot_of(catalog_base, v1_base, previous, today):
    labelled = catalog_resources(catalog_base, True)
    attributes = {rid: row for rid, row in catalog_resources(catalog_base, False).items()
                  if preview(row)}
    index = v1_index(v1_base + "/v1/models")
    rows = []
    for rid in sorted(labelled.keys() | attributes.keys()):
        reason = ("label+attribute" if rid in attributes else "label") if rid in labelled else "attribute"
        rows.append(row_of(labelled.get(rid, attributes.get(rid)), reason, index, previous, today))
    counts = {"label": len(labelled), "attribute_only": len(attributes.keys() - labelled.keys()),
              "total": len(rows), "callable": sum(r["v1_id"] is not None for r in rows)}
    return {"schema": 1, "generated": today,
            "source": {"catalog_url": catalog_base + CATALOG_PATH,
                       "models_url": v1_base + "/v1/models", "org": ORG,
                       "filters": BASE_FILTERS, "label_filter": FILTER,
                       "attribute_filter": {"key": "PREVIEW", "value": "true"}},
            "counts": counts, "rows": rows}


def serialise(snapshot):
    """Stable header order and one JSON row per line keep the inventory compact."""
    header = {key: value for key, value in snapshot.items() if key != "rows"}
    prefix = json.dumps(header, ensure_ascii=False, indent=2)[:-2]
    rows = ",\n".join("    " + json.dumps(row, ensure_ascii=False) for row in snapshot["rows"])
    return (prefix + ',\n  "rows": [\n' + rows + "\n  ]\n}\n").encode("utf-8")


def base_url(name, default):
    value = os.environ.get(name, default).rstrip("/")
    parsed = urlsplit(value)
    require(parsed.scheme in ("http", "https") and bool(parsed.netloc)
            and not parsed.username and not parsed.password and not parsed.path
            and not parsed.query and not parsed.fragment, f"{name}: expected an HTTP(S) origin")
    return value


def main(argv=None, *, today=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="no write; 1 if resource IDs differ")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--json", action="store_true", help="JSON added/removed/counts on stdout")
    args = parser.parse_args(argv)
    try:
        today = today or datetime.now(timezone.utc).date().isoformat()
        previous = read_previous(args.snapshot)
        snapshot = snapshot_of(base_url("AUTOOS_NIM_CATALOG_BASE_URL", CATALOG_BASE),
                               base_url("AUTOOS_NIM_V1_BASE_URL", V1_BASE), previous, today)
        current = {row["resource_id"] for row in snapshot["rows"]}
        report = {"added": sorted(current - previous.keys()),
                  "removed": sorted(previous.keys() - current), "counts": snapshot["counts"]}
        if not args.check:
            args.snapshot.write_bytes(serialise(snapshot))
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            for key in ("added", "removed"):
                print(key + ": " + (", ".join(report[key]) or "(none)"))
            print("counts: " + " ".join(f"{key}={value}" for key, value in report["counts"].items()))
        return int(args.check and bool(report["added"] or report["removed"]))
    except (CatalogError, OSError, ValueError) as exc:
        print(f"nim-free-catalog: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
