# -*- coding: utf-8 -*-
"""以「脱离作业对象」的方式拉起长跑进程，避免会话中断把它们一起带走。

用法：
  python spawn_detached.py server            # 起 boot.py（服务端 + 本地 HTTP）
  python spawn_detached.py game              # 设原子后起 patch/mx.bin
  python spawn_detached.py soak [秒数]        # 起 soak.py（默认 2000s）

实现：CREATE_DETACHED_PROCESS(0x8) + CREATE_NEW_PROCESS_GROUP(0x200)，
子进程不继承控制台，输出由各自脚本写文件（server.log / soak_*.csv）。
"""
import ctypes
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = r'D:\mxsj2013\client'
FLAGS = 0x00000008 | 0x00000200


def atoms():
    k32 = ctypes.WinDLL('kernel32')
    k32.GlobalAddAtomA.restype = ctypes.c_ushort
    k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalFindAtomA.restype = ctypes.c_ushort
    k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]
    for n in (b'mxpatch_run', b'mxpatch_shadow'):
        while k32.GlobalFindAtomA(n):
            k32.GlobalDeleteAtom(k32.GlobalFindAtomA(n))
        k32.GlobalAddAtomA(n)


def spawn(cmd, cwd, env=None):
    p = subprocess.Popen(cmd, cwd=cwd, creationflags=FLAGS,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env)
    print('spawned pid=%d %s' % (p.pid, ' '.join(cmd)), flush=True)
    return p.pid


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if what == 'server':
        spawn([sys.executable, '-u', 'boot.py'], HERE)
    elif what == 'game':
        atoms()
        spawn([os.path.join(CLIENT, 'patch', 'mx.bin')], CLIENT)
    elif what == 'soak':
        sec = sys.argv[2] if len(sys.argv) > 2 else '2000'
        env = dict(os.environ, SOAK_SEC=sec)
        spawn([sys.executable, '-u', 'soak.py'], HERE, env)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
