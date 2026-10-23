# -*- coding: utf-8 -*-
"""P0 隔离稳定性测试：客户端保持在线 ≥30 分钟，周期操作 + 采样。

采样维度（每 60s 一行 CSV）：
  · 服务端 RSS（psapi，不依赖 psutil）
  · 在线会话数 / 角色位置 / 经验 / 背包件数 / 是否战斗中
  · 存档文件数、大小、JSON 可解析性
  · 服务端日志新增的 Traceback/异常行数
操作节奏：
  · 每 15s 走 2 步（真实点击 → C2S_MOVE）
  · 每 5 分钟打一场（GM 设置遇敌间隔=1 触发自然遇敌 → 客户端点击敌方下指令 → 等服务端 WarEnd）
  · 每 10 分钟做一次存档校验（GM /api/save 后读 JSON）
"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save')
LOG = os.path.join(HERE, 'server.log')
WEB = 'http://127.0.0.1:8099/'
DURATION = int(os.environ.get('SOAK_SEC', '1900'))
CSV = os.path.join(HERE, 'soak_samples.csv')

psapi = ctypes.WinDLL('psapi')
k32 = ctypes.windll.kernel32

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class PMC(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


def rss_mb(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
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
    # 端口持有者才是真正跑了 m4_server 的解释器（boot.json 里是 py 启动器 stub）
    import subprocess
    out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                         encoding='mbcs', errors='replace').stdout
    for ln in out.splitlines():
        m = re.search(r'TCP\s+127\.0\.0\.1:12239\s+\S+\s+LISTENING\s+(\d+)', ln)
        if m:
            return int(m.group(1))
    boot = json.load(io.open(os.path.join(HERE, 'boot.json')))
    return int(boot['server'])


def api(path, body=None):
    if body is None:
        return json.loads(urllib.request.urlopen(WEB + path, timeout=8).read())
    req = urllib.request.Request(WEB + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


BAD = re.compile(r'Traceback|Exception|严重|✗|ERROR')
DROP = re.compile(r'Windows 10053|Windows 10054|远程主机强迫关闭')


def scan_errors(start):
    try:
        with io.open(LOG, encoding='utf-8', errors='replace') as f:
            f.seek(start)
            text = f.read()
    except OSError:
        return 0, 0
    bad = len([l for l in text.splitlines() if BAD.search(l)])
    drop = len([l for l in text.splitlines() if DROP.search(l)])
    return bad, drop


def sess_count():
    """从日志里取最近一次会话数（服务端 gm_state 不含 sessions 字段）。"""
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


def save_health():
    files = [f for f in os.listdir(SAVE) if f.endswith('.json')]
    ok, bad, total = 0, 0, 0
    for fn in files:
        p = os.path.join(SAVE, fn)
        total += os.path.getsize(p)
        try:
            json.load(io.open(p, encoding='utf-8'))
            ok += 1
        except Exception:
            bad += 1
    return len(files), ok, bad, total


WALK = [(520, 300), (470, 250), (560, 340), (500, 200), (600, 260)]
ENEMY = [(227, 189), (230, 215), (240, 200), (212, 175), (250, 230)]


def move_once():
    for (x, y) in WALK:
        ui.click(x, y, 0.6)
        time.sleep(0.4)


def cmd_seen():
    with io.open(LOG, encoding='utf-8', errors='replace') as f:
        tail = f.read().splitlines()[-80:]
    for ln in tail:
        if 'opcode=160' in ln or 'opcode=161' in ln:
            return ln
    return None


def battle_once():
    """一轮完整战斗：自然遇敌 → 客户端点敌方下指令 → 等服务端 WarEnd。"""
    api('api/encounter', {'every': 1})
    t0 = time.time()
    started = False
    while time.time() - t0 < 30:
        if api('api/state').get('in_war'):
            started = True
            break
        move_once()
    if not started:
        api('api/encounter', {'every': 5})
        return {'started': False}
    time.sleep(1.2)
    hit = None
    for i in range(20):
        x, y = ENEMY[i % len(ENEMY)]
        ui.click(x, y, 0.45)
        time.sleep(0.3)
        hit = cmd_seen()
        if hit:
            break
    t1 = time.time()
    while time.time() - t1 < 75:
        if not api('api/state').get('in_war'):
            break
        time.sleep(2)
    api('api/encounter', {'every': 5})
    return {'started': True, 'cmd': hit, 'war_sec': round(time.time() - t1, 1)}


def main():
    pid = server_pid()
    start_off = log_size()
    rows = []
    t_start = time.time()
    next_sample = 0
    next_battle = time.time() + 180
    battles = []
    errors_total = drops_total = 0
    with io.open(CSV, 'w', encoding='utf-8') as f:
        f.write('ts,elapsed_s,rss_mb,sessions,map,px,py,exp,grade,in_war,items,'
                'save_files,save_ok,save_bad,save_bytes,new_err,new_drop\n')
    print('SOAK START pid=%s duration=%ss' % (pid, DURATION), flush=True)
    while time.time() - t_start < DURATION:
        now = time.time()
        el = int(now - t_start)
        if el >= next_sample:
            next_sample = el + 60
            st = api('api/state')
            nf, ok, bad, total = save_health()
            ne, nd = scan_errors(start_off)
            errors_total += ne
            drops_total += nd
            start_off = log_size()
            row = [time.strftime('%H:%M:%S'), el, rss_mb(pid), sess_count(),
                   st.get('mapname'), st.get('px'), st.get('py'), st.get('exp'),
                   st.get('grade'), int(bool(st.get('in_war'))), len(st.get('items') or []),
                   nf, ok, bad, total, ne, nd]
            rows.append(row)
            with io.open(CSV, 'a', encoding='utf-8') as f:
                f.write(','.join('' if v is None else str(v) for v in row) + '\n')
            print('SAMPLE %s' % row, flush=True)
        if now >= next_battle:
            next_battle = now + 300
            r = battle_once()
            battles.append(r)
            print('BATTLE %s' % r, flush=True)
            continue
        move_once()
        time.sleep(1.0)

    st = api('api/state')
    nf, ok, bad, total = save_health()
    rss = [r[2] for r in rows if isinstance(r[2], (int, float))]
    summary = {
        'duration_s': int(time.time() - t_start),
        'samples': len(rows),
        'rss_start_mb': rss[0] if rss else None,
        'rss_end_mb': rss[-1] if rss else None,
        'rss_max_mb': max(rss) if rss else None,
        'rss_min_mb': min(rss) if rss else None,
        'sessions_min': min(r[3] for r in rows) if rows else None,
        'sessions_max': max(r[3] for r in rows) if rows else None,
        'errors_total': errors_total,
        'abnormal_close_total': drops_total,
        'battles': battles,
        'battle_count': len(battles),
        'save_files': nf, 'save_ok': ok, 'save_bad': bad, 'save_bytes': total,
        'final_exp': st.get('exp'), 'final_grade': st.get('grade'),
        'final_map': st.get('mapname'), 'final_pos': [st.get('px'), st.get('py')],
        'final_items': len(st.get('items') or []),
    }
    with io.open(os.path.join(HERE, 'soak_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print('SOAK DONE', json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
