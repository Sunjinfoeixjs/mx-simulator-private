# -*- coding: utf-8 -*-
# ⚠ 图例：本脚本为 M4 期回归用例，**已被 _e2e_m7.py 取代**（2026-10-18）。
#   自「主角→宠物→敌」出手序与宠物进场后，旧用例未在主角行动后补发宠物指令(168)，
#   跑完整链路会停在 expect_enemy_turn 处（服务端正在等宠物指令）。已修正的项：
#   · 进世界帧序列改为动态读取（招式帧随 TEACH_MAGIC_IDS 变化）
#   · WState 位域改为 MSB-first 解码 · 敌方名字取 ENEMY_NAME · 补消费宠物 48/49/50/53 与 33
#   新契约请以 _e2e_m7.py 为准（宠物技能/变异帧/野狼/415/会心红字）。
"""m4_server.py 端到端自测：用本地 socket 模拟客户端走完整闭环，验证服务端帧收发与状态机。

覆盖 M4 帧契约（含 2026-10-15 依客户端源码修正的三处语义）：
  · WState 位域与 hp_chg 符号（ward=HIT / hp_chg 正数=扣血 / life=DEAD 触发死亡动画）
  · 多单位战斗（--enemies，逐个击杀到全灭才 WarEnd）
  · 招式可用性补帧（PropResist/PropPower/SkillLevel/Block）——否则客户端报「无法使用这个招式」
"""
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, 'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m

SEED = 0x12345678
PORT = 3135
ENEMIES = 3        # 本测试用 3 个敌方单位，验证多单位与逐个击杀


def tmp_savedir(prefix='m4e2e_'):
    """复制真实存档到 tmp 并返回该 save 目录：测试起点与真机一致，写入不污染真实进度。

    ★ 2026-10-19：m4/m5/m7 此前不传 savedir → 用默认 SAVE_DIR，一跑就把真机进度
      （role.json）覆盖成测试终态；真机联调时这会把玩家角色刷回测试状态。
      真机联调与 runner（tests/_run_all.py）批量跑之前，必须先隔离。
    ★ 归一化两项易变状态（副本内，不动真档）：
      · tasks 剥掉——进行中任务会让每次击杀都补发 S2CTaskAdd(72)，
        战斗中段突然多一帧，逐帧断言全乱（真机联调时用户在玩游戏，任务态会变）；
      · move_count 归零——决定遇怪时机（m4/m5/m7 各自也会归零，这里双保险）。
    没有存档也能跑（全新角色），不视为错误。
    """
    import json
    import os
    import shutil
    import tempfile
    d = os.path.join(tempfile.mkdtemp(prefix=prefix), 'save')
    os.makedirs(d, exist_ok=True)
    try:
        for fn in os.listdir(m.SAVE_DIR):
            src = os.path.join(m.SAVE_DIR, fn)
            if os.path.isfile(src):
                shutil.copy2(src, d)
        role = os.path.join(d, 'role.json')
        if os.path.isfile(role):
            sv = json.load(open(role, encoding='utf-8'))
            sv['tasks'] = {}
            sv['move_count'] = 0
            json.dump(sv, open(role, 'w', encoding='utf-8'), ensure_ascii=False)
    except OSError:
        pass
    return d


def recv_exact(c, n, timeout=2.0):
    c.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        ch = c.recv(n - len(buf))
        if not ch:
            raise ConnectionError('closed')
        buf += ch
    return buf


def recv_s2c(c, timeout=2.0):
    n = recv_exact(c, 1, timeout)[0]
    if n == 0xFF:
        n = struct.unpack('<H', recv_exact(c, 2, timeout))[0]
    payload = recv_exact(c, n, timeout)
    return payload[0], payload[1:]   # opcode, data


def drain_quiet(c, quiet=0.5, hard=8.0):
    """读完当前积压的所有帧，直到 quiet 秒无新帧（或 hard 秒上限）。

    ★ 2026-10-19：进世界帧序列的长度**随存档状态变**（招式数 = 基线+玩家后学的、
      地图 NPC 数、背包件数）——写死帧数的老断言在存档漂移后必红。统一用本助手
      抽干，再从「下一帧」开始断言，测试只验协议语义、不绑存档内容。
    返回读到的帧数。
    """
    import socket as _sk
    n = 0
    t0 = time.time()
    while time.time() - t0 < hard:
        try:
            recv_s2c(c, quiet)
            n += 1
        except _sk.timeout:
            break
    return n


def send_c2s(c, op, body=b''):
    if len(body) >= 0xFF:
        c.sendall(bytes([op]) + b'\xFF' + struct.pack('<H', len(body)) + body)
    else:
        c.sendall(bytes([op]) + bytes([len(body)]) + body)


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)      # CLIENT_SCRIPT_VERSION
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)       # GetVersion + update.ini 1.2.73
    return bytes(b)


def move_body(x, y):
    return struct.pack('<HH', x, y) + b'\x00'


def expect_enemy_turn(c, alive, first_op=None, defending=False):
    """消费并校验一个完整的敌方回合帧序列（M5）。

    序列：WarTurn(114) → 每个存活敌方 [AttackNormal(117) WState(123) HPPercent(125)
          AttackNormalEnd(119) PropChange(17)] → WarTurn(114)

    first_op：若调用方已预先读掉开场 WarTurn 的 opcode，可传入以避免重复读取。
    defending：本回合主角处于防御姿态 → 受击伤害减半（DEFEND_DAMAGE_DIVISOR）
      且 ward=WARD_DEFEND（服务端 _enemy_attack 的约定）。
    返回主角本回合后的 HP（由 PropChange 帧解析）。
    """
    if first_op is None:
        op, _ = recv_s2c(c)
    else:
        op = first_op
    assert op == 114, ('敌方回合起始 WarTurn', op)
    hero_hp = None
    for wid in alive:
        op, _ = recv_s2c(c)
        assert op == 117, ('AttackNormal', wid, op)
        op, data = recv_s2c(c)
        assert op == 123, ('WState', wid, op)
        # 敌方打主角：主角 ward=HIT、hp_chg 为**正数**（本服符号约定），life 正常
        # ★ 2026-10-19：HERO_WAR_ID=100（非 0，宠物引入后改的），旧 wid=0 全错位；
        #   防御姿态下伤害减半（ceil(20/2)=10）且 ward=WARD_DEFEND。
        want_dmg = ((m.ENEMY_ATTACK_DMG + m.DEFEND_DAMAGE_DIVISOR - 1)
                    // m.DEFEND_DAMAGE_DIVISOR) if defending else m.ENEMY_ATTACK_DMG
        want_ward = m.WARD_DEFEND if defending else m.WARD_HIT
        assert check_wstate(data, m.HERO_WAR_ID, 0, m.LIFE_NORMAL,
                            want_ward, want_dmg), data.hex()
        op, _ = recv_s2c(c)
        assert op == 125, ('HPPercent', wid, op)
        op, _ = recv_s2c(c)
        assert op == 119, ('AttackNormalEnd', wid, op)
        op, data = recv_s2c(c)
        assert op == 17, ('PropChange(主角HP)', wid, op)
        assert data[0] == m.PROP_IDX_HP, data.hex()
        hero_hp = struct.unpack('<H', data[1:3])[0]
    op, _ = recv_s2c(c)
    assert op == 114, ('敌方回合结束 WarTurn', op)
    return hero_hp


def check_wstate(data, wid, hurt, life, ward, hp_chg):
    """校验 S2CWarWState(123) 帧体：id(1) bitfield(1) hp_chg(4 signed)。

    bitfield 布局与客户端 `netwar.S2CWarWState` 的三个 UnpackBit 调用一致：
        hurt = bits[0:4]   life = bits[4:6]   ward = bits[6:8]
    """
    if len(data) != 6:
        return False
    got_wid, bf, got_hp = struct.unpack('<BBi', data)
    # 位域为 MSB-first（build_war_wstate）：hurt=bit7..4, life=bit3..2, ward=bit1..0
    got_hurt = (bf >> 4) & 0xF
    got_life = (bf >> 2) & 0x3
    got_ward = bf & 0x3
    return (got_wid, got_hurt, got_life, got_ward, got_hp) == (wid, hurt, life, ward, hp_chg)


def run():
    srv = m.M4Server('127.0.0.1', PORT, seed=SEED, encounter_every=3, enemies=ENEMIES,
                     savedir=tmp_savedir('m4m4_'))
    # 2026-10-18：关掉随机分支（敌方招式/会心）以保证本脚本的逐帧断言确定
    srv.enemy_skill_chance = 0.0
    srv.enemy_hurt_bs_chance = 0.0
    srv.move_count = 0          # 存档里的移动计数会影响首次遇怪步数，测试内归零
    # ★ 2026-10-19：宠物请出战斗——本脚本断言「敌方全部瞄主角」的逐帧序列，
    #   宠物参战后 _enemy_attack 随机瞄主角/宠物、且每个英雄动作后挂起等宠物指令
    #   （op168），帧序列随目标变。宠物流（发 168 → 宠物四连 → 收尾回合）由
    #   _e2e_m7.py 覆盖；这里保持宠物参战前的确定性逐帧断言。
    #   ⚠ 抑制**不能**在这里设：登录会走 _apply_save()（载入共享存档）、建角会走
    #     _reset_character()，两者都把 active_pet_id/pet_hp 改回「宠物出战」
    #     （共享 role.json 存的就是 active_pet_id=1001），此处的赋值必被覆盖。
    #     有效位置是「进世界之后、移动触发遇怪之前」——见 ⑤ 之前那段。
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    time.sleep(0.4)

    c = socket.create_connection(('127.0.0.1', PORT))
    steps = []

    # ① Hello
    op, data = recv_s2c(c)
    assert op == 1 and len(data) == 6, (op, data.hex())
    c.sendall(hello_resp(SEED))
    steps.append('Hello')

    # ② 登录（无角色 → Count=0）
    send_c2s(c, 1, login_body())
    op, data = recv_s2c(c)
    assert op == 2 and data[0] == 0, (op, data.hex())
    steps.append('登录→AccountInfo(0)')

    # ③ 建角
    send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    op, _ = recv_s2c(c)
    assert op == 6, op                      # 创建成功
    op, _ = recv_s2c(c)
    assert op == 2, op                      # AccountInfo(1)
    op, _ = recv_s2c(c)
    assert op == 3, op                      # RoleInfo
    steps.append('建角')

    # ④ 选角进入 → 进世界序列（★ 含招式可用性补帧）
    send_c2s(c, 2, struct.pack('<I', 1))
    seq = []
    for _ in range(3):                      # PropInit(16) PropResist(18) PropPower(19)
        op, _ = recv_s2c(c)
        seq.append(op)
    assert seq == [16, 18, 19], seq
    n_prog = 0                              # 养成同步（M9）：等级/经验/下一级/点数/五维×5/金钱
    prog_idx = []
    while True:
        op, data = recv_s2c(c)
        if op != 17:
            break
        n_prog += 1
        prog_idx.append(data[0])
    # 前 10 项稳定（等级/经验/下一级/点数/五维×5/金钱）；其后是生活状态四项：
    # 活力/活力上限/体力/体力上限（下标 24/25/26/27）。用常量而不是写死字面量——
    # 这类「生活状态」帧服务器还在继续加，钉死下标就会漂一次红一次（同下方招式帧）。
    assert prog_idx[:10] == [5, 8, 9, 37, 36, 32, 33, 34, 35, 43], prog_idx
    assert prog_idx[10:] == [m.PROP_IDX_HUOLI, m.PROP_IDX_HUOLI_MAX,
                             m.PROP_IDX_TILI, m.PROP_IDX_TILI_MAX], prog_idx
    n_magic = 0                             # SkillLevel(65)：每式两帧（学 + 装备）
    while op == 65:
        # 2=学招式 / 7=装备进战斗菜单；1 是生活技能（工艺），不计入招式帧
        if data[0] in (2, 7):
            n_magic += 1
        op, data = recv_s2c(c)
    # ★ 2026-10-19：服务端以 self.magics 为准（= 基线 TEACH_MAGIC_IDS + 玩家后学的，
    #   见 m4_server.py:1526-1528），共享存档里多学一式就会多两帧——断言必须从实际
    #   状态推导，不能写死基线 8，否则存档一漂移测试就红（runner 批量跑时的假失败）。
    assert n_magic == 2 * len(srv.magics), (n_magic, len(srv.magics))
    assert op == 96, ('SceneM', op)
    while True:                             # AddNpc(99)×k → 随行宠物(105) → GotoM(97)
        op, _ = recv_s2c(c)
        if op not in (99, 105):
            break
    assert op == 97, op                     # GotoM
    # 宠物帧 SummonInfo(48)/SummonSkill(49)/SummonKey(53) + SetFight(50) + 背包 ItemAdd(33)×N
    # ★ 2026-10-22：改成「逐只身份帧（48/49/53）先发完，最后用 50 指出出战那只」——
    #   多宠时这只顺序才成立（每只都要有 48/49/53，50 只有一条）。
    for op_want in (48, 49, 53, 50):
        op, _ = recv_s2c(c)
        assert op == op_want, ('宠物帧', op_want, op)
    for i in range(len(srv.items)):
        op, _ = recv_s2c(c)
        assert op == 33, ('ItemAdd', i, op)
    steps.append('进世界 16/18/19 + 招式65×%d + 96/99../97 + 宠物48/49/53/50 + 背包33×%d'
                 % (n_magic, len(srv.items)))

    # ★ 宠物请出战斗（有效位置）：进世界之后、移动触发遇怪之前。
    #   _start_war() 会按 active_pet_id/pet_hp 重算 _pet_alive，只有在这里设才逃得过
    #   登录/建角的复位；此后无人再改它，故本场（及本脚本后续回合）都是无宠帧序。
    # ★2026-10-22：暗雷默认只在场景表内的野外/迷宫触发（城镇不遇怪，原版行为），
    #   出怪名单也随图变化。本脚本测的是**战斗基础帧序**，且逐帧断言敌方固定是
    #   ENEMY_SHAPE（野狼），故显式切到 legacy 模式（全图野狼）——遇怪表由
    #   tests/_test_encounter_map.py 单独覆盖。
    m.ENCOUNTER_PER_MAP = False
    srv._pet_alive = False
    srv.active_pet_id = 0

    # ⑤ 移动 ×3 → 第 3 次遇怪（多单位）
    for i in range(1, 4):
        send_c2s(c, 54, move_body(45 + i, 40))
        op, _ = recv_s2c(c)
        assert op == 86, op                 # Track 回显
        if i < 3:
            continue
        # 第 3 次：遇怪（WarStart 前会先收到一帧 PropChange 回满主角血量）
        op, data = recv_s2c(c)
        assert op == 17 and data[0] == m.PROP_IDX_HP, (op, data.hex())
        assert struct.unpack('<H', data[1:3])[0] == m.HERO_HP, data.hex()
        while True:                         # 之后是 5 项战斗资源回满（m_MP/m_FP/m_At/m_Df/m_Dp）
            op, _ = recv_s2c(c)
            if op != 17:
                break
        assert op == 112, op                # WarStart
        op, data = recv_s2c(c)
        assert op == 115, op                # WarAdd 我方
        # name = hero_name 的 GBK+\0（C 字符串，无 [len] 前缀）
        assert data.endswith(b'\xb2\xe2\xca\xd4\xbd\xc7\xc9\xab\x00'), data.hex()
        assert b'\x08\xb2\xe2\xca\xd4' not in data, data.hex()
        if srv._pet_alive:                  # 宠物作为己方单位也会 WarAdd（id=250，造型 4005）
            op, data = recv_s2c(c)
            assert op == 115 and data[0] == m.PET_WAR_ID, (op, data.hex())
            assert struct.unpack('<H', data[1:3])[0] == m.PET_SHAPE, data.hex()
        for k in range(ENEMIES):
            op, data = recv_s2c(c)
            assert op == 115, (k, op)       # WarAdd 敌方 ×ENEMIES
            # name 以 \0 结尾、无 [len] 前缀（名字取 ENEMY_NAME，录像复刻为「野狼」）
            nb = m.ENEMY_NAME.encode('gbk') + b'\x00'
            assert nb in data and bytes([len(m.ENEMY_NAME)]) + nb[:1] not in data, data.hex()
        op, _ = recv_s2c(c)
        assert op == 114, op                # WarTurn
    steps.append('移动×3→遇怪(%d敌方)' % ENEMIES)

    # ⑥ 招式（C2SWarPerform 161）→ 必须回 S2CWarPerform(122)，否则客户端招式命令缺失
    # 眉来眼去 powrate=100%，本基础回归用它保留「招式 50 + 普攻 50 击杀」链；
    # 94% / 112% 的差异倍率由 _e2e_m7 与 _test_hero_magic 专门覆盖。
    magic_id = m.TEACH_MAGIC_IDS[1]
    send_c2s(c, 161, struct.pack('<BH', 1, magic_id))
    op, data = recv_s2c(c)
    assert op == 122, op                           # S2CWarPerform
    assert data[0] == m.HERO_WAR_ID and struct.unpack('<H', data[1:3])[0] == magic_id, data.hex()
    assert data[3:] == bytes([1]), data.hex()      # vic_ids = [1]（首个敌方）
    op, data = recv_s2c(c)
    assert op == 123, op                           # WState
    # ★ 2026-10-22：招式伤害 = 主角基础物伤 × 客户端 powrate；
    #   存档属性漂移会变——从服务端实际状态推导，不写死 47/50。
    assert check_wstate(data, wid=1, hurt=0, life=0, ward=1,
                        hp_chg=srv._magic_damage(magic_id)), data.hex()
    # 招式消耗：出手后服务端结算 FP/MP 扣减 PropChange(17)（m7 同款处理），
    # 先抽干再进敌方回合；读到的非 17 opcode 经 first_op 复用，不重复读。
    op, _ = recv_s2c(c)
    while op == 17:
        op, _ = recv_s2c(c)
    # ★ M5 敌方回合紧随其后：`_enemy_turn` 自己会先发 WarTurn 开场帧。
    #   注意此时 1 号掉了 50 HP（100→50）**仍然存活**，故三只都会行动。
    expect_enemy_turn(c, alive=[1, 2, 3], first_op=op)
    steps.append('招式→Perform(122)+WState(powrate伤害)+敌方回合×3')

    # ⑦ 战斗道具 / 召唤的**合法性**（2026-10-23 语义收紧）
    #    ★ 现在分两条路：
    #      · 非法指令（道具不存在/用不上、召唤不成立）→ 只回一帧 WarTurn(0) 交还操作权，
    #        **不消耗本回合**（旧实现无条件推进，等于「点错一次白送敌方一轮」）；
    #      · 合法指令 → 走 _resolve_hero_round 按速度序推进整回合。
    #    先验非法路径（道具 id=1 不存在、本场无宠物可召回），再验合法路径（给自己嗑药）。
    send_c2s(c, 165, struct.pack('<BI', 1, 1))     # 目标=敌方 1、道具 id=1（不在背包）
    op, _ = recv_s2c(c)
    assert op == 114, ('非法战斗道具应只回 WarTurn', op)
    send_c2s(c, 166, struct.pack('<BB', 1, 0))     # 召回，但本场 _pet_alive=False
    op, _ = recv_s2c(c)
    assert op == 114, ('无宠物可召回应只回 WarTurn', op)
    # 合法道具：2007（shape 12，回血 160）给自己用 → 先出消耗帧，再推进整回合
    send_c2s(c, 165, struct.pack('<BI', m.HERO_WAR_ID, 2007))
    item_ops = []
    while True:
        op, _ = recv_s2c(c)
        if op == 114:
            break
        item_ops.append(op)
    assert 17 in item_ops and 35 in item_ops, ('道具生效应出 PropChange+ItemQuantity', item_ops)
    expect_enemy_turn(c, alive=[1, 2, 3], first_op=114)
    # 防御：与攻击/招式一致，推进到敌方回合（此时 1 号仍在，三只都会行动）；
    #   防御姿态下本回合受击伤害减半、ward=DEFEND
    send_c2s(c, 163)
    expect_enemy_turn(c, alive=[1, 2, 3], defending=True)
    steps.append('非法道具/召唤→只回 WarTurn；合法道具→整回合；防御→敌方回合（伤害减半）')

    # ⑧ 击杀 1 号：它在步骤⑥已被招式打掉 50 HP（100→50），故本击即致死。
    #    这一击同时验证「致死帧」——life 必须 == LIFE_DEAD(2)，客户端才会播死亡动画。
    send_c2s(c, 160, struct.pack('<B', 1))         # 目标 id=1
    op, _ = recv_s2c(c)                            # AttackNormal(117)
    assert op == 117, op
    op, data = recv_s2c(c)                         # WState(123)
    assert op == 123, op
    assert check_wstate(data, 1, 0, m.LIFE_DEAD, m.WARD_HIT,
                     srv._hero_attack_damage()), data.hex()
    op, _ = recv_s2c(c)                            # AttackNormalEnd(119)
    assert op == 119, op
    op, _ = recv_s2c(c)                            # WarDel(116)
    assert op == 116, op
    # 敌方回合紧随 WarDel（_enemy_turn 自带开场 WarTurn）
    expect_enemy_turn(c, alive=[2, 3])
    steps.append('击杀1号→WState(life=DEAD)+WarDel+敌方回合')

    # ⑨ 换目标打 2 号、3 号（验证 target 解析）：各 100 HP，需 2 击。
    #    alive 需动态计算（1 号已死；每只被击杀后从存活集移除）。
    alive = [2, 3]
    for wid in (2, 3):
        for shot in (1, 2):
            send_c2s(c, 160, struct.pack('<B', wid))
            op, _ = recv_s2c(c)
            assert op == 117, op
            op, data = recv_s2c(c)
            assert op == 123, op
            want_life = m.LIFE_NORMAL if shot == 1 else m.LIFE_DEAD
            assert check_wstate(data, wid, 0, want_life, m.WARD_HIT,
                     srv._hero_attack_damage()), (wid, shot, data.hex())
            op, _ = recv_s2c(c)
            assert op == 119, op
            op, _ = recv_s2c(c)
            if shot == 1:
                # 存活 → 直接进入敌方回合；已读到的 op 即敌方回合的开场 WarTurn
                expect_enemy_turn(c, alive=list(alive), first_op=op)
            else:
                assert op == 116, op               # 致死 → WarDel
                alive.remove(wid)
                if alive:
                    # 敌方回合开始（剩余存活单位）
                    expect_enemy_turn(c, alive=list(alive))
                else:
                    # 全灭：WarEnd(113) → [可能先补宠物帧 48] → SceneM(96) → AddNpc(99)×k → GotoM(97)
                    # ★ 2026-10-19：AddNpc 帧数随地图 NPC 数变（永泽城 3 个），
                    #   原写死「读 3 帧 == [96,99,97]」只对 1-NPC 图成立；
                    #   _resync_scene 还会先补 S2CSummonInfo(48)——先扫到 96 再收集到 97。
                    op, _ = recv_s2c(c)
                    assert op == 113, op
                    tail = []
                    while True:
                        op, _ = recv_s2c(c)
                        if op == 96:
                            tail.append(op)
                            break
                    while True:
                        op, _ = recv_s2c(c)
                        tail.append(op)
                        if op == 97:
                            break
                    assert tail[0] == 96 and tail[-1] == 97, tail
                    assert all(o == 99 for o in tail[1:-1]), tail
    steps.append('逐目标击杀2/3号→全灭WarEnd+场景重同步')

    # ⑩ NPC 点击 → 对话
    # ★ 2026-10-19：全灭后服务端补发胜利经验 PropChange(17, m_Exp)——先抽干再点 NPC，
    #   否则第一帧读到的是经验帧而非对话回包（_award_battle_exp）。
    drain_quiet(c, quiet=0.4)
    send_c2s(c, 96, struct.pack('<I', 1000))
    op, data = recv_s2c(c)
    assert op == 152 and '你好' in data.decode('gbk', 'replace'), (op, data.hex())
    steps.append('NPC 对话')

    # ⑪ NPC 选项 1 → 接任务 + 下一段对话
    send_c2s(c, 98, struct.pack('<I', 1))
    op, _ = recv_s2c(c)
    assert op == 72, op                     # S2CTaskAdd
    op, _ = recv_s2c(c)
    assert op == 152, op                    # 下一段对话
    steps.append('接任务')

    # ⑫ 任务查询
    send_c2s(c, 48)
    op, _ = recv_s2c(c)
    assert op == 72, op                     # S2CTaskAdd
    steps.append('任务查询')

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
    print('PASS：%s' % ' → '.join(steps))
    print('全部 %d 步断言通过' % len(steps))


if __name__ == '__main__':
    run()
