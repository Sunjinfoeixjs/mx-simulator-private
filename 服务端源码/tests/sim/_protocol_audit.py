# -*- coding: utf-8 -*-
"""_protocol_audit.py — 服务端下发 opcode vs 客户端真实分派表 的一致性审计。

真值源：`客户端源码/src/net.py` 的 `NET_FUNCTION_NAME`（opcode → (模块, 处理函数)）。
服务端侧：`m4_server.py` 里所有 `make_s2c(OP_XXX, ...)` 实际用到的 opcode。

审计三件事：
  1. 服务端发出的 opcode 客户端有没有登记（没登记 = 帧被静默丢弃）
  2. 登记的处理函数在对应模块里是否真的存在（改名/删函数 = 静默丢弃）
  3. 客户端登记了但服务端从不发的 opcode（只作参考，不算错）

用法：python _protocol_audit.py
退出码：0 = 无致命项；1 = 存在未登记 opcode 或缺失处理函数
"""
from __future__ import print_function

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', '..', '客户端源码', 'src'))
NET_PY = os.path.join(SRC, 'net.py')
M4 = os.path.join(HERE, 'm4_server.py')


def _block(src, start_marker):
    i = src.index(start_marker)
    i = src.index('{', i)
    depth = 0
    for j in range(i, len(src)):
        c = src[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise ValueError('未找到块结束')


def load_client_table():
    src = io.open(NET_PY, encoding='utf-8', errors='replace').read()
    table = ast.literal_eval(_block(src, 'NET_FUNCTION_NAME'))
    return {int(k): v for k, v in table.items()}


def load_server_ops():
    src = io.open(M4, encoding='utf-8', errors='replace').read()
    # 常量含 OP_* 与个别 S2C_* 别名（如 S2C_NPC_BUYLIST=147）
    consts = {m.group(1): int(m.group(2))
              for m in re.finditer(r'^((?:OP|S2C)_[A-Z0-9_]+)\s*=\s*(\d+)', src, re.M)}
    used = {}
    dynamic = []
    for m in re.finditer(r'(?<!def )make_s2c\(\s*([A-Za-z_][A-Za-z0-9_]*|\d+)', src):
        arg = m.group(1)
        line = src.count('\n', 0, m.start()) + 1
        if arg.isdigit():
            used.setdefault(int(arg), set()).add('字面量')
        elif arg in consts:
            used.setdefault(consts[arg], set()).add(arg)
        else:
            dynamic.append((line, arg))
    return consts, used, dynamic


WHITELIST = {250: 'C2GS_PING 回显（net.py:142，非 S2C 分派）'}


def handler_exists(module, func):
    """模块可能位于 src/ 根或子目录（login/ dialog/ data/ ...），全树查找。"""
    candidates = [os.path.join(SRC, module + '.py')]
    for root, _dirs, files in os.walk(SRC):
        if module + '.py' in files:
            candidates.append(os.path.join(root, module + '.py'))
    for path in candidates:
        if os.path.exists(path):
            txt = io.open(path, encoding='utf-8', errors='replace').read()
            if re.search(r'^def\s+%s\s*\(' % re.escape(func), txt, re.M):
                return True
            return False  # 文件在但函数没找到
    return None


def main():
    table = load_client_table()
    consts, used, dynamic = load_server_ops()
    src = io.open(M4, encoding='utf-8', errors='replace').read()

    print('客户端 NET_FUNCTION_NAME：%d 条；服务端 OP_* 常量：%d 个；实际下发：%d 个 opcode'
          % (len(table), len(consts), len(used)))
    print('=' * 76)

    bad = 0
    print('%-6s %-24s %-14s %-26s %s' % ('op', '服务端常量', '客户端模块', '处理函数', '判定'))
    print('-' * 76)
    for op in sorted(used):
        names = '/'.join(sorted(used[op]))
        if op in WHITELIST:
            print('%-6d %-24s %-14s %-26s %s' % (op, names, '-', '-', '白名单:' + WHITELIST[op]))
            continue
        if op not in table:
            print('%-6d %-24s %-14s %-26s %s' % (op, names, '-', '-', '★未登记'))
            bad += 1
            continue
        ent = table[op]
        module, func = ent[0], ent[1]
        ok = handler_exists(module, func)
        verdict = 'OK' if ok else ('★函数缺失' if ok is False else '★模块缺失')
        if not ok:
            bad += 1
        print('%-6d %-24s %-14s %-26s %s' % (op, names, module, func, verdict))

    print('-' * 76)
    # 客户端有登记、服务端不发的（信息项）
    never = sorted(set(table) - set(used))
    if dynamic:
        print('运行时决定 opcode 的调用点（需人工确认）：%s'
              % ', '.join('L%d:%s' % (ln, a) for ln, a in dynamic))
    print('客户端已登记但服务端未下发：%d 个（正常，客户端功能远多于模拟服）' % len(never))
    print('  前 20 个：%s' % ', '.join('%d:%s.%s' % (o, table[o][0], table[o][1]) for o in never[:20]))
    print('=' * 76)
    print('结论：%s（%d 处问题）' % ('全部对齐' if bad == 0 else '存在不一致', bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
