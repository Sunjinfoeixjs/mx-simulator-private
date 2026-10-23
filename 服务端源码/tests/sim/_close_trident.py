# -*- coding: utf-8 -*-
"""关闭 Internet Explorer_TridentDlgFrame("脚本错误")：WM_CLOSE → 复查 → ESC 兜底。"""
import ctypes
import ctypes.wintypes as wt
import time

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


def find():
    out = []

    def walk(h, depth=0):
        if depth > 3:
            return
        if cls(h) == 'Internet Explorer_TridentDlgFrame' and u32.IsWindowVisible(h):
            out.append(h)
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
    return out


count = 0
for round_ in range(4):
    dlgs = find()
    if not dlgs:
        print('round %d: 无对话框' % round_)
        break
    for dlg in dlgs:
        print('round %d: 关闭 %s 「%s」' % (round_, hex(dlg), text(dlg)))
        u32.SendMessageW(dlg, 0x0010, 0, 0)       # WM_CLOSE
        count += 1
    time.sleep(1.0)

# 仍存在则对 IE 子窗口发 ESC
left = find()
for dlg in left:
    kids = []

    def on_child(c, l):
        kids.append(c)
        return True

    u32.EnumChildWindows(dlg, ENUMPROC(on_child), 0)
    for c in kids:
        if cls(c) == 'Internet Explorer_Server':
            u32.PostMessageW(c, 0x0100, 0x1B, 0)  # WM_KEYDOWN VK_ESCAPE
            u32.PostMessageW(c, 0x0101, 0x1B, 0)  # WM_KEYUP
            print('ESC -> %s' % hex(c))
print('剩余对话框 = %s ; WM_CLOSE 次数=%d' % ([hex(x) for x in find()], count))
