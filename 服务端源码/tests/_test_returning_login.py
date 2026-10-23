# -*- coding: utf-8 -*-
"""老角色重新登录 —— 回归测试（2026-10-22 真机踩坑）。

问题现场
  客户端停在「正在登录中…」，服务端日志只有「登录: abcd123」，没有
  「已回 AccountInfo(1)+RoleInfo」。根因：`_apply_save()` 载入存档后
  **没有置 have_role**，op1 走 `else: AccountInfo(0)`（=无角色），客户端
  永远等不到角色列表；旧档还缺 hero_name，角色列表会是空名。

本测试锁两件事：
  ① 有存档的账号登录 → 必发 AccountInfo(1) + RoleInfo，且 have_role=True；
  ② hero_name 持久化：重建会话后名字不丢（旧档没有该字段时回落账号名）。
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m  # noqa: E402

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, f):
        self.sent.append(f)


def make(tmp, name):
    return m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, name + '_logs'),
                      savedir=os.path.join(tmp, name + '_save'))


def login_body(account):
    # op1: 前 4B 头 + 32B 账号名 + 其余填充（handler 只读 body[4:36]）
    return b'\x00\x0a\x2b\x00' + account.encode('gbk').ljust(32, b'\x00') + b'\x00' * 58


def main():
    tmp = tempfile.mkdtemp(prefix='m4_login_')
    try:
        # ① 建角 → 落档
        s1 = make(tmp, 'a')
        c1 = FakeConn()
        s1._dispatch(c1, 3, ('测试侠'.encode('gbk')).ljust(20, b'\x00')
                     + bytes([1, 0, 0, 1]))
        check(s1.have_role, '建角后 have_role=True')
        s1._persist()
        sv = m.load_role(s1.savepath)
        check(sv.get('hero_name') == '测试侠', '存档写入 hero_name（%s）' % sv.get('hero_name'))
        check(int(sv.get('hero_id')) == s1.hero_id, '存档写入 hero_id')

        # ② 新会话（模拟重启）用同一存档登录
        s2 = make(tmp, 'a')
        c2 = FakeConn()
        s2._dispatch(c2, 1, login_body('abcd123'))
        ops = [(f[1], f[2:]) for f in c2.sent]
        acc = [b for (op, b) in ops if op == m.OP_ACCOUNT_INFO]
        role = [b for (op, b) in ops if op == m.OP_ROLE_INFO]
        check(s2.have_role, '老角色登录后 have_role=True')
        check(acc and acc[0][:1] == b'\x01', '回 AccountInfo(1)（有角色）')
        check(bool(role), '回 RoleInfo(3)')
        check(s2.hero_name == '测试侠', '角色名从存档恢复（%s）' % s2.hero_name)
        check(s2.hero_id == s1.hero_id, '角色 id 从存档恢复（%d）' % s2.hero_id)

        # ③ 旧档兼容：手动删掉 hero_name/hero_id，登录应回落账号名且不崩
        import json
        s2._persist()                       # 先让 role_abcd123.json 真正落盘
        d = json.load(open(s2.savepath, encoding='utf-8'))
        d.pop('hero_name', None)
        d.pop('hero_id', None)
        json.dump(d, open(s2.savepath, 'w', encoding='utf-8'), ensure_ascii=False)
        s3 = make(tmp, 'a')
        c3 = FakeConn()
        s3._dispatch(c3, 1, login_body('abcd123'))
        check(s3.have_role and s3.hero_name == 'abcd123',
              '旧档无名字 → 回落账号名（%s）' % s3.hero_name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
