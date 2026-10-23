"""Regression tests for inventory use, shop flow, and activated pet combat."""
import os
import shutil
import struct
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sim'))
import m4_server as m


class FakeConn:
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(frame)


def op(frame):
    return frame[1]


def main():
    tmp = tempfile.mkdtemp(prefix='m4_common_ops_')
    passed = []

    def check(name, condition):
        passed.append((name, bool(condition)))
        print('[%s] %s' % ('PASS' if condition else 'FAIL', name))

    srv = m.M4Server(
        '127.0.0.1', 0, m.SEED_DEFAULT, 0,
        logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    srv.enemy_skill_chance = 0.0
    # 宠物被动（必杀 8% / 连击 40%）会多出一条 AttackNormal 链，逐帧断言要确定性
    srv.pet_passive_mute = True
    try:
        conn = FakeConn()
        srv.hero_hp = 100
        srv._handle_item_use(conn, struct.pack('<II', 2002, 2003))
        check('批量使用两个回血道具并受最大 HP 限制',
              srv.hero_hp == m.HERO_HP and srv.items[2002]['qty'] == 9
              and srv.items[2003]['qty'] == 2)
        check('批量使用回传两件物品数量与角色属性',
              sum(op(frame) == m.OP_ITEM_QUANTITY for frame in conn.sent) == 2
              and any(op(frame) == m.OP_PROP_CHANGE for frame in conn.sent))

        before = srv.items[2001]['qty']
        srv._handle_item_use(conn, struct.pack('<I', 2001))
        check('未实现效果的道具不被误消耗', srv.items[2001]['qty'] == before)
        before = srv.items[2002]['qty']
        srv._handle_item_use(conn, struct.pack('<I', 2002))
        check('角色 HP 已满时不浪费回血道具',
              srv.hero_hp == m.HERO_HP and srv.items[2002]['qty'] == before)

        srv.pet_hp = 100
        before_pet_item = srv.items[2002]['qty']
        srv._handle_item_use(conn, struct.pack('<IIB', 2002, m.PET_ID, 0))
        check('宠物定向使用道具恢复宠物 HP',
              srv.pet_hp == 260 and srv.items[2002]['qty'] == before_pet_item - 1
              and any(op(frame) == m.OP_SUMMON_INFO for frame in conn.sent))

        srv.in_war = True
        srv._pet_alive = True
        srv.pet_hp = 100
        srv._handle_item_use(conn, struct.pack('<IIB', 2002, m.PET_WAR_ID, 0))
        check('战斗中宠物道具使用更新宠物血条',
              srv.pet_hp == 260 and any(op(frame) == m.OP_WAR_HP_PERCENT
                                        for frame in conn.sent))
        srv.in_war = False

        # Pet activation is authoritative: inactive pets are absent from battle,
        # active pets retain their current HP and participate in speed order.
        srv.pet_hp = 83
        srv._set_active_pet(conn, 0)
        srv._start_war(conn)
        adds = [frame[2:] for frame in conn.sent if op(frame) == m.OP_WAR_ADD]
        check('休息中的宠物不加入战斗行动序',
              not any(body and body[0] == m.PET_WAR_ID for body in adds)
              and m.PET_WAR_ID not in srv._speed_order())

        srv.in_war = False
        srv._set_active_pet(conn, m.PET_ID)
        srv._start_war(conn)
        adds = [frame[2:] for frame in conn.sent if op(frame) == m.OP_WAR_ADD]
        check('激活宠物加入战斗且不被自动回满',
              srv.pet_hp == 83 and srv._pet_alive
              and any(body and body[0] == m.PET_WAR_ID for body in adds)
              and m.PET_WAR_ID in srv._speed_order())

        srv.enemy_hp = {m.ENEMY_WAR_ID: 500}
        srv.enemy_speed = {m.ENEMY_WAR_ID: 90}
        pet_turn = FakeConn()
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[0]):
            srv._resolve_hero_round(pet_turn, lambda: False)
            check('主角行动后等待客户端宠物指令',
                  srv._pending_pet_suffix == []
                  and not any(op(frame) == m.OP_WAR_TURN and frame[2] == 0
                              for frame in pet_turn.sent))
            srv._finish_pet_turn(
                pet_turn, m.C2S_WAR_SUMM_ATTACK,
                bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
        attackers = [frame[2] for frame in pet_turn.sent
                     if op(frame) == m.OP_WAR_ATTACK_NORMAL]
        check('宠物手动普攻按指定目标结算',
              attackers == [m.ENEMY_WAR_ID, m.PET_WAR_ID]
              and any(op(frame) == m.OP_WAR_TURN for frame in pet_turn.sent))

        skill_turn = FakeConn()
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[0]):
            srv._resolve_hero_round(skill_turn, lambda: False)
            # ★ 技能 id 必须取 **petskill** 表的宠物技能 id：415 = 「乱舞冲击」
            #   （录像 v_23 宠物面板第 1 行第 4 格 tooltip 的 petskill id）。
            #   1102 是 `magicdata/skilldata` 的**英雄招式** id（SummonSkill 帧里
            #   出现的那组 id 就是取错了表），内核按「不在宠物技能表 → 不执行未知
            #   效果」拒收，故这里用 415 才是宠物真值（见《录像补全_M7_野狼与宠物技能.md》）。
            srv._finish_pet_turn(
                skill_turn, m.C2S_WAR_SUMM_PERFORM,
                bytes([m.ENEMY_WAR_ID]) + struct.pack('<H', 415))
        perform_frames = [frame for frame in skill_turn.sent
                          if op(frame) == m.OP_WAR_PERFORM]
        check('宠物已配置技能走 WarPerform 并结算伤害',
              len(perform_frames) == 1
              and perform_frames[0][2] == m.PET_WAR_ID
              and any(op(frame) == m.OP_WAR_WSTATE for frame in skill_turn.sent)
              and any(op(frame) == m.OP_WAR_TURN for frame in skill_turn.sent))

        # Defensive stance is an explicit simulation policy: halve the next
        # incoming damage in the current round and expose the client defend ward.
        srv.in_war = True
        srv.enemy_hp = {m.ENEMY_WAR_ID: 500}
        srv.enemy_speed = {m.ENEMY_WAR_ID: 90}
        srv._pet_alive = False
        srv.hero_hp = 100
        defend_turn = FakeConn()
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[0]):
            srv._hero_defend_action(defend_turn)
        defend_states = [frame for frame in defend_turn.sent
                         if op(frame) == m.OP_WAR_WSTATE
                         and frame[2] == m.HERO_WAR_ID]
        check('主角防御：本回合受击减半并播放防御受击姿态',
              srv.hero_hp == 100 - (m.ENEMY_ATTACK_DMG + 1) // 2
              and len(defend_states) == 1
              # ★ WState 位域（客户端 MSB-first）：bitfield = (hurt<<4)|(life<<2)|ward
              #   → ward 在**低 2 位**（UnpackBit(6,2) 在 MSB-first 下 = bit1..0），
              #   早期写成 >>6 是取错了位。
              and (defend_states[0][3] & 0x3) == m.WARD_DEFEND
              and struct.unpack('<i', defend_states[0][4:8])[0]
              == (m.ENEMY_ATTACK_DMG + 1) // 2)

        # A pet can protect the hero. Emit op121 immediately before the attack
        # that actually hits the protected unit; damage and HP bar go to pet.
        srv._pet_alive = True
        srv.pet_hp = 100
        srv.hero_hp = 100
        protect_turn = FakeConn()
        def protect_act():
            srv._set_round_protect(protect_turn, m.PET_WAR_ID, m.HERO_WAR_ID)
            return False
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[0]):
            srv._resolve_hero_round(protect_turn, protect_act)
        protect_frames = [frame for frame in protect_turn.sent
                          if op(frame) == m.OP_WAR_PROTECT]
        protect_idx = next((i for i, frame in enumerate(protect_turn.sent)
                            if op(frame) == m.OP_WAR_PROTECT), -1)
        attack_idx = next((i for i, frame in enumerate(protect_turn.sent)
                           if op(frame) == m.OP_WAR_ATTACK_NORMAL), -1)
        check('宠物保护主角：op121 关联保护者/目标并转移伤害',
              len(protect_frames) == 1
              and protect_frames[0][2:4] == bytes([m.PET_WAR_ID, m.HERO_WAR_ID])
              and 0 <= attack_idx < protect_idx
              and srv.hero_hp == 100
              and srv.pet_hp == 100 - m.ENEMY_ATTACK_DMG)
        srv._finish_pet_turn(
            protect_turn, m.C2S_WAR_SUMM_ATTACK,
            bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
        check('保护结算后正常完成宠物回合', srv._pending_pet_suffix is None
              and any(op(frame) == m.OP_WAR_TURN for frame in protect_turn.sent))

        srv.hero_hp = 100
        srv.pet_hp = 100
        srv.enemy_speed[m.ENEMY_WAR_ID] = 90
        hero_protect_turn = FakeConn()
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[-1]):
            srv._hero_war_protect(hero_protect_turn, m.PET_WAR_ID)
        hero_protect_frames = [frame for frame in hero_protect_turn.sent
                               if op(frame) == m.OP_WAR_PROTECT]
        check('主角保护宠物：有效目标受击时由主角承伤',
              len(hero_protect_frames) == 1
              and hero_protect_frames[0][2:4]
              == bytes([m.HERO_WAR_ID, m.PET_WAR_ID])
              and srv.hero_hp == 100 - m.ENEMY_ATTACK_DMG
              and srv.pet_hp == 100)
        srv._finish_pet_turn(
            hero_protect_turn, m.C2S_WAR_SUMM_ATTACK,
            bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))

        srv.hero_hp = 100
        srv.pet_hp = 100
        srv.enemy_speed[m.ENEMY_WAR_ID] = 20
        pet_guard_turn = FakeConn()
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[-1]):
            srv._resolve_hero_round(pet_guard_turn, lambda: False)
            srv._finish_pet_turn(
                pet_guard_turn, m.C2S_WAR_SUMM_GUARD, bytes([m.PET_WAR_ID]))
        pet_guard_states = [frame for frame in pet_guard_turn.sent
                            if op(frame) == m.OP_WAR_WSTATE
                            and frame[2] == m.PET_WAR_ID]
        check('宠物防御：慢位敌方伤害减半并播放防御受击姿态',
              srv.pet_hp == 100 - (m.ENEMY_ATTACK_DMG + 1) // 2
              and len(pet_guard_states) == 1
              and (pet_guard_states[0][3] & 0x3) == m.WARD_DEFEND   # ward 在低 2 位
              and struct.unpack('<i', pet_guard_states[0][4:8])[0]
              == (m.ENEMY_ATTACK_DMG + 1) // 2)

        # Force an enemy hit onto the pet to verify HP/KO state and next-battle
        # persistence without depending on random targeting.
        srv.pet_hp = m.ENEMY_ATTACK_DMG
        with mock.patch.object(srv.battle_rng, 'choice', side_effect=lambda name, seq: seq[-1]):
            srv._enemy_attack(conn, m.ENEMY_WAR_ID)
        check('宠物被击倒后退出战斗单位并保留倒地 HP',
              srv.pet_hp == 0 and not srv._pet_alive)
        srv.in_war = False
        srv._start_war(conn)
        check('下一场战斗不自动复活倒地宠物',
              srv.pet_hp == 0 and not srv._pet_alive
              and m.PET_WAR_ID not in srv._speed_order())

        srv.in_war = False
        srv._persist()
        restored = m.M4Server(
            '127.0.0.1', 0, m.SEED_DEFAULT, 0,
            logdir=os.path.join(tmp, 'logs2'), savedir=os.path.join(tmp, 'save'))
        try:
            check('存档保留宠物激活状态与 HP',
                  restored.active_pet_id == m.PET_ID and restored.pet_hp == 0)
        finally:
            restored._logf.close()
    finally:
        srv._logf.close()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok in passed if not ok]
    print('==== %d/%d passed ====' % (len(passed) - len(failed), len(passed)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
