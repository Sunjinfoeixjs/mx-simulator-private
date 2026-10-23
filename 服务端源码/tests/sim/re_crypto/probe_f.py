# -*- coding: utf-8 -*-
"""求「下一张表 start」的递推 f：S_{i+1} = f(E_i)，E_i = gen_table(S_i)[-1]。

已知（probe_state 实测）：
  RX: S0=30881D1B E0=B60F6C04 S1=320F50BC E1=6EAF3821 S2=0A234223 E2=0817801B
  TX: S0=116C190B E0=F32E96B2 S1=6FE92948 E1=0D9632DB S2=176127D2 E2=5F1BF2C3
先验证 E_i == gen_table(S_i)[-1]，再暴力试 f 是否 = _step^k。
"""
import sys

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import mx_crypto as mxc  # noqa: E402

CHAINS = {
    'RX': [(0x30881D1B, 0xB60F6C04, 0x320F50BC, 0x6EAF3821, 0x0A234223, 0x0817801B)],
    'TX': [(0x116C190B, 0xF32E96B2, 0x6FE92948, 0x0D9632DB, 0x176127D2, 0x5F1BF2C3)],
}
MAXK = 1_500_000


def find_k(a, b, maxk=MAXK):
    v = a
    for k in range(maxk + 1):
        if v == b:
            return k
        v = mxc._step(v)
    return None


def main():
    for nm, rows in CHAINS.items():
        s0, e0, s1, e1, s2, e2 = rows[0]
        print('\n[%s]' % nm)
        print('  gen_table(S0)[-1] == E0 ? %s (0x%08X)' % (mxc.gen_table(s0)[-1] == e0, mxc.gen_table(s0)[-1]))
        print('  gen_table(S1)[-1] == E1 ? %s (0x%08X)' % (mxc.gen_table(s1)[-1] == e1, mxc.gen_table(s1)[-1]))
        print('  gen_table(S2)[-1] == E2 ? %s (0x%08X)' % (mxc.gen_table(s2)[-1] == e2, mxc.gen_table(s2)[-1]))
        k1 = find_k(e0, s1)
        k2 = find_k(e1, s2)
        print('  E0 -> S1 : _step^k  k=%s' % k1)
        print('  E1 -> S2 : _step^k  k=%s' % k2)
        if k1 is None:
            # 试 S0 -> S1 直接
            print('  S0 -> S1 : _step^k  k=%s' % find_k(s0, s1))


if __name__ == '__main__':
    main()
