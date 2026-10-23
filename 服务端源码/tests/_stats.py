# -*- coding: utf-8 -*-
"""STATS 块：把「项目健康数字」打成固定格式，人看、工具 diff、写报告时抄。

背景：报告里反复出现这类数字——「契约表 54 条 / 回归 15 步 / build_* 55 个 /
覆盖率 134/134」。过去每次都要分别跑 `_run_all.py`、`_c2s_coverage.py`、
`grep '^def build_'`、`len(DISPATCH_PRE_WAR_OPS)` 四个地方手工抄，
抄漏一个报告就过期（§18.1 那条「44 条 / 13 步 / 53 个」就是这么来的）。

现在有两级用法：
  · `_run_all.py` 每跑完回归，**自动在末尾打印同一块 STATS**（步数/通过/耗时为实测，
    其余为当次代码事实）——报告要引数字，直接从回归输出里复制；
  · 本脚本单独跑：静态代码事实 + 从回归日志（`--log`）补 steps 三项。

固定格式（`key : value`，键排序，两行标记包住）：

    === STATS ===
    build_star_count     : 55
    coverage_denominator : 134
    coverage_numerator   : 134
    dispatch_pre_war_ops : 54
    elapsed_sec          : 118.4
    mode                 : full
    steps_failed         : 0
    steps_passed         : 33
    steps_total          : 33
    === END STATS ===

约定：
  · `mode` 记录回归的筛选方式（full/fast/slow/关键字）。**筛选跑的 steps_total
    不可与全量比**——diff 之前先看这一行，否则会把「只跑了 9 步」误读成退步。
  · `elapsed_sec` 随机器负载抖动，属预期；代码事实五项（build_*/coverage/ops）
    才是可 diff 的。
  · 取值可能是 `-`（本模式不产生该值）或 `ERROR`（采集失败，如覆盖率扫描器
    守卫触发）。两者都是合法值，格式不破。
  · 覆盖率口径 = `_c2s_coverage.py` 的静态口径（分母=客户端真的会发的去重
    opcode，分子=_dispatch 链里真有分支的）。它自己挂回归，扫描段漂移会红。

用法：
    python tests/_stats.py                  # 只打静态事实（steps 三项为 -）
    python tests/_stats.py --log 回归输出.log  # 从回归日志补 steps 三项
    python tests/_stats.py --self-test      # 格式契约自检（parse(format(x))==x）
"""
from __future__ import print_function

import argparse
import contextlib
import io
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
# sim/ 优先取 tests/ 里面（本工作区布局），否则取兄弟目录（原布局）。
# 写死兄弟目录会让 build_star_count 采集恒为 ERROR（读不到 m4_server.py）。
SIM = os.path.join(TESTS, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(TESTS), 'sim')
REPORT = r'C:\Users\Administrator\MX\协议资料\报告'

BEGIN = '=== STATS ==='
END = '=== END STATS ==='

# STATS 键的固定顺序（排序即稳定，diff 才不会乱）。
KEYS = [
    'build_star_count',
    'coverage_denominator',
    'coverage_numerator',
    'dispatch_pre_war_ops',
    'elapsed_sec',
    'mode',
    'steps_failed',
    'steps_passed',
    'steps_total',
]

ERROR = 'ERROR'      # 采集失败（如覆盖率扫描段守卫触发）
UNKNOWN = '-'        # 本模式不产生该值（如不跑回归就没有 steps 实测）


def _import_quiet(name, path):
    """import 一个模块但吞掉它 import 期的 stdout（m4_server 会打配置横幅）。"""
    if path not in sys.path:
        sys.path.insert(0, path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        __import__(name)
    return sys.modules[name]


def code_facts():
    """静态代码事实（不起内核、不跑回归，约 1~2s）。

    返回 dict，键为 KEYS 的子集；单项采集失败记 ERROR（不抛）——
    STATS 是报告工具，不该因为它自己崩掉把回归输出带走。
    """
    facts = {}
    m = _import_quiet('m4_server', SIM)

    # build_* 组包函数数（与报告口径一致：源码行 `def build_`，不含 import 来的）
    try:
        with io.open(os.path.join(SIM, 'm4_server.py'), encoding='utf-8') as f:
            src = f.read()
        facts['build_star_count'] = len(re.findall(r'(?m)^def build_', src))
    except OSError:
        facts['build_star_count'] = ERROR

    # 分派顺序契约表条数（模块常量，单一事实源）
    try:
        facts['dispatch_pre_war_ops'] = len(m.DISPATCH_PRE_WAR_OPS)
    except AttributeError:
        facts['dispatch_pre_war_ops'] = ERROR

    # 上行覆盖率（复用 _c2s_coverage 的静态口径，不解析它的打印输出）
    try:
        cc = _import_quiet('_c2s_coverage', REPORT)
        cf = cc.compute()
        facts['coverage_denominator'] = len(cf['alive'])
        facts['coverage_numerator'] = len(cf['impl'])
    except Exception:                                    # noqa: BLE001
        # 扫描段守卫触发（RuntimeError）或任何采集异常：记 ERROR。
        # 真正的红由回归里的 _c2s_coverage 步骤负责，这里只保证格式不破。
        facts['coverage_denominator'] = ERROR
        facts['coverage_numerator'] = ERROR
    return facts


def format_stats(facts=None, steps_total=UNKNOWN, steps_passed=UNKNOWN,
                 steps_failed=UNKNOWN, elapsed_sec=UNKNOWN, mode=UNKNOWN):
    """把事实打成固定格式 STATS 块（键排序、两行标记）。"""
    row = dict(code_facts() if facts is None else facts)
    row['steps_total'] = steps_total
    row['steps_passed'] = steps_passed
    row['steps_failed'] = steps_failed
    row['elapsed_sec'] = elapsed_sec
    row['mode'] = mode
    out = [BEGIN]
    for k in KEYS:
        v = row.get(k, UNKNOWN)
        if isinstance(v, float):
            v = '%.1f' % v
        out.append('%-21s: %s' % (k, v))
    out.append(END)
    return '\n'.join(out)


def parse_stats(text):
    """解析 STATS 块 → dict（供工具消费；值尽量转 int/float）。

    找不到标记返回 None。`--self-test` 用它验证 format/parse 互为逆。
    """
    if BEGIN not in text or END not in text:
        return None
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    out = {}
    for ln in body.strip().split('\n'):
        if ':' not in ln:
            continue
        k, v = ln.split(':', 1)
        k, v = k.strip(), v.strip()
        if v == UNKNOWN:
            out[k] = None
        elif re.match(r'^\d+$', v):
            out[k] = int(v)
        elif re.match(r'^\d+\.\d+$', v):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def read_text_any(path):
    """读文本文件，容忍三种常见编码（供 --log 用）。

    ★ Windows PowerShell 的 `>` 重定向写的是 **UTF-16LE（带 BOM）**——
      按 UTF-8 读会得到一堆 NUL，正则全不匹配，steps 三项静默变 None
      （2026-10-20 实测踩到，mode 还会回落成 'full' 造成误导）。
      cmd 的 `>` 与 python 的 print 重定向则是 UTF-8。两种都要认。
    """
    with io.open(path, 'rb') as f:
        raw = f.read()
    for bom, enc in ((b'\xff\xfe', 'utf-16-le'),      # BOM 顺序：FF FE 在 FE FF 前
                     (b'\xfe\xff', 'utf-16-be'),
                     (b'\xef\xbb\xbf', 'utf-8-sig')):
        if raw.startswith(bom):
            return raw[len(bom):].decode(enc, 'replace')
    return raw.decode('utf-8', 'replace')


_STEP_RE = re.compile(r'^\s+(PASS|FAIL|PASS\*)\s+([\d.]+)s\s+(.+?)\s*$')
_SUM_RE = re.compile(r'合计\s+(\d+)\s*/\s*(\d+)\s*通过')


def stats_from_log(log_text):
    """从 _run_all.py 的输出里提取 steps 三项（缺啥返回 None，不猜）。

    认两种行：进度行 `  PASS   12.3s  步骤名`（耗时要累加）与汇总行
    ` 合计 34/34 通过`（权威总数）。汇总行后写，覆盖进度行的累加判定。
    """
    total = passed = None
    elapsed = 0.0
    for ln in log_text.replace('\r\n', '\n').split('\n'):
        mt = _STEP_RE.match(ln)
        if mt:
            elapsed += float(mt.group(2))
            if mt.group(1).startswith('PASS'):
                passed = (passed or 0) + 1
            continue
        ms = _SUM_RE.search(ln)
        if ms:
            passed, total = int(ms.group(1)), int(ms.group(2))
    if total is None:
        return {'steps_total': None, 'steps_passed': None,
                'steps_failed': None, 'elapsed_sec': None}
    return {'steps_total': total, 'steps_passed': passed,
            'steps_failed': total - (passed or 0),
            'elapsed_sec': round(elapsed, 1)}


def _self_test():
    """格式契约：parse(format(x)) == x（值类型保持）。"""
    sample = format_stats(
        facts={'build_star_count': 55, 'coverage_denominator': 134,
               'coverage_numerator': 134, 'dispatch_pre_war_ops': 54},
        steps_total=33, steps_passed=33, steps_failed=0,
        elapsed_sec=118.4, mode='full')
    got = parse_stats(sample)
    want = {'build_star_count': 55, 'coverage_denominator': 134,
            'coverage_numerator': 134, 'dispatch_pre_war_ops': 54,
            'elapsed_sec': 118.4, 'mode': 'full', 'steps_failed': 0,
            'steps_passed': 33, 'steps_total': 33}
    ok = got == want and list(got.keys()) == sorted(want.keys())
    print('[%s] parse(format(...)) 往返一致（9 键、类型保持、排序稳定）'
          % ('PASS' if ok else 'FAIL'))
    if not ok:
        print('  got : %r' % (got,))
        print('  want: %r' % (want,))
        return 1
    # 缺值/错误值也要能进格式
    fmt2 = format_stats(facts={'build_star_count': ERROR}, mode=UNKNOWN)
    p2 = parse_stats(fmt2)
    ok2 = (p2 is not None and p2['build_star_count'] == ERROR
           and p2['mode'] is None and len(p2) == 9)
    print('[%s] ERROR/- 占位值不破坏格式（仍为 9 键）'
          % ('PASS' if ok2 else 'FAIL'))
    if not ok2:
        return 1

    # 日志解析：合成一份最小回归输出（进度行 + 汇总行），三种编码都要能读
    sample_log = (' 一键回归：2 个步骤\n'
                  '  PASS      1.5s  假步骤A\n'
                  '  FAIL     0.5s  假步骤B\n'
                  ' 合计 1/2 通过\n')
    want_log = {'steps_total': 2, 'steps_passed': 1,
                'steps_failed': 1, 'elapsed_sec': 2.0}
    ok3 = True
    for enc in ('utf-8', 'utf-16-le', 'utf-16-be'):
        blob = sample_log.encode(enc)
        if enc == 'utf-16-le':
            blob = b'\xff\xfe' + blob          # PowerShell `>` 的形态
        elif enc == 'utf-16-be':
            blob = b'\xfe\xff' + blob
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as tf:
            tf.write(blob)
            tmp = tf.name
        try:
            got = stats_from_log(read_text_any(tmp))
        finally:
            os.unlink(tmp)
        if got != want_log:
            ok3 = False
            print('    %s → %r' % (enc, got))
    print('[%s] 回归日志解析（UTF-8 / UTF-16LE+BOM / UTF-16BE+BOM）'
          % ('PASS' if ok3 else 'FAIL'))
    return 0 if ok3 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description='STATS 块：项目健康数字，固定格式')
    ap.add_argument('--log', default=None,
                    help='_run_all.py 的输出文件路径，用于补 steps 三项')
    ap.add_argument('--self-test', action='store_true',
                    help='格式契约自检（parse(format(x))==x）')
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    steps = {'steps_total': UNKNOWN, 'steps_passed': UNKNOWN,
             'steps_failed': UNKNOWN, 'elapsed_sec': UNKNOWN}
    mode = UNKNOWN
    if args.log:
        log_text = read_text_any(args.log)
        steps = stats_from_log(log_text)
        for key in ('fast', 'slow'):
            if ('--%s' % key) in log_text:
                mode = key
        if mode == UNKNOWN and '没有匹配的步骤' not in log_text:
            mode = 'full'
    print(format_stats(mode=mode, **steps))
    return 0


if __name__ == '__main__':
    sys.exit(main())
