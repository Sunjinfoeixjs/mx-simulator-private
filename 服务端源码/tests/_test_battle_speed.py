# -*- coding: utf-8 -*-
"""战斗级测试：出手顺序（速度序结算）+ 回合超时默认普攻。

在 127.0.0.1:3136 起 m4_server 实例（--enemies 4 --turn-timeout 8），
脚本客户端走完整战斗：
  1) 进世界 → 移动遇怪
  2) 发普攻 → 校验帧序：快位敌(1,2,3) → 主角(HERO_WAR_ID) → WarTurn → 慢位敌(4) → WarTurn
  3) 不发指令等超时 → 校验默认普攻帧出现（主角 AttackNormal）
  4) 防御 → 校验敌方仍按速度序行动且操作权交还
"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', 3136
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
    if len(body) >= 0xFF:
        c.sendall(bytes([op]) + b'\xFF' + struct.pack('<H', len(body)) + body)
    else:
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


def drain(c, window=0.3):
    out = []
    try:
        while True:
            out.append(recv_s2c(c, window))
    except (socket.timeout, ConnectionError):
        pass
    return out


def collect_until_turn(c, max_frames=200, frame_timeout=5.0):
    """收集帧直到遇到「交还操作权」的 WarTurn（回合结束标志）。"""
    frames = []
    turns = 0
    for _ in range(max_frames):
        op, data = recv_s2c(c, frame_timeout)
        frames.append((op, data))
        if op == m.OP_WAR_END:
            break
        if op == m.OP_WAR_TURN:
            turns += 1
            # 本回合最后一帧 WarTurn：其后客户端应重获菜单。
            # 判定：连续两帧 WarTurn 之间没有动作帧即回合结束。
            if turns >= 1 and frames[-2][0] != m.OP_WAR_TURN:
                # 再看下一帧是否为 WarTurn 或空闲
                try:
                    nxt = recv_s2c(c, 0.6)
                    frames.append(nxt)
                    if nxt[0] == m.OP_WAR_TURN:
                        break
                except socket.timeout:
                    break
    return frames


def collect_until_quiet(c, quiet_timeout=0.6, max_frames=200):
    """Collect actions sent before the server pauses for the pet command."""
    frames = []
    for _ in range(max_frames):
        try:
            frames.append(recv_s2c(c, quiet_timeout))
        except socket.timeout:
            break
    return frames


def finish_pet_action(c, frames):
    """Submit a pet attack only when the server is still waiting for its turn."""
    if any(op == m.OP_WAR_END or (op == m.OP_WAR_TURN and data[:1] == b'\x00')
           for op, data in frames):
        return frames
    send_c2s(c, m.C2S_WAR_SUMM_ATTACK,
             bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
    frames.extend(collect_until_turn(c))
    return frames


def attack_order(frames):
    """从帧序列提取 AttackNormal(117) 的攻方 id 序列。"""
    return [data[0] for op, data in frames if op == m.OP_WAR_ATTACK_NORMAL]


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    drain(c)

    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    drain(c)
    send_c2s(c, 3, '速度测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.2)
    drain(c)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    time.sleep(0.4)
    drain(c)

    # 移动 3 次触发遇怪（encounter-every=3）
    for i in range(3):
        send_c2s(c, m.C2S_MAP_MOVE, struct.pack('<HH', 45, 40) + b'\x00')
        time.sleep(0.25)
        drain(c, 0.2)

    # ── 回合 1：普攻，校验速度序 ──
    send_c2s(c, m.C2S_WAR_ATTACK, bytes([1]))   # 打敌 1（最快，115）
    # 客户端先提交主角指令，再提交宠物指令；宠物行动前不发最终 WarTurn。
    frames = finish_pet_action(c, collect_until_quiet(c))
    order = attack_order(frames)
    # 期望：1,2,3（快位敌）→ 主角 → 敌4（宠物之前）→ 宠物手动攻击。
    expect = [1, 2, 3, m.HERO_WAR_ID, 4, 250]
    check('速度序：快位敌先动→主角→慢位敌', order == expect,
          'got=%s expect=%s' % (order, expect))
    turn_idx = [i for i, (op, _d) in enumerate(frames) if op == m.OP_WAR_TURN]
    check('回合内 WarTurn 数量 = 2（慢位前+结束）', len(turn_idx) == 2,
          'turns=%s' % turn_idx)

    # ── 回合 2：超时（不发指令，等 > turn-timeout=8s）──
    t0 = time.time()
    frames = collect_until_turn(c, max_frames=300, frame_timeout=15.0)
    dt = time.time() - t0
    order = attack_order(frames)
    # 超时默认普攻：主角(HERO_WAR_ID) 出现在序列中，且整体仍按速度序
    has_hero = m.HERO_WAR_ID in order
    check('超时触发默认普攻（主角出手）', has_hero and dt >= 7,
          'order=%s dt=%.1fs' % (order, dt))

    # ── 回合 3：防御，校验操作权交还 ──
    # 注意：敌 1 已在「普攻 50 + 超时默认普攻 50」中被打死（100 HP），
    # 故本回合期望为剩余存活敌方的速度序 [2,3,4] + 出战宠物 250。
    send_c2s(c, m.C2S_WAR_DEFEND)
    frames = finish_pet_action(c, collect_until_quiet(c))
    order = attack_order(frames)
    check('防御回合：存活敌方按速度序行动', order == [2, 3, 4, 250],
          'order=%s' % order)

    # ── 打到战斗结束（验证多回合不卡）──
    for rnd in range(12):
        send_c2s(c, m.C2S_WAR_ATTACK, bytes([1]))
        try:
            frames = finish_pet_action(c, collect_until_quiet(c))
        except (ConnectionError, socket.timeout):
            break
        if any(op == m.OP_WAR_END for op, _d in frames):
            check('战斗可结束（WarEnd 到达）', True, 'round=%d' % rnd)
            break
    else:
        check('战斗可结束（WarEnd 到达）', False, '12 回合未结束')

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
