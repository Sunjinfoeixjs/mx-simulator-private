# -*- coding: utf-8 -*-
"""mxmain.dll 网络加密算法（0x43CC50）—— 已还原的纯 Python 实现
=================================================================

本文件是 `0x43CC50` 的**完整还原**，不依赖 DLL、不依赖 unicorn，
纯 Python 逐字节复现客户端加密流。

──────────────────────────────────────────────────────────────────
一、调用链（全部静态确认，非虚拟化）
──────────────────────────────────────────────────────────────────
  CNet 对象偏移：
    +0x72  加密开关（1 = 启用；SetSeed 末尾置 1）
    +0x80  RX 流状态（前向）+0x88 是 4096 字节密钥表
    +0x1090 TX 流状态（发送）+0x1098 是 4096 字节密钥表
    +0x1088/+0x108c  发送上下文/参数（A/B）
    +0x2098/+0x209c  接收上下文/参数（C/D）

  SetSeed (0x430A60)：
      (A,B,C,D) = 0x430790(seed)              ← 四路模运算扩展（见下）
      0x43CAF0(RX+0x80,  A)                    ← 清状态并把游标置 4096
      0x43CC50(RX+0x80,  NULL, B)              ← 填充 RX 密钥表
      0x43CAF0(TX+0x1090,C)
      0x43CC50(TX+0x1090,NULL, D)
      [this+0x72] = 1

  发送 0x430AE0 → 0x43CC50(TX+0x1090, buf, len)
  接收 0x430B50 → 0x43CC50(RX+0x80 , buf, len)

──────────────────────────────────────────────────────────────────
二、0x43CA70 —— 密钥表生成器（4096 字节 / 1024 个字）
──────────────────────────────────────────────────────────────────
  内层每步（eax 为状态）：
      ebx = eax
      eax = eax << 1                    ; 左移
      ebx = ebx & 0xDA3A2A9C            ; 掩码
      edx = ebx
      ebx = bswap(ebx)                  ; 字节序翻转
      bx ^= dx                          ; 低 16 位异或
      bh ^= bl                          ; 高字节异或低字节
      若 (bh^bl) 的奇偶为奇 → eax |= 1   ; 即 jp 未跳转时 inc eax
  外层 1024 次，每次 32 步，**把当前 eax 直接写入表**并继续链式推进。
  这一步已用原版 DLL + unicorn 逐字比对，6 个种子 × 2 方向 = 全 4096 字节 100% 一致。

──────────────────────────────────────────────────────────────────
三、0x43CC50 —— 异或流变换
──────────────────────────────────────────────────────────────────
      if cursor >= 4096:                ; 每个 cycle 结束（可在一次调用中途）
          table = fill(word)            ; 由 0x43CA70 生成，word = 上一张表的末字经 VM 推进
          cursor = 0
      for i in range(len):
          buf[i] ^= table[cursor]
          cursor = cursor + 1            ; 0..4096（不取模！到 4096 就换表）
  即：**按 4096 字节换表的异或流**，cursor 线性推进，收发各一份独立状态。

★ 边界／换表（2026-10-21 修订，推翻旧「448 字节」结论）：
  旧文档把「448~492 字节」当成算法边界，实测是**测量伪影**：
    · 一次调用塞 >~448 字节时，VM 内部缓冲溢出，输出后半段被写成 0（且随
      内存布局在不同次运行间漂移）→ measure3.py 取 min 得到 448；
    · 真实客户端按小包调用（收包：1B 长度 + N 字节体，N 通常 < 200），
      实测**全程与模型逐字节一致**，直到 cursor 跨过 4096。
  跨 4096 时 DLL 会用 VM 内部推进出的新 start **重填**一张表（可在一次调用
  中途发生）。新 start 满足 S_{i+1} = f(gen_table(S_i)[-1])，f 不可解析，
  故与 SEED_START 一样用实测真值表 `mx_fill_chain.FILL_CHAIN`（每 seed 一张
  [S0, S1, ...]）。链条够长时，密钥流可覆盖整局，不再有 448 的天花板。
  未采集链的 seed 退化为单表（只有第一个 cycle 正确）。

──────────────────────────────────────────────────────────────────
四、0x430790 —— seed 四路模扩展（非虚拟化，逐指令移植并验证）
──────────────────────────────────────────────────────────────────
  常数：0x6FFD, 0x53EF, 0x758B, 0x7159
  每轮 5 个 j（j=0,5），每步先 `|= 0x80000000`，再按 (j&1) 选两素数做
  ((v % p1) << 16) | (v % p2)，A/C 两路各 4 次/轮 → 共 40 次。
  末尾 B = ((A>>1)+(C>>1)) % 0xFFD，D = ((A>>1)+(C>>1)) % 0xFFB。
  注：函数内 A 与 C 是同一个种子初值分流，实测与 dll 一致。

──────────────────────────────────────────────────────────────────
五、方向 start 值
──────────────────────────────────────────────────────────────────
  表内容只由 start 决定，而 start 本身由 seed 经最后一层 VM 变换得到。
  已证实：start 忽略 seed 最高位（seed 与 seed|0x80000000 结果相同），
  且非线性（非 GF(2) 线性），是带密钥的混淆。
  因此本模块给出 `SEED_START` 真值表（由原版 DLL 实测采集），
  未收录的 seed 会返回 None，调用方需自行选择已知 seed 或扩充表。
"""

import struct

MASK32 = 0xFFFFFFFF
MIX_MASK = 0xDA3A2A9C
TABLE_SIZE = 4096

# 奇偶表：1 = 奇数个 1
_PARITY_ODD = bytes(bin(i).count('1') & 1 for i in range(256))


# ─────────────────────────── 0x43CA70 生成器 ───────────────────────────
def _step(eax):
    """0x43CA92..0x43CAA5 单步。"""
    ebx = eax
    eax = (eax << 1) & MASK32
    ebx &= MIX_MASK
    edx = ebx
    ebx = int.from_bytes(ebx.to_bytes(4, 'little'), 'big')      # bswap
    bx = ((ebx & 0xFFFF) ^ (edx & 0xFFFF)) & 0xFFFF             # xor bx, dx
    ebx = (ebx & 0xFFFF0000) | bx
    bh = (ebx >> 8) & 0xFF
    bl = ebx & 0xFF
    if _PARITY_ODD[bh ^ bl]:                                    # jp 不跳 → inc
        eax = (eax + 1) & MASK32
    return eax


def gen_table(start, words=1024):
    """由 start 生成密钥表（默认 1024 个字 = 4096 字节）。"""
    out = []
    eax = start & MASK32
    for _ in range(words):
        for _ in range(32):
            eax = _step(eax)
        out.append(eax)
    return out


def table_bytes(start):
    return struct.pack('<1024I', *gen_table(start))


# ─────────────────────────── seed → start 真值表 ───────────────────────────
# 由原版 mxmain.dll 实测采集（unicorn 沙箱逐字节验证）。
# 格式： seed: (rx_start, tx_start, rx_cursor, tx_cursor, usable_bytes)
#   start   = 0x43CA70 生成器的输入（密钥表 = gen_table(start)）
#   cursor  = SetSeed 后该方向的读指针（= 预热字节数 B/D）
#   usable  = 该方向一次填充真正可用的密钥流长度（实测）
SEED_START = {
    0x00000000: (0x398E1111, 0x61E110DC, 1097, 278, 448),
    0x00000001: (0x0D243207, 0x24BD2C07, 3387, 1898, 448),
    0x00000002: (0x2E201549, 0x52584A3E, 1575, 1346, 488),
    0x00000003: (0x21331C10, 0x4CA81E79, 4050, 1005, 46),
    0x00000004: (0x01372C64, 0x00330834, 1275, 65, 448),
    0x00000005: (0x3B9634A2, 0x49F152FC, 477, 2387, 448),
    0x00000010: (0x0F273F77, 0x357034E7, 2419, 1938, 488),
    0x000000FF: (0x68933FE6, 0x1057111F, 2448, 1398, 488),
    0x00000100: (0x28864592, 0x310748D6, 3980, 3065, 116),
    0x00010000: (0x008C4F00, 0x2505514A, 1284, 1462, 448),
    0x0A5E0BE4: (0x501C1A0E, 0x4FBC4AC8, 2823, 146, 448),
    0x12345678: (0x30881D1B, 0x116C190B, 2654, 3044, 448),
    0x80000000: (0x398E1111, 0x61E110DC, 1097, 278, 448),
    0xDEADBEEF: (0x5F662B1B, 0x6E915773, 2472, 3141, 448),
    0xFFFFFFFF: (0x6BA84602, 0x02803B94, 2076, 1197, 448),
}

# 【2026-10-21 修订】旧的「确定性上限」= 大缓冲单调用的输出截断点（VM 内部溢出，
# 后半段写 0，且随内存布局漂移），**不是算法边界**。真实客户端按小包调用，实测
# 与模型逐字节一致直到 cursor 跨 4096（换表点）。此表仅为兼容旧测试保留。
USABLE = 448
_USABLE = {
    0x00000000: (448, 448), 0x00000001: (448, 448), 0x00000002: (448, 448),
    0x00000003: (46, 448), 0x00000004: (448, 448), 0x00000005: (448, 448),
    0x00000010: (448, 448), 0x000000FF: (448, 488), 0x00000100: (116, 448),
    0x00010000: (448, 448), 0x0A5E0BE4: (448, 448),
    0x12345678: (448, 448), 0x80000000: (448, 448),
    0xDEADBEEF: (448, 448), 0xFFFFFFFF: (448, 448),
}


# ─────────────────────────── 0x43CC50 流变换 ───────────────────────────
class MXStream(object):
    """单个方向的异或流（对应 CNet+0x80 或 +0x1090 的状态）。

    cursor 0..4095，跨过 4096 时**重填**一张新表（不是取模复用旧表）。
    `chain` = 每次重填的 start 序列 [S0, S1, S2, ...]，由 `re_crypto/collect_chain.py`
    实测采集（与 SEED_START 同性质的真值表）。只给 [S0] 时退化为「单表取模复用」
    —— 仅第一个 cycle（4096-cursor 字节）正确，之后与客户端发散。
    链耗尽后同样退化为单表复用（并置 `exhausted`，服务端可据此告警）。
    """

    def __init__(self, start, cursor=0, chain=None):
        self.chain = list(chain) if chain else [start & MASK32]
        self.idx = 0
        self._tables = {0: table_bytes(self.chain[0])}
        self.cursor = cursor & 0xFFF
        self.table = self._tables[0]
        self.exhausted = False

    def _table(self, idx):
        t = self._tables.get(idx)
        if t is None:
            t = table_bytes(self.chain[idx])
            self._tables[idx] = t
        return t

    def crypt(self, data):
        out = bytearray(len(data))
        cur = self.cursor
        idx = self.idx
        tbl = self._table(idx)
        chain = self.chain
        for i, b in enumerate(data):
            if cur == 4096:
                if idx + 1 < len(chain):
                    idx += 1
                    tbl = self._table(idx)
                else:
                    self.exhausted = True      # 链短于流量：复用末表（必然发散）
                cur = 0
            out[i] = b ^ tbl[cur]
            cur += 1
        self.cursor = cur
        self.idx = idx
        self.table = tbl
        return bytes(out)


def fill_chain_for(seed):
    """取某 seed 的 wrap 重填链（真值表模块 mx_fill_chain；缺失则 None）。

    返回 {'rx': [S0, S1, ...], 'tx': [...]} 或 None。
    缺链时 RealCipher 退化为单表（只有第一个 cycle 正确）。
    """
    try:
        from mx_fill_chain import FILL_CHAIN
    except ImportError:
        return None
    return FILL_CHAIN.get(seed & MASK32)


class RealCipher(object):
    """客户端真实加密：收发两个独立方向。

    与 CNet 一致：crypt_tx 走 +0x1090，crypt_rx 走 +0x80。
    同一方向跨包连续推进（TCP 有序），与客户端行为一致。
    若存在该 seed 的 wrap 重填链，则跨 4096 字节自动换表（可支撑整局）。
    """

    def __init__(self, seed):
        entry = SEED_START.get(seed & MASK32)
        if entry is None:
            raise KeyError(
                'seed 0x%08X 未收录：start 值由 VM 最后一层生成，'
                '请从原版 DLL 采集后加入 mx_crypto.SEED_START' % seed)
        rx_start, tx_start, rx_cur, tx_cur, _u = entry
        self.seed = seed & MASK32
        chain = fill_chain_for(self.seed) or {}
        # 与客户端一致：RX / TX 各自独立的读游标 + 重填链
        self.rx = MXStream(rx_start, cursor=rx_cur, chain=chain.get('rx'))
        self.tx = MXStream(tx_start, cursor=tx_cur, chain=chain.get('tx'))
        # 旧的「确定性上限」（实为大缓冲单调用伪影；保留供旧测试引用）
        self.rx_usable, self.tx_usable = _USABLE[seed & MASK32]

    def crypt_tx(self, data):
        return self.tx.crypt(data)

    def crypt_rx(self, data):
        return self.rx.crypt(data)

    # 便于与旧接口互操作
    def crypt(self, data):
        return self.crypt_tx(data)

    def reset(self, seed):
        self.__init__(seed)
