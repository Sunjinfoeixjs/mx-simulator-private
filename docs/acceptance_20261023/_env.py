# -*- coding: utf-8 -*-
"""环境探测：UAC / 管理员组 / 端口占用 / 客户端清单。"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess

print('--- token ---')
print('IsUserAnAdmin', bool(ctypes.windll.shell32.IsUserAnAdmin()))

try:
    out = subprocess.run(['whoami', '/groups'], capture_output=True, text=True,
                         encoding='mbcs', errors='replace')
    for ln in out.stdout.splitlines():
        if 'Administrators' in ln or 'High' in ln or 'Mandatory' in ln:
            print(ln.strip())
except Exception as exc:  # noqa
    print('whoami failed', exc)

print('--- registry UAC ---')
try:
    import winreg
    k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                       r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System')
    for name in ('EnableLUA', 'ConsentPromptBehaviorAdmin', 'PromptOnSecureDesktop'):
        try:
            print(name, winreg.QueryValueEx(k, name)[0])
        except OSError:
            print(name, 'absent')
except Exception as exc:  # noqa
    print('reg failed', exc)

print('--- ports ---')
for port in (12239, 12240, 8080, 3134):
    r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                       encoding='mbcs', errors='replace')
    hits = [ln.strip() for ln in r.stdout.splitlines()
            if ':%d ' % port in ln and 'LISTENING' in ln]
    print(port, hits or 'free')


def manifest(path):
    """粗略提取 exe 内嵌 manifest 的 requestedExecutionLevel。"""
    data = open(path, 'rb').read()
    for key in (b'requireAdministrator', b'asInvoker', b'highestAvailable'):
        if key in data:
            return key.decode()
    return 'n/a'


for exe in ('mx.exe', 'mx_noadmin.exe', 'patch\\mx.bin'):
    p = os.path.join(r'D:\mxsj2013\client', exe)
    print(exe, os.path.isfile(p), manifest(p) if os.path.isfile(p) else '')
