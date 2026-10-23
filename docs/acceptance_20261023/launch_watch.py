# -*- coding: utf-8 -*-
"""拉起客户端并逐秒观察：窗口、类名、标题、进程存活、服务端连接。"""
import io
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

CLIENT = r'D:\mxsj2013\client'
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'server_console.log')

p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
print('launcher pid', p.pid, flush=True)

last_n = 0
for i in range(70):
    time.sleep(1)
    wins = [w for w in wt.enum_windows()
            if w['class'] in ('Game', '#32770') or '梦想' in w['title']]
    txt = io.open(LOG, encoding='utf-8', errors='replace').read().splitlines()
    new = txt[last_n:]
    last_n = len(txt)
    sig = [(w['class'], w['title'][:22], w['rect']) for w in wins]
    print('t=%02ds alive=%s wins=%s' % (i + 1, p.poll(), sig), flush=True)
    for ln in new:
        if any(k in ln for k in ('接入', 'Hello', 'PropInit', '进世界', '断开', '账号', '拒绝')):
            print('   |', ln, flush=True)
