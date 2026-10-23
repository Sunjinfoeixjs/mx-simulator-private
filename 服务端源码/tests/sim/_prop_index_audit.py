# -*- coding: utf-8 -*-
"""_prop_index_audit.py — 核对服务端 S2CPropChange 下标 vs 客户端 hero.ITEM_ALL 真值表。

背景：客户端 nethero.S2CPropChange 用 `hero.ITEM_ALL[type]` 直接取字段，
所以下标必须与服务端常量逐一对齐；错位会静默写到别的字段（不报错、不崩）。

用法：python _prop_index_audit.py [--verbose]
输出：索引表 + 服务端常量比对结果 + 退出码（0=全对，1=有错位）
"""
from __future__ import print_function

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_HERO = os.path.abspath(os.path.join(
    HERE, '..', '..', '客户端源码', 'src', 'hero.py'))
SERVER_M4 = os.path.abspath(os.path.join(HERE, 'm4_server.py'))

CTOR_RE = re.compile(
    r'\b(C[A-Za-z]*Item[A-Za-z]*|COrgItem|CTitleItem)\s*\(\s*([^,]+?)\s*,\s*'
    r"'(m_[A-Za-z_]+|sOrg)'")

# 服务端注释里声明的语义（用于判定错位后"实际指到谁"）
SERVER_EXPECT = {
    'PROP_IDX_HP': 'm_HP',
    'PROP_IDX_MP': 'm_MP',
    'PROP_IDX_FP': 'm_FP',
    'PROP_IDX_AT': 'm_At',
    'PROP_IDX_DF': 'm_Df',
    'PROP_IDX_DP': 'm_Dp',
    'PROP_IDX_EXP': 'm_Exp',
    'PROP_IDX_NEXTEXP': 'm_NextExp',
    'PROP_IDX_GOLD': 'm_Gold',
}


def parse_item_all():
    src = io.open(CLIENT_HERO, encoding='utf-8', errors='replace').read()
    i = src.index('ITEM_ALL = (')
    # 元组末尾紧跟下一个顶层 def，用它做边界（末项以 "))" 收尾，没有独占一行的 ")"）
    j = src.index('def CreateDlgItem', i)
    block = src[i:j]
    rows = []
    for m in CTOR_RE.finditer(block):
        ctor, typ, name = m.group(1), m.group(2).strip(), m.group(3)
        rows.append((len(rows), ctor, typ, name))
    return rows


def parse_server_idxs():
    src = io.open(SERVER_M4, encoding='utf-8', errors='replace').read()
    out = {}
    for m in re.finditer(r'^(PROP_IDX_[A-Z]+)\s*=\s*(\d+)', src, re.M):
        out[m.group(1)] = int(m.group(2))
    line = re.search(r'^(PROP_IDX_EXP,\s*PROP_IDX_NEXTEXP,\s*PROP_IDX_GOLD)\s*=\s*([\d,\s]+)$',
                     src, re.M)
    if line:
        names = [x.strip() for x in line.group(1).split(',')]
        vals = [int(x.strip()) for x in line.group(2).split(',')]
        for n, v in zip(names, vals):
            out[n] = v
    return out


def main():
    rows = parse_item_all()
    if not rows:
        print('!! 解析 ITEM_ALL 失败')
        return 2
    name_at = dict((i, n) for i, _c, _t, n in rows)
    total = len(rows)

    print('ITEM_ALL 共 %d 项（下标 0..%d）' % (total, total - 1))
    print('-' * 72)
    for i, ctor, typ, name in rows:
        mark = ''
        if name in ('m_HP', 'm_HPMax', 'm_MP', 'm_FP', 'm_Exp', 'm_NextExp',
                    'm_Gold', 'm_At', 'm_Df', 'm_Dp'):
            mark = '   <=='
        print('%3d  %-26s %-22s %s%s' % (i, name, ctor, typ, mark))

    print('-' * 72)
    idxs = parse_server_idxs()
    bad = 0
    print('服务端常量比对：')
    for k in sorted(SERVER_EXPECT):
        if k not in idxs:
            print('  %-18s 未找到定义' % k)
            continue
        got = idxs[k]
        actual = name_at.get(got, '<越界>')
        want = SERVER_EXPECT[k]
        ok = (actual == want)
        if not ok:
            bad += 1
        print('  %-18s = %-3d  客户端实际=% -20s  期望=%s  %s'
              % (k, got, actual, want, 'OK' if ok else '★错位'))
    print('-' * 72)
    print('末 8 项（不走 CreateProp 主循环）：%s'
          % ', '.join('%d:%s' % (i, name_at[i]) for i in range(total - 8, total)))
    print('结论：%d 处错位' % bad)
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
