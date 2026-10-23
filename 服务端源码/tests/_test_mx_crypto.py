# -*- coding: utf-8 -*-
"""回归测试：mx_crypto（0x43CC50 还原实现）。

这些向量由**原版 mxmain.dll** 在 unicorn 沙箱中逐字节采集，作为真值锚点，
不依赖 DLL 也不依赖 unicorn 即可运行。

覆盖：
  * 密钥表生成器（0x43CA70）：start -> 4096 字节表
  * 收发两方向异或流、跨包连续推进、加解密互逆
  * 已实测的种子真值向量
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import mx_crypto as mxc  # noqa: E402

_fail = []


def check(name, cond, extra=''):
    if cond:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s %s' % (name, extra))
        _fail.append(name)


def main():
    print('[1] 密钥表生成器 0x43CA70 —— 真值向量（原版 DLL 采集）')
    # tx_start 0x116C190B -> 表首 3 字
    t = mxc.gen_table(0x116C190B, 3)
    check('tx table[0..2]', t == [0xC511BC09, 0x0F96E94F, 0x0CC1E2D1],
          '%s' % ['%08X' % x for x in t])
    # rx_start 0x30881D1B -> 表首 3 字
    t = mxc.gen_table(0x30881D1B, 3)
    check('rx table[0..2]', t == [0xFE583FE5, 0x622D1EC4, 0x5A7AE70C],
          '%s' % ['%08X' % x for x in t])

    print('[2] 收发两方向初始密文（seed=0x12345678，原版 DLL 采集）')
    c = mxc.RealCipher(0x12345678)
    tx0 = c.crypt_tx(bytes(16))
    check('tx keystream[0:16]',
          tx0.hex() == '7d684dfd4990746c6b5e76e5b3b6bb7a', tx0.hex())
    rx0 = mxc.RealCipher(0x12345678).crypt_rx(bytes(16))
    check('rx keystream[0:16]',
          rx0.hex() == 'caa5ca34704a3dfce8289330ff484b58', rx0.hex())

    print('[3] 流属性')
    # 跨包连续：两次 8 字节 == 一次 16 字节
    a = mxc.RealCipher(0x12345678)
    split = a.crypt_tx(bytes(8)) + a.crypt_tx(bytes(8))
    check('跨包连续推进', split == tx0)
    # 加解密互逆
    msg = b'\x05C2S_LOGIN_ACCOUNT_ENTER'
    enc = mxc.RealCipher(1).crypt_tx(msg)
    dec = mxc.RealCipher(1).crypt_tx(enc)
    check('加解密互逆', dec == msg)
    # 异或线性：ct(pt) == pt ^ keystream
    rnd = bytes(range(32))
    ct = mxc.RealCipher(1).crypt_tx(rnd)
    ks = mxc.RealCipher(1).crypt_tx(bytes(32))
    check('ct = pt XOR ks', ct == bytes(x ^ y for x, y in zip(rnd, ks)))

    print('[4] 表结构')
    check('表长 4096 字节', len(mxc.table_bytes(0x30881D1B)) == 4096)
    check('读写方向相互独立',
          mxc.RealCipher(5).crypt_tx(bytes(4)) != mxc.RealCipher(5).crypt_rx(bytes(4)))

    print('[5] 已知限制')
    check('每个已收录 seed 都有确定性上限',
          all(s in mxc._USABLE for s in mxc.SEED_START))
    check('USABLE 常量存在', mxc.USABLE == 448)

    print()
    if _fail:
        print('FAILED: %d 项 %s' % (len(_fail), _fail))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
