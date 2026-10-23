# -*- coding: utf-8 -*-
"""判断桌面输入是否被其它程序注入：观察鼠标位置/前台窗口/遮罩窗。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wtool

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('dwTime', wt.DWORD)]


def last_input_age_ms():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    u32.GetLastInputInfo(ctypes.byref(li))
    return k32.GetTickCount() - li.dwTime


for w in wtool.enum_windows(visible_only=False):
    if 'Codex' in w['class'] or 'Codex' in w['title'] or 'ChatGPT' in w['title']:
        print('overlay', w)

for i in range(10):
    pt = wt.POINT()
    u32.GetCursorPos(ctypes.byref(pt))
    fg = u32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(fg, buf, 256)
    print('t=%d cursor=(%d,%d) lastinput=%dms fg=0x%X %r'
          % (i, pt.x, pt.y, last_input_age_ms(), fg, buf.value))
    time.sleep(1)
