# -*- coding: utf-8 -*-
"""
真机测试工具 v1 —— 干净状态复现「双击 mx.exe」完整启动链
================================================================
背景（反汇编结论）:
  mx.exe 引导逻辑 = GlobalAtom 状态机:
    [run=0, shadow=0] 干净   -> 静默路径: 建run原子 -> 复制自身到 patch\\mx.bin
                                -> 建shadow原子 -> 启动 patch\\mx.bin -> 等run原子消失 -> 退出 rc=0(设计)
    [run=1, shadow=1] 副本进程 -> 删run原子 -> 返回1 -> 主流程继续 -> 显示启动器UI
    [仅存其一] 非正常残留   -> 弹「更新程序已经运行,是否继续运行?」对话框

模式:
  python _mx_run.py            # wrapper(非提权): 清理检查 -> 提权助手 -> 轮询日志 -> 截图
  python _mx_run.py --helper   # 助手(提权后运行): 清原子 -> 启动mx.exe -> 监控 -> 自动点击
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import time
import threading
import urllib.request

SIM_DIR = r'C:\Users\Administrator\MX\服务端源码\sim'
CLIENT_DIR = r'D:\mxsj2013\client'
MX_EXE = os.path.join(CLIENT_DIR, 'mx.exe')
LOG = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'mx_run.log')
SHOTS = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'mx_shots')
VENV_PYTHONW = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe'
HELPER_DURATION = 1800.0   # 2026-10-18 真机联调：210s 会在验证中途关掉客户端，改为 30 分钟
WRAPPER_MAX = 240.0

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# 64 位句柄必须显式声明 restype/argtypes，否则会被截断为 int32
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
kernel32.GlobalFindAtomA.restype = ctypes.c_ushort
kernel32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
kernel32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]

# user32 句柄返回值必须显式声明，否则 64 位下被截断为 int32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.IsWindowEnabled.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
user32.GetDlgCtrlID.restype = ctypes.c_int

BM_CLICK = 0xF5
TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE = ctypes.c_void_p(-1).value


# ---------------------------------------------------------------- logging
def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
    except OSError:
        pass
    if not sys.argv[0].lower().endswith('pythonw.exe'):
        try:
            print(line, flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------- atoms
def atom_state():
    kernel32.GlobalFindAtomA.restype = ctypes.c_ushort
    kernel32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
    return (kernel32.GlobalFindAtomA(b'mxpatch_run'),
            kernel32.GlobalFindAtomA(b'mxpatch_shadow'))


def clean_atoms():
    kernel32.GlobalFindAtomA.restype = ctypes.c_ushort
    kernel32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
    kernel32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]
    for name in (b'mxpatch_run', b'mxpatch_shadow'):
        n = 0
        while True:
            a = kernel32.GlobalFindAtomA(name)
            if a == 0:
                break
            kernel32.GlobalDeleteAtom(a)
            n += 1
            if n > 20:
                break
        log('清理原子 %s: 删除 %d 次' % (name.decode(), n))


# ---------------------------------------------------------------- processes
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_void_p),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


def enum_procs():
    """返回 {pid: (name, exe_path)}"""
    out = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return out
        while True:
            pid = pe.th32ProcessID
            name = pe.szExeFile
            path = ''
            h = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        path = buf.value
                finally:
                    kernel32.CloseHandle(h)
            out[pid] = (name, path)
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return out


def interesting_proc(name, path):
    n = (name or '').lower()
    if n in ('mx.exe', 'mx.bin', 'gameplayer.exe', 'mxupd.exe', 'mxupdate_unicode.exe'):
        return True
    p = (path or '').lower()
    return 'mxsj2013' in p


# ---------------------------------------------------------------- windows
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _cls(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid_of(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def enum_windows():
    """返回 [(hwnd, pid, cls, title, visible)]"""
    res = []

    def cb(hwnd, _):
        res.append((hwnd, _pid_of(hwnd), _cls(hwnd), _text(hwnd),
                    bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return res


def enum_children(hwnd):
    """返回 [(hwnd, cls, text, ctrlid, rect)]"""
    res = []

    def cb(h, _):
        rect = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(rect))
        res.append((h, _cls(h), _text(h), user32.GetDlgCtrlID(h),
                    (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return res


def click_now(hwnd):
    r = user32.PostMessageW(hwnd, BM_CLICK, 0, 0)  # 异步，避免模态处理阻塞
    if not r:
        r = user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
    return r


def _activate_top(hwnd):
    """尽力把窗口置前（AttachThreadInput hack），返回是否成功成为前台"""
    top = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    if not top:
        top = hwnd
    user32.SetWindowPos(top, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # TOPMOST, NOSIZE|NOMOVE
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if fg_tid and fg_tid != my_tid:
            attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))
        user32.BringWindowToTop(top)
        user32.SetForegroundWindow(top)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(my_tid, fg_tid, False)
            except Exception:
                pass
    time.sleep(0.25)
    return user32.GetForegroundWindow() == top


def phys_click(hwnd):
    """物理点击：激活前台 + 真实鼠标点击窗口中心。
    返回 (前台成功, 坐标, 命中校验, 命中窗口描述)
    命中校验 = 点击坐标处的顶层窗口确实是目标窗口（或其祖先）"""
    ok_fg = _activate_top(hwnd)
    top = user32.GetAncestor(hwnd, 2) or hwnd
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    # 分两步移动 + 悬停，产生 WM_MOUSEMOVE 序列（更接近真实鼠标）
    user32.SetCursorPos(cx - 60, cy - 30)
    time.sleep(0.08)
    user32.SetCursorPos(cx, cy)
    time.sleep(0.35)
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hit = user32.WindowFromPoint(pt)
    hit_top = (user32.GetAncestor(hit, 2) or hit) if hit else 0
    hit_ok = bool(hit) and (hit == hwnd or hit == top or hit_top == top)
    hit_desc = '%s/%r' % (_cls(hit), _text(hit)[:24]) if hit else 'None'
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.09)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(0.1)
    try:
        user32.SetCursorPos(20, 300)  # 指针移开避免遮挡
    except Exception:
        pass
    return ok_fg, (cx, cy), hit_ok, hit_desc


def msg_click(hwnd):
    """WM_LBUTTONDOWN/UP 直达按钮（client 中心坐标）——不依赖前台"""
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    x = max(1, (rc.right - rc.left) // 2)
    y = max(1, (rc.bottom - rc.top) // 2)
    lp = (y << 16) | (x & 0xFFFF)
    r1 = user32.PostMessageW(hwnd, 0x0201, 0x0001, lp)  # WM_LBUTTONDOWN + MK_LBUTTON
    time.sleep(0.06)
    r2 = user32.PostMessageW(hwnd, 0x0202, 0, lp)  # WM_LBUTTONUP
    return (r1, r2)


# ---------------------------------------------------------------- helper
def helper_main():
    log('==== HELPER 启动（提权=%s, pid=%d）====' % (
        bool(ctypes.windll.shell32.IsUserAnAdmin()), os.getpid()))

    log('起始原子: run=%d shadow=%d' % atom_state())
    clean_atoms()
    log('清理后原子: run=%d shadow=%d' % atom_state())

    st0 = os.stat(os.path.join(CLIENT_DIR, 'patch', 'mx.bin'))
    log('patch\\mx.bin 起始: size=%d ctime=%s mtime=%s' % (
        st0.st_size,
        time.strftime('%m-%d %H:%M:%S', time.localtime(st0.st_ctime)),
        time.strftime('%m-%d %H:%M:%S', time.localtime(st0.st_mtime))))

    log('拉起 mx.exe (cwd=%s)' % CLIENT_DIR)
    proc = None
    try:
        proc = subprocess.Popen([MX_EXE], cwd=CLIENT_DIR, close_fds=True)
        log('mx.exe pid=%d' % proc.pid)
    except OSError as e:
        log('!! 拉起失败: %r' % e)

    seen = {}            # pid -> (name, path)
    win_sig_prev = None
    clicked = set()
    go_seen_at = None
    go_ready_at = None   # 「请点击进入游戏」首次出现时刻
    go_state = {'n': 0, 'stage': 'idle', 'at': 0.0}
    go_success = False
    ui_prev = None       # (cid1009文本, cid1008文本, btn1003_enabled)
    t0 = time.time()
    tick = 0
    atom_prev = atom_state()

    while time.time() - t0 < HELPER_DURATION:
        tick += 1
        # --- processes
        procs = enum_procs()
        cur = {}
        for pid, (name, path) in procs.items():
            if interesting_proc(name, path):
                cur[pid] = (name, path)
        for pid in cur:
            if pid not in seen:
                log('+ 进程  pid=%-6d %-16s %s' % (pid, cur[pid][0], cur[pid][1]))
        for pid in list(seen):
            if pid not in cur and pid not in procs:
                log('- 进程  pid=%-6d %-16s 已退出' % (pid, seen[pid][0]))
        seen = cur

        # --- windows
        wins = enum_windows()
        focus = [w for w in wins
                 if w[2] in ('#32770', 'MX_SPLASH_WINDOW_CLASS')
                 or any(k in w[3] for k in ('梦想', '更新', '错误', '脚本'))
                 or w[1] in seen]
        sig = tuple(sorted((w[0], w[1], w[2], w[3], w[4]) for w in focus))
        if sig != win_sig_prev:
            log('--- 窗口变化（顶层 %d / 关注 %d）---' % (len(wins), len(focus)))
            for w in focus:
                log('  >> HWND=0x%08X pid=%-6d vis=%d class=%r title=%r' % (
                    w[0], w[1], w[4], w[2], w[3]))
                if w[2] == '#32770' or w[2] == 'MX_SPLASH_WINDOW_CLASS':
                    for ch in enum_children(w[0]):
                        log('     [child] cls=%-20r cid=%-5d rect=%-24s text=%r' % (
                            ch[1], ch[3], ch[4], ch[2]))
            win_sig_prev = sig

        # --- UI 演化监控（启动器 cid=1009 状态文本 / cid=1008 最新版本 / cid=1003 按钮 enabled）
        for w in focus:
            if w[2] != '#32770' or '梦想世界更新程序' not in w[3]:
                continue
            kids = enum_children(w[0])
            st_txt = ''
            ver_txt = ''
            btn_en = None
            for k in kids:
                if k[3] == 1009:
                    st_txt = k[2].strip()
                elif k[3] == 1008:
                    ver_txt = k[2].strip()
                elif k[3] == 1003 and k[1] == 'Button':
                    btn_en = bool(user32.IsWindowEnabled(k[0]))
            key = (st_txt, ver_txt, btn_en)
            if key != ui_prev:
                log('UI演化: status1009=%r latestver1008=%r btn1003_enabled=%s' % key)
                ui_prev = key

        # --- atoms (每 4 tick ≈ 1s)
        if tick % 4 == 0:
            a = atom_state()
            if a != atom_prev:
                log('原子变化: run=%d shadow=%d' % a)
                atom_prev = a

        # --- 自动点击: 「更新程序已经运行」对话框 -> 是
        for w in focus:
            if w[2] == '#32770' and w[0] not in clicked:
                kids = enum_children(w[0])
                txt = ''.join(k[2] for k in kids)
                title = w[3]
                if '更新程序已经运行' in txt and '是' in txt:
                    for k in kids:
                        if k[1] == 'Button' and '是' in k[2]:
                            r = click_now(k[0])
                            clicked.add(w[0])
                            log('★ 点击「是」key=%r ret=%d (hwnd=0x%08X)' % (k[2], r, w[0]))
                            break
                elif '脚本' in title or ('错误' in title and 'Internet' in title):
                    for k in kids:
                        if k[1] == 'Button' and ('否' in k[2] or '是' in k[2]):
                            r = click_now(k[0])
                            clicked.add(w[0])
                            log('★ 点击脚本错误框 %r ret=%d' % (k[2], r))
                            break

        # --- 自动点击「提示」类对话框的「确定」（优先于其它点击）
        for w in focus:
            if w[2] == '#32770' and w[3] == '提示' and w[0] not in clicked:
                kids = enum_children(w[0])
                tip_txt = ' | '.join('%s(cid=%d)=%r' % (k[1], k[3], k[2]) for k in kids if k[2])
                for k in kids:
                    if k[1] == 'Button' and k[2] == '确定':
                        r = click_now(k[0])
                        clicked.add(w[0])
                        log('★ 点击「提示·确定」ret=%d 提示内容[%s]' % (r, tip_txt))
                        break

        # --- 自动点击「进入游戏」（物理点击链 + 严格就绪判定）
        # 教训1: BM_CLICK 走 BN_CLICKED 错误分支（弹「提示·更新完成」死循环）
        # 教训2: 更新检查未完成时点击（最新版本:未知 / 按钮 disabled）也弹「提示·更新完成」
        #        -> 必须等「请点击进入游戏」出现 + 按钮 enabled 后点击
        for w in focus:
            if w[2] != '#32770' or '梦想世界更新程序' not in w[3]:
                continue
            kids = enum_children(w[0])
            alltxt = ''.join(k[2] for k in kids)
            go_btn = None
            for k in kids:
                if k[1] == 'Button' and (
                        '进入游戏' in k[2]
                        or (k[3] == 1003 and '梦想世界更新程序' in w[3])):
                    go_btn = k
                    break
            if go_btn is None:
                continue
            if go_seen_at is None:
                go_seen_at = time.time()
                log('** 发现「进入游戏」按钮 hwnd=0x%08X cid=%d rect=%s（UI 文本: %r）' % (
                    go_btn[0], go_btn[3], go_btn[4], alltxt[:150]))
            b = go_btn[0]
            now = time.time()
            btn_en = bool(user32.IsWindowEnabled(b))

            if '正在退出' in alltxt:
                if not go_success:
                    go_success = True
                    log('✅✅✅ 点击生效！cid=1009 已变「正在退出......请稍等！」')
                continue

            has_tip = any(x[2] == '#32770' and x[3] == '提示' and x[4] for x in wins)
            st = go_state

            if has_tip:
                pass  # 交给「提示」处理段点「确定」
            elif st['stage'] == 'phys' and now - st['at'] > 2.5:
                r = msg_click(b)
                st['stage'] = 'msg'
                st['at'] = now
                log('↩ 物理点击 2.5s 无反应 → 补发 WM_LBUTTONDOWN/UP ret=%s (hwnd=0x%08X)' % (
                    r, b))
            elif st['stage'] == 'msg' and now - st['at'] > 4.0:
                st['stage'] = 'idle'
                log('… 本轮点击无效（msg 兜底后 4s 无反应），准备下一轮')

            # 严格就绪: 出现「请点击进入游戏」（1009 完整就绪文案）
            # 注意: cid=1003 是自绘按钮, IsWindowEnabled 恒为 False, 不能作为点击条件
            # 2026-10-18 真机联调：更新服务器不可达时官方启动器直接放行（按钮 enabled，
            # 文案为「获取更新列表失败…」），此时也应视为就绪，否则永远点不进去。
            _ready_txt = ('请点击进入游戏' in alltxt
                          or ('获取更新列表失败' in alltxt and btn_en))
            if go_ready_at is None and _ready_txt:
                go_ready_at = now
                log('** UI 就绪（%s），准备点击（延迟 2.5s 缓冲）' % ('请点击进入游戏' if '请点击进入游戏' in alltxt else '更新列表失败但按钮可用'))

            if (st['stage'] == 'idle' and not has_tip and st['n'] < 4
                    and now >= st['at'] + 8 and go_ready_at is not None
                    and now - go_ready_at > 2.5):
                st['n'] += 1
                st['stage'] = 'phys'
                st['at'] = now
                ok_fg, coord, hit_ok, hit_desc = phys_click(b)
                log('★★★ 物理点击「进入游戏」第%d次 fg=%s 坐标=%s 命中=%s (hit=%s) btn_en=%s 完整UI=%r' % (
                    st['n'], ok_fg, coord, hit_ok, hit_desc, btn_en, alltxt))

        if tick % 20 == 0:
            a = atom_state()
            log('HB t=%ds atoms=run%d/shadow%d procs=%d wins=%d' % (
                int(time.time() - t0), a[0], a[1], len(seen), len(wins)))

        time.sleep(0.25)

    # 收尾
    log('--- 巡检结束 最终状态 ---')
    procs = enum_procs()
    for pid, (name, path) in procs.items():
        if interesting_proc(name, path):
            log('  存活: pid=%-6d %-16s %s' % (pid, name, path))
    for w in enum_windows():
        if w[2] in ('#32770', 'MX_SPLASH_WINDOW_CLASS') or '梦想' in w[3]:
            log('  窗口: hwnd=0x%08X pid=%d class=%r title=%r' % (w[0], w[1], w[2], w[3]))
    log('最终原子: run=%d shadow=%d' % atom_state())
    st1 = os.stat(os.path.join(CLIENT_DIR, 'patch', 'mx.bin'))
    log('patch\\mx.bin 结束: size=%d ctime=%s mtime=%s' % (
        st1.st_size,
        time.strftime('%m-%d %H:%M:%S', time.localtime(st1.st_ctime)),
        time.strftime('%m-%d %H:%M:%S', time.localtime(st1.st_mtime))))
    log('==== HELPER DONE ====')


# ---------------------------------------------------------------- runas spawn
class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD),
                ('fMask', ctypes.c_ulong),
                ('hwnd', wintypes.HWND),
                ('lpVerb', wintypes.LPCWSTR),
                ('lpFile', wintypes.LPCWSTR),
                ('lpParameters', wintypes.LPCWSTR),
                ('lpDirectory', wintypes.LPCWSTR),
                ('nShow', ctypes.c_int),
                ('hInstApp', ctypes.c_void_p),
                ('lpIDList', ctypes.c_void_p),
                ('lpClass', wintypes.LPCWSTR),
                ('hkeyClass', ctypes.c_void_p),
                ('dwHotKey', wintypes.DWORD),
                ('hIcon', ctypes.c_void_p),
                ('hProcess', wintypes.HANDLE)]


def spawn_elevated_helper():
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    sei = SHELLEXECUTEINFOW()
    ctypes.memset(ctypes.byref(sei), 0, ctypes.sizeof(sei))
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = 'runas'
    sei.lpFile = VENV_PYTHONW
    sei.lpParameters = '"%s" --helper' % os.path.join(SIM_DIR, '_mx_run.py')
    sei.lpDirectory = SIM_DIR
    sei.nShow = 0
    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    err = ctypes.get_last_error()
    if not ok:
        log('!! ShellExecuteExW(runas) 失败: err=%d' % err)
        return False
    log('提权助手已请求启动 (hProcess=%r)' % sei.hProcess)
    return True


# ---------------------------------------------------------------- wrapper
def shoot_loop(stop):
    try:
        from PIL import ImageGrab
    except Exception as e:
        log('截图线程不可用: %r' % e)
        return
    os.makedirs(SHOTS, exist_ok=True)
    i = 0
    while not stop.is_set():
        i += 1
        try:
            img = ImageGrab.grab()
            p = os.path.join(SHOTS, 'run_%s_%02d.png' % (time.strftime('%H%M%S'), i))
            img.save(p)
        except Exception:
            pass
        stop.wait(3.0)


def wrapper_main():
    open(LOG, 'w', encoding='utf-8').close()
    log('==== WRAPPER 启动 pid=%d ====' % os.getpid())

    # 预检: 模拟服 HTTP
    for path in ('/server7.txt', '/blank'):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080' + path, timeout=3) as r:
                body = r.read(200)
            log('预检 8080%s -> %s len=%d head=%r' % (path, r.status, len(body), body[:60]))
        except Exception as e:
            log('预检 8080%s -> 失败 %r' % (path, e))

    log('当前原子: run=%d shadow=%d' % atom_state())
    if not spawn_elevated_helper():
        log('==== WRAPPER 中止（提权失败）====')
        return

    stop = threading.Event()
    th = threading.Thread(target=shoot_loop, args=(stop,), daemon=True)
    th.start()

    deadline = time.time() + WRAPPER_MAX
    pos = 0
    done = False
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
                if 'HELPER DONE' in chunk:
                    done = True
                    break
        except OSError:
            pass

    stop.set()
    log('截图目录: %s' % SHOTS)
    log('==== WRAPPER DONE (helper_done=%s) ====' % done)


if __name__ == '__main__':
    if '--helper' in sys.argv:
        os.makedirs(SHOTS, exist_ok=True)
        helper_main()
    else:
        wrapper_main()
