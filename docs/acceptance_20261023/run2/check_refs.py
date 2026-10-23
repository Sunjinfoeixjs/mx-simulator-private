# -*- coding: utf-8 -*-
"""检查文档里引用的证据文件是否存在（避免文档指向不存在的截图）。"""
import io
import os
import re
import sys

DOCS = r'C:\Users\Administrator\MX\docs'
ACC = os.path.join(DOCS, 'acceptance_20261023')
ROOT = os.path.abspath(os.path.join(DOCS, '..'))
bad = []
n = 0
for fn in os.listdir(DOCS):
    if not fn.endswith('.md'):
        continue
    s = io.open(os.path.join(DOCS, fn), encoding='utf-8').read()
    for m in re.finditer(r'[\w./\-\u4e00-\u9fa5*]+\.(?:png|json|log|csv)', s):
        p = m.group(0)
        n += 1
        cands = [os.path.join(DOCS, p), os.path.join(ACC, p), os.path.join(ROOT, p),
                 os.path.join(ACC, 'run2', p), os.path.join(ACC, 'run2', 'shots', p),
                 os.path.join(ACC, 'shots', p), os.path.join(ACC, 'save_iso', p),
                 os.path.join(ACC, 'run2', 'save', p)]
        if '*' in p:
            continue
        if not any(os.path.exists(c) for c in cands):
            bad.append((fn, p))
print('refs', n, 'missing', len(bad))
for x in sorted(set(bad)):
    print(' ', x[0], '->', x[1])
sys.exit(0)
