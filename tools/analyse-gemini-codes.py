import json, collections, io, os, re, sys

log = os.path.join(os.path.expanduser("~"), ".omniroute", "logs", "application", "app.log")
day = sys.argv[1] if len(sys.argv) > 1 else '2026-09-30'
code_re = re.compile(r'lastErrorCode=([0-9.]+)')
err_re = re.compile(r'lastError=([^|]+)$')
codes = collections.Counter()
errs = collections.Counter()
n = 0
for line in io.open(log, encoding='utf-8', errors='replace'):
    if day not in line or 'gemini' not in line.lower():
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    msg = str(obj.get('msg') or '')
    if 'cooling down' not in msg:
        continue
    n += 1
    m = code_re.search(msg)
    codes[m.group(1) if m else '?'] += 1
    e = err_re.search(msg)
    errs[(e.group(1).strip() if e else '?')] += 1
print('gemini cooling-down lines on', day, ':', n)
print('by lastErrorCode:', codes.most_common(10))
print('by lastError:', errs.most_common(10))
