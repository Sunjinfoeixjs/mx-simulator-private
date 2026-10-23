# -*- coding: utf-8 -*-
"""纯探索模式冒烟：连指定端口(--no-encounter 服) → 握手 → 登录 → 进世界 →
移动(确认不遇怪) → 开商店(op147) → 购买(op99→op33 入背包)。逐帧验证服务端在跑。
用法：python _test_explore.py [port]
"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', (int(sys.argv[1]) if len(sys.argv) > 1 else 3134)
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
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


def collect(c, maxf=200, window=3.0):
    frames = []
    try:
        for _ in range(maxf):
            op, data = recv_s2c(c, window)
            frames.append((op, data))
    except (socket.timeout, ConnectionError):
        pass
    return frames


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    collect(c, 6, 0.3)
    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    collect(c, 6, 0.3)
    send_c2s(c, 3, '探索测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    collect(c, 6, 0.3)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    time.sleep(0.5)
    wf = collect(c, 60, 0.5)

    # 进世界帧校验
    ops = [o for o, _ in wf]
    from collections import Counter
    print('   世界帧 opcodes:', sorted(Counter(ops).items()))
    check('进世界含 SceneM(96)', m.OP_MAP_SCENE_M in ops)
    check('进世界含 AddNpc(99)', m.OP_MAP_ADD_NPC in ops)
    item_adds = [d for o, d in wf if o == m.OP_ITEM_ADD]
    check('进世界下发背包 ItemAdd(≥8)', len(item_adds) >= 8, 'got=%d' % len(item_adds))
    check('进世界含宠物 SummonInfo(op48)', 48 in ops, 'op48=%s' % (48 in ops))
    check('进世界含宠物技能格(op49)', 49 in ops)
    # 资质(op51) 由客户端打开面板时以 C2S_SUMM_VIEW(129) 主动请求 → 再回发
    send_c2s(c, 129, struct.pack('<I', m.PET_ID))
    time.sleep(0.2)
    vf = collect(c, 20, 0.3)
    vops = [o for o, _ in vf]
    check('请求宠物资质 C2S_SUMM_VIEW(129) → 回发 op51', 51 in vops, 'op51=%s' % (51 in vops))

    # 移动 5 次（纯探索：绝不应触发战斗），累计所有移动帧
    move_frames = []
    for i in range(5):
        send_c2s(c, m.C2S_MAP_MOVE, struct.pack('<HH', 45 + i, 40) + b'\x00')
        time.sleep(0.2)
        move_frames += collect(c, 8, 0.2)
    time.sleep(0.4)
    post = collect(c, 12, 0.4)
    move_frames += post
    ops_post = [o for o, _ in move_frames]
    check('移动后【不】触发战斗（无 WarStart112）', m.OP_WAR_START not in ops_post)
    check('移动后【不】进入指令等待（无 WarTurn114）', m.OP_WAR_TURN not in ops_post)
    # 移动应由服务端回显 MapTrack(86)
    check('移动收到 MapTrack(86) 回显', m.OP_MAP_TRACK in ops_post)

    # 开商店：点击 NPC(96)
    send_c2s(c, m.C2S_NPC_LOOK, struct.pack('<I', m.NPC_ID))
    time.sleep(0.3)
    nf = collect(c, 30, 0.4)
    nops = [o for o, _ in nf]
    check('NPC 对话帧 NPC_CHAT_EXT(152)', m.OP_NPC_CHAT_EXT in nops)
    check('初次对话不误开商店', m.S2C_NPC_BUYLIST not in nops)

    # 选项 2 明确打开购买面板
    send_c2s(c, m.C2S_NPC_ANSWER, struct.pack('<I', 2))
    time.sleep(0.2)
    shop = collect(c, 10, 0.3)
    check('NPC 选项 2 打开商店 S2CNpcBuyList(147)',
          any(o == m.S2C_NPC_BUYLIST for o, _ in shop))

    # 购买 index=2（幻灵珠 shape=10411）×1 → 应新增 1 件 ItemAdd(33)
    send_c2s(c, m.C2S_NPC_BUY, struct.pack('<HH', 2, 1))
    time.sleep(0.3)
    bf = collect(c, 30, 0.4)
    bops = [o for o, _ in bf]
    new_adds = [d for o, d in bf if o == m.OP_ITEM_ADD]
    check('购买后下发新道具 ItemAdd(33)', len(new_adds) >= 1, 'new=%d' % len(new_adds))
    check('购买后回确认聊天(152)', m.OP_NPC_CHAT_EXT in bops)

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
