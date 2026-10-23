# -*- coding: utf-8 -*-
"""诊断：0x43CC50 密钥流在 ~448 字节处到底发生了什么。

只做测量，不改模型。回答四个问题：
  1. DLL 多次重跑（fresh setup）的密钥流在哪个下标开始**彼此不一致**？
  2. DLL 与还原模型 `gen_table(start)[(cur+i)&0xFFF]` 在哪个下标开始**不一致**？
  3. 分块调用（小包，模拟真实客户端）与一次大调用，结果是否相同？
  4. 越过边界后的字节，是否等于「换一张表」（尝试用链上不同 word 生成表去匹配）？

用法: python probe_boundary.py [--seed 0x12345678] [--len 1024]
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80


def setup(d, seed):
    o = d.alloc(0x3000, 512, fill=0)
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    return o


def keystream(d, seed, off, n):
    """fresh setup 后一次调用 0x43CC50(n 字节零缓冲)，返回 n 字节密钥流。"""
    o = setup(d, seed)
    buf = d.alloc(n + 16, 16, data=bytes(n))
    d.call(0x43CC50, [buf, n], this=o + off, timeout=3000000)
    return d.read(buf, n)


def keystream_chunked(d, seed, off, n, chunk):
    """fresh setup 后按 chunk 字节分块调用，返回拼接后的 n 字节。"""
    o = setup(d, seed)
    buf = d.alloc(n + 16, 16, data=bytes(n))
    pos = 0
    while pos < n:
        k = min(chunk, n - pos)
        d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
        pos += k
    return d.read(buf, n)


def first_diff(a, b, start=0):
    for i in range(start, min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return None


def dump(label, data, lo, hi):
    seg = data[lo:hi]
    print('    %-14s [%d:%d] = %s' % (label, lo, hi, seg.hex(' ')))


def analyze(d, seed, name, off, cur, start, n):
    print('\n==== %s  seed=0x%08X  cursor=%d  start=0x%08X ====' % (name, seed, cur, start))
    tbl = mxc.table_bytes(start)
    model = bytes(tbl[(cur + i) & 0xFFF] for i in range(n))

    runs = [keystream(d, seed, off, n) for _ in range(4)]

    # 1) 多跑自一致
    self_div = None
    for i in range(n):
        if any(r[i] != runs[0][i] for r in runs[1:]):
            self_div = i
            break
    # 2) 与模型一致
    model_div = first_diff(runs[0], model)

    print('  runs 自一致到 : %s' % (self_div if self_div is not None else n))
    print('  模型匹配到     : %s' % (model_div if model_div is not None else n))

    # 3) 分块 vs 大调用
    for chunk in (16, 64, 128, 448, 512):
        ks = keystream_chunked(d, seed, off, n, chunk)
        d1 = first_diff(ks, runs[0])
        d2 = first_diff(ks, model)
        print('  chunk=%-4d  vs 大调用差异@%-5s  vs 模型差异@%-5s'
              % (chunk, d1, d2))

    # 4) 边界附近输出
    edge = model_div if model_div is not None else (self_div if self_div is not None else n)
    lo = max(0, edge - 8)
    hi = min(n, edge + 24)
    print('  ── 边界附近（edge=%d）──' % edge)
    dump('dll run0', runs[0], lo, hi)
    dump('dll run1', runs[1], lo, hi)
    dump('model', model, lo, hi)

    # 5) 越界后是否像「另一张表 / 前段重复」
    if model_div is not None and model_div + 4 <= n:
        tail = runs[0][model_div:model_div + 16]
        print('  ── 越界后 16 字节 = %s' % tail.hex(' '))
        m = min(16, n - model_div)
        hit = None
        for cand in range(0, len(tbl)):
            if all(tbl[(cand + k) & 0xFFF] == tail[k] for k in range(m)):
                hit = cand
                break
        print('     ↳ 本表内部重复位置: %s'
              % ('cursor+%d → 表[%d]' % (hit - cur, hit & 0xFFF) if hit is not None else '无'))
        print('     ↳ 全 0 = %s' % (tail == bytes(m)))
    return self_div, model_div


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='0x12345678')
    ap.add_argument('--len', type=int, default=1024)
    args = ap.parse_args()
    seed = int(args.seed, 0)
    n = args.len

    st = mxc.SEED_START.get(seed)
    if st is None:
        print('seed 0x%08X 不在 SEED_START，先补表' % seed)
        return 2
    d = Dll()
    analyze(d, seed, 'RX', RX, st[2], st[0], n)
    analyze(d, seed, 'TX', TX, st[3], st[1], n)
    return 0


if __name__ == '__main__':
    sys.exit(main())
