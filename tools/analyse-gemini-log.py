import json, collections, io, os, re, sys

log = os.path.join(os.path.expanduser("~"), ".omniroute", "logs", "application", "app.log")
day = sys.argv[1] if len(sys.argv) > 1 else '2026-09-30'
RL = re.compile(r'(lastErrorCode=429|status[=: ]429|rate limit)', re.I)

gem_cool = 0
cool_tot = 0
gem_429 = 0
r429_tot = 0
cool_by_prov = collections.Counter()
by_day_cool = collections.Counter()
by_day_429 = collections.Counter()
examples = []
for line in io.open(log, encoding='utf-8', errors='replace'):
    try:
        obj = json.loads(line)
    except Exception:
        obj = None
    if obj is None:
        continue
    ts = str(obj.get('time') or obj.get('timestamp') or '')
    d = ts[:10]
    msg = str(obj.get('msg') or obj.get('message') or '')
    if 'cooling down' in msg:
        by_day_cool[d] += 1
        prov = msg.split('|')[0].strip()
        if d == day:
            cool_tot += 1
            cool_by_prov[prov] += 1
            if prov == 'gemini':
                gem_cool += 1
    if RL.search(msg):
        by_day_429[d] += 1
        if d == day:
            r429_tot += 1
            if 'gemini' in msg.lower() or obj.get('module') == 'gemini':
                gem_429 += 1
            if len(examples) < 5:
                examples.append((obj.get('module'), msg[:220]))

print('DAY', day)
print('gemini cooling-down lines:', gem_cool)
print('all providers cooling-down lines:', cool_tot)
print('gemini 429-ish lines:', gem_429)
print('all providers 429-ish lines:', r429_tot)
print('cool by provider (today):', cool_by_prov.most_common(15))
print('cool by day (last 5):', by_day_cool.most_common()[-5:])
print('429 by day (last 5):', by_day_429.most_common()[-5:])
for m, e in examples:
    print('429EX', m, '|', e)
