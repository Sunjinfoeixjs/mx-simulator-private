# -*- coding: utf-8 -*-
"""按时间段打印日志：python logwin.py <log> <HH:MM:SS> <HH:MM:SS>"""
import io
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
path, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3]
lines = io.open(path, encoding='utf-8', errors='replace').read().splitlines()
out = []
on = False
for ln in lines:
    i = ln.find('[')
    ts = ln[i + 1:i + 9] if i >= 0 and ln[i + 1:i + 3].isdigit() else ''
    if ts == t0:
        on = True
    if on:
        out.append(ln)
    if ts and ts > t1:
        break
print('lines', len(out))
for ln in out:
    print(ln)
