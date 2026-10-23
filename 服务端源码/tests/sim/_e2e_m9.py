# -*- coding: utf-8 -*-
"""M9 冒烟测试：养成闭环（升级 / 加点 / 学招式），全部走真实 socket 与线上字节。

逐条断言：
  ① 进世界帧序：PropInit(16) → Resist(18) → Power(19) → 养成同步 PropChange(17)×10
     → 招式 65×16 → SceneM(96) → AddNpc(99)×k → GotoM(97)
  ② 升级 C2S 17（空包）→ PropChange(m_Grade=5)=2 / m_Exp=8 / m_NextExp=9 / m_Point=37
  ③ 加点 C2S 16（体+2 力+1）→ PropChange(36/32/37)，且五项顺序 = 体/力/魔/魅/敏
  ④ 点战斗技能师(1002) → 152 菜单；选项 1 → S2CNpcLearnList(149)，含 1104-1109
  ⑤ 学招式 C2S 103(1104) → PropChange(m_Gold=43) + S2CSkillLevel(65) sktype=2 与 7
  ⑥ 包在战斗中也不能被「战斗保底」吞掉：开战后发 16/17，仍按养成处理

用法：python _e2e_m9.py
"""
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m
import _e2e_m4 as E          # 复用收发/握手工具（该模块导入无副作用）

PORT = 3138
SEED = 0x0BADF00D

steps = []


def expect(c, op_want, what):
    op, data = E.recv_s2c(c)
    assert op == op_want, '%s: 期望 op=%d 实得 %d (%s)' % (what, op_want, op, data.hex())
    return data


def drain(c, timeout=0.4):
    """把当前已经到达的帧全部读掉，返回 [(op, body)]。"""
    out = []
    c.settimeout(timeout)
    try:
        while True:
            op, data = E.recv_s2c(c)
            out.append((op, data))
    except (socket.timeout, ConnectionError):
        pass
    finally:
        c.settimeout(6)
    return out


def prop_map(frames):
    """把 PropChange 帧解成 {ITEM_ALL 下标: 值}（帧体 = type(1) + value(变宽)）。"""
    w = {1: 1, 5: 1, 8: 4, 9: 4, 32: 2, 33: 2, 34: 2, 35: 2, 36: 2, 37: 2, 43: 4}
    out = {}
    for op, body in frames:
        if op != m.OP_PROP_CHANGE:
            continue
        t = body[0]
        out[t] = int.from_bytes(body[1:1 + w.get(t, len(body) - 1)], 'little')
    return out


def run():
    tmp = tempfile.mkdtemp(prefix='m4m9_')
    srv = m.M4Server('127.0.0.1', PORT, seed=SEED, encounter_every=0, enemies=1,
                     turn_timeout=30, logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    srv._loaded = None
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.4)

    c = socket.create_connection(('127.0.0.1', PORT), timeout=6)
    op, data = E.recv_s2c(c)
    assert op == 1, op
    c.sendall(E.hello_resp(SEED))
    E.send_c2s(c, 1, E.login_body())
    op, data = E.recv_s2c(c)
    assert op == 2 and data[0] == 0, (op, data.hex())
    E.send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    expect(c, 6, '建角')
    # 建角后服务端还会补一帧 AccountInfo(2)；离开角色列表时又补 RoleInfo(3)。
    # 这两帧与养成无关，吞掉即可（真实客户端也是自己消费）。
    drain(c, 0.25)
    E.send_c2s(c, 2, struct.pack('<I', 1))
    while True:
        op, data = E.recv_s2c(c)
        if op == 16:
            break
        assert op in (2, 3, 6), ('进世界前出现意外帧 op=%d' % op)

    # ① 进世界帧序（上一轮循环已经把 PropInit(16) 读掉了）
    expect(c, 18, 'PropResist')
    expect(c, 19, 'PropPower')
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
    n_magic = 0
    while op == 65:
        # 2=学招式 / 7=装备；1 是生活技能，不计入招式帧
        if data[0] in (2, 7):
            n_magic += 1
        op, data = E.recv_s2c(c)
    assert n_magic == 2 * len(m.TEACH_MAGIC_IDS), n_magic
    assert op == 96, op
    while True:                              # 跳过 AddNpc(99)×k 与随行宠物(105)，等到 GotoM(97)
        op, data = E.recv_s2c(c)
        if op not in (99, 105):
            break
    assert op == 97, op
    drain(c, 0.3)
    steps.append('进世界：养成同步 %d 帧 + 招式 %d 帧' % (len(prog), n_magic))

    # ② 升级：先给足经验（服务端 1000 经验门槛）
    srv.exp = srv.next_exp
    E.send_c2s(c, 17, b'')
    fr = drain(c)
    p = prop_map(fr)
    assert p.get(5) == 2, ('m_Grade', p)
    assert p.get(8) == 0, ('m_Exp', p)
    assert p.get(9) == m.NEXT_EXP_INIT * 2, ('m_NextExp', p)
    assert p.get(37) == m.POINT_INIT + m.POINT_PER_LEVEL, ('m_Point', p)
    steps.append('升级 → Lv2，点数 %d，下一级 %d' % (p[37], p[9]))

    # ③ 加点：体(36)+2 / 力(32)+1
    E.send_c2s(c, 16, bytes([2, 1, 0, 0, 0]))
    p = prop_map(drain(c))
    assert p.get(36) == m.ATTR_BASE + 2 and p.get(32) == m.ATTR_BASE + 1, p
    assert p.get(37) == m.POINT_INIT + m.POINT_PER_LEVEL - 3, p
    steps.append('加点：体 %d 力 %d，余点数 %d' % (p[36], p[32], p[37]))

    # ④ 找战斗技能师：152 菜单 → 选项 1 → 149 课表
    E.send_c2s(c, 96, struct.pack('<I', m.TEACHER_NPC_ID))
    data = expect(c, 152, '技能师菜单')
    assert m.TEACHER_NPC_NAME.encode('gbk') in data, data.hex()
    E.send_c2s(c, 98, struct.pack('<I', 1))
    data = expect(c, 149, '教招式列表')
    mode, icost = struct.unpack('<BH', data[:3])
    sids, i = [], 3
    while True:
        skid = struct.unpack('<H', data[i:i + 2])[0]
        if skid <= 0:
            break
        sids.append(skid)
        i += 5
    # ★ 2026-10-22：课表现在 = 人物招式 + **剧情技能**（剧情点花在这里，
    #   见 m4_server._hero_learn_juqing）。客户端 dlglearnskill 对 2001–2029
    #   自动把花费列换成 m_StoryPoint，所以两条入口共用一张列表。
    #   断言拆成两段：招式段必须与 LEARN_MAGIC_IDS 逐一对上，剧情技能段只要
    #   落在 2001–2029 区间内且非空——不逐条写死，免得 juqingdata 一改就红。
    assert sids[:len(m.LEARN_MAGIC_IDS)] == list(m.LEARN_MAGIC_IDS), sids
    _jq = sids[len(m.LEARN_MAGIC_IDS):]
    assert _jq and all(2001 <= s < 2030 for s in _jq), _jq
    assert mode == 0 and icost == 0, (mode, icost)
    steps.append('课表 %d 门：招式 %s + 剧情技能 %d 门'
                 % (len(sids), sids, len(_jq)))

    # ⑤ 学 1104
    gold_before = srv.coins
    E.send_c2s(c, 103, struct.pack('<H', 1104))
    fr = drain(c, 0.5)
    p = prop_map(fr)
    skills = [b for o, b in fr if o == 65]
    assert p.get(43) == gold_before - m.M4Server._learn_cost(1), (p.get(43), gold_before)
    assert len(skills) == 2, [s.hex() for s in skills]
    assert skills[0][0] == 2 and struct.unpack('<H', skills[0][1:3])[0] == 1104, skills[0].hex()
    assert skills[1][0] == 7, skills[1].hex()
    steps.append('学 1104：扣 %d 金，回 65×2（学 %d 级 + 装备）'
                 % (m.M4Server._learn_cost(1), skills[0][3]))

    # ⑥ 分派顺序：养成分支必须排在「战斗保底」之前。
    #   若排错了，战斗态下的 C2S 17 会落进保底分支、回一帧 WarTurn(114)。
    #   这里只置标志、不真开战（真开战的帧量与本项无关），经验已清零 → 升级必然被拒。
    srv.in_war = True
    srv.await_cmd = True
    srv.cmd_deadline = time.time() + 30      # ★ 别让回合超时线程插进来发默认普攻
    srv.enemy_hp = {1: 100}
    E.send_c2s(c, 17, b'')
    fr = drain(c, 0.35)
    got_ops = [o for o, _ in fr]
    assert 114 not in got_ops, got_ops
    assert 123 not in got_ops, got_ops
    srv.in_war = False
    srv.await_cmd = False
    srv.enemy_hp = {}
    steps.append('战斗态下 17 未被战斗保底吞掉（无 WarTurn/WState 回声）')

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
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    run()
    print()
    print('PASS：' + ' → '.join(steps))
    print('全部 %d 步断言通过' % len(steps))
