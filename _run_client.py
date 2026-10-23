# -*- coding: utf-8 -*-
"""临时：设置启动器原子后直接拉起 patch/mx.bin（免提权副本），并截图。

背景：mx.exe 清单要求 requireAdministrator；已把副本清单改成 asInvoker 并覆盖到
patch/mx.bin。启动器的 GlobalAtom 状态机要求 [mxpatch_run, mxpatch_shadow] 同时存在
才会走「副本进程」分支 → 删 run 原子 → 显示启动器 UI。
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
u32 = ctypes.WinDLL('user32', use_last_error=True)
k32.GlobalAddAtomA.restype = ctypes.c_ushort
k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalFindAtomA.restype = ctypes.c_ushort
k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]

for name in (b'mxpatch_run', b'mxpatch_shadow'):
    while k32.GlobalFindAtomA(name):
        k32.GlobalDeleteAtom(k32.GlobalFindAtomA(name))
    a = k32.GlobalAddAtomA(name)
    print('atom %s -> %d' % (name, a))

exe = r'D:\mxsj2013\client\patch\mx.bin'
p = subprocess.Popen([exe], cwd=r'D:\mxsj2013\client')
print('pid', p.pid)
time.sleep(12)
print('alive', p.poll())

u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
res = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def cb(h, lp):
    n = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(h, n, 256)
    if n.value:
        r = wintypes.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        res.append((h, u32.IsWindowVisible(h), n.value,
                    (r.left, r.top, r.right - r.left, r.bottom - r.top)))
    return True


u32.EnumWindows(cb, 0)
for x in res:
    print(x)

from PIL import ImageGrab
ImageGrab.grab().save(r'C:\Users\Administrator\MX\_screen_client.png')
print('shot saved')
