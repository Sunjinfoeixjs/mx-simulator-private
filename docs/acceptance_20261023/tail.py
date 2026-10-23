# -*- coding: utf-8 -*-
"""按 UTF-8 读取日志尾部并打印（避免 GBK 控制台炸掉）。"""
import io
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
path = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
pat = sys.argv[3] if len(sys.argv) > 3 else None
if not os.path.exists(path):
    print('missing', path)
    raise SystemExit(1)
lines = io.open(path, encoding='utf-8', errors='replace').read().splitlines()
if pat:
    lines = [ln for ln in lines if pat in ln]
print('total_lines', len(lines))
for ln in lines[-n:]:
    print(ln)
