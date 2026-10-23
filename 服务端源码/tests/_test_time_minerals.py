# -*- coding: utf-8 -*-
"""协议级测试：服务器时间(15→15 / 61→192) / 黑名单(14) / 矿点(78→87 sub5)。"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', 3137
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


def drain(c, window=0.35):
    out = []
    try:
        while True:
            out.append(recv_s2c(c, window))
    except (socket.timeout, ConnectionError):
        pass
    return out


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    drain(c)

    # ── C2S_TIME(15) → S2C op15（core/mxgw.S2CTime）──
    # ★两条时间通道不同（net.py L164/L317）：mxgw 用 15 查询、必须回 15；
    #   且 op15 回 **10000Hz 单调滴答**（2026-10-16 真机修复：回 Unix 秒会触发
    #   mxgw 漂移检查 `cdt=ΔGetTime/100 > sdt=Δt+500` → 客户端断线 007；
    #   2026-10-20 起时钟源为 time.monotonic()——绝对值与墙钟无关，
    #   故这里不再比对墙钟，只验证通道正确 + 单调递增 + 量纲）。
    send_c2s(c, m.C2S_TIME, b'\x00')
    op, data = recv_s2c(c)
    tval1 = struct.unpack('<I', data[:4])[0] if len(data) >= 4 else 0
    time.sleep(0.2)
    send_c2s(c, m.C2S_TIME, b'\x00')
    op2, data2 = recv_s2c(c)
    tval2 = struct.unpack('<I', data2[:4])[0] if len(data2) >= 4 else 0
    # 200ms → ≈2000 滴答（±60% 容限，照顾调度抖动）
    dtick = (tval2 - tval1) & 0xFFFFFFFF
    ok = (op == m.OP_MXGW_TIME and op2 == m.OP_MXGW_TIME
          and len(data) == 4 and len(data2) == 4
          and 800 <= dtick <= 3200)
    check('C2S_TIME(15) → mxgw.S2CTime(15) 10000Hz单调滴答',
          ok, 't1=%d t2=%d dtick=%d' % (tval1, tval2, dtick))

    # ── C2GS_TIME(61) → S2C op192（dlgcalendar.S2CTime）──
    send_c2s(c, m.C2GS_TIME, b'\x00')
    op, data = recv_s2c(c)
    check('C2GS_TIME(61) → dlgcalendar.S2CTime(192)', op == m.OP_TIME and len(data) == 4,
          'op=%d data=%s' % (op, data.hex(' ')))

    # ── C2S_PRODUCE(78) sub5 = 矿点 → S2C op87 sub5 ──
    send_c2s(c, m.C2S_PRODUCE, bytes([m.PRODUCE_SUB_MINERALS]))
    op, data = recv_s2c(c)
    ok = op == m.OP_PRODUCE and data[0] == m.PRODUCE_SUB_MINERALS
    if ok:
        iopen = data[1]
        iscene = struct.unpack('<I', data[2:6])[0]
        ilen = data[6]
        ok = (iopen == 1 and iscene == m.MINIMAP_ID and ilen == 0 and len(data) == 7)
        check('C2S_PRODUCE(78) sub5 → S2CProduce(87) 矿点',
              ok, 'iOpen=%d iscene=%d ilen=%d len=%d' % (iopen, iscene, ilen, len(data)))
    else:
        check('C2S_PRODUCE(78) sub5 → S2CProduce(87) 矿点', False, 'op=%d' % op)

    # ── C2S_BACLLIST(14) → 不应答 ──
    send_c2s(c, m.C2S_BACLLIST, b'\x00' + struct.pack('<I', 0))
    got = drain(c, 0.5)
    check('C2S_BACLLIST(14) → 无应答（本服无逻辑）', not got, 'got=%d frames' % len(got))

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
