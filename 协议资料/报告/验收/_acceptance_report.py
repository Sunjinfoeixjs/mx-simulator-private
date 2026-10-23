# -*- coding: utf-8 -*-
"""生成「自动化验收」记录（机器 PASS 那一栏）。

为什么要生成而不是手写
----------------------
本仓规定「文档不手写回归数字」（`docs/04-testing.md` §12.1.4）：手抄的步骤数/耗时/通过率
一旦与实跑不符，读者没法分辨是回归退化还是文档过期。所以这一栏**只由脚本产出**：

    python 协议资料/报告/验收/_acceptance_report.py          # 跑一遍再写
    python 协议资料/报告/验收/_acceptance_report.py --log x.txt   # 复用已有输出（不重跑）

产出：`验收/自动化验收.md`（覆盖写）。人工 GUI 那一栏见 `验收/人工GUI验收_真实客户端.md`，
两份合起来才算验收结论——自动化只证明「服务端字节与帧序」，证明不了「真实客户端画面对不对」。

⚠ 本文件**不要手改**：下次生成会覆盖。要加说明，改模板（本脚本）而不是产物。
"""
import argparse
import datetime
import io
import os
import platform
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))   # 验收 → 报告 → 协议资料 → MX
# ★ 目录布局（2026-10-22 核实）：模拟服内核在 `服务端源码/tests/sim`，回归入口在
#   `服务端源码/tests/sim/tests/_run_all.py`。旧路径 `服务端源码/sim` 已不存在——
#   指错了不会报错，只会「跑出 0 步 / 全 FAIL」，比崩掉更难发现。
SIM = os.path.join(MX, '服务端源码', 'tests', 'sim')
RUNNER = os.path.join(SIM, 'tests', '_run_all.py')
OUT = os.path.join(HERE, '自动化验收.md')

RE_SUMMARY = re.compile(r'^\s{2}(PASS|FAIL)\s+([\d.]+)s\s+(.+?)(?:　\(rc=(\d+)\))?\s*$')
RE_STATS = re.compile(r'=== STATS ===')


def run_suite():
    """跑全量回归并返回 (stdout, returncode)。"""
    env = dict(os.environ)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    p = subprocess.run([sys.executable, RUNNER], cwd=MX, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout.decode('utf-8', 'replace'), p.returncode


def parse_steps(text):
    """从 runner 输出里取逐步骤结果 → [(序号, 名称, PASS/FAIL, 耗时s, rc)]。"""
    rows = []
    for line in text.splitlines():
        m = RE_SUMMARY.match(line)
        if m:
            rows.append((len(rows) + 1, m.group(3).strip(), m.group(1),
                         float(m.group(2)), m.group(4)))
    return rows


def parse_stats(text):
    """取 `=== STATS ===` 块原文（原样贴进记录，避免二次抄写）。"""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if RE_STATS.search(ln):
            block = []
            for ln2 in lines[i:]:
                block.append(ln2.rstrip())
                if '=== END STATS ===' in ln2:
                    break
            return '\n'.join(block)
    return ''


def build(text, rc, mode, source):
    rows = parse_steps(text)
    npass = sum(1 for r in rows if r[2] == 'PASS')
    nfail = len(rows) - npass
    stats = parse_stats(text)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    failed = [r for r in rows if r[2] == 'FAIL']

    out = []
    out.append('# 自动化验收记录（机器 PASS 栏）')
    out.append('')
    out.append('> **本文件由 `_acceptance_report.py` 生成，请勿手改。**')
    out.append('> 人工 GUI 验收是另一份：`人工GUI验收_真实客户端.md`。'
               '两份都 PASS 才算验收通过——自动化证明不了真机画面。')
    out.append('')
    out.append('## 1. 本次运行')
    out.append('')
    out.append('| 项 | 值 |')
    out.append('|---|---|')
    out.append('| 生成时间 | %s |' % now)
    out.append('| 命令 | `python 服务端源码/tests/sim/tests/_run_all.py` |')
    out.append('| 模式 | `%s` |' % mode)
    out.append('| 来源 | %s |' % source)
    out.append('| 解释器 | Python %s（%s） |'
               % (platform.python_version(), platform.platform()))
    out.append('| 工作目录 | `%s` |' % MX)
    out.append('| 步骤 | **%d 个**：通过 %d / 失败 %d |' % (len(rows), npass, nfail))
    out.append('| 退出码 | %d |' % rc)
    out.append('| 结论 | %s |' % ('**PASS**' if nfail == 0 else '**FAIL**'))
    out.append('')
    if failed:
        out.append('### ✗ 失败项')
        out.append('')
        for (i, name, _st, _dt, _rc) in failed:
            out.append('- `%s`（第 %d 步）' % (name, i))
        out.append('')
    out.append('## 2. 逐步骤结果')
    out.append('')
    out.append('| # | 步骤 | 结果 | 耗时 |')
    out.append('|---|---|---|---|')
    for (i, name, st, dt, _rc) in rows:
        out.append('| %d | %s | %s | %.1fs |'
                   % (i, name, '✅ PASS' if st == 'PASS' else '❌ FAIL', dt))
    out.append('')
    if stats:
        out.append('## 3. STATS 块（原样）')
        out.append('')
        out.append('```')
        out.append(stats)
        out.append('```')
        out.append('')
    out.append('## 4. 这一栏证明了什么 / 没证明什么')
    out.append('')
    out.append('| 已证明 | 未证明（必须人工 GUI） |')
    out.append('|---|---|')
    out.append('| 服务端发/收的字节与 `frames.SPEC` 一致（含位域 MSB-first、`hp_chg` 符号、`life=DEAD`） | '
               '真实 `mx.exe` 能不能把这些帧画出来、按钮能不能点 |')
    out.append('| 帧序契约：`117→123→119`、`122→123`、`WarDel→WarEnd→96/99/97` | '
               '客户端有没有卡「请等待」、场景是否真的重建 |')
    out.append('| 召唤兽指令 168–174 的解析与演出分支 | 宠物指令菜单是否真的弹出（`S2CWarSummon` 生效） |')
    out.append('| 协议级 socket 客户端的连续流程（`_e2e_m4/m5/m7`） | 同一个客户端会话里**连续**打多场、切图、对话不崩 |')
    out.append('')
    out.append('> 自动化里的「客户端」都是**协议级脚本**（`sim/_e2e_*.py` 自己拼帧），'
               '不是 `mx.exe`。所以真机验收**不能**用本文件替代。')
    out.append('')
    return '\n'.join(out) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成自动化验收记录')
    ap.add_argument('--log', help='复用已有的 runner 输出文件（默认现跑一遍全量回归）')
    ap.add_argument('--out', default=OUT, help='输出路径（默认 验收/自动化验收.md）')
    args = ap.parse_args(argv)

    if args.log:
        text = io.open(args.log, encoding='utf-8', errors='replace').read()
        rc, mode, source = 0, 'reused', '`%s`' % os.path.basename(args.log)
    else:
        print('跑全量回归（约 3 分钟）…')
        text, rc = run_suite()
        mode, source = 'full', '本次实跑'
        # 顺手把原始输出留档，便于事后核对（覆盖写）
        raw = os.path.join(HERE, '_last_run_all.txt')
        with io.open(raw, 'w', encoding='utf-8') as f:
            f.write(text)
        print('  runner rc=%d，原始输出：%s' % (rc, os.path.basename(raw)))

    doc = build(text, rc, mode, source)
    with io.open(args.out, 'w', encoding='utf-8') as f:
        f.write(doc)
    rows = parse_steps(text)
    nfail = sum(1 for r in rows if r[2] == 'FAIL')
    print('已写出 %s（%d 步，失败 %d）' % (args.out, len(rows), nfail))
    return 0 if (rows and nfail == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
