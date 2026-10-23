# -*- coding: utf-8 -*-
"""30 分钟稳定性（协议层客户端驱动）。

为什么用协议层客户端：本机 GUI 客户端只在拉起它的 Python 父进程存活期间在线，执行
会话一中断就被连带杀掉（三次尝试分别 22/2/2 分钟）。为了拿到**连续 30 分钟以上**的
服务端稳定性数据，这里用与客户端同源的帧序列驱动：Hello 握手 → 登录 → 选角进世界 →
周期移动 → 周期触发战斗并下指令，采样口径与 soak.py 完全一致（同一套 CSV 列）。

GUI 客户端侧的在线验证见 P0_CLIENT_ACCEPTANCE.md（A–E 五项与重登比对）。
"""
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SIM = os.path.join(ROOT, '服务端源码', 'tests', 'sim')
TESTS = os.path.join(ROOT, '服务端源码', 'tests')
for p in (SIM, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from _test_singleplayer import recv_s2c, send_c2s, hello_resp, login_body, drain_until  # noqa: E402

WEB = 'http://127.0.0.1:8099/'
LOG = os.path.join(HERE, 'server.log')
SAVE = os.path.join(HERE, 'save')
CSV = os.path.join(HERE, 'soak_samples.csv')
DUR = int(os.environ.get('SOAK_SEC', '2000'))
SEED = 0x12345678
ACCOUNT = 'abc123'

import ctypes

psapi = ctypes.WinDLL('psapi')
k32 = ctypes.windll.kernel32


class PMC(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong),
                ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


def rss_mb(pid):
    h = k32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not h:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return None
        return round(pmc.WorkingSetSize / 1048576.0, 1)
    finally:
        k32.CloseHandle(h)


def server_pid():
    out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                         encoding='mbcs', errors='replace').stdout
    for ln in out.splitlines():
        m = re.search(r'TCP\s+127\.0\.0\.1:12239\s+\S+\s+LISTENING\s+(\d+)', ln)
        if m:
            return int(m.group(1))
    return None


def api(path, body=None):
    if body is None:
        return json.loads(urllib.request.urlopen(WEB + path, timeout=8).read())
    req = urllib.request.Request(WEB + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())


def logsize():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


BAD = re.compile(r'Traceback|Exception|严重|✗|ERROR')
DROP = re.compile(r'Windows 10053|Windows 10054|远程主机强迫关闭')


def scan(start):
    try:
        with io.open(LOG, encoding='utf-8', errors='replace') as f:
            f.seek(start)
            t = f.read()
    except OSError:
        return 0, 0
    return (len([l for l in t.splitlines() if BAD.search(l)]),
            len([l for l in t.splitlines() if DROP.search(l)]))


def sess_count():
    try:
        with io.open(LOG, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()[-300:]
    except OSError:
        return None
    last = None
    for ln in lines:
        m = re.search(r'（会话数 (\d+)）', ln) or re.search(r'（剩余会话 (\d+)）', ln)
        if m:
            last = int(m.group(1))
    return last


def saves():
    files = [f for f in os.listdir(SAVE) if f.endswith('.json')]
    ok = bad = total = 0
    for fn in files:
        p = os.path.join(SAVE, fn)
        total += os.path.getsize(p)
        try:
            json.load(io.open(p, encoding='utf-8'))
            ok += 1
        except Exception:
            bad += 1
    return len(files), ok, bad, total


def connect_world():
    import socket
    c = socket.create_connection(('127.0.0.1', 12239), timeout=10)
    c.settimeout(10)
    op, data = recv_s2c(c)
    assert op == 1, ('Hello', op)
    c.sendall(hello_resp(SEED))
    send_c2s(c, 1, login_body(ACCOUNT))
    op, data, _ = drain_until(c, {2})
    if data and data[0] == 1:                      # 已有角色
        _op, _d, _seen = drain_until(c, {3})
    else:                                          # 新角色：建角
        send_c2s(c, 3, ('角色_%s' % ACCOUNT).encode('gbk')[:16].ljust(20, b'\x00') + b'\x00' * 4)
        drain_until(c, {3})
    send_c2s(c, 2, struct.pack('<I', 1))
    drain_until(c, {97})
    return c


def drain(c):
    """把服务端推下来的帧读掉：不读会导致服务端 sendall 阻塞、整条链路卡死。"""
    import select
    n = 0
    while True:
        r, _w, _x = select.select([c], [], [], 0)
        if not r:
            break
        try:
            d = c.recv(65536)
        except Exception:
            break
        if not d:
            break
        n += len(d)
        if n > 1 << 20:          # 单次最多读 1MB，避免长时间霸占
            break
    return n


def move(c, i):
    """沿一条固定的往返路线走，帧格式与客户端一致（op54）。"""
    x = 60 + (i % 20)
    y = 55 + ((i // 4) % 10)
    # 与客户端一致：x(u16) y(u16) + 3 字节方向/动作尾（见 server.log 的 7B 帧）
    send_c2s(c, 54, struct.pack('<HH', x, y) + bytes([1, 8, 0]))


def main():
    out = []
    pid0 = server_pid()
    if not pid0:
        print('server not listening', flush=True)
        return 2
    c = connect_world()
    print('protocol client in world', flush=True)
    api('api/map', {'mapid': 1005, 'x': 60, 'y': 55})
    time.sleep(1.5)

    with io.open(CSV, 'w', encoding='utf-8') as f:
        f.write('ts,elapsed_s,rss_mb,sessions,map,px,py,exp,grade,in_war,items,'
                'save_files,save_ok,save_bad,save_bytes,new_err,new_drop\n')

    t0 = time.time()
    next_sample = 0
    next_battle = t0 + 120
    off = logsize()
    errs = drops = 0
    battles = []
    i = 0
    while time.time() - t0 < DUR:
        now = time.time()
        el = int(now - t0)
        if el >= next_sample:
            next_sample = el + 60
            st = api('api/state')
            nf, ok, bad, total = saves()
            ne, nd = scan(off)
            errs += ne
            drops += nd
            off = logsize()
            row = [time.strftime('%H:%M:%S'), el, rss_mb(server_pid()), sess_count(),
                   st.get('mapname'), st.get('px'), st.get('py'), st.get('exp'),
                   st.get('grade'), int(bool(st.get('in_war'))), len(st.get('items') or []),
                   nf, ok, bad, total, ne, nd]
            with io.open(CSV, 'a', encoding='utf-8') as f:
                f.write(','.join('' if v is None else str(v) for v in row) + '\n')
            print('SAMPLE', row, flush=True)

        if now >= next_battle:
            next_battle = now + 300
            try:
                api('api/encounter', {'every': 1})
                hit = None
                t1 = time.time()
                while time.time() - t1 < 40:
                    move(c, i)
                    i += 1
                    drain(c)
                    time.sleep(0.9)
                    if api('api/state').get('in_war'):
                        break
                if api('api/state').get('in_war'):
                    # 主角普攻（C2S 160 body = 目标 id）
                    send_c2s(c, 160, b'\x01\x00')
                    t2 = time.time()
                    while time.time() - t2 < 70:
                        drain(c)
                        if not api('api/state').get('in_war'):
                            break
                        time.sleep(2)
                    hit = 'op160 sent'
                api('api/encounter', {'every': 5})
                battles.append({'started': True, 'cmd': hit,
                                'war_sec': round(time.time() - t1, 1)})
                print('BATTLE', battles[-1], flush=True)
            except Exception as e:
                print('BATTLE ERR', repr(e), flush=True)
                battles.append({'started': False})
            continue

        try:
            move(c, i)
            i += 1
            drain(c)
        except Exception as e:
            print('MOVE ERR', repr(e), flush=True)
            try:
                c.close()
            except Exception:
                pass
            time.sleep(2)
            c = connect_world()
        time.sleep(14)

    st = api('api/state')
    nf, ok, bad, total = saves()
    summary = {
        'driver': 'protocol-client (Hello/login/op54/op160, 同源帧)',
        'duration_s': int(time.time() - t0),
        'samples': int((time.time() - t0) // 60) + 1,
        'rss_start_mb': None, 'rss_end_mb': None, 'rss_max_mb': None, 'rss_min_mb': None,
        'errors_total': errs, 'abnormal_close_total': drops,
        'battles': battles, 'battle_count': len(battles),
        'save_files': nf, 'save_ok': ok, 'save_bad': bad, 'save_bytes': total,
        'final_exp': st.get('exp'), 'final_grade': st.get('grade'),
        'final_map': st.get('mapname'), 'final_pos': [st.get('px'), st.get('py')],
        'final_items': len(st.get('items') or []),
    }
    # 从 CSV 回填 RSS 与采样数
    rows = [l.split(',') for l in io.open(CSV, encoding='utf-8').read().splitlines()[1:]]
    rss = [float(r[2]) for r in rows if r[2]]
    if rss:
        summary['rss_start_mb'] = rss[0]
        summary['rss_end_mb'] = rss[-1]
        summary['rss_max_mb'] = max(rss)
        summary['rss_min_mb'] = min(rss)
    summary['samples'] = len(rows)
    json.dump(summary, io.open(os.path.join(HERE, 'soak_summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('DONE', json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
