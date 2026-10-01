import json

d = json.load(open('catalog/ai-registry.json', encoding='utf-8'))
prov = d['providers']
models = d['models']


def tier_of(leg):
    prefix, _, mid = leg.partition('/')
    pid = prefix if prefix in prov else next(
        (k for k, v in prov.items() if isinstance(v, dict) and v.get('omniroute_id') == prefix), None)
    p = prov.get(pid) or {}
    m = models.get(mid) or {}
    t = m['tier'] if 'tier' in m else p.get('tier')
    return pid, mid, t, p.get('trains_on_prompts'), m.get('trains_on_prompts')


for rid in ('t2-worker', 't3-driver', 't2-worker-free-only', 't3-driver-free-only'):
    r = d['routes'][rid]
    print('==', rid, '==')
    print('  surfaces:', json.dumps(r.get('surfaces'))[:300])
    print('  unavailable:', list(r.get('unavailable_legs') or {}))
    for leg in r.get('legs') or []:
        pid, mid, t, ptp, mtp = tier_of(leg)
        print('   %-55s tier=%-12s ptp=%s mtp=%s' % (leg, t, ptp, mtp))
