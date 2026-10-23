# -*- coding: utf-8 -*-
"""系统设置对话框（`dialog/dlgsysopt`）链路的自动化测试（进程内，无 socket）。

为什么单列
----------
`dlgsysopt` 是唯一一个**一次操作打三条上行**的 UI：设置保存(21) / 手机安全(12) /
私密资料(11)。它们都是「客户端存本地 + 顺手告诉服务器」，服务端**不回帧**，
所以既不会有帧序断言、也不会在真机上炸——正因如此，之前完全没被覆盖：

  · 落进战斗保底 → 平白多一帧 WarTurn（已加进 `DISPATCH_PRE_WAR_OPS`，本测试守）；
  · 收下就丢 → 重登后设置回到默认，玩家会以为「保存失败」（本测试守存盘回读）。

断言清单
--------
① op21 sub=1：4 字节位图原样存进 sys_switch，且**不回帧**；
② op21 sub=2：长串（快捷键表）按裸字节收下（`PacketAddS(s,len)` 无 \0），存进 sys_data；
③ op12 sub=2 → sub=1：设置写回，回读帧 `S2C 58 sub=1` 恰好 15 字节且字段对得上；
④ op11 sub=2：私密资料原文存盘，**日志脱敏**（不得出现完整身份证号）；
⑤ op88 sub=113：只记录，不回帧；
⑥ 存盘回读：`_persist` 后的存档被新会话 `_apply_save` 读回，四项状态一致；
⑦ 战斗态安全：`in_war=True` 时发 21/12/11 都不产生 `WarTurn(114)`。

用法： python tests/_test_sysopt.py
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = next(p for p in (os.path.join(ROOT, 'sim'), ROOT,
                       os.path.join(HERE, 'sim'))
           if os.path.isfile(os.path.join(p, 'm4_server.py')))
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import tap                     # noqa: E402

RESULT = []


def check(cond, name, detail=''):
    RESULT.append((name, bool(cond)))
    print(('[PASS] %s %s' if cond else '[FAIL] %s %s') % (name, detail))


class FakeConn(object):
    def __init__(self):
        self.buf = b''

    def sendall(self, data):
        self.buf += bytes(data)


def frames(conn):
    parts, _rest = tap.split_frames('S2C', conn.buf)
    conn.buf = b''
    return [(op, body) for _raw, op, body in parts]


def new_server(tmp, name='a'):
    srv = m.M4Server('127.0.0.1', 3199, seed=0x51C0FFEE, encounter_every=0, enemies=1,
                     turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    for attr, val in (('items', {}), ('tasks', {}), ('hero_id', 1),
                      ('hero_name', '测试'), ('cur_npc', m.NPC_ID)):
        if not hasattr(srv, attr):
            setattr(srv, attr, val)
    srv.account = 'u%s' % name
    srv._bind_account(srv.account)
    return srv


def dispatch(srv, op, body):
    """走真实 `_dispatch`，返回本次产生的 (op, body) 列表。"""
    conn = FakeConn()
    srv._dispatch(conn, op, body)
    return frames(conn)


def run():
    tmp = tempfile.mkdtemp(prefix='m4sysopt_')
    srv = new_server(tmp)
    logs = []
    srv.log = lambda line, _L=logs: _L.append(line)

    # ── ① 开关位图 ──────────────────────────────────────────────────
    sw = 0b1011
    out = dispatch(srv, m.C2S_SYSSWITCH,
                   bytes([m.SYSSWITCH_SUB_SWITCH]) + struct.pack('<I', sw))
    check(srv.sys_switch == sw, '① op21 sub=1 存下位图 0x%X' % srv.sys_switch)
    check(not out, '① 不回帧（客户端自己提示保存成功）', str(out))
    check(any('系统设置保存' in ln for ln in logs), '① 日志认领该分支')

    # ── ② 快捷键串（无 \0）───────────────────────────────────────────
    logs[:] = []
    hotkey = '1=1101|2=1102|3=1103'
    out = dispatch(srv, m.C2S_SYSSWITCH,
                   bytes([m.SYSSWITCH_SUB_DATA1]) + hotkey.encode('gbk'))
    check(srv.sys_data.get(int(m.SYSSWITCH_SUB_DATA1)) == hotkey,
          '② op21 sub=2 存下快捷键串', repr(srv.sys_data))
    check(not out, '② 不回帧')

    # ── ③ 手机安全：写入 → 回读 ─────────────────────────────────────
    setting = bytes([m.MOBILE_SUB_SETTING, 1, 1, 8, 0, 22, 30]) + \
        struct.pack('<I', 2) + bytes([3])
    out = dispatch(srv, m.C2S_MOBILE, setting)
    check(not out, '③a op12 sub=2 只存不回帧')
    check(srv.mobile['receive'] == 1 and srv.mobile['limit'] == [8, 0, 22, 30]
          and srv.mobile['degree'] == 2 and srv.mobile['money'] == 3,
          '③b 设置字段写入正确', str(srv.mobile))

    out = dispatch(srv, m.C2S_MOBILE, bytes([m.MOBILE_SUB_INFO]))
    check(len(out) == 1 and out[0][0] == m.S2C_MOBILE, '③c 回 S2CMobile(58)', str(out))
    body = out[0][1] if out else b''
    check(len(body) == 15, '③d 帧体 15 字节', '%d 字节' % len(body))
    if len(body) == 15:
        sub, chk, recv, en = body[0], body[1], body[2], body[3]
        lim = list(body[4:8])
        degree = struct.unpack('<I', body[8:12])[0]
        money, balance = body[12], struct.unpack('<H', body[13:15])[0]
        check(sub == 1 and recv == 1 and lim == [8, 0, 22, 30] and degree == 2
              and money == 3,
              '③e 读序与写入一致（sub=1 recv=%d limit=%s degree=%d money=%d）'
              % (recv, lim, degree, money))

    # ── ④ 私密资料（脱敏）───────────────────────────────────────────
    logs[:] = []
    info = 'idcard=110101199001011234||qq=12345678||mobile=13800138000||name=张三'
    out = dispatch(srv, m.C2S_DUOYITONG,
                   bytes([m.DUOYITONG_SUB_PRIVATE]) + info.encode('gbk'))
    check(srv.private_info == info, '④a 原文存盘（回填面板要用）')
    check(not out, '④b 不回帧')
    joined = '\n'.join(logs)
    check('110101199001011234' not in joined and '13800138000' not in joined,
          '④c 日志里没有完整证件号/手机号', joined.split('\n')[-2] if logs else '')

    # ── ⑤ 私密面板请求（op88 sub=113）───────────────────────────────
    out = dispatch(srv, m.C2S_OPENUI, bytes([m.OPENUI_SUB_PRIVATE]))
    check(not out, '⑤ op88 sub=113 不应答（面板客户端自开）')

    # ── ⑥ 存盘 → 新会话回读 ────────────────────────────────────────
    srv._persist()
    srv2 = new_server(tmp, 'b')
    srv2.account = srv.account
    srv2._bind_account(srv.account)
    saved = m.load_role(srv2.savepath)
    check(isinstance(saved, dict), '⑥a 存档已落盘', srv2.savepath)
    if isinstance(saved, dict):
        srv2._apply_save(saved)
        check(srv2.sys_switch == sw, '⑥b 位图回读 %d' % srv2.sys_switch)
        check(srv2.sys_data.get(int(m.SYSSWITCH_SUB_DATA1)) == hotkey, '⑥c 快捷键串回读')
        check(srv2.mobile['limit'] == [8, 0, 22, 30], '⑥d 手机设置回读', str(srv2.mobile))
        check(srv2.private_info == info, '⑥e 私密资料回读')

    # ── ⑦ 战斗态安全 ───────────────────────────────────────────────
    srv3 = new_server(tmp, 'c')
    srv3.in_war = True
    srv3.await_cmd = False
    srv3._pending_pet_suffix = None
    srv3.enemy_hp = {1: 100}
    srv3.log = lambda line, _L=logs: _L.append(line)
    bad = []
    for op, body in ((m.C2S_SYSSWITCH, bytes([1]) + struct.pack('<I', 1)),
                     (m.C2S_MOBILE, bytes([m.MOBILE_SUB_INFO])),
                     (m.C2S_DUOYITONG, bytes([2]) + b'qq=1')):
        out = dispatch(srv3, op, body)
        if any(o == m.OP_WAR_TURN for o, _b in out):
            bad.append(op)
    check(not bad, '⑦ 战斗态发 21/12/11 不产生 WarTurn', '落保底的 op=%s' % (bad,))

    shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in RESULT if not ok]
    print('\n全部 %d 项断言，失败 %d 项' % (len(RESULT), len(failed)))
    for n in failed:
        print('  ✗ %s' % n)
    print('=== 系统设置（dlgsysopt）%s ===' % ('PASS' if not failed else 'FAIL'))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(run())
