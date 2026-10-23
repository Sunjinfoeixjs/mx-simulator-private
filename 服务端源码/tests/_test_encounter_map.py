# -*- coding: utf-8 -*-
"""场景遇怪表（per_map）—— 回归测试。

真值依据（见 gen_encounter_data.py 头）：
  · 场景/等级/怪物名单 = 《梦想世界2013资料全库》暗雷/明雷场景表
    （叶子猪端游资料站同表交叉核对：https://mx.yzz.cn/call/2010,100673.shtml）
  · 怪物名 -> shape = 客户端 src/data/monsterdata.py
  · 场景名 -> mapid = world_config.json

覆盖三件事：
  ① 数据自洽：43 个场景全部落到真实 mapid；124 条怪物名全部有 shape；
     城镇/室内不在表内（不遇怪）。
  ② 开怪：野外图走 `_start_war` 出的怪必须来自本图场景表，shape 与名字一致。
  ③ 遇怪闸门：城镇走 N 步**不**开战；野外走 N 步开战；per_map=False 时回到全图野狼。

用法：python _test_encounter_map.py
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m            # noqa: E402
import encounter_data as ed      # noqa: E402

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


def make_server(tmp, mapid):
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv.mapid = mapid
    srv.px, srv.py = 100, 100
    # 关掉出口判定：本测试只测遇怪，不测传送
    srv._arrive_xy = (srv.px, srv.py)
    return srv


def war_add_enemies(conn):
    """从已发送帧里解出敌方 WarAdd 的 (name, shape)。

    帧 = [1B N][op][wid(1)][shape(2)][desc(1,空)][bitfield(1)][owner(1)][cls(1)][grade(1)][name...]
    故 name 从下标 10 起（0=N, 1=op, 2=wid, 3..4=shape, 5..9=五个定长字段）。
    """
    out = []
    for fr in conn.sent:
        if len(fr) >= 10 and fr[1] == m.OP_WAR_ADD:
            wid = fr[2]
            if wid >= m.HERO_WAR_ID:      # 主角=100 / 宠物=250 / 队友=200+ 都不算敌方
                continue
            shape = struct.unpack_from('<H', fr, 3)[0]
            name = fr[10:].split(b'\x00')[0]
            out.append((name, shape, fr))
    return out


def test_data():
    print('── ① 数据自洽 ──')
    check(len(ed.MAP_ENCOUNTERS) == 43, '场景数 43（实际 %d）' % len(ed.MAP_ENCOUNTERS))
    bad_map = [mid for mid in ed.MAP_ENCOUNTERS if mid not in m.MAPS]
    check(not bad_map, '全部 mapid 都在 MAPS 里（越界 %s）' % bad_map)
    total = 0
    bad_shape = []
    for mid, e in ed.MAP_ENCOUNTERS.items():
        for name, shape in e['monsters']:
            total += 1
            if not isinstance(shape, int) or shape <= 0 or not name:
                bad_shape.append((mid, name, shape))
    check(total == 124, '怪物条目 124（实际 %d）' % total)
    check(not bad_shape, '每条怪物都有名字与 shape（异常 %s）' % bad_shape)
    check(1001 not in ed.MAP_ENCOUNTERS and 1054 not in ed.MAP_ENCOUNTERS,
          '城镇 永泽城(1001)/明月镇(1054) 不在遇怪表内')
    tx = ed.MAP_ENCOUNTERS[1005]
    check(tx['scene'] == '天水平原', '1005 = 天水平原')
    check({n for n, _s in tx['monsters']} == {'青蛙怪', '小兔子', '小蜜蜂'},
          '天水平原怪物名单 = 青蛙怪/小兔子/小蜜蜂')


def test_pick():
    print('── ② 开怪（按场景表选怪）──')
    tmp = tempfile.mkdtemp(prefix='m4_enc_')
    try:
        srv = make_server(tmp, 1005)
        conn = FakeConn()
        srv._start_war(conn)
        got = war_add_enemies(conn)
        check(len(got) == 1, '天水平原开战敌方单位 1 只（实际 %d）' % len(got))
        if got:
            name, shape, _fr = got[0]
            expect = {n.encode('gbk'): s for n, s in
                      ed.MAP_ENCOUNTERS[1005]['monsters']}
            check(name in expect, '敌方名字来自场景表：%r' % name)
            check(expect.get(name) == shape,
                  'shape 与 monsterdata 一致：%r -> %d' % (name, shape))
        check(srv._enc_scene == '天水平原', '_enc_scene 记录场景名')

        # 城镇：_encounter_def 必须为 None（不遇怪）
        srv_city = make_server(tmp, 1001)
        check(srv_city._encounter_def() is None, '城镇 永泽城 无遇怪定义')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate():
    print('── ③ 遇怪闸门 ──')
    tmp = tempfile.mkdtemp(prefix='m4_enc_gate_')
    try:
        # 城镇走 3 步：每步都到遇怪步数，也不该开战
        city = make_server(tmp, 1001)
        city.encounter_every = 1
        cc = FakeConn()
        for _ in range(3):
            city._dispatch(cc, m.C2S_MAP_MOVE, struct.pack('<HH', city.px, city.py) + b'\x00')
        check(not city.in_war, '城镇走 3 步不遇怪（in_war=%s）' % city.in_war)

        # 野外走 1 步：开战且怪物来自本图
        wild = make_server(tmp, 1005)
        wild.encounter_every = 1
        wc = FakeConn()
        wild._dispatch(wc, m.C2S_MAP_MOVE, struct.pack('<HH', wild.px, wild.py) + b'\x00')
        check(wild.in_war, '天水平原走 1 步遇怪（in_war=%s）' % wild.in_war)
        got = war_add_enemies(wc)
        names = {n for n, _s, _f in got}
        check(names and names <= {n.encode('gbk') for n, _s
                                  in ed.MAP_ENCOUNTERS[1005]['monsters']},
              '遇怪名字在本图名单内：%s' % names)

        # per_map=False：回到全图野狼（旧行为）
        old = m.ENCOUNTER_PER_MAP
        try:
            m.ENCOUNTER_PER_MAP = False
            legacy = make_server(tmp, 1005)
            legacy.encounter_every = 1
            lc = FakeConn()
            legacy._dispatch(lc, m.C2S_MAP_MOVE,
                             struct.pack('<HH', legacy.px, legacy.py) + b'\x00')
            check(legacy.in_war, 'per_map=False 仍会遇怪')
            lname = {n for n, _s, _f in war_add_enemies(lc)}
            check(lname == {m.ENEMY_NAME.encode('gbk')},
                  'per_map=False 回落 ENEMY_NAME=%r（实际 %s）' % (m.ENEMY_NAME, lname))
        finally:
            m.ENCOUNTER_PER_MAP = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_data()
    test_pick()
    test_gate()
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
