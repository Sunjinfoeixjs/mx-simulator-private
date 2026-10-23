# -*- coding: utf-8 -*-
"""临时：常驻拉起 patch/mx.bin（免提权副本），保持进程树存活，供后续点击/截图。"""
import ctypes
import subprocess
import time

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.GlobalAddAtomA.restype = ctypes.c_ushort
k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalFindAtomA.restype = ctypes.c_ushort
k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]

for name in (b'mxpatch_run', b'mxpatch_shadow'):
    while k32.GlobalFindAtomA(name):
        k32.GlobalDeleteAtom(k32.GlobalFindAtomA(name))
    print('atom', name, k32.GlobalAddAtomA(name), flush=True)

p = subprocess.Popen([r'D:\mxsj2013\client\patch\mx.bin'], cwd=r'D:\mxsj2013\client')
print('pid', p.pid, flush=True)
for i in range(120):
    time.sleep(5)
    print('tick', i, 'poll', p.poll(), flush=True)
