# -*- coding: utf-8 -*-
"""_data_truth_check.py — 服务端手写数据表里的「物品 id + 名称」对客户端真值表校验。

真值源：`客户端源码/src/data/itemdata.py` 的 `DATA`（4,973 条，id → (名称, 描述)）。
被检对象：sim 目录下所有 `(id, '名称')` 形式的字面量（掉落池、商店、任务奖励…）。

判定：
  OK        id 在 itemdata 中，且名称完全一致
  不一致     id 在 itemdata 中，名称不同（手写漂移）
  未知 id   id 不在 itemdata 中（可能是招式/宠物/非物品 id，需人工确认）

用法：python _data_truth_check.py [文件...]   # 默认 drop_data.py m4_server.py
退出码：0 = 无不一致；1 = 存在不一致
"""
from __future__ import print_function

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ITEMDATA = os.path.abspath(os.path.join(
    HERE, '..', '..', '客户端源码', 'src', 'data', 'itemdata.py'))

PAIR_RE = re.compile(r'\(\s*(\d{1,6})\s*,\s*[\'"]([^\'"]{1,30})[\'"]')


def load_itemdata():
    src = io.open(ITEMDATA, encoding='utf-8', errors='replace').read()
    blk = src[src.index('DATA = {') + len('DATA = '):]
    blk = blk[:blk.index('SHAPETABLE')].rstrip().rstrip(',')
    raw = ast.literal_eval(blk)
    out = {}
    for k, v in raw.items():
        name = v[0] if isinstance(v, (tuple, list)) else v
        if isinstance(name, bytes):
            try:
                name = name.decode('gbk')
            except Exception:
                continue
        name = re.sub(r'#[rRnNcC][0-9A-Fa-f]*', '', str(name)).strip()
        out[int(k)] = name
    return out


def main(argv):
    files = argv[1:] or ['drop_data.py', 'm4_server.py']
    truth = load_itemdata()
    print('客户端 itemdata 真值：%d 条' % len(truth))

    total = ok = bad = unknown = 0
    problems = []
    for fn in files:
        path = fn if os.path.isabs(fn) else os.path.join(HERE, fn)
        src = io.open(path, encoding='utf-8', errors='replace').read()
        seen = set()
        for m in PAIR_RE.finditer(src):
            iid, name = int(m.group(1)), m.group(2)
            if iid not in truth:
                unknown += 1
                continue
            key = (iid, name)
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if truth[iid] == name:
                ok += 1
            else:
                bad += 1
                line = src.count('\n', 0, m.start()) + 1
                problems.append((os.path.basename(path), line, iid, name, truth[iid]))

    print('已校验 (id,名称) 对：%d ；一致 %d ；不一致 %d ；非物品 id 跳过 %d'
          % (total, ok, bad, unknown))
    if problems:
        print('-' * 78)
        print('%-18s %-6s %-8s %-14s %s' % ('文件', '行', 'id', '服务端名', '客户端真名'))
        for f, ln, iid, got, want in problems:
            print('%-18s %-6d %-8d %-14s %s' % (f, ln, iid, got, want))
    print('-' * 78)
    print('结论：%s' % ('全部一致' if bad == 0 else '存在 %d 处名称漂移' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
