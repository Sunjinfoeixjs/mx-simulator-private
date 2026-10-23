# -*- coding: utf-8 -*-
"""加密模式端到端回归：原版 mxmain.dll（+0x72=1）通道的「握手明文 → 之后加密+压缩」。

不启动 GUI、不依赖 DLL/unicorn，用真实 socket 走一遍：
  ① 服务端发 Hello（明文、不压缩）→ 客户端校验 opcode 与 seed；
  ② 客户端回 25B hello_response（明文）→ 服务端校验通过后切换加密；
  ③ 客户端发加密 C2S_PING（op8）→ 服务端用 TX 流解密、回 S2C(250)；
  ④ 客户端用 RX 流解密 + LZSS 解压 → 断言 opcode==250。

这覆盖了加密接线里最容易错的两处：收发方向密钥流是否对、S2C 是否
「先压缩再加密」且长度前缀为压缩后长度。
"""
import os
import socket
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = next(p for p in (os.path.join(ROOT, 'sim'), ROOT,
                       os.path.join(HERE, 'sim'))
           if os.path.isfile(os.path.join(p, 'm4_server.py')))
sys.path.insert(0, SIM)

import m4_server as m  # noqa: E402
import mx_crypto  # noqa: E402
import lzss  # noqa: E402

SEED = 0x12345678


def recv_exact(c, n, timeout=3.0):
    c.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        ch = c.recv(n - len(buf))
        if not ch:
            raise ConnectionError('closed')
        buf += ch
    return buf


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def recv_s2c_enc(c, cipher, timeout=3.0):
    """读一帧加密 S2C：[N][compressed]，解密 + 解压 → (opcode, body)。"""
    ln = cipher.crypt_rx(recv_exact(c, 1, timeout))[0]
    if ln == 0xFF:
        ln = struct.unpack('<H', cipher.crypt_rx(recv_exact(c, 2, timeout)))[0]
    compressed = cipher.crypt_rx(recv_exact(c, ln, timeout))
    payload = lzss.decompress(compressed)
    return payload[0], payload[1:]


def send_c2s_enc(c, cipher, op, body=b''):
    frame = bytes([op]) + bytes([len(body)]) + body
    c.sendall(cipher.crypt_tx(frame))


def main():
    tmp = tempfile.mkdtemp(prefix='cryptomode_')
    srv = m.M4Server('127.0.0.1', 0, seed=SEED, encounter_every=0, enemies=1,
                     turn_timeout=30, logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'), encrypt=True)
    port = srv.serve_background(0)

    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect(('127.0.0.1', port))

    fails = []

    def check(name, cond, extra=''):
        if cond:
            print('  ok   %s' % name)
        else:
            print('  FAIL %s %s' % (name, extra))
            fails.append(name)

    # ① Hello（明文）
    ln = recv_exact(c, 1)[0]
    hello = recv_exact(c, ln)
    check('Hello 明文可读（N=%d，op=%d）' % (ln, hello[0]),
          hello[0] == m.OP_HELLO and len(hello) == 7, hello.hex())
    seed = struct.unpack('<I', hello[1:5])[0]
    check('Hello seed 一致', seed == SEED, hex(seed))

    # ② 25B 回应（明文）
    c.sendall(hello_resp(SEED))

    # ③ 建立对称流，发加密 C2S_PING(8)
    cipher = mx_crypto.RealCipher(SEED)
    time.sleep(0.1)          # 等服务端完成握手、包上 CryptoConn
    send_c2s_enc(c, cipher, m.C2S_PING)

    # ④ 收加密 S2C(250)，解密 + 解压
    op, body = recv_s2c_enc(c, cipher)
    check('S2C 解密+解压后 opcode==250', op == 250, 'op=%d body=%s' % (op, body.hex()))

    c.close()
    try:
        srv.stop()
    except Exception:  # noqa: BLE001
        pass

    print()
    if fails:
        print('FAILED: %s' % fails)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
