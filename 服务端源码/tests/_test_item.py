# -*- coding: utf-8 -*-
"""协议级测试：物品帧（S2C 33/36/38/35/34 + C2S 35/33/34）。

3139 实例：登录进入世界应收到 8 件 ItemAdd(33)；
  C2S_ITEM_STRING(35) → S2CItemString(38)
  C2S_ITEM_DRAG(33)   → S2CItemPos(36)
  C2S_ITEM_USE(34) ×N → S2CItemQuantity(35) 递减 → 归零 S2CItemDel(34)（回血类附 op17）
"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', 3139
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


def collect(c, want, window=1.0, maxf=90):
    frames = []
    try:
        for _ in range(maxf):
            op, data = recv_s2c(c, window)
            frames.append((op, data))
            if op == want:
                break
    except (socket.timeout, ConnectionError):
        pass
    return frames


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    collect(c, 999, 0.3)
    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    collect(c, 999, 0.3)
    send_c2s(c, 3, '物品测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    collect(c, 999, 0.3)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    frames = collect(c, m.OP_ITEM_ADD, 2.0, maxf=40)

    adds = [(i, d) for i, (o, d) in enumerate(frames) if o == m.OP_ITEM_ADD]
    # 收集所有 ItemAdd（世界进入后连续下发）
    time.sleep(0.4)
    try:
        while True:
            o, d = recv_s2c(c, 0.4)
            frames.append((o, d))
            if o == m.OP_ITEM_ADD:
                adds.append((len(frames) - 1, d))
    except (socket.timeout, ConnectionError):
        pass

    check('进世界下发 ItemAdd(33) × %d' % len(m.MOCK_ITEMS),
          len(adds) == len(m.MOCK_ITEMS), '实收 %d' % len(adds))
    if adds:
        iid, shape, key, qty, pos = None, None, None, None, None
        bad = []
        for _idx, d in adds:
            wtype, iid, shape, key, qty, pos = struct.unpack('<BIIHBB', d[:13])
            exp = dict((x[0], x) for x in m.MOCK_ITEMS).get(iid)
            if exp is None:
                bad.append('未知 iid=%d' % iid)
                continue
            if (wtype, shape, key, qty, pos) != (m.ITEM_WTYPE_NORMAL, exp[1], exp[2], exp[3], exp[4]):
                bad.append('iid=%d 字段不符 got=%s' % (iid, (wtype, shape, key, qty, pos)))
        check('ItemAdd 字段逐一校验（type=1/shape/key/qty/pos）', not bad,
              '异常 %d 项 %s' % (len(bad), bad[:3]))
        first = struct.unpack('<BIIHBB', adds[0][1][:13])
        check('首件为背包 pos=%d' % first[5], first[5] == m.ITEM_POS_BAG_START,
              'pos=%d shape=%d qty=%d' % (first[5], first[2], first[3]))

    # ── C2S 35 → S2C 38 ──
    send_c2s(c, m.C2S_ITEM_STRING, struct.pack('<I', 2002) + bytes([0]))
    frames = collect(c, m.OP_ITEM_STRING, 2.0)
    iss = [d for o, d in frames if o == m.OP_ITEM_STRING]
    ok = len(iss) == 1 and struct.unpack('<I', iss[0][:4])[0] == 2002
    check('C2S_ITEM_STRING(35) → S2CItemString(38)', ok,
          'n=%d' % len(iss))

    # ── C2S 33 → S2C 36 ──
    send_c2s(c, m.C2S_ITEM_DRAG, struct.pack('<I', 2001) + bytes([20, 30]))
    frames = collect(c, m.OP_ITEM_POS, 2.0)
    pos_f = [d for o, d in frames if o == m.OP_ITEM_POS]
    ok = False
    if pos_f:
        wtype, iid, pos = struct.unpack('<BIB', pos_f[0][:6])
        ok = (wtype == m.ITEM_WTYPE_NORMAL and iid == 2001 and pos == 30)
        check('C2S_ITEM_DRAG(33) → S2CItemPos(36)', ok,
              'type=%d id=%d pos=%d' % (wtype, iid, pos))
    else:
        check('C2S_ITEM_DRAG(33) → S2CItemPos(36)', False, '未收到 op36')

    # ── C2S 34 ×3 → S2C 35 递减 → 归零 S2C 34 ──
    qty_seen = []
    del_seen = 0
    for k in range(3):
        send_c2s(c, m.C2S_ITEM_USE, struct.pack('<II', 2003, 0))
        fr = collect(c, m.OP_ITEM_DEL, 1.5, maxf=12)
        for o, d in fr:
            if o == m.OP_ITEM_QUANTITY:
                iid, q, fl = struct.unpack('<IBB', d[:6])
                if iid == 2003:
                    qty_seen.append(q)
            if o == m.OP_ITEM_DEL:
                del_seen += 1
    check('C2S_ITEM_USE(34) → 数量递减 3→2→1', qty_seen[:2] == [2, 1],
          'qty=%s' % qty_seen)
    check('用尽后 → S2CItemDel(34)', del_seen >= 1, 'del=%d' % del_seen)

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
