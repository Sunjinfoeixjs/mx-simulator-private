# -*- coding: utf-8 -*-
"""战斗全链路验证：字段定义 → 收发包 → 整场演出（不需要真实端口）。

覆盖用户可见的七段：
  ① 遇怪摆场（112 → 115×3 → 129 → 114）
  ② 攻击（160 → 117/123/119）
  ③ 招式（161 → 122/123，Perform 族**没有** 119）
  ④ 召唤兽行动（169 无 id / 168 带 id → 122 或 117）
  ⑤ 敌方回合（117 或 122 → 123 → 125 → 119 [→ 17]）
  ⑥ 死亡（WState life=DEAD + WarDel 116）
  ⑦ 战斗结束 + 场景恢复（113 → 48 + 96 + 99×k + 97）

为什么单列一个测试
------------------
`_e2e_m4/m7` 走真实 socket，断言写在**手写的 struct 偏移**上；本测试的每一帧都用
`frames.unpack_s2c()`（唯一字段定义）解，等于同时校验：

  · 服务端 `build_war_*` 发出来的字节与 `frames.SPEC` 一致；
  · 该规格能把整帧**吃满**（`_len == len(body)`，多读/少读都会被抓到）。

字段定义与收发包共用一份的动机见 `sim/frames.py` 头部。

随机性处理：敌方打谁由 `random` 决定（主角/宠物），故 ⑤ 允许两种目标，
只断言「伤害符号与位域」；要逐帧钉死目标的场景交给 `_e2e_m5`（战败路径）。

用法： python tests/_test_battle_flow.py
"""
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

import m4_server as m          # noqa: E402
import frames as bf            # noqa: E402

RESULT = []


def check(cond, name, detail=''):
    RESULT.append((name, bool(cond)))
    print(('[PASS] %s %s' if cond else '[FAIL] %s %s') % (name, detail))


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))


def split_frame(raw):
    """S2C 帧 → (op, body)。帧头口径见 M4 规格 §0.2（N 含 opcode）。"""
    n = raw[0]
    if n == 0xFF:
        n = struct.unpack('<H', raw[1:3])[0]
        raw = raw[3:]
    else:
        raw = raw[1:]
    assert n == len(raw), '帧长度不符：头 %d 实 %d' % (n, len(raw))
    return raw[0], raw[1:]


def take(conn, since):
    """取 since 之后发出的帧 → [(op, body, decoded|None)]，并逐帧用 schema 解。"""
    out = []
    for raw in conn.sent[since:]:
        op, body = split_frame(raw)
        dec = bf.unpack_s2c(op, body)
        if 112 <= op <= 129:
            check(dec is not None and dec['_len'] == len(body),
                  'schema 吃满 op=%d' % op, 'body=%s' % body.hex(' '))
        out.append((op, body, dec))
    return out


def battle_ops(frames):
    """只留战斗帧 opcode（112–129），用于断言帧序。"""
    return [op for (op, _b, _d) in frames if 112 <= op <= 129]


def first_dec(frames, op):
    for (o, _b, d) in frames:
        if o == op:
            return d
    return None


def all_dec(frames, op):
    return [d for (o, _b, d) in frames if o == op]


def main():
    tmp = tempfile.mkdtemp(prefix='m4flow_')
    srv = m.M4Server('127.0.0.1', 3199, 0x12345678, 100,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    conn = FakeConn()
    ENEMY = m.ENEMY_WAR_ID

    # 关掉随机分支：敌方只普攻、不会心（打谁仍随机，见文件头说明）；
    # 宠物被动（必杀 8%/连击 40%）同样关掉，否则帧序会多出一条 AttackNormal 链。
    srv.enemy_skill_chance = 0.0
    srv.enemy_hurt_bs_chance = 0.0
    srv.pet_passive_mute = True
    srv.enemies = 1
    srv._pet_alive = True
    srv.active_pet_id = m.PET_ID
    srv.pet_hp = m.PET_HP
    srv.hero_name = '测试角色'
    srv.magics = {m.TEACH_MAGIC_IDS[0]: 1}

    # ── ① 遇怪 ──────────────────────────────────────────────────────────
    srv._start_war(conn)
    frames = take(conn, 0)
    check(battle_ops(frames) == [112, 115, 115, 129, 115, 114],
          '① 遇怪帧序 112→115(我)→115(宠)→129→115(敌)→114', battle_ops(frames))
    d = first_dec(frames, 112)
    check(d and d['scene'] == srv.mapid - 1001 and d['x'] == srv.px and d['y'] == srv.py,
          '① WarStart 场景回填（scene=%s → 客户端 +1001）' % (d and d['scene']))
    adds = all_dec(frames, 115)
    check(adds[0]['id'] == m.HERO_WAR_ID and adds[0]['allied'] == 1,
          '① 主角 WarAdd allied=1（bit7）')
    check(adds[1]['id'] == m.PET_WAR_ID and adds[1]['owner_id'] == m.HERO_WAR_ID,
          '① 宠物 WarAdd owner_id=主角（决定站位）')
    check(adds[2]['id'] == ENEMY and adds[2]['allied'] == 0,
          '① 敌方 WarAdd allied=0')
    check(first_dec(frames, 129)['id'] == m.PET_WAR_ID,
          '① WarSummon(129) 标记宠物可指挥')

    # 强制速度序：主角(100) → 宠物(49) → 敌方(40)；血量拉高，避免中途误杀
    srv.enemy_speed = {ENEMY: 40}
    srv.enemy_speed_base = 40
    srv.enemy_hp[ENEMY] = 500
    mark = len(conn.sent)

    # ── ② 攻击（160）────────────────────────────────────────────────────
    srv._hero_attack(conn, ENEMY)
    frames = take(conn, mark)
    check(battle_ops(frames) == [117, 123, 119],
          '② 攻击帧序 117→123→119（宠物窗口挂起，不发 WarTurn）', battle_ops(frames))
    got = first_dec(frames, 117)
    check(got and got['att_id'] == m.HERO_WAR_ID and got['vic_id'] == ENEMY,
          '② AttackNormal 攻守 id')
    ws = first_dec(frames, 123)
    check(ws and ws['id'] == ENEMY and ws['ward'] == m.WARD_HIT
          and ws['life'] == m.LIFE_NORMAL and ws['hurt'] == m.HURT_NONE
          and ws['hp_chg'] == srv._hero_attack_damage(),
          '② WState 位域/符号（ward=HIT, life=NORMAL, hp_chg=+%d）'
          % (ws and ws['hp_chg'] or -1))
    check(srv._pending_pet_suffix is not None, '② 主角动作后挂起等宠物指令')

    # ── ④ 召唤兽行动（169：body 只有 target+perform，无宠物 id）─────────
    # ★ 必须取**主动**招式（type=1/2）：被动（112 必杀 / 102 连击…）客户端不进
    #   宠物招式菜单，服务端也按非法指令回落普攻 —— 那样测不到 Perform 分支。
    mark = len(conn.sent)
    pet_pid = m.pet_active_skills()[0]
    srv._finish_pet_turn(conn, 169, bf.pack_c2s('C2SWarSummPerform',
                                                target=ENEMY, perform=pet_pid))
    frames = take(conn, mark)
    seq = battle_ops(frames)
    check(seq[:2] == [122, 123],
          '④ 宠物招式 169 → 122→123（Perform 族无 119）', seq)
    pf = first_dec(frames, 122)
    check(pf and pf['id'] == m.PET_WAR_ID and pf['pid'] == pet_pid
          and list(pf['vic_ids']) == [ENEMY],
          '④ Perform 施法者=宠物（服务端按 g_SummonObj 认，不从帧里读）')
    check(first_dec(frames, 123)['hp_chg'] == m.PET_DAMAGE,
          '④ 宠物伤害 = PET_DAMAGE(%d)' % m.PET_DAMAGE)

    # ── ⑤ 敌方回合（宠物之后的慢位）─────────────────────────────────────
    check(117 in seq and seq[-1] == 114,
          '⑤ 敌方回合 117… → 114 交还操作权', seq)
    eh = all_dec(frames, 123)[-1]
    check(eh['id'] in (m.HERO_WAR_ID, m.PET_WAR_ID)
          and eh['hp_chg'] == m.ENEMY_ATTACK_DMG and eh['ward'] == m.WARD_HIT,
          '⑤ 敌方命中 WState（目标 %s，hp_chg=+%d）' % (eh['id'], eh['hp_chg']))
    if eh['id'] == m.HERO_WAR_ID:
        # 17 不在战斗帧定义里（走 ITEM_ALL 宽度表），此处按 type(1)+value(2B) 自解
        hp = [struct.unpack('<H', b[1:3])[0] for (o, b, _d) in frames
              if o == 17 and len(b) >= 3]
        check(hp and hp[-1] == m.HERO_HP - m.ENEMY_ATTACK_DMG,
              '⑤ 主角真实血量走 PropChange(17) → %d'
              % (hp[-1] if hp else -1))
    else:
        check(srv.pet_hp == m.PET_HP - m.ENEMY_ATTACK_DMG,
              '⑤ 打宠物时不发 PropChange，扣血记在服务端 pet_hp=%d' % srv.pet_hp)

    # ── ③ 招式（161 → 122）──────────────────────────────────────────────
    mark = len(conn.sent)
    pid = m.TEACH_MAGIC_IDS[0]
    srv._hero_perform(conn, pid, None)
    frames = take(conn, mark)
    seq = battle_ops(frames)
    check(seq[:2] == [122, 123],
          '③ 招式帧序 122→123（不能拿 117 顶替）', seq)
    hf = first_dec(frames, 122)
    check(hf['id'] == m.HERO_WAR_ID and hf['pid'] == pid,
          '③ Perform 主角 pid=%d' % pid)
    check(any(o == 17 for (o, _b, _d) in frames),
          '③ 施法消耗走 PropChange(17)')

    # 招式之后同样挂宠物窗口；用 168（带 id）收尾，顺便覆盖 168 的解析
    mark = len(conn.sent)
    srv._finish_pet_turn(conn, 168, bf.pack_c2s('C2SWarSummAttack',
                                                id=m.PET_WAR_ID, target=ENEMY))
    frames2 = take(conn, mark)
    seq2 = battle_ops(frames2)
    check(seq2[:3] == [117, 123, 119] and seq2[-1] == 114,
          '④b 宠物普攻 168 → 117→123→119 → 回合收尾 114', seq2)

    # ── ⑥⑦ 击杀 → 死亡 / 战斗结束 / 场景恢复 ───────────────────────────
    srv.enemy_hp[ENEMY] = 1          # 下一击必杀，避免依赖伤害数值
    mark = len(conn.sent)
    srv._hero_attack(conn, ENEMY)
    frames = take(conn, mark)
    seq = battle_ops(frames)
    check(seq[:3] == [117, 123, 119],
          '⑥ 击杀一击仍是 117→123→119', seq)
    ws = first_dec(frames, 123)
    check(ws['life'] == m.LIFE_DEAD,
          '⑥ 击杀 WState life=DEAD(2)（否则客户端不播死亡动画）')
    check(116 in seq and 113 in seq,
          '⑥⑦ 死亡 WarDel(116) → WarEnd(113)', seq)
    check(not srv.in_war, '⑦ WarEnd 后 in_war=False')
    check(seq.index(116) < seq.index(113), '⑦ WarDel 在 WarEnd 之前')

    # 场景恢复：宠物信息(48) → SceneM(96) → AddNpc(99)×k → GotoM(97) [→ 随行宠物 105]
    rest = [op for (op, _b, _d) in frames if op in (48, 96, 97, 99, 105)]
    check(96 in rest and 97 in rest and rest.index(96) < rest.index(97),
          '⑦ 场景恢复 96→99×k→97（WarEnd 之后，否则地图空）', rest)
    check(all(op in (99, 105) for op in rest[rest.index(96) + 1:rest.index(97)]),
          '⑦ SceneM 与 GotoM 之间只有 AddNpc/随行宠物')

    # 经验/掉落必须在场景恢复之后，否则会被客户端 ClearAllObject 冲掉
    tail = [op for (op, _b, _d) in frames]
    awards = [i for i, o in enumerate(tail) if o in (17, 33)]
    if awards:
        check(min(awards) > tail.index(113), '⑦ 经验/掉落帧在 WarEnd 之后')

    shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in RESULT if not ok]
    print('\n全部 %d 项断言，失败 %d 项' % (len(RESULT), len(failed)))
    for n in failed:
        print('  ✗ %s' % n)
    print('=== 战斗全链路（字段定义驱动）%s ===' % ('PASS' if not failed else 'FAIL'))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
