# -*- coding: utf-8 -*-
"""真机联调：提权状态下完成「拉起 mx.exe → 等进入游戏按钮可用 → 点击 → 保持进程存活」。

用法：
    python _live_launch.py            # 非提权 → 自动 runas 提权后执行 --go
    python _live_launch.py --go       # 提权环境直接执行

设计要点（踩过的坑）：
· mx.exe 是启动器：它会把自己复制到 patch\\mx.bin 再拉起，自身退出；
  真正的「梦想世界更新程序」窗口属于 mx.bin（class #32770）。
· 更新检查必然失败（UpdateURL 指向 mxupdate.duoyi.com），失败后 cid=1003 才 enabled，
  所以判定条件是「按钮 enabled」而非「请点击进入游戏」文案。
· 启动器是提权进程，低完整性进程的 SendInput/BM_CLICK 会被 UIPI 拦掉 ⇒ 本脚本必须提权。
· 脚本不结束（保持父进程存活），否则部分 Windows 版本会连带回收子进程。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

u32 = ctypes.windll.user32
LOG = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'live_launch.log')
CLIENT_DIR = r'D:\mxsj2013\client'
VENV_PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'

u32.FindWindowW.restype = wt.HWND
u32.FindWindowExW.restype = wt.HWND
u32.GetWindow.restype = wt.HWND
u32.GetDlgItem.restype = wt.HWND


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def find_launcher():
    h = u32.FindWindowW(None, '梦想世界更新程序')
    if not h:
        h = u32.FindWindowW(None, '梦想世界')
    return h


def find_btn(hwin, cid=1003):
    """按控件 id 找按钮（对话框用 GetDlgItem 最稳）。"""
    h = u32.GetDlgItem(hwin, cid)
    if h:
        return h
    # 兜底：遍历子窗口
    GW_CHILD, GW_HWNDNEXT = 5, 2

    def walk(parent):
        c = u32.GetWindow(parent, GW_CHILD)
        while c:
            buf = ctypes.create_unicode_buffer(256)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(c, cls, 256)
            u32.GetWindowTextW(c, buf, 256)
            cid_v = u32.GetDlgCtrlID(c)
            if cid_v == cid:
                return c
            got = walk(c)
            if got:
                return got
            c = u32.GetWindow(c, GW_HWNDNEXT)
        return None

    return walk(hwin)


def click(hbtn, hwin):
    r = wt.RECT()
    u32.GetWindowRect(hbtn, ctypes.byref(r))
    log('按钮 rect=(%d,%d,%d,%d) enabled=%s' % (r.left, r.top, r.right, r.bottom,
                                               u32.IsWindowEnabled(hbtn)))
    u32.ShowWindow(hwin, 9)
    u32.SetForegroundWindow(hwin)
    time.sleep(0.4)
    ret = u32.SendMessageW(hbtn, 0x00F5, 0, 0)          # BM_CLICK
    log('BM_CLICK ret=%r' % ret)
    for _ in range(12):                                 # 最多 6s 等 BM_CLICK 生效
        time.sleep(0.5)
        if u32.FindWindowA(b'Game', None):
            return True
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    log('物理点击 (%d,%d)' % (cx, cy))
    u32.SetCursorPos(cx, cy)
    time.sleep(0.1)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    for _ in range(40):                                 # 物理点击后再等最多 20s 出窗口
        time.sleep(0.5)
        if u32.FindWindowA(b'Game', None):
            return True
    return False


def go():
    log('==== live_launch GO pid=%d elevated=%s ====' % (os.getpid(), ctypes.windll.shell32.IsUserAnAdmin() != 0))
    if u32.FindWindowA(b'Game', None):
        log('已有 Game 窗口，无需启动')
        return 0
    p = subprocess.Popen([os.path.join(CLIENT_DIR, 'mx.exe')], cwd=CLIENT_DIR)
    log('已启动 mx.exe pid=%d' % p.pid)

    hwin = None
    for i in range(40):
        time.sleep(0.5)
        hwin = find_launcher()
        if hwin:
            log('找到启动器窗口 hwnd=0x%08X（%.1fs）' % (hwin, (i + 1) * 0.5))
            break
    if not hwin:
        log('!! 未找到启动器窗口')
        return 2

    hbtn = None
    for i in range(60):
        hbtn = find_btn(hwin, 1003)
        if hbtn and u32.IsWindowEnabled(hbtn):
            log('按钮可用（%.1fs）' % (i * 0.5))
            break
        time.sleep(0.5)
    if not hbtn:
        log('!! 未找到 cid=1003')
        return 3

    if click(hbtn, hwin):
        log('★ 已进入游戏（Game 窗口就绪）')
    else:
        log('⚠ 点击后仍无 Game 窗口')

    # 保持存活：轮询游戏进程/窗口，直到 Game 窗口消失
    miss = 0
    while True:
        time.sleep(5)
        if u32.FindWindowA(b'Game', None):
            miss = 0
        else:
            miss += 5
            if miss >= 30:
                log('Game 窗口连续 30s 不存在，脚本退出')
                return 0


def main():
    if '--go' in sys.argv:
        return go()
    # 非提权 → runas 提权执行自己
    params = '"%s" --go' % os.path.abspath(__file__)
    r = ctypes.windll.shell32.ShellExecuteW(None, 'runas', VENV_PY, params,
                                            os.path.dirname(os.path.abspath(__file__)), 1)
    log('已请求提权启动 (ret=%d)' % r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
