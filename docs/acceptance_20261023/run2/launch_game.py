# -*- coding: utf-8 -*-
"""直接拉起游戏进程（跳过更新程序）。

原理（见 MX/_run_client.py）：mx.exe 的清单是 requireAdministrator，patch/mx.bin 是
改成 asInvoker 的副本；启动器用两个全局原子 mxpatch_run / mxpatch_shadow 判断
「是否走副本进程分支」——两个原子都在时直接进游戏，否则弹更新/启动器 UI。

本进程常驻，游戏进程树随它存活；agent.py 通过窗口附着操作。
"""
import ctypes
import os
import subprocess
import sys
import time

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.GlobalAddAtomA.restype = ctypes.c_ushort
k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalFindAtomA.restype = ctypes.c_ushort
k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]

CLIENT = r'D:\mxsj2013\client'
EXE = os.path.join(CLIENT, 'patch', 'mx.bin')

for name in (b'mxpatch_run', b'mxpatch_shadow'):
    while k32.GlobalFindAtomA(name):
        k32.GlobalDeleteAtom(k32.GlobalFindAtomA(name))
    print('atom', name.decode(), k32.GlobalAddAtomA(name), flush=True)

p = subprocess.Popen([EXE], cwd=CLIENT)
print('game pid', p.pid, flush=True)
while True:
    time.sleep(5)
