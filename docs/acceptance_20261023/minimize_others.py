# -*- coding: utf-8 -*-
"""最小化除客户端以外的可见大窗口，保证真实鼠标点击能落到客户端上。

python minimize_others.py [restore]
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

u32 = ctypes.windll.user32
SW_MINIMIZE = 6
SW_RESTORE = 9
restore = 'restore' in sys.argv

skip_classes = {'Progman', 'Shell_TrayWnd', 'WorkerW', 'Game', 'SynTrackCursorWindowClass',
                'Windows.UI.Core.CoreWindow', 'XamlExplorerHostIslandWindow',
                'CodexComputerUseSwiftOverlay', 'EdgeUiInputTopWndClass', 'IME',
                'MSCTFIME UI', 'SoPY_Hint', 'GDI+ Hook Window Class'}

for w in wt.enum_windows():
    if w['class'] in skip_classes:
        continue
    title = w['title']
    if not title:
        continue
    x, y, ww, hh = w['rect']
    if restore:
        u32.ShowWindow(w['hwnd'], SW_RESTORE)
        print('restore', title[:30])
        continue
    if ww > 400 and hh > 300:
        u32.ShowWindow(w['hwnd'], SW_MINIMIZE)
        print('minimize', title[:30], w['rect'])
