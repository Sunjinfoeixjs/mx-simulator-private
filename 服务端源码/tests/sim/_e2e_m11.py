# -*- coding: utf-8 -*-
"""M11 端到端冒烟：NPC 任务交互(100) / 物品详情(101) 全链路，真实 socket 与线上字节。

逐条断言：
  ① C2S_NPC_GETDETAILS(101, pos=0) → S2CNpcDetails(151)，
     字节被 tap.OVERLAY[151] 干净反解（pos=0、desc 含「小麦种子」）——
     build_*(服务端写) == OVERLAY(调试台读) == Unpack*(客户端读) 三角一致
  ② 未接任务时 C2S_NPC_TASK(100) 不造数据（无 TaskAdd 帧）
  ③ 点村长(96) → 选项 1(98) 接任务 → TaskAdd(72)；再发 op100 → 重发 TaskAdd(72)
  ④ op101 越界格位 → op151 仍应答（desc=「没有这个商品」），客户端面板不悬挂

用法：python _e2e_m11.py
"""
import os
import socket
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m
import tap
import _e2e_m4 as E          # 复用收发/握手工具（该模块导入无副作用）

PORT = 3139
SEED = 0x0BADF00D

steps = []


def expect(c, op_want, what):
    op, data = E.recv_s2c(c)
    assert op == op_want, '%s: 期望 op=%d 实得 %d (%s)' % (what, op_want, op, data.hex())
    return data


def expect_none(c, op_want, what, wait=0.6):
    """wait 秒内不应出现 op_want 帧（出现即断言失败；超时=符合预期）。"""
    c.settimeout(wait)
    try:
        while True:
            op, _data = E.recv_s2c(c)
            assert op != op_want, '%s: 不该收到 op=%d' % (what, op_want)
    except socket.timeout:
        pass
    finally:
        c.settimeout(6)


def decode_151(data):
    rows, tail, note, consumed = tap.decode_fields(tap.OVERLAY['S2C'][151], data)
    assert not tail and not note and consumed == len(data), \
        'op151 反解不干净: tail=%s note=%s %d/%d' % (tail, note, consumed, len(data))
    return rows


def drain(c, wait=0.6):
    """读掉当前已到达的全部帧（登录应答等背景帧）。"""
    c.settimeout(wait)
    try:
        while True:
            E.recv_s2c(c)
    except socket.timeout:
        pass
    finally:
        c.settimeout(6)


def run():
    tmp = tempfile.mkdtemp(prefix='m4m11_')
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
    drain(c)                            # 读掉登录应答（op2 账号信息/角色列表）

    # ① 物品详情（面板数据服务端自持，未进世界也可应答）
    E.send_c2s(c, m.C2S_NPC_GETDETAILS, struct.pack('<I', 0))
    data = expect(c, m.S2C_NPC_DETAILS, '① 物品详情')
    rows = decode_151(data)
    assert rows[0]['value'] == 0 and '小麦种子' in rows[1]['value'], rows
    steps.append('① op101(pos=0) → S2CNpcDetails(151)「%s」，OVERLAY 反解 %dB 吃满'
                 % (rows[1]['value'], len(data)))

    # ② 未接任务：C2SNpcTask 不造数据
    E.send_c2s(c, m.C2S_NPC_TASK, struct.pack('<H', m.TASK_NEWBIE_ID))
    expect_none(c, m.OP_TASK_ADD, '② 未接任务不应答')
    steps.append('② 未接任务 op100 → 无 TaskAdd（正确不造数据）')

    # ③ 接任务闭环：点村长 → 选项 1 → TaskAdd；op100 重发 TaskAdd
    E.send_c2s(c, m.C2S_NPC_LOOK, struct.pack('<I', m.NPC_ID))
    expect(c, m.OP_NPC_CHAT_EXT, '③ 村长对话')
    E.send_c2s(c, m.C2S_NPC_ANSWER, struct.pack('<I', 1))
    expect(c, m.OP_TASK_ADD, '③ 接任务 TaskAdd')
    E.recv_s2c(c)                       # 同分支还回一帧 152（NPC_CHAT_TASK_OK），读掉
    E.send_c2s(c, m.C2S_NPC_TASK, struct.pack('<HB', m.TASK_NEWBIE_ID, 1))
    data = expect(c, m.OP_TASK_ADD, '③ op100 重发 TaskAdd')
    assert len(data) >= 2, 'TaskAdd 帧体异常'
    steps.append('③ 接任务后 op100 → S2CTaskAdd(72) 重发（同 tid 客户端先 Remove 再重建）')

    # ④ 放弃任务（C2S_GIVEUP_TASK 49：flag(1)+taskid(2)）→ S2CTaskRemove(73)
    #    不处理的后果：客户端点「放弃」后任务栏不消失、击杀还暗涨进度（状态分叉）
    E.send_c2s(c, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, m.TASK_NEWBIE_ID))
    data = expect(c, m.OP_TASK_REMOVE, '④ 放弃任务 → TaskRemove')
    assert struct.unpack('<H', data[:2])[0] == m.TASK_NEWBIE_ID, data.hex()
    steps.append('④ op49 放弃任务 → S2CTaskRemove(73) tid=%d' % m.TASK_NEWBIE_ID)

    # ⑤ 越界格位也要应答，客户端 UpdateDetails 才不悬挂
    # （pos 线上为 1B —— 客户端 UnpackInt(1)，面板格位恒 <256，不会截断）
    E.send_c2s(c, m.C2S_NPC_GETDETAILS, struct.pack('<I', 99))
    data = expect(c, m.S2C_NPC_DETAILS, '⑤ 越界格位')
    rows = decode_151(data)
    assert rows[0]['value'] == 99 and '没有这个商品' in rows[1]['value'], rows
    steps.append('⑤ op101(pos=99 越界) → op151「%s」' % rows[1]['value'])

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
    print('M11 端到端冒烟 PASS：')
    for s in steps:
        print('  ✓ %s' % s)
    return 0


if __name__ == '__main__':
    sys.exit(run())
