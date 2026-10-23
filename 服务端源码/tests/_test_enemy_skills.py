# -*- coding: utf-8 -*-
"""敌方技能按造型（shape）配置 —— 回归测试。

真值依据（137 场真机录像，见 `协议资料/报告/数值与协议真值审计_20261018.md` §8.2）：
  · shape 1（梦想企鹅）→ 1103 落英缤纷（17 次施法）
  · shape 2130（野狼）  → 415 乱舞冲击（9 次施法）
  · 未登记造型回落 ENEMY_SKILLS

除字段检查外，还做一次**端到端**验证：把技能率置 1.0 走 `_enemy_attack`，
直接解 WarPerform(122) 帧里的 pid，确认不是只改了字段没接线。

用法：python _test_enemy_skills.py
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

    def sendall(self, frame):
        self.sent.append(frame)


def make_server(tmp):
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    srv._arm_cmd_timer = lambda: None
    return srv


def perform_pids(conn):
    """从已发送帧里取出所有 WarPerform(122) 的 pid。"""
    out = []
    for fr in conn.sent:
        if len(fr) >= 4 and fr[1] == m.OP_WAR_PERFORM:
            out.append(struct.unpack_from('<H', fr, 3)[0])   # [len][op][id][pid:2]
    return out


def run_case(tmp, name, shape, expect_skills, by_shape=None, expect_pid=None):
    old_shape, old_tbl = m.ENEMY_SHAPE, m.ENEMY_SKILLS_BY_SHAPE
    try:
        m.ENEMY_SHAPE = shape
        if by_shape is not None:
            m.ENEMY_SKILLS_BY_SHAPE = by_shape
        srv = make_server(tmp)
        conn = FakeConn()
        srv._start_war(conn)
        check(srv.enemy_skills == expect_skills,
              '%s：技能库 %s' % (name, srv.enemy_skills))
        if expect_pid is not None:
            srv.enemy_skill_chance = 1.0          # 必放技能
            conn.sent = []
            srv._enemy_attack(conn, 1)
            pids = perform_pids(conn)
            check(pids and set(pids) <= set(expect_skills),
                  '%s：WarPerform(122) 实际发出 pid=%s' % (name, pids))
            if pids:
                check(pids[0] == expect_pid,
                      '%s：pid 命中真值 %d' % (name, expect_pid))
    finally:
        m.ENEMY_SHAPE, m.ENEMY_SKILLS_BY_SHAPE = old_shape, old_tbl


def main():
    tmp = tempfile.mkdtemp(prefix='m4_enemy_skills_')
    try:
        run_case(tmp, 'shape 2130 野狼', 2130, [415], expect_pid=415)
        run_case(tmp, 'shape 1 企鹅', 1, [1103], expect_pid=1103)
        run_case(tmp, '未登记造型 9999', 9999, list(m.ENEMY_SKILLS), expect_pid=415)
        run_case(tmp, '配置覆盖 shape 1 → [2201]', 1, [2201],
                 by_shape={1: [2201]}, expect_pid=2201)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
