# -*- coding: utf-8 -*-
"""M5 补充验证：主角战败路径 + 录像对齐的 6 敌方单位。

主自测 _e2e_m4.py 走的是「玩家获胜」路径，本脚本覆盖：
  ① 敌方多到主角被打死 → 必须 WarDel×N + WarEnd + 场景重同步（且不卡「请等待」）
  ② --enemies 6（对齐客户端录像中场上 6+ 只「恶魔·分身」）
"""
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, 'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m
from _e2e_m4 import recv_s2c, send_c2s, hello_resp, login_body, move_body
from _e2e_m4 import tmp_savedir, drain_quiet

SEED = 0x12345678


def bring_to_war(c, port, enemies, srv=None):
    """跑到「遇怪」并返回进入战斗时的敌方 id 列表（c 须为已连接 socket）。

    srv：传服务端实例，则会在「进世界之后、移动之前」把宠物请出战斗（见下方注释）。
    """
    op, data = recv_s2c(c)
    assert op == 1
    c.sendall(hello_resp(SEED))
    send_c2s(c, 1, login_body())
    op, _ = recv_s2c(c)
    assert op == 2
    send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    for _ in range(3):
        recv_s2c(c)
    send_c2s(c, 2, struct.pack('<I', 1))
    # ★ 2026-10-19：进世界帧数随存档状态变（招式=基线+后学、NPC 数、背包件数），
    #   原写死 range(8) 在存档漂移后必然错位——改成抽干积压再继续（见 drain_quiet）。
    drain_quiet(c)
    if srv is not None:
        # ★ 宠物请出战斗（有效位置）：起线程前设的会被登录/建角复位——载入共享存档走
        #   _apply_save()、新建角色走 _reset_character()，两者都把 active_pet_id/pet_hp
        #   改回「宠物出战」。必须在进世界之后再设一次，_start_war() 重算 _pet_alive
        #   才会是 False；否则宠物进场、每个英雄动作后挂起等 op168，帧序全乱。
        srv._pet_alive = False
        srv.active_pet_id = 0
        # ★2026-10-22：暗雷只在场景表内的野外/迷宫触发，起始图是城镇 → 不遇怪。
        #   搬到一张有暗雷的野外图（不产生帧，不影响帧序断言）。
        srv.mapid = next(mid for mid in m.MAP_LINKS[m.START_MAP_ID]
                         if mid in m.ENCOUNTER_BY_MAP)
        srv._arrive_xy = None
    ids = []
    for i in range(1, 4):
        send_c2s(c, 54, move_body(45 + i, 40))
        op, _ = recv_s2c(c)
        assert op == 86
        if i < 3:
            continue
        op, data = recv_s2c(c)
        assert op == 17, op                  # 主角血量重置
        while True:                         # 之后 5 项战斗资源回满（m_MP/m_FP/m_At/m_Df/m_Dp）
            op, _ = recv_s2c(c)
            if op != 17:
                break
        assert op == 112, op                 # WarStart
        op, _ = recv_s2c(c)
        assert op == 115                     # 我方
        for _ in range(enemies):
            op, _ = recv_s2c(c)
            assert op == 115                 # 敌方
            ids.append(None)
        op, _ = recv_s2c(c)
        assert op == 114                     # WarTurn
    return ids


def test_defeat(port, enemies, srv=None):
    """主角被围殴致死：验证 WarDel×N + WarEnd + 场景重同步（不卡「请等待」）。"""
    c = socket.create_connection(('127.0.0.1', port))
    bring_to_war(c, port, enemies, srv)
    # 每回合「防御」，让敌方自由攻击直到主角倒地
    rounds = 0
    ended = False
    while rounds < 40:
        send_c2s(c, 163)                     # 防御 → 服务端推进敌方回合
        rounds += 1
        saw_end = False
        tail = []
        op, _ = recv_s2c(c)
        assert op == 114, ('回合开始', op)
        while True:
            op, _ = recv_s2c(c)
            if op == 114:                    # 回合结束（交还操作权）
                break
            if op == 116:                    # 主角倒地 → 服务端逐个 WarDel 清场
                continue
            if op == 113:                    # 战败 → WarEnd
                saw_end = True
                # ★ 2026-10-19：战后场景重同步 = [可能先补宠物帧 48] → SceneM(96)
                #   → AddNpc(99)×k（随地图 NPC 数变）→ GotoM(97)。先扫到 96 开头，
                #   再收集到 97 验结构（原写死「读 3 帧 == [96,99,97]」只对 1-NPC 图成立）。
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
                break
            assert op == 117, ('AttackNormal', op)
            for _ in range(4):               # WState 125 119 17
                recv_s2c(c)
        if saw_end:
            assert tail and tail[0] == 96 and tail[-1] == 97, tail
            assert all(o == 99 for o in tail[1:-1]), tail   # 中间全是 AddNpc
            ended = True
            break
    c.close()
    assert ended, '主角未在 %d 回合内被打倒（敌方 %d）' % (rounds, enemies)
    return rounds


def test_six_enemies(port, srv=None):
    """--enemies 6：对齐客户端录像中 6+ 只敌方单位，验证摆位与回合都成立。"""
    c = socket.create_connection(('127.0.0.1', port))
    ids = bring_to_war(c, port, 6, srv)
    assert len(ids) == 6, len(ids)
    # 打一发普攻，确认敌方回合有 6 个单位依次行动
    send_c2s(c, 160, struct.pack('<B', 1))
    op, _ = recv_s2c(c)
    assert op == 117
    for _ in range(2):                       # WState 119
        recv_s2c(c)
    op, _ = recv_s2c(c)
    assert op == 114, op                     # 敌方回合开场
    acted = 0
    while True:
        op, _ = recv_s2c(c)
        if op == 114:
            break
        assert op == 117, op
        acted += 1
        for _ in range(4):
            recv_s2c(c)
    c.close()
    return acted


def main():
    ok = True
    # ① 战败路径（敌方多、伤害高 → 3 回合内必倒：6×20=120/回合，200HP → 2 回合）
    port = 3141
    srv = m.M4Server('127.0.0.1', port, seed=SEED, encounter_every=3, enemies=6,
                     savedir=tmp_savedir('m4m5a_'))
    srv.move_count = 0          # 存档移动计数决定遇怪时机，归零保证「第 3 步遇怪」确定
    # ★ 2026-10-19：宠物请出战斗（同 _e2e_m4）——本脚本断言「敌方全瞄主角」的
    #   战败回合数与 6 敌依次行动，宠物参战后目标随机、且挂起等宠物指令会打乱序列。
    #   宠物流由 _e2e_m7.py 覆盖。
    #   ⚠ 抑制**不在这里**设：登录/建角会复位 active_pet_id，此处赋值必被覆盖；
    #     实际在 bring_to_war() 里「进世界之后、移动之前」设，故把 srv 传进去。
    # 关掉随机分支（敌方招式 122 / 会心红字）保证逐帧断言确定（同 _e2e_m4）
    srv.enemy_skill_chance = 0.0
    srv.enemy_hurt_bs_chance = 0.0
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.4)
    try:
        r = test_defeat(port, 6, srv)
        print('① 战败路径 PASS（%d 回合后主角倒地 → WarEnd + SceneM/AddNpc/GotoM）' % r)
    except AssertionError as e:
        ok = False
        print('① 战败路径 FAIL:', e)

    # ② 6 敌方单位
    port2 = 3142
    srv2 = m.M4Server('127.0.0.1', port2, seed=SEED, encounter_every=3, enemies=6,
                      savedir=tmp_savedir('m4m5b_'))
    srv2.move_count = 0         # 同上：归零保证遇怪时机确定
    # 宠物请出战斗：同 srv——在 bring_to_war() 内、进世界之后再设（此处设会被登录复位）
    srv2.enemy_skill_chance = 0.0   # 确定性：关敌方随机招式/会心（同 srv）
    srv2.enemy_hurt_bs_chance = 0.0
    threading.Thread(target=srv2.run, daemon=True).start()
    time.sleep(0.4)
    try:
        n = test_six_enemies(port2, srv2)
        assert n == 6, n
        print('② 6 敌方单位 PASS（6 只依次行动）')
    except AssertionError as e:
        ok = False
        print('② 6 敌方单位 FAIL:', e)

    print('RESULT:', 'PASS' if ok else 'FAIL')
    # ★ 显式收尾（2026-10-20）：本脚本起了**两个** daemon 服务端线程，
    #   必须在解释器 finalizing 之前结束它们。否则线程若正卡在 log() 的 print 里，
    #   会与关闭流程争 stdout 锁 → Fatal Python error: _enter_buffered_busy
    #   → 进程 0xC0000409（测试其实已 PASS，崩在收尾）。实测 27% → 0%。
    for _s in (srv, srv2):
        try:
            _s.stop()
        except Exception:                  # noqa: BLE001
            pass
    for _th in threading.enumerate():
        if _th is not threading.current_thread() and _th.daemon:
            _th.join(timeout=2.0)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
