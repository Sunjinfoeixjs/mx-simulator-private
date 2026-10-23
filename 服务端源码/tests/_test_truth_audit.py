# -*- coding: utf-8 -*-
"""真值审计冒烟：把四项审计（属性索引 / opcode / 数据表名称 / provenance 准确性）接进测试。

这些错误都是**静默**的：下标错位只写到隔壁字段、opcode 未登记只被丢弃、
名称漂移只显示错字、provenance 写过期结论都不会崩溃——只会让数值或结论悄悄不对。

用法：python _test_truth_audit.py
"""
from __future__ import print_function

import os
import subprocess
import sys

SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
AUDITS = (
    ('_prop_index_audit.py', '属性帧索引（PROP_IDX_* ↔ hero.ITEM_ALL）'),
    ('_protocol_audit.py', 'opcode 通道（NET_FUNCTION_NAME ↔ 下发帧）'),
    ('_data_truth_check.py', '数据表 (id, 名称) 对 itemdata 真值'),
    ('_provenance_audit.py', 'provenance 真值声明与证据边界'),
)

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


def run(script):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    p = subprocess.run([sys.executable, os.path.join(SIM, script)], cwd=SIM, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode('utf-8', 'replace').strip().splitlines()
    return p.returncode, (out[-1] if out else '')


for script, desc in AUDITS:
    rc, last = run(script)
    check(rc == 0, '%-42s | %s' % (desc, last))

print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
sys.exit(0 if ok else 1)
