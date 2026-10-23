# -*- coding: utf-8 -*-
"""存档/日志根路径契约：`CLAW` 必须等于项目根，`SAVE_DIR`/`LOGDIR` 不得套两层。

背景（P5-00 审计发现，2026-10-23）：
    这四个模块都用同一行算项目根：

        CLAW = os.path.dirname(os.path.dirname(os.path.dirname(
                   os.path.abspath(__file__))))
        LOGDIR   = os.path.join(CLAW, '服务端源码', 'logs')
        SAVE_DIR = os.path.join(CLAW, '服务端源码', 'save')

    这行只在「文件位于 MX/<a>/<b>/x.py」时成立。实际文件在
    `MX/服务端源码/tests/sim/`，是 **4 层**深 → `dirname×3` 只到
    `MX/服务端源码`，再拼一次 `'服务端源码'` 就成了：

        期望  MX/服务端源码/save/role.json
        实际  MX/服务端源码/服务端源码/save/role.json

    后果不是崩溃，而是**静默换档**：
      · `load_role()` 对「文件不存在」返回 None，属于正常路径，不报错；
      · 默认启动时 `singleplayer.py` 用 `args.save_dir or m.SAVE_DIR`；
      · 于是历史 `MX/服务端源码/save/role.json` 不会被读到 → 玩家以为进度丢了。

    实测现场（本测试写成时的磁盘状态）：
      · `MX/服务端源码/save/role.json`          2763 B（10-22 02:19）← 真存档
      · `MX/服务端源码/服务端源码/logs/`         **242** 份 m4_srv_*.log，最新 10-23 17:16
      · `MX/服务端源码/logs/`                    **1288** 份，最新 10-22 17:28
      · `MX/服务端源码/服务端源码/save/`         不存在

    ★ 日志侧不是「将来可能出事」而是**已经坏了**：会话闸门
      `协议资料/报告/验收/_verify_session_flow.py` 自己算的根是对的，读 `服务端源码/logs`；
      而服务端写到嵌套目录 → 闸门永远看不到 10-23 的新会话，只能拿 10-22 的旧日志判 FAIL。
      所以修的不是「潜在风险」，是「P5-01/P5-09 的前置阻断项」。

本测试锁定三件事：
  1. `m4_server.CLAW` == 项目根（由本文件独立推导，不借用被测模块的算法）
  2. `SAVE_DIR` / `LOGDIR` 相对项目根的路径**恰好**是「服务端源码/save」「服务端源码/logs」，
     即路径分量里不得出现两个连续的 `服务端源码`
  3. 其余三个同源模块（m2_server / crypto_capture / crypto_probe）的 `CLAW` 行必须与
     m4_server 完全一致 —— 否则修一处漏三处，日志会分裂到不同目录
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


# ── 项目根的独立推导 ────────────────────────────────────────────────
# 本文件在 MX/服务端源码/tests/，所以往上一层是 服务端源码、再往上一层是 MX。
# ★ 这里**刻意不复用** m4_server 的算法：如果用被测对象算期望值，
#   它错的时候期望值跟着一起错，测试永远绿。
PROJ = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(PROJ, '服务端源码')


def rel_parts(path, root):
    """相对 root 的路径分量（小写化 + 去掉空段），用于判「套了几层」。"""
    p = os.path.normpath(os.path.abspath(path))
    r = os.path.normpath(os.path.abspath(root))
    try:
        rel = os.path.relpath(p, r)
    except ValueError:                       # Windows 跨盘符
        return ['<other-drive>']
    return [x.lower() for x in re.split(r'[\\/]+', rel) if x]


def main():
    # ── 前置：独立推导出的一切必须自洽，否则是测试写错了 ──
    check('前置：项目根下存在 服务端源码/', os.path.isdir(SRC), SRC)
    check('前置：tests 与 sim 同层（本工作区布局）', os.path.isdir(SIM), SIM)

    try:
        import m4_server as m
    except Exception as e:                    # noqa: BLE001
        check('import m4_server', False, '%s: %s' % (type(e).__name__, e))
        return finish()

    # ── ① CLAW 必须等于项目根 ──
    claw = getattr(m, 'CLAW', '<无此属性>')
    check('m4_server.CLAW == 项目根',
          os.path.normpath(str(claw)) == os.path.normpath(PROJ),
          'CLAW=%s  期望=%s' % (claw, PROJ))

    # ── ② SAVE_DIR / LOGDIR 的分量不得重复 ──
    # 期望分量（相对项目根）：
    #   save → ['服务端源码', 'save']
    #   logs → ['服务端源码', 'logs']
    for attr, tail in (('SAVE_DIR', 'save'), ('LOGDIR', 'logs')):
        path = getattr(m, attr, '<无此属性>')
        parts = rel_parts(path, PROJ) if not str(path).startswith('<') else []
        want = ['服务端源码', tail]
        check('%s 相对项目根恰为 服务端源码/%s' % (attr, tail),
              parts == want,
              '%s → %s' % (attr, '/'.join(parts) or str(path)))

    # ── ③ SAVE_PATH 必须落在 SAVE_DIR 里（别只修一半） ──
    sp = getattr(m, 'SAVE_PATH', '')
    sd = getattr(m, 'SAVE_DIR', '')
    check('SAVE_PATH 位于 SAVE_DIR 内',
          bool(sp) and bool(sd) and
          os.path.normpath(os.path.dirname(os.path.abspath(sp))) ==
          os.path.normpath(os.path.abspath(sd)),
          'SAVE_PATH=%s  SAVE_DIR=%s' % (sp, sd))

    # ── ④ 真实存档可达性：只要磁盘上确实存在历史档，SAVE_DIR 就必须指向它 ──
    # 这条是「结果导向」的：不关心怎么算的，只关心玩家那份档找不找得到。
    legacy = os.path.join(SRC, 'save', 'role.json')
    if os.path.isfile(legacy):
        ok = (os.path.normpath(os.path.abspath(sd)) ==
              os.path.normpath(os.path.abspath(os.path.dirname(legacy))))
        check('历史存档 role.json 落在 SAVE_DIR 里', ok,
              '' if ok else '磁盘上有 %s，但 SAVE_DIR=%s' % (legacy, sd))
    else:
        print('[SKIP] 磁盘上没有 %s，跳过「历史存档可达」一项（不是通过）'
              % legacy)

    # ── ⑤ 同源模块的 CLAW 行必须一致（静态，避免修一处漏三处） ──
    # 允许前导缩进：修好之后这行在 try/except 里，不是顶格。
    pat = re.compile(r'^\s*CLAW\s*=\s*(.+?)\s*$', re.M)
    m4_line = None
    for fn in ('m4_server.py', 'm2_server.py', 'crypto_capture.py', 'crypto_probe.py'):
        p = os.path.join(SIM, fn)
        if not os.path.isfile(p):
            check('%s 存在' % fn, False, p)
            continue
        src = io.open(p, 'r', encoding='utf-8', errors='replace').read()
        hit = pat.search(src)
        if not hit:
            check('%s 有 CLAW 定义' % fn, False, '未匹配到 ^CLAW=')
            continue
        expr = hit.group(1)
        if m4_line is None:
            m4_line = expr
        same = (expr == m4_line)
        check('%s 的 CLAW 与 m4_server 同式' % fn, same,
              '' if same else '%s ≠ %s' % (expr, m4_line))

    return finish()


def finish():
    npass = sum(1 for _, ok in results if ok)
    print('')
    print('=' * 60)
    print('存档/日志根路径契约：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
