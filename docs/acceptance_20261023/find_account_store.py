# -*- coding: utf-8 -*-
"""查找客户端把「上次账号」存在哪里：注册表 + 客户端目录小文件。"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import winreg

needle = 'abcd123'
base = r'D:\mxsj2013\client'

print('=== registry HKCU ===')
def walk(root, path, depth=0):
    if depth > 3:
        return
    try:
        k = winreg.OpenKey(root, path)
    except OSError:
        return
    i = 0
    while True:
        try:
            name, val, typ = winreg.EnumValue(k, i)
        except OSError:
            break
        i += 1
        s = str(val)
        if needle in s.lower() or 'duoyi' in name.lower() or 'mxsj' in name.lower():
            print('%s\\%s [%s] = %r' % (path, name, typ, s[:80]))
    j = 0
    while True:
        try:
            sub = winreg.EnumKey(k, j)
        except OSError:
            break
        j += 1
        if any(t in sub.lower() for t in ('duoyi', 'mxsj', 'mx', '梦想', 'mengxiang')):
            print('key', path + '\\' + sub)
            walk(root, path + '\\' + sub, depth + 1)


for root_name, root in (('HKCU', winreg.HKEY_CURRENT_USER), ('HKLM', winreg.HKEY_LOCAL_MACHINE)):
    try:
        k = winreg.OpenKey(root, r'Software')
    except OSError:
        continue
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(k, i)
        except OSError:
            break
        i += 1
        low = sub.lower()
        if any(t in low for t in ('duoyi', 'mxsj', 'mx', '梦想', 'mengxiang')):
            print('root key', root_name, sub)
            walk(root, r'Software\%s' % sub)

print('=== client small files containing needle ===')
for dirpath, dirnames, filenames in os.walk(base):
    if dirpath.count(os.sep) - base.count(os.sep) > 2:
        dirnames[:] = []
        continue
    for fn in filenames:
        p = os.path.join(dirpath, fn)
        try:
            if os.path.getsize(p) > 2_000_000:
                continue
            data = open(p, 'rb').read()
        except OSError:
            continue
        if needle.encode() in data.lower():
            print('hit', p, len(data))
