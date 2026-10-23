# -*- coding: utf-8 -*-
"""
《梦想世界》自搭环境 —— 协议闭环演示
====================================
离线、纯 Python (socket + threading)。完整复现「进入游戏」链路在自搭环境里的闭环:

  Connect
    → 服务端发 Hello (明文): 4字节种子 + 2字节服务器号
    → 客户端发 25字节 Hello 回应 (NakedSend, 明文, 精确混淆公式)
    → 两端 SetSeed: 从同一 seed 派生两个方向的密钥流状态
    → 客户端发 C2SLoginIn (opcode=1, 加密)
    → 服务端解密, 校验字段, 回 S2C 进入游戏包 (加密)
    → 客户端解密, 进入游戏世界 ✓

证明: 加密边界 + 握手 + 报文格式 在自搭环境里完全闭环。
真实加密算法 (VMProtect 保护) 以 ReferenceStreamCipher 占位, 可一键替换 (protocol_crypto.RealCipher)。
"""
import socket
import threading
import struct
import time

from protocol_crypto import hello_response, hello_obfuscate, ReferenceStreamCipher

HOST, PORT = '127.0.0.1', 31340
SEED = 0xC0FFEE11          # 服务端下发的握手种子 (演示)
S2C_LOGIN_OK = 100         # 演示用 S2C opcode (真实值见 S2C 回调表)


# ───────────────────────── 帧封装 (握手后加密通道)
def send_frame(sock, cipher, opcode, body):
    """打包: [2B 明文长度][ opcode(2B LE) + body ] 再整体加密。"""
    payload = struct.pack('<H', opcode) + body
    enc = cipher.crypt(payload)
    sock.sendall(struct.pack('<H', len(payload)) + enc)


def recv_frame(sock, cipher):
    """收包: 读 2B 长度 → 读密文 → 解密 → 拆 opcode/body。"""
    n = struct.unpack('<H', recv_exact(sock, 2))[0]
    enc = recv_exact(sock, n)
    payload = cipher.crypt(enc)
    opcode = struct.unpack('<H', payload[:2])[0]
    return opcode, payload[2:]


def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接断开")
        buf += chunk
    return buf


# ───────────────────────── 服务端
def server_loop(conn):
    try:
        # ① Hello (明文): 4字节种子 + 2字节服务器号
        conn.sendall(struct.pack('<I', SEED) + struct.pack('<H', 1))
        print("[Server] 发送 Hello: seed=0x%08X servernum=1 (明文)" % SEED)

        # ② 收客户端 25字节 NakedSend 回应 (明文, 不走加密)
        resp = recv_exact(conn, 25)
        expected = hello_response(SEED)
        assert resp[:3] == expected[:3], "Hello 回应校验失败"
        print("[Server] 收到 25字节 Hello 回应并校验通过 (NakedSend 明文)")

        # ③ SetSeed: 双状态 (recv 解密 client->server, send 加密 server->client)
        #    方向对齐: server_recv 必须与 client_send 使用同一密钥流
        server_recv_cipher = ReferenceStreamCipher(SEED)   # 解密 client->server
        server_send_cipher = ReferenceStreamCipher(SEED)   # 加密 server->client
        print("[Server] 参考 cipher 初始化完成（非真实客户端算法）")

        # ④ 收 C2SLoginIn (opcode=1, 加密)
        op, body = recv_frame(conn, server_recv_cipher)
        assert op == 1, "期望 C2SLoginIn(op=1), 实得 %d" % op
        # C2SLoginIn body 布局: v0(1) v1(1) v2(2) name(32) pwd(20) ...
        name = body[4:4 + 32].rstrip(b'\x00').decode('gbk', 'replace')
        pwd = body[36:36 + 20].rstrip(b'\x00').decode('gbk', 'replace')
        print("[Server] 解密 C2SLoginIn: 帐号=%s 密码=%s (密文已还原)" % (name, pwd))

        # ⑤ 模拟进入游戏: 回 S2C 包 (加密)
        send_frame(conn, server_send_cipher, S2C_LOGIN_OK, b'welcome-to-world')
        print("[Server] 发送 S2C 进入游戏回包 (加密)")
    except Exception as e:
        print("[Server] 异常:", e)
    finally:
        conn.close()


# ───────────────────────── 客户端
def client_run():
    time.sleep(0.3)
    s = socket.create_connection((HOST, PORT))

    # ① 收 Hello (明文)
    hello = recv_exact(s, 6)
    seed = struct.unpack('<I', hello[:4])[0]
    servernum = struct.unpack('<H', hello[4:6])[0]
    print("[Client] 收到 Hello: seed=0x%08X servernum=%d" % (seed, servernum))

    # ③ 模拟客户端的两个方向；参考 cipher 仅用于本地闭环演示。
    client_send_cipher = ReferenceStreamCipher(seed)
    client_recv_cipher = ReferenceStreamCipher(seed)
    print("[Client] 参考 cipher 初始化完成（非真实客户端算法）")

    # ② 25字节 NakedSend 回应 (明文, 精确混淆公式)
    s.sendall(hello_response(seed))
    print("[Client] 发送 25字节 Hello 回应 (NakedSend, 明文, 混淆seed=0x%08X)" % hello_obfuscate(seed))

    # ④ C2SLoginIn (opcode=1, 加密) —— body 布局严格按 netlogin.C2SLoginIn
    body = bytes([0, 10]) + struct.pack('<H', 43)          # 脚本版本 0,10,43
    body += '100860001'.encode().ljust(32, b'\x00')        # 帐号 32B
    body += 'abcdef'.encode().ljust(20, b'\x00')           # 密码 20B (明文上总线!)
    body += bytes([1, 1]) + struct.pack('<H', 50)          # GetVersion / update.ini 占位
    body += bytes([192, 168, 1, 100])                      # 本机IP
    body += b'\x00' * (8 + 1 + 4 + 17)                     # adsid/tgid/driveid/mac 占位
    send_frame(s, client_send_cipher, 1, body)
    print("[Client] 发送 C2SLoginIn (op=1, 加密)")

    # ⑤ 收 S2C 进入游戏 (加密)
    op, rb = recv_frame(s, client_recv_cipher)
    print("[Client] 收到 S2C op=%d: %r (已解密)" % (op, rb))
    print("\n>>> 进入游戏世界 ✓  协议在自搭环境闭环成功")
    s.close()


def main():
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((HOST, PORT))
    ls.listen(1)
    print("=" * 64)
    print("《梦想世界》自搭环境 —— 协议闭环演示 (加密层参考实现)")
    print("=" * 64)
    threading.Thread(target=lambda: server_loop(ls.accept()[0]), daemon=True).start()
    client_run()
    ls.close()


if __name__ == '__main__':
    main()
