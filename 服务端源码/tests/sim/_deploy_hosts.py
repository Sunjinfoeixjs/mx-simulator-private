# -*- coding: utf-8 -*-
"""
部署/移除 hosts 劫持条目: 127.0.0.1 mxupdate.duoyi.com
用法:
  python _deploy_hosts.py add      # 提权追加（自动 runas 自提权）
  python _deploy_hosts.py remove   # 提权移除
  python _deploy_hosts.py check    # 只读检查（无需提权）
"""
import ctypes
import os
import sys
import time

HOSTS = r'C:\Windows\System32\drivers\etc\hosts'
ENTRY = '127.0.0.1 mxupdate.duoyi.com'
LOGF = r'C:\Users\Administrator\MX\服务端源码\sim\_deploy_hosts.log'


def logp(s):
    try:
        with open(LOGF, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), s))
            f.flush()
    except OSError:
        pass


def read_hosts():
    with open(HOSTS, 'r', encoding='latin1', newline='') as f:
        return f.read()


def check():
    txt = read_hosts()
    ok = any(ENTRY.split()[1] in ln and ln.strip().startswith('127.0.0.1')
             for ln in txt.splitlines())
    return ok, txt


def run_go():
    # 已提权：执行实际写入
    action = sys.argv[2] if len(sys.argv) > 2 else 'add'
    txt = read_hosts()
    logp('go action=%s admin=%s hosts_len=%d' % (
        action, bool(ctypes.windll.shell32.IsUserAnAdmin()), len(txt)))
    if action == 'add':
        if ENTRY in txt:
            logp('已存在，不重复添加')
        else:
            bak = HOSTS + '.mx_bak'
            if not os.path.exists(bak):
                with open(bak, 'w', encoding='latin1', newline='') as f:
                    f.write(txt)
                logp('已备份 -> %s' % bak)
            if txt and not txt.endswith('\n'):
                txt += '\r\n'
            txt += ENTRY + '\r\n'
            with open(HOSTS, 'w', encoding='latin1', newline='') as f:
                f.write(txt)
            logp('已追加: %s' % ENTRY)
    elif action == 'remove':
        lines = [ln for ln in txt.splitlines(True)
                 if not (ENTRY in ln)]
        with open(HOSTS, 'w', encoding='latin1', newline='') as f:
            f.writelines(lines)
        logp('已移除条目')
    ok, txt2 = check()
    logp('验证: enrty_present=%s' % ok)
    logp('hosts 尾部: %r' % txt2[-200:])


def elevate_and_go(action):
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_ulong), ('fMask', ctypes.c_ulong),
                    ('hwnd', ctypes.c_void_p), ('lpVerb', ctypes.c_wchar_p),
                    ('lpFile', ctypes.c_wchar_p), ('lpParameters', ctypes.c_wchar_p),
                    ('lpDirectory', ctypes.c_wchar_p), ('nShow', ctypes.c_int),
                    ('hInstApp', ctypes.c_void_p), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', ctypes.c_wchar_p), ('hkeyClass', ctypes.c_void_p),
                    ('dwHotKey', ctypes.c_ulong), ('hIcon', ctypes.c_void_p),
                    ('hProcess', ctypes.c_void_p)]

    pythonw = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe'
    sei = SHELLEXECUTEINFOW()
    ctypes.memset(ctypes.byref(sei), 0, ctypes.sizeof(sei))
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x40
    sei.lpVerb = 'runas'
    sei.lpFile = pythonw
    sei.lpParameters = '"%s" go %s' % (os.path.abspath(__file__), action)
    sei.lpDirectory = os.path.dirname(os.path.abspath(__file__))
    sei.nShow = 0
    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    print('elevate request ok=%s err=%d' % (bool(ok), ctypes.get_last_error()))


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if mode == 'go':
        run_go()
    elif mode == 'check':
        ok, txt = check()
        print('hosts 劫持条目存在:', ok)
        print('hosts 内容:', repr(txt[:400]))
    else:
        elevate_and_go(mode)
