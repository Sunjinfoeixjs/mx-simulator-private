# -*- coding: utf-8 -*-
"""组队闭环冒烟：建队 → 名册 → 阵型 → 踢人 → 队伍信息 → 离队，
外加「战斗中建队不落保底」「队友作为己方单位入场」两个契约点。

帧校验用客户端 netteam.S2CPlayerTeam 的解析顺序反解（见 _test_team_protocol）。
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
for p in (SIM, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import m4_server as m            # noqa: E402
import team as teammod           # noqa: E402
from _test_dispatch import make_server, FakeConn, s2c_frames   # noqa: E402
from _test_team_protocol import (Reader, s2cteamlist, s2cteaminfo,   # noqa: E402
                                 s2cteamerinfo, body_of)

fails = []


def check(name, cond, extra=''):
    print(('  ok   ' if cond else '  FAIL ') + name + ('' if cond else ' → %s' % (extra,)))
    if not cond:
        fails.append(name)


def team_body(sub, *args):
    """组队帧体：sub(1) + 参数（小端）。"""
    return struct.pack('<B', sub) + b''.join(args)


def only_subs(frames, op=82):
    """取出 op82 帧并按 sub 分类：(sub, payload_after_sub)。"""
    out = []
    for o, b in frames:
        if o != op:
            continue
        rd = Reader(b)
        sub = rd.int(1)
        out.append((sub, b[1:]))
    return out


def main():
    tmp = tempfile.mkdtemp(prefix='teamflow_')
    srv = make_server(tmp)
    srv.hero_id = 1
    srv.hero_name = '测试队长'
    srv.in_war = False

    print('[1] 建队（C2SMakeTeam(0)）')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_MAKETEAM, struct.pack('<I', 0)))
    frames = s2c_frames(conn.buf)
    subs = [s for s, _ in only_subs(frames)]
    check('回 op82 帧', len(frames) > 0 and all(o == 82 for o, _ in frames),
          [o for o, _ in frames])
    check('含 TeamStatus(1)', teammod.S_STATUS in subs, subs)
    check('含 TeamList(2)', teammod.S_LIST in subs, subs)
    check('含 TeamInfo(6)', teammod.S_TEAMINFO in subs, subs)
    check('含 GPS(7)', teammod.S_GPS in subs, subs)
    check('服务端进入 LEADER 态', srv.team.status_of(1) == teammod.TEAM_LEADER)

    # 状态帧值 = 2
    st_payload = [p for s, p in only_subs(frames) if s == teammod.S_STATUS][0]
    check('TeamStatus 值 == LEADER(2)', Reader(st_payload).int(1) == teammod.TEAM_LEADER)

    # 名册：队长 + 陪同队友
    lst_payload = [p for s, p in only_subs(frames) if s == teammod.S_LIST][0]
    rd = Reader(lst_payload)
    members, lineup, canuse = s2cteamlist(rd)
    check('名册人数 == %d' % len(srv.team.members), len(members) == len(srv.team.members),
          len(members))
    check('第 0 位是队长且姓名正确', members[0]['name'] == '测试队长' and members[0]['id'] == 1,
          members[0])
    check('有陪同队友（>1 人）', len(members) > 1, len(members))
    check('队长 canuse=1（可改阵型）', canuse == 1, canuse)
    check('名册帧整帧消费干净', rd.i == len(lst_payload))

    # 队伍信息
    info_payload = [p for s, p in only_subs(frames) if s == teammod.S_TEAMINFO][0]
    info = s2cteaminfo(Reader(info_payload))
    check('TeamInfo leader == 队长名', info['leader'] == '测试队长', info)
    check('TeamInfo max == 5', info['max'] == teammod.TEAM_MAX_MEMBERS, info)

    print('[2] 改阵型（C2SSetLineup）')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_SETLINEUP, b'\x03'))
    frames = s2c_frames(conn.buf)
    lp = [p for s, p in only_subs(frames) if s == teammod.S_LIST]
    check('改阵型回 TeamList', len(lp) == 1, [s for s, _ in only_subs(frames)])
    rd = Reader(lp[0])
    _m, lineup2, _c = s2cteamlist(rd)
    check('阵型回读 == 3', lineup2 == 3, lineup2)

    print('[3] 名册请求（C2SRequestMemberInfo）')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_REQMEMBERINFO))
    subs = [s for s, _ in only_subs(s2c_frames(conn.buf))]
    check('回 TeamList + TeamerInfo',
          teammod.S_LIST in subs and teammod.S_TEAMERINFO in subs, subs)

    print('[4] 踢人（C2SKickout）')
    victim = srv.team.members[1]['id']
    n0 = len(srv.team.members)
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM,
                  team_body(teammod.T_KICKOUT, struct.pack('<I', victim)))
    check('成员数 -1', len(srv.team.members) == n0 - 1, len(srv.team.members))
    lp = [p for s, p in only_subs(s2c_frames(conn.buf)) if s == teammod.S_LIST]
    check('踢人后回 TeamList', len(lp) == 1)
    rd = Reader(lp[0])
    after, _l, _c = s2cteamlist(rd)
    check('被踢的人已不在名册', all(x['id'] != victim for x in after))

    print('[5] 队长不可被踢')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_KICKOUT, struct.pack('<I', 1)))
    check('队长仍在队', srv.team.status_of(1) == teammod.TEAM_LEADER)

    print('[6] 队伍目标（C2SChangeTarget）')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_CHANGE_TARGET, b'\x07'))
    subs = [s for s, _ in only_subs(s2c_frames(conn.buf))]
    check('回 TeamTarget(12)', teammod.S_TEAMTARGET in subs, subs)
    check('目标已记录 == 7', srv.team.target == 7, srv.team.target)

    print('[7] 战斗中发组队指令不落保底')
    srv.in_war = True
    srv.await_cmd = False
    srv._pending_pet_suffix = None
    srv.enemy_hp = {1: 100}
    fallbacks = []
    srv._fallback_turn = lambda c, o, _f=fallbacks: _f.append(o)
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_REQMEMBERINFO))
    check('战斗态未落保底', not fallbacks, fallbacks)
    srv.in_war = False

    print('[8] 队友作为己方单位入场')
    srv._start_war(FakeConn())
    check('记录到队友 war_id', len(srv._team_war_ids) > 0, srv._team_war_ids)
    check('war_id 不与主角/宠物/敌方冲突',
          all(w not in (m.HERO_WAR_ID, m.PET_WAR_ID) and w not in srv.enemy_hp
              for w in srv._team_war_ids), list(srv._team_war_ids))
    srv.in_war = False

    print('[9] 离队（C2SEscape）→ NONE')
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_PLAYER_TEAM, team_body(teammod.T_ESCAPE))
    frames = s2c_frames(conn.buf)
    st_payload = [p for s, p in only_subs(frames) if s == teammod.S_STATUS]
    check('回 TeamStatus', len(st_payload) == 1)
    check('状态 == NONE(0)', Reader(st_payload[0]).int(1) == teammod.TEAM_NONE)
    check('服务端已解散', srv.team.active is False)
    lp = [p for s, p in only_subs(frames) if s == teammod.S_LIST]
    if lp:
        rd = Reader(lp[0])
        empty, _l, _c = s2cteamlist(rd)
        check('离队后名册为空', empty == [], empty)

    print('[10] 非队长视角：状态 NORMAL')
    st = teammod.TeamState(1, '队长')
    st.create()
    st.add_simulated('队友A', pid=2)
    check('队长 LEADER', st.status_of(1) == teammod.TEAM_LEADER)
    check('队员 NORMAL', st.status_of(2) == teammod.TEAM_NORMAL)
    check('路人 NONE', st.status_of(999) == teammod.TEAM_NONE)

    print()
    if fails:
        print('FAIL（%d 项）' % len(fails))
        for f in fails:
            print('  ✗ %s' % f)
        return 1
    print('组队闭环 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
