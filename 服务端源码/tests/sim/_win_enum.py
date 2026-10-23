# -*- coding: utf-8 -*-
"""诊断用：枚举某进程的全部可见顶层窗口及子控件文本（纯 ctypes）。

用于真机联调排障——把客户端模态框（「错误」/「脚本错误」等）里的文字读出来。
用法:
    python _win_enum.py            # 列出所有可见顶层窗口（含 pid/class）
    python _win_enum.py 1872       # 只看指定 pid，并展开子控件树（深度 3）
"""
import ctypes
import ctypes.wintypes as wt
import sys

u32 = ctypes.windll.user32
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GW_CHILD, GW_HWNDNEXT = 5, 2

u32.GetWindow.restype = wt.HWND
u32.GetWindow.argtypes = [wt.HWND, wt.UINT]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

want_pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def text(h):
    buf = ctypes.create_unicode_buffer(1024)
    u32.GetWindowTextW(h, buf, 1024)
    return buf.value


def cls(h):
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, buf, 256)
    return buf.value


def wpid(h):
    p = wt.DWORD()
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


tops = []


def on_top(h, l):
    if u32.IsWindowVisible(h) and (not want_pid or wpid(h) == want_pid):
        tops.append(h)
    return True


u32.EnumWindows(ENUMPROC(on_top), 0)
print('可见顶层窗口 %d 个%s'
      % (len(tops), ('（仅 pid=%d）' % want_pid) if want_pid else ''))

for h in tops:
    print('=' * 64)
    print('HWND=%s pid=%d class=%r title=%r'
          % (hex(h), wpid(h), cls(h), text(h)))
    if not want_pid:
        continue

    limit = [120]                          # 防止控件树过大刷屏

    def walk(parent, depth=0, maxdepth=3):
        if depth > maxdepth or limit[0] <= 0:
            return
        c = u32.GetWindow(parent, GW_CHILD)
        while c and limit[0] > 0:
            limit[0] -= 1
            t, k = text(c), cls(c)
            vis = '' if u32.IsWindowVisible(c) else '(隐)'
            if t or k not in ('Internet Explorer_Server', 'Shell DocObject View'):
                print('%s[%s]%s %r' % ('  ' * (depth + 1), k, vis, t))
            walk(c, depth + 1, maxdepth)
            c = u32.GetWindow(c, GW_HWNDNEXT)

    walk(h)
