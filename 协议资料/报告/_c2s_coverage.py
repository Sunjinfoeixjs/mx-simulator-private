# -*- coding: utf-8 -*-
"""上行覆盖率与三档归类（一次性分析脚本，产出打印文本，可重复运行）。

三个口径，之前混在一起过，这里分开：

  **分母** = 客户端**真的会发**的 opcode：常量在 `net.py` 定义，且被 `net.py` 以外的模块引用。
             只定义没人用的（死常量）不算——那会把覆盖率做低。
  **分子** = `_dispatch()` 里**真的出现在分支条件**里的 opcode
             （`op == X` / `op in (...)` / `op in 集合常量`）。
             只在内核里"提到名字"不算：日志、白名单、注释里出现过常量名不等于有分支。
  **分档** = 按「离线单人玩得到吗」把未实现项分 A/B/C，见 `C2S_TIERS`。

A 档 = 影响单人可玩（有 UI 入口，点了没反应）
B 档 = 社交/在线功能（离线没有对象）
C 档 = 未开放玩法（原服也没对单人开放，或需要运营系统）

新增 opcode 时：实现完把 `C2S_TIERS` 里的那条删掉即可，覆盖率会自动上升。

★ 2026-10-19 扩展「真机实测证据」维度：静态分析只能说「客户端会发」，不能说
  「真玩起来确实会碰到」。把调试台 `/api/stats` 的 `unknown_c2s`（客户端真发过、
  服务端当时没分支）抓进来，就能把未实现项分成「纸上存在」与「真机会踩」——
  后者才是补覆盖的优先项。离线时用内置快照 `LIVE_EVIDENCE`，在线加 `--live` 刷新。
"""
from __future__ import print_function

import argparse
import collections
import io
import os
import re
import sys

SRC = r'C:\Users\Administrator\MX\客户端源码\src'
SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
if not os.path.isdir(SIM):
    # 本工作区布局：sim 被整体挪进 服务端源码/tests/sim（写死单一路径会让
    # 「上行覆盖率」这一步在回归里恒红，且报错是 FileNotFoundError 而非可读提示）
    SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'

sys.path.insert(0, SIM)

# ── 三档归类：opcode → (档位, 理由) ─────────────────────────────────────
# 只登记**未实现**的；实现后删掉那条，覆盖率与分档会一起变。
# ★ 2026-10-19：op100 NPC 任务选项 / op101 物品详情 已实现（_dispatch 分支 + S2C 应答），
#   按脚本契约从「未实现」表移除；正文见 m4_server._dispatch 与 真机实验记录。
# ★ 2026-10-19：上行覆盖已收敛到 134/134（100%）。原先 A/B/C 三档里的 70 条
#   已全部落地为 _dispatch 的显式分支，本表因此清空——它现在只用于登记
#   「新发现、尚未实现」的 opcode。新增未实现项时往这里补一条即可，
#   实现后删掉，覆盖率会自动上升（脚本会报「已实现残留」）。
C2S_TIERS = {}

# ── 第三态：已接住（_dispatch 有显式忽略分支）但功能未实现 ─────────────────
# 为什么单列：这些码**不算未实现**（有分支，不会落战斗保底），但**也不算实现**
# （单机服无对手方/无业务）。留在 C2S_TIERS 会永远触发「已实现残留」告警，
# 删掉又会丢信息——单列一张表，覆盖率口径不变、信息不丢。
C2S_IGNORED = {
    19: 'DIY_MAGIC 自创招式（单机无业务）',
    20: 'PLAYER_PHOTO 头像（无对手方）',
    23: 'PLAYER_IB 物品账单/交易明细（无对手方）',
    52: 'HERO_SALE 摆摊/角色出售（需摆摊系统）',
    55: 'MAP_SYN_DIREMOTE 方向/表情同步（单机无他人；真机高频）',
    80: 'DISTRICT 街区（无业务）',
    149: 'PLAYER_CHAT 世界/当前频道（单机无对手方）',
}

# ── 真机实测证据（2026-10-19 真机会话，来自调试台 /api/stats）─────────────
# unknown_c2s：客户端**真发过**、当时服务端没分支（现已全部接住或实现）
# c2s_nodef  ：客户端会发、但字典缺字段定义（出现时只能看 hex，需补字典）
LIVE_EVIDENCE = {
    'unknown_c2s': [19, 20, 23, 49, 52, 55, 80, 149],
    'c2s_nodef': [8, 15, 90, 91, 114, 125, 135, 158, 250],
    'note': '2026-10-19 真机会话快照（39 种 C2S）；--live 可从运行中的调试台刷新',
}

# 分派链 if/elif 分支数下限（dispatched_ops 的扫描段守卫，2026-10-20）。
# 来历：实测 handle+_dispatch_locked 全量约 130+ 条；取 40 留有充分余量，
# 又能抓住「分支体被搬走」这类漂移（漂移后实测只剩 19 条）。
MIN_BRANCHES = 40


def fetch_live(base='http://127.0.0.1:8090'):
    """从运行中的调试台抓实时证据；不可用返回 None（不阻断离线分析）。"""
    try:
        from urllib.request import urlopen
        import json
        with urlopen(base + '/api/stats', timeout=1.5) as r:
            st = json.load(r)
        return {'unknown_c2s': st.get('unknown_c2s') or [],
                'c2s_nodef': st.get('c2s_nodef') or [],
                'note': '实时抓取 %s' % base}
    except Exception:                                     # noqa: BLE001
        return None


def load_c2s_consts(net_text):
    """上行常量：名字 → opcode（C2S_* 与 C2GS_* 两族都要认）。"""
    consts = {}
    for m in re.finditer(r'^\s*((?:C2S|C2GS)_\w+)\s*=\s*(\d+)\s*$', net_text, re.M):
        consts.setdefault(m.group(1), int(m.group(2)))
    return consts


def const_users(src_dir, consts):
    """常量名 → 引用它的客户端模块集合（net.py 自己不算，它对每个常量都出现）。"""
    users = collections.defaultdict(set)
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            rel = os.path.relpath(os.path.join(root, fn), src_dir)[:-3].replace(os.sep, '/')
            if rel == 'net':
                continue
            with io.open(os.path.join(root, fn), encoding='utf-8', errors='replace') as f:
                text = f.read()
            for nm in set(re.findall(r'\b(C2S_\w+|C2GS_\w+)\b', text)):
                users[nm].add(rel)
    return users


def dispatched_ops(sim_dir):
    """`_dispatch()` 里真正出现在分支条件中的 opcode 集合。

    ★ 这是本脚本的核心口径：只认 `if/elif` 的条件表达式，不认日志/注释里提到的常量名。
      否则「写了常量但没接线」会白算成已实现（before 2026-10-18 就是这么算的）。

    ★ 2026-10-20 修（静默错读事故）：`_dispatch` 被重构成「包装方法 + _dispatch_locked」
      （RLock 临界区，见 m4_server._dispatch 注释），分支体整体搬进 _dispatch_locked。
      旧实现只扫 `def _dispatch(` 到下一个方法 → 只捡到 handle 的前置分支 19 条，
      覆盖率从 100% 静默掉到 14%，而脚本没挂回归、没人发现。
      现在：若存在 `_dispatch_locked`，扫描段延伸到它的末尾；并用 MIN_BRANCHES
      兜底——分支数跌破下限直接判红（大声失败，不给错数）。
    """
    import m4_server as m

    with io.open(os.path.join(sim_dir, 'm4_server.py'), encoding='utf-8') as f:
        lines = f.read().split('\n')

    # 分派不只在 _dispatch 里：handle() 在调它之前还有一段前置分支（心跳 op8 就在那儿）。
    # 两段一起扫，否则「已实现」会漏掉前置段处理的 opcode。
    pre = next(i for i, l in enumerate(lines) if l.strip().startswith('def handle('))
    start = next(i for i, l in enumerate(lines) if l.strip().startswith('def _dispatch('))
    # 分支体可能在 _dispatch_locked 里（2026-10-20 锁重构后的形态）
    locked = next((i for i, l in enumerate(lines)
                   if l.strip().startswith('def _dispatch_locked(')), None)
    body_start = locked if locked is not None else start
    end = next(i for i in range(body_start + 1, len(lines))
               if re.match(r'^    def ', lines[i]))
    seg = lines[pre:end]

    # 条件可能跨行（`elif (op in (...)\n or op == X)`），按缩进把续行拼回来
    conds, cur = [], None
    for line in seg:
        s = line.strip()
        if re.match(r'^(if|elif)\b', s):
            if cur:
                conds.append(cur)
            cur = s
        elif cur is not None and line.startswith('                ') and not s.startswith('#'):
            cur += ' ' + s        # 续行（条件折行）
        elif cur is not None:
            conds.append(cur)
            cur = None
    if cur:
        conds.append(cur)

    # ★ 扫描段守卫：分派链的历史分支数下限。跌破说明 _dispatch 又被重构/搬走了，
    #   此时任何「覆盖率」数字都是错的——必须红，不能静默输出一个难看的百分比。
    if len(conds) < MIN_BRANCHES:
        raise RuntimeError(
            '分派链只扫到 %d 条 if/elif（下限 %d）——_dispatch 的分支体疑似又漂移了'
            '（当前从 handle() 扫到 %s）。修 dispatched_ops 的扫描段，不要放宽下限。'
            % (len(conds), MIN_BRANCHES,
               '_dispatch_locked 末尾' if locked is not None else '_dispatch 末尾'))

    ops = set()

    def add_token(tok):
        tok = tok.strip().strip('()[]{},')
        if not tok:
            return
        if tok.isdigit():
            ops.add(int(tok))
        elif tok in m.__dict__:
            val = getattr(m, tok)
            if isinstance(val, int):
                ops.add(val)
            elif isinstance(val, (set, frozenset, list, tuple)):
                ops.update(v for v in val if isinstance(v, int))

    for c in conds:
        for mt in re.finditer(r'\bop\s*==\s*([A-Za-z_]\w*|\d+)', c):
            add_token(mt.group(1))
        for mt in re.finditer(r'\bop\s+in\s+(\([^)]*\)|[A-Za-z_]\w*|\[[^\]]*\])', c):
            for tok in re.split(r'[,\s]+', mt.group(1).strip('()[]')):
                if tok in ('or', 'and'):
                    continue
                add_token(tok)
    return ops


def compute(live_url='http://127.0.0.1:8090', use_live=False):
    """静态计算覆盖率事实，不打印（供 main() 与 tests/_stats.py 复用）。

    返回 dict：consts / alive_names / alive(分母) / impl(分子) / miss / ghost /
              sent / nodef / by_tier / unknown / caught / stale / miss_ign / note
    可能 raise RuntimeError：分派链扫描段守卫触发（分支体漂移）。
    """
    net_text = io.open(os.path.join(SRC, 'net.py'), encoding='utf-8').read()
    consts = load_c2s_consts(net_text)
    users = const_users(SRC, consts)
    handled = dispatched_ops(SIM)

    ev = (fetch_live(live_url) if use_live else None) or LIVE_EVIDENCE
    sent = set(ev.get('unknown_c2s') or [])      # 真机实测确实发过的
    nodef = set(ev.get('c2s_nodef') or [])       # 字典缺字段定义

    alive_names = [(op, nm) for nm, op in consts.items() if users.get(nm)]
    alive = {}
    for op, nm in alive_names:
        alive.setdefault(op, nm)          # 同码异名（23 / 88）在这里合并
    impl = sorted(op for op in alive if op in handled)
    miss = sorted(op for op in alive if op not in handled)

    ghost = sorted(op for op in handled if op not in alive and 1 <= op <= 250)

    by_tier = collections.defaultdict(list)
    unknown = []
    for op in miss:
        tier = C2S_TIERS.get(op)
        if not tier:
            unknown.append(op)
            continue
        by_tier[tier[0]].append((op, tier[1], sorted(users[alive[op]])[:2]))

    caught = sorted(op for op in C2S_IGNORED if op in handled)
    miss_ign = [op for op in C2S_IGNORED if op not in handled]
    stale = sorted(op for op in C2S_TIERS if op in handled)
    return {
        'note': ev.get('note'), 'sent': sent, 'nodef': nodef,
        'consts': consts, 'alive_names': alive_names, 'alive': alive,
        'users': users,
        'impl': impl, 'miss': miss, 'ghost': ghost,
        'by_tier': by_tier, 'unknown': unknown,
        'caught': caught, 'miss_ign': miss_ign, 'stale': stale,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='C2S 上行覆盖率与 A/B/C 分档')
    ap.add_argument('--live', action='store_true',
                    help='尝试从调试台 %s 抓实时证据（不可用则退回内置快照）'
                         % 'http://127.0.0.1:8090')
    ap.add_argument('--live-url', default='http://127.0.0.1:8090')
    args = ap.parse_args(argv)

    try:
        f = compute(live_url=args.live_url, use_live=args.live)
    except RuntimeError as e:
        # 扫描段守卫：分派链形态漂移。此时任何百分比都是错的——大声红。
        print('✗ %s' % e)
        return 2

    sent, nodef = f['sent'], f['nodef']
    consts, alive_names, alive = f['consts'], f['alive_names'], f['alive']
    impl, miss, ghost = f['impl'], f['miss'], f['ghost']

    print('真机证据：%s' % f['note'])
    print('  真机发过（当时无分支）%d 条：%r' % (len(sent), sorted(sent)))
    print('  字典缺字段定义 %d 条：%r' % (len(nodef), sorted(nodef)))
    print()

    print('上行常量 %d 条；客户端会发 %d 条（去重后 %d 个 opcode，23/88 各有两名）'
          % (len(consts), len(alive_names), len(alive)))
    print('已实现 %d / 未实现 %d（覆盖率 %.0f%%，分母取去重 opcode %d）'
          % (len(impl), len(miss), 100.0 * len(impl) / max(1, len(alive)), len(alive)))
    print()

    if ghost:
        print('（提示）内核有分支但客户端不会发：%r（多为预留/历史遗留）' % ghost)
        print()

    by_tier = f['by_tier']
    unknown = f['unknown']
    print('—— 未实现项分档 ——')
    for t in ('A', 'B', 'C'):
        names = {'A': '影响单人可玩', 'B': '社交/在线功能', 'C': '未开放玩法'}[t]
        print('%s 档（%s）：%d 条' % (t, names, len(by_tier[t])))
    print()
    for t in ('A', 'B', 'C'):
        names = {'A': '影响单人可玩', 'B': '社交/在线功能', 'C': '未开放玩法'}[t]
        print('== %s 档 · %s ==' % (t, names))
        for op, why, mods in by_tier[t]:
            mark = '★真机' if op in sent else '     '
            print('  %-4d %s %-26s %-28s %s'
                  % (op, mark, alive[op].replace('C2S_', '').replace('C2GS_', ''), why,
                     ','.join(mods)))
        print()

    caught = f['caught']
    if caught:
        print('== 已接住（显式忽略）但功能未实现 · %d 条 ==' % len(caught))
        for op in caught:
            print('  %-4d %-26s %s%s'
                  % (op, alive.get(op, '?').replace('C2S_', ''), C2S_IGNORED[op],
                     '（真机实测发过）' if op in sent else ''))
        if f['miss_ign']:
            print('  ⚠ C2S_IGNORED 里这些其实**没**接住（要补分支）：%r' % f['miss_ign'])
        print()

    stale = f['stale']
    if stale:
        print('⚠ C2S_TIERS 里还留着**已实现**的条目（实现后该删）：')
        for op in stale:
            print('  %-4d %-26s %s' % (op, alive.get(op, '?'), C2S_TIERS[op][1]))
        print()

    if unknown:
        print('⚠ 未分档的 opcode（要补进 C2S_TIERS）：')
        users = f['users']
        for op in unknown:
            print('  %-4d %-26s 发送方 %s'
                  % (op, alive[op], ','.join(sorted(users[alive[op]])[:3])))
    else:
        print('OK  所有未实现项都已分档（%d 条）' % len(miss))
    return 0 if (not unknown and not stale) else 1


if __name__ == '__main__':
    sys.exit(main())
