# -*- coding: utf-8 -*-
"""单机测试模拟服 v2 —— 正确的 C2S 帧格式。

★帧格式实测修正（2026-10-15 原始流 dump 确认）：
  S2C（服务器→客户端，0x430fe0 确认）: [1B 长度 N][opcode 1B][data]
  C2S（客户端→服务器，实测）        : [opcode 1B][1B 长度 N][body N]
  C2S 登录包实例: 01 5e 00 0a 2b 00 61 64 63 64 31 32 33...  (op=1 len=0x5E body=94B)

链路:
  ① TCP → 服务器发 S2C Hello 帧 [07][01][seed 4B][servernum 2B]
  ② 客户端回 25B（C0=0x100000000）
  ③ 收 C2S [01][len][C2SLoginIn body] → 已有角色: 回 AccountInfo(Count=1)+RoleInfo；无角色: Count=0
  ④ 建角 [03] → 回 S2C [06] 创建成功 + AccountInfo(1)+RoleInfo
  ⑤ 选角界面 C2SAccount [05 isub16] → 回 S2CAccountManage [07] type=15（角色列表每连接只下发一次，避免循环）
  ⑥ 选角进入 [02] → 回 PropInit(op16)+SceneM(op96)+GotoM(op97)
  ⑦ 移动 [54] → Track；PING [08] → 250；敏感词查询 [06] → BandWord [29]

用法: python m2_server.py [--seed 0x12345678] [--port 3134]
"""
import argparse
import os
import socket
import struct
import sys
import time

# 项目根按标志目录找（见 proj_root.py）：原先 `dirname×3` 在本布局下只到
# `MX/服务端源码`，日志会落到 `MX/服务端源码/服务端源码/logs`（P5-00 审计发现）。
from proj_root import find_project_root, fallback_root
CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')
SEED_DEFAULT = 0x12345678
SERVER_NUM = 1
OP_C2S_PING = 8
OP_C2GS_PING = 250

# ---- 进世界默认场景（map/1001.map 南遥国新手场景）----
SCENE_ID = 1
MAP_ID = 1001
MINIMAP_ID = 1001
SPAWN_X = 45
SPAWN_Y = 40


def make_s2c(opcode, data=b''):
    """S2C 帧: [1B 长度 N][opcode 1B][data]，N 含 opcode。"""
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


def recv_c2s(conn, timeout=30):
    """C2S 帧: [opcode 1B][1B 长度 N][body N]，N==0xFF 时扩展 0xFF+2B u16。"""
    op = recv_exact(conn, 1, timeout)[0]
    ln = recv_exact(conn, 1, timeout)[0]
    if ln == 0xFF:
        ln = struct.unpack('<H', recv_exact(conn, 2, timeout))[0]
    body = recv_exact(conn, ln, timeout)
    return op, body


def hello_response(server_seed):
    obf = ((server_seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def build_login_account_info(count=0, vid=1):
    now = int(time.time()) & 0xFFFFFFFF
    return make_s2c(2, struct.pack('<B', count) + struct.pack('<I', now) + struct.pack('<B', vid))


def var_gbk(s):
    """变长字符串（UnpackString(0)）：GBK 字节 + \\0 结尾（C 字符串，无长度前缀）。
    ★实测修正（2026-10-15 客户端截图）：旧 [1B len] 前缀被客户端渲染为名字前的「口」。"""
    return s.encode('gbk', 'replace') + b'\x00'


def build_prop_init(hero_id, name):
    """S2CPropInit（opcode 16）：id 4B + 67 项属性，字段序见 docs/M2_协议规格.md §2。

    最小合法值：m_Desc=n=0（1B），定长域全 0，HP/MP/FP/SP 给默认值便于 HUD 显示。
    返回帧（不含长度头）。字段总宽 = 220B（含 m_Desc 的 1B）。
    """
    p = bytearray()
    p += struct.pack('<B', 0)                      # 0  m_Icon
    p += struct.pack('<H', 1)                      # 1  m_Shape
    p += b'\x00'                                   # 2  m_Desc (n=0)
    p += struct.pack('<B', 1)                      # 3  m_Sex (1=男)
    p += name.encode('gbk', 'replace')[:16].ljust(20, b'\x00')  # 4  m_Name
    p += struct.pack('<B', 1)                      # 5  m_Grade
    p += struct.pack('<II', 0, 0)                  # 6  m_Cash (TLONG 8B)
    p += struct.pack('<I', 0)                      # 7  m_Saving
    p += struct.pack('<I', 0)                      # 8  m_Exp
    p += struct.pack('<I', 1000)                   # 9  m_NextExp
    p += struct.pack('<I', 1000)                   # 10 m_NeedExp
    p += struct.pack('<B', 1)                      # 11 m_Class (战士)
    p += struct.pack('<B', 1)                      # 12 m_Country (南遥国)
    p += b'\x00' * 20                              # 13 sOrg
    p += struct.pack('<B', 0)                      # 14 m_EmoteForTitle
    p += b'\x00' * 22                              # 15 m_Title
    p += struct.pack('<HHHHHHHHHHHH',              # 16-27 12×TWORD
                     200, 200, 100, 100, 100, 100, 80, 80, 100, 100, 100, 100)  # HP HP_Max MP MP_Max FP FP_Max SP SP_Max Huoli Huoli_Max Tili Tili_Max
    p += struct.pack('<HHHHHHHHHH', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)  # 28-37 10×TWORD（Att/Def/Spe/Magdef/Str/Mag/Cha/Dex/Con/Point）
    p += struct.pack('<I', 0)                      # 38 m_Switch
    p += struct.pack('<B', 0)                      # 39 m_GridPoint
    p += struct.pack('<I', 0)                      # 40 m_SkillExp
    p += struct.pack('<I', 0)                      # 41 m_PFPoint
    p += struct.pack('<H', 0)                      # 42 m_StoryPoint
    p += struct.pack('<I', 1000)                   # 43 m_Gold
    p += struct.pack('<I', 0)                      # 44 m_UI
    p += struct.pack('<I', 0)                      # 45 m_DoubleExp
    p += struct.pack('<I', 0)                      # 46 m_BuChangExp
    p += struct.pack('<I', 0)                      # 47 m_OrgOffer
    p += struct.pack('<I', 0)                      # 48 m_OrgOffer_Max
    p += struct.pack('<I', 0)                      # 49 m_OrgCoin
    p += struct.pack('<I', 0)                      # 50 m_CookPoint
    p += struct.pack('<I', 0)                      # 51 m_FightCash
    p += struct.pack('<I', 0)                      # 52 m_ShenWuBi
    p += struct.pack('<B', 6)                      # 53 m_SummonMax
    p += struct.pack('<B', 20)                     # 54 m_BagMax
    p += struct.pack('<B', 0)                      # 55 m_VIP
    p += struct.pack('<B', 0)                      # 56 m_FlyFlag
    p += struct.pack('<I', 0)                      # 57 m_Child
    p += struct.pack('<B', 0)                      # 58 m_GradeLimit
    p += struct.pack('<B', 0)                      # 59 m_Force
    p += struct.pack('<B', 0)                      # 60 m_TitleColor
    p += struct.pack('<H', 1)                      # 61 m_RealShape
    p += struct.pack('<B', 0)                      # 62 m_XiulianTotalNeed
    p += struct.pack('<I', 0)                      # 63 m_PhotoEffect
    p += struct.pack('<I', 0)                      # 64 m_IsGuestLogin
    p += struct.pack('<H', 0)                      # 65 m_Birthday
    p += struct.pack('<H', 0)                      # 66 m_AutoUpgrade
    assert len(p) == 220, len(p)
    return make_s2c(16, struct.pack('<I', hero_id) + bytes(p))


def build_scene_m(mapid=MAP_ID):
    """S2CMapSceneM（opcode 96）：scene(2B) mapid(2B) minimapid(2B) x(2B) y(2B) 场景名(变长)。"""
    return make_s2c(96, struct.pack('<HHHHH', SCENE_ID, mapid, MINIMAP_ID, SPAWN_X, SPAWN_Y)
                    + var_gbk('南遥国'))


def build_goto_m():
    """S2CMapGotoM（opcode 97）：x(2B) y(2B) 角色定点。"""
    return make_s2c(97, struct.pack('<HH', SPAWN_X, SPAWN_Y))


class M2Server:
    def __init__(self, host='127.0.0.1', port=3134, seed=SEED_DEFAULT, logdir=LOGDIR):
        self.host, self.port, self.seed = host, port, seed
        self.hero_id = 1
        self.hero_name = ''
        self.have_role = False
        # 每个连接独立：角色列表只在登录/建角后下发一次，避免选角面板
        # init 重发 C2SAccount(op5) 触发客户端无限重进选角循环
        self.role_listed = False
        os.makedirs(logdir, exist_ok=True)
        self.logpath = os.path.join(logdir, 'm2_srv_%s.log' % time.strftime('%Y%m%d_%H%M%S'))
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

    def _send_role_list(self, conn):
        """下发 S2CLoginAccountInfo(Count=1) + S2CLoginRoleInfo —— 每个连接仅一次。"""
        conn.sendall(build_login_account_info(1))
        conn.sendall(make_s2c(3, struct.pack('<I', self.hero_id) + struct.pack('<I', 0)
                              + struct.pack('<B', 1) + struct.pack('<H', 0) + struct.pack('<H', 0)
                              + struct.pack('<B', 1) + struct.pack('<B', 0) + struct.pack('<B', 1)
                              + struct.pack('<i', 0)
                              + self.hero_name.encode('gbk', 'replace')[:16].ljust(16, b'\x00')))
        self.role_listed = True
        self.log('   已回 AccountInfo(1)+RoleInfo')

    def run(self):
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(2)
        self.log('模拟服 v2 %s:%d（seed=0x%08X）' % (self.host, self.port, self.seed))
        while True:
            try:
                conn, addr = ls.accept()
            except KeyboardInterrupt:
                break
            self.log('── 接入: %s' % (addr,))
            try:
                self.handle(conn)
            except Exception as e:  # noqa: BLE001
                self.log('异常: %r' % e)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
        ls.close()

    def handle(self, conn):
        time.sleep(0.05)
        conn.sendall(make_s2c(1, struct.pack('<I', self.seed) + struct.pack('<H', SERVER_NUM)))
        self.log('① S2C Hello 帧已发')
        try:
            resp = recv_exact(conn, 25, 6)
        except (ConnectionError, socket.timeout):
            self.log('② 握手未完成')
            return
        ok = resp[:3] == hello_response(self.seed)[:3] and resp[3:] == b'\x00' * 22
        self.log('② Hello %s（%s）' % ('PASS ✓' if ok else 'FAIL ✗', resp[:3].hex(' ')))
        if not ok:
            return
        self.role_listed = False   # 新连接：角色列表尚未下发
        conn.settimeout(60)
        while True:
            try:
                op, body = recv_c2s(conn, 300)
            except (ConnectionError, socket.timeout):
                self.log('   （连接结束/超时）')
                break
            self.log('③ C2S opcode=%d body=%dB: %s' % (op, len(body), body[:48].hex(' ')))
            if op == OP_C2S_PING:
                conn.sendall(make_s2c(OP_C2GS_PING))
                self.log('   心跳→250')
                continue
            if op == 1 and len(body) >= 56:
                name = body[4:36].rstrip(b'\x00').decode('gbk', 'replace')
                pwd = body[36:56].rstrip(b'\x00').decode('gbk', 'replace')
                self.log('   >>> 登录: 帐号=%s 密码=%s' % (name, pwd))
                if self.have_role:
                    # 登录响应自带角色列表（真服行为），避免选角面板再发 op5 触发循环
                    self._send_role_list(conn)
                else:
                    conn.sendall(build_login_account_info(0))
                    self.log('   已回 AccountInfo(Count=0) → 无角色界面')
            elif op == 3 and len(body) >= 24:
                nm = body[0:20].rstrip(b'\x00').decode('gbk', 'replace')
                self.log('   >>> 建角: %s' % nm)
                self.hero_name = nm
                self.have_role = True
                conn.sendall(make_s2c(6))
                self.log('   已回创建成功 → 下发角色列表')
                self._send_role_list(conn)
            elif op == 5 and len(body) >= 1 and body[0] == 16:
                if not self.role_listed and self.have_role:
                    # 兜底：客户端尚未收到角色列表时补发（不会触发循环，
                    # 因为之后 role_listed=True，重复 op5 走下方 AccountManage）
                    self._send_role_list(conn)
                else:
                    # C2SAccount(isub16)（选角面板查询）→ S2CAccountManage op7
                    # type=15 仅设置 selectrole.g_AccountFlag，无 UI 副作用
                    conn.sendall(make_s2c(7, struct.pack('<BB', 15, 0)))
                    self.log('   已回 AccountManage(op7) type=15 查询')
            elif op == 6 and len(body) >= 5:
                # C2SBanVer(type, ver) → S2CBandWord op29，recvtime=0 不写词库文件
                btype = body[0]
                conn.sendall(make_s2c(29, struct.pack('<BBI', btype, 0, 0) + b'\x00'))
                self.log('   已回 BandWord(op29) type=%d ver=0' % btype)
            elif op == 2 and len(body) >= 4:
                self.hero_id = struct.unpack('<I', body[:4])[0]
                self.log('   >>> 选角进入 id=%d name=%s' % (self.hero_id, self.hero_name))
                # 进世界序列: PropInit → SceneM → GotoM（docs/M2_协议规格.md §4）
                conn.sendall(build_prop_init(self.hero_id, self.hero_name))
                self.log('   已回 PropInit(op16) %dB' % len(build_prop_init(self.hero_id, self.hero_name)))
                conn.sendall(build_scene_m())
                self.log('   已回 SceneM(op96) scene=%d map=%d (%d,%d)'
                         % (SCENE_ID, MAP_ID, SPAWN_X, SPAWN_Y))
                conn.sendall(build_goto_m())
                self.log('   已回 GotoM(op97) (%d,%d)' % (SPAWN_X, SPAWN_Y))
            elif op == 54 and len(body) >= 5:
                x = struct.unpack('<H', body[0:2])[0]
                y = struct.unpack('<H', body[2:4])[0]
                self.log('   >>> 移动 x=%d y=%d' % (x, y))
                conn.sendall(make_s2c(86, struct.pack('<I', self.hero_id) + struct.pack('<H', x)
                                      + struct.pack('<H', y) + b'\x00\x00\x00'))
                self.log('   已回 Track')
            else:
                self.log('   （未识别 op=%d）' % op)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3134)
    ap.add_argument('--seed', default=hex(SEED_DEFAULT))
    args = ap.parse_args()
    M2Server(args.host, args.port, int(args.seed, 0)).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
