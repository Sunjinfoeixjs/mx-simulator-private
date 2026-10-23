# -*- coding: utf-8 -*-
"""用客户端 netteam.S2CPlayerTeam 的解析顺序反向校验 team.py 的编码。

解析器逐字照抄自 src/netteam.py（UnpackInt/UnpackString 语义：
小端定长；UnpackString(n) 取 n 字节去尾部 \\x00, n=0 取剩余全部）。
"""
import os
import struct
import sys

SIM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim')
sys.path.insert(0, SIM)
import team  # noqa: E402


class Reader(object):
    def __init__(self, buf):
        self.b = buf
        self.i = 0

    def int(self, n):
        v = int.from_bytes(self.b[self.i:self.i + n], 'little')
        self.i += n
        return v

    def string(self, n):
        if n == 0:
            v = self.b[self.i:]
            self.i = len(self.b)
        else:
            v = self.b[self.i:self.i + n]
            self.i += n
        return v.rstrip(b'\x00').decode('gbk', 'replace')


def s2cteamlist(rd):
    num = rd.int(1)
    out = []
    for _ in range(num):
        t = {}
        t['id'] = rd.int(4)
        t['vip'] = rd.int(1)
        t['class'] = rd.int(1)
        t['grade'] = rd.int(1)
        t['icon'] = rd.int(1)
        t['photo_effect'] = rd.int(1)
        t['photo'] = (rd.int(4), rd.int(4), rd.int(4), rd.int(4))
        t['leave'] = rd.int(1)
        t['name'] = rd.string(20)
        t['point'] = rd.int(2)
        out.append(t)
    lineup = rd.int(1)
    canuse = rd.int(1)
    return out, lineup, canuse


def s2cteaminfo(rd):
    return dict(title=rd.string(20), leader=rd.string(20), min=rd.int(1),
                max=rd.int(1), cls=rd.int(1), message=rd.string(0))


def s2cteamerinfo(rd):
    num = rd.int(1)
    out = []
    for _ in range(num):
        out.append((rd.int(4), rd.string(20), rd.int(1), rd.int(1), rd.int(1)))
    return out


def s2cgps(rd):
    return dict(id=rd.int(4), scene=rd.int(2), x=rd.int(2), y=rd.int(2),
                name=rd.string(0))


def body_of(frame):
    """去掉长度前缀，返回 (op, body)。"""
    if frame[0] == 0xFF:
        ln = struct.unpack('<H', frame[1:3])[0]
        assert ln == len(frame) - 3, '长帧长度不符 %d vs %d' % (ln, len(frame) - 3)
        return frame[3], frame[3:]
    assert frame[0] == len(frame) - 1
    return frame[1], frame[1:]


fails = []


def check(name, cond, extra=''):
    print(('  ok   ' if cond else '  FAIL ') + name + ('' if cond else ' ' + str(extra)))
    if not cond:
        fails.append(name)


def main():
    print('[1] S2CTeamStatus')
    op, b = body_of(team.build_team_status(team.TEAM_LEADER))
    check('op == 82', op == 82, op)
    rd = Reader(b[1:])
    check('sub==1 且 status==2', rd.int(1) == 1 and rd.int(1) == 2)

    print('[2] S2CTeamList（2 人，姓名含中文）')
    members = [
        {'id': 100, 'name': '测试队长', 'class': 1, 'grade': 3, 'icon': 0,
         'leave': 0, 'point': 0},
        {'id': 101, 'name': '阿豆', 'class': 2, 'grade': 2, 'icon': 1,
         'leave': 1, 'point': 5},
    ]
    op, b = body_of(team.build_team_list(members, lineup=1, canuse=1))
    rd = Reader(b[1:])
    check('sub==2', rd.int(1) == 2)
    got, lineup, canuse = s2cteamlist(rd)
    check('人数 == 2', len(got) == 2, len(got))
    check('队长姓名/等级回读', got[0]['name'] == '测试队长' and got[0]['grade'] == 3,
          got[0])
    check('second 成员 leave=1 point=5',
          got[1]['name'] == '阿豆' and got[1]['leave'] == 1 and got[1]['point'] == 5,
          got[1])
    check('lineup/canuse', (lineup, canuse) == (1, 1), (lineup, canuse))
    check('整帧消费干净', rd.i == len(b) - 1, (rd.i, len(b) - 1))

    print('[3] S2CTeamInfo（变长 message 到帧尾）')
    op, b = body_of(team.build_team_info('我的队', '测试队长', 1, 5, 0, '单机队伍'))
    rd = Reader(b[1:])
    check('sub==6', rd.int(1) == 6)
    info = s2cteaminfo(rd)
    check('字段回读', info == dict(title='我的队', leader='测试队长', min=1, max=5,
                                cls=0, message='单机队伍'), info)
    check('整帧消费干净', rd.i == len(b) - 1)

    print('[4] S2CTeamerInfo')
    op, b = body_of(team.build_teamer_info([
        {'id': 100, 'name': '测试队长', 'grade': 3, 'target': 0, 'class': 1},
        {'id': 101, 'name': '阿豆', 'grade': 2, 'target': 1, 'class': 2},
    ]))
    rd = Reader(b[1:])
    check('sub==11', rd.int(1) == 11)
    rows = s2cteamerinfo(rd)
    check('2 行且字段对', rows == [(100, '测试队长', 3, 0, 1),
                                   (101, '阿豆', 2, 1, 2)], rows)
    check('整帧消费干净', rd.i == len(b) - 1)

    print('[5] S2CGPS')
    op, b = body_of(team.build_team_gps(100, 1001, 45, 40, '测试队长'))
    rd = Reader(b[1:])
    check('sub==7', rd.int(1) == 7)
    g = s2cgps(rd)
    check('字段回读', g == dict(id=100, scene=1001, x=45, y=40, name='测试队长'), g)
    check('整帧消费干净', rd.i == len(b) - 1)

    print('[6] TeamState 生命周期')
    st = team.TeamState(100, '测试队长', hero_class=1, hero_grade=3)
    check('初始 NONE', st.status_of(100) == team.TEAM_NONE)
    check('建队返回 True', st.create() is True)
    check('建队后 LEADER', st.status_of(100) == team.TEAM_LEADER)
    check('重复建队幂等', st.create() is False)
    m = st.add_simulated('阿豆', grade=2, cls=2)
    check('加队友成功', m is not None and m['name'] == '阿豆')
    check('成员数 2', len(st.members) == 2)
    check('临时离队', st.reposition(m['id'], 1) is True and st.members[1]['leave'] == 1)
    check('归队', st.reposition(m['id'], 0) is True and st.members[1]['leave'] == 0)
    check('队长不可被移除', st.remove(100) is False)
    check('移除队友', st.remove(m['id']) is True and len(st.members) == 1)
    # 满员
    for i in range(team.TEAM_MAX_MEMBERS - 1):
        st.add_simulated('队友%d' % i)
    check('加满 %d 人' % team.TEAM_MAX_MEMBERS,
          len(st.members) == team.TEAM_MAX_MEMBERS, len(st.members))
    check('满员再加返回 None', st.add_simulated('多余') is None)
    check('解散', st.dismiss() is True and st.status_of(100) == team.TEAM_NONE)

    print('[7] 名单编码：满员也能完整往返')
    st.create()
    for i in range(team.TEAM_MAX_MEMBERS - 1):
        st.add_simulated('队友%d' % i, grade=i + 1)
    op, b = body_of(team.build_team_list(st.list_for_wire()))
    rd = Reader(b[1:])
    rd.int(1)
    got, _l, _c = s2cteamlist(rd)
    check('满员往返人数一致', len(got) == team.TEAM_MAX_MEMBERS, len(got))
    check('整帧消费干净', rd.i == len(b) - 1)

    print()
    if fails:
        print('FAIL %d 项: %s' % (len(fails), fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
