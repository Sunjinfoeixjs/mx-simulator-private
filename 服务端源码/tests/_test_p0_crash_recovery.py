# -*- coding: utf-8 -*-
"""P0 第三轮：异常退出、半帧断线与快速重连的隔离集成回归。"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
sys.path.insert(0, SIM)

import m4_server as m  # noqa: E402
from _test_singleplayer import recv_s2c, hello_resp  # noqa: E402

fails = []


def check(cond, msg):
    print(('OK   ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


def wait_empty(srv, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if srv.session_count() == 0:
            return True
        time.sleep(0.03)
    return srv.session_count() == 0


def test_abrupt_exit(root):
    savedir = os.path.join(root, 'crash_save')
    logdir = os.path.join(root, 'crash_logs')
    code = (
        "import os,sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import m4_server as m\n"
        "s=m.M4Server(savedir=sys.argv[2],logdir=sys.argv[3],enemies=1)\n"
        "s._save_async=False\n"
        "s.px=321\n"
        "ok=s._persist()\n"
        "s._logf.flush()\n"
        "os._exit(23 if ok else 91)\n"
    )
    proc = subprocess.run([sys.executable, '-c', code, SIM, savedir, logdir],
                          cwd=SIM, timeout=30,
                          env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    check(proc.returncode == 23,
          '隔离子进程在确认保存后以 os._exit(23) 异常退出')
    path = os.path.join(savedir, 'role.json')
    loaded = m.load_role(path)
    check(isinstance(loaded, dict) and loaded.get('px') == 321,
          '异常退出后已确认存档仍是完整 JSON（px=321）')

    recovered = m.M4Server(savedir=savedir,
                           logdir=os.path.join(root, 'recover_logs'), enemies=1)
    try:
        check(recovered.px == 321, '新进程可读取异常退出前已确认的数据')
        recovered._save_async = False
        recovered.px = 322
        check(recovered._persist() is True, '异常退出后顺序接管可继续保存')
        check((m.load_role(path) or {}).get('px') == 322,
              '恢复后的新数据正确落盘（px=322）')
    finally:
        recovered._logf.close()


def test_corrupt_guard(root):
    savedir = os.path.join(root, 'broken_save')
    os.makedirs(savedir, exist_ok=True)
    path = os.path.join(savedir, 'role.json')
    original = b'{broken p0 archive'
    with open(path, 'wb') as f:
        f.write(original)
    srv = m.M4Server(savedir=savedir,
                     logdir=os.path.join(root, 'broken_logs'), enemies=1)
    try:
        check(srv._save_blocked is True, '损坏存档触发写保护')
        check(srv._persist() is None, '损坏存档拒绝普通持久化')
        with open(path, 'rb') as f:
            check(f.read() == original, '损坏存档原始字节未被新角色覆盖')
    finally:
        srv._logf.close()


def test_half_frame_and_reconnect(root):
    srv = m.MultiServer('127.0.0.1', 0, m.SEED_DEFAULT, 0,
                        savedir=os.path.join(root, 'net_save'),
                        logdir=os.path.join(root, 'net_logs'),
                        max_sessions=4)
    port = srv.serve_background()
    try:
        c = socket.create_connection(('127.0.0.1', port), timeout=5)
        op, _body = recv_s2c(c)
        c.sendall(hello_resp(m.SEED_DEFAULT))
        c.sendall(b'\x01')               # 只有 opcode，没有 length/body
        c.close()
        check(op == m.OP_HELLO, '半帧断线前握手正常')
        check(wait_empty(srv), '半帧后断线会话资源已回收')

        # 高频短连接：连续握手/退出后服务端仍可继续接受连接，无会话泄漏。
        good = True
        for _ in range(20):
            x = socket.create_connection(('127.0.0.1', port), timeout=5)
            hop, _ = recv_s2c(x)
            good = good and hop == m.OP_HELLO
            x.close()
        good = good and wait_empty(srv)
        check(good, '连续 20 次快速连接/退出后仍可服务且无会话泄漏')

        y = socket.create_connection(('127.0.0.1', port), timeout=5)
        yop, _ = recv_s2c(y)
        y.close()
        check(yop == m.OP_HELLO, '异常与高频断线后仍接受新连接')
        check(wait_empty(srv), '最终会话计数归零')
    finally:
        srv._stop = True


def main():
    with tempfile.TemporaryDirectory(prefix='p0_t3_recovery_') as root:
        test_abrupt_exit(root)
        test_corrupt_guard(root)
        test_half_frame_and_reconnect(root)
    if fails:
        print('\nP0 异常恢复 FAIL（%d 项）' % len(fails))
        return 1
    print('\nP0 异常恢复 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
