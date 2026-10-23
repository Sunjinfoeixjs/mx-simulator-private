# -*- coding: utf-8 -*-
"""P7 生效后写出 dialog/dlgsysopt.py，并做 lib2to3 + 关键区域校验。"""
import io
import os
import sys

CLAW = r"C:\Users\Administrator\MX\Claw"
sys.path.insert(0, os.path.join(CLAW, 'tools'))
sys.path.insert(0, os.path.join(CLAW, 'tools', 'opmap'))
import u6_patch26  # noqa
from uncompyle6.main import decompile_file  # noqa

SRC = os.path.join(CLAW, 'src')
PYC = os.path.join(SRC, 'dialog', 'dlgsysopt.pyc')
PY = os.path.join(SRC, 'dialog', 'dlgsysopt.py')


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
finally:
    sys.stdout = old

text = buf.getvalue()
print('deparse OK, %d bytes, %d lines' % (len(text.encode('utf-8')),
                                          len(text.splitlines())))

# 与 B6/_finalize 落盘方式一致（text 模式 → Windows CRLF）
with open(PY, 'w', encoding='utf-8', errors='replace') as f:
    f.write(text)
print('written -> %s' % PY)

# lib2to3 解析校验
from lib2to3 import pygram, pytree            # noqa: E402
from lib2to3.pgen2 import driver as pgen_driver  # noqa: E402

parser = pgen_driver.Driver(pygram.python_grammar, convert=pytree.convert)
with io.open(PY, encoding='utf-8') as fh:
    content = fh.read()
try:
    parser.parse_string(content)
    print('lib2to3 解析: PASS')
except Exception as ex:
    print('lib2to3 解析: FAIL %s' % ex)

# 关键区域：OnMessage 的 continue/else 结构（原卡点 1502_0 附近）
lines = content.splitlines()
for i, ln in enumerate(lines):
    if 'continue' in ln:
        lo, hi = max(0, i - 6), min(len(lines), i + 8)
        print('---- continue 上下文 (行 %d) ----' % (i + 1))
        for j in range(lo, hi):
            print('%6d | %s' % (j + 1, lines[j]))
        break
