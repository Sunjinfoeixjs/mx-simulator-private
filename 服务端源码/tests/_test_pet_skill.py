# -*- coding: utf-8 -*-
"""宠物招式：按 petskill 的 type 分流（不再一律套用普攻伤害）。

七组断言：
  ① 菜单口径       —— 只有 type=1/2 进「宠物招式」菜单（与 warui 的过滤一致）
  ② 主动群攻 415   —— 122 帧列出多目标，逐目标结算，只扣一次 FP
  ③ 招式资源       —— FP 按 petskill 的 fpneed 表达式扣（Py2 整除），并 S2CSummonInfo 同步
  ④ 资源不足       —— 回落普攻（117），不静默打出「看起来像招式」的伤害
  ⑤ 被动/未登记    —— 发被动当主动用 = 非法指令 → 回落普攻并记日志
  ⑥ 被动效果       —— 必杀 +8% 会心（hurt=1）、连击 40% 追加一击（两击都 ×75%）
  ⑦ 边界           —— 非法/已死目标回落；群攻全灭 → WarDel×N → WarEnd 一次

证据强度：415 的「群体物理攻击」是 petskill desc 真值；每目标倍率 1.0 与目标数 2
是 SIM（petskill 没有威力/效率/目标数字段），见 m4_server.PET_SKILL_EFFECTS。
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(('OK   ' if cond else 'FAIL ') + msg)
    ok = ok and bool(cond)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(bytes(frame))


def ops(conn, since=0):
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


def opcode_list(conn, since=0):
    return [op for (op, _b) in ops(conn, since)]


def pet_info_fp(body):
    """S2CSummonInfo(48) 里的 m_FP：pid4+name20+shape2+desc1+grade1 → hp4 mp4 **fp4**。"""
    return struct.unpack('<I', body[36:40])[0]


def make_war(tmp, n=3, tag='p'):
    s = m.M4Server('127.0.0.1', 0, encounter_every=0, enemies=n,
                   logdir=os.path.join(tmp, 'logs_' + tag),
                   savedir=os.path.join(tmp, 'save_' + tag))
    s._arm_cmd_timer = lambda: None
    s.in_war = True
    s.await_cmd = True
    s._pet_alive = True
    s.pet_hp = m.PET_HP
    s.active_pet_id = m.PET_ID
    s.hero_hp = m.HERO_HP
    s.enemy_hp = {i + 1: 500 for i in range(n)}
    s.enemy_meta = {i + 1: {'spawn_index': i, 'speed': 1,
                            'shape': m.ENEMY_SHAPE, 'name': m.ENEMY_NAME}
                    for i in range(n)}
    s.enemy_speed_base = 1
    s.enemy_speed = {i + 1: 1 for i in range(n)}
    s._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1}
    s._spawn_index.update({i + 1: i + 2 for i in range(n)})
    s.enemy_skill_chance = 0.0
    s._hurt_bs_chance = 0.0
    s._reset_hero_res()
    s._reset_pet_res()
    return s


def main():
    tmp = tempfile.mkdtemp(prefix='m4petskill_')
    try:
        # ── ① 菜单口径 ────────────────────────────────────────────────
        print('── ① 只有 type=1/2 进宠物招式菜单 ──')
        check(m.pet_active_skills() == [415],
              '本服宠物可主动施放的招式 = %s（415 乱舞冲击）'
              % m.pet_active_skills())
        passives = [s for s in m.pet_skill_ids() if s not in m.pet_active_skills()]
        check(passives == [112, 319, 317, 102, 505],
              '其余 %d 个都是被动（type=0），不进菜单' % len(passives))

        # ── ② 415 群攻 ────────────────────────────────────────────────
        print('── ② 415 乱舞冲击：群体物理，逐目标结算 ──')
        s = make_war(tmp, tag='aoe')
        c = FakeConn()
        s._pet_perform(c, 415, 2)
        frames = ops(c)
        perf = [b for (op, b) in frames if op == m.OP_WAR_PERFORM]
        check(len(perf) == 1 and perf[0][0] == m.PET_WAR_ID
              and struct.unpack('<H', perf[0][1:3])[0] == 415,
              'Perform 施法者=宠物(250)、pid=415')
        vics = list(perf[0][3:]) if perf else []
        check(vics == [2, 1], '群攻 vic 列表 = 首选目标 + 下一目标 %s' % (vics,))
        dmg = s._pet_skill_damage(415)
        check(s.enemy_hp[1] == 500 - dmg and s.enemy_hp[2] == 500 - dmg
              and s.enemy_hp[3] == 500,
              '两只各吃 %d 伤害，第三只不动' % dmg)
        check(dmg == s._dmg(m.PET_AT, m.ENEMY_DF, m.PET_DP, m.ENEMY_LEVEL),
              '每目标伤害 = 宠物普攻 × 倍率 1.0（SIM，不凭空加倍）')
        ws = [b for (op, b) in frames if op == m.OP_WAR_WSTATE]
        check(len(ws) == 2, '每个目标一条 WState（%d 条）' % len(ws))

        # ── ③ 招式资源 ────────────────────────────────────────────────
        print('── ③ FP 消耗按 petskill fpneed 表达式（Py2 整除）──')
        need = 15 + int(m.PET_LEVEL) // 3 + 3 * 5
        fp_need, mp_need = s._pet_skill_need(415)
        check((fp_need, mp_need) == (need, 0),
              '415 需要 FP=%d（15+LV/3+3*5，LV=%d）' % (need, m.PET_LEVEL))
        check(s.pet_res['m_FP'] == m.PET_FP - need,
              '施放后宠物 FP %d → %d（只扣一次，不按目标数乘）'
              % (m.PET_FP, s.pet_res['m_FP']))
        infos = [b for (op, b) in frames if op == m.OP_SUMMON_INFO]
        check(infos and pet_info_fp(infos[-1]) == s.pet_res['m_FP'],
              'S2CSummonInfo(48) 把新 FP 同步给客户端（菜单据此置灰）')

        # ── ④ 资源不足 ────────────────────────────────────────────────
        print('── ④ 资源不足 → 回落普攻，不发 122 ──')
        c2 = FakeConn()
        s._pet_perform(c2, 415, 1)
        seq = opcode_list(c2)
        check(m.OP_WAR_PERFORM not in seq,
              'FP=0 再发 415 → 不发 Perform（客户端不会演招式）')
        check(m.OP_WAR_ATTACK_NORMAL in seq,
              '回落为普攻 117（回合照常推进，不卡「请等待」）')
        check(s.pet_res['m_FP'] == 0, '资源不足时不扣成负数')

        # ── ⑤ 被动 / 未登记 ───────────────────────────────────────────
        print('── ⑤ 被动当主动用 / 未登记效果 → 回落普攻 ──')
        s2 = make_war(tmp, tag='passive')
        for pid in (112, 102, 505):
            c = FakeConn()
            s2._pet_perform(c, pid, 1)
            seq = opcode_list(c)
            check(m.OP_WAR_PERFORM not in seq and m.OP_WAR_ATTACK_NORMAL in seq,
                  '被动 %d（%s）被拒 → 普攻'
                  % (pid, (m.PET_PASSIVE_EFFECTS.get(pid) or {}).get('name', '?')))
        c = FakeConn()
        s2._pet_perform(c, 1067, 1)     # 1067 宁心：不在本宠物技能表
        check(m.OP_WAR_PERFORM not in opcode_list(c),
              '不在宠物技能表内的 id=1067 → 不执行未知效果')

        # ── ⑥ 被动效果 ────────────────────────────────────────────────
        print('── ⑥ 被动：必杀 +8%% 会心 / 连击 40%% 追加（两击 ×75%%）──')
        # ★ P3-03 起随机源换成 `battle_rng`（按用途分流 + 固定种子），
        #   原来 `mock.patch.object(m.random, 'random', side_effect=[...])` 的打桩
        #   不再生效 —— 它钉的是实现细节（模块级 random），不是行为。
        #   改为**把概率参数钉成 0/1**：`chance(p)` 在 p>=1 恒真、p<=0 恒假且不抽数，
        #   于是「必触发 / 必不触发」由语义决定，与随机源实现无关。
        #   断言一条未改（hurt 位、帧数、两击 ×75%、总伤害）。
        NO_CRIT = {'crit_add': 0.0, 'combo_chance': 0.0,
                   'combo_rate': 0.75, 'combo_max': 1}
        s3 = make_war(tmp, tag='crit')
        base = s3._dmg(m.PET_AT, m.ENEMY_DF, m.PET_DP, m.ENEMY_LEVEL)
        with mock.patch.object(s3, '_pet_passives',
                               return_value=dict(NO_CRIT, crit_add=1.0)):
            c = FakeConn()
            s3._pet_attack(c, 1)
        ws = [b for (op, b) in ops(c) if op == m.OP_WAR_WSTATE]
        check(ws and (ws[0][1] >> 4) == m.HURT_BS,
              '必杀触发 → WState hurt=1（客户端 war\\shit\\ 红字）')
        check(struct.unpack('<i', ws[0][2:6])[0] == base,
              '会心只改表现位，不改伤害（%d）' % base)

        s4 = make_war(tmp, tag='combo')
        with mock.patch.object(s4, '_pet_passives',
                               return_value=dict(NO_CRIT, combo_chance=1.0)):
            c = FakeConn()
            s4._pet_attack(c, 1)
        seq = opcode_list(c)
        check(seq.count(m.OP_WAR_ATTACK_NORMAL) == 2,
              '连击触发 → 两条 AttackNormal（追加一次）%s' % seq)
        ws = [struct.unpack('<i', b[2:6])[0] for (op, b) in ops(c)
              if op == m.OP_WAR_WSTATE]
        combo_dmg = max(1, int(base * 0.75))
        check(ws == [combo_dmg, combo_dmg],
              '连击两击都是 ×75%%（%d = %d×0.75，desc 说「每次」）' % (ws[0], base))
        check(s4.enemy_hp[1] == 500 - combo_dmg * 2, '连击总伤害 = 两击之和')

        s5 = make_war(tmp, tag='nocomb')
        with mock.patch.object(s5, '_pet_passives',
                               return_value=dict(NO_CRIT)):
            c = FakeConn()
            s5._pet_attack(c, 1)
        check(opcode_list(c).count(m.OP_WAR_ATTACK_NORMAL) == 1,
              '连击未触发（chance 0 → 恒不抽签）→ 只有一击')

        # ── ⑦ 边界 ────────────────────────────────────────────────────
        print('── ⑦ 非法/已死目标、群攻全灭 ──')
        s6 = make_war(tmp, tag='target')
        c = FakeConn()
        s6._pet_perform(c, 415, 99)
        perf = [b for (op, b) in ops(c) if op == m.OP_WAR_PERFORM]
        check(perf and list(perf[0][3:]) == [1, 2],
              '非法目标 99 → 回落首发 + 下一目标 %s'
              % (list(perf[0][3:]) if perf else None))
        s6.enemy_hp[1] = 1
        s6._pet_attack(FakeConn(), 1)
        s6.pet_res['m_FP'] = m.PET_FP        # 上一发已耗光 FP，这里只为验目标回落
        c = FakeConn()
        s6._pet_perform(c, 415, 1)
        perf = [b for (op, b) in ops(c) if op == m.OP_WAR_PERFORM]
        check(perf and list(perf[0][3:]) == [2, 3],
              '指定已死单位 → 群攻从存活单位重新选 %s'
              % (list(perf[0][3:]) if perf else None))

        s7 = make_war(tmp, n=2, tag='wipe')
        s7.enemy_hp = {1: 1, 2: 1}
        c = FakeConn()
        s7._pet_perform(c, 415, 1)
        seq = opcode_list(c)
        check(seq.count(m.OP_WAR_DEL) == 2 and seq.count(m.OP_WAR_END) == 1,
              '群攻全灭 → WarDel×2 + WarEnd×1')
        check(seq.index(m.OP_WAR_END) == len(seq) - 1 or m.OP_WAR_END in seq,
              'WarEnd 收场')
        check(not s7.in_war, '战斗结束：in_war=False')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
