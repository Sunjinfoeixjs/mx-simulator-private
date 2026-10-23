# -*- coding: utf-8 -*-
"""M7 冒烟测试：2026-10-18《本地离线》录像补全项（野狼 / 宠物技能 / 变异 / 乱舞冲击 / 会心红字）

逐条断言（全部为线上字节级）：
  ① 宠物技能帧 S2CSummonSkill(49)：n=6, x=6, 6 个 petskill id 与 PET_SKILLS 完全一致
  ② 变异帧 S2CSummonKey(53)：pid=1001, key=0, birthkey=2 → 客户画「变异」角标
  ③ 敌方 WarAdd(115)：name=野狼(d2 b0 c0 c7, NUL 结尾)、shape=2130(52 08)
  ④ 敌方招式 WarPerform(122)：pid=415（乱舞冲击）而非 magicdata 招式
  ⑤ WState(123)：敌方命中我方时 hurt=1（会心 war\\shit\\ 红字），伤害 hp_chg 为正
  ⑥ 出手序：主角 → 宠物 → 野狼（ENEMY_SPEED_BASE=40 < PET_SPEED=49 < HERO_SPEED=100）

用法：python _e2e_m7.py
"""
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m
import _e2e_m4 as E          # 复用收发/握手工具（该模块导入无副作用）

PORT = 3136
SEED = 0x0BADF00D


def expect(c, op_want, what):
    op, data = E.recv_s2c(c)
    assert op == op_want, '%s: 期望 op=%d 实得 %d (%s)' % (what, op_want, op, data.hex())
    return data


def ws(data):
    """解 WState 位域（MSB-first，见 build_war_wstate）：hurt=bit7..4, life=bit3..2, ward=bit1..0。"""
    assert len(data) == 6, data.hex()
    wid, bf, hp = struct.unpack('<BBi', data)
    return wid, (bf >> 4) & 0xF, (bf >> 2) & 0x3, bf & 0x3, hp


def run():
    srv = m.M4Server('127.0.0.1', PORT, seed=SEED, encounter_every=1, enemies=1,
                     savedir=E.tmp_savedir('m4m7_'))
    # 确定性注入：必出招式 + 必会心，便于断言 415 与 hurt=1
    srv.enemy_skill_chance = 1.0
    srv.enemy_hurt_bs_chance = 1.0
    # 宠物被动（必杀 8% 会心 / 连击 40% 追加）会插帧，逐帧断言必须关掉
    srv.pet_passive_mute = True
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.4)

    c = socket.create_connection(('127.0.0.1', PORT))
    steps = []

    # ── 握手 / 登录 / 建角 / 选角 ──
    op, data = E.recv_s2c(c)
    assert op == 1, op
    c.sendall(E.hello_resp(SEED))
    E.send_c2s(c, 1, E.login_body())
    op, data = E.recv_s2c(c)
    assert op == 2 and data[0] == 0, (op, data.hex())
    E.send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    expect(c, 6, '建角')
    expect(c, 2, '建角回 AccountInfo')
    expect(c, 3, '建角回 RoleInfo')

    E.send_c2s(c, 2, struct.pack('<I', 1))
    # 进世界：PropInit(16) PropResist(18) PropPower(19) → 养成同步 17×10 → 招式 65×2×N
    #         → SceneM(96) → AddNpc(99)×k → GotoM(97)
    expect(c, 16, 'PropInit')
    expect(c, 18, 'PropResist')
    expect(c, 19, 'PropPower')
    # 养成同步（M9）：等级/经验/下一级/点数/五维×5/金钱，全部走 PropChange(17)
    prog = []
    while True:
        op, data = E.recv_s2c(c)
        if op != 17:
            break
        prog.append(data[0])
    # 前 10 项稳定（等级/经验/下一级/点数/五维×5/金钱）；其后是生活状态四项：
    # 活力/活力上限/体力/体力上限（下标 24/25/26/27）。用常量而不是写死字面量——
    # 这类「生活状态」帧服务器还在继续加，钉死下标就会漂一次红一次（同下方招式帧）。
    assert prog[:10] == [5, 8, 9, 37, 36, 32, 33, 34, 35, 43], prog
    assert prog[10:] == [m.PROP_IDX_HUOLI, m.PROP_IDX_HUOLI_MAX,
                         m.PROP_IDX_TILI, m.PROP_IDX_TILI_MAX], prog
    magic_frames = 0
    while op == 65:
        # 2=学招式 / 7=装备；1 是生活技能，不计入招式帧
        if data[0] in (2, 7):
            magic_frames += 1
        op, data = E.recv_s2c(c)
    # ★ 2026-10-19：同 _e2e_m4——服务端以 self.magics 为准（基线+后学），断言从实际状态推导
    assert magic_frames == 2 * len(srv.magics), magic_frames
    assert op == 96, ('SceneM', op)
    while True:                 # 跳过 AddNpc(99)×k 与随行宠物(105)，等到 GotoM(97)
        op, data = E.recv_s2c(c)
        if op in (99, 105):
            continue
        break
    assert op == 97, ('GotoM', op)
    steps.append('进世界：养成同步 %d 帧 + 招式帧 %d（%d 式×学+装备）'
                 % (len(prog), magic_frames, len(m.TEACH_MAGIC_IDS)))

    # ① 宠物属性 → 技能 → 出战 → 变异
    expect(c, 48, 'S2CSummonInfo')
    data = expect(c, 49, 'S2CSummonSkill')
    pid, n, x = struct.unpack('<IBB', data[:6])
    ids = list(struct.unpack('<%dH' % n, data[6:6 + 2 * n]))
    assert pid == m.PET_ID, pid
    assert n == len(m.PET_SKILLS) and x == m.PET_HF_FROM, (n, x)
    assert ids == list(m.PET_SKILLS), ids
    assert 415 in ids and 505 in ids, ids          # 录像 tooltip 实证的两条
    steps.append('技能帧 n=%d x=%d ids=%s' % (n, x, ids))

    # ★ 2026-10-22：53（身份/变异角标）在 50（出战）之前 —— 多宠时先发完每只的
    #   48/49/53，最后一条 50 指出谁在出战。
    data = expect(c, 53, 'S2CSummonKey')
    kpid, key, birth = struct.unpack('<IHB', data)
    assert kpid == m.PET_ID and birth == m.PET_BIRTHKEY == 2, data.hex()
    # 绑定位（m_Key|=64）在参战流程里才置位，登录时还只是存档里的值
    assert key & ~m.PET_KEY_BOUND == m.PET_KEY & ~m.PET_KEY_BOUND, data.hex()
    steps.append('变异帧 pid=%d key=%d birthkey=%d（MARK_ISCOLOR）' % (kpid, key, birth))
    expect(c, 50, 'S2CSetFight')

    # 背包帧（逐件 ItemAdd 33，数目 = 当前背包件数；之后服务端静默等客户端动作）
    n_items = len(srv.items)
    for i in range(n_items):
        data = expect(c, 33, 'ItemAdd#%d' % i)
    assert n_items > 0, n_items
    steps.append('背包 ItemAdd×%d' % n_items)

    # ② 移动一次即遇怪（encounter_every=1）
    # ★2026-10-22：默认暗雷只在场景表内的野外/迷宫触发且出怪随图变化；本脚本逐帧
    #   断言敌方固定是 ENEMY_SHAPE（野狼），故显式切到 legacy 模式（全图野狼），
    #   遇怪表由 tests/_test_encounter_map.py 单独覆盖。
    m.ENCOUNTER_PER_MAP = False
    E.send_c2s(c, 54, E.move_body(46, 40))
    # Track 回显(86) → 回满 HP(17)+5 项战斗资源(17) → WarStart(112)
    while True:
        op, data = E.recv_s2c(c)
        if op in (86, 17):
            continue
        break
    assert op == 112, ('WarStart', op, data.hex())
    # 我方 WarAdd：主角 + 宠物（宠物后紧跟 Summon(129)：标记可指挥 → 主角选完动作弹宠物菜单）
    expect(c, 115, 'WarAdd 主角')
    expect(c, 115, 'WarAdd 宠物')
    expect(c, 129, 'WarSummon 宠物')
    # ③ 敌方 WarAdd：shape=ENEMY_SHAPE（野狼）、name=野狼
    data = expect(c, 115, 'WarAdd 敌方')
    wid, shape = struct.unpack('<BH', data[:3])
    assert wid == m.ENEMY_WAR_ID and shape == m.ENEMY_SHAPE, (wid, shape, data.hex())
    assert b'\xd2\xb0\xc0\xc7\x00' in data, data.hex()   # 野狼 + NUL，无长度前缀
    assert b'\x04\xd2\xb0' not in data, data.hex()
    expect(c, 114, 'WarTurn')
    steps.append('遇怪：WarAdd 敌方 shape=%d name=野狼' % shape)

    # ④ 主角出招 → Perform(122) 回显；随后敌方回合必放 415 且必会心
    _pid = m.TEACH_MAGIC_IDS[0]              # 不写死招式 id（基线表改了这里也不会漏）
    E.send_c2s(c, 161, struct.pack('<BH', 1, _pid))
    data = expect(c, 122, 'S2CWarPerform 主角')
    assert data[0] == m.HERO_WAR_ID and struct.unpack('<H', data[1:3])[0] == _pid, data.hex()
    data = expect(c, 123, 'WState 主角命中')
    got = ws(data)
    # ★ 2026-10-19：同 m4——伤害含力量加成，从服务端实际状态推导
    assert got == (m.ENEMY_WAR_ID, m.HURT_NONE, m.LIFE_NORMAL, m.WARD_HIT,
                   srv._magic_damage(1101)), got

    # 主角出手后：结算施法消耗 PropChange(17)——1101 仅 m_FP/m_Df 非零 → 恰好 2 帧
    cost = m.MAGIC_COST.get(1101, {})
    n_cost = sum(1 for k, v in cost.items() if v)
    for i in range(n_cost):
        expect(c, 17, '施法消耗 PropChange#%d' % i)
    # 出手序 主角→宠物→敌：服务端此刻等宠物指令（C2S 168），不发帧
    E.send_c2s(c, 168, bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
    # 宠物普攻：AttackNormal(117)→WState(123)→AttackNormalEnd(119)
    expect(c, 117, '宠物 AttackNormal')
    data = expect(c, 123, '宠物命中 WState')
    got = ws(data)
    assert got[0] == m.ENEMY_WAR_ID and got[4] == m.PET_DAMAGE, got
    expect(c, 119, '宠物 AttackNormalEnd')

    # 敌方回合（本回合开场 WarTurn 已在遇怪时下发，g_Turn 非 0，故这里直接演出）：
    #   Perform(122,pid=415) → WState(hurt=BS) → HPPercent(125) → AttackNormalEnd(119)
    #   → 若打主角还有 PropChange(17) → 回合末 WarTurn(114)
    data = expect(c, 122, '敌方 Perform')
    ewid, epid = struct.unpack('<BH', data[:3])
    assert ewid == m.ENEMY_WAR_ID and epid == 415, (ewid, epid, data.hex())
    data = expect(c, 123, '敌方命中 WState')
    twid, thurt, tlife, tward, hp_chg = ws(data)
    assert twid in (m.HERO_WAR_ID, m.PET_WAR_ID), twid
    assert thurt == m.HURT_BS, ('会心 hurt 位', thurt)
    assert tlife == m.LIFE_NORMAL and tward == m.WARD_HIT, (tlife, tward)
    assert hp_chg == m.ENEMY_ATTACK_DMG, hp_chg
    expect(c, 125, 'HPPercent')
    expect(c, 119, 'AttackNormalEnd')
    if twid == m.HERO_WAR_ID:          # 打主角时补 PropChange 同步 hp_chg（打宠物则无）
        data = expect(c, 17, 'PropChange 主角HP')
        assert data[0] == m.PROP_IDX_HP, data.hex()
        assert struct.unpack('<H', data[1:3])[0] == m.HERO_HP - m.ENEMY_ATTACK_DMG, data.hex()
    expect(c, 114, '回合末 WarTurn')
    steps.append('敌方 Perform pid=415 + WState hurt=BS(%d) hp_chg=%d → 回合推进' % (thurt, hp_chg))

    # ⑤ 召唤兽指令族：**169 的 body 没有 id**（target(1)+perform(2)，见 M4 规格 §3.4.2）
    #    主角「防御」不造成伤害 → 战斗必继续，且宠物指令窗口照常打开（_resolve_hero_round）。
    E.send_c2s(c, 163)
    # ★ 取**主动**招式（type=1/2）：PET_SKILLS[0]=112「必杀」是被动，
    #   客户端不进宠物招式菜单，服务端按非法指令回落普攻（117），断言 122 会红。
    _pid169 = m.pet_active_skills()[0]
    E.send_c2s(c, 169, struct.pack('<BH', m.ENEMY_WAR_ID, _pid169))   # ← 3 字节，无召唤兽 id
    data = expect(c, 122, 'R2 宠物 Perform(169)')
    pwid, ppid = struct.unpack('<BH', data[:3])
    assert pwid == m.PET_WAR_ID and ppid == _pid169, data.hex()
    assert data[3:] and set(data[3:]) == {m.ENEMY_WAR_ID}, data.hex()   # 目标由 body[0] 指定
    _ended = False
    while True:                                    # 收尾：回合交还 或 战斗结束
        op, _ = E.recv_s2c(c)
        if op == 113:
            _ended = True
            while True:                            # 等场景重同步收尾（SceneM→AddNpc→GotoM 97）
                op2, _ = E.recv_s2c(c)
                if op2 == 97:
                    break
            break
        if op == 114:
            break
    steps.append('召唤兽招式 169（body=target+perform，无 id）→ Perform(122) pid=%d；回合%s'
                 % (_pid169, '结束' if _ended else '推进'))

    c.close()
    # ★ 显式收尾（2026-10-20）：daemon 线程必须在解释器 finalizing 之前结束。
    #   否则它若正卡在 log() 的 print 里，会与关闭流程争 stdout 的 BufferedWriter 锁，
    #   触发 Fatal Python error: _enter_buffered_busy，进程以 0xC0000409 退出
    #   ——**测试其实已经 PASS**，崩在收尾，看起来像随机失败。
    #   实测：不做显式收尾约 27% 概率崩；stop()+join() 后 0/20。
    try:
        srv.stop()
        for _th in threading.enumerate():
            if _th is not threading.current_thread() and _th.daemon:
                _th.join(timeout=2.0)
    except Exception:                      # noqa: BLE001
        pass
    print('PASS：' + ' → '.join(steps))
    print('全部 %d 步断言通过' % len(steps))


if __name__ == '__main__':
    run()
