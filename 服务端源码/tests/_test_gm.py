# -*- coding: utf-8 -*-
"""GM 面板「即时下发协议帧」验证：连 3144(游戏) → 进世界并保持连接 →
打 8081(Web) 改 HP/加物品/传送 → 确认游戏客户端 socket 收到对应帧
(op17 PropChange / op33 ItemAdd / op97 GotoM)。无客户端时改值不应崩。"""
import socket
import struct
import sys
import time
import json
import urllib.request

HOST, GHOST, GPORT, WPORT = '127.0.0.1', '127.0.0.1', 3144, 8081
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


def post(path, body):
    req = urllib.request.Request(
        'http://%s:%d/%s' % (GHOST, WPORT, path),
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=5).read().decode('utf-8'))


def main():
    c = socket.create_connection((HOST, GPORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    collect(c, 6, 0.3)
    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    collect(c, 6, 0.3)
    send_c2s(c, 3, 'GM测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    collect(c, 6, 0.3)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    time.sleep(0.5)
    collect(c, 60, 0.5)   # 吞掉进世界帧，保持连接处于「世界循环」
    print('   已进世界并保持连接，开始 GM 帧下发验证')

    # 1) 改 HP=50 → 客户端应收到 PropChange(17)，idx=PROP_IDX_HP，值=50
    post('api/hp', {'value': 50})
    fr = collect(c, 20, 1.0)
    hp_frames = [d for o, d in fr
                 if o == m.OP_PROP_CHANGE and len(d) >= 3 and d[0] == m.PROP_IDX_HP]
    hp_val = struct.unpack('<H', hp_frames[0][1:3])[0] if hp_frames else None
    check('GM 改 HP=50 → 客户端收到 PropChange(17) hp=50',
          hp_val == 50, 'got=%s' % hp_val)

    # 2) 加物品 shape=22100 → 客户端应收到 ItemAdd(33) 且含该 shape
    post('api/item/add', {'shape': 22100, 'qty': 1, 'name': 'GMTEST'})
    fr = collect(c, 20, 1.0)
    adds = [d for o, d in fr if o == m.OP_ITEM_ADD]
    hit = any(struct.pack('<I', 22100) in d for d in adds)
    check('GM 加物品 shape=22100 → 客户端收到 ItemAdd(33)', hit,
          'adds=%d' % len(adds))

    # 3) 传送坐标 → 客户端应收到 GotoM(97)
    post('api/pos', {'x': 60, 'y': 70})
    fr = collect(c, 20, 1.0)
    check('GM 传送 → 客户端收到 GotoM(97)',
          m.OP_MAP_GOTO_M in [o for o, _ in fr])

    # 4) 断开后改值不应崩（_gm_send 应安全跳过）
    c.close()
    time.sleep(0.4)
    r = post('api/coins', {'value': 12345})
    check('断开后 GM 改金钱不崩且生效',
          bool(r.get('ok')) and r['state']['coins'] == 12345,
          'coins=%s' % r['state']['coins'])

    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
