# -*- coding: utf-8 -*-
"""_mx_click.py — 提权点击启动器按钮（默认 cid=1003「进入游戏」）

用法:
  python _mx_click.py                # 非提权 → 自动 runas 提权执行 --go 1003
  python _mx_click.py 1004           # 指定 cid
  python _mx_click.py --go 1003      # 提权环境直接执行

流程: 找「梦想世界更新程序」窗口 → 按 cid 找 Button →
      SetForegroundWindow → BM_CLICK → 3s 无变化则真实鼠标点击中心 →
      监测 25s 进程/窗口变化，全部写 %TEMP%\\mx_click.log
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHONW = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe'
LOG = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'mx_click.log')

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
        out[e.th32ProcessID] = e.szExeFile.decode('gbk', 'replace')
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


WATCH_EXE = ('mx.exe', 'mx.bin', 'gameplayer.exe', 'mxsj.exe', 'mxclient.exe', 'mxupd.exe')


def watch_procs():
    out = {}
    for pid, name in enum_procs().items():
        if name.lower() in WATCH_EXE:
            out[pid] = name
    return out


def watch_wins():
    out = []
    for h, pid, cls, title, vis in enum_windows():
        if vis and (cls in ('#32770', 'MX_SPLASH_WINDOW_CLASS', 'Afx:') or cls.startswith('Afx')
                    or '梦想' in title or '登录' in title or '服务器' in title or '更新' in title):
            out.append((h, pid, cls, title))
    return out


def report_change(tag):
    for pid, name in sorted(watch_procs().items()):
        log('  %s proc pid=%-6d %s' % (tag, pid, name))
    for h, pid, cls, title in watch_wins():
        log('  %s win  hwnd=0x%08X pid=%-6d class=%-24r title=%r' % (tag, h, pid, cls, title))


def find_child_by_cid(hwin, cid):
    found = []

    def cb(h, lp):
        if u32.GetDlgCtrlID(h) == cid:
            found.append(h)
        return True

    u32.EnumChildWindows(hwin, WNDENUMPROC(cb), 0)
    return found


def dump_buttons(hwin):
    log('--- 全部 Button 控件 ---')

    def cb(h, lp):
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, cls, 256)
        if cls.value == 'Button':
            r = wintypes.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            txt = ctypes.create_unicode_buffer(128)
            u32.GetWindowTextW(h, txt, 128)
            log('  btn cid=%-5d hwnd=0x%08X rect=(%d,%d,%d,%d) enabled=%s vis=%s text=%r' % (
                u32.GetDlgCtrlID(h), h, r.left, r.top, r.right, r.bottom,
                u32.IsWindowEnabled(h), u32.IsWindowVisible(h), txt.value))
        return True

    u32.EnumChildWindows(hwin, WNDENUMPROC(cb), 0)


def find_top(title):
    """找顶层窗口（优先可见）"""
    hits = []
    for h, pid, cls, t, vis in enum_windows():
        if t == title:
            hits.append((h, pid, cls, t, vis))
    hits.sort(key=lambda x: 0 if x[4] else 1)
    return hits


def go(cid, title=None):
    log('==== _mx_click GO cid=%d title=%r pid=%d ====' % (cid, title, os.getpid()))
    if title:
        hits = find_top(title)
        log('标题 %r 命中顶层窗口 %d 个: %s' % (
            title, len(hits), [(hex(h), pid, vis) for h, pid, _c, _t, vis in hits]))
        hwin = hits[0][0] if hits else None
    else:
        hwin = u32.FindWindowW(None, '梦想世界更新程序')
        if not hwin:
            hwin = u32.FindWindowW(None, '梦想世界')
    log('目标窗口 hwnd=%s' % (hex(hwin) if hwin else 'None'))
    if not hwin:
        log('!! 找不到启动器窗口，可用窗口:')
        for h, pid, cls, title, vis in enum_windows():
            if vis and title:
                log('   0x%08X pid=%-6d %r %r' % (h, pid, cls, title))
        return 2

    hbtn = None
    cand = find_child_by_cid(hwin, cid)
    log('cid=%d 命中 %d 个' % (cid, len(cand)))
    if cand:
        hbtn = cand[0]
    if not hbtn:
        dump_buttons(hwin)
        return 3

    r = wintypes.RECT()
    u32.GetWindowRect(hbtn, ctypes.byref(r))
    txt = ctypes.create_unicode_buffer(128)
    u32.GetWindowTextW(hbtn, txt, 128)
    log('按钮 hwnd=0x%08X rect=(%d,%d,%d,%d) enabled=%s vis=%s text=%r' % (
        hbtn, r.left, r.top, r.right, r.bottom,
        u32.IsWindowEnabled(hbtn), u32.IsWindowVisible(hbtn), txt.value))

    log('--- 点击前状态 ---')
    report_change('bef')

    u32.ShowWindow(hwin, 9)  # SW_RESTORE
    u32.SetForegroundWindow(hwin)
    time.sleep(0.4)

    ret = u32.SendMessageW(hbtn, 0x00F5, 0, 0)  # BM_CLICK
    log('BM_CLICK ret=%r err=%d' % (ret, ctypes.get_last_error()))

    changed = False
    for i in range(3):
        time.sleep(1.0)
        if not u32.IsWindow(hwin) or watch_procs():
            changed = True
            break

    if not changed:
        cx = (r.left + r.right) // 2
        cy = (r.top + r.bottom) // 2
        log('BM_CLICK 暂无反应 → 真实鼠标点击中心 (%d,%d)' % (cx, cy))
        u32.SetForegroundWindow(hwin)
        time.sleep(0.3)
        u32.SetCursorPos(cx, cy)
        time.sleep(0.2)
        u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.08)
        u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP

    # 监测 25 秒
    for i in range(25):
        time.sleep(1.0)
        win_alive = bool(u32.IsWindow(hwin))
        procs = watch_procs()
        if i in (4, 9, 14, 19, 24) or not win_alive:
            log('+%02ds 启动器存活=%s procs=%s' % (
                i + 1, win_alive,
                ['%d:%s' % (p, n) for p, n in sorted(procs.items())]))
            if not win_alive or i == 24:
                report_change('now')
        if not win_alive and i < 4:
            break

    log('==== _mx_click DONE ====')
    return 0


def spawn_elevated(cid, title=None):
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
    sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = 'runas'
    sei.lpFile = VENV_PYTHONW
    if title:
        sei.lpParameters = '"%s" --go-win "%s" %d' % (
            os.path.join(SIM_DIR, '_mx_click.py'), title, cid)
    else:
        sei.lpParameters = '"%s" --go %d' % (os.path.join(SIM_DIR, '_mx_click.py'), cid)
    sei.lpDirectory = SIM_DIR
    sei.nShow = 0
    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        log('!! ShellExecuteExW(runas) 失败 err=%d' % ctypes.get_last_error())
        return False
    log('已请求提权点击 (hProcess=%r)' % sei.hProcess)
    return True


if __name__ == '__main__':
    if '--go-win' in sys.argv:
        i = sys.argv.index('--go-win')
        t = sys.argv[i + 1]
        c = int(sys.argv[i + 2])
        sys.exit(go(c, title=t))
    elif '--go' in sys.argv:
        i = sys.argv.index('--go')
        cid = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 1003
        sys.exit(go(cid))
    else:
        open(LOG, 'w', encoding='utf-8').close()
        args = sys.argv[1:]
        title = None
        cid = 1003
        if '--win' in args:
            i = args.index('--win')
            title = args[i + 1]
            args = args[i + 2:]
        for a in args:
            if a.isdigit():
                cid = int(a)
        spawn_elevated(cid, title)
        time.sleep(2.0)
        # 镜像日志
        deadline = time.time() + 40
        pos = 0
        while time.time() < deadline:
            time.sleep(1.0)
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
