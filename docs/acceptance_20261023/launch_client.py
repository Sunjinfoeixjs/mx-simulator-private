# -*- coding: utf-8 -*-
"""拉起原版客户端（asInvoker 副本，便于从非提权会话发送输入）并导出窗口。"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt
import time

CLIENT = r'D:\mxsj2013\client'
exe = sys.argv[1] if len(sys.argv) > 1 else 'mx_noadmin.exe'
path = os.path.join(CLIENT, exe)
print('launch', path)
p = subprocess.Popen([path], cwd=CLIENT)
print('pid', p.pid, flush=True)

for i in range(12):
    time.sleep(1)
    wins = [w for w in wt.enum_windows() if w['visible']
            and ('梦想' in w['title'] or w['class'] in ('Game', '#32770'))]
    names = [(w['class'], w['title'], w['rect']) for w in wins]
    print('t=%ds alive=%s' % (i + 1, p.poll()), flush=True)
    for n in names:
        print('   ', n, flush=True)
