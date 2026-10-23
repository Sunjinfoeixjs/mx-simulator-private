# -*- coding: utf-8 -*-
"""
《梦想世界》协议 —— 可运行 / 可热改 的自测闭环 (live testbed)
================================================================

背景约束
--------
真实 2013 客户端无法在本沙箱运行，且官方服 IP 已全灭。因此“运行游戏”在这里落地为：
用已逆向出的协议知识搭一个**能真实跑起来**的 mock 后端 + 客户端，让我们可以：

  1. 跑通「握手 → SetSeed → 登录 → 进世界」链路（复用 protocol_crypto 参考加密层）；
  2. 用 dissect_combat / dissect_itempet 产出的字段 JSON **双向编解码**真实 opcode 报文；
  3. 每改一处字段定义（JSON）或处理逻辑，一键重跑，立刻看到：
       - 该 opcode 是否还能 编码→解码 无损往返（自测）；
       - 服务端能否正确“读懂”客户端发出的包（联调 transcript）。

这就是“边运行游戏边测试修改”的可执行形态：脚本在跑、协议在跑，改 JSON/处理器即重测。

设计
----
  * 字段 JSON 在**运行时**加载 —— 编辑 JSON 后直接重跑本脚本即生效（热改），无需改代码。
  * PacketCodec.encode / decode：同一份字段规格，既能组包也能解包（往返一致性是修改的天然回归测试）。
  * MockServer：登录后持续收 opcode 帧，按 (opcode, 首字节子命令) 反查规格并解码打印，回 S2C ack。
  * Client：登录后按 scenario 发若干 opcode 包（物品/宠物/仓库/商店），验证我们拆解的字段。

用法
----
  python live_testbed.py            # 跑握手+登录+联调 transcript
  python live_testbed.py --selftest # 仅对所有拆解函数做 编码→解码 无损回归，不启 socket
"""
import socket
import struct
import sys
import threading
import time
import json
import os

from protocol_crypto import (hello_response, ReferenceStreamCipher)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Claw/
DATA = os.path.join(PROJ, '协议资料', '字典')
HOST, PORT = '127.0.0.1', 31341
SEED = 0xC0FFEE11
S2C_LOGIN_OK = 100

# ── 整数字段 size → struct 格式 ──
I_FMT = {1: 'B', 2: 'H', 4: 'I', 8: 'Q'}
SAMPLE = {1: 0x42, 2: 0x4242, 4: 0x42424242, 8: 0x4242424242424242}
SAMPLE_STR = '测试x'


# ════════════════════════════════════════════════════════════════════
# 1. 字段规格加载（运行时读 JSON —— 改 JSON 即热改）
# ════════════════════════════════════════════════════════════════════
def load_field_maps():
    """合并 combat + itempet 两份字段 JSON → {function_name: spec}。"""
    merged = {}
    for fn in ('combat_opcodes_fields.json', 'itempet_opcodes_fields.json'):
        p = os.path.join(DATA, fn)
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding='utf-8'))
        for k, v in d.items():
            merged[k] = v
    return merged


# ════════════════════════════════════════════════════════════════════
# 2. 双向编解码器（同规格：组包 & 解包）
# ════════════════════════════════════════════════════════════════════
class PacketCodec:
    def __init__(self, specs):
        self.specs = specs

    def encode(self, spec, args=None):
        """按 spec 的 fields 顺序组包；args 为 {字段描述: 值}。
        字面量子命令（value 为纯数字）自动写入，无需在 args 提供。"""
        args = args or {}
        body = bytearray()
        for i, f in enumerate(spec['fields']):
            if f['kind'] == 'I':
                # 字节宽：常量 → 直接用；变量（如 "param:data"，宽度由运行时参数决定）→ 参考宽 4B
                if isinstance(f['size'], int):
                    w = f['size']
                else:
                    w = 4
                if f['value'].isdigit():
                    v = int(f['value'])
                else:
                    v = args.get(f['value'], SAMPLE.get(w, 0))
                fmt = I_FMT.get(w, 'I')
                body += struct.pack('<' + fmt, v & ((1 << (8 * w)) - 1))
            elif f['kind'] == 'S':
                s = args.get(f['value'], SAMPLE_STR)
                b = s.encode('gbk', 'replace')
                if f['size'] == 'N:len':
                    body += b                      # 变长串：写到帧尾
                else:
                    n = int(f['size'])
                    body += b[:n].ljust(n, b'\x00')  # 定长串：补 \x00
        return bytes(body)

    def decode(self, spec, body):
        """按 spec 解析 body → {字段描述: 值}。"""
        out, off = {}, 0
        for i, f in enumerate(spec['fields']):
            if f['kind'] == 'I':
                w = f['size'] if isinstance(f['size'], int) else 4
                raw = body[off:off + w]
                off += w
                v = int.from_bytes(raw, 'little') if raw else 0
                key = 'subcmd' if (i == 0 and f['value'].isdigit()) else f['value']
                out[key] = v
            elif f['kind'] == 'S':
                if f['size'] == 'N:len':
                    raw = body[off:]
                    off = len(body)
                else:
                    n = int(f['size'])
                    raw = body[off:off + n]
                    off += n
                out[f['value']] = raw.rstrip(b'\x00').decode('gbk', 'replace')
        return out


def build_codec():
    return PacketCodec(load_field_maps())


# opcode → [spec,...] 反查表（供服务端按 opcode 派发）
def build_opcode_index(specs):
    idx = {}
    for name, spec in specs.items():
        idx.setdefault(spec['opcode'], []).append(spec)
    return idx


def lookup_spec(opcode_index, opcode, body):
    """按 (opcode, 首字节子命令) 选规格 —— 复刻命令族派发逻辑。"""
    cands = opcode_index.get(opcode, [])
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1 and body:
        sub = body[0]
        for s in cands:
            if s['fields'] and s['fields'][0]['kind'] == 'I' and s['fields'][0]['value'].isdigit():
                if int(s['fields'][0]['value']) == sub:
                    return s
        return cands[0]
    return None


# ════════════════════════════════════════════════════════════════════
# 3. 帧封装（握手后加密通道，复用参考加密层）
# ════════════════════════════════════════════════════════════════════
def send_frame(sock, cipher, opcode, body):
    payload = struct.pack('<H', opcode) + body
    enc = cipher.crypt(payload)
    sock.sendall(struct.pack('<H', len(payload)) + enc)


def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接断开")
        buf += chunk
    return buf


def recv_frame(sock, cipher):
    n = struct.unpack('<H', recv_exact(sock, 2))[0]
    payload = cipher.crypt(recv_exact(sock, n))
    return struct.unpack('<H', payload[:2])[0], payload[2:]


# ════════════════════════════════════════════════════════════════════
# 4. Mock 服务端
# ════════════════════════════════════════════════════════════════════
class MockServer:
    def __init__(self, codec, opcode_index):
        self.codec = codec
        self.opcode_index = opcode_index

    def run(self, conn):
        try:
            # ① Hello（明文）
            conn.sendall(struct.pack('<I', SEED) + struct.pack('<H', 1))
            print("[Server] 发送 Hello seed=0x%08X (明文)" % SEED)
            # ② 收 25B NakedSend 回应
            resp = recv_exact(conn, 25)
            assert resp[:3] == hello_response(SEED)[:3], "Hello 回应校验失败"
            print("[Server] 收到 25B Hello 回应并校验通过 (NakedSend 明文)")
            # ③ SetSeed：双状态
            recv_c = ReferenceStreamCipher(SEED)   # 解密 client→server
            send_c = ReferenceStreamCipher(SEED)   # 加密 server→client
            print("[Server] SetSeed 完成: 双密钥流状态已建立, +0x72=1")

            # ④ 收 C2SLoginIn
            op, body = recv_frame(conn, recv_c)
            assert op == 1, "期望 C2SLoginIn(op=1), 实得 %d" % op
            name = body[4:4 + 32].rstrip(b'\x00').decode('gbk', 'replace')
            pwd = body[36:36 + 20].rstrip(b'\x00').decode('gbk', 'replace')
            print("[Server] 解密 C2SLoginIn: 帐号=%s 密码=%s" % (name, pwd))
            send_frame(conn, send_c, S2C_LOGIN_OK, b'welcome-to-world')
            print("[Server] 发送 S2C 进入游戏回包 (加密)")

            # ⑤ 进世界后：持续解码客户端 opcode 报文（联调）
            print("\n[Server] ── 进入世界，开始联调解码客户端 opcode 报文 ──")
            while True:
                try:
                    op, body = recv_frame(conn, recv_c)
                except ConnectionError:
                    break
                spec = lookup_spec(self.opcode_index, op, body)
                if spec is None:
                    print("  [op %d] 无对应规格（字典外），body=%s" % (op, body.hex()))
                    send_frame(conn, send_c, 0, b'')
                    continue
                dec = self.codec.decode(spec, body)
                print("  [op %d] %s -> %s" % (op, spec['function'], dec))
                # 回一个 S2C ack（用同 opcode 区，简单 echo）
                send_frame(conn, send_c, op, b'\x01')
        except Exception as e:
            print("[Server] 异常:", e)
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
# 5. 客户端 + 联调场景
# ════════════════════════════════════════════════════════════════════
# 场景：函数名 -> 测试参数（仅给命名/字符串字段；字面量子命令自动取）
SCENARIO = [
    ('C2SItemGet',     {'param:id': 12345}),
    ('C2SItemDrag',    {'param:id': 222, 'param:source': 1, 'param:dest': 2}),
    ('C2SSetFight',    {'param:summon_id': 99887766}),
    ('C2SSetIntro',    {'param:intro': '我的召唤兽'}),   # 物品商店子命令族 op89
    ('C2SSHSetPwd',    {'param:oldpwd': 'oldpwd1234567890123', 'param:newpwd': 'newpwd1234567890123'}),
]


def client_run(codec):
    time.sleep(0.3)
    s = socket.create_connection((HOST, PORT))
    # ① 收 Hello
    hello = recv_exact(s, 6)
    seed = struct.unpack('<I', hello[:4])[0]
    print("[Client] 收到 Hello seed=0x%08X" % seed)
    # ③ SetSeed
    send_c = ReferenceStreamCipher(seed)
    recv_c = ReferenceStreamCipher(seed)
    print("[Client] SetSeed 完成: 双密钥流状态已建立")
    # ② NakedSend 回应
    s.sendall(hello_response(seed))
    print("[Client] 发送 25B Hello 回应 (NakedSend 明文)")
    # ④ C2SLoginIn
    body = bytes([0, 10]) + struct.pack('<H', 43)
    body += '100860001'.encode().ljust(32, b'\x00')
    body += 'abcdef'.encode().ljust(20, b'\x00')
    body += bytes([1, 1]) + struct.pack('<H', 50)
    body += bytes([192, 168, 1, 100])
    body += b'\x00' * (8 + 1 + 4 + 17)
    send_frame(s, send_c, 1, body)
    print("[Client] 发送 C2SLoginIn (op=1, 加密)")

    op, rb = recv_frame(s, recv_c)
    print("[Client] 收到 S2C op=%d: %r (已解密)" % (op, rb))
    print("\n[Client] ── 进世界，按 scenario 发 opcode 报文联调 ──")

    for fname, args in SCENARIO:
        spec = codec.specs.get(fname)
        if spec is None:
            print("  [跳过] 字典无 %s" % fname)
            continue
        pkt = codec.encode(spec, args)
        send_frame(s, send_c, spec['opcode'], pkt)
        print("  [发 op %d] %s  body=%s" % (spec['opcode'], fname, pkt.hex()))
        # 收服务端 ack
        aop, _ = recv_frame(s, recv_c)
        print("  [收 S2C op %d] ack" % aop)
    s.close()
    print("\n>>> 联调完成，协议闭环运行正常 ✓")


# ════════════════════════════════════════════════════════════════════
# 6. 回归自测：所有函数 编码→解码 无损
# ════════════════════════════════════════════════════════════════════
def roundtrip_selftest(codec):
    print("=" * 64)
    print("回归自测：对所有拆解函数做 编码→解码 无损校验")
    print("=" * 64)
    total = len(codec.specs)
    fail = 0
    for name, spec in codec.specs.items():
        try:
            body = codec.encode(spec)
            dec = codec.decode(spec, body)
            body2 = codec.encode(spec, dec)
            ok = (body == body2)
        except Exception as e:
            ok = False
            dec = 'ERR:%s' % e
        tag = 'PASS' if ok else 'FAIL'
        if not ok:
            fail += 1
        print("  [%s] op %3d  %-22s %s" % (tag, spec['opcode'], name, '' if ok else '-> ' + str(dec)))
    print("-" * 64)
    print("结果: %d/%d 通过, %d 失败" % (total - fail, total, fail))
    return fail == 0


# ════════════════════════════════════════════════════════════════════
# 7. 入口
# ════════════════════════════════════════════════════════════════════
def main():
    codec = build_codec()
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--selftest':
        ok = roundtrip_selftest(codec)
        sys.exit(0 if ok else 1)

    opcode_index = build_opcode_index(codec.specs)
    srv = MockServer(codec, opcode_index)
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((HOST, PORT))
    ls.listen(1)
    print("=" * 64)
    print("《梦想世界》协议自测闭环 (可运行 / 可热改) —— mock 后端 + 客户端")
    print("=" * 64)
    threading.Thread(target=lambda: srv.run(ls.accept()[0]), daemon=True).start()
    client_run(codec)
    ls.close()
    print("\n提示: 编辑 *_opcodes_fields.json 后重跑本脚本即验证改动（热改无需改代码）。")


if __name__ == '__main__':
    main()
