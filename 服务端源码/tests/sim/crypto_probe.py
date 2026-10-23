# -*- coding: utf-8 -*-
"""动态 trace 辅助 mock —— 触发握手/SetSeed/发包，客户端加密帧落盘。
原版 DLL 客户端连本服 → 发 Hello → 25B → 登录包【密文】落盘。
配合 x64dbg：断 mxmain.dll+0x3CC50 记录明文→密文 + VM 字节码。
用法: python crypto_probe.py [--seed 0x12345678] [--port 3134]
"""
import argparse
import os
import socket
import struct
import sys
import time

# 项目根按标志目录找（见 proj_root.py）：原先 `dirname×3` 在本布局下只到
# `MX/服务端源码`，探测产物会落到 `MX/服务端源码/服务端源码/logs`（P5-00 审计发现）。
from proj_root import find_project_root, fallback_root
CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')
SEED_DEFAULT = 0x12345678


def make_frame(opcode, data=b''):
    payload = bytes([opcode]) + data
    return (b'\xFF' + struct.pack('<H', len(payload)) + payload) if len(payload) >= 0xFF \
        else bytes([len(payload)]) + payload


def recv_exact(sock, n, timeout):
    buf = b''
    sock.settimeout(timeout)
    while len(buf) < n:
        ch = sock.recv(n - len(buf))
        if not ch:
            raise ConnectionError('连接断开')
        buf += ch
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3134)
    ap.add_argument('--seed', default=hex(SEED_DEFAULT))
    args = ap.parse_args()
    seed = int(args.seed, 0)
    os.makedirs(LOGDIR, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    logp = os.path.join(LOGDIR, 'probe_%s.log' % ts)

    def log(s):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
        print(line, flush=True)
        open(logp, 'a').write(line + '\n')

    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((args.host, args.port))
    ls.listen(2)
    log('抓包服 %s:%d（seed=0x%08X）' % (args.host, args.port, seed))
    n = 0
    while True:
        try:
            conn, addr = ls.accept()
        except KeyboardInterrupt:
            log('退出')
            break
        n += 1
        cap = os.path.join(LOGDIR, 'probe_%s_%02d.bin' % (ts, n))
        log('── 接入 #%d: %s → %s' % (n, addr, cap))
        try:
            conn.settimeout(30)
            time.sleep(0.05)
            conn.sendall(make_frame(1, struct.pack('<I', seed) + struct.pack('<H', 1)))
            log('① 已发 Hello 帧（seed=0x%08X）' % seed)
            try:
                resp = recv_exact(conn, 25, 8)
                log('② 25B 回应: %s' % resp.hex(' '))
            except (ConnectionError, socket.timeout) as e:
                log('② 未收到 25B: %r' % e)
                continue
            buf = b''
            t0 = time.time()
            while time.time() - t0 < 8:
                try:
                    ch = conn.recv(4096)
                    if not ch:
                        break
                    buf += ch
                except socket.timeout:
                    break
            open(cap, 'wb').write(buf)
            log('③ 登录包 %dB 落盘: %s' % (len(buf), cap))
            log('   hex: %s' % buf[:160].hex(' '))
            if not buf:
                log('   （无数据）')
        except Exception as e:  # noqa: BLE001
            log('会话异常: %r' % e)
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == '__main__':
    main()
