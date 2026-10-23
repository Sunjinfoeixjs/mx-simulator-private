# -*- coding: utf-8 -*-
"""时间双通道单测（进程内，不起内核）：mxgw(op15) 漂移校验 + 日历(op192)。

背景：客户端有**两条**时间通道（net.py 路由表）：
  · C2S_TIME(15)  ← core/mxgw.GetTime       → 必须回 S2C op15，10000Hz 滴答
  · C2GS_TIME(61) ← dialog/dlgcalendar      → 回 S2C op192，Unix 秒
mxgw 那条是**在线校验**：`SecondGetResult` 里
    cdt = (客户端第二次GetTime - 第一次)/100
    sdt = (服务端第二次t - 第一次t) + 500
    cdt > sdt → blacklist.C2SResult(10,0) + Disconnect + 「网络错误(007)」

本测试守护三件事（都是真实踩过的）：
  1. op15 必须回 10000Hz 滴答而不是 Unix 秒（2026-10-16 修复：回秒每次必断 007）；
  2. op15 的时钟源必须**单调**（2026-10-20 加固：墙钟回跳会造出假漂移）；
  3. op192 必须回 Unix 秒（日历要显示真实日期），两条通道不许混。
"""
import struct
import sys
import time

SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))


def parse_s2c(frame):
    """[N 1B][op 1B][data]，N 含 opcode。"""
    return frame[1], frame[2:]


def mxgw_ticks_of(frame):
    op, data = parse_s2c(frame)
    return op, (struct.unpack('<I', data[:4])[0] if len(data) >= 4 else None)


def main():
    # ── 1. 通道分离：op15 ↔ op192 不混用 ──
    f15 = m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True)
    f192 = m.build_server_time(m.OP_TIME)
    op15, t15 = mxgw_ticks_of(f15)
    op192, t192 = mxgw_ticks_of(f192)
    check('op15 通道：opcode=15 且 4B 载荷',
          op15 == m.OP_MXGW_TIME and len(f15) == 6,
          'op=%d len=%d' % (op15, len(f15)))
    check('op192 通道：opcode=192 且 4B 载荷',
          op192 == m.OP_TIME and len(f192) == 6,
          'op=%d len=%d' % (op192, len(f192)))

    # ── 2. 量纲：op15 ≈ 10000Hz 单调时钟；op192 ≈ Unix 秒 ──
    #    time.monotonic() 的绝对值任意，只验证「量纲」：
    #    两次调用间隔真实经过 Δ 秒，滴答差应 ≈ Δ×10000（±20% + 100 容限）。
    a = mxgw_ticks_of(m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True))[1]
    t0 = time.monotonic()
    time.sleep(0.25)
    b = mxgw_ticks_of(m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True))[1]
    dt = (b - a) & 0xFFFFFFFF
    real = time.monotonic() - t0
    want = real * 10000
    ok = abs(dt - want) < max(want * 0.2, 100)
    check('op15 量纲 = 10000Hz 单调时钟（250ms → ≈2500 滴答）',
          ok, 'dt=%d want≈%.0f real=%.3fs' % (dt, want, real))

    wall = int(time.time())
    check('op192 量纲 = Unix 秒（与墙钟差 <2s）',
          abs(t192 - wall) < 2,
          't192=%d wall=%d' % (t192, wall))

    # ── 3. 单调性：墙钟被打断（回跳 1 小时）时 op15 滴答仍然递增 ──
    #    复现 mxgw 判定：cdt = 客户端增量/100，sdt = 服务端增量+500。
    #    若服务端用墙钟，回跳会让 sdt 变小甚至为负 → cdt > sdt → 误断 007。
    real_time = time.time
    base = real_time()
    seq = [base, base + 600.0, base + 600.0 - 3600.0]   # 第三次「回跳」一小时
    it = iter(seq)
    time.time = lambda: next(it)
    try:
        t1 = mxgw_ticks_of(m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True))[1]
        time.sleep(0.02)
        t2 = mxgw_ticks_of(m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True))[1]
        time.sleep(0.02)
        t3 = mxgw_ticks_of(m.build_server_time(m.OP_MXGW_TIME, mxgw_ticks=True))[1]
    finally:
        time.time = real_time
    d_server = t3 - t1          # ★不用掩码：客户端 UnpackInt(4) 是有符号值，
                                #   t<0 时补 0xFFFFFFFF，两次校正后相减——等价于原始差。
                                #   掩码差恒为正，会把「回跳」伪装成「巨量前进」而漏报。
    # 客户端两次查询间隔 = 40ms → cdt = 0.04s×10000/100 = 4 滴答
    cdt = 4
    sdt = d_server + 500        # 与 core/mxgw.py SecondGetResult 同式
    check('墙钟回跳 1 小时后 op15 仍单调递增（原始值严格大于）',
          t2 > t1 and t3 > t2,
          't1=%d t2=%d t3=%d' % (t1, t2, t3))
    check('mxgw 判定不误报：cdt(%d) <= sdt(%d)（用墙钟此处必 < → 假 007）'
          % (cdt, sdt),
          cdt <= sdt, 'd_server=%d' % d_server)

    # ── 4. 载荷恒为 4B u32（回绕语义由客户端 t<0 补偿逻辑处理）──
    big = m.make_s2c(m.OP_MXGW_TIME, struct.pack('<I', 0xFFFFFFF0))
    op, data = parse_s2c(big)
    check('载荷恒为 4B u32',
          op == m.OP_MXGW_TIME and len(data) == 4, 'len=%d' % len(data))

    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        for n in bad:
            print('  x %s' % n)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
