# -*- coding: utf-8 -*-
"""最小模拟服（本地接管 Phase-1 v4）—— 确定性帧格式 + S2C 登录响应。

帧格式（★2026-10-22 勘误：两向不对称，别把下行格式套到上行）：
  S2C = [1B 长度 N][opcode 1B][data]，N **含** opcode（mxmain.dll FUN_00430fe0 确证〔A〕）
  C2S = [opcode 1B][1B 长度 N][body N]，N **不含** opcode（真机日志 + tap 分帧 desync=0 确证〔C〕）
  两侧 N==0xFF 均扩展 0xFF+2B u16。
  ⚠ 本文件是 M1 遗留件：`parse_login_frame()` 仍按旧假设（长度在前）解析上行帧，
    与真机不符；它落盘的 `*_after_handshake.bin` 是离线模拟器自造字节，不可当实测抓包。
    日常联调请用 m4_server.py（M2 阶段用 m2_server.py）。详见 协议资料/规格/M1_协议规格.md §1.3。
握手：
  ① 客户端 TCP 连接（非代理不发数据）
  ② 服务器发 Hello 帧 07 01 [seed 4B LE][servernum 2B LE]
  ③ 客户端回 25B NakedSend（3B 混淆种子 + 22 个 0x00）
  ④ 客户端发 C2SLoginIn 帧（补丁版明文）：[N][opcode=1][ver0 1B][ver1 1B][ver2 2B][name 32B][pwd 20B]...
  ⑤ 服务器回 S2CLoginAccountInfo 帧（opcode=2）：[N][02][Count 1B=0][CurrentTime 4B][vid 1B]
     → Count=0 触发 netlogin.EnterSelectRole() → 客户端进入选角界面

用法:
  python mock_server_min.py                 # 监听 127.0.0.1:3134（与 local.ini 对齐）
  python mock_server_min.py --seed 0x12345678 --linger 300

产物：Claw/logs/mock_srv_<时间>.log 与 ..._after_handshake.bin
"""
import argparse
import os
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from protocol_crypto import hello_response  # noqa: E402

CLAW = os.path.dirname(os.path.dirname(HERE))
LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')

SEED_DEFAULT = 0x12345678
SERVER_NUM = 1

OP_HELLO = 1                 # S2CHello
OP_LOGIN_ACCOUNT_INFO = 2    # S2CLoginAccountInfo
OP_LOGIN_ROLE_INFO = 3       # S2CLoginRoleInfo
OP_C2S_LOGIN = 1             # C2SLoginIn (客户端→服务器)


def u16le(b, o=0):
    return struct.unpack_from('<H', b, o)[0]


def make_frame(opcode, data=b''):
    """帧 = [1B N][opcode 1B][data]，N = 1 + len(data)。"""
    payload = bytes([opcode]) + data
    if len(payload) >= 0xFF:
        return b'\xFF' + struct.pack('<H', len(payload)) + payload
    return bytes([len(payload)]) + payload


def parse_login_frame(buf):
    """按确定帧格式拆客户端登录流 → [(opcode, data, info)]。

    ⚠ 历史缺陷（2026-10-22 未改行为，只留说明）：本函数的**上行**帧假设是
    `[N][opcode][body]`，来自把 S2C 收包逻辑外推到发送方向；真机 C2S 实为
    `[opcode][N][body]`。因此它在真机字节上会失步，`>>> C2SLoginIn` 一行不可作为
    M1 判据。正确实现见 m4_server.recv_c2s。
    """
    out = []
    off = 0
    while off < len(buf):
        n = buf[off]
        if n == 0xFF:
            if off + 3 > len(buf):
                break
            n = u16le(buf, off + 1)
            hdr = 3
        else:
            hdr = 1
        if off + hdr + n > len(buf):
            break
        payload = buf[off + hdr:off + hdr + n]
        if payload:
            op = payload[0]
            data = payload[1:]
            info = None
            if op == OP_C2S_LOGIN and len(data) >= 4 + 32 + 20:
                info = {'opcode': op,
                        'name': data[4:36].rstrip(b'\x00').decode('gbk', 'replace'),
                        'pwd': data[36:56].rstrip(b'\x00').decode('gbk', 'replace')}
            out.append((op, data, info))
        off += hdr + n
    return out


class MockServer:
    def __init__(self, host='127.0.0.1', port=3134, seed=SEED_DEFAULT,
                 linger=300.0, logdir=LOGDIR):
        self.host, self.port, self.seed, self.linger = host, port, seed, linger
        self.result = {}
        os.makedirs(logdir, exist_ok=True)
        ts = time.strftime('%Y%m%d_%H%M%S')
        self.logpath = os.path.join(logdir, 'mock_srv_%s.log' % ts)
        self.cappath = os.path.join(logdir, 'mock_srv_%s_after_handshake.bin' % ts)
        self._logf = open(self.logpath, 'a', encoding='utf-8')

    def log(self, s):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
            print(line.encode(enc, 'replace').decode(enc, 'replace'), flush=True)
        self._logf.write(line + '\n')
        self._logf.flush()

    def run(self, max_conn=None):
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(2)
        self.log('监听 %s:%d（seed=0x%08X servernum=%d）；日志 %s'
                 % (self.host, self.port, self.seed, SERVER_NUM, self.logpath))
        self.log('帧格式：1B长度 + 1B opcode（mxmain.dll 0x430fe0 确认）')
        n = 0
        try:
            while max_conn is None or n < max_conn:
                conn, addr = ls.accept()
                n += 1
                self.log('── 客户端接入 #%d: %s' % (n, addr))
                try:
                    self.handle(conn)
                except Exception as e:  # noqa: BLE001
                    self.log('会话异常: %r' % e)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
        except KeyboardInterrupt:
            self.log('收到中止信号，退出')
        finally:
            ls.close()

    def handle(self, conn):
        conn.settimeout(6)
        # ① 等待原生接收循环就绪，再发确定格式 Hello 帧
        time.sleep(0.05)
        hello = make_frame(OP_HELLO, struct.pack('<I', self.seed) + struct.pack('<H', SERVER_NUM))
        try:
            conn.sendall(hello)
            self.log('① 已发 Hello 帧 %dB: %s（seed=0x%08X）' % (len(hello), hello.hex(' '), self.seed))
        except OSError as e:
            self.log('① 发送 Hello 帧失败: %r' % e)
            return

        # ② 收 25B Hello 回应
        resp = b''
        t0 = time.time()
        while len(resp) < 25 and time.time() - t0 < 6:
            try:
                ch = conn.recv(25 - len(resp))
                if not ch:
                    break
                resp += ch
            except socket.timeout:
                break
            except ConnectionError:
                break
        self.log('② 客户端回应 %dB: %s' % (len(resp), resp[:40].hex(' ')))

        if len(resp) >= 3 and resp[3:] == b'\x00' * (len(resp) - 3) and resp[:3] != b'\x00\x00\x00':
            exp3 = hello_response(self.seed)[:3]
            ok = resp[:3] == exp3
            self.log('③ Hello 回应: %s' % resp.hex(' '))
            self.log('   校验: %s（期望前 3B: %s）' % ('PASS ✓' if ok else 'FAIL ✗', exp3.hex(' ')))
            self.result['hello_ok'] = ok
            if not ok:
                return
        elif not resp:
            self.log('   （无回应 —— 握手未开始，继续等待登录流）')
        else:
            self.log('   （回应非预期形态，继续收登录流）')

        # ③ 收登录包（客户端明文）
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
        open(self.cappath, 'wb').write(buf)
        self.log('④ 登录流 %dB，已存: %s' % (len(buf), self.cappath))
        if buf:
            self.log('   hex(前 160B): %s' % buf[:160].hex(' '))
            for op, data, info in parse_login_frame(buf):
                if info:
                    self.log('   >>> C2SLoginIn: 帐号=%s 密码=%s' % (info['name'], info['pwd']))
                    self.result['login'] = info
                else:
                    self.log('   帧 opcode=%d data=%dB（未识别）' % (op, len(data)))

        # ④ 回 S2CLoginAccountInfo（Count=0 → 客户端进选角界面）
        if self.result.get('login'):
            now = int(time.time()) & 0xFFFFFFFF
            reply = make_frame(OP_LOGIN_ACCOUNT_INFO,
                               struct.pack('<B', 0) + struct.pack('<I', now) + struct.pack('<B', 1))
            try:
                conn.sendall(reply)
                self.log('⑤ 已回 S2CLoginAccountInfo 帧 %dB: %s（Count=0 → 选角界面）'
                         % (len(reply), reply.hex(' ')))
            except OSError as e:
                self.log('⑤ 发送 S2C 登录响应失败: %r' % e)

        # ⑤ 保活（后续 S2C 回包在此扩展）
        self.log('⑥ 保持连接 %ds 中…（Ctrl+C 退出）' % self.linger)
        conn.settimeout(1.0)
        t0 = time.time()
        while time.time() - t0 < self.linger:
            try:
                ch = conn.recv(4096)
                if not ch:
                    break
                self.log('   （保活期间收到 %dB: %s）' % (len(ch), ch[:40].hex(' ')))
            except socket.timeout:
                continue
        self.log('── 会话结束')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3134)
    ap.add_argument('--seed', default=hex(SEED_DEFAULT))
    ap.add_argument('--linger', type=float, default=300.0)
    args = ap.parse_args()
    seed = int(args.seed, 0)
    srv = MockServer(args.host, args.port, seed, args.linger)
    srv.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
