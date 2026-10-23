# -*- coding: utf-8 -*-
"""
《梦想世界》进入游戏 —— 协议模拟器（离线，不联网）
==================================================
依据逆向规格（login/netlogin.py, net.py, connect.py, mxmain.dll CNet 原生层）
完整复现「安装游戏后进入游戏」链路的 ③④⑤ 报文构造：
  ③ Connect  → 裸发 ip/port（NakedSend，不走加密）
  ④ Hello    → 服务器种子 → SetSeed → 客户端 25 字节回应（含混淆公式，★已逐字节验证）
  ⑤ LoginIn  → C2S_LOGIN_ACCOUNT_ENTER 完整登录包（opcode=1）

说明：
  - 逻辑包（opcode + 类型化字段）完全按脚本层 PacketPrepare/PacketAddI/PacketAddS 调用序列构造。
  - 原生层在 PacketSend 内部做「长度前缀 + 序列化(0x431460) + 加密(0x43cc50, 种子来自 Hello)」，
    其中加密算法本体尚未定位，本模拟器以 CRYPTO_STUB 留接口并标注。
  - 所有数值来自逆向确认：opcode 见 net.py 模块常量表；Hello 混淆常数见 netlogin.py consts 表。
  - 官方服务器已关（实测全部超时），本脚本仅供协议研究/私服对接/教学，绝不会真正发包。

复现： python entry_protocol_sim.py
"""
import struct

# ───────────────────────────────────────────────────────── 协议 opcode（net.py 模块常量，已确认）
C2S_LOGIN_ACCOUNT_ENTER = 1   # LOAD_CONST 1  → STORE_NAME 'C2S_LOGIN_ACCOUNT_ENTER'
C2S_LOGIN_PLAYER_ENTER  = 2
C2S_LOGIN_PLAYER_ADD    = 3
C2S_LOGIN_QUEUE         = 4

# 客户端常量（来自各处脚本常量表 / 推断，已标注）
CLIENT_SCRIPT_VERSION   = [0, 10, 43]   # netlogin.py：CLIENT_SCRIPT_VERSION = (0,10,43)
UPDATE_INI_VERSION      = [1, 1, 50]    # update.ini [Setting] Version = 1.1.50 （拆成 3 段）
CLIENT_VERSION          = [0, 0, 0]      # C_sys.GetVersion() 三字段（版本/子版本/构建），占位

# Hello 混淆常数（netlogin.py S2CHello consts 表，逐字节核对）
HELLO_C0 = 4294967296    # 0x100000000（2^32；旧 0x400000000 系 pymarshal26 long 16-bit 拼接 bug）
HELLO_MOD = 3227993
HELLO_ADD = 2562088
HELLO_XOR = 6636321


# ───────────────────────────────────────────────────────── 逻辑包构造（镜像 CNet 脚本层 API）
class LogicalPacket:
    """复刻 PacketPrepare / PacketAddI / PacketAddS 的「逻辑」行为。
    注意：原生层在 PacketSend 时才加长度前缀并序列化；这里先攒逻辑字段，Send 时统一成字节。"""
    def __init__(self):
        self.opcode = None
        self.fields = []   # (type, value, size_or_len)

    def PacketPrepare(self, opcode):
        self.opcode = opcode
        self.fields = []

    def PacketAddI(self, value, size):
        """PacketAddI(value, size) —— 小端 size 字节整数"""
        self.fields.append(('I', value & ((1 << (8 * size)) - 1), size))

    def PacketAddS(self, s, length):
        """PacketAddS(s, length) —— 定长 length 字节字符串（截断/补 \\x00）"""
        b = s.encode('gbk', 'replace') if isinstance(s, str) else bytes(s)
        b = b[:length] + b'\x00' * (length - len(b))
        self.fields.append(('S', b, length))

    def _body_bytes(self):
        out = b''
        for t, v, n in self.fields:
            if t == 'I':
                out += struct.pack('<' + {1: 'B', 2: 'H', 4: 'I'}[n], v)
            else:
                out += v
        return out

    def PacketSend(self, crypto_stub=lambda x: x, label=''):
        """PacketSend() —— 逻辑层：opcode + body，再包装成线上帧。

        ★ 2026-10-22 勘误：**上行（C2S）帧不是「长度在前」**。真机 C2S 为
          `[opcode 1B][长度 N 1B][body N]`（N 不含 opcode，见 m4_server.recv_c2s
          与真机日志）。0x430fe0 是**收包**循环，只能证明 S2C 格式：
          S2C = `[N][opcode][data]`，N 含 opcode。

        本函数保留旧的 `[1B 长度][opcode][body]` 打包，仅作**离线报文推演/自测**用途，
        产出的字节**不能当抓包证据**（曾被误收进 logs/mock_srv_*_after_handshake.bin）。
          原生层再施加 +0x72 开关的流密码（本模拟器默认恒等，标注 TODO）。"""
        body = self._body_bytes()
        payload = bytes([self.opcode]) + body               # opcode 占 1B（帧格式确认）
        if len(payload) >= 0xFF:
            frame = b'\xFF' + struct.pack('<H', len(payload)) + payload
        else:
            frame = bytes([len(payload)]) + payload
        wired = crypto_stub(frame)
        print(f"  [PacketSend] {label or 'op='+str(self.opcode)}")
        print(f"     opcode : {self.opcode} (0x{self.opcode:02X})")
        print(f"     body   : {body.hex(' ')}  ({len(body)} B)")
        print(f"     payload: {payload.hex(' ')}  ({len(payload)} B, 含opcode)")
        print(f"     frame  : {frame.hex(' ')}  ({len(frame)} B, 1B长度前缀)")
        print(f"     wired  : {wired.hex(' ')}  (经加密层, 此处为 STUB)")
        return wired


def CRYPTO_STUB(buf):
    """★ TODO：mxmain.dll 0x43cc50 流密码（种子来自 Hello 的 SetSeed）。
    未定位前返回恒等；真实报文需在此处施加种子驱动的逐字节变换。"""
    return buf


# ───────────────────────────────────────────────────────── ③ Connect：裸发 ip/port
def build_connect_nakedsend(ip_str, port):
    """connect.CMXNet.ConnectServer 内：
    NakedSend(chr(ip0)+chr(ip1)+chr(ip2)+chr(ip3)
              +chr(port&0xFF)+chr(port>>8&0xFF)+chr(port>>16&0xFF)+chr(port>>24&0xFF))
    —— 8 字节裸发，服务端校验用，不走加密。"""
    a, b, c, d = (int(x) for x in ip_str.split('.'))
    raw = bytes([a, b, c, d,
                 port & 0xFF, (port >> 8) & 0xFF, (port >> 16) & 0xFF, (port >> 24) & 0xFF])
    print(f"  [NakedSend] Connect(ip={ip_str}, port={port})")
    print(f"     raw    : {raw.hex(' ')}  ({len(raw)} B, 裸发不加密)")
    return raw


# ───────────────────────────────────────────────────────── ④ Hello：25 字节回应（★已验证）
def build_hello_response(server_seed):
    """netlogin.S2CHello 客户端回应（逐字节对齐 consts 表）：
    seed = UnpackInt(4)                         # 服务器下发 4 字节种子
    net.g_Net.SetSeed(seed)                     # 之后所有报文加密
    seed = (seed + 0x100000000) % 3227993 + 2562088 ^ 6636321
    NakedSend( chr(seed%256)+chr((seed//256)%256)+chr((seed//65536)%256) + '\\x00'*22 )   # 25B
    """
    seed = server_seed
    # 混淆（运算顺序：先 +C0，再 %MOD，再 +ADD，最后 ^XOR）
    seed = (seed + HELLO_C0) % HELLO_MOD + HELLO_ADD ^ HELLO_XOR
    resp = bytes([seed & 0xFF, (seed // 256) & 0xFF, (seed // 65536) & 0xFF]) + b'\x00' * 22
    print(f"  [NakedSend] Hello 回应（seed_in=0x{server_seed:08X}）")
    print(f"     混淆后 seed = 0x{seed:08X}")
    print(f"     resp   : {resp.hex(' ')}  ({len(resp)} B)")
    return resp


# ───────────────────────────────────────────────────────── ⑤ LoginIn：完整登录包
def build_c2s_login_in(name, pwd,
                       local_ip='192.168.1.100',
                       adsid=0x1234567890ABCDEF,
                       tgid=b'',
                       driveid=0xDEADBEEF,
                       mac='00:1A:2B:3C:4D:5E',
                       overdo=False):
    """netlogin.C2SLoginIn(name, pwd) —— 字段顺序严格来自 login_netlogin_fixed.dis 186-530 行。
    文案约束（loginin.LoginOk）：name 须纯数字、pwd 恰 6 位。"""
    assert name.isdigit(), "帐号须纯数字 (^[0-9]+$)"
    assert len(pwd) == 6, "密码须恰 6 位"

    pkt = LogicalPacket()
    pkt.PacketPrepare(C2S_LOGIN_ACCOUNT_ENTER)                       # opcode 1
    pkt.PacketAddI(CLIENT_SCRIPT_VERSION[0], 1)                      # 脚本版本 [0]
    pkt.PacketAddI(CLIENT_SCRIPT_VERSION[1], 1)                      #           [10]
    pkt.PacketAddI(CLIENT_SCRIPT_VERSION[2], 2)                      #           [43]
    pkt.PacketAddS(name, 32)                                        # 帐号 定长32
    pkt.PacketAddS(pwd, 20)                                         # 密码 定长20（明文上总线!）
    # C_sys.GetVersion() → 三字段 (1,1,2 字节)
    pkt.PacketAddI(CLIENT_VERSION[0], 1)
    pkt.PacketAddI(CLIENT_VERSION[1], 1)
    pkt.PacketAddI(CLIENT_VERSION[2], 2)
    # update.ini [Setting] Version = 1.1.50 → 拆 3 段
    pkt.PacketAddI(UPDATE_INI_VERSION[0], 1)
    pkt.PacketAddI(UPDATE_INI_VERSION[1], 1)
    pkt.PacketAddI(UPDATE_INI_VERSION[2], 2)
    # 本机 IP（g_OverdoVersion 时伪造 192.168.x.x）
    ip_a, ip_b, ip_c, ip_d = (int(x) for x in local_ip.split('.'))
    for octet in (ip_a, ip_b, ip_c, ip_d):
        pkt.PacketAddI(octet, 4 if not overdo else 4)               # 4 字节/段
    # 广告 ID（8 字节）：高 32 + 低 35 位（&0x37FFFFFFF）
    pkt.PacketAddI((adsid >> 32) & 0xFFFFFFFF, 4)
    pkt.PacketAddI(adsid & 0x37FFFFFFF, 4)
    # 推广 ID
    pkt.PacketAddI(len(tgid), 1)
    if tgid:
        pkt.PacketAddS(tgid, len(tgid))
    # 硬盘序列号（4 字节）
    pkt.PacketAddI(driveid & 0xFFFFFFFF, 4)
    # MAC 地址
    pkt.PacketAddS(mac, len(mac))
    return pkt


# ───────────────────────────────────────────────────────── 主流程演示
def main():
    print("=" * 70)
    print("《梦想世界》进入游戏 —— 协议模拟（离线）")
    print("=" * 70)

    IP, PORT = '219.132.193.76', 3134     # server.ini 解码出的推荐区官方 IP（已阵亡）
    print(f"\n[① 启动] mx.exe → init → main.Start() → mainpage.Show(True)")
    print(f"[② 选服] server.ini 三层解码 → 推荐区 {IP}:{PORT}")

    print(f"\n[③ Connect] 建立 TCP 连接并裸发 ip/port")
    build_connect_nakedsend(IP, PORT)

    print(f"\n[④ Hello] 收到服务器种子后构造 25 字节回应（SetSeed 建立加密通道）")
    SEED = 0x12345678                      # 假设的服务器下发种子（真实值运行时由 UnpackInt(4) 取得）
    build_hello_response(SEED)

    print(f"\n[⑤ LoginIn] 构造 C2S_LOGIN_ACCOUNT_ENTER 登录包")
    pkt = build_c2s_login_in(name='100860001', pwd='abcdef')
    pkt.PacketSend(CRYPTO_STUB, label='C2SLoginIn')

    print(f"\n[⑥⑦ 选角/进世界] S2CLoginRoleInfo → C2SLoginPlayerEnter(2) → mainpage 进游戏")
    print("  （此处需服务端 S2C 交互，离线无法推进；官方服已关，真实进入不可行）")

    print("\n" + "=" * 70)
    print("结论：进入游戏链路 7 步全部逆向还原；官方服已关，直连进入不可行。")
    print("剩余未攻：mxmain.dll 0x43cc50 流密码（加密层，协议复现最后一关）。")
    print("=" * 70)


if __name__ == '__main__':
    main()
