# -*- coding: utf-8 -*-
"""协议级测试：战斗深化（施法消耗扣减 / 战斗道具 / 保护推进）。

自带内核：本脚本**自己起一个临时模拟服**（FRESH 存档，故 2007 战斗道具数量恒为 5），
不再依赖外部先起服——这样才能进 `sim/tests/_run_all.py` 一键回归。
登录 → 进世界 → 移动 3 次遇怪 →
  C2S_WAR_PERFORM(161) 主角首式 → 校验消耗，再提交宠物指令完成回合
  C2S_WAR_PROTECT(167) → 校验敌方行动帧，再提交宠物指令完成回合
  C2S_WAR_ITEM(165)    → 校验数量扣减，再提交宠物指令完成回合

用法：
  python tests/_test_war2.py                 # 默认：自带内核（端口 3143，FRESH 临时存档）
  python tests/_test_war2.py --connect       # 连手动起的 3140（不起自带内核）
  python tests/_test_war2.py --connect 3134  # 连指定端口
连手动服时**不保证**下列前提，个别断言可能红：
  · 该服开局 2007 战斗道具数量 = 5（只对干净存档成立；自带内核用 FRESH 存档恒成立）
  · 该服按 --enemies 6 / --encounter-every 3 起（否则 3 回合流程走不完）
  · 关掉随机分支（--no-enemy-skill），否则敌方改放招式会打乱逐帧断言
已验证的绿配方（2026-10-19，实测 9/9——`--connect` 会跳过「自带内核」那条，故比默认少 1 项）：
  python sim/m4_server.py --port 3140 --enemies 6 --encounter-every 3 --save-dir <空目录> --no-enemy-skill
  python tests/_test_war2.py --connect 3140
"""
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

HOST, PORT = '127.0.0.1', 3143      # 3140 已被 _e2e_m10 占用，这里避开
sys.path.insert(0, 'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m  # noqa

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))


def recv_exact(c, n, timeout=3.0):
    c.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        ch = c.recv(n - len(buf))
        if not ch:
            raise ConnectionError('closed')
        buf += ch
    return buf


def recv_s2c(c, timeout=3.0):
    n = recv_exact(c, 1, timeout)[0]
    if n == 0xFF:
        n = struct.unpack('<H', recv_exact(c, 2, timeout))[0]
    payload = recv_exact(c, n, timeout)
    return payload[0], payload[1:]


def send_c2s(c, op, body=b''):
    c.sendall(bytes([op]) + bytes([len(body)]) + body)


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def collect_round(c, maxf=200, window=3.0):
    """收集一回合的帧，直到「交还操作权」的 WarTurn（其后无动作帧）。"""
    frames = []
    try:
        for _ in range(maxf):
            op, data = recv_s2c(c, window)
            frames.append((op, data))
            if op == m.OP_WAR_TURN:
                try:
                    nxt = recv_s2c(c, 0.5)
                    frames.append(nxt)
                    if nxt[0] == m.OP_WAR_TURN:
                        break
                except socket.timeout:
                    break
    except (socket.timeout, ConnectionError):
        pass
    return frames


def finish_pet_turn(c, frames, pet_op=None, pet_body=None):
    if any(op == m.OP_WAR_END for op, data in frames):
        return frames
    # ★ WarTurn(lastcmd=0) 有两种含义：「回合交还操作权（回合开始）」和「回合结束」。
    #   只凭它就提前返回，会在**回合刚轮到我方**时误判成已结束，把宠物指令吞掉
    #   （表现为「宠物技能 op169 → 宠物 WarPerform」永远拿不到帧）。
    #   真正的判据是：主角这一回合还没出手（本回合无主角的 WarPerform）且已翻篇。
    hero_acted = any(o == m.OP_WAR_PERFORM and d[:1] == bytes([m.HERO_WAR_ID])
                     for o, d in frames)
    if not hero_acted and any(op == m.OP_WAR_TURN and data[:1] == b'\x00'
                              for op, data in frames):
        return frames
    if pet_op is None:
        pet_op = m.C2S_WAR_SUMM_ATTACK
        pet_body = bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID])
    send_c2s(c, pet_op, pet_body)
    frames.extend(collect_round(c))
    return frames


def start_server(port):
    """自带内核：起临时模拟服（FRESH 存档 + 确定性随机分支），返回 (srv, tmpdir)。"""
    tmp = tempfile.mkdtemp(prefix='m4war2_')
    # ★2026-10-22：默认暗雷只在场景表内的野外/迷宫触发（城镇不遇怪）且出怪随图变化。
    #   本脚本逐帧断言的是**战斗深化**（出手/招式/防御/道具/宠物指令），
    #   故显式切到 legacy 模式（全图固定野狼）；遇怪表由 _test_encounter_map.py 覆盖。
    m.ENCOUNTER_PER_MAP = False
    srv = m.M4Server('127.0.0.1', port, seed=0x12345678, encounter_every=3, enemies=6,
                     logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    srv.move_count = 0              # 遇怪时机确定：第 3 步必遇
    srv.enemy_skill_chance = 0.0    # 关随机分支（敌方招式/会心），逐帧断言才稳
    srv.enemy_hurt_bs_chance = 0.0
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.5)
    return srv, tmp


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # 开关：--connect [port] → 连**已起的内核**（不传端口默认 3140，即手动起服惯用端口）；
    # 不带 --connect 则自带内核（端口 3143，FRESH 临时存档）。回归里默认走自带内核。
    connect_port = None
    if '--connect' in argv:
        i = argv.index('--connect')
        connect_port = int(argv[i + 1]) if (i + 1 < len(argv) and argv[i + 1].isdigit()) else 3140
    tmp = None
    if connect_port is None:
        srv, tmp = start_server(PORT)
        check('自带内核已启动（FRESH 临时存档 → 2007 数量恒为 5）', srv._loaded is None)
        host, port = HOST, PORT
    else:
        host, port = HOST, connect_port
        print('[用法] --connect：连已起的内核 %s:%d（不起自带内核、也不清临时档）' % (host, port))
        print('        该服需满足 2007 数量=5 / --enemies 6 / --encounter-every 3，否则个别断言会红')
    c = socket.create_connection((host, port), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    collect_round(c, 6, 0.3)
    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    collect_round(c, 6, 0.3)
    send_c2s(c, 3, '战斗测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    collect_round(c, 6, 0.3)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    time.sleep(0.4)
    collect_round(c, 20, 0.4)

    # 移动 3 次遇怪
    # ★ WarTurn 可能在**移动循环里那次 collect_round** 就已经收到了（战斗在第 3 步触发），
    #   只看最后一批会把「已经开战」误判成没开战——故整段累计。
    pre = []
    for i in range(3):
        send_c2s(c, m.C2S_MAP_MOVE, struct.pack('<HH', 45, 40) + b'\x00')
        time.sleep(0.25)
        pre += collect_round(c, 8, 0.2)
    time.sleep(0.5)
    # 帧数随敌方数变（WarStart + AddNpc 我方/宠物 + 敌方×N + WarTurn），给够再收
    pre += collect_round(c, 30, 0.5)
    # ★ 判「已开战」看 WarStart(112) 而不是 WarTurn(114)：WarStart 之后还有
    #   AddNpc(我方/宠物)×N + Summon(129) + 敌方×N 才轮到 WarTurn，一次性
    #   collect_round 的帧预算常常收到 112 就满了，等不到 114 → 误判成没开战。
    in_war = any(o == m.OP_WAR_START for o, _ in pre)
    check('移动 3 次后进入战斗（WarStart 已到）', in_war, 'frames=%d' % len(pre))

    # ── 主角首式（TEACH_MAGIC_IDS[0]）：施法消耗扣减 ──
    # ★ 宠物技能必须取自**宠物自己的**技能表（PET_SKILLS）；原先拿主角招式表里的值
    #   （TEACH_MAGIC_IDS[1]=1102）当宠物技能，服务端判「宠物指令无效，默认普攻」。
    # ★ 而且要取**主动**招式（type=1/2）：PET_SKILLS[0]=112「必杀」是被动，
    #   客户端不进宠物招式菜单，服务端按非法指令回落普攻 → 测不到 Perform(122)。
    _pet_sk = m.pet_active_skills()[0]
    send_c2s(c, m.C2S_WAR_PERFORM,
             bytes([m.HERO_WAR_ID]) + struct.pack('<H', m.TEACH_MAGIC_IDS[0]))
    fr = finish_pet_turn(
        c, collect_round(c), m.C2S_WAR_SUMM_PERFORM,
        bytes([m.ENEMY_WAR_ID]) + struct.pack('<H', _pet_sk))
    props = {}
    for o, d in fr:
        if o == m.OP_PROP_CHANGE and len(d) >= 2:
            idx = d[0]
            if idx in (m.PROP_IDX_MP, m.PROP_IDX_FP) and len(d) >= 3:
                # 2 字节属性（TWORD）：m_MP / m_FP
                props[idx] = struct.unpack('<H', d[1:3])[0]
            else:
                # 1 字节属性（TBYTE）：m_At / m_Df / m_Dp（及 HP 等）
                props[idx] = d[1]
    # ★ 期望值从服务端自己的「战斗资源基线 + 招式消耗表」推导，不写死 85/96
    #   （基线由 _reset_hero_res 在开战时刷成 hero_res_fresh()）
    _base = m.hero_res_fresh()
    _cost = (m.MAGIC_COST or {}).get(m.TEACH_MAGIC_IDS[0]) or {}
    _fp_want = _base['m_FP'] - int(_cost.get('m_FP', 0) or 0)
    _df_want = _base['m_Df'] - int(_cost.get('m_Df', 0) or 0)
    check('招式后 m_FP = %d' % _fp_want, props.get(m.PROP_IDX_FP) == _fp_want,
          'got=%s' % props.get(m.PROP_IDX_FP))
    check('招式后 m_Df = %d' % _df_want, props.get(m.PROP_IDX_DF) == _df_want,
          'got=%s' % props.get(m.PROP_IDX_DF))
    check('招式后 m_MP(18) 未变（mana=0）', m.PROP_IDX_MP not in props,
          'props=%s' % props)
    has_perform = any(o == m.OP_WAR_PERFORM for o, _ in fr)
    has_end_turn = any(o == m.OP_WAR_TURN for o, _ in fr)
    check('含 WarPerform(122) 且回合结束交还操作权', has_perform and has_end_turn,
          'perform=%s turn=%s' % (has_perform, has_end_turn))
    pet_perform = [d for o, d in fr if o == m.OP_WAR_PERFORM and d[0] == m.PET_WAR_ID]
    check('宠物技能 op169 → 宠物 WarPerform(122)',
          len(pet_perform) == 1
          and struct.unpack('<H', pet_perform[0][1:3])[0] == _pet_sk,
          'frames=%r' % pet_perform)

    # ── 保护（167）：应推进回合（敌方行动帧 + WarTurn）──
    # ★ 目标必须是存活友方：非法目标（敌方/自己/倒地宠物）现在会被判「行动非法」
    #   并只回 WarTurn 重开窗口，不消耗回合（见 _hero_war_protect）。
    send_c2s(c, m.C2S_WAR_PROTECT, bytes([m.PET_WAR_ID]))
    fr = finish_pet_turn(c, collect_round(c))
    atk = [d[0] for o, d in fr if o == m.OP_WAR_ATTACK_NORMAL]
    check('C2S_WAR_PROTECT(167) → 敌方行动帧 + WarTurn',
          len(atk) > 0 and any(o == m.OP_WAR_TURN for o, _ in fr),
          '敌方出手=%s' % atk)

    # ── 战斗道具（165：id(1)+item(4)）→ 扣数量 + 回合推进 ──
    send_c2s(c, m.C2S_WAR_ITEM, bytes([m.HERO_WAR_ID]) + struct.pack('<I', 2007))
    fr = finish_pet_turn(c, collect_round(c))
    q = [(struct.unpack('<I', d[:4])[0], d[4]) for o, d in fr if o == m.OP_ITEM_QUANTITY]
    check('C2S_WAR_ITEM(165) → S2CItemQuantity(35)：2007 数量 5→4',
          (2007, 4) in q, 'qty=%s' % q)
    check('用道具后回合推进（WarTurn）', any(o == m.OP_WAR_TURN for o, _ in fr),
          'frames=%d' % len(fr))

    c.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)  # 收工清临时存档目录（仅自带内核时有）
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
