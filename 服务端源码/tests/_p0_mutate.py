# -*- coding: utf-8 -*-
"""P0 变异验证公共跑法：施加变异 → 跑测试 → 还原 → 校验哈希一致。

与 P3-05 那版的区别：加了**超时**保护。分帧类测试一旦被改出死循环，
表现是挂住而不是判红 —— 没有 timeout 的变异脚本会跟着一起挂死。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TIMEOUT = 40


def sha(p):
    return hashlib.sha256(io.open(p, 'rb').read()).hexdigest()[:16]


def run_suite(rel):
    p = subprocess.run([PY, os.path.join(HERE, rel)], cwd=HERE,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=TIMEOUT)
    return p.returncode, p.stdout.decode('utf-8', 'replace')


def mutate(test_rel, mutations):
    """mutations: [(名字, 文件相对 HERE, 旧串, 新串)]"""
    rows = []
    for name, rel, old, new in mutations:
        path = os.path.join(HERE, rel)
        raw = io.open(path, encoding='utf-8').read()
        if old not in raw:
            rows.append((name, '构造错', '待替换片段未找到'))
            print('!! %-42s 构造错（片段未找到）' % name)
            continue
        bak = path + '.p0bak'
        shutil.copy2(path, bak)
        h0 = sha(path)
        try:
            io.open(path, 'w', encoding='utf-8', newline='').write(
                raw.replace(old, new, 1))
            try:
                rc, out = run_suite(test_rel)
                fails = [ln[5:].strip() for ln in out.splitlines()
                         if ln.startswith('FAIL')]
                verdict = '判红' if rc != 0 else '打不红'
                rows.append((name, verdict, '; '.join(fails[:2]) or '(无FAIL行)'))
            except subprocess.TimeoutExpired:
                rows.append((name, '判红(超时)', '死循环/挂住，%ds 未返回' % TIMEOUT))
                verdict = '判红(超时)'
            print('%s  %-42s %s' % (verdict, name,
                                    (rows[-1][2])[:90]))
        finally:
            shutil.copy2(bak, path)
            os.remove(bak)
            assert sha(path) == h0, '还原失败：%s' % path
    print('')
    print('还原后复跑：' + ('绿' if run_suite(test_rel)[0] == 0 else '仍然红！'))
    return rows
