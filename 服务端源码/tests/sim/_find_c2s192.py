# -*- coding: utf-8 -*-
"""列出 net.py 中所有 C2S/C2GS 常量（按值排序），定位 192 归属。"""
import io
import re

P = r"C:\Users\Administrator\MX\Claw\src\net.py"
t = io.open(P, encoding='utf-8', errors='replace').read()
pairs = []
for m in re.finditer(r'^(C2S\w*|C2GS\w*)\s*=\s*(\d+)\s*$', t, re.M):
    pairs.append((int(m.group(2)), m.group(1)))
pairs.sort()
print('C2S/C2GS 常量共 %d 个，值域 %d..%d' % (len(pairs), pairs[0][0], pairs[-1][0]))
print('')
print('--- 150..200 区间 ---')
for v, n in pairs:
    if 150 <= v <= 200:
        print('  %3d  %s' % (v, n))
print('')
print('--- 最大值附近 ---')
for v, n in pairs[-15:]:
    print('  %3d  %s' % (v, n))
print('')
print('192 是否在表内: %s' % [n for v, n in pairs if v == 192])
