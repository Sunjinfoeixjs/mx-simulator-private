# -*- coding: utf-8 -*-
"""用 Win32 API 关闭「脚本错误」模态框：枚举可见 #32770 对话框 → 点含「否」的按钮（BM_CLICK）。

比合成键盘/鼠标可靠（模态 IE 宿主对 SendKeys 不响应）。
"""
import ctypes
import ctypes.wintypes as wt
import sys

u32 = ctypes.windll.user32
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def text(h):
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(h, buf, 512)
    return buf.value


def cls(h):
    buf = ctypes.create_unicode_buffer(512)
    u32.GetClassNameW(h, buf, 512)
    return buf.value


want = sys.argv[1] if len(sys.argv) > 1 else '否'
found_dlgs = []


def on_top(h, l):
    if u32.IsWindowVisible(h):
        c = cls(h)
        t = text(h)
        if c == '#32770':
            found_dlgs.append((h, t))
    return True


u32.EnumWindows(ENUMPROC(on_top), 0)
print('对话框候选 = %s' % [(t, hex(h)) for h, t in found_dlgs])

clicked = 0
for h, t in found_dlgs:
    btns = []

    def on_child(c, l):
        if cls(c).startswith('Button'):
            btns.append((c, text(c)))
        return True

    u32.EnumChildWindows(h, ENUMPROC(on_child), 0)
    print('  「%s」按钮 = %s' % (t, [b[1] for b in btns]))
    for c, bt in btns:
        if want in bt.replace('&', ''):
            u32.SendMessageW(c, 0x00F5, 0, 0)      # BM_CLICK
            clicked += 1
            print('  BM_CLICK -> %s' % bt)

if not found_dlgs:
    print('未找到 #32770 对话框')
print('clicked=%d' % clicked)
