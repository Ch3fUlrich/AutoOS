#!/usr/bin/env python3
"""Read-only weekly Codex catalog check; never applies a registry bump.

No model-catalog weekly timer exists in tools. Suggested cron (repo cwd):
0 9 * * 1 python3 tools/codex-newest-check.py

Exit codes, kept distinct so a caller can act without parsing the text:
  Exit 0  the gateway lists no sol id newer than
          providers.cx.model_policy.current - already newest, nothing to do;
  Exit 1  the gateway probe failed - nothing was decided and nothing was
          proposed (no answer came back, not "no update exists");
  Exit 3  a bump proposal was written for review (PROPOSAL_REL) - a human must
          look. Exit 3 is not a failure: only 3 means a proposal exists, only
          1 means no answer came back.
"""
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler
import autoos_gateway_key
import registry

# Review output, not a catalog change: printed to stdout AND written where
# .gitignore covers it (finding B4), so no `git add -A` can sweep it into a
# commit of somebody else's review queue.
PROPOSAL_REL = 'scratch/codex-newest-proposal.md'


def version(model):
    """Numeric version tuple from registry.parse_sol_id - the one sol-id grammar."""
    parsed = registry.parse_sol_id(model)
    return parsed[1] if parsed else ()


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
        target = registry.ROOT / PROPOSAL_REL
        target.parent.mkdir(exist_ok=True)
        target.write_text(text, encoding='utf-8')
    else:
        print('No newer sol ID; registry unchanged.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
