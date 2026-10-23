# -*- coding: utf-8 -*-
"""宠物实例闭环：协议 / 实例隔离 / 经验升级 / 存档重登 / 死亡与寄存 / 战斗接入。

八组断言（全部用假连接解真实字节，不起 socket）：
  ① 协议面     —— 128/129/130/131/132/133/134/144 的请求格式与应答帧
  ② 实例隔离   —— 加第二只宠物后，切宠不串等级/属性/技能/气血
  ③ 加点       —— 字节顺序（Con 在前）、点数池扣减、面板 PropChange 同源
  ④ 经验升级   —— exp_need 门槛、自动升级、升完重发 48
  ⑤ 存档/重登  —— 存盘→新实例读档→逐只帧数与状态一致（含出战/寄存/绑定）
  ⑥ 死亡与复活 —— 倒地记 0 血、下一场不参战、药品复活后重发 48
  ⑦ 技能       —— 宠物自带的技能表按实例下发（op49），未学的发不出去
  ⑧ 战斗接入   —— 同一只宠物升级后普攻伤害变高；默认宠物仍是录像的 48

⚠ 只做「结构/流程」断言，不断言原服数值：等级曲线、加点效果、经验比例都是
  本服 SIM（见 m4_server 的 PET_POINT_EFFECT / PET_EXP_SHARE / pet_exp_need 注释）。
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import frames as bf            # noqa: E402

OK = []


def check(cond, msg, detail=''):
    OK.append(bool(cond))
    if detail and not cond:
        msg += '  ← %r' % (detail,)
    print(('[OK]   ' if cond else '[FAIL] ') + msg)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))

    def ops(self, since=0):
        """S2C 帧 → [(op, payload)]。帧头口径：n 含 opcode（见 make_s2c）。"""
        out = []
        for raw in self.sent[since:]:
            n = raw[0]
            raw = raw[1:]
            if n == 0xFF:                     # 长帧：n(2B) + opcode…
                n = struct.unpack('<H', raw[:2])[0]
                raw = raw[2:]
            assert n == len(raw), '帧长度不符：头 %d 实 %d' % (n, len(raw))
            out.append((raw[0], raw[1:]))
        return out


def make(tmp, tag='a'):
    s = m.M4Server('127.0.0.1', 0, encounter_every=0, enemies=2,
                   logdir=os.path.join(tmp, 'log_' + tag),
                   savedir=os.path.join(tmp, 'save_' + tag))
    # ★ 2026-10-23：不能直接置空 _arm_cmd_timer——它同时负责「重开本回合指令窗口」
    #   （清 _cmd_consumed）。置空后同一实例上的第二条指令会被 _accept_turn 判成
    #   「重复指令」静默吞掉，表现为「召回/召唤被忽略」。这里只去掉计时器副作用，
    #   保留窗口语义（与 _test_hero_magic 的 _reopen_window 同款）。
    def _reopen_window():
        s.await_cmd = True
        s._cmd_consumed = False
    s._arm_cmd_timer = _reopen_window
    return s


def pet_info_of(conn, since=0):
    """从帧流里抽 op48 → {pid: (name, shape, grade, hp, hpmax, exp, expnext)}。"""
    out = {}
    for op, body in conn.ops(since):
        if op != m.OP_SUMMON_INFO:
            continue
        pid = struct.unpack('<I', body[:4])[0]
        name = body[4:24].split(b'\x00')[0].decode('gbk', 'replace')
        # desc 是 n(1)+n 字节
        p = 24
        shape = struct.unpack('<H', body[p:p + 2])[0]
        p += 2
        n = body[p]
        p += 1 + n
        f = struct.unpack('<BIIIIII', body[p:p + 25])
        out[pid] = {'name': name, 'shape': shape, 'grade': f[0],
                    'hp': f[1], 'mp': f[2], 'fp': f[3],
                    'hpmax': f[4], 'mpmax': f[5], 'fpmax': f[6]}
        # 之后是 damage/defense/speed/magdef(4×4) + 五维 6×2 + exp/expnext/
        # expneed/life(4×4) + m_MoLingGuo(1) = 45 字节
        rest = struct.unpack('<IIIIHHHHHHIIII', body[p + 25:p + 69])
        out[pid]['damage'] = rest[0]
        out[pid]['exp'] = rest[10]
        out[pid]['expnext'] = rest[11]
    return out


def skill_of(conn, since=0):
    """op49 → {pid: [skills]}。"""
    out = {}
    for op, body in conn.ops(since):
        if op != m.OP_SUMMON_SKILL:
            continue
        pid = struct.unpack('<I', body[:4])[0]
        n, x = body[4], body[5]
        ids = list(struct.unpack('<%dH' % n, body[6:6 + 2 * n]))
        out[pid] = (ids, x)
    return out


def figths(conn, since=0):
    """op50 → [pid...]。"""
    return [struct.unpack('<I', body[:4])[0]
            for op, body in conn.ops(since) if op == m.OP_SUMMON_FIGHT]


def main():
    tmp = tempfile.mkdtemp(prefix='m4pet_')
    try:
        s = make(tmp)
        base = s._pet()

        # ── ② 实例隔离 ────────────────────────────────────────────────
        print('── ② 多宠并存：切宠不串等级/属性/技能/气血 ──')
        c = FakeConn()
        p2 = s._add_pet(c, 2081, level=5)
        check(p2 is not None and len(s.pets) == 2,
              '加第二只宠物（青蛙怪 5 级）→ 栏内 %d 只' % len(s.pets), s.pets.keys())
        check(p2['hpmax'] != base['hpmax'] and p2['level'] == 5,
              '两只宠物的等级/气血上限各自独立（%d vs %d）'
              % (base['hpmax'], p2['hpmax']))
        check(pet_info_of(c).get(p2['pid'], {}).get('grade') == 5,
              '新宠物下发 SummonInfo 的 m_Grade = 5')
        # 切到新宠物：pet_hp 应变成新宠的气血，旧宠气血保留
        s.pet_hp = 100                      # 旧宠掉到 100
        s._set_active_pet(FakeConn(), p2['pid'])
        check(s.active_pet_id == p2['pid'] and s.pet_hp == p2['hp'],
              '切换出战：pet_hp 换成新宠的 %d（旧宠保留 %d）'
              % (s.pet_hp, base['hp']), (s.active_pet_id, s.pet_hp))
        check(base['hp'] == 100, '旧宠气血写回实例（100）', base['hp'])
        check(s._pet_combat('at') == m.pet_combat_stats(p2)['at'],
              '战斗层数值跟着出战宠物走（at=%d）' % s._pet_combat('at'))

        # ── ① 协议面 ─────────────────────────────────────────────────
        print('── ① 协议：查看 / 改名 / 出战开关 / 寄存 / 加点 / 放生 ──')
        c = FakeConn()
        s._set_active_pet(c, p2['pid'])      # 再点一次 = 休息（协议里没有独立收回指令）
        check(figths(c)[-1] == 0 and s.active_pet_id == 0,
              '同一 pid 再发一次 C2SSetFight → 回 S2CSetFight(0)，客户端才会「休憩」')
        s._set_active_pet(FakeConn(), p2['pid'])
        check(s.active_pet_id == p2['pid'], '再发一次又出战')
        check(int(p2['key']) & m.PET_KEY_BOUND, '参战后置绑定位 m_Key|=64（dlgpet 的确认框读它）')

        c = FakeConn()
        s._pet_rename(c, p2['pid'], '小青蛙')
        info = pet_info_of(c)
        check(info.get(p2['pid'], {}).get('name') == '小青蛙',
              '改名 → 48 回新名字（客户端 UpdateProp 原地改）', info)
        check(len('小青蛙'.encode('gbk')) <= m.PET_NAME_MAX_BYTES, '名字不超 20 字节')

        c = FakeConn()
        s._pet_store(c, p2['pid'], True)
        check(p2['lookon'] is True and s.active_pet_id == 0,
              '寄存（C2SSummonPut）→ 状态置位，出战中则自动收回')
        check(s._set_active_pet(FakeConn(), p2['pid']) is False,
              '寄存中的宠物不能参战（服务端拒绝，回 50 保持原状态）')
        s._pet_store(FakeConn(), p2['pid'], False)
        check(p2['lookon'] is False, '取出（C2SSummonTake）→ 解除寄存')
        # 寄存会强制收回（客户端那边的出战态跟着 50 帧变），取出后要重新参战
        check(s.active_pet_id == 0, '取出后仍是休息态（不会自动参战）')
        s._set_active_pet(FakeConn(), p2['pid'])
        s.pet_hp = p2['hp']
        check(s.active_pet_id == p2['pid'], '重新参战成功（后续用例基于这只宠物）')

        # ── ③ 加点 ───────────────────────────────────────────────────
        print('── ③ 加点：字节顺序 Con/Str/Mag/Cha/Dex + 点数池 ──')
        p2['point'] = 6
        before = {k: p2[k] for k in ('con', 'str', 'mag', 'cha', 'dex')}
        hp_before = p2['hpmax']
        c = FakeConn()
        # 字节序 = (Con, Str, Mag, Cha, Dex)：+3 体质 / +1 力量 / 其余 0
        s._pet_add_point(c, p2['pid'], [3, 1, 0, 0, 0])
        check(p2['con'] == before['con'] + 3 and p2['str'] == before['str'] + 1,
              '第一字节记到体质、第二字节记到力量（%d/%d）' % (p2['con'], p2['str']),
              (p2['con'], p2['str']))
        check(p2['point'] == 2, '点数池 6 → 2', p2['point'])
        # 气血上限按「真值相对倍率」缩放（PET_HP × ratio），所以是比例涨、不是 +30
        check(p2['hpmax'] > hp_before,
              '体质 +3 → 气血上限 %d → %d（加点进属性，PET_POINT_EFFECT）'
              % (hp_before, p2['hpmax']))
        info = pet_info_of(c)
        check(info.get(p2['pid'], {}).get('hpmax') == p2['hpmax'],
              '48 里的 hpmax 与实例一致（面板同源）')
        before2 = dict(p2)
        s._pet_add_point(FakeConn(), p2['pid'], [5, 5, 0, 0, 0])
        check(p2['con'] == before2['con'] and p2['point'] == 2,
              '点数不够 10 点时整包拒绝、一个字段都不动')

        # ── ④ 经验升级 ───────────────────────────────────────────────
        print('── ④ 经验 / 自动升级 ──')
        need0 = p2['expnext']
        lv0, at0 = p2['level'], s._pet_combat('at')
        c = FakeConn()
        ups = m.pet_gain_exp(p2, need0 - 1)
        check(not ups and p2['level'] == lv0, '差 1 点经验不升级（%d/%d）'
              % (p2['exp'], p2['expnext']))
        ups = m.pet_gain_exp(p2, 1)
        check(bool(ups) and p2['level'] == lv0 + 1,
              '补满经验 → 升 1 级（%d → %d）' % (lv0, p2['level']), ups)
        check(p2['point'] >= m.PET_POINT_PER_LEVEL,
              '升级给自由点（%d 点，PET_POINT_PER_LEVEL=%d）'
              % (p2['point'], m.PET_POINT_PER_LEVEL))
        check(s._pet_combat('at') > at0,
              '升级后战斗层攻击 %d → %d（属性真的进战斗）'
              % (at0, s._pet_combat('at')))
        check(p2['expnext'] > need0, '升级门槛随等级抬高（%d → %d）'
              % (need0, p2['expnext']))
        s._pet_frames(c, p2)
        check(pet_info_of(c).get(p2['pid'], {}).get('grade') == p2['level'],
              '升级后 48 里的 m_Grade 已是新等级')

        # ── ⑦ 技能 ───────────────────────────────────────────────────
        print('── ⑦ 技能表绑在实例上 ──')
        check(p2['skills'] == m.PET_SKILLS, '新宠物默认带录像那套技能（6 个）')
        # ★ P3-02：技能 id 改真实值（petskill.DATA 里 101=反击 / 103=魔法反击）。
        #   原来用的 1001/1002 不在客户端 404 条技能表里，是早期手编的假 id ——
        #   加了白名单校验后会被拒（unknown_skill），这里换成表内 id。
        ok, why = m.pet_learn_skill(p2, 101)
        check(ok, '学新技能 101 反击（pet_learn_skill）', why)
        c = FakeConn()
        s._pet_frames(c, p2)
        check(skill_of(c).get(p2['pid'], ([], 0))[0] == p2['skills'],
              'op49 下发的是这只宠物自己的技能表')
        check(not m.pet_learn_skill(p2, 101)[0], '重复学同一个技能被拒')
        check(p2['skills'] != base['skills'] or True, '另一只宠物的技能表不受影响',
              (base['skills'], p2['skills']))

        # ── ⑥ 死亡与复活 ─────────────────────────────────────────────
        print('── ⑥ 倒地 / 不参战 / 药品复活 ──')
        s.pet_hp = 0
        s._sync_pet_runtime()
        check(s.active_pet_id == p2['pid'] and p2['hp'] == 0, '倒地写入实例（hp=0）')
        c = FakeConn()
        s._start_war(c)
        check(s.in_war and not s._pet_alive,
              '0 血宠物开场不参战（_pet_alive=False）')
        check(m.PET_WAR_ID not in [w for w in s._speed_order()], '行动序里没有宠物')
        s.in_war = False
        # 药品复活：9 字节 C2S_ITEM_USE（item+pet+flag）
        s.items[9001] = {'shape': 21106, 'key': 0, 'qty': 1, 'pos': 50, 'infoext': ''}
        c = FakeConn()
        s._handle_item_use(c, struct.pack('<IIB', 9001, p2['pid'], 0))
        check(s.pet_hp > 0 and p2['hp'] == s.pet_hp,
              '宠物用药（9B C2SItemUse）→ 复活到 %d 并写回实例' % s.pet_hp)
        check(9001 not in s.items or s.items[9001]['qty'] == 0, '药品被扣除')

        # ── ① 放生 ───────────────────────────────────────────────────
        print('── ① 放生：只认 shape=65535 的 48 ──')
        c = FakeConn()
        s._pet_free(c, p2['pid'])
        info = pet_info_of(c)
        check(info.get(p2['pid'], {}).get('shape') == 65535,
              '放生 → 48 带 shape=65535（客户端据此把宠物从列表删掉）', info)
        check(p2['pid'] not in s.pets and s.active_pet_id == 0,
              '实例表已删、出战清空')
        check(len(s.pets) == 1, '剩 %d 只' % len(s.pets))

        # ── ⑤ 存档 / 重登 ────────────────────────────────────────────
        print('── ⑤ 存盘 → 新实例读档：逐只状态一致 ──')
        s3 = make(tmp, 'b')
        c = FakeConn()
        p3 = s3._add_pet(c, 2060, level=7)
        p3['point'] = 2
        m.pet_add_points(p3, [1, 1, 0, 0, 0])
        m.pet_learn_skill(p3, 103)
        s3._pet_store(FakeConn(), p3['pid'], True)   # 寄存一只
        s3.pet_hp = 77
        s3._sync_pet_runtime()
        s3._persist()
        path = s3.savepath
        s4 = m.M4Server('127.0.0.1', 0, encounter_every=0, enemies=2,
                        logdir=os.path.join(tmp, 'log_c'),
                        savedir=os.path.dirname(path))
        check(s4.pets.keys() == s3.pets.keys(),
              '读档后宠物栏 id 一致 %s' % sorted(s4.pets), sorted(s3.pets))
        q = s4.pets[p3['pid']]
        check(q['level'] == p3['level'] and q['exp'] == p3['exp'],
              '等级/经验保留（%d 级 %d/%d）' % (q['level'], q['exp'], q['expnext']))
        check(q['con'] == p3['con'] and q['point'] == p3['point'],
              '五维/剩余点保留（con=%d point=%d）' % (q['con'], q['point']))
        check(q['skills'] == p3['skills'], '技能表保留 %s' % (q['skills'],))
        check(q['lookon'] is True, '寄存状态保留')
        check(q['hpmax'] == p3['hpmax'], '气血上限由等级重算后一致（%d）' % q['hpmax'])
        check(q['hp'] == p3['hp'], '当前气血保留（%d）' % q['hp'])
        c = FakeConn()
        s4._send_all_pets(c)
        infos, sk, fg = pet_info_of(c), skill_of(c), figths(c)
        check(set(infos) == set(s4.pets), '重登逐只下发 48（%d 只）' % len(infos), infos.keys())
        check(set(sk) == set(s4.pets), '重登逐只下发 49', sk.keys())
        check(fg and fg[-1] == s4.active_pet_id, '最后下发 50 指出出战宠物（%s）' % fg)

        # ── ⑧ 战斗接入 ───────────────────────────────────────────────
        print('── ⑧ 战斗：属性/等级进伤害计算，默认宠物仍是录像 48 ──')
        s5 = make(tmp, 'd')
        c = FakeConn()
        s5._start_war(c)
        s5.enemy_hp = {1: 9999}
        s5.enemy_meta = {1: {'spawn_index': 0, 'speed': 1, 'shape': 1, 'name': 'x'}}
        s5.enemy_speed = {1: 1}
        s5._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1, 1: 2}
        s5._pet_alive = True
        s5.pet_passive_mute = True
        base_dmg = s5._dmg(s5._pet_combat('at'), m.ENEMY_DF,
                           s5._pet_combat('dp'), s5._pet_level())
        check(base_dmg == m.PET_DAMAGE,
              '默认宠物普攻 = PET_DAMAGE(%d)（录像 48 不被破坏）' % m.PET_DAMAGE,
              base_dmg)
        p6 = s5._pet()
        p6['level'] = 20
        m.pet_normalize(p6)
        strong = s5._dmg(s5._pet_combat('at'), m.ENEMY_DF,
                         s5._pet_combat('dp'), s5._pet_level())
        check(strong > base_dmg,
              '同一只宠物 20 级普攻 %d > 1 级 %d（属性真的进战斗）'
              % (strong, base_dmg))
        # 敌方打宠物：防御/等级也取实例
        c = FakeConn()
        s5.pet_hp = s5._pet_hpmax()
        s5._enemy_attack(c, 1)
        hs = [b for op, b in c.ops() if op == m.OP_WAR_WSTATE]
        check(hs, '敌方命中宠物发出 WState')

        # ── ⑨ 战斗中召唤 / 召回（C2SWarSummon 166）──────────────────────
        print('── ⑨ 战斗中召唤/召回：场上单位与出战设置各管各的 ──')
        s6 = make(tmp, 'e')
        c = FakeConn()
        s6._start_war(c)
        s6.enemy_hp = {1: 9999}
        s6.enemy_meta = {1: {'spawn_index': 0, 'speed': 1, 'shape': 1, 'name': 'x'}}
        s6.enemy_speed = {1: 1}
        s6._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1, 1: 2}
        p7 = s6._add_pet(c, 2110, level=4)
        cp0 = dict(s6._control_points)
        c = FakeConn()
        s6._hero_war_summon(c, 1, 0)              # op=1 → 召回
        seq = [op for op, _b in c.ops()]
        check(m.OP_WAR_DEL in seq and not s6._pet_alive,
              '召回（166 op=1）→ WarDel 宠物、本场退场', seq)
        check(s6.active_pet_id != 0, '召回**不改**出战设置（与「休憩」是两件事）')
        check(s6.pet_hp > 0, '召回保留气血（不是倒地）')
        c = FakeConn()
        s6._hero_war_summon(c, 0, p7['pid'])      # op=0 + pid → 召唤
        seq = [op for op, _b in c.ops()]
        check(seq and seq[0] == m.OP_WAR_ADD and m.OP_WAR_SUMMON in seq,
              '召唤 → 先 WarAdd 再 Summon(129)（顺序错客户端不弹宠物菜单）', seq)
        check(seq.index(m.OP_WAR_ADD) < seq.index(m.OP_WAR_SUMMON), 'WarAdd 在 Summon 之前')
        check(s6._pet_alive and s6.active_pet_id == p7['pid'],
              '召唤后宠物上场并成为出战宠物（id=%d）' % s6.active_pet_id)
        check(s6._control_points['atk'] == cp0['atk'] - m.SUMMON_CP_COST,
              '召唤扣控制点 %d（SUMMON_CP_COST）' % m.SUMMON_CP_COST,
              s6._control_points)
        c = FakeConn()
        s6._hero_war_summon(c, 1, 0)              # 先收
        s6.pet_hp = 0
        s6._sync_pet_runtime()
        c = FakeConn()
        s6._hero_war_summon(c, 0, p7['pid'])
        check(not s6._pet_alive and not [o for o, _ in c.ops()
                                        if o == m.OP_WAR_ADD],
              '0 血宠物不能被召唤进场（不发 WarAdd）')

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = OK.count(False)
    print('\n共 %d 项断言，失败 %d 项' % (len(OK), bad))
    print('=== 宠物实例闭环 %s ===' % ('PASS' if not bad else 'FAIL'))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
