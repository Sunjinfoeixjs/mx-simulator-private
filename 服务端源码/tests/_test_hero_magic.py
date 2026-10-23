# -*- coding: utf-8 -*-
"""玩家招式战斗语义：逐级消耗 / 威力×效率模型 / 群攻目标 / 边界拒绝。

七组断言：
  ① 逐级消耗         —— 1 级与 6 级的 FP/MP 不同（Py2 整除口径，见生成器文件头）
  ② 消耗真扣         —— 扣的是**本级**的数，且 PropChange 与 hero_res 同源
  ③ 威力×效率模型    —— sim-pow-eff-anchored-v1：1 级≈普攻、随等级按 pow 增长
  ④ 录像校准         —— 11 条主角放招命中（全 50）逐条比对，残差 ≤ 容差
  ⑤ 群攻目标数       —— slv//6+2（1 级 2 个、6 级 3 个），且只扣一次资源
  ⑥ 资源不足 / 非法招式 —— 一分不扣、一滴不掉
  ⑦ 非法目标 / 已死单位 / 群攻全灭 —— 回落目标、不重复结算、WarDel→WarEnd 各一次

⚠ ③④ 断言的模型是**模拟模型**，不是原服公式：语料只有 1 级、且是本服自录的
  （见 m4_server.MAGIC_DAMAGE_MODEL 上方注释）。本测试守的是「模型没有静默漂移」，
  不是「模型等于原服」。
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402
import frames as bf   # noqa: E402


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(bytes(frame))


ok = True


def check(cond, msg):
    global ok
    print(('OK   ' if cond else 'FAIL ') + msg)
    ok = ok and bool(cond)


def make_war(tmp, n=3, tag='w'):
    s = m.M4Server('127.0.0.1', 0, encounter_every=0, enemies=n,
                   logdir=os.path.join(tmp, 'logs_' + tag),
                   savedir=os.path.join(tmp, 'save_' + tag))
    # 测试不启计时器；但必须保留「重开回合窗口」的语义（清 _cmd_consumed），
    # 否则同一实例上连续发第二条指令会被 _accept_turn 判成重复指令直接吞掉。
    def _reopen_window():
        s.await_cmd = True
        s._cmd_consumed = False
    s._arm_cmd_timer = _reopen_window
    s.in_war = True
    s.await_cmd = True
    s._pet_alive = False
    s.hero_hp = m.HERO_HP
    s.enemy_hp = {i + 1: 500 for i in range(n)}
    s.enemy_meta = {i + 1: {'spawn_index': i, 'speed': 1,
                            'shape': m.ENEMY_SHAPE, 'name': m.ENEMY_NAME}
                    for i in range(n)}
    s.enemy_speed_base = 1
    s.enemy_speed = {i + 1: 1 for i in range(n)}
    s._spawn_index = {m.HERO_WAR_ID: 0}
    s._spawn_index.update({i + 1: i + 1 for i in range(n)})
    s.enemy_skill_chance = 0.0
    s._hurt_bs_chance = 0.0
    s._reset_hero_res()
    return s


def ops(conn, since=0):
    """取 since 之后的帧 → [(op, body)]（只做长度自检，不依赖 tap）。

    帧头口径：N = len(opcode + data)（**含** opcode，见 make_s2c）。
    """
    out = []
    for raw in conn.sent[since:]:
        n = raw[0]
        body = raw[1:]
        if n == 0xFF:
            n = struct.unpack('<H', raw[1:3])[0]
            body = raw[3:]
        assert n == len(body), '帧长度不符：头 %d 实 %d' % (n, len(body))
        out.append((body[0], body[1:]))
    return out


def vic_ids_of(body):
    """S2CWarPerform(122)：id(1) pid(2) vic_id(1)×N。"""
    return list(body[3:])


def main():
    tmp = tempfile.mkdtemp(prefix='m4magic_')
    try:
        # ── ① 逐级消耗 ────────────────────────────────────────────────────
        print('── ① 逐级消耗（法力/力道随等级；攻击点/防御点/干扰点是常量）──')
        s = make_war(tmp, tag='lv')
        c1 = m.magic_cost_at(1103, 1)
        c6 = m.magic_cost_at(1103, 6)
        # 1103 fp 表达式：15+(slv-1)*1+(slv/6+2-1)*10；Py2 整除 → 1 级 25、6 级 40
        check(c1['m_FP'] == 25 and c6['m_FP'] == 40,
              '1103 力道消耗 1级 25 → 6级 40（Py2 整除口径）')
        check(c1['m_At'] == 9 and c6['m_At'] == 9,
              '1103 攻击点是常量 9（magicdata 里 at/df/dp 不是表达式）')
        mp1 = m.magic_cost_at(2101, 1)
        mp6 = m.magic_cost_at(2101, 6)
        check(mp1['m_MP'] == 25 and mp6['m_MP'] == 40,
              '2101 法力消耗 1级 25 → 6级 40（MP 系招式，fp=0）')
        check(s._magic_cost(1103) == c1,
              '默认 1 级：服务端取到的消耗 == magic_cost_at(1103,1)')

        s6 = make_war(tmp, tag='lv6')
        s6.magics[1103] = 6
        check(s6._magic_level(1103) == 6 and s6._magic_cost(1103) == c6,
              '招式练到 6 级后，服务端消耗真的变成 6 级那组（不是固定 1 级）')
        s6.magics[1103] = 99
        check(s6._magic_level(1103) == m.magic_max_level(1103),
              '等级超过 magicdata max_lv(%d) 时钳到 max_lv' % m.magic_max_level(1103))

        # ── ② 消耗真扣（6 级用 6 级数字）────────────────────────────────
        print('── ② 消耗真扣 + PropChange 同源 ──')
        s6 = make_war(tmp, tag='spend')
        s6.magics[1103] = 6
        before = dict(s6.hero_res)
        conn = FakeConn()
        s6._hero_perform(conn, 1103, 1)
        check(s6.hero_res['m_FP'] == before['m_FP'] - c6['m_FP']
              and s6.hero_res['m_At'] == before['m_At'] - c6['m_At'],
              '6 级落英缤纷：FP -%d / 攻击点 -%d' % (c6['m_FP'], c6['m_At']))
        props = {}
        for op, body in ops(conn):
            if op == m.OP_PROP_CHANGE and len(body) >= 2 and body[0] in (
                    m.PROP_IDX_FP, m.PROP_IDX_AT):
                props[body[0]] = (struct.unpack('<H', body[1:3])[0]
                                  if body[0] == m.PROP_IDX_FP else body[1])
        check(props.get(m.PROP_IDX_FP) == s6.hero_res['m_FP']
              and props.get(m.PROP_IDX_AT) == s6.hero_res['m_At'],
              'PropChange 下发的 FP/攻击点 == 服务端 hero_res（客户端资源条同源）')

        # ── ③ 威力 × 效率模型 ────────────────────────────────────────────
        print('── ③ 威力(pow) × 效率(powrate) 模型（SIM）──')
        check(m.MAGIC_DAMAGE_MODEL.startswith('sim-'),
              '模型名显式标 sim-（%s）——不会被读成原服公式' % m.MAGIC_DAMAGE_MODEL)
        s = make_war(tmp, tag='model')
        base = s._hero_attack_damage()
        d1101 = s._magic_damage(1101)
        check(d1101 == int(base * 0.94),
              '流水行云 pow50/eff94%%：%d → %d（威力归一 1.0，效率 94%%）'
              % (base, d1101))
        check(s._magic_damage(1301) == base,
              '弯弓射月 pow85/eff100%%：%d（1 级威力归一，不因 pow 绝对值变大）' % base)
        # 1103 威力 90+(slv-1)*12 → 6 级 150，归一 150/90=1.6667
        s6 = make_war(tmp, tag='model6')
        s6.magics[1103] = 6
        want6 = int(base * (150.0 / 90.0) * 1.0)
        check(s6._magic_damage(1103) == want6,
              '落英缤纷 6 级：pow 150/90 → %d（威力真进伤害，不是只看效率）'
              % s6._magic_damage(1103))
        check(s6._magic_damage(1103) > s._magic_damage(1103),
              '同一招式 6 级伤害 > 1 级伤害（升级有实际收益）')

        # ── ④ 录像校准 ───────────────────────────────────────────────────
        print('── ④ 用已有录像校准（11 次放招命中）──')
        hits = m.MAGIC_CALIBRATION_HITS
        total = sum(len(v) for v in hits.values())
        worst = 0
        for pid, observed in sorted(hits.items()):
            got = s._magic_damage(pid)
            for want in observed:
                worst = max(worst, abs(got - want))
        check(total == 11, '校准语料条数 = %d（5 个招式）' % total)
        check(worst <= m.MAGIC_CALIBRATION_TOLERANCE,
              '全部命中残差 ≤ %d 点（实测最大 %d 点，来自 1101 的 94%% 效率）'
              % (m.MAGIC_CALIBRATION_TOLERANCE, worst))
        check(set(hits) <= set(m.TEACH_MAGIC_IDS),
              '校准语料里的招式都在本服已授予列表内（可复现）')

        # ── ⑤ 群攻目标数 ─────────────────────────────────────────────────
        print('── ⑤ 群攻目标数 slv//6+2，且只扣一次资源 ──')
        check((m.magic_target_limit(1103, 1), m.magic_target_limit(1103, 6),
               m.magic_target_limit(1103, 12)) == (2, 3, 4),
              '群攻目标数 1→2 / 6→3 / 12→4 级')
        check(m.magic_target_limit(1101, 12) == 1, '非群攻恒 1 目标')
        s = make_war(tmp, tag='aoe')
        before = dict(s.hero_res)
        c = FakeConn()
        s._hero_perform(c, 1103, 2)
        dmg = s._magic_damage(1103)
        check(s.enemy_hp[2] == 500 - dmg and s.enemy_hp[1] == 500 - dmg
              and s.enemy_hp[3] == 500,
              '1 级群攻：首选目标 + 下一个（2 目标），第 3 只不掉血')
        check(s.hero_res['m_FP'] == before['m_FP'] - m.magic_cost_at(1103, 1)['m_FP'],
              '群攻只扣**一次**消耗（不是按目标数乘）')
        perf = [b for (op, b) in ops(c) if op == m.OP_WAR_PERFORM]
        check(perf and vic_ids_of(perf[0]) == [2, 1],
              'Perform 帧 vic 列表 == 实际挨打的两个目标 %s'
              % (vic_ids_of(perf[0]) if perf else None))

        # ── ⑥ 资源不足 / 未学招式 ────────────────────────────────────────
        print('── ⑥ 资源不足 / 未学招式：拒绝且不产生任何结算 ──')
        s3 = make_war(tmp, tag='reject')
        hp0, res0 = dict(s3.enemy_hp), dict(s3.hero_res)
        s3._hero_perform(FakeConn(), 9999, 1)
        check(s3.enemy_hp == hp0 and s3.hero_res == res0,
              '未学习的招式被拒，敌方血量与资源不变')
        s3.hero_res['m_FP'] = m.magic_cost_at(1103, 1)['m_FP'] - 1
        hp1, res1 = dict(s3.enemy_hp), dict(s3.hero_res)
        c = FakeConn()
        s3._hero_perform(c, 1103, 1)
        check(s3.enemy_hp == hp1 and s3.hero_res == res1,
              'FP 差 1 点也拒绝，敌方血量与资源不变')
        check(not any(op == m.OP_WAR_PERFORM for (op, _b) in ops(c)),
              '拒绝时不发 Perform(122)（客户端不会演招式）')
        check(any(op == m.OP_WAR_TURN for (op, _b) in ops(c)),
              '拒绝时回 WarTurn(114) 交还操作权（菜单能重新弹出）')
        # 攻击点不足：1103 需要 9 点攻击点
        s4 = make_war(tmp, tag='at')
        s4.hero_res['m_At'] = 8
        hp2, res2 = dict(s4.enemy_hp), dict(s4.hero_res)
        s4._hero_perform(FakeConn(), 1103, 1)
        check(s4.enemy_hp == hp2 and s4.hero_res == res2,
              '攻击点 8/9 不足 → 拒绝，血量与资源不变')

        # ── ⑦ 非法目标 / 已死单位 / 群攻全灭 ────────────────────────────
        print('── ⑦ 非法目标 / 已死单位 / 群攻全灭 ──')
        s = make_war(tmp, tag='target')
        c = FakeConn()
        s._hero_perform(c, 1101, 99)     # 越界目标
        perf = [b for (op, b) in ops(c) if op == m.OP_WAR_PERFORM]
        check(perf and vic_ids_of(perf[0]) == [1],
              '非法目标 id=99 → 回落首个存活敌方，且 Perform 帧里就是 1')
        # 已死目标：先打死 1 号，再指定它
        s.enemy_hp[1] = 1
        s._hero_attack(FakeConn(), 1)
        check(1 not in s.enemy_hp, '1 号已从 enemy_hp 清除')
        c2 = FakeConn()
        s._hero_perform(c2, 1101, 1)
        perf = [b for (op, b) in ops(c2) if op == m.OP_WAR_PERFORM]
        check(perf and vic_ids_of(perf[0]) == [2],
              '指定已死单位 → 回落到存活敌方 2')
        # 群攻里同一发不重复结算：1 号残血 1 点，群攻同时打 1/2
        s = make_war(tmp, tag='aoe_kill')
        s.enemy_hp[1] = 1
        s.enemy_hp[2] = 500
        c = FakeConn()
        s._hero_perform(c, 1103, 1)
        check(s.enemy_hp[2] == 500 - s._magic_damage(1103),
              '群攻中途击杀 1 号后，2 号照常吃满伤害')
        ws = [b for (op, b) in ops(c) if op == m.OP_WAR_WSTATE and b[0] == 1]
        check(len(ws) == 1, '1 号只收到一条 WState（不会重复播死亡）')
        # 群攻全灭
        s = make_war(tmp, n=2, tag='aoe_win')
        s.enemy_hp = {1: 1, 2: 1}
        c = FakeConn()
        s._hero_perform(c, 1103, 1)
        seq = [op for (op, _b) in ops(c)]
        check(seq.count(m.OP_WAR_PERFORM) == 1, '全灭一发：只有一条 Perform')
        check(seq.count(m.OP_WAR_END) == 1, 'WarEnd(113) 恰好一次')
        check(seq.count(m.OP_WAR_DEL) == 2, 'WarDel(116) 两只各一次')
        check(seq.index(m.OP_WAR_DEL) < seq.index(m.OP_WAR_END),
              'WarDel 全部在 WarEnd 之前（客户端先移除单位再收场）')
        check(not s.in_war, '战斗结束：in_war=False')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
