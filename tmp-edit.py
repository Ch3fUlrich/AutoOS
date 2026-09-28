import io, json

P = 'catalog/ai-registry.json'
s = io.open(P, encoding='utf-8').read()
ROUTES_AT = s.index('"routes": {')


def route_span(rid):
    i = s.index('"%s": {' % rid, ROUTES_AT)
    j = s.index('\n    },\n', i)
    return i, j


def replace_once(old, new, where=None):
    global s
    span = where or (0, len(s))
    seg = s[span[0]:span[1]]
    assert seg.count(old) == 1, (span, seg.count(old), old[:60])
    s = s[:span[0]] + seg.replace(old, new) + s[span[1]:]


def gate(rid, entries):
    """Insert gated unavailable_legs entries at the top of a route's block."""
    i, j = route_span(rid)
    text = ''
    for leg, note in entries:
        text += ('        "%s": {\n          "$comment": %s,\n'
                 '          "available": false\n        },\n'
                 % (leg, json.dumps(note, ensure_ascii=False)))
    old = '      "unavailable_legs": {\n'
    new = old + text
    # ROUTES_AT moves as we edit; re-locate each time.
    i, j = route_span(rid)
    replace_once(old, new, (i, j))


UNPRICED = ('FREEKEYS-2 documented tail fallback, NOT a live leg: providers.%s is tier '
            'credit (a finite $%s vendor grant) and models.%s carries no real price '
            '(price_in/price_out 0 = "no price on file"), so '
            'tools/autoos_resolver.py credit_leg_priced() refuses it fail-closed and '
            'gateway_legs() drops it here too - an unpriced grant renders to the gateway '
            'as a free leg and would drain it with nothing in the ledger to show it. '
            'Probe-passed (FREEKEYS-1: tool_calls proven). GATE LIFTED BY: a real '
            'per-token price on the model row, then deleting this entry.')

F2 = ('FREEKEYS-2 (D-141 item 3): the free band is ordered ahead of the paid legs and '
      'gained the probe-passed free grants (%s). Every combo keeps its legs on distinct '
      'providers so an OmniRoute priority fall-through on a 429 lands on another '
      'provider, not on the same rate limit. The %s legs trail the route, gated: they '
      'are unpriced credit and are dropped until a price lands. Reset-aware cooldowns '
      '(PLAN section 18) untouched.')

# --- deepseek-v4.1-flash: the pinned group had one rendered leg, no fallback ---
i, j = route_span('deepseek-v4.1-flash')
replace_once('      "legs": [\n        "deepseek/deepseek-flash",\n',
             '      "$comment": "Pinned per-model group (mapping doc Open choice 11), so '
             'every leg stays a DeepSeek V4 Flash weight - but it rendered ONE model, '
             'which is no fallback at all. FREEKEYS-2 adds the free bazaarlink copy '
             '(providers.bazaarlink, tier free, tool_calls proven, 1M advertised) behind '
             'the native head, so a 429 on DeepSeek API falls to a connection that costs '
             'nothing. The opencode-zen leg stays gated (client-bound, 402/429 probe).",\n'
             '      "legs": [\n        "deepseek/deepseek-flash",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n', (i, j))

# --- gemini-3.8-flash: pinned group, gains a documented (gated) credit tail ---
i, j = route_span('gemini-3.8-flash')
replace_once('      "legs": [\n        "gemini/gemini-3.8-flash",\n',
             '      "$comment": "Pinned per-model group: both legs are the same Gemini '
             '3.8 Flash weights, the gateway connection and the OpenRouter mirror. The '
             'OpenRouter leg stays gated (credits exhausted, operator 2026-09-25), so '
             'this combo answers on one provider today; its cross-provider redundancy is '
             'in the tier routes, which all carry the Gemini free leg plus the '
             'FREEKEYS-1 grants. gemini-3.5-flash on DeepInfra is the documented tail '
             'fallback once it is priced.",\n'
             '      "legs": [\n        "gemini/gemini-3.8-flash",\n'
             '        "deepinfra/google/gemini-3.5-flash",\n', (i, j))
gate('gemini-3.8-flash', [('deepinfra/google/gemini-3.5-flash',
                           UNPRICED % ('deepinfra', '5', 'google/gemini-3.5-flash'))])

# --- t1-orchestrator-free-only ---
i, j = route_span('t1-orchestrator-free-only')
replace_once('      "$comment": "free fallback so t1 stays servable while OpenRouter/DeepSeek credit is out (L0 2026-09-27T16:00:38Z every tier keeps a free leg); gemini-3.8-flash context 1M per Google AI Studio model card - verify against the registry model entry",',
             '      "$comment": "free fallback so t1 stays servable while OpenRouter/DeepSeek credit is out (L0 2026-09-27T16:00:38Z every tier keeps a free leg); gemini-3.8-flash context 1M per Google AI Studio model card - verify against the registry model entry. '
             + F2 % ('bazaarlink 1M, scaleway Qwen3-235B, nebius GLM-5.3-Flash', 'no credit')
             + ' A free-only route takes no credit leg at all: the grants are metered money, not free.', (i, j))
replace_once('        "opencode-zen/muse-spark-1.3-contributor-free",\n        "gemini/gemini-3.8-flash"\n      ],',
             '        "opencode-zen/muse-spark-1.3-contributor-free",\n'
             '        "gemini/gemini-3.8-flash",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n'
             '        "scaleway/qwen3-235b-a22b-instruct-2507",\n'
             '        "nebius/zai-org/GLM-5.3-Flash"\n      ],',
             route_span('t1-orchestrator-free-only'))

# --- t2-worker ---
i, j = route_span('t2-worker')
replace_once('        "antigravity/gemini-3.7-flash-high",\n',
             '        "antigravity/gemini-3.7-flash-high",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n'
             '        "scaleway/qwen3-235b-a22b-instruct-2507",\n'
             '        "scaleway/mistral-small-3.2-24b-instruct-2506",\n'
             '        "nebius/zai-org/GLM-5.2",\n', (i, j))
replace_once('        "free_ai/qwen7b"\n      ],',
             '        "free_ai/qwen7b",\n'
             '        "morph/morph-dsv4flash",\n'
             '        "deepinfra/google/gemini-3.1-flash-lite"\n      ],',
             route_span('t2-worker'))
gate('t2-worker', [('morph/morph-dsv4flash', UNPRICED % ('morph', '10', 'morph-dsv4flash')),
                   ('deepinfra/google/gemini-3.1-flash-lite',
                    UNPRICED % ('deepinfra', '5', 'google/gemini-3.1-flash-lite'))])
i, j = route_span('t2-worker')
replace_once(' It is the same leg t2-worker-free-only already ends with;',
             ' FREEKEYS-2 (D-141 item 3) ordered the band free -> credit -> paid and '
             'put the probe-passed free grants (bazaarlink 1M, scaleway Qwen3-235B, '
             'scaleway Mistral Small 3.2, nebius GLM-5.2) behind the two Gemini legs and '
             'ahead of every paid one; this stopgap keeps its documented last place, so '
             'it is still reached only when everything ahead of it failed. It is the same '
             'leg t2-worker-free-only already ends with;', (i, j))

# --- t2-worker-free-only ---
i, j = route_span('t2-worker-free-only')
replace_once('      "legs": [\n        "gemini/gemini-3.8-flash",\n        "antigravity/gemini-3.7-flash-medium",\n',
             '      "$comment": "FREEKEYS-2 (D-141 item 3): '
             + F2 % ('bazaarlink 1M, scaleway Qwen3-235B, scaleway Mistral Small 3.2, nebius GLM-5.2', 'no credit')
             + ' Free-only: it takes no `credit` grant. groq/cerebras stay gated (deny-groq, '
               '402 billing) and free_ai/qwen7b stays the documented stopgap last leg.",\n'
             '      "legs": [\n        "gemini/gemini-3.8-flash",\n        "antigravity/gemini-3.7-flash-medium",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n'
             '        "scaleway/qwen3-235b-a22b-instruct-2507",\n'
             '        "scaleway/mistral-small-3.2-24b-instruct-2506",\n'
             '        "nebius/zai-org/GLM-5.2",\n', (i, j))

# --- t3-driver ---
i, j = route_span('t3-driver')
replace_once('        "mistral/mistral-code-latest",\n',
             '        "mistral/mistral-code-latest",\n'
             '        "scaleway/mistral-small-3.2-24b-instruct-2506",\n'
             '        "nebius/zai-org/GLM-5.2",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n'
             '        "scaleway/qwen3-235b-a22b-instruct-2507",\n', (i, j))
replace_once('        "meta_api/muse-spark-1.3-contributor"\n      ],',
             '        "meta_api/muse-spark-1.3-contributor",\n'
             '        "morph/morph-glm52-744b",\n'
             '        "deepinfra/google/gemini-3.7-flash"\n      ],',
             route_span('t3-driver'))
gate('t3-driver', [('morph/morph-glm52-744b', UNPRICED % ('morph', '10', 'morph-glm52-744b')),
                   ('deepinfra/google/gemini-3.7-flash',
                    UNPRICED % ('deepinfra', '5', 'google/gemini-3.7-flash'))])
i, j = route_span('t3-driver')
replace_once('with the dead leg gone L12 and L13 move up one.',
             'with the dead leg gone L12 and L13 move up one. FREEKEYS-2 (D-141 item 3) '
             'kept that measured head (200, 125 rpm, 625k tpm) in first place and put the '
             'new FREEKEYS-1 free grants (scaleway Mistral Small 3.2, nebius GLM-5.2, '
             'bazaarlink 1M, scaleway Qwen3-235B) straight behind it, ahead of every paid '
             'leg; the two credit legs trail the route gated, unpriced until a price lands.',
             (i, j))

# --- t3-driver-free-only ---
i, j = route_span('t3-driver-free-only')
replace_once('        "cerebras/qwen-3.8-27b",\n        "free_ai/qwen7b"\n      ],',
             '        "cerebras/qwen-3.8-27b",\n'
             '        "scaleway/mistral-small-3.2-24b-instruct-2506",\n'
             '        "nebius/zai-org/GLM-5.3-Flash",\n'
             '        "bazaarlink/deepseek/deepseek-v4-flash-0731free:free",\n'
             '        "scaleway/qwen3-235b-a22b-instruct-2507",\n'
             '        "free_ai/qwen7b"\n      ],', (i, j))
replace_once('      "legs": [\n        "groq/qwen/qwen3.8-27b",',
             '      "$comment": "FREEKEYS-2 (D-141 item 3): '
             + F2 % ('scaleway Mistral Small 3.2, nebius GLM-5.3-Flash, bazaarlink 1M, scaleway Qwen3-235B', 'no credit')
             + ' Free-only, so groq/cerebras stay gated (deny-groq 400/413, 401 credits) and '
               'free_ai/qwen7b stays the last-resort stopgap.",\n'
             '      "legs": [\n        "groq/qwen/qwen3.8-27b",',
             route_span('t3-driver-free-only'))

io.open(P, 'w', encoding='utf-8').write(s)
json.loads(s)
print('registry edited, parses clean')
