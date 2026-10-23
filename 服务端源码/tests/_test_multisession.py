# -*- coding: utf-8 -*-
"""多会话 / 多角色冒烟。

验证四件事：
  1. 两个客户端可**同时**在线（旧实现会拒绝第二个连接）
  2. 两个会话的角色状态**互不串味**（位置/背包各自独立）
  3. 存档按账号分文件（role_<账号>.json），互不覆盖
  4. 同一账号的第二个活跃会话被拒绝，不会产生两份可写内存状态
  5. 老的单角色 role.json 会被第一个账号认领，但**不会**被第二个账号克隆

协议细节沿用 _test_singleplayer 的帧工具（Hello/登录/建角/进世界/移动）。
"""
import os
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
for p in (SIM, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import m4_server as m                                   # noqa: E402
from _test_singleplayer import (recv_s2c, send_c2s, hello_resp,   # noqa: E402
                                login_body, move_body, drain_until)

SEED = 0x12345678
fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg
          if cond else '  FAIL %s' % (msg,))
    if not cond:
        fails.append(msg)


def hello_and_login(c, account, steps=None):
    """握手 + 登录 + 建角 + 进世界。account 决定落到哪个角色存档。"""
    op, data = recv_s2c(c)
    assert op == 1 and len(data) == 6, ('Hello', op, data.hex())
    c.sendall(hello_resp(SEED))
    send_c2s(c, 1, login_body(account))
    op, data, seen = drain_until(c, {2})
    assert op == 2 and data[0] == 0, ('AccountInfo(0)', op, data.hex())
    send_c2s(c, 3, ('角色_%s' % account).encode('gbk')[:16].ljust(20, b'\x00') + b'\x00' * 4)
    op, data, seen = drain_until(c, {3})
    send_c2s(c, 2, struct.pack('<I', 1))
    op, data, seen = drain_until(c, {97})       # GotoM 收尾
    return seen


def hello_and_enter_existing(c, account):
    """握手并进入已存在角色；用于验证释放所有权后的真实重登。"""
    op, data = recv_s2c(c)
    assert op == 1 and len(data) == 6, ('Hello', op, data.hex())
    c.sendall(hello_resp(SEED))
    send_c2s(c, 1, login_body(account))
    op, data, _seen = drain_until(c, {2})
    assert op == 2 and data[0] == 1, ('AccountInfo(1)', op, data.hex())
    op, data, _seen = drain_until(c, {3})
    assert op == 3, ('RoleInfo', op, data.hex())
    send_c2s(c, 2, struct.pack('<I', 1))
    _op, _data, seen = drain_until(c, {97})
    return seen


def main():
    tmp = tempfile.mkdtemp(prefix='m4multi_')
    srv = m.MultiServer('127.0.0.1', 0, SEED, 0,          # encounter_every=0：只测会话，不打架
                        logdir=os.path.join(tmp, 'logs'),
                        savedir=os.path.join(tmp, 'save'),
                        enemies=1, turn_timeout=30, max_sessions=4)
    port = srv.serve_background()
    print('多会话内核端口 %d' % port)
    steps = []
    try:
        # ── 1. 两个客户端同时在线 ──────────────────────────────
        import socket
        a = socket.create_connection(('127.0.0.1', port), timeout=5)
        hello_and_login(a, '100000001', steps)
        check(srv.session_count() >= 1, '客户端 A 已建立会话（当前 %d）' % srv.session_count())

        b = socket.create_connection(('127.0.0.1', port), timeout=5)
        hello_and_login(b, '100000002', steps)
        check(srv.session_count() >= 2,
              '客户端 B 也能同时在线（不会被拒绝；当前 %d 会话）' % srv.session_count())

        # ── 2. 状态互不串味 ────────────────────────────────────
        # 两份会话对象必须是不同实例、不同账号、不同存档路径
        with srv._lock:
            sessions = list(srv.sessions)
        check(len(sessions) >= 2, '至少两个会话对象（实得 %d）' % len(sessions))
        accs = {s.account for s in sessions}
        check(accs == {'100000001', '100000002'}, '两个会话账号各自独立：%r' % (accs,))
        paths = {s.savepath for s in sessions}
        check(len(paths) == 2, '两个会话存档路径不同：%r' % (paths,))
        check(all('role_' in os.path.basename(p) for p in paths),
              '存档按账号分文件：%r' % ([os.path.basename(p) for p in paths],))

        # A 走一步、B 不动 → 只有 A 的位置变
        pos_a0 = {s.account: (s.px, s.py) for s in sessions}
        send_c2s(a, 54, struct.pack('<HH', pos_a0['100000001'][0],
                                    pos_a0['100000001'][1]) + bytes([1, 2]))
        drain_until(a, {86}, limit=60)      # MapTrack
        time.sleep(0.3)
        pos_a1 = {s.account: (s.px, s.py) for s in sessions}
        check(pos_a1['100000001'] != pos_a0['100000001'],
              'A 移动后自己的位置变了 %r → %r'
              % (pos_a0['100000001'], pos_a1['100000001']))
        check(pos_a1['100000002'] == pos_a0['100000002'],
              'A 移动**不影响** B 的位置 %r' % (pos_a1['100000002'],))

        # 背包也各自独立：给 A 一件道具不该出现在 B
        sA = next(s for s in sessions if s.account == '100000001')
        sB = next(s for s in sessions if s.account == '100000002')
        n_a, n_b = len(sA.items), len(sB.items)
        sA.items[999999] = {'shape': 22100, 'key': 0, 'qty': 1,
                            'pos': m.ITEM_POS_BAG_START, 'infoext': '测试道具'}
        check(999999 in sA.items and 999999 not in sB.items,
              'A 的背包与 B 的背包互不影响（A=%d 件, B=%d 件）' % (n_a, n_b))

        # A 独立进入并结束一场真实战斗链，B 的战斗状态不得被带入。
        war_start = sA.gm_war_start()
        check(war_start.get('ok') and sA.in_war and not sB.in_war,
              'A 开战不影响 B 的战斗状态')
        war_win = sA.gm_war_win()
        check(war_win.get('ok') and not sA.in_war and not sB.in_war,
              'A 战斗结束后双方状态各自归零')

        # 同账号第二会话在读档前就拒绝，避免两份可写内存状态产生。
        c = socket.create_connection(('127.0.0.1', port), timeout=5)
        op, data = recv_s2c(c)
        check(op == 1, '重复登录客户端收到 Hello')
        c.sendall(hello_resp(SEED))
        send_c2s(c, 1, login_body('100000001'))
        duplicate_replied = False
        try:
            recv_s2c(c, timeout=1.0)
            duplicate_replied = True
        except (ConnectionError, OSError):
            pass
        c.close()
        time.sleep(0.2)
        with srv._lock:
            owners = dict(srv._account_owners)
            same_account = [s for s in srv.sessions if s.account == '100000001']
        check(not duplicate_replied, '同账号第二会话在账号应答前被拒绝')
        check(owners.get('100000001') is sA and len(same_account) == 1,
              '账号所有权仍唯一归属原会话')

        # ── 3. 落档 → 两个角色各自一个文件 ──────────────────────
        sA._persist()
        sB._persist()
        pa = m.char_savepath(os.path.join(tmp, 'save'), '100000001')
        pb = m.char_savepath(os.path.join(tmp, 'save'), '100000002')
        check(os.path.isfile(pa), '账号1 存档已生成 %s' % os.path.basename(pa))
        check(os.path.isfile(pb), '账号2 存档已生成 %s' % os.path.basename(pb))
        check(pa != pb, '两个角色的存档文件不同')
        da, db = m.load_role(pa), m.load_role(pb)
        check(da is not None and db is not None, '两份存档都可读')
        if da and db:
            check(da['px'] != db['px'] or da['py'] != db['py'],
                  '两份存档位置不同（%r vs %r）'
                  % ((da['px'], da['py']), (db['px'], db['py'])))

        # ── 4. 老存 role.json 只被第一个账号认领 ────────────────
        tmp2 = tempfile.mkdtemp(prefix='m4legacy_')
        savedir2 = os.path.join(tmp2, 'save')
        os.makedirs(savedir2, exist_ok=True)
        legacy = dict(da)
        m.save_role(legacy, os.path.join(savedir2, 'role.json'))

        s1 = m.M4Server('127.0.0.1', 0, SEED, 0, savedir=savedir2)
        s1._bind_account('acc_first')
        check(s1._loaded is not None,
              '第一个账号认领旧存档（px=%s）' % (s1._loaded or {}).get('px'))
        check(s1.px == legacy['px'], '认领后位置与旧存档一致')
        s1._persist()      # 写进 role_acc_first.json

        s2 = m.M4Server('127.0.0.1', 0, SEED, 0, savedir=savedir2)
        s2._bind_account('acc_second')
        check(s2._loaded is None,
              '第二个账号**不**克隆旧存档（应是全新角色）')
        check(s2.px == m.SPAWN_X and s2.py == m.SPAWN_Y,
              '第二个账号落在出生点 (%d,%d)' % (s2.px, s2.py))
        shutil.rmtree(tmp2, ignore_errors=True)

        # ── 5. 会话上限 ────────────────────────────────────────
        check(srv.max_sessions == 4, '会话上限可配置（%d）' % srv.max_sessions)

        # A 先断线，B 必须继续在线；释放所有权后 A 用新连接重登并恢复存档。
        saved_a_pos = (da['px'], da['py'])
        second_war = sA.gm_war_start()
        check(second_war.get('ok') and sA.in_war,
              'A 可在断线前进入第二场战斗（用于战斗中断线恢复）')
        a.close()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            with srv._lock:
                if '100000001' not in srv._account_owners:
                    break
            time.sleep(0.05)
        with srv._lock:
            b_still_online = (srv._account_owners.get('100000002') is sB)
            a_released = '100000001' not in srv._account_owners
        check(a_released, 'A 断线后释放账号所有权')
        check(b_still_online, 'A 断线不导致 B 掉线')

        a2 = socket.create_connection(('127.0.0.1', port), timeout=5)
        hello_and_enter_existing(a2, '100000001')
        with srv._lock:
            sA2 = next((s for s in srv.sessions if s.account == '100000001'), None)
        check(sA2 is not None, '释放后新连接可重新登录 A')
        check(sA2 is not None and (sA2.px, sA2.py) == saved_a_pos,
              'A 重登加载正确坐标 %r' % (saved_a_pos,))
        check(sA2 is not None and 999999 in sA2.items,
              'A 重登加载正确背包数据')
        check(sA2 is not None and not sA2.in_war,
              '战斗中断线后重登不残留战斗状态')

        a2.close()
        b.close()
        time.sleep(0.5)
        check(srv.session_count() == 0,
              '断开后会话回收干净（剩余 %d）' % srv.session_count())
        with srv._lock:
            remaining_owners = dict(srv._account_owners)
        check(not remaining_owners,
              '断开后账号所有权回收干净（剩余 %r）' % remaining_owners)
    finally:
        try:
            srv._stop = True
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print('FAIL（%d 项）' % len(fails))
        for f in fails:
            print('  ✗ %s' % (f,))
        return 1
    print('多会话/多角色 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
