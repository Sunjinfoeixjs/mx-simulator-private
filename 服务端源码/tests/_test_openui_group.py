# -*- coding: utf-8 -*-
"""协议级测试：C2S_OPENUI(88) / C2S_GROUP(81) 的应答逻辑。

在 127.0.0.1:3135 起一个 m4_server 测试实例，用脚本客户端走：
  Hello 握手 → 登录 → OPENUI 109/129/109(异地) → GROUP 80/81/99(未映射)
逐帧断言 S2C 应答内容。
"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', 3135
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


def drain(c, window=0.4):
    """读干净当前积压帧（带超时）。"""
    out = []
    try:
        while True:
            out.append(recv_s2c(c, window))
    except (socket.timeout, ConnectionError):
        pass
    return out


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    # Hello
    op, data = recv_s2c(c)
    check('Hello 帧', op == m.OP_HELLO and len(data) == 6, 'op=%d' % op)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    drain(c)

    # 登录（让 hero_name 生效）
    send_c2s(c, 1, login_body())
    time.sleep(0.3)
    drain(c)
    send_c2s(c, 3, '试试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    drain(c)

    # ── OPENUI 109：小地图 NPC（本地图 1001）──
    send_c2s(c, m.C2S_OPENUI, struct.pack('<BH', m.OPENUI_SUB_ADDNPC, m.MINIMAP_ID))
    op, data = recv_s2c(c)
    ok = op == m.OP_OPENUI and data[0] == m.OPENUI_RESP_ADDNPC
    if ok:
        minimap = struct.unpack('<H', data[1:3])[0]
        n = data[3]
        x, y = struct.unpack('<HH', data[4:8])
        slen = data[8]
        name = data[9:9 + slen].decode('gbk')
        elen = data[9 + slen]
        ok = (minimap == m.MINIMAP_ID and n == 1 and (x, y) == (47, 42)
              and name == '村长' and elen == 0)
        check('OPENUI 109 → S2CAddMinimapNpc', ok,
              'minimap=%d n=%d (%d,%d) name=%s' % (minimap, n, x, y, name))
    else:
        check('OPENUI 109 → S2CAddMinimapNpc', False, 'op=%d sub=%s' % (op, data[:1]))

    # ── OPENUI 109：非本地图 → 空列表 ──
    send_c2s(c, m.C2S_OPENUI, struct.pack('<BH', m.OPENUI_SUB_ADDNPC, 5))
    op, data = recv_s2c(c)
    ok = op == m.OP_OPENUI and data[0] == m.OPENUI_RESP_ADDNPC \
        and struct.unpack('<H', data[1:3])[0] == 5 and data[3] == 0
    check('OPENUI 109(异地) → 空标记', ok, 'op=%d data=%s' % (op, data.hex(' ')))

    # ── OPENUI 129：静修/称号面板 ──
    send_c2s(c, m.C2S_OPENUI, bytes([m.OPENUI_SUB_JINGXIU, 0]))
    op, data = recv_s2c(c)
    check('OPENUI 129 → S2CShowBaoziTitle',
          op == m.OP_OPENUI and data == bytes([m.OPENUI_SUB_JINGXIU]),
          'op=%d data=%s' % (op, data.hex(' ')))

    # ── OPENUI 未映射 sub（109 之外的 None 处理器，如 161）→ 不应答 ──
    send_c2s(c, m.C2S_OPENUI, bytes([161, 0]))
    got = drain(c, 0.5)
    check('OPENUI 161(未映射) → 无应答', not got, 'got=%s' % got)

    # ── GROUP 80：打开帮派面板 ──
    send_c2s(c, m.C2S_GROUP, bytes([m.GROUP_SUB_OPEN]))
    op, data = recv_s2c(c)
    ok = op == m.OP_GROUP and data[0] == m.GROUP_SUB_OPEN
    if ok:
        name = data[1:17].rstrip(b'\x00').decode('gbk')
        owner = struct.unpack('<I', data[17:21])[0]
        ok = name == m.FAMILY_NAME and owner == 1
        check('GROUP 80 → S2COpenFamily', ok,
              'name=%s owner=%d' % (name, owner))
    else:
        check('GROUP 80 → S2COpenFamily', False, 'op=%d' % op)

    # ── GROUP 81：成员列表（打开面板后客户端自动请求）──
    send_c2s(c, m.C2S_GROUP, bytes([m.GROUP_SUB_MEMBERLIST]))
    op, data = recv_s2c(c)
    ok = op == m.OP_GROUP and data[0] == m.GROUP_SUB_MEMBERLIST
    if ok:
        mid = struct.unpack('<I', data[1:5])[0]
        name = data[5:21].rstrip(b'\x00').decode('gbk')
        grade, cls, online = data[21], data[22], data[23]
        term = struct.unpack('<I', data[24:28])[0]
        ok = (mid == 1 and name == '试试' and (grade, cls, online) == (1, 1, 1)
              and term == 0 and len(data) == 28)
        check('GROUP 81 → S2CFamilyMemberList', ok,
              'mid=%d name=%s g/c/o=%d/%d/%d term=%d len=%d'
              % (mid, name, grade, cls, online, term, len(data)))
    else:
        check('GROUP 81 → S2CFamilyMemberList', False, 'op=%d' % op)

    # ── GROUP 未映射 sub（如 109 C2SOpenGiftHis，GroupFunc 无 109）→ 不应答 ──
    send_c2s(c, m.C2S_GROUP, bytes([109]))
    got = drain(c, 0.5)
    check('GROUP 109(未映射) → 无应答', not got, 'got=%s' % got)

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
