# -*- coding: utf-8 -*-
"""列出当前可见窗口（过滤输入法噪声）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

for w in wt.enum_windows():
    if w['class'] in ('IME', 'MSCTFIME UI', 'SoPY_Hint', 'GDI+ Hook Window Class',
                      'Windows.UI.Core.CoreWindow', 'Xaml_WindowedPopupClass',
                      'Default IME'):
        continue
    print('%-6d %-28s %-40r %s' % (w['hwnd'], w['class'], w['title'], w['rect']))
