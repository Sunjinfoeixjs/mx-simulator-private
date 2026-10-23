# -*- coding: utf-8 -*-
"""定位 Internet Explorer_TridentDlgFrame("脚本错误") 并 BM_CLICK 其「否」按钮。"""
import ctypes
import ctypes.wintypes as wt
import sys

u32 = ctypes.windll.user32
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def text(h):
    b = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(h, b, 512)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(512)
    u32.GetClassNameW(h, b, 512)
    return b.value


targets = []


def walk(h, depth=0):
    if depth > 3:
        return
    if cls(h) == 'Internet Explorer_TridentDlgFrame' and u32.IsWindowVisible(h):
        targets.append(h)
    kids = []

    def on_child(c, l):
        kids.append(c)
        return True

    u32.EnumChildWindows(h, ENUMPROC(on_child), 0)
    for c in kids:
        walk(c, depth + 1)


tops = []


def on_top(h, l):
    if u32.IsWindowVisible(h):
        tops.append(h)
    return True


u32.EnumWindows(ENUMPROC(on_top), 0)
for h in tops:
    walk(h)

print('找到 TridentDlgFrame = %s' % [hex(t) for t in targets])
want = sys.argv[1] if len(sys.argv) > 1 else '否'
clicked = 0
for dlg in targets:
    print('「%s」 子控件：' % text(dlg))
    kids = []

    def on_child(c, l):
        kids.append(c)
        return True

    u32.EnumChildWindows(dlg, ENUMPROC(on_child), 0)
    for c in kids:
        cl, tx = cls(c), text(c)
        print('   %s %-24s %r' % (hex(c), cl, tx))
        if want in tx.replace('&', ''):
            u32.SendMessageW(c, 0x00F5, 0, 0)      # BM_CLICK
            clicked += 1
            print('      -> BM_CLICK')
print('clicked=%d' % clicked)
