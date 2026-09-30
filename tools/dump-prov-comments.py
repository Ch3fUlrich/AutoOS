import json

d = json.load(open('catalog/ai-registry.json', encoding='utf-8'))
p = d['providers']
for k in ('bazaarlink', 'novita_ai', 'navyai', 'bluesminds', 'agentrouter', 'arcee', 'cheaperinference', 'together_ai', 'sambanova', 'cloudflare_workers_ai'):
    v = p.get(k)
    if not v:
        print(k, 'ABSENT')
        continue
    c = v.get('$comment')
    print('=====', k, '| tier=', v.get('tier'), '| available=', v.get('available'), '| credit_usd=', v.get('credit_usd'))
    print(c if isinstance(c, str) else json.dumps(c, indent=1))
    print()
