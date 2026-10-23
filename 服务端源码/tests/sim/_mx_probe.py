# -*- coding: utf-8 -*-
"""真机联调探针：同一进程内完成「提权拉起 mx.exe → 全程抓窗 + 截图」。

复用 singleplayer.launch_client（与生产路径完全一致），彻底消除工具调用间隔，
避免客户端秒退时抓不到现场。窗口集有变化即 dump 控件树（读出弹框文字），
并按 1.5s 节奏全屏截图到 %TEMP%\\mx_shots\\。

用法: python _mx_probe.py [最长秒数=90] [目标exe=D:\\mxsj2013\\client\\mx.exe]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import singleplayer as sp                                   # noqa: E402

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GW_CHILD, GW_HWNDNEXT = 5, 2

u32.GetWindow.restype = wt.HWND
u32.GetWindow.argtypes = [wt.HWND, wt.UINT]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
k32.GetProcessId.restype = wt.DWORD
k32.GetProcessId.argtypes = [wt.HANDLE]

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
TARGET = sys.argv[2] if len(sys.argv) > 2 else r'D:\mxsj2013\client\mx.exe'
TAG = time.strftime('%H%M%S')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'mx_shots')
os.makedirs(OUT, exist_ok=True)


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
        limit = [150]
    if depth > maxdepth or limit[0] <= 0:
        return
    c = u32.GetWindow(parent, GW_CHILD)
    while c and limit[0] > 0:
        limit[0] -= 1
        t, k = text(c), cls(c)
        vis = '' if u32.IsWindowVisible(c) else '(隐)'
        print('%s[%s]%s %r' % ('  ' * (depth + 1), k, vis, t))
        walk(c, depth + 1, maxdepth, limit)
        c = u32.GetWindow(c, GW_HWNDNEXT)


print('本进程提权状态: %s' % bool(ctypes.windll.shell32.IsUserAnAdmin()))
print('拉起目标: %s' % TARGET)
proc = sp.launch_client(os.path.dirname(TARGET), TARGET)
pid = k32.GetProcessId(proc._h) if hasattr(proc, '_h') else proc.pid
print('已拉起 mx.exe pid=%s' % pid)

last_sig = None
shots = 0
t_shot = 0.0
deadline = time.time() + BUDGET

while True:
    rc = proc.poll()
    now = time.time()
    if rc is None:
        tops = tops_of(pid)
        sig = tuple((cls(h), text(h)) for h in tops)
        if sig != last_sig:
            last_sig = sig
            print('[%s] rc=None 顶层窗口 %d 个' % (time.strftime('%H:%M:%S'), len(tops)))
            for h in tops:
                vis = '' if u32.IsWindowVisible(h) else '(隐)'
                print('  >> HWND=%s%s class=%r title=%r'
                      % (hex(h), vis, cls(h), text(h)))
                walk(h)
    if now - t_shot >= 1.5 and shots < 30:
        shots += 1
        t_shot = now
        p = os.path.join(OUT, 'probe_%s_%02d.png' % (TAG, shots))
        try:
            ImageGrab.grab().save(p)
            print('  截图 -> %s' % p)
        except Exception as e:                              # noqa: BLE001
            print('  截图失败: %r' % e)
    if rc is not None:
        print('[%s] mx.exe 已退出 rc=%s（存活 %.1fs）'
              % (time.strftime('%H:%M:%S'), rc, now - (deadline - BUDGET)))
        break
    if now >= deadline:
        print('达到 %ds 预算，mx.exe 仍在运行（探针退出，客户端不受影响）' % BUDGET)
        break
    time.sleep(0.25)

print('probe done (shots=%d, out=%s)' % (shots, OUT))
