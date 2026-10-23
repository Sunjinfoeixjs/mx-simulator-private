# -*- coding: utf-8 -*-
"""Web 端口冲突：自动顺延 + 明确打印。

背景（2026-10-20）：旧实现直接 bind，端口被占就抛 OSError 让进程崩掉。
而 8080 是最容易被占的端口（另一个调试台、别的开发服务）。
单机工具链（singleplayer 会拉起本进程）更怕「起不来」，不怕「换了个端口」。

本测试锁定：
  1. 端口空闲 → 原样使用，不打印任何提示
  2. 端口被占 → 自动 +1 顺延，日志明确写出新旧端口
  3. 连续多个端口被占 → 顺延到第一个空闲的
  4. 显式 --web-port 被占 → 仍顺延，但打警告并提示「要固定请释放」
  5. port=0 → 交给 OS，不参与顺延
  6. MultiServer 与 M4Server 行为一致（两套实现不能漂移）
"""
import os
import socket
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


def occupy(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', port))
    s.listen(1)
    return s


def main():
    tmp = tempfile.mkdtemp(prefix='webport_')
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                     logdir=os.path.join(tmp, 'logs'))
    logs = []
    srv.log = lambda s, _L=logs: _L.append(s)

    # ── 1. 端口空闲：不动、不提示 ──
    #   用一个高位端口降低与真实服务撞车概率
    base = 18900
    if not m.M4Server._port_free(base):
        # 万一被占，往后找
        for i in range(1, 50):
            if m.M4Server._port_free(base + i):
                base += i
                break
    got, bumped, reason = srv._pick_web_port(base)
    check('端口空闲时原样返回', got == base and not bumped,
          'want=%d got=%d bumped=%r' % (base, got, bumped))
    check('端口空闲时 reason 为空', reason == '', 'reason=%r' % (reason,))

    # ── 2. 端口被占：+1 顺延 ──
    p2 = base + 10
    b2 = occupy(p2)
    try:
        got, bumped, reason = srv._pick_web_port(p2)
        check('端口被占时顺延到 +1', got == p2 + 1 and bumped,
              'want=%d got=%d' % (p2, got))
        check('顺延时 reason 说明新旧端口',
              str(p2) in reason and str(got) in reason, 'reason=%r' % (reason,))
    finally:
        b2.close()

    # ── 3. 连续多个被占 ──
    p3 = base + 20
    blockers = [occupy(p3 + i) for i in range(3)]
    try:
        got, bumped, reason = srv._pick_web_port(p3)
        check('连续 3 个端口被占时顺延到第 4 个', got == p3 + 3,
              'want=%d got=%d' % (p3, got))
    finally:
        for b in blockers:
            b.close()

    # ── 4. port=0 交给 OS ──
    got, bumped, reason = srv._pick_web_port(0)
    check('port=0 不参与顺延（交给 OS）', got == 0 and not bumped,
          'got=%r reason=%r' % (got, reason))

    # ── 5. 端到端：被占时启动面板，日志与 _web_port 一致 ──
    p5 = base + 30
    b5 = occupy(p5)
    try:
        logs[:] = []
        srv.serve_background(0, web=True, web_port=p5, explicit_web_port=False)
        time.sleep(0.6)
        joined = chr(10).join(logs)
        check('启动后 _web_port 已更新为顺延后的端口',
              srv._web_port == p5 + 1, '_web_port=%r' % (srv._web_port,))
        check('日志打印了「默认端口被占用」',
              '被占用' in joined and str(p5 + 1) in joined,
              [l for l in logs if '端口' in l][:1])
        check('日志打印了实际 URL（端口与 _web_port 一致）',
              ('http://127.0.0.1:%d/' % srv._web_port) in joined)
    finally:
        try:
            srv.stop()
        except Exception:                                   # noqa: BLE001
            pass
        b5.close()

    # ── 6. 显式 --web-port 被占：打警告 ──
    srv2 = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save2'),
                      logdir=os.path.join(tmp, 'logs2'))
    logs2 = []
    srv2.log = lambda s, _L=logs2: _L.append(s)
    p6 = base + 40
    b6 = occupy(p6)
    try:
        srv2.serve_background(0, web=True, web_port=p6, explicit_web_port=True)
        time.sleep(0.6)
        joined2 = chr(10).join(logs2)
        check('显式端口被占时打 ⚠ 警告',
              '⚠' in joined2 and '--web-port' in joined2,
              [l for l in logs2 if 'web-port' in l][:1])
        check('显式端口警告里提示「要固定请先释放」',
              '释放' in joined2)
    finally:
        try:
            srv2.stop()
        except Exception:                                   # noqa: BLE001
            pass
        b6.close()

    # ── 7. MultiServer 与 M4Server 口径一致 ──
    ms = m.MultiServer('127.0.0.1', 0, savedir=os.path.join(tmp, 'save3'),
                       logdir=os.path.join(tmp, 'logs3'))
    p7 = base + 50
    b7 = occupy(p7)
    try:
        got_a, bumped_a, _r = m.M4Server._pick_web_port(srv, p7)
        got_b, bumped_b, _r2 = m.M4Server._pick_web_port(ms, p7)
        check('MultiServer 复用同一套顺延逻辑（结果一致）',
              got_a == got_b and bumped_a == bumped_b,
              'M4=%d / Multi=%d' % (got_a, got_b))
    finally:
        b7.close()

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('Web 端口冲突：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
