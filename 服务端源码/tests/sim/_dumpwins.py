# -*- coding: utf-8 -*-
"""枚举所有可见顶层窗口（类名/标题/进程），并深入子窗口找「脚本错误」框与其按钮。"""
import ctypes
import ctypes.wintypes as wt

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


def pid_of(h):
    p = wt.DWORD()
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


rows = []


def walk(h, depth, lines):
    if depth > 4:
        return
    cl = cls(h)
    tx = text(h)
    vis = bool(u32.IsWindowVisible(h))
    if tx or cl.startswith('#'):
        lines.append('%s[%s] %-22s %s (pid=%d vis=%s)'
                     % ('  ' * depth, hex(h), cl, tx[:40], pid_of(h), int(vis)))
    kids = []

    def on_child(c, l):
        kids.append(c)
        return True

    u32.EnumChildWindows(h, ENUMPROC(on_child), 0)
    for c in kids:
        walk(c, depth + 1, lines)


tops = []


def on_top(h, l):
    if u32.IsWindowVisible(h):
        tops.append(h)
    return True


u32.EnumWindows(ENUMPROC(on_top), 0)
lines = []
for h in tops:
    walk(h, 0, lines)

out = '\n'.join(lines)
open(r'C:\Users\Administrator\MX\_wins.txt', 'w', encoding='utf-8').write(out)
uncached = [ln for ln in lines]
print('\n'.join(uncached[:120]))
print('... 共 %d 行 -> C:\\Users\\Administrator\\MX\\_wins.txt' % len(lines))
