# -*- coding: utf-8 -*-
"""判定 warrior.py 差异来源：禁用 P7 规则后重新 deparse，与磁盘/带 P7 版对比。"""
import io
import os
import sys

CLAW = r"C:\Users\Administrator\MX\Claw"
sys.path.insert(0, os.path.join(CLAW, 'tools'))
sys.path.insert(0, os.path.join(CLAW, 'tools', 'opmap'))
import u6_patch26  # noqa
from uncompyle6.parsers.parse26 import Python26Parser  # noqa
from uncompyle6.main import decompile_file  # noqa

SRC = os.path.join(CLAW, 'src')
PYC = os.path.join(SRC, 'core', 'warrior.pyc')
PY = os.path.join(SRC, 'core', 'warrior.py')


class Null:
    def write(self, *a):
        pass

    def flush(self):
        pass


def deparse():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = Null()
    try:
        decompile_file(PYC, buf)
        return buf.getvalue()
    finally:
        sys.stdout = old


with io.open(PY, encoding='utf-8', errors='replace') as f:
    disk = f.read().replace('\r\n', '\n')

with_p7 = deparse().replace('\r\n', '\n')
print('带 P7  == 磁盘 ? %s' % (with_p7 == disk))

# 禁用 P7：把 _CF_JA_RULES 置空（monkey-patch customize 包装）
_orig = Python26Parser.customize_grammar_rules
import u6_patch26 as P  # noqa


def _no_p7(self, tokens, customize):
    saved = P._CF_JA_RULES
    P._CF_JA_RULES = ''
    try:
        _orig(self, tokens, customize)
    finally:
        P._CF_JA_RULES = saved


Python26Parser.customize_grammar_rules = _no_p7
no_p7 = deparse().replace('\r\n', '\n')
print('无 P7  == 磁盘 ? %s' % (no_p7 == disk))

# 差异定位
fl = no_p7.splitlines()
dl = disk.splitlines()
first = None
for i in range(min(len(fl), len(dl))):
    if fl[i] != dl[i]:
        first = (i + 1, fl[i][:60], dl[i][:60])
        break
print('无 P7 vs 磁盘 首个差异: %s' % (first,))
print('无 P7 行数=%d 磁盘行数=%d' % (len(fl), len(dl)))
