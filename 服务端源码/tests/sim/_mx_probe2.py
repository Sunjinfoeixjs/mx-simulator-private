# -*- coding: utf-8 -*-
"""真机联调・提权交互探针（模拟「用户在本机操作」的完整链路）。

与 _mx_probe.py 的区别：
  1) 未被提权时先自提权（ShellExecuteEx runas，静默）再运行；
  2) 检测到「更新程序已经运行」弹框时自动点「是」（BM_CLICK）；
  3) 不因首个 mx.exe 退出而停止——继续观察后继进程（自更新重启的可能）；
  4) 全程截图 + 窗口树 dump，写入日志文件。

用法:
    python _mx_probe2.py            # 自提权后运行（预算 150s）
    python _mx_probe2.py --go 150   # 已提权模式（内部用）
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

BUDGET = 150.0
TARGET = r'D:\mxsj2013\client\mx.exe'
OUT = os.path.join(os.environ.get('TEMP', '.'), 'mx_shots')
TAG = time.strftime('%H%M%S')
LOG = os.path.join(os.environ.get('TEMP', '.'), 'mx_probe2_%s.log' % TAG)

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GW_CHILD, GW_HWNDNEXT = 5, 2
BM_CLICK = 0x00F5

u32.GetWindow.restype = wt.HWND
u32.GetWindow.argtypes = [wt.HWND, wt.UINT]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

_logf = None


def log(msg):
    global _logf
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    if _logf:
        _logf.write(line + '\n')
        _logf.flush()


def elevate_self():
    """ShellExecuteExW runas 重启自身（pythonw 无控制台窗口）。"""
    SEE_MASK_NOCLOSEPROCESS = 0x40
    SW_HIDE = 0

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('fMask', wt.ULONG), ('hwnd', wt.HWND),
                    ('lpVerb', wt.LPCWSTR), ('lpFile', wt.LPCWSTR),
                    ('lpParameters', wt.LPCWSTR), ('lpDirectory', wt.LPCWSTR),
                    ('nShow', ctypes.c_int), ('hInstApp', wt.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', wt.LPCWSTR),
                    ('hkeyClass', wt.HKEY), ('dwHotKey', wt.DWORD),
                    ('hIcon', wt.HANDLE), ('hProcess', wt.HANDLE)]

    pyw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.isfile(pyw):
        pyw = sys.executable
    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = 'runas'
    sei.lpFile = pyw
    sei.lpParameters = '"%s" --go %d' % (os.path.abspath(__file__), int(BUDGET))
    sei.lpDirectory = os.path.dirname(os.path.abspath(__file__))
    sei.nShow = SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.windll.kernel32.GetLastError()
        print('自提权失败 WinError=%d' % err)
        return False
    if sei.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 3000)
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return True


def text(h):
    buf = ctypes.create_unicode_buffer(1024)
    u32.GetWindowTextW(h, buf, 1024)
    return buf.value


def cls(h):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, buf, 256)
    return buf.value


def wpid(h):
    p = wt.DWORD()
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def mx_pids():
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq mx.exe', '/FO', 'CSV', '/NH'],
            stderr=subprocess.DEVNULL).decode('gbk', 'replace')
    except subprocess.CalledProcessError:
        return []
    pids = []
    for ln in out.splitlines():
        f = [x.strip('"') for x in ln.split('","')]
        if len(f) >= 2 and f[0].lower() == 'mx.exe' and f[1].isdigit():
            pids.append(int(f[1]))
    return pids


def tops_of(pid):
    tops = []

    def on_top(h, l):
        if wpid(h) == pid:
            tops.append(h)
        return True

    u32.EnumWindows(ENUMPROC(on_top), 0)
    return tops


def walk(parent, depth=0, maxdepth=3, limit=None):
    if limit is None:
        limit = [160]
    if depth > maxdepth or limit[0] <= 0:
        return
    c = u32.GetWindow(parent, GW_CHILD)
    while c and limit[0] > 0:
        limit[0] -= 1
        t, k = text(c), cls(c)
        vis = '' if u32.IsWindowVisible(c) else '(隐)'
        log('%s[%s]%s %r' % ('  ' * (depth + 1), k, vis, t))
        walk(c, depth + 1, maxdepth, limit)
        c = u32.GetWindow(c, GW_HWNDNEXT)


def click_yes_on_update_dialog(pid):
    """在 pid 的窗口里找「更新程序已经运行」对话框并点「是」。返回是否点了。"""
    clicked = 0
    for h in tops_of(pid):
        if cls(h) != '#32770' or not u32.IsWindowVisible(h):
            continue
        # 找对话框里的 Static 文本与按钮
        found_dlg, yes_btn = False, None
        kids, btns = [], []

        def on_child(c, l):
            kids.append(c)
            return True

        u32.EnumChildWindows(h, ENUMPROC(on_child), 0)
        for c in kids:
            k, t = cls(c), text(c)
            if k == 'Static' and '更新程序已经运行' in t:
                found_dlg = True
            if k.startswith('Button'):
                btns.append((c, t))
        if not found_dlg:
            continue
        for c, t in btns:
            if '是' in t.replace('&', ''):
                yes_btn = c
        if yes_btn:
            u32.SendMessageW(yes_btn, BM_CLICK, 0, 0)
            clicked += 1
            log('  ★ 已点击「是」按钮（更新程序已运行对话框，hwnd=%s）' % hex(h))
    return clicked


def grab(shot_no):
    try:
        from PIL import ImageGrab
        p = os.path.join(OUT, 'p2_%s_%02d.png' % (TAG, shot_no))
        ImageGrab.grab().save(p)
        return p
    except Exception as e:                                      # noqa: BLE001
        log('  截图失败: %r' % e)
        return None


def main():
    global _logf, BUDGET
    os.makedirs(OUT, exist_ok=True)
    _logf = open(LOG, 'w', encoding='utf-8')

    log('提权状态: %s' % bool(ctypes.windll.shell32.IsUserAnAdmin()))
    log('目标: %s' % TARGET)

    proc = None
    try:
        proc = subprocess.Popen([TARGET], cwd=os.path.dirname(TARGET))
        log('已直接拉起（提权父进程）pid=%s' % proc.pid)
    except OSError as e:
        log('Popen 失败: %r' % e)
        _logf.close()
        return 2

    seen_pids = set()
    exited = set()
    last_sig = {}
    shot = 0
    clicked_total = 0
    deadline = time.time() + BUDGET

    while time.time() < deadline:
        now_pids = mx_pids()
        for p in now_pids:
            if p not in seen_pids:
                seen_pids.add(p)
                log('=== 发现 mx.exe pid=%d（累计第 %d 个）===' % (p, len(seen_pids)))
        for p in list(seen_pids - set(now_pids)):
            if p not in exited:
                exited.add(p)
                log('--- mx.exe pid=%d 已退出（已退出 %d 个）---' % (p, len(exited)))

        for p in now_pids:
            tops = tops_of(p)
            sig = tuple((cls(h), text(h)) for h in tops)
            if sig != last_sig.get(p):
                last_sig[p] = sig
                log('pid=%d 窗口集变化（%d 个顶层）:' % (p, len(tops)))
                for h in tops:
                    vis = '' if u32.IsWindowVisible(h) else '(隐)'
                    log('  >> HWND=%s%s class=%r title=%r'
                        % (hex(h), vis, cls(h), text(h)))
                    walk(h)
            clicked_total += click_yes_on_update_dialog(p)
            # 把游戏窗口提到前台（提权后可操作）
            for h in tops:
                if cls(h) in ('MX_SPLASH_WINDOW_CLASS',) or \
                        (cls(h) == '#32770' and text(h) not in ('', '错误')):
                    u32.SetForegroundWindow(h)

        if shot < 60 and now_pids:
            shot += 1
            p = grab(shot)
            if p:
                log('  截图 -> %s' % p)

        if not now_pids and exited and time.time() > deadline - BUDGET + 20:
            # 全部进程退出且已过 20s 缓冲（等自更新重启的后继进程）
            pass
        time.sleep(0.25)

    log('探针结束：发现进程 %d 个，点击「是」共 %d 次，截图 %d 张'
        % (len(seen_pids), clicked_total, shot))
    log('日志: %s' % LOG)
    _logf.close()
    return 0


if __name__ == '__main__':
    if '--go' in sys.argv:
        idx = sys.argv.index('--go')
        if len(sys.argv) > idx + 1:
            BUDGET = float(sys.argv[idx + 1])
        sys.exit(main())
    else:
        ok = elevate_self()
        print('已请求提权运行，日志将写入 %s' % LOG)
        sys.exit(0 if ok else 1)
