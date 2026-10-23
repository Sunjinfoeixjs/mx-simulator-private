# -*- coding: utf-8 -*-
"""临时演示客户端：连上调试台的内核跑一轮真实会话，让界面各页签都有数据。
用完即删。用法： python _demo_client.py <调试台端口> [--hold]
"""
import json
import socket
import struct
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:%d' % int(sys.argv[1] if len(sys.argv) > 1 else 8090)
HOLD = '--hold' in sys.argv

h = json.loads(urllib.request.urlopen(BASE + '/api/hello', timeout=5).read())
port = h['kernel']['port']
print('kernel port =', port, flush=True)


def recv_s2c(c, t=3.0):
    c.settimeout(t)

    def exact(n):
        buf = b''
        while len(buf) < n:
            ch = c.recv(n - len(buf))
            if not ch:
                raise ConnectionError('closed')
            buf += ch
        return buf
    n = exact(1)[0]
    if n == 0xFF:
        n = struct.unpack('<H', exact(2))[0]
    p = exact(n)
    return p[0], p[1:]


def send(c, op, body=b''):
    c.sendall(bytes([op, len(body)]) + body)


def drain(c, t=0.4):
    out = []
    while True:
        try:
            out.append(recv_s2c(c, t)[0])
        except Exception:                                    # noqa: BLE001
            return out


def hello(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


c = socket.create_connection(('127.0.0.1', port), timeout=5)
op, data = recv_s2c(c)
seed = struct.unpack('<I', data[:4])[0]
c.sendall(hello(seed))
time.sleep(0.2)

# 登录
b = bytearray()
b += bytes([0, 10]) + struct.pack('<H', 43)
b += b'100860001'.ljust(32, b'\x00') + b'abcdef'.ljust(20, b'\x00')
b += bytes([1, 2]) + struct.pack('<H', 73)
send(c, 1, bytes(b))
time.sleep(0.35)
op, data = recv_s2c(c)
if data[0] == 0:                                   # 无角色 → 建角
    send(c, 3, '调试阿飞'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    for _ in range(3):
        recv_s2c(c)
send(c, 2, struct.pack('<I', 1))                   # 选角进世界
time.sleep(1.2)
drain(c, 0.4)

send(c, 54, struct.pack('<HH', 45, 40) + b'\x00')  # 走一步
drain(c, 0.3)
send(c, 96, struct.pack('<I', 1000))               # 点村长
drain(c, 0.4)

# 走到遇怪并打一个回合，让战斗面板有内容
for _ in range(6):
    send(c, 54, struct.pack('<HH', 45, 40) + b'\x00')
    drain(c, 0.25)
    try:
        st = json.loads(urllib.request.urlopen(BASE + '/api/state', timeout=3).read())
        if st.get('in_war'):
            break
    except Exception:                                        # noqa: BLE001
        pass
send(c, 160, bytes([1]))                           # 普攻
drain(c, 0.8)
send(c, 161, struct.pack('<BH', 100, 1101))        # 招式
drain(c, 0.8)
print('session ready', flush=True)

if not HOLD:
    sys.exit(0)
while True:                                        # 保持连接，周期 PING
    time.sleep(3)
    try:
        send(c, 8)
        recv_s2c(c, 2)
    except Exception:                                        # noqa: BLE001
        break
