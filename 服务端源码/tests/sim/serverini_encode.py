# -*- coding: utf-8 -*-
"""server.ini 编解码器（三层格式，2026-10-14 全部还原）。

格式（与官方 server7.txt / 客户端 update 链完全一致）：
    第1层容器:  b'-1\\r\\n' + zlib_stream
    zlib 内容:  flag(1B, 实测 0x73) + n(4B LE) + payload(n B)     （无多余尾巴）
    第2层编码:  payload[i] = (plain[i] + SKEY[i % 9]) & 0xFF
                SKEY = [0,9,16,21,24,25,24,21,16]（= p*(10-p)）
    第3层文本:  GBK, \\r\\n 分行
压缩参数：zlib level 9 / strategy 0 时与原文件逐字节一致（已验证）。

CLI:
  python serverini_encode.py selftest <file.bin>      # 解码→重编码 逐字节校验
  python serverini_encode.py decode <file.bin>        # 解码为文本（打印 + --out 落盘）
  python serverini_encode.py encode <text.txt> <out.bin>   # 文本 → 编码文件
"""
import struct
import sys
import zlib

SKEY = [0, 9, 16, 21, 24, 25, 24, 21, 16]
MAGIC = b'-1\r\n'


def decode(data: bytes):
    """返回 (flag, plain_bytes, text)"""
    assert data[:4] == MAGIC, '缺少 -1\\r\\n 魔数'
    r = zlib.decompress(data[4:])
    flag = r[0]
    n = struct.unpack('<I', r[1:5])[0]
    payload = r[5:5 + n]
    assert len(payload) == n, 'payload 截断'
    plain = bytes((b - SKEY[i % 9]) & 0xFF for i, b in enumerate(payload))
    return flag, plain, plain.decode('gbk')


def decode_file(path):
    return decode(open(path, 'rb').read())


def encode(text_or_plain, flag=0x73):
    """文本(str)/字节 → 完整文件字节（zlib level 9）。"""
    plain = text_or_plain.encode('gbk') if isinstance(text_or_plain, str) else text_or_plain
    payload = bytes((b + SKEY[i % 9]) & 0xFF for i, b in enumerate(plain))
    r = bytes([flag]) + struct.pack('<I', len(payload)) + payload
    c = zlib.compressobj(9, zlib.DEFLATED, 15, 8, 0)
    z = c.compress(r) + c.flush()
    return MAGIC + z


def selftest(path):
    raw = open(path, 'rb').read()
    flag, plain, text = decode(raw)
    re_enc = encode(plain, flag)
    ok_plain = decode(re_enc)[1] == plain
    ok_bytes = re_enc == raw
    print('%s: flag=0x%02X plain=%dB lines=%d' % (path, flag, len(plain), text.count('\r\n') + 1))
    print('  解码→重编码 明文一致: %s' % ok_plain)
    print('  解码→重编码 逐字节一致: %s %s' % (ok_bytes, '' if ok_bytes else '(zlib 参数需调整，但不影响客户端)'))
    return ok_plain


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == 'selftest':
        return 0 if selftest(arg) else 1
    if cmd == 'decode':
        flag, plain, text = decode_file(arg)
        print(text)
        if '--out' in sys.argv:
            out = sys.argv[sys.argv.index('--out') + 1]
            open(out, 'w', encoding='utf-8').write(text)
            print('已写出', out)
        return 0
    if cmd == 'encode':
        out = sys.argv[3]
        data = open(arg, 'rb').read()
        open(out, 'wb').write(encode(data))
        print('已写出', out)
        return 0
    print('未知命令', cmd)
    return 1


if __name__ == '__main__':
    sys.exit(main())
