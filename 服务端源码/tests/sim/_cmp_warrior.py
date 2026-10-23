# -*- coding: utf-8 -*-
"""比对 core/warrior.py：当前管线 fresh vs 磁盘，定位首个差异的结构含义。"""
import io
import os
import sys

CLAW = r"C:\Users\Administrator\MX\Claw"
sys.path.insert(0, os.path.join(CLAW, 'tools'))
sys.path.insert(0, os.path.join(CLAW, 'tools', 'opmap'))
import u6_patch26  # noqa
from uncompyle6.main import decompile_file  # noqa

SRC = os.path.join(CLAW, 'src')
PYC = os.path.join(SRC, 'core', 'warrior.pyc')
PY = os.path.join(SRC, 'core', 'warrior.py')


class Null:
    def write(self, *a):
        pass

    def flush(self):
        pass


buf = io.StringIO()
old = sys.stdout
sys.stdout = Null()
try:
    decompile_file(PYC, buf)
    fresh = buf.getvalue()
finally:
    sys.stdout = old

with io.open(PY, encoding='utf-8', errors='replace') as f:
    disk = f.read()

fl = fresh.replace('\r\n', '\n').splitlines()
dl = disk.replace('\r\n', '\n').splitlines()
print('fresh 行数=%d  disk 行数=%d' % (len(fl), len(dl)))

# 列出全部差异行（前 20 处）
diffs = []
i = j = 0
while i < len(fl) and j < len(dl) and len(diffs) < 20:
    if fl[i] == dl[j]:
        i += 1
        j += 1
        continue
    # 尝试同步
    k = 1
    while k < 6 and i + k < len(fl) and fl[i + k] != dl[j]:
        k += 1
    if i + k < len(fl) and fl[i + k] == dl[j]:
        for x in range(k):
            diffs.append((i + 1 + x, fl[i + x], '<无>'))
        i += k
        continue
    k = 1
    while k < 6 and j + k < len(dl) and fl[i] != dl[j + k]:
        k += 1
    if j + k < len(dl) and fl[i] == dl[j + k]:
        for x in range(k):
            diffs.append((i + 1, '<无>', dl[j + x]))
        j += k
        continue
    diffs.append((i + 1, fl[i], dl[j]))
    i += 1
    j += 1

print('差异处（fresh 行号 / fresh / disk）共 %d:' % len(diffs))
for ln, a, b in diffs:
    print('  L%-6d fresh=%-46r disk=%r' % (ln, a[:44], b[:44]))

print('')
print('---- fresh 300-345 ----')
for i in range(299, min(345, len(fl))):
    print('%6d | %s' % (i + 1, fl[i]))
print('')
print('---- disk 300-345 ----')
for i in range(299, min(345, len(dl))):
    print('%6d | %s' % (i + 1, dl[i]))
