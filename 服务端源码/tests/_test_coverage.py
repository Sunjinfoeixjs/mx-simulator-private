"""上行覆盖率与三档归类的自检：口径正确 + 分档不漏不陈。

跑的是 `协议资料/报告/_c2s_coverage.py` 的同一套函数，只断言不打印。
守三件事：
  ① **分子口径**：只认 `handle()` 前置段 / `_dispatch()` 分支条件里出现过的 opcode，
     而不是「常量名在内核里被提到」；
  ② **分档不漏**：每条未实现项都能在 `C2S_TIERS` 里找到档位（漏了报告就会少算）；
  ③ **分档不陈**：`C2S_TIERS` 里不能留着已经实现的条目（实现完忘了删，分档数字就会骗人）。
"""
import os
import sys

sys.path.insert(0, r'C:\Users\Administrator\MX\协议资料\报告')
import _c2s_coverage as C

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


net_path = os.path.join(C.SRC, 'net.py')
if not os.path.exists(net_path):
    print('OK   （跳过）客户端源码不在本机：%s' % net_path)
    sys.exit(0)

import io

net = io.open(net_path, encoding='utf-8').read()
consts = C.load_c2s_consts(net)
users = C.const_users(C.SRC, consts)
handled = C.dispatched_ops(C.SIM)

alive = {}
for nm, op in consts.items():
    if users.get(nm):
        alive.setdefault(op, nm)
miss = sorted(op for op in alive if op not in handled)
impl = sorted(op for op in alive if op in handled)

print('=' * 70)
print(' A. 口径')
print('=' * 70)
check(len(consts) == 138, '上行常量 138 条（实得 %d）' % len(consts))
check(len(alive) == 134, '客户端会发且去重后 134 个 opcode（实得 %d）' % len(alive))
check(len(impl) + len(miss) == len(alive), '已实现 + 未实现 == 分母（%d+%d）' % (len(impl), len(miss)))
check(len(handled) > 0, '严格口径解析到 %d 个 opcode 有分支' % len(handled))
# 严格口径必须能展开集合常量（C2S_WAR_SUMM_ACTIONS 那批按名字匹配时一个都数不到）
check(len([op for op in handled if 160 <= op <= 177]) >= 10,
      '战斗段集合常量已展开（160-177 命中 %d 个）'
      % len([op for op in handled if 160 <= op <= 177]))
# 前置段的心跳也算已实现
check(8 in handled, '前置段处理的心跳 op8 计入已实现')

print()
print('=' * 70)
print(' B. 分档完整性')
print('=' * 70)
unknown = [op for op in miss if op not in C.C2S_TIERS]
check(not unknown, '未实现项都有分档（漏 %r）' % unknown)
bad_tier = [(op, t) for op, t in C.C2S_TIERS.items() if t[0] not in ('A', 'B', 'C')]
check(not bad_tier, '档位只能是 A/B/C（异常 %r）' % bad_tier)
stale = sorted(op for op in C.C2S_TIERS if op in handled)
check(not stale, '分档表里没有已实现条目（残留 op=%r）' % stale)
no_reason = [op for op, t in C.C2S_TIERS.items() if not t[1].strip()]
check(not no_reason, '每条分档都写了理由（缺 %r）' % no_reason)

by = {}
for op in miss:
    by[C.C2S_TIERS[op][0]] = by.get(C.C2S_TIERS[op][0], 0) + 1
check(sum(by.values()) == len(miss),
      '三档合计 == 未实现数（A%d+B%d+C%d=%d / %d）'
      % (by.get('A', 0), by.get('B', 0), by.get('C', 0), sum(by.values()), len(miss)))
# 未实现为 0 时「A 档非空」前提已不成立（覆盖率收敛到 100%），
# 只在仍有未实现项时校验分档形状，避免把「已全部实现」误判成失败。
if miss:
    check(1 <= by.get('A', 0) < len(miss),
          'A 档非空且不是全部（%d/%d）' % (by.get('A', 0), len(miss)))
else:
    print('OK   （跳过）未实现为 0，覆盖率已收敛，无需校验三档形状')

print()
print('=' * 70)
print(' C. 已实现的必须真的在分支里（抽查关键项）')
print('=' * 70)
for op, why in ((16, '加点'), (17, '升级'), (103, '学招式'), (106, '收购'),
                (88, 'OpenUI'), (192, 'Common')):
    check(op in handled, 'op%-4d（%s）在分派分支里' % (op, why))

print()
print('=' * 70)
print(' D. 报告里的数字不能过期（同一类事故已经发生过两次）')
print('=' * 70)
report = r'C:\Users\Administrator\MX\协议资料\报告\客户端网络层解析_20261018.md'
if not os.path.exists(report):
    print('OK   （跳过）报告不存在：%s' % report)
else:
    import re

    md = io.open(report, encoding='utf-8').read()
    head = re.search(r'^## 四、上行：(\d+) 条会发，(\d+) 条有实现（(\d+)%）', md, re.M)
    tail = re.search(r'未实现 \*\*(\d+) 条\*\* → A (\d+) / B (\d+) / C (\d+)', md)
    alive_names = len([1 for nm, op in consts.items() if users.get(nm)])
    pct = int(round(100.0 * len(impl) / max(1, len(alive))))
    if not head or not tail:
        check(False, '报告 §4 的统计句子找不到（标题格式变了？要同步改本测试）')
    else:
        got = tuple(int(x) for x in head.groups())
        want = (alive_names, len(impl), pct)
        check(got == want,
              '§4 标题「%d 条会发 / %d 条有实现 / %d%%」与实际一致（实际 %r）'
              % (got + (want,)))
        g2 = tuple(int(x) for x in tail.groups())
        w2 = (len(miss), by.get('A', 0), by.get('B', 0), by.get('C', 0))
        check(g2 == w2,
              '§4.1「未实现 %d → A%d/B%d/C%d」与实际一致（实际 %r）'
              % (g2 + (w2,)))
        if got != want or g2 != w2:
            print('     → 跑一遍 协议资料/报告/_c2s_coverage.py 并更新报告 §4')

print()
print('=' * 70)
print(' 上行覆盖率自检 %s' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
