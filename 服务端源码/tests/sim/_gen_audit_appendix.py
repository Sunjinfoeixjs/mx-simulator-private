# -*- coding: utf-8 -*-
"""_gen_audit_appendix.py — 生成审计报告附录（ITEM_ALL 索引表 + opcode 通道表）。

输出：_audit_appendix.md（供报告拼接）
用法：python _gen_audit_appendix.py
"""
from __future__ import print_function

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '_audit_appendix.md')
PY = sys.executable


def run(script):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    p = subprocess.run([PY, os.path.join(HERE, script)], cwd=HERE, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode('utf-8', 'replace')


def item_all_lines():
    txt = run('_prop_index_audit.py')
    lines = []
    started = False
    for ln in txt.splitlines():
        if ln.startswith('ITEM_ALL 共'):
            started = True
            continue
        if not started:
            continue
        if ln.startswith('---') and lines:
            break
        # " 16  m_HP  CHeroPropMultiItemText  TWORD   <=="
        parts = ln.split()
        if not parts or not parts[0].isdigit():
            continue
        idx, name, ctor, typ = parts[0], parts[1], parts[2], parts[3]
        lines.append('| %s | `%s` | %s | %s |' % (idx, name, ctor, typ))
    return lines


def opcode_lines():
    txt = run('_protocol_audit.py')
    lines = []
    started = False
    for ln in txt.splitlines():
        if ln.startswith('op '):
            started = True
            continue
        if not started:
            continue
        if ln.startswith('---') and lines:
            break
        parts = ln.split()
        if not parts or not parts[0].isdigit():
            continue
        op, const, mod, func = parts[0], parts[1], parts[2], parts[3]
        if '白名单' in ln:
            verdict = '白名单（非 S2C 分派）'
        else:
            verdict = 'OK' if ln.rstrip().endswith('OK') else '★'
        lines.append('| %s | %s | %s | %s | %s |' % (op, const, mod, func, verdict))
    return lines


def main():
    it = item_all_lines()
    oc = opcode_lines()
    buf = []
    buf.append('### 附录 A · `hero.ITEM_ALL` 索引真值表（75 项，客户端 `src/hero.py:217`）\n')
    buf.append('S2CPropChange（op17）的 `type` 字段就是本表下标，服务端下发的每个属性变更都按此定位。\n')
    buf.append('| 下标 | 字段 | 解码类 | 类型/宽度 |')
    buf.append('|---:|---|---|---|')
    buf.extend(it)
    buf.append('')
    buf.append('%s\n' % ('### 附录 B · 服务端实际下发的 opcode 通道（%d 条静态；另有 op15 / op192 两条动态通道经 `build_server_time()` 复用）' % len(oc)))
    buf.append('| op | 服务端常量 | 客户端模块 | 处理函数 | 判定 |')
    buf.append('|---:|---|---|---|---|')
    buf.extend(oc)
    buf.append('')
    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(buf))
    print('已生成 %s（ITEM_ALL %d 行 / opcode %d 行）' % (OUT, len(it), len(oc)))


if __name__ == '__main__':
    main()
