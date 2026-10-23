# -*- coding: utf-8 -*-
"""_check_anchors 的跨文件扫描能力。

背景（2026-10-20）：文档正在拆成 docs/*.md（README / 01-architecture / ...）。
检查器如果只认主文件，子文档里的符号引用就完全失去守护。

本测试锁定 discover_reports 的行为：
  1. 目录参数 → 递归展开所有 *.md
  2. 跳过 anchors/（脚本生成区，不手改也不该被当正文检查）
  3. 跳过非 .md 文件
  4. 文件 + 目录混合、重复参数 → 去重
  5. 不存在的路径 → 跳过并告警，不崩
"""
import io
import os
import sys
import tempfile
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = r'C:\Users\Administrator\MX\协议资料\报告'

spec = importlib.util.spec_from_file_location(
    'ca', os.path.join(REPORT, '_check_anchors.py'))
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


def main():
    tmp = tempfile.mkdtemp(prefix='disc_')
    docs = os.path.join(tmp, 'docs')
    os.makedirs(os.path.join(docs, 'anchors'), exist_ok=True)
    os.makedirs(os.path.join(docs, 'sub'), exist_ok=True)
    for rel in ('README.md', '01-architecture.md', 'sub/nested.md'):
        p = os.path.join(docs, rel)
        io.open(p, 'w', encoding='utf-8').write('# %s\n' % rel)
    # 不该被扫的：anchors 下的 md、非 md 文件
    io.open(os.path.join(docs, 'anchors', 'kernel.md'), 'w',
            encoding='utf-8').write('# 生成区\n')
    io.open(os.path.join(docs, 'notes.txt'), 'w', encoding='utf-8').write('x\n')

    # ── 1. 目录递归展开 ──
    got = ca.discover_reports([docs])
    names = sorted(os.path.relpath(g, docs).replace(os.sep, '/') for g in got)
    check('目录递归展开（含子目录）',
          names == ['01-architecture.md', 'README.md', 'sub/nested.md'],
          'got=%r' % (names,))

    # ── 2. 跳过 anchors/ ──
    check('跳过 anchors/ 生成区',
          not any('anchors' in n for n in names), 'got=%r' % (names,))

    # ── 3. 跳过非 md ──
    check('跳过非 .md 文件',
          not any(n.endswith('.txt') for n in names), 'got=%r' % (names,))

    # ── 4. 混合 + 去重 ──
    single = os.path.join(tmp, 'single.md')
    io.open(single, 'w', encoding='utf-8').write('# single\n')
    got2 = ca.discover_reports([single, docs, single, docs])
    check('文件+目录混合去重', len(got2) == 4,
          'got=%d（期望 4）' % len(got2))
    check('结果有序（便于 diff）', got2 == sorted(got2))

    # ── 5. 不存在路径不崩 ──
    got3 = ca.discover_reports(['/no/such/path.md'])
    check('不存在的路径被跳过（不崩）', got3 == [], 'got=%r' % (got3,))

    # ── 6. default_reports 含 docs（若存在）──
    dr = ca.default_reports()
    check('default_reports 含主文档',
          any('模拟服全链路代码解析.md' in p for p in dr), 'dr=%r' % (dr,))
    real_docs = os.path.join(REPORT, 'docs')
    if os.path.isdir(real_docs):
        check('default_reports 含 docs/（目录存在时）',
              any('docs' in p for p in dr), 'dr=%r' % (dr,))

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('跨文件扫描：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
