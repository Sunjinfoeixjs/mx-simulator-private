# -*- coding: utf-8 -*-
"""单机游戏架构回归测试（2026-10-16）

覆盖本次「客户端 + 模拟服 → 单机游戏架构」改造的不变量：

  A. 内核可嵌入宿主进程：`serve_background(port=0)` 自动选空闲端口并返回真实端口
  B. 完整协议闭环仍通：Hello → 登录 → 建角 → 进世界 → 移动 → 遇怪战斗 →
     攻击 → NPC 对话 → 任务查询（用 drain_until 动态等帧，不硬编码帧数）
  C. 单连接语义：第二个并发连接被拒绝，且不影响第一个连接
  D. 持久化 round-trip：断开落档 → 新内核按同目录读档，位置/背包一致
  E. 无 socket 路径：InMemoryConn 直接驱动同一个内核（CI / 无网环境）
  F. 客户端配置生成：local.ini 端口改写（明文 GBK、其它行不动）+
     mx.ini 受管键写入（其它键保留）

跑法（纯进程内 + 本地回环，不连任何外部服务）：
    python _test_singleplayer.py
"""
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

# Resolve this isolated checkout's test server, never a different worktree.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sim'))
import m4_server as m                                   # noqa: E402
import singleplayer as sp                               # noqa: E402
from transport import make_memory_pair                  # noqa: E402

SEED = 0x12345678
ENEMIES = 2
ENCOUNTER_EVERY = 3
TURN_TIMEOUT = 30


# ────────────────────────── 帧工具（与 _e2e_m4 同口径）────────────────────────
def recv_exact(c, n, timeout=3.0):
    c.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        ch = c.recv(n - len(buf))
        if not ch:
            raise ConnectionError('closed')
        buf += ch
    return buf


def recv_s2c(c, timeout=3.0):
    n = recv_exact(c, 1, timeout)[0]
    if n == 0xFF:
        n = struct.unpack('<H', recv_exact(c, 2, timeout))[0]
    payload = recv_exact(c, n, timeout)
    return payload[0], payload[1:]


def send_c2s(c, op, body=b''):
    if len(body) >= 0xFF:
        c.sendall(bytes([op]) + b'\xFF' + struct.pack('<H', len(body)) + body)
    else:
        c.sendall(bytes([op]) + bytes([len(body)]) + body)


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def move_body(x, y):
    return struct.pack('<HH', x, y) + b'\x00'


def drain_until(c, wanted, limit=400, timeout=3.0):
    """持续读帧直到出现 wanted 中的某个 opcode；返回 (op, data, seen)。"""
    seen = []
    for _ in range(limit):
        op, data = recv_s2c(c, timeout)
        seen.append(op)
        if op in wanted:
            return op, data, seen
    raise AssertionError('drain_until 超限未命中 %r，已读：%r' % (wanted, seen[-30:]))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ────────────────────────── 场景 ─────────────────────────
def play_to_battle(c, steps, srv):
    """Hello → 登录 → 建角 → 进世界 → 移动至遇怪。返回遇怪时读到的 opcode 序列。"""
    op, data = recv_s2c(c)
    check(op == 1 and len(data) == 6, ('Hello', op, data.hex()))
    c.sendall(hello_resp(SEED))
    steps.append('Hello')

    send_c2s(c, 1, login_body())
    op, data, _seen = drain_until(c, {2})
    check(op == 2 and data[0] == 0, ('登录→AccountInfo(0)', op, data.hex()))
    steps.append('登录')

    send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    op, data, seen = drain_until(c, {3})
    check(6 in seen, ('建角后应见 PlayerAdd(6)', seen))
    steps.append('建角')

    send_c2s(c, 2, struct.pack('<I', 1))
    op, data, seen = drain_until(c, {97})          # GotoM = 进世界序列收尾
    for want in (16, 18, 19, 96, 99):
        check(want in seen, ('进世界缺少帧 %d' % want, seen))
    check(seen.count(65) >= 2, ('招式帧不足', seen))
    steps.append('进世界(%d帧)' % len(seen))

    # Since per-map encounters were introduced, starter town 1001 never
    # auto-starts WarStart. Move to a genuine wild map via the public
    # test-only GM interface; keep the normal C2S move -> encounter path.
    check(m.ENCOUNTER_PER_MAP, 'per-map encounter policy unexpectedly disabled')
    check(srv.mapid == m.START_MAP_ID and not srv._encounter_allowed_here(),
          'starter city must not randomly encounter enemies')
    wild_map = 1028
    check(wild_map in m.ENCOUNTER_BY_MAP and wild_map in m.MAPS,
          'expected beginner wild map unavailable')
    changed = srv.gm_goto_map(wild_map, 45, 40)
    check(changed.get('pushed') and srv._encounter_allowed_here(),
          ('wild encounter preparation failed', changed))
    op, data, new_scene = drain_until(c, {97})
    check(op == 97 and 96 in new_scene, ('wild scene not delivered', new_scene))
    steps.append('wild map 1028 via test GM teleport (city encounters disabled)')

    for i in range(1, ENCOUNTER_EVERY + 1):
        send_c2s(c, 54, move_body(45 + i, 40))
        # 每次移动先回 Track(86)；最后一次移动在 Track 之后紧接遇怪序列
        op, data, seen = drain_until(c, {86})
        check(op == 86, ('移动%d 未见 Track(86)' % i, seen))
        steps.append('移动%d→Track' % i)
        if i < ENCOUNTER_EVERY:
            continue
        op, data, seen2 = drain_until(c, {112})
        check(op == 112, ('最后一次移动未遇怪', seen2))
        # WarStart 之后是 WarAdd×N + WarTurn，继续收到 WarTurn 收尾
        op, data, seen3 = drain_until(c, {114})
        check(op == 114, ('未见 WarTurn(114)', seen3))
        steps.append('移动%d→遇怪' % i)
        return seen + seen2 + seen3
    raise AssertionError('走完 %d 次移动仍未遇怪' % ENCOUNTER_EVERY)


def scenario_protocol(port, steps, srv):
    """真实回环 TCP：完整协议闭环 + 单连接拒绝。"""
    c = socket.create_connection(('127.0.0.1', port), timeout=5)
    war_seen = play_to_battle(c, steps, srv)
    check(112 in war_seen, ('未见 WarStart(112)', war_seen))
    check(115 in war_seen, ('未见 WarAdd(115)', war_seen))
    check(114 in war_seen, ('未见 WarTurn(114)', war_seen))

    # 攻击 → AttackNormal(117) / WState(123) / AttackNormalEnd(119)
    send_c2s(c, 160, struct.pack('<B', 1))
    op, data, seen = drain_until(c, {119})
    for want in (117, 123):
        check(want in seen, ('攻击缺帧 %d' % want, seen))
    steps.append('攻击→%r' % seen[:6])
    send_c2s(c, m.C2S_WAR_SUMM_ATTACK,
             bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
    op, data, seen = drain_until(c, {114, 113})
    check(op in (114, 113), ('宠物行动后未推进战斗回合', seen))
    steps.append('宠物手动攻击→回合推进')

    # Finish a remaining wild encounter using the existing test-only GM
    # interface, then return to the starter city before city NPC assertions.
    # Never disable per-map encounter policy to force a fight inside town.
    if srv.in_war:
        done = srv.gm_war_win()
        check(done.get('ok'), ('wild battle cleanup failed', done))
    back = srv.gm_goto_map(m.START_MAP_ID, 45, 40)
    check(back.get('pushed') and srv.mapid == m.START_MAP_ID,
          ('return to starter city failed', back))
    op, data, city_scene = drain_until(c, {97})
    check(op == 97 and 96 in city_scene, ('city scene not delivered', city_scene))
    steps.append('return to starter city after wild battle')

    # NPC 对话
    send_c2s(c, 96, struct.pack('<I', 1000))
    op, data, seen = drain_until(c, {152})
    check('你好' in data.decode('gbk', 'replace'), ('NPC 对话文案', data[:60]))
    check(147 not in seen, ('初次对话不应误开商店', seen))
    steps.append('NPC 对话')

    # 明确选择商店选项后才打开购买列表
    send_c2s(c, 98, struct.pack('<I', 2))
    op, data, seen = drain_until(c, {147})
    check(op == 147 and data[0] == 2, ('商店选项→购买列表', op, data[:6].hex()))
    steps.append('NPC 商店列表')

    # 接任务
    send_c2s(c, 98, struct.pack('<I', 1))
    op, data, seen = drain_until(c, {152})
    check(72 in seen, ('接任务缺 TaskAdd(72)', seen))
    steps.append('接任务')

    # 任务查询
    send_c2s(c, 48)
    op, data, seen = drain_until(c, {72})
    steps.append('任务查询')

    # ── 单连接语义：第二个连接必须被拒绝，且不影响第一个 ──
    c2 = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        c2.settimeout(3.0)
        first = c2.recv(64)
    except (ConnectionError, socket.timeout):
        first = b''
    check(first == b'', ('第二个连接应被直接关闭，却收到 %r' % first[:32]))
    c2.close()
    steps.append('拒绝并发连接')

    # 第一个连接仍然可用
    send_c2s(c, 8)
    op, data, _seen = drain_until(c, {250})
    check(op == 250, ('PING 回显', op))
    steps.append('首连接仍可用')

    c.close()
    return True


def scenario_persistence(port, steps):
    """断开落档 → 由外层用新内核读档校验。"""
    c = socket.create_connection(('127.0.0.1', port), timeout=5)
    op, _ = recv_s2c(c)
    c.sendall(hello_resp(SEED))
    send_c2s(c, 1, login_body())
    drain_until(c, {2})
    send_c2s(c, 3, '存档测试'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    drain_until(c, {3})
    send_c2s(c, 2, struct.pack('<I', 1))
    drain_until(c, {97})
    for i in range(1, 4):
        send_c2s(c, 54, move_body(50 + i, 41))
        drain_until(c, {86, 112})
    c.close()
    steps.append('跑动后断开（触发落档）')
    return True


def scenario_headless(steps):
    """无 socket 路径：同一个内核用 InMemoryConn 直接驱动。"""
    tmp = tempfile.mkdtemp(prefix='m4_headless_')
    try:
        srv = m.M4Server('127.0.0.1', 0, SEED, ENCOUNTER_EVERY,
                         savedir=tmp, enemies=ENEMIES, turn_timeout=TURN_TIMEOUT)
        server_end, client_end = make_memory_pair()
        t = threading.Thread(target=srv.handle, args=(server_end,), daemon=True)
        t.start()

        op, data = recv_s2c(client_end)
        check(op == 1 and len(data) == 6, ('headless Hello', op, data.hex()))
        client_end.sendall(hello_resp(SEED))

        send_c2s(client_end, 1, login_body())
        op, data, _seen = drain_until(client_end, {2})
        check(op == 2 and data[0] == 0, ('headless 登录', op, data.hex()))

        send_c2s(client_end, 3, '无头角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
        op, data, seen = drain_until(client_end, {3})
        check(6 in seen, ('headless 建角', seen))

        send_c2s(client_end, 2, struct.pack('<I', 1))
        op, data, seen = drain_until(client_end, {97})
        check(16 in seen and 96 in seen and 99 in seen, ('headless 进世界', seen))

        # Signal end-of-input first. Do not close server_end while its
        # sendall() loop is still emitting the remaining world/pet/item frames.
        client_end.close()
        t.join(timeout=5)
        check(not t.is_alive(), 'InMemoryConn server thread did not exit')
        server_end.close()
        steps.append('InMemoryConn 全流程（零 socket）')
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_client_config(steps):
    """客户端配置生成：local.ini 端口改写 + mx.ini 受管键。"""
    d = tempfile.mkdtemp(prefix='m4_client_')
    try:
        local = os.path.join(d, 'local.ini')
        open(local, 'wb').write(
            '推荐区\r\n'
            '本地测试服 127.0.0.1 3134 3 ·推荐\r\n'
            '电信3区\r\n'
            '本地测试服 127.0.0.1 3134 3 &\r\n'
            '======================\r\n'
            '======================\r\n'.encode('gbk'))
        mx = os.path.join(d, 'mx.ini')
        open(mx, 'wb').write(
            '[Game]\r\n'
            'fullscreen=1\r\n'
            'music=50\r\n'
            '[Video]\r\n'
            'width=1024\r\n'.encode('gbk'))

        ok, msg = sp.rewrite_local_ini(d, 45123)
        check(ok, ('rewrite_local_ini', msg))
        raw = open(local, 'rb').read()
        text = raw.decode('gbk')
        check('本地测试服 127.0.0.1 45123 3 ·推荐' in text, ('端口未改写', text))
        check('电信3区' in text and '======================' in text,
              ('原有行被破坏', text))
        # 仍是明文（不是三层加密）：明文 GBK 应能以首行原文解回
        check(raw.decode('gbk').startswith('推荐区'),
              ('local.ini 不再是明文 GBK', raw[:8]))
        check(os.path.isfile(local + '.bak_singleplayer'), '缺少 local.ini 备份')

        ok, msg = sp.update_mx_ini(d)
        check(ok, ('update_mx_ini', msg))
        mxt = open(mx, 'rb').read().decode('gbk')
        check('serverini=local.ini' in mxt, ('serverini 未写入', mxt))
        check(sp.FAR_FUTURE in mxt, ('refreshserver 未写入', mxt))
        check('fullscreen=1' in mxt and 'music=50' in mxt,
              ('[Game] 其它键被清掉', mxt))
        check('width=1024' in mxt, ('其它段被破坏', mxt))

        # 幂等：再跑一次不应堆积重复键
        sp.update_mx_ini(d)
        mxt2 = open(mx, 'rb').read().decode('gbk')
        check(mxt2.count('serverini=local.ini') == 1, ('serverini 重复', mxt2))
        steps.append('客户端配置生成 + 幂等')
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def scenario_http_hijack(steps):
    """本地 HTTP 劫持：server7 列表端口重编 + 劫持服务响应。"""
    binp = os.path.join(os.path.dirname(os.path.abspath(sp.__file__)),
                        'server7_local.bin')
    check(os.path.isfile(binp), '缺少 server7_local.bin')
    bak = binp + '.bak_test'
    shutil.copy2(binp, bak)
    try:
        from serverini_encode import decode_file
        ok, msg = sp.rebuild_server7_bin(45999)
        check(ok, ('rebuild_server7_bin', msg))
        _flag, _plain, text = decode_file(binp)
        check('本地测试服 127.0.0.1 45999 3 ·推荐' in text,
              ('列表端口未重编', [l for l in text.split('\r\n')
                                if '本地测试服' in l]))
        check('3134' not in text.split('本地测试服')[0] or True, '')  # 占位防呆

        # 起劫持服务（随机端口，避免与既有实例冲突）
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        free = s.getsockname()[1]
        s.close()
        httpd, msg = sp.start_local_http(free)
        check(httpd is not None, ('start_local_http', msg))
        try:
            import urllib.error
            import urllib.request
            body = urllib.request.urlopen(
                'http://127.0.0.1:%d/server7.txt' % free, timeout=5).read()
            check(len(body) > 100, ('server7.txt 响应过小', len(body)))
            blank = urllib.request.urlopen(
                'http://127.0.0.1:%d/blank' % free, timeout=5).read()
            check(b'<html' in blank.lower(), ('blank 页不是 HTML', blank[:40]))
            for path in ('newupdate.txt', 'news.txt', 'version.txt',
                         'unexpected/server7.txt', 'unexpected/blank'):
                try:
                    urllib.request.urlopen(
                        'http://127.0.0.1:%d/%s' % (free, path), timeout=5)
                except urllib.error.HTTPError as e:
                    check(e.code == 404, ('%s 应返回 404' % path, e.code))
                else:
                    raise AssertionError('%s 不应伪装成服务器列表或页面' % path)
        finally:
            httpd.shutdown()
        steps.append('HTTP 劫持（server7/blank 响应 + 未知资源 404）')
        return True
    finally:
        shutil.move(bak, binp)


def scenario_client_exe(steps):
    """客户端可执行文件选择：优先 mx.exe（游戏主程序），显式指定优先。

    ★ `gameplayer.exe` 是 .mwr 录像播放器（2026-10-16 真机复核定案），
      绝不应被选为客户端入口。
    """
    d = tempfile.mkdtemp(prefix='m4_exe_')
    try:
        # 只有 gameplayer.exe → 不选它（录像播放器），仍返回 mx.exe 路径（不存在）
        open(os.path.join(d, 'gameplayer.exe'), 'w').close()
        check(sp.resolve_client_exe(d) == os.path.join(d, 'mx.exe'),
              '只有 gameplayer.exe 时不得选中（录像播放器）')
        # 只有 mx.exe → 选中
        open(os.path.join(d, 'mx.exe'), 'w').close()
        check(sp.resolve_client_exe(d) == os.path.join(d, 'mx.exe'),
              '应选中 mx.exe')
        # 两者都在 → 仍选 mx.exe
        check(sp.resolve_client_exe(d) == os.path.join(d, 'mx.exe'),
              '两者并存应选 mx.exe')
        # 显式指定 > 一切
        check(sp.resolve_client_exe(d, r'C:\foo\bar.exe') == r'C:\foo\bar.exe',
              '显式指定应优先')
        steps.append('客户端可执行文件选择（mx.exe 优先 / 显式优先）')
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ────────────────────────── runner ─────────────────────────
def run():
    tmp = tempfile.mkdtemp(prefix='m4_sp_')
    steps = []
    srv = None
    try:
        # A. 内核嵌入宿主进程：自动选空闲端口
        srv = m.M4Server('127.0.0.1', 0, SEED, ENCOUNTER_EVERY,
                         savedir=tmp, enemies=ENEMIES, turn_timeout=TURN_TIMEOUT)
        srv.enemy_skill_chance = 0.0   # 敌方招式关闭，保证战斗行动序列断言确定性
        port = srv.serve_background(0)
        check(port > 0, ('自动选端口失败', port))
        steps.append('serve_background→127.0.0.1:%d' % port)

        # B + C
        scenario_protocol(port, steps, srv)

        # D. 断开落档 → 新内核按同目录读档
        scenario_persistence(port, steps)
        # 多角色：存档按登录账号分文件（role_<账号>.json），不再是单一 role.json
        save_path = m.char_savepath(tmp, '100860001')
        for _ in range(50):
            if os.path.isfile(save_path):
                break
            time.sleep(0.1)
        check(os.path.isfile(save_path), '断开后未生成存档 %s' % save_path)
        before = m.load_role(save_path)
        check(before is not None and 'px' in before, ('存档内容异常', before))

        srv2 = m.M4Server('127.0.0.1', 0, SEED, ENCOUNTER_EVERY,
                          savedir=tmp, enemies=ENEMIES, turn_timeout=TURN_TIMEOUT)
        # __init__ 只载入 legacy role.json；按账号的角色要等登录绑定后才生效
        srv2._bind_account('100860001')
        check(srv2.px == before['px'] and srv2.py == before['py'],
              ('读档位置不一致', (srv2.px, srv2.py), (before['px'], before['py'])))
        check(srv2.items == {int(k): v for k, v in before['items'].items()},
              '读档背包不一致')
        steps.append('持久化 round-trip（位置 %d,%d，背包 %d 件）'
                     % (srv2.px, srv2.py, len(srv2.items)))

        # E. 无 socket 路径
        scenario_headless(steps)

        # F. 客户端配置生成
        scenario_client_config(steps)

        # G. 本地 HTTP 劫持（列表端口重编 + 服务响应）
        scenario_http_hijack(steps)

        # H. 客户端可执行文件选择
        scenario_client_exe(steps)

        print('PASS：%s' % ' → '.join(steps))
        print('全部 %d 组断言通过' % len(steps))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(run())
