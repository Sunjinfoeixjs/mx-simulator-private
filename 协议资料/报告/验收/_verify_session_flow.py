# -*- coding: utf-8 -*-
"""真机会话「连续完整流程」闸门：拿 `logs/m4_srv_*.log` 判人工 GUI 验收是否成立。

为什么需要它
------------
自动化回归（`sim/tests/_run_all.py`）用的是**协议级 socket 客户端**，能证明服务端的
字节与帧序，但**证明不了**真实 `mx.exe` 能不能把这条流程走完（渲染、UI 状态机、
「请等待」是否清除、场景是否重建，都只有客户端自己知道）。

所以人工验收拆成两步，缺一不可：

  ① 人在客户端里点（GUI 记录：看得对不对）；
  ② 拿那次会话的日志过这道闸门（机器记录：**是不是同一个会话里连续走完的**）。

闸门检查的是机器能判的部分：

  · 会话切分：每个 `── 接入:` 是一次连接；握手未完成的不计入；
  · 连续性：一次会话内走完「登录→进世界→移动→遇怪→战斗闭环→场景恢复→NPC→传送→再战」，
    中途没有重连（重连 = 连续性不成立，客户端崩过/踢过）；
  · 战斗闭环配对：每个 `遇怪！WarStart` 后面必须有 `WarEnd`，且再后面有场景重同步；
  · 计数：遇怪场数、死亡清场次数、传送次数、移动次数。

用法
----
    python 协议资料/报告/验收/_verify_session_flow.py                       # 自动取最新 m4_srv_*.log
    python 协议资料/报告/验收/_verify_session_flow.py --log <path> --min-battles 2
    python 协议资料/报告/验收/_verify_session_flow.py --md 协议资料/报告/验收/会话检查.md
    python 协议资料/报告/验收/_verify_session_flow.py --self-test            # 解析器自检（CI 用）

判定：必需阶段齐全 + 单会话 + 战斗闭环配对无缺 + 场数 ≥ --min-battles → 退出码 0。
"""
import argparse
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))   # 验收 → 报告 → 协议资料 → MX
DEFAULT_LOG_DIR = os.path.join(MX, '服务端源码', 'logs')

# (阶段键, 中文名, 正则, 是否必需)
STAGES = [
    ('hello',   '握手 Hello',        r'② Hello PASS',                       True),
    ('login',   '登录（账号上线）',   r'>>> 登录: \S+',                       True),
    ('role',    '建角/载入存档',      r'>>> 建角: |已载入角色存档',            True),
    ('world',   '进世界',            r'>>> 选角进入|进世界：',                True),
    ('move',    '移动',              r'>>> 移动 ',                           True),
    ('war',     '遇怪进战斗',        r'遇怪！WarStart',                      True),
    ('act',     '主角出指令',        r'   普攻 |   招式 pid=|   >>> 防御',     True),
    # 召唤兽动作用 `--min-pet` 单独卡（默认 ≥1）：宠物可能在战斗中倒地，
    # 那时没有指令帧是合规的，但不能整场验收一次都没有。
    ('pet',     '召唤兽指令结算',     r'等待宠物指令 op168-174|宠物招式 pid=|   宠物 \d+ → 敌', False),
    ('enemy',   '敌方回合',          r'   敌方 \d+ 行动 →',                   True),
    ('end',     '战斗结束 WarEnd',   r'→ WarEnd',                            True),
    ('resync',  '场景恢复重同步',     r'↻ 战斗结束场景重同步',                 True),
    ('npc',     'NPC 交互',          r'>>> 点击 NPC|>>> (接任务|交任务)',       False),
    ('shop',    '商店/买卖',         r'NPC 购买|下发商店列表|NPC 收购',         False),
    ('travel',  '切图/传送',         r'★ 传送 →|→ 目标图 ',                   False),
]

RE_SESSION = re.compile(r'── 接入:')
RE_END = re.compile(r'（连接结束）')
RE_WAR_START = re.compile(r'遇怪！WarStart')
RE_WAR_END = re.compile(r'→ WarEnd')
RE_RESYNC = re.compile(r'↻ 战斗结束场景重同步')
RE_PET_DOWN = re.compile(r'宠物倒地 → WarDel|主角倒地 → WarEnd')


def _strip_prefix(line):
    """去掉 `[acct] ` 与 `[HH:MM:SS] ` 前缀，便于统一匹配。"""
    line = re.sub(r'^\[\d{2}:\d{2}:\d{2}\] ', '', line)
    line = re.sub(r'^\[[^\]]+\] ', '', line)
    return line


def split_sessions(text):
    """按 `── 接入:` 切会话 → [{'lines': [...], 'raw': [...]}]（第 0 段是启动头，丢弃）。"""
    sessions, cur = [], None
    for raw in text.splitlines():
        if RE_SESSION.search(raw):
            if cur is not None:
                sessions.append(cur)
            cur = {'raw': [], 'lines': []}
            continue
        if cur is None:
            continue
        cur['raw'].append(raw)
        cur['lines'].append(_strip_prefix(raw))
    if cur is not None:
        sessions.append(cur)
    return sessions


def analyse(session):
    """单个会话 → 阶段命中、计数、闭环配对。"""
    lines = session['lines']
    info = {'stages': {}, 'counts': {}, 'gaps': [], 'ended': False}
    for key, name, pat, required in STAGES:
        rx = re.compile(pat)
        hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
        info['stages'][key] = {'name': name, 'required': required,
                               'hit': bool(hits), 'first': lines[hits[0]].strip() if hits else '',
                               'count': len(hits)}
    info['ended'] = any(RE_END.search(ln) for ln in lines)
    info['counts'] = {
        'moves': info['stages']['move']['count'],
        'battles': info['stages']['war']['count'],
        'war_ends': info['stages']['end']['count'],
        'resyncs': info['stages']['resync']['count'],
        'teleports': info['stages']['travel']['count'],
        'pet_actions': info['stages']['pet']['count'],
        'enemy_turns': info['stages']['enemy']['count'],
        'deaths': sum(1 for ln in lines if RE_PET_DOWN.search(ln)),
    }
    # 战斗闭环配对：第 k 次 WarStart 之后，必须有第 k+1 个 WarEnd 与其 1 个 resync
    starts = [i for i, ln in enumerate(lines) if RE_WAR_START.search(ln)]
    ends = [i for i, ln in enumerate(lines) if RE_WAR_END.search(ln)]
    resyncs = [i for i, ln in enumerate(lines) if RE_RESYNC.search(ln)]
    for k, si in enumerate(starts):
        ei = next((j for j in ends if j > si), None)
        if ei is None:
            info['gaps'].append('第 %d 场战斗没有 WarEnd' % (k + 1))
            continue
        ri = next((j for j in resyncs if j > ei), None)
        if ri is None:
            info['gaps'].append('第 %d 场战斗 WarEnd 后没有场景重同步' % (k + 1))
    return info


def score(info):
    """会话完整度：必需阶段数 → 场数 → 非必需阶段数。"""
    req = sum(1 for k, v in info['stages'].items() if v['required'] and v['hit'])
    opt = sum(1 for k, v in info['stages'].items() if not v['required'] and v['hit'])
    return (req, info['counts']['battles'], opt)


def render(log_path, sessions, best_idx, best, min_battles, min_pet=1):
    """输出 markdown 报告文本（也可直接贴进人工验收记录）。"""
    st = best['stages']
    missing = [st[k]['name'] for k, _n, _p, req in STAGES if req and not st[k]['hit']]
    c = best['counts']
    handshakes = sum(1 for s in sessions if analyse(s)['stages']['hello']['hit'])
    continuity_ok = (best_idx == len(sessions) - 1)
    battles_ok = c['battles'] >= min_battles
    pet_ok = c['pet_actions'] >= min_pet
    balanced_ok = not best['gaps']
    verdict = (not missing) and continuity_ok and battles_ok and pet_ok and balanced_ok

    out = []
    out.append('## 会话闸门（机器判定）')
    out.append('')
    out.append('- 日志：`%s`' % os.path.basename(log_path))
    out.append('- 连接次数：**%d**（握手成功的会话 **%d** 个%s）'
               % (len(sessions), handshakes,
                  '，客户端重连过' if handshakes > 1 else ''))
    out.append('- 采用会话：**第 %d 个**（%d 行，' % (best_idx + 1, len(sessions[best_idx]['lines']))
               + ('日志中最后一个' if continuity_ok else '不是最后一个，之后又重连了') + '）')
    out.append('- 连续性：%s；战斗场数：%d（要求 ≥%d）；召唤兽指令：%d 次（要求 ≥%d）；闭环配对：%s'
               % ('✅ 末次会话内走完' if continuity_ok else '❌ 末次会话不完整',
                  c['battles'], min_battles, c['pet_actions'], min_pet,
                  '✅ 无缺失' if balanced_ok else '❌ 有缺失'))
    out.append('')
    out.append('| 阶段 | 必需 | 命中 | 次数 | 首条证据 |')
    out.append('|---|---|---|---|---|')
    for key, name, _pat, required in STAGES:
        v = st[key]
        ev = ('`%s`' % v['first'][:60]) if v['first'] else '—'
        out.append('| %s | %s | %s | %d | %s |'
                   % (name, '是' if required else '否',
                      '✅' if v['hit'] else ('❌' if required else '·'), v['count'], ev))
    out.append('')
    out.append('- 移动 %d 次 / 遇怪 %d 场 / 死亡清场 %d 次 / 传送 %d 次 / 宠物指令 %d 次 / 敌方回合 %d 次'
               % (c['moves'], c['battles'], c['deaths'], c['teleports'],
                  c['pet_actions'], c['enemy_turns']))
    if missing:
        out.append('- ❌ 缺必需阶段：%s' % '、'.join(missing))
    if not pet_ok:
        out.append('- ❌ 召唤兽指令只有 %d 次（要求 ≥%d）——宠物指令菜单是否弹出/是否卡超时，'
                   '必须人工确认' % (c['pet_actions'], min_pet))
    if best['gaps']:
        out.append('- ❌ 闭环缺口：%s' % '；'.join(best['gaps']))
    out.append('')
    out.append('**会话闸门判定：%s**' % ('PASS' if verdict else 'FAIL'))
    out.append('')
    out.append('> 这一栏只判「服务端日志里这条流程是否连续发生」。客户端画面对不对、'
               '按钮是否响应、有没有卡「请等待」，仍需在 §4 逐条人工勾选——两者都 PASS 才算验收通过。')
    return '\n'.join(out) + '\n', verdict


SELFTEST_LOG = u"""
[10:00:00] 模拟服 v4 127.0.0.1:3134（seed=0x12345678，每 5 次移动遇怪，回合超时 30s）
[10:00:01] ── 接入: ('127.0.0.1', 50001)
[10:00:01] ① S2C Hello 已发
[10:00:01] ② Hello PASS ✓（16 ed 4b）
[10:00:02] ③ C2S opcode=1 body=60B: 00 0a 2b 00 31 30 30 38 36 30 30 30 30 31 00
[acct1] [10:00:02]    >>> 登录: acct1
[acct1] [10:00:02]    （已载入角色存档 role_acct1.json）
[acct1] [10:00:03]    >>> 选角进入 id=1 name=测试
[acct1] [10:00:03]    进世界：PropInit + Resist/Power + 养成同步 + 招式9 + 场景
[acct1] [10:00:05]    >>> 移动 (67,70)→(68,70) [第 1 次]
[acct1] [10:00:06]    遇怪！WarStart + WarAdd(主角100) + WarAdd(敌方×1 ids=[1]) + WarTurn
[acct1] [10:00:07]    普攻 100 → 1（50 伤害）
[acct1] [10:00:07]    等待宠物指令 op168-174（超时默认宠物普攻）
[acct1] [10:00:08]    宠物 250 → 敌 1（48 伤害）
[acct1] [10:00:08]    敌方 1 行动 → 主角承受 20，HP 380/400
[acct1] [10:00:09]    敌方全灭 → WarEnd（战斗结束）
[acct1] [10:00:09]    ↻ 战斗结束场景重同步（影狼原）
[acct1] [10:00:12]    >>> 点击 NPC id=1001
[acct1] [10:00:14]    ★ 传送 → 疾风谷(1029) @(13,113)
[acct1] [10:00:16]    遇怪！WarStart + WarAdd(主角100) + WarAdd(敌方×1 ids=[1]) + WarTurn
[acct1] [10:00:17]    招式 pid=1101 → S2CWarPerform(id=100 vic=1) + WState
[acct1] [10:00:17]    敌方 1 行动 → 主角承受 20，HP 360/400
[acct1] [10:00:18]    敌方全灭 → WarEnd（战斗结束）
[acct1] [10:00:18]    ↻ 战斗结束场景重同步（疾风谷）
[acct1] [10:00:19]    （连接结束）
"""


def _self_test():
    p = _analyse_text(SELFTEST_LOG)
    ok = p['verdict']
    print('自检：%s（会话 %d / 场数 %d / 缺口 %s）'
          % ('PASS' if ok else 'FAIL', p['sessions'], p['best']['counts']['battles'],
             p['best']['gaps'] or '无'))
    return 0 if ok else 1


def _analyse_text(text, min_battles=1, min_pet=1):
    sessions = split_sessions(text)
    if not sessions:
        return {'verdict': False, 'sessions': 0, 'best': None}
    infos = [analyse(s) for s in sessions]
    best_idx = max(range(len(infos)), key=lambda i: score(infos[i]))
    best = infos[best_idx]
    st = best['stages']
    missing = [st[k]['name'] for k, _n, _p, req in STAGES if req and not st[k]['hit']]
    verdict = ((not missing) and not best['gaps']
               and best['counts']['battles'] >= min_battles
               and best['counts']['pet_actions'] >= min_pet)
    return {'verdict': verdict, 'sessions': len(sessions), 'best_idx': best_idx,
            'best': best, 'missing': missing}


def main(argv=None):
    ap = argparse.ArgumentParser(description='真机会话连续流程闸门')
    ap.add_argument('--log', help='m4_srv_*.log 路径（缺省取 logs 下最新一份）')
    ap.add_argument('--min-battles', type=int, default=2,
                    help='同一会话内最少遇怪场数（默认 2，验收「连续」至少两场）')
    ap.add_argument('--min-pet', type=int, default=1,
                    help='同一会话内最少召唤兽指令次数（默认 1；宠物全程倒地的场次可用 0 放宽）')
    ap.add_argument('--md', help='把报告写到该 markdown 文件（默认打印到 stdout）')
    ap.add_argument('--self-test', action='store_true', help='解析器自检（不读真日志）')
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    path = args.log
    if not path:
        cands = glob.glob(os.path.join(DEFAULT_LOG_DIR, 'm4_srv_*.log'))
        if not cands:
            print('找不到 m4_srv_*.log（%s）' % DEFAULT_LOG_DIR)
            return 2
        path = max(cands, key=os.path.getmtime)

    text = io.open(path, encoding='utf-8', errors='replace').read()
    sessions = split_sessions(text)
    if not sessions:
        print('日志里没有 `── 接入:`，无法判定：%s' % path)
        return 2
    infos = [analyse(s) for s in sessions]
    best_idx = max(range(len(infos)), key=lambda i: score(infos[i]))
    report, verdict = render(path, sessions, best_idx, infos[best_idx],
                             args.min_battles, args.min_pet)

    if args.md:
        with io.open(args.md, 'w', encoding='utf-8') as f:
            f.write(report)
        print('已写出：%s（判定 %s）' % (args.md, 'PASS' if verdict else 'FAIL'))
    else:
        print(report)

    # 额外提示：多连接 = 客户端重连过，连续性存疑（不直接判死，但必须写进人工记录）
    if sum(1 for i in infos if i['stages']['hello']['hit']) > 1:
        print('⚠ 本日志出现多次握手成功（%d 次）→ 客户端重连过；'
              '只有在**单次会话**内走完才算「连续」，请在人工记录里注明重连原因。'
              % sum(1 for i in infos if i['stages']['hello']['hit']))
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())
