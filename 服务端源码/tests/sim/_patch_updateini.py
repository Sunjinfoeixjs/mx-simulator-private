# -*- coding: utf-8 -*-
"""把客户端 update.ini 的 HTML 页地址指向本地劫持服（消除启动器脚本错误模态框）。

背景：启动器内嵌 IE 加载 MiniPage（http://mx.duoyi.com/m_index.shtm）等官方 HTML 页，
这些页引用已失效的 http://image.duoyi.com/js/jquery.min.js → 弹「脚本错误」模态框
（对 SendKeys/WM_CLOSE 均不响应）。改指本地 8080（回空白 HTML）后不再触发。
"""
import io
import os
import shutil

CLIENT = r'D:\mxsj2013\client'
INI = os.path.join(CLIENT, 'update.ini')
BAK = os.path.join(CLIENT, 'update.ini.bak_before_localhtml')
LOCAL = 'http://127.0.0.1:8080/blank'

PAGE_KEYS = ('MiniPage=', 'DownPage=', 'GuidePage=', 'OfficialPage=',
             'OfficalPage=', 'ForumPage=', 'GameInfoPage=')

if not os.path.isfile(BAK):
    shutil.copy2(INI, BAK)
    print('[备份] %s -> %s' % (INI, BAK))
else:
    print('[备份] 已存在（保留首次备份）: %s' % BAK)

raw = open(INI, 'rb').read()
text = raw.decode('gbk')
lines = text.replace('\r\n', '\n').split('\n')
out = []
changed = []
for ln in lines:
    if any(ln.startswith(k) for k in PAGE_KEYS):
        key = ln.split('=')[0]
        if ln.split('=', 1)[1].strip() != LOCAL:
            changed.append('%s  %s -> %s' % (key, ln.split('=', 1)[1].strip(), LOCAL))
            ln = '%s=%s' % (key, LOCAL)
    out.append(ln)

open(INI, 'w', encoding='gbk', newline='').write('\r\n'.join(out))
print('[改动] %d 处：' % len(changed))
for c in changed:
    print('   ' + c)
print('[完成] 还原: 用 %s 覆盖回 update.ini' % BAK)
