# -*- coding: utf-8 -*-
"""文档体检（三向）：A 符号存在 / B 生成表同步 / C 数字与 STATS 一致。

背景：2026-10-19 连着三轮出现「指名一处已刷、别处同款旧号还在」——报告里的
`m4_server.py:NNN` / 裸 `:NNN` 引用随源码增删整体移位，只改被引用的那一处不够。
2026-10-20 起正文不再写行号，行号模式（--lines）保留给历史报告；同日新增
**方向 C**：文档里手写的 STATS 可覆盖数字（N 步 / N 个 build_* / N 条契约 /
覆盖率 N/M），逐个与源码实况比对，不一致判红——手写值从此「允许但被守护」，
漂移不再可能静默（覆盖率那次 100%→14% 就是这么来的）。

三个方向：
  A  文档→代码：反引号符号是否真实存在于源码
  B  表→代码 ：§17 生成表与源码逐行一致（`_gen_anchor_table.py --write` 修复）
  C  数字→STATS：手写指标值与 `m4_server` / `_c2s_coverage` / `_run_all.STEPS` 实况一致
     · 可校验：步骤数、契约表条数、build_* 个数、覆盖率分子/分母、通过比的总数
     · 不可校验（随每次运行变化）→ 禁手写：elapsed（约 Ns/秒/分钟）
     · 跳过围栏代码块与 §18 复核快照（历史记录，数字为当日值）

用法：
    python _check_anchors.py                       # 三向全跑（主文档 + docs/）
    python _check_anchors.py 其它报告.md           # 指定报告
    python _check_anchors.py --verbose             # 连通过的也逐条打印
    python _check_anchors.py --quiet               # 只报失败（挂 CI 用）
    --symbols / --table / --stats                  # 单跑一个方向
    --lines                                        # 行号模式（历史报告）
退出码：A 有未知符号、B 表不一致、C 数字不一致 → 1，否则 0。
"""
from __future__ import print_function

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
if not os.path.isdir(SIM):
    # 本工作区布局：sim 在 服务端源码/tests/sim（见 _c2s_coverage / tap 的同款兜底）
    SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
CLIENT = r'C:\Users\Administrator\MX\客户端源码\src'

# 候选源文件（按 basename 索引）：报告里出现 `xxx.py:NNN` 时按 basename 找
SRC_FILES = ['m4_server.py', 'tap.py', 'debugger.py', 'singleplayer.py',
             # 2026-10-19：world_config 开始被正文直接引用行号（probe_config/apply_to），
             # 不纳入候选会让这类引用一律报「未知源文件」。同理补 transport/editor_server。
             'world_config.py', 'transport.py', 'editor_server.py',
             'net.py', 'nethero.py', 'netmap.py', 'netitem.py', 'netpet.py',
             'netnpc.py', 'netplayer.py', 'nettask.py', 'netwar.py',
             'netteam.py', 'netfriend.py', 'netchat.py']
SRC_DIRS = [SIM, CLIENT, os.path.join(CLIENT, 'dialog'), os.path.join(CLIENT, 'core'),
            r'C:\Users\Administrator\MX\客户端源码\scripts\dialog',
            r'C:\Users\Administrator\MX\客户端源码\scripts\login',
            # 2026-10-20：符号体检还要覆盖测试与审计脚本——文档里常引用
            # `EXPECT_LOG` / `MANUAL_DUMMY` / `verify_exit_codes` 这类测试侧符号，
            # 不纳入会让它们全部误报成「源码中未找到」。
            os.path.join(SIM, 'tests'),                    # runner（sim/tests/_run_all）
            os.path.dirname(SIM),                          # 测试本体（本工作区：tests/*.py）
            os.path.join(os.path.dirname(SIM), 'tests'),   # 旧布局：tests 与 sim 平级
            r'C:\Users\Administrator\MX\客户端源码\scripts',
            HERE]

# 像「合理锚点」的行：定义 / 分支 / 常量 / 赋值 / 关键调用（conn.sendall、self._x…）
# ★ 大小写都收：常量全大写、变量小写（如 `installed = tap.install(m)`）都是合法指向
PLAUSIBLE = re.compile(
    r'^\s*(def |class |elif |if |while |for |return |try:|self\.|conn\.|tap\.'
    r'|[A-Za-z_][A-Za-z0-9_]*\s*=[^=])')


def load_sources():
    """basename → 行列表（同名文件只取第一个找到的）。"""
    src = {}
    for d in SRC_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn in SRC_FILES and fn not in src:
                try:
                    src[fn] = io.open(os.path.join(d, fn), encoding='utf-8').read().split('\n')
                except OSError:
                    pass
    return src


def classify(text):
    """把源码行判成 OK / WARN（空行或纯注释）/ None（不算合理目标）。"""
    if text is None:
        return None                       # 越界
    t = text.strip()
    if not t:
        return 'WARN'
    if t.startswith('#'):
        # 段标记（── xxx ──）是合法指向；普通行内注释多半指错了
        return 'OK' if ('──' in t or t.startswith('# ====')) else 'WARN'
    if t.startswith('"""') or t.startswith("'''"):
        return 'OK'                       # docstring 起始（区间引用常见）
    if PLAUSIBLE.match(t):
        return 'OK'
    if t.startswith(('while ', 'try:', 'except', 'else:')):
        return 'OK'
    return 'WARN'


def discover_reports(paths):
    """把参数展开成**实际存在的 md 文件列表**（2026-10-20）。

    为什么需要：文档正在拆成 `docs/*.md` 多文件（README / 01-architecture / ...），
    检查器不能只认单一入口文件。参数可以是：
      · 文件路径   → 原样收下
      · 目录路径   → 递归收下所有 *.md（跳过 anchors/ 等生成区）
      · 空         → 用默认：报告目录下的主文档 + docs/（若存在）

    返回去重后的绝对路径列表，按路径排序（输出稳定、便于 diff）。
    """
    out = []
    for p in (paths or []):
        ap = os.path.abspath(p)
        if os.path.isdir(ap):
            for root, dirs, files in os.walk(ap):
                # 跳过脚本生成的锚点区（不手改，也不该被当正文检查）
                dirs[:] = [d for d in dirs if d != 'anchors']
                for fn in files:
                    if fn.lower().endswith('.md'):
                        out.append(os.path.join(root, fn))
        elif os.path.isfile(ap):
            out.append(ap)
        else:
            print('  ⚠ 路径不存在，已跳过：%s' % ap)
    return sorted(set(out))


def default_reports():
    """默认扫描范围：主文档 + docs/ 目录（若存在）。"""
    out = [os.path.join(HERE, '模拟服全链路代码解析.md')]
    docs = os.path.join(HERE, 'docs')
    if os.path.isdir(docs):
        out.append(docs)
    return out


def check_symbols(report_paths, verbose=False):
    """符号存在性体检：文档里提到的符号，是否真的存在于源码中。

    背景（2026-10-20）：正文与速查表已改为**只写符号名、不写行号**
    （行号随每次改动漂移，维护成本极高）。行号锚点体检因此失去对象，
    但「符号名写错 / 改名后忘了同步」这类问题仍然存在，需要另一种检查。

    做法：取文档里**反引号包裹的标识符**，逐个在候选源码里搜。
    找不到的列出来，由人判断是笔误、改名，还是外部/历史符号。
    """
    blobs = []
    for d in SRC_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.py'):
                continue
            try:
                blobs.append(io.open(os.path.join(d, fn), encoding='utf-8',
                                     errors='replace').read())
            except (IOError, OSError):
                pass
    allsrc = chr(10).join(blobs)

    # 文档里刻意记录为「不存在」的符号：它们出现在「否定结论」里，
    # 源码中本来就搜不到，属于正确内容而非笔误。
    KNOWN_ABSENT = set('''
        expdata          客户端资源表名；文档结论是「全库无 expdata」
        serverNum        协议字段名（不是代码标识符）
        dispatch_pre_war_ops  STATS 键名（协议字段名，不是代码标识符；源码里是
                             大写常量 DISPATCH_PRE_WAR_OPS，见 _check_stats_refs.py）
        '''.split())

    SKIP = set('''
        True False None self cls args kwargs
        str int float bool list dict set tuple bytes
        len range open print format isinstance getattr setattr hasattr
        append extend update pop get keys values items join split strip
        encode decode replace startswith endswith rstrip lstrip
        '''.split())


    ident = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{2,}$')
    problems, checked = [], 0
    for rp in report_paths:
        try:
            doc = io.open(rp, encoding='utf-8', errors='replace').read()
        except (IOError, OSError) as e:
            print('读不到 %s: %s' % (rp, e))
            continue
        for m in re.finditer(r'`([A-Za-z_][A-Za-z0-9_]{2,})`', doc):
            sym = m.group(1)
            if sym in SKIP or sym in KNOWN_ABSENT or not ident.match(sym):
                continue
            checked += 1
            if sym not in allsrc:
                problems.append(sym)

    uniq = sorted(set(problems))
    if verbose:
        print('符号体检：检查 %d 处引用，%d 个名字未在源码中找到'
              % (checked, len(uniq)))
    for s in uniq:
        print('  ? %s  （源码中未找到同名标识符，确认是否笔误/改名/外部符号）' % s)
    return len(uniq)

def check_table(report_path=None, verbose=False):
    """方向 B：§17 生成的符号表必须与源码一致。

    §17 由 `_gen_anchor_table.py` 扫描源码生成。代码增删符号后若忘了重跑
    `--write`，表就会与代码不符——这比「文档漏写」更隐蔽：表看起来是完整的，
    只是过时了。

    做法：就地调用生成器的 `gen()` 拿到「当前应有的内容」，与文档里两个标记
    之间的实际内容逐字比对。不一致就列出差异行。

    返回不一致项数（0 = 一致）。
    """
    import importlib.util
    gen_path = os.path.join(HERE, '_gen_anchor_table.py')
    if not os.path.isfile(gen_path):
        print('找不到生成器 %s' % gen_path)
        return 1
    spec = importlib.util.spec_from_file_location('_gen_anchor_table', gen_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                      # noqa: BLE001
        print('生成器加载失败：%r' % e)
        return 1
    try:
        want = mod.gen()
    except Exception as e:                      # noqa: BLE001
        print('生成失败：%r' % e)
        return 1

    rp = report_path or os.path.join(HERE, '模拟服全链路代码解析.md')
    try:
        doc = io.open(rp, encoding='utf-8', errors='replace').read()
    except (IOError, OSError) as e:
        print('读不到 %s: %s' % (rp, e))
        return 1

    if mod.BEGIN not in doc or mod.END not in doc:
        # ★ 不是错误（2026-10-20）：拆分的子文档（docs/*.md）本来就不含 §17
        #   生成表——那张表只存在于主文档。返回 0 表示「本文件无需检查方向 B」，
        #   而不是「检查失败」。返回 1 会让 `_check_anchors.py docs/` 永远红。
        if verbose:
            print('  （本文件无 §17 生成表，方向 B 跳过：%s）'
                  % os.path.basename(rp))
        return 0

    i = doc.index(mod.BEGIN) + len(mod.BEGIN)
    j = doc.index(mod.END)
    cur = doc[i:j]
    fence = chr(96) * 3
    want_block = chr(10) + fence + chr(10) + want + chr(10) + fence + chr(10)

    cur_lines = [l for l in cur.strip().split(chr(10))]
    want_lines = [l for l in want_block.strip().split(chr(10))]
    if cur_lines == want_lines:
        if verbose:
            print('  §17 索引与源码一致（%d 行）' % len(want_lines))
        return 0

    # 列出差异：新增 / 缺失 / 顺序变化
    cs, ws = set(cur_lines), set(want_lines)
    only_doc = [l for l in cur_lines if l not in ws]
    only_src = [l for l in want_lines if l not in cs]
    print('  §17 索引与源码**不一致**：')
    for l in only_src[:20]:
        print('    + 源码有、表里缺：%s' % l.strip())
    for l in only_doc[:20]:
        print('    - 表里有、源码无：%s' % l.strip())
    if not only_doc and not only_src:
        print('    （行集合相同但顺序不同）')
    print('    修复：python 协议资料/报告/_gen_anchor_table.py --write')
    return max(1, len(only_doc) + len(only_src))


# ───────────────────────── 方向 C：数字与 STATS 比对（2026-10-20）─────────────────────────
# 手写指标值允许存在（可读性优先），但每次回归与源码实况比对，不一致判红。
# 基准全部**静态可采**：m4_server 常量/源码、_c2s_coverage 静态口径、_run_all.STEPS。
# elapsed_sec 随每次运行变化，静态不可校验 → 禁手写（写「见 STATS 块」）。

C_SIM = SIM                              # 与上方 SIM 同源（含「sim 在 tests 里」的兜底）
C_TESTS = os.path.join(C_SIM, 'tests')   # runner 本体在 sim/tests/（它跑的测试在 服务端源码/tests/）

C_RULES = [
    # (正则, 取数说明, 基准键序列) —— 第 i 个捕获组对应第 i 个基准键
    (re.compile(r'(\d+)\s*个步骤'),
     '回归步骤数', ('steps_total',)),
    (re.compile(r'(\d+)\s*/\s*(\d+)\s*通过'),
     '回归通过比（校验分母=步骤总数；分子是运行时值，不校验）',
     (None, 'steps_total')),
    (re.compile(r'回归[^。\n]{0,6}?(\d+)\s*步'),
     '回归步数', ('steps_total',)),
    (re.compile(r'(?:覆盖率|上行覆盖|上行指令)[^。\n]{0,26}?(\d+)\s*/\s*(\d+)'),
     '覆盖率分子/分母', ('coverage_numerator', 'coverage_denominator')),
    (re.compile(r'已实现\s*\*{0,2}\s*(\d+)'),
     '覆盖率分子', ('coverage_numerator',)),
    (re.compile(r'未实现\s*\*{0,2}\s*(\d+)'),
     '覆盖率未实现数（=分母-分子）', ('coverage_miss',)),
    (re.compile(r'build_\*[^。\n]{0,16}?(\d+)\s*个'),
     'build_* 个数', ('build_star_count',)),
    (re.compile(r'DISPATCH_PRE_WAR_OPS[^。\n]{0,24}?(\d+)\s*条'),
     '分派契约表条数', ('dispatch_pre_war_ops',)),
    (re.compile(r'契约表[，,、\s*]{0,8}(\d+)\s*条'),
     '分派契约表条数', ('dispatch_pre_war_ops',)),
    (re.compile(r'真的会发」\*{0,2}\s*(\d+)\s*条'),
     '覆盖率分母', ('coverage_denominator',)),
]

C_BANNED = [
    # 随每次运行变化的值，手写必过期 → 直接禁（文档写「见 STATS 块 elapsed_sec」）
    (re.compile(r'约\s*\d+(?:\.\d+)?s'), 'elapsed_sec'),
    (re.compile(r'约\s*\d+\s*秒'), 'elapsed_sec'),
    (re.compile(r'约\s*\d+\s*分钟'), 'elapsed_sec'),
]

# §18 是历史快照：从「## 18. 」到「## 19. 」整段跳过（含 12.1.4 引用规则所说的豁免）。
SNAP_START = re.compile(r'^## 18\. ')
SNAP_END = re.compile(r'^## 19\. ')

LIVE_NOTE = {
    'steps_total': 'len(_run_all.STEPS)',
    'coverage_numerator': '_c2s_coverage 静态分子',
    'coverage_denominator': '_c2s_coverage 静态分母',
    'coverage_miss': '分母 - 分子（派生键）',
    'build_star_count': 'm4_server.py ^def build_ 计数',
    'dispatch_pre_war_ops': 'len(DISPATCH_PRE_WAR_OPS)',
    'elapsed_sec': '随每次运行变化',
}


def live_stats():
    """方向 C 的基准值（静态采集，约 1.5s）。m4_server 导入期的配置横幅被吞掉。"""
    import contextlib

    def quiet_import(name, path):
        if path not in sys.path:
            sys.path.insert(0, path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            __import__(name)
        return sys.modules[name]

    m = quiet_import('m4_server', C_SIM)
    cc = quiet_import('_c2s_coverage', HERE)
    runner = quiet_import('_run_all', C_TESTS)
    cf = cc.compute()
    src = io.open(os.path.join(C_SIM, 'm4_server.py'), encoding='utf-8').read()
    return {
        'coverage_numerator': len(cf['impl']),
        'coverage_denominator': len(cf['alive']),
        'coverage_miss': len(cf['alive']) - len(cf['impl']),   # 派生键：分母-分子
        'build_star_count': len(re.findall(r'(?m)^def build_', src)),
        'dispatch_pre_war_ops': len(m.DISPATCH_PRE_WAR_OPS),
        'steps_total': len(runner.STEPS),
    }


def check_stats_refs(reports, verbose=False):
    """方向 C：手写指标值 vs 实况。返回 (不一致项列表, 已核对数)。

    不一致项 = (文件, 行号, 说明, 文档值, 实况值, 修法)。
    覆盖率扫描器异常时整体降级为 1 项失败（宁可红，不可静默放过）。
    """
    try:
        stats = live_stats()
    except Exception as e:                              # noqa: BLE001
        return [('?', 0, 'STATS 基准采集失败', repr(e)[:60],
                 '-', '先修覆盖率扫描器/内核导入，再谈数字校验')], 0

    bad, verified = [], 0
    for rp in reports:
        try:
            text = io.open(rp, encoding='utf-8').read()
        except (IOError, OSError) as e:
            print('读不到 %s: %s' % (rp, e))
            continue
        lines = text.split('\n')
        in_fence = in_snap = False
        for no, ln in enumerate(lines, 1):
            if ln.lstrip().startswith('```'):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if SNAP_START.match(ln):
                in_snap = True
            elif SNAP_END.match(ln):
                in_snap = False
            if in_snap:
                continue
            for pat, what, keys in C_RULES:
                for m in pat.finditer(ln):
                    for gi, key in enumerate(keys, 1):
                        if key is None:            # 运行时值（通过比的分子），跳过
                            continue
                        claimed = int(m.group(gi))
                        actual = stats[key]
                        verified += 1
                        if claimed != actual:
                            rel = os.path.relpath(rp, HERE)
                            bad.append((rel, no, what, claimed, actual,
                                        '改成 %d，或改为键名引用（STATS 块 %s）'
                                        % (actual, key)))
            for pat, key in C_BANNED:
                m = pat.search(ln)
                if m:
                    rel = os.path.relpath(rp, HERE)
                    bad.append((rel, no, '手写 %s（随每次运行变化，不可校验）' % key,
                                m.group(0)[:24], '每次都变',
                                '改为「见 STATS 块 %s」' % key))
    if verbose:
        print('  方向 C：核对 %d 处手写值' % verified)
    return bad, verified


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='文档三向体检：A 符号存在 / B 生成表同步 / C 数字与 STATS 一致')
    ap.add_argument('reports', nargs='*', default=None,
                    help='要检查的 md 文件或目录；目录会递归展开 *.md。' +
                         '不写=主文档 + docs/（若存在）')
    ap.add_argument('--verbose', action='store_true', help='打印通过项')
    ap.add_argument('--quiet', action='store_true', help='只报失败')
    ap.add_argument('--symbols', action='store_true',
                    help='只做方向 A：文档引用的符号是否存在')
    ap.add_argument('--table', action='store_true',
                    help='只做方向 B：§17 生成表是否与源码一致')
    ap.add_argument('--stats', action='store_true',
                    help='只做方向 C：手写指标值与 STATS/源码实况比对')
    ap.add_argument('--lines', action='store_true',
                    help='行号模式（历史报告用；正文已不写行号）')
    args = ap.parse_args(argv)

    # ★ 跨文件扫描（2026-10-20）：把参数（文件/目录）展开成实际 md 列表。
    #   文档正在拆成 docs/*.md，检查器必须能一次覆盖全部，而不是只认主文件。
    args.reports = discover_reports(args.reports or default_reports())
    if not args.reports:
        print('没有可检查的 md 文件（参数与默认范围都为空）')
        return 2

    # ── 行号模式：保留给历史报告与临时核对 ──
    if args.lines:
        return _check_line_refs(args)

    # 单方向选择：给了哪个 --xxx 就只跑哪个
    picked = [f for f in (args.symbols, args.table, args.stats) if f]
    do_a = do_b = do_c = True
    if picked:
        do_a, do_b, do_c = args.symbols, args.table, args.stats

    print('文档三向体检：%d 个文件' % len(args.reports))
    for _rp in args.reports:
        print('  · %s' % os.path.relpath(_rp, HERE))

    bad = 0
    if do_a:
        n = check_symbols(args.reports, verbose=args.verbose)
        if n:
            bad += n
        elif not args.quiet:
            print('  方向 A（文档→代码）：引用的符号全部存在')
    if do_b:
        # 方向 B（§17 生成表）只在**主文档**里有；拆分子文档不含该表。
        _main = os.path.join(HERE, '模拟服全链路代码解析.md')
        _tbl_doc = _main if _main in args.reports else args.reports[0]
        n = check_table(_tbl_doc, verbose=args.verbose)
        if n:
            bad += n
        elif not args.quiet:
            print('  方向 B（表→代码）：§17 索引与源码一致')
    if do_c:
        c_bad, verified = check_stats_refs(args.reports, verbose=args.verbose)
        if c_bad:
            bad += len(c_bad)
            for rel, no, what, claimed, actual, fix in c_bad:
                print('  方向 C（数字→STATS）：✗ %s:%d  %s：文档 %r，实况 %r'
                      % (rel, no, what, claimed, actual))
                print('      修法：%s' % fix)
        elif not args.quiet:
            print('  方向 C（数字→STATS）：手写值与实况一致（核对 %d 处）'
                  % verified)

    if bad:
        print('')
        print('结果：%d 项不通过' % bad)
        return 1
    if not args.quiet:
        print('')
        print('结果：三向一致')
    return 0


def _check_line_refs(args):
    """行号模式（历史报告）。"""
    src = load_sources()
    ok_n, warns, errors = 0, [], []
    pat = re.compile(r'([\w.]+\.py):(\d+)(?:-(\d+))?')
    for rp in args.reports:
        try:
            doc = io.open(rp, encoding='utf-8', errors='replace').read()
        except (IOError, OSError) as e:
            print('读不到 %s: %s' % (rp, e))
            continue
        for lineno, line in enumerate(doc.split(chr(10)), 1):
            for m in pat.finditer(line):
                fn, a = m.group(1), int(m.group(2))
                fname = os.path.basename(fn)
                if fname not in src:
                    errors.append((rp, lineno, fname, a, '未知源文件'))
                    continue
                body = src[fname]
                if a < 1 or a > len(body):
                    errors.append((rp, lineno, fname, a,
                                   '行号越界（文件共 %d 行）' % len(body)))
                    continue
                verdict = classify(body[a - 1])
                if verdict == 'OK':
                    ok_n += 1
                elif verdict == 'WARN':
                    warns.append((rp, lineno, fname, a, body[a - 1].strip()[:60]))
                else:
                    errors.append((rp, lineno, fname, a, '无法判定'))

    base = os.path.basename(args.reports[0]) if args.reports else '?'
    print('文档行号体检：%s（%d 个源文件）' % (base, len(src)))
    print('  OK %d / WARN %d / ERROR %d' % (ok_n, len(warns), len(errors)))
    if not args.quiet:
        for r in warns:
            print('  WARN  报告第%d行 %s:%s → %r' % (r[1], r[2], r[3], r[4]))
    for r in errors:
        print('  ERROR 报告第%d行 %s:%s → %s' % (r[1], r[2], r[3], r[4]))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
