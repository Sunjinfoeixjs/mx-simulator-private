# -*- coding: utf-8 -*-
"""P0 隔离稳定性：持续双账号登录、移动、战斗、保存和重连。

默认运行 30 分钟。所有角色与日志均写入显式的 --run-dir，不接触正式存档。
每分钟写一行进度，结束时生成 summary.json；任一轮失败立即以非零码退出。
"""
import argparse
import json
import os
import socket
import struct
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')
for _path in (SIM, HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import m4_server as m  # noqa: E402
from _test_multisession import hello_and_enter_existing, hello_and_login  # noqa: E402
from _test_singleplayer import drain_until, hello_resp, login_body, recv_s2c, send_c2s  # noqa: E402

SEED = 0x12345678
ACCOUNTS = ('p0soak_a', 'p0soak_b')


def _write_json(path, value):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _connect(port, account, exists):
    conn = socket.create_connection(('127.0.0.1', port), timeout=5)
    conn.settimeout(5)
    if exists:
        hello_and_enter_existing(conn, account)
    else:
        hello_and_login(conn, account)
    return conn


def _session(srv, account):
    with srv._lock:
        result = next((s for s in srv.sessions if s.account == account), None)
    if result is None:
        raise AssertionError('missing session for %s' % account)
    return result


def _drain_raw(conn):
    conn.settimeout(0.03)
    total = 0
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            total += len(data)
    except (socket.timeout, ConnectionError, OSError):
        pass
    finally:
        conn.settimeout(5)
    return total


def _wait_empty(srv, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with srv._lock:
            if not srv.sessions and not srv._account_owners:
                return True
        time.sleep(0.02)
    return False


def _duplicate_is_rejected(port, account):
    conn = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        op, data = recv_s2c(conn)
        if op != m.OP_HELLO or len(data) != 6:
            return False
        conn.sendall(hello_resp(SEED))
        send_c2s(conn, 1, login_body(account))
        try:
            recv_s2c(conn, timeout=0.5)
            return False
        except (ConnectionError, OSError):
            return True
    finally:
        conn.close()


def _validate_saves(savedir):
    values = []
    paths = []
    for account in ACCOUNTS:
        path = m.char_savepath(savedir, account)
        with open(path, 'r', encoding='utf-8') as fh:
            values.append(json.load(fh))
        paths.append(os.path.realpath(path))
    if len(set(paths)) != 2:
        raise AssertionError('accounts share one save path')
    if any(not isinstance(v, dict) or 'px' not in v or 'items' not in v for v in values):
        raise AssertionError('invalid save structure')
    return paths


def run(args):
    run_dir = os.path.abspath(args.run_dir)
    savedir = os.path.join(run_dir, 'save')
    logdir = os.path.join(run_dir, 'server_logs')
    os.makedirs(savedir, exist_ok=True)
    os.makedirs(logdir, exist_ok=True)
    summary_path = os.path.join(run_dir, 'summary.json')

    # 会话详细帧仍写各自日志，但不向控制台刷数十万行。
    m._SHUTTING_DOWN = True
    srv = m.MultiServer('127.0.0.1', 0, SEED, 0, logdir=logdir,
                        savedir=savedir, enemies=1, turn_timeout=10,
                        max_sessions=4, encrypt=False)
    srv.log = lambda _line: None
    port = srv.serve_background()
    started_wall = time.time()
    started_mono = time.monotonic()
    deadline = started_mono + args.duration
    tracemalloc.start()
    base_current, _ = tracemalloc.get_traced_memory()
    cycles = 0
    duplicate_checks = 0
    bytes_drained = 0
    last_progress = started_mono
    error = None
    print('START=%s duration=%ss port=%d run_dir=%s'
          % (time.strftime('%Y-%m-%dT%H:%M:%S%z'), args.duration, port, run_dir), flush=True)

    try:
        while time.monotonic() < deadline:
            exists = cycles > 0
            a = b = None
            try:
                a = _connect(port, ACCOUNTS[0], exists)
                b = _connect(port, ACCOUNTS[1], exists)
                sa = _session(srv, ACCOUNTS[0])
                sb = _session(srv, ACCOUNTS[1])
                if sa is sb or sa.savepath == sb.savepath:
                    raise AssertionError('session or save-path alias')

                # 两账号分别移动，实际走 socket 分派。
                # 成对往返，避免长时间单向移动最终越过 uint16 坐标边界。
                if cycles % 2 == 0:
                    moves = ((a, sa, 4), (b, sb, 3))
                else:
                    moves = ((a, sa, 2), (b, sb, 1))
                for conn, session, direction in moves:
                    send_c2s(conn, 54, struct.pack('<HH', session.px, session.py)
                             + bytes([1, direction]))
                    drain_until(conn, {86}, limit=80)

                # 交替覆盖战斗开始/结束和两账号保存。
                fighter = sa if cycles % 2 == 0 else sb
                if not fighter.gm_war_start().get('ok'):
                    raise AssertionError('war start rejected')
                if not fighter.gm_war_win().get('ok'):
                    raise AssertionError('war win rejected')
                if fighter.in_war:
                    raise AssertionError('battle state not reset')
                sa._persist()
                sb._persist()
                bytes_drained += _drain_raw(a) + _drain_raw(b)

                if cycles % 10 == 0:
                    if not _duplicate_is_rejected(port, ACCOUNTS[0]):
                        raise AssertionError('duplicate login was not rejected')
                    duplicate_checks += 1
            finally:
                if a is not None:
                    a.close()
                if b is not None:
                    b.close()
            if not _wait_empty(srv):
                raise AssertionError('session/account ownership leak after disconnect')
            _validate_saves(savedir)
            cycles += 1

            now = time.monotonic()
            if now - last_progress >= 60:
                current, peak = tracemalloc.get_traced_memory()
                progress = {
                    'status': 'RUNNING', 'cycles': cycles,
                    'elapsed_sec': round(now - started_mono, 3),
                    'sessions': srv.session_count(),
                    'owners': len(srv._account_owners),
                    'duplicate_checks': duplicate_checks,
                    'bytes_drained': bytes_drained,
                    'tracemalloc_current': current,
                    'tracemalloc_peak': peak,
                }
                _write_json(summary_path, progress)
                print('PROGRESS ' + json.dumps(progress, ensure_ascii=False, sort_keys=True), flush=True)
                last_progress = now
            remain = deadline - time.monotonic()
            if remain > 0:
                time.sleep(min(args.interval, remain))
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
    finally:
        srv._stop = True
        _wait_empty(srv, timeout=2)

    ended_mono = time.monotonic()
    current, peak = tracemalloc.get_traced_memory()
    growth = current - base_current
    result = {
        'status': 'PASS' if error is None and ended_mono - started_mono >= args.duration else 'FAIL',
        'start_epoch': started_wall,
        'elapsed_sec': round(ended_mono - started_mono, 3),
        'requested_sec': args.duration,
        'cycles': cycles,
        'duplicate_checks': duplicate_checks,
        'bytes_drained': bytes_drained,
        'final_sessions': srv.session_count(),
        'final_owners': len(srv._account_owners),
        'tracemalloc_current': current,
        'tracemalloc_peak': peak,
        'tracemalloc_growth': growth,
        'save_paths': _validate_saves(savedir) if cycles else [],
        'error': error,
    }
    if growth > args.max_growth_bytes:
        result['status'] = 'FAIL'
        result['error'] = 'tracemalloc growth %d > %d' % (growth, args.max_growth_bytes)
    _write_json(summary_path, result)
    print('FINAL ' + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=int, default=1800)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--max-growth-bytes', type=int, default=128 * 1024 * 1024)
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    if args.duration < 1 or args.interval < 0:
        parser.error('duration must be >=1 and interval must be >=0')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
