"""tools/autoos_oc_fallback.py - Claude limit to OpenCode fallback helpers."""
import os
import urllib.error
import urllib.request

EXIT_OC_FALLBACK_DOWN = 15
CLAUDE_LIMIT_MARKERS = (
    "usage limit reached", "you've hit your limit", "you have hit your limit",
    "claude usage limit", "5-hour limit reached", "weekly limit reached",
)
FALLBACK_MODELS = ("mimo-v2.6-flash", "muse-spark-1.3")
OPENCODE_IDS = {"mimo-v2.6-flash": "omniroute/mimo-v2.6-flash", "muse-spark-1.3": None}
FREE_MODELS = {"muse-spark-1.3"}


def claude_limit_stop(client_name: str, stop: str | None, is_rate_limit=None) -> bool:
    if client_name != "claude" or stop is None:
        return False
    low = stop.lower()
    return any(m in low for m in CLAUDE_LIMIT_MARKERS) or bool(callable(is_rate_limit) and is_rate_limit(stop))


def pick_fallback_model(env=None) -> str:
    val = (os.environ if env is None else env).get("AUTOOS_OPENCODE_FALLBACK_MODEL")
    if not val:
        return FALLBACK_MODELS[0]
    if val in FALLBACK_MODELS:
        return val
    raise ValueError("invalid AUTOOS_OPENCODE_FALLBACK_MODEL: %r (must be one of %s)"
                     % (val, ", ".join(FALLBACK_MODELS)))


def opencode_url(env=None) -> str:
    url = (os.environ if env is None else env).get("AUTOOS_OPENCODE_URL") or "http://127.0.0.1:4096"
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("invalid AUTOOS_OPENCODE_URL scheme: %r (must start with http:// or https://)" % url)
    return url


def opencode_healthy(url: str, timeout: float = 3.0, opener=None) -> tuple[bool, str]:
    target = f"{url.rstrip('/')}/global/health"
    call = opener if opener is not None else urllib.request.urlopen
    try:
        resp = call(target, timeout=timeout)
        status = getattr(resp, "status", getattr(resp, "code", 200))
        close = getattr(resp, "close", None)
        if close:
            close()
        return (True, "ok") if 200 <= status < 300 else (False, f"{url}: HTTP {status}")
    except urllib.error.HTTPError as exc:
        return False, f"{url}: HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"{url}: {getattr(exc, 'reason', exc)}"
    except Exception as exc:
        return False, f"{url}: {exc}"


def fallback_decision(client_name: str, stop: str | None, env=None,
                      is_rate_limit=None, opener=None) -> dict:
    if not claude_limit_stop(client_name, stop, is_rate_limit):
        return {"switch": False}
    src = os.environ if env is None else env
    try:
        url, model = opencode_url(src), pick_fallback_model(src)
    except ValueError as exc:
        msg = ("opencode fallback refused: bad fallback config (%s); "
               "Claude is rate-limited - not retrying Claude" % exc)
        return {"switch": False, "refused": True, "rc": EXIT_OC_FALLBACK_DOWN, "message": msg}
    healthy, reason = opencode_healthy(url, opener=opener)
    if healthy:
        return {
            "switch": True,
            "client": "opencode",
            "model": model,
            "opencode_model": OPENCODE_IDS[model],
            "free": model in FREE_MODELS,
            "url": url,
        }
    msg = ("opencode fallback refused: %s; Claude is rate-limited "
           "and the fallback server is down - not retrying Claude" % reason)
    return {"switch": False, "refused": True, "rc": EXIT_OC_FALLBACK_DOWN, "message": msg}
