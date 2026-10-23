#!/usr/bin/env python3
"""Read-only weekly Codex catalog check; never applies a registry bump.

No model-catalog weekly timer exists in tools. Suggested cron (repo cwd):
0 9 * * 1 python3 tools/codex-newest-check.py
Exit 3 means review scratch/codex-newest-proposal.md; exit 1 means probe failed.
"""
import json
import os
import re
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
import autoos_gateway_key
import registry


def version(model):
    """Numeric version tuple, padded so 6 and 6.0 compare equally."""
    match = re.fullmatch(r'(?:cx/)?gpt-(\d+(?:\.\d+)*)-sol(?:-[a-z]+)?', model)
    return tuple((list(map(int, match[1].split('.'))) + [0] * 8)[:8]) if match else ()


def proposal(models, current):
    newer = sorted({m for m in models if m.startswith('cx/') and version(m) > version(current)}, key=version)
    if not newer:
        return 0, ''
    return 3, '# Codex newest-only bump proposal\n\nCurrent: `%s`\n\nReview gateway IDs: %s\n\nNo registry changes applied.\n' % (current, ', '.join(newer))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward a client key off loopback.


def main():
    current = registry.load(registry.DEFAULT_REGISTRY_PATH)['providers']['cx']['model_policy']['current']
    autoos_gateway_key._QUIET = True
    try:
        env = dict(os.environ, AUTOOS_OMNIROUTE_URL='http://127.0.0.1:20128')
        key = autoos_gateway_key.resolve_client_key(env=env)
        request = Request('http://127.0.0.1:20128/v1/models', headers={'Authorization': 'Bearer ' + key})
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            payload = json.load(response)
        models = sorted(row['id'] for row in payload['data'] if row.get('id', '').startswith('cx/') and version(row['id']))
    except Exception:
        print('Codex catalog probe failed (details suppressed to protect client credentials).')
        return 1
    print('Gateway codex sol IDs: ' + (', '.join(models) or '(none)'))
    code, text = proposal(models, current)
    if text:
        print(text)
        target = registry.ROOT / 'scratch/codex-newest-proposal.md'
        target.parent.mkdir(exist_ok=True)
        target.write_text(text, encoding='utf-8')
    else:
        print('No newer sol ID; registry unchanged.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
