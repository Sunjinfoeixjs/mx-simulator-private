# -*- coding: utf-8 -*-
"""核对「编码空间分母」的三种来源是否一致（一次性脚本，用完即删）。

分母错了，调试台列出的「未实现清单」就会漏项——使用者会漏做工作。
"""
import io
import json
import os
import re

MX = r'C:\Users\Administrator\MX'
D = os.path.join(MX, '协议资料', '字典')
CLI = os.path.join(MX, '客户端源码')


def read(p):
    return io.open(p, encoding='utf-8', errors='replace').read()


# ── C2S 来源 A：字典 protocol_functions.json（调试台在用）──
doc = json.loads(read(os.path.join(D, 'protocol_functions.json')))
a = set()
for _fn, body in (doc.get('modules') or {}).items():
    for f in body.get('functions') or []:
        if f.get('dir') == 'C2S' and f.get('opcode') is not None:
            a.add(int(f['opcode']))

# ── C2S 来源 B：全表 protocol_c2s_full.txt ──
b = set()
for ln in read(os.path.join(D, 'protocol_c2s_full.txt')).splitlines():
    m = re.match(r'\s*(\d+)\s+(\S+)', ln)
    if m and not ln.lstrip().startswith('#'):
        b.add(int(m.group(1)))

# ── C2S 来源 C：客户端源码里的 C2S_* 常量 ──
c = {}
for root, _dirs, files in os.walk(CLI):
    if '__pycache__' in root:
        continue
    for fn in files:
        if not fn.endswith('.py'):
            continue
        try:
            txt = read(os.path.join(root, fn))
        except OSError:
            continue
        for m in re.finditer(r'^\s*(C2S_\w+)\s*=\s*(?:0x([0-9a-fA-F]+)|(\d+))\s*$',
                             txt, re.M):
            v = int(m.group(2), 16) if m.group(2) else int(m.group(3))
            c.setdefault(v, m.group(1))

print('C2S 分母三来源')
print('  A 字典 protocol_functions.json : %d 个' % len(a))
print('  B 全表 protocol_c2s_full.txt   : %d 个' % len(b))
print('  C 客户端源码 C2S_* 常量        : %d 个' % len(c))
print('  A∩C = %d    A 有 C 无 = %s' % (len(a & set(c)), sorted(a - set(c))))
print('  C 有 A 无 = %s' % sorted(set(c) - a))
print('  A 有 B 无 = %s' % sorted(a - b))
print('  B 有 A 无 = %s' % sorted(b - a))
print()

# ── S2C 来源 A：客户端 net.py 路由（调试台在用）──
txt = read(os.path.join(CLI, 'src', 'net.py'))
A2 = {}
for m in re.finditer(r'^\s*(\d+)\s*:\s*[\'"](\w+)\.(\w+)[\'"]', txt, re.M):
    A2.setdefault(int(m.group(1)), (m.group(2), m.group(3)))
print('S2C 分母两来源')
print('  A 客户端 net.py 路由表 : %d 个' % len(A2))

# ── S2C 来源 B：全表 protocol_s2c_full.txt ──
B2 = set()
for ln in read(os.path.join(D, 'protocol_s2c_full.txt')).splitlines():
    if ln.lstrip().startswith('#') or not ln.strip():
        continue
    parts = [p for p in re.split(r'\s+', ln.strip()) if p]
    if len(parts) >= 2 and re.fullmatch(r'\d+', parts[0]):
        B2.add(int(parts[0]))
print('  B 全表 protocol_s2c_full.txt tag 列 : %d 个' % len(B2))
print('  A(B) 与 B(B) 无法直接比：前者是 opcode，后者含 tag+行去重，仅列数目供参考')
print()

# ── 另一个可能口径：字典里带 opcode 的 S2C 条目 ──
s2c_with_op = set()
for _fn, body in (doc.get('modules') or {}).items():
    for f in body.get('functions') or []:
        if f.get('dir') == 'S2C' and f.get('opcode') is not None:
            s2c_with_op.add(int(f['opcode']))
print('  字典里 dir=S2C 且带 opcode 的条目 : %d 个' % len(s2c_with_op))
print('  opcode 范围: %s' % (sorted(s2c_with_op)[:5], ))
print()

# ── 调试台当前分母 ──
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tap as T                                                    # noqa: E402
p = T.Protocol()
print('调试台分母')
print('  C2S table : %d' % len(p.table('C2S')))
print('  S2C table : %d' % len(p.table('S2C')))
print('  S2C overlay 独有（net.py 没有的）: %s'
      % sorted(set(T.OVERLAY['S2C']) - set(p.s2c_routes)))
print('  net.py 有路由但 S2C table 没进 : %s'
      % sorted(set(p.s2c_routes) - {r['op'] for r in p.table('S2C')}))
