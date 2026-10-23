# -*- coding: utf-8 -*-
"""启动客户端并把「错误」对话框的文本与截图抓下来（诊断为什么起不来）。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

CLIENT = r'D:\mxsj2013\client'
u = ui.u32


def dump(h, depth=0):
    n = ctypes.create_unicode_buffer(1024)
    cls = ctypes.create_unicode_buffer(128)
    u.GetWindowTextW(h, n, 1024)
    u.GetClassNameW(h, cls, 128)
    print('  ' * depth + '%s %r' % (cls.value, n.value), flush=True)
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(c, lp):
        kids.append(c)
        return True

    u.EnumChildWindows(h, cb, 0)
    for c in kids:
        dump(c, depth + 1)


p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
print('pid', p.pid, flush=True)
time.sleep(6)
for w in ui.windows():
    if w['class'] == '#32770' or '错误' in w['title'] or w['class'] == 'Game':
        print('WIN', w['class'], repr(w['title']), w['rect'], flush=True)
        if w['class'] == '#32770':
            dump(w['hwnd'])
            ui.u32.SetForegroundWindow(w['hwnd'])
            time.sleep(0.3)
            from PIL import ImageGrab
            x, y, ww, hh = w['rect']
            path = os.path.join(ui.SHOT_DIR, 'X_err_dialog.png')
            ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(path)
            print('SHOT', path, flush=True)
print('alive', p.poll(), flush=True)
