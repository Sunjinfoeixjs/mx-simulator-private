# -*- coding: utf-8 -*-
"""mxmain.dll 自定义 LZSS（0x447610 Decompress / 0x447c10 Compress）的纯 Python 实现。

用途：模拟服「加密模式」下，服务器→客户端方向的报文必须经过 `Compress` 压缩
（客户端收包路径 `FUN_00430fe0` 在 +0x72=1 时无条件 `CLZWDecompress`）。
客户端→服务器方向**不压缩**（`FUN_00430ae0` 发送路径无压缩调用）。

格式（由反编译 + `_mwr_parse.py` 逐字节对齐确认）：
  · 块首 `0x11+n`        → n 个裸字面量（纯字面量快路径，n < 0xef）
  · ctrl < 0x10          → 字面量段 `ctrl+3` 字节（ctrl==0 走扩展长度）
  · ctrl 0x10..0x1f      → 匹配：len=ctrl&7（0=扩展），dist=(u16>>2)+(ctrl&8?2048:0)
  · ctrl 0x20..0x3f      → 匹配：len=ctrl&0x1f（0=扩展），dist=(u16>>2)+1
  · ctrl > 0x3f          → 匹配：len=(ctrl>>5)+1，dist=p[0]*8+((ctrl>>2)&7)+1
  · 每次匹配后跟 `ctrl & 3` 个裸字节
  · `11 00 00`           → dist 解析为 0，块结束哨兵

压缩策略：**纯字面量编码**（不找匹配）。对本服报文体积而言完全足够，
且天然合法 —— 客户端只做解压、从不比对压缩结果，字面量编码可被 `Decompress`
逐字节还原。相比照搬 0x447c10 的 888 字节哈希匹配器，字面量编码更简单、零出错面。
"""
import struct

EOC = b'\x11\x00\x00'


def _zero_run(block, i):
    """LZ 的「扩展长度」：连续 0 字节每字节加 0xff，再取非零字节。"""
    acc = 0
    while i < len(block) and block[i] == 0:
        i += 1
        acc += 0xff
    return acc, i


def decompress(block):
    """完整解压一个块（对应 mxmain.dll `Decompress` 0x447610）。

    返回解压后的字节串；非法回溯引用返回 None（与 _mwr_parse 口径一致）。
    """
    out = bytearray()
    i = 0
    if not block:
        return bytes(out)
    if block[0] >= 0x12:
        n = block[0] - 0x11
        i = 1
        out += block[i:i + n]
        i += n
    while i < len(block):
        ctrl = block[i]
        i += 1
        if ctrl < 0x10:                      # 字面量段
            if ctrl == 0:
                acc, i = _zero_run(block, i)
                if i >= len(block):
                    break
                n = acc + 0x0f + block[i]
                i += 1
            else:
                n = ctrl + 3
            out += block[i:i + n]
            i += n
            continue
        if ctrl > 0x3f:                      # 远距离短匹配
            if i >= len(block):
                break
            dist = block[i] * 8 + ((ctrl >> 2) & 7) + 1
            ln = (ctrl >> 5) + 1
            i += 1
        elif ctrl >= 0x20:                   # 中距离
            if i + 2 > len(block):
                break
            ln = ctrl & 0x1f
            off = struct.unpack_from('<H', block, i)[0]
            i += 2
            if ln == 0:
                acc, i = _zero_run(block, i)
                if i >= len(block):
                    break
                ln = acc + 0x1f + block[i]
                i += 1
            dist = (off >> 2) + 1
        else:                                # 0x10..0x1f：近距离 / 哨兵
            if i + 2 > len(block):
                break
            ln = ctrl & 7
            off = struct.unpack_from('<H', block, i)[0]
            i += 2
            if ln == 0:
                acc, i = _zero_run(block, i)
                if i >= len(block):
                    break
                ln = acc + 7 + block[i]
                i += 1
            dist = (off >> 2) + (2048 if (ctrl & 8) else 0)
            if dist == 0:
                break                        # `11 00 00` 结束哨兵
        if dist > len(out):
            return None                      # 非法回溯引用
        src = len(out) - dist
        for k in range(ln):
            out.append(out[src + k])
        n = ctrl & 3                         # 匹配后紧随的裸字节
        if n:
            out += block[i:i + n]
            i += n
    return bytes(out)


def compress(data):
    """纯字面量 LZSS 编码（合法、可被 `decompress` 逐字节还原，任意长度）。

    结构：若干字面量段（每段 4..18 字节，ctrl = n-3）+ 结尾 `11 00 00`。
    长度 1..3 用「块首字面量快路径」`0x11+n` 承载；长度 0 只发结束哨兵。
    """
    data = bytes(data)
    out = bytearray()
    n = len(data)
    if n == 0:
        out += EOC
        return bytes(out)
    if n < 4:
        out.append(0x11 + n)
        out += data
        out += EOC
        return bytes(out)
    i = 0
    while i < n:
        rem = n - i
        if rem <= 18:
            seg = rem
        else:
            # 取尽量大的段，但保证剩余不是 1/2/3（否则凑不成合法字面量段）
            seg = 18
            while (rem - seg) in (1, 2, 3):
                seg -= 1
        out.append(seg - 3)
        out += data[i:i + seg]
        i += seg
    out += EOC
    return bytes(out)
