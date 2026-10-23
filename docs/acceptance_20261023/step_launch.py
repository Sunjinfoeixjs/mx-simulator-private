# -*- coding: utf-8 -*-
"""启动客户端并连拍前 25 秒，用来看清登录界面是否出现。"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

CLIENT = r'D:\mxsj2013\client'
p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
print('client pid', p.pid, 'launcher_alive', p.poll(), flush=True)

t0 = time.time()
seen = None
while time.time() - t0 < 25:
    w = wt.find_window(class_eq='Game')
    if w:
        seen = w
        break
    time.sleep(0.5)
if not seen:
    print('Game 窗口未出现')
    raise SystemExit(1)
print('Game 窗口', seen['rect'], '在 %.1fs 出现' % (time.time() - t0))

for i in range(6):
    wt.bring(seen)
    wt.shot(wt.find_window(class_eq='Game'), 'seq_%02d' % i)
    time.sleep(1.5)
