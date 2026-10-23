# -*- coding: utf-8 -*-
"""加密抓包模拟服（方案 B 辅助）—— 触发握手/SetSeed，接收并落盘客户端加密帧。

用途：配合 x64dbg 断点（0x30a60 SetSeed / 0x30ae0 发送加密 / 0x30b50 收包解密）。
客户端用【原版 mxmain.dll】（加密开启）连本服：
  ① 服务器发 Hello 帧（seed 可指定）→ 客户端 SetSeed（断点①触发）
  ② 客户端 25B NakedSend（明文，记录）
  ③ 客户端登录包【密文】→ 落盘 crypto_capture_<ts>.bin + 打印 hex
  ④ 服务器不回（保持连接）→ 客户端停在「正在登录中」→ 此时可从容记录断点

用法:
  python crypto_capture.py [--seed 0x12345678] [--port 3134]
"""
import argparse
import hashlib
import json
import os
import socket
import struct
import time

# 项目根按标志目录找（见 proj_root.py）：原先 `dirname×3` 在本布局下只到
# `MX/服务端源码`，抓包产物会落到 `MX/服务端源码/服务端源码/logs`（P5-00 审计发现）。
from proj_root import find_project_root, fallback_root
CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')
SEED_DEFAULT = 0x12345678


def make_frame(opcode, data=b''):
    payload = bytes([opcode]) + data
    if len(payload) >= 0xFF:
        return b'\xFF' + struct.pack('<H', len(payload)) + payload
    return bytes([len(payload)]) + payload


def recv_exact(sock, n, timeout):
    buf = b''
    sock.settimeout(timeout)
    while len(buf) < n:
        ch = sock.recv(n - len(buf))
        if not ch:
            raise ConnectionError('连接断开')
        buf += ch
    return buf


def save_capture(path, payload, metadata):
    """Write raw captured bytes and a JSON sidecar with reproducibility metadata."""
    digest = hashlib.sha256(payload).hexdigest()
    details = dict(metadata)
    details.update({
        'capture_file': os.path.basename(path),
        'bytes': len(payload),
        'sha256': digest,
    })
    with open(path, 'wb') as f:
        f.write(payload)
    with open(os.path.splitext(path)[0] + '.json', 'w', encoding='utf-8') as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return digest


def recv_window(sock, seconds, chunk_size=4096):
    """Read all available bytes until a monotonic deadline or peer close."""
    deadline = time.monotonic() + seconds
    chunks = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(chunk_size)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b''.join(chunks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3134)
    ap.add_argument('--seed', default=hex(SEED_DEFAULT))
    ap.add_argument('--capture-seconds', type=float, default=8.0)
    args = ap.parse_args()
    if args.capture_seconds <= 0:
        ap.error('--capture-seconds must be greater than zero')
    seed = int(args.seed, 0)
    if not 0 <= seed <= 0xFFFFFFFF:
        ap.error('--seed must be an unsigned 32-bit integer')

    os.makedirs(LOGDIR, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    logp = os.path.join(LOGDIR, 'crypto_capture_%s.log' % ts)
    capture_prefix = os.path.join(LOGDIR, 'crypto_capture_%s_XX.bin' % ts)

    def log(s):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
        print(line, flush=True)
        with open(logp, 'a') as f:
            f.write(line + '\n')

    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((args.host, args.port))
    ls.listen(2)
    log('加密抓包服 %s:%d（seed=0x%08X）落盘=%s' %
        (args.host, args.port, seed, capture_prefix))

    n = 0
    while True:
        try:
            conn, addr = ls.accept()
        except KeyboardInterrupt:
            log('退出')
            break
        n += 1
        log('── 接入 #%d: %s' % (n, addr))
        cap = os.path.join(
            LOGDIR, 'crypto_capture_%s_%02d.bin' % (ts, n))
        try:
            conn.settimeout(30)
            time.sleep(0.05)
            # ① Hello 帧
            conn.sendall(make_frame(1, struct.pack('<I', seed) + struct.pack('<H', 1)))
            log('① 已发 Hello 帧（seed=0x%08X）' % seed)
            # ② 25B 回应
            try:
                resp = recv_exact(conn, 25, 8)
                log('② 25B 回应: %s' % resp.hex(' '))
            except (ConnectionError, socket.timeout) as e:
                log('② 未收到 25B: %r' % e)
                continue
            # ③ 收客户端帧（加密）直到静默
            buf = recv_window(conn, args.capture_seconds)
            digest = save_capture(cap, buf, {
                'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'peer': '%s:%d' % addr,
                'seed': seed,
                'hello_response_hex': resp.hex(),
                'capture_seconds': args.capture_seconds,
                'note': 'Raw client TCP stream after Hello; encrypted bytes, not decoded frames.',
            })
            log('③ 收流 %dB（密文）: %s' % (len(buf), buf[:160].hex(' ')))
            log('   已落盘 %s（sha256=%s）' % (cap, digest))
            log('   元数据 %s' % (os.path.splitext(cap)[0] + '.json'))
            if not buf:
                log('   （无数据——客户端可能未发登录包，检查断点）')
        except Exception as e:  # noqa: BLE001
            log('会话异常: %r' % e)
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == '__main__':
    main()
