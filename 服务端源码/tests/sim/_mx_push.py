# -*- coding: utf-8 -*-
"""_mx_push.py — 提权：点击「进入游戏」+ 点掉「提示」+ 100ms 高频监控新进程

用法:
  python _mx_push.py            # 非提权 → 自动 runas 提权执行 --go
  python _mx_push.py --go       # 提权环境直接执行
  python _mx_push.py --go --rounds 3   # 点击轮数（默认 3）

流程（每轮）:
  1. 找「梦想世界更新程序」窗口 → cid=1003「进入游戏」→ BM_CLICK（1s 后无反应补真实鼠标）
  2. 高频 100ms 监控 12s：新进程（含瞬时）+ 新窗口（含「提示」）
  3. 若出现「提示」窗口 → 点 cid=2「确定」，再监控 12s
日志: %TEMP%\\mx_push.log
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHONW = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe'
LOG = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'mx_push.log')

u32 = ctypes.WinDLL('user32', use_last_error=True)
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Process32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
INVALID_HANDLE = ctypes.c_void_p(-1).value
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def log(s):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


class PE32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_char * 260)]


def enum_procs():
    out = {}
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == INVALID_HANDLE:
        return out
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.szExeFile.decode('gbk', 'replace'), e.th32ParentProcessID)
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def enum_windows():
    out = []

    def cb(h, lp):
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(h, title, 512)
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        out.append((h, pid.value, cls.value, title.value, bool(u32.IsWindowVisible(h))))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_child_by_cid(hwin, cid):
    found = []

    def cb(h, lp):
        if u32.GetDlgCtrlID(h) == cid:
            found.append(h)
        return True

    u32.EnumChildWindows(hwin, WNDENUMPROC(cb), 0)
    return found


def find_top(title, visible_only=True):
    hits = [w for w in enum_windows() if w[3] == title and (w[4] or not visible_only)]
    hits.sort(key=lambda x: 0 if x[4] else 1)
    return hits


def click_btn(h):
    ret = u32.SendMessageW(h, 0x00F5, 0, 0)  # BM_CLICK
    return ret


def click_real(h):
    r = wintypes.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    u32.SetCursorPos(cx, cy)
    time.sleep(0.1)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    return cx, cy


def monitor(seconds, known_procs, known_wins, tag):
    """高频 0.1s 监控，返回 (procs, wins) 更新后的已知集合"""
    t_end = time.time() + seconds
    while time.time() < t_end:
        p = enum_procs()
        for pid, (name, ppid) in p.items():
            if pid not in known_procs:
                log('  [%s] +PROC pid=%-6d ppid=%-6d %s' % (tag, pid, ppid, name))
        known_procs = p
        w = enum_windows()
        wset = set((x[0], x[3], x[4]) for x in w)
        if wset != known_wins:
            for h, pid, cls, title, vis in w:
                key = (h, title, vis)
                if key not in known_wins and (vis or title):
                    pn = p.get(pid, ('?', 0))[0]
                    log('  [%s] +WIN  0x%08X pid=%-6d vis=%d %r %r (%s)' % (
                        tag, h, pid, vis, cls, title, pn))
            known_wins = wset
        time.sleep(0.1)
    return known_procs, known_wins


def go(rounds=3):
    log('==== _mx_push GO rounds=%d pid=%d ====' % (rounds, os.getpid()))
    known_procs = enum_procs()
    known_wins = set((x[0], x[3], x[4]) for x in enum_windows())
    log('基线: procs=%d wins=%d' % (len(known_procs), len(known_wins)))

    for rd in range(1, rounds + 1):
        hwin = u32.FindWindowW(None, '梦想世界更新程序')
        if not hwin:
            log('第 %d 轮：启动器窗口不存在，结束' % rd)
            break
        cand = find_child_by_cid(hwin, 1003)
        if not cand:
            log('第 %d 轮：cid=1003 不存在，结束' % rd)
            break
        b = cand[0]

        u32.ShowWindow(hwin, 9)
        u32.SetForegroundWindow(hwin)
        time.sleep(0.4)

        ret = click_btn(b)
        log('第 %d 轮: BM_CLICK「进入游戏」ret=%r' % (rd, ret))
        known_procs, known_wins = monitor(2.0, known_procs, known_wins, 'r%d-刚点' % rd)

        alive = bool(u32.IsWindow(b))
        if alive:
            cx, cy = click_real(b)
            log('第 %d 轮: 补真实鼠标点击 (%d,%d)' % (rd, cx, cy))
            known_procs, known_wins = monitor(3.0, known_procs, known_wins, 'r%d-鼠标' % rd)
        else:
            log('第 %d 轮: 按钮随窗口消失（启动器已关闭）' % rd)

        # 处理「提示」类模态框（最多点 3 个）
        for _i in range(3):
            tips = find_top('提示')
            if not tips:
                break
            th = tips[0][0]
            btns = find_child_by_cid(th, 2)
            log('  发现「提示」窗口 0x%08X，按钮数=%d' % (th, len(btns)))
            if btns:
                r2 = click_btn(btns[0])
                log('  点「确定」ret=%r' % r2)
            known_procs, known_wins = monitor(3.0, known_procs, known_wins, 'r%d-提示后' % rd)

        # 本轮收尾观察
        known_procs, known_wins = monitor(6.0, known_procs, known_wins, 'r%d-尾' % rd)

        # 状态汇总
        log('  == 第 %d 轮后状态 ==' % rd)
        for pid, (name, ppid) in sorted(enum_procs().items()):
            ln = name.lower()
            if ln in ('mx.exe', 'mx.bin', 'gameplayer.exe', 'mxupd.exe', 'mxupdate_unicode.exe'):
                log('     proc pid=%-6d ppid=%-6d %s' % (pid, ppid, name))
        for h, pid, cls, title, vis in enum_windows():
            if vis and title in ('梦想世界更新程序', '提示', '梦想世界'):
                log('     win  0x%08X pid=%-6d vis=%d %r' % (h, pid, vis, title))

    log('==== _mx_push DONE ====')
    return 0


def spawn_elevated():
    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong),
                    ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                    ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', ctypes.c_void_p), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', ctypes.c_void_p),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', ctypes.c_void_p),
                    ('hProcess', wintypes.HANDLE)]

    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    sei = SHELLEXECUTEINFOW()
    ctypes.memset(ctypes.byref(sei), 0, ctypes.sizeof(sei))
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x00000040
    sei.lpVerb = 'runas'
    sei.lpFile = VENV_PYTHONW
    sei.lpParameters = '"%s" --go' % os.path.join(SIM_DIR, '_mx_push.py')
    sei.lpDirectory = SIM_DIR
    sei.nShow = 0
    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        log('!! ShellExecuteExW(runas) 失败 err=%d' % ctypes.get_last_error())
        return False
    log('已请求提权运行 (hProcess=%r)' % sei.hProcess)
    return True


if __name__ == '__main__':
    if '--go' in sys.argv:
        sys.exit(go())
    else:
        open(LOG, 'w', encoding='utf-8').close()
        spawn_elevated()
        time.sleep(2.0)
        deadline = time.time() + 90
        pos = 0
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    if 'DONE' in chunk:
                        break
            except OSError:
                pass
        print('==== 镜像结束 ====')
