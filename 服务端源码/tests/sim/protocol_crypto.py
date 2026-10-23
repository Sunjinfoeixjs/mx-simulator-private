# -*- coding: utf-8 -*-
"""
《梦想世界》网络协议 —— 加密层参考实现与边界说明
==================================================
[已逆向确认的事实]  (mxmain.dll, capstone 静态分析)
  * 加密是「对称 XOR 流密码」，SetSeed 从同一握手种子派生收发两个方向的参数。
  * FUN_00430a60 将收发两组字段分别传给 FUN_00430790：
        - 发送字段位于 CNet +0x1088 / +0x108c
        - 接收字段位于 CNet +0x2098 / +0x209c
    它们是传给初始化/变换函数的上下文与参数槽；不能据此把字段偏移当作密钥流状态字节。
  * 启用开关: 对象 +0x72 字节 != 0 时, 收发均经 0x43cc50 变换; == 0 时明文裸发
  * NakedSend (Hello 25字节回应 / Connect 的 ip:port 裸发) 绕过加密
  * SetSeed 初始化两方向后置 [obj+0x72]=1
  * **0x43cc50 本体已还原（2026-10-19）** —— 见 `sim/mx_crypto.py`：
      - 0x43CC50 是基于 4096 字节密钥表的异或流（游标线性推进，收发各一份状态）
      - 密钥表由 0x43CA70 从 start 值链式生成（1024 字 × 每字 32 步混合）
      - start 由 seed 经 VM 最后一层混淆得到（实测采集表 SEED_START）
     原先「VMProtect 区段静态不可读」的判断只针对入口 thunk；实际生成器
      0x43CA70 在 .text 中未被虚拟化，可直接静态还原并经原版 DLL 逐字节验证。

[本模块]
  * Cipher 接口: 加解密同一操作 (XOR 流)
  * ReferenceStreamCipher: 占位参考实现 (LCG 种子流)，仅用于离线自测
  * RealCipher: 真实算法（转发到 mx_crypto.RealCipher）
"""

import struct

# ── Hello 握手混淆常数 (netlogin.py S2CHello consts 表) ──
# ★已定论（2026-10-15）：HELLO_C0 = 4294967296 = 0x100000000（2^32）。
#   证据：原始 marshal 字节 6c 03 00 00 00 00 00 00 00 04 00 → 15-bit digit 语义 = 2^32；
#   同文件 adsid 掩码字节 ff 7f ff 7f 03 00 → 15-bit 语义 = 0xFFFFFFFF（32 位掩码，锚点）。
#   旧值 0x400000000 来自 pymarshal26 曾用 16-bit 拼接读 long 的 bug，已修正。
HELLO_C0 = 4294967296    # 0x100000000
HELLO_MOD = 3227993
HELLO_ADD = 2562088
HELLO_XOR = 6636321

# ── CNet 字段位置 / 开关 (反编译确认) ──
OFF_SEND_CONTEXT = 0x1088
OFF_SEND_PARAMETER = 0x108C
OFF_RECV_CONTEXT = 0x2098
OFF_RECV_PARAMETER = 0x209C
OFF_CRYPTO_FLAG = 0x72    # 加密开关 (非0=启用)


def hello_obfuscate(server_seed: int) -> int:
    """客户端 Hello 25字节回应的种子混淆 (纯算术, 非 0x43cc50, 已验证与字节码一致)。"""
    return ((server_seed + HELLO_C0) % HELLO_MOD + HELLO_ADD) ^ HELLO_XOR


def hello_response(server_seed: int) -> bytes:
    """构造 Hello 回应 (NakedSend 明文, 25 字节): 3字节混淆种子 + 22个\\x00。"""
    obf = hello_obfuscate(server_seed)
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


class Cipher:
    """加解密接口: XOR 流密码, encrypt 与 decrypt 为同一操作。"""

    def reset(self, seed: int) -> None:
        raise NotImplementedError

    def crypt(self, data: bytes) -> bytes:
        raise NotImplementedError


class ReferenceStreamCipher(Cipher):
    """占位参考实现: 线性同余 (LCG) 种子流 XOR。

    注意: A / C 为占位常数, 真实 LCG 常数在被 VMProtect 保护的 0x43cc50 内,
    静态不可得。本类仅用于「自搭环境协议闭环」演示 —— 两端都用它即天然一致。

    一旦还原真实常数, 仅需替换本类 (或填入 RealCipher), 协议其余部分零改动。
    """

    MASK = 0xFFFFFFFF
    A = 0x9E3779B9   # 占位 (黄金比常数, 非真实)
    C = 0x1234567     # 占位

    def __init__(self, seed: int):
        self.reset(seed)

    def reset(self, seed: int) -> None:
        self.s = (seed ^ self.A) & self.MASK

    def crypt(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        for i, b in enumerate(data):
            self.s = (self.s * self.A + self.C) & self.MASK
            ks = (self.s >> 16) & 0xFF
            out[i] = b ^ ks
        return bytes(out)


class RealCipher(Cipher):
    """真实算法 —— 已还原，见 `sim/mx_crypto.py`（本类为薄适配层）。

    还原结论（2026-10-19，原版 mxmain.dll + unicorn 逐字节验证）：
      * 0x43CC50 = 基于 4096 字节密钥表的异或流，游标线性推进、收发各一份状态
      * 密钥表由 0x43CA70 从 start 值链式生成（每步 32 连档位混合）
      * start 由 seed 经 VM 最后一层混淆得到，已实测采集进 mx_crypto.SEED_START
    详见 mx_crypto 模块头部的完整说明。
    """

    def __init__(self, seed: int):
        from mx_crypto import RealCipher as _Impl
        self._impl = _Impl(seed)

    def reset(self, seed: int) -> None:
        self.__init__(seed)

    def crypt(self, data: bytes) -> bytes:
        return self._impl.crypt_tx(data)

    def crypt_tx(self, data: bytes) -> bytes:
        return self._impl.crypt_tx(data)

    def crypt_rx(self, data: bytes) -> bytes:
        return self._impl.crypt_rx(data)


def make_ciphers(seed: int, real: bool = False):
    """返回发送/接收方向各自独立的 cipher 实例。

    real=True 时返回真实算法（mx_crypto），否则返回占位参考实现。
    """
    cls = RealCipher if real else ReferenceStreamCipher
    return cls(seed), cls(seed)
