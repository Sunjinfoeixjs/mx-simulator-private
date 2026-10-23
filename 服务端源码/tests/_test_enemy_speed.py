# -*- coding: utf-8 -*-
"""敌方出手速度按造型（shape）配置 —— 回归测试。

真值依据（137 场真机录像，见 `协议资料/报告/数值与协议真值审计_20261018.md` §8.1）：
  · shape 2130（野狼）：慢于主角(100)与宠物(49) → 主角 → 宠物 → 野狼
  · shape 1（梦想企鹅等）：6 只时恰好 3 只快于主角 → 敌1,2,3 → 主角 → 敌4,5,6
  · 未登记造型回落 ENEMY_SPEED_BASE

用法：python _test_enemy_speed.py
"""
from __future__ import print_function

import os
import shutil
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


def make_server(tmp, enemies):
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=enemies,
                     logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    srv._arm_cmd_timer = lambda: None      # 测试不启计时器，避免 Timer 线程拖住进程
    return srv


def run_case(tmp, name, shape, enemies, expect_speeds, expect_order, by_shape=None):
    old_shape, old_tbl = m.ENEMY_SHAPE, m.ENEMY_SPEED_BY_SHAPE
    try:
        m.ENEMY_SHAPE = shape
        if by_shape is not None:
            m.ENEMY_SPEED_BY_SHAPE = by_shape
        srv = make_server(tmp, enemies)
        srv._start_war(FakeConn())
        check(srv.enemy_speed == expect_speeds,
              '%s：速度表 %s' % (name, srv.enemy_speed))
        check(srv._speed_order() == expect_order,
              '%s：出手序 %s（期望 %s）' % (name, srv._speed_order(), expect_order))
    finally:
        m.ENEMY_SHAPE, m.ENEMY_SPEED_BY_SHAPE = old_shape, old_tbl


def main():
    tmp = tempfile.mkdtemp(prefix='m4_enemy_speed_')
    try:
        # ① 野狼：主角 → 宠物 → 野狼（M7 录像）
        run_case(tmp, 'shape 2130 单狼', 2130, 1,
                 {1: 40, }, [m.HERO_WAR_ID, m.PET_WAR_ID, 1])

        # ② 企鹅 ×6：115/110/105 在主角前，100(同速,主角先手)/95/90 在后
        run_case(tmp, 'shape 1 六只', 1, 6,
                 {1: 115, 2: 110, 3: 105, 4: 100, 5: 95, 6: 90},
                 [1, 2, 3, m.HERO_WAR_ID, 4, 5, 6, m.PET_WAR_ID])

        # ③ 未登记造型：回落 ENEMY_SPEED_BASE
        run_case(tmp, '未登记造型 9999', 9999, 2,
                 {1: m.ENEMY_SPEED_BASE, 2: m.ENEMY_SPEED_BASE - m.ENEMY_SPEED_STEP},
                 [m.HERO_WAR_ID, m.PET_WAR_ID, 1, 2])

        # ④ 大阵容：野狼 ×12 递减到 0 以下 → 钳到下限 1（录像最大 6，超出无真值）
        run_case(tmp, 'shape 2130 ×12 下限钳制', 2130, 12,
                 {1: 40, 2: 35, 3: 30, 4: 25, 5: 20, 6: 15, 7: 10, 8: 5,
                  9: 1, 10: 1, 11: 1, 12: 1},
                 [m.HERO_WAR_ID, m.PET_WAR_ID,
                  1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])

        # ⑤ 配置覆盖：新表替换默认表
        run_case(tmp, '配置覆盖 shape 1 → 60', 1, 2,
                 {1: 60, 2: 55}, [m.HERO_WAR_ID, 1, 2, m.PET_WAR_ID],
                 by_shape={1: 60})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
