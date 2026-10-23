# -*- coding: utf-8 -*-
"""组队（C2S_PLAYER_TEAM 72 / S2C_PLAYER_TEAM 82）—— 纯数据层。

单人服没有第二个真人，但「组队」是客户端里一条完整且高频的交互链：
建队 → 面板 → 队伍信息 → 成员列表 → 临时离队/归队 → 解散。
本模块只负责**队伍状态与协议帧**，不碰战斗/存档；服务端 `M4Server`
持有 `TeamState` 实例并在 `_dispatch` 里接线。

—— 协议依据（全部来自客户端源码，不是猜的）——
C2S：netteam.py 里每个 `PacketPrepare(net.C2S_PLAYER_TEAM)` 之后
     `PacketAddI(sub,1) + 参数`，sub 取值 1..17。
S2C：net.py `g_Func[82] = ('netteam','S2CPlayerTeam')`，
     netteam.S2CPlayerTeam 先读 sub(1)，再按 sub 解 body：
       sub1  S2CTeamStatus   status(1)
       sub2  S2CTeamList     num(1) + 每人 17 字段 + lineup(1) + canuse(1)
       sub6  S2CTeamInfo     title(f20) leader(f20) min(1) max(1) class(1) message(s)
       sub11 S2CTeamerInfo   num(1) + 每人 pid(4) name(f20) grade(1) target(1) cls(1)
       sub7  S2CGPS          id(4) scene(2) x(2) y(2) name(s)
     其余 sub（3/4/5/8/10/12）需要真人对手方，本服不主动下发。

状态常量取自 teamui.py：NONE=0 / NORMAL=1 / LEADER=2 / LEAVE=3。
"""

import struct

# ── C2S sub（netteam.py）───────────────────────────────────────────────
T_MAKETEAM = 1          # C2SMakeTeam(id)：id=0 表示自己建队
T_KICKOUT = 2           # C2SKickout(id)
T_ACCEPTJOIN = 3        # C2SAcceptJoin(id, answer)
T_ESCAPE = 4            # C2SEscape()：临时离队 / 队伍里点「离队」
T_REQMEMBERINFO = 5     # C2SRequestMemberInfo()
T_RESPREQ = 6           # C2SResponseRequest(id, answer)
T_CHANGELEADER = 7      # C2SChangeLeader(id)
T_FREEMODE = 8          # C2SFreeMode()：非队长点「暂离」回来
T_GETTEAMINFO = 9       # C2SGetTeamInfo(tid)
T_RECRUIT = 10          # C2SRecruit(name,min,max,class,msg)：招募
T_CANCELRECRUIT = 11    # C2SCancelRecruit()
T_SETLINEUP = 12        # C2SSetLineup(idx)：阵型
T_INVITE_RETURN = 13    # C2SInviteForReturn(pid)
T_INVITE_TRANSFER = 14  # C2SInviteForTransfer(tid, i)
T_QUEST_TEAMINFO = 15   # C2SQuestTeamInfo(target)
T_QUEST_TEAMERINFO = 16 # C2SQuestTeamerInfo(target)
T_CHANGE_TARGET = 17    # C2SChangeTarget(target)

# ── S2C sub（netteam.S2CPlayerTeam 分发）──────────────────────────────
S_STATUS = 1
S_LIST = 2
S_REQUESTJOIN = 3
S_ANSWERJOIN = 4
S_REQLIST = 5
S_TEAMINFO = 6
S_GPS = 7
S_INVITERETURN = 8
S_SEARCHINFO = 10
S_TEAMERINFO = 11
S_TEAMTARGET = 12

# ── 队伍状态（teamui.py TEAM_MEMBER_*）────────────────────────────────
TEAM_NONE = 0
TEAM_NORMAL = 1
TEAM_LEADER = 2
TEAM_LEAVE = 3

OP_S2C_PLAYER_TEAM = 82

# 队伍人数上限（客户端 teaminfo.dlg 的 high 输入框限制 3 位，服务端按常规 5 人）
TEAM_MAX_MEMBERS = 5


def s2c_team(payload):
    """S2C_PLAYER_TEAM(82) 帧：总包 = 长度帧头 + op + body。"""
    body = struct.pack('<B', OP_S2C_PLAYER_TEAM) + payload
    if len(body) >= 0xFF:
        return b'\xff' + struct.pack('<H', len(body)) + body
    return bytes([len(body)]) + body


def _gbk(s, width=0):
    """GBK 定长/变长字符串，与客户端 PacketAddS/UnpackString 对齐。

    UnpackString(n)：定长取 n 字节并去掉尾部 \\x00；UnpackString(0) 读剩余全部。
    """
    raw = (s or '').encode('gbk', 'replace')[:width or 256]
    if width:
        return raw + b'\x00' * (width - len(raw))
    return raw


def build_team_status(status):
    """sub1 S2CTeamStatus：status(1)。"""
    return s2c_team(struct.pack('<BB', S_STATUS, status))


def build_team_list(members, lineup=0, canuse=0):
    """sub2 S2CTeamList。每人的字段顺序严格按 netteam.S2CTeamList：

    id(4) vip(1) class(1) grade(1) icon(1) photo_effect(1)
    photo(4×4) leave(1) name(f20) point(2)
    末尾再跟 lineup(1) + canuse(1)。

    `members` 是 dict 列表，缺失键用默认值补齐（避免 KeyError 打断整帧）。
    """
    p = bytearray()
    p += struct.pack('<BB', S_LIST, len(members))
    for m in members:
        photo = m.get('photo') or (0, 0, 0, 0)
        p += struct.pack('<I', m.get('id', 0))
        p += struct.pack('<B', m.get('vip', 0))
        p += struct.pack('<B', m.get('class', 1))
        p += struct.pack('<B', m.get('grade', 1))
        p += struct.pack('<B', m.get('icon', 0))
        p += struct.pack('<B', m.get('photo_effect', 0))
        for v in photo[:4]:
            p += struct.pack('<I', v & 0xFFFFFFFF)
        p += struct.pack('<B', m.get('leave', 0))
        p += _gbk(m.get('name', ''), 20)
        p += struct.pack('<H', m.get('point', 0))
    p += struct.pack('<B', lineup & 0xFF)
    p += struct.pack('<B', canuse & 0xFF)
    return s2c_team(bytes(p))


def build_team_info(title, leader, imin, imax, iclass, message):
    """sub6 S2CTeamInfo：title(f20) leader(f20) min(1) max(1) class(1) message(s)。"""
    p = struct.pack('<B', S_TEAMINFO)
    p += _gbk(title, 20)
    p += _gbk(leader, 20)
    p += struct.pack('<B', imin & 0xFF)
    p += struct.pack('<B', imax & 0xFF)
    p += struct.pack('<B', iclass & 0xFF)
    p += _gbk(message, 0)
    return s2c_team(p)


def build_teamer_info(members):
    """sub11 S2CTeamerInfo：num(1) + 每人 pid(4) name(f20) grade(1) target(1) cls(1)。"""
    p = struct.pack('<BB', S_TEAMERINFO, len(members))
    for m in members:
        p += struct.pack('<I', m.get('id', 0))
        p += _gbk(m.get('name', ''), 20)
        p += struct.pack('<B', m.get('grade', 1))
        p += struct.pack('<B', m.get('target', 0))
        p += struct.pack('<B', m.get('class', 1))
    return s2c_team(p)


def build_team_gps(pid, scene, x, y, name):
    """sub7 S2CGPS：id(4) scene(2) x(2) y(2) name(s)。"""
    p = struct.pack('<BIHHH', S_GPS, pid, scene & 0xFFFF, x & 0xFFFF, y & 0xFFFF)
    p += _gbk(name, 0)
    return s2c_team(p)


def build_team_target(target):
    """sub12 S2CTeamTarget：target(1)。"""
    return s2c_team(struct.pack('<BB', S_TEAMTARGET, target & 0xFF))


class TeamState(object):
    """一支队伍的内存态。

    单人服只可能有一支队伍，且队长恒为本地玩家；`members` 里除本人外可以
    挂「陪同修炼的队友」（由服务端按需生成，用于让队伍面板/阵型/成员列表
    有真实内容可操作）。所有成员都只是数据，不参与战斗行动。
    """

    def __init__(self, hero_id, hero_name, hero_class=1, hero_grade=1):
        self.active = False
        self.leader_id = hero_id
        self.members = []           # dict 列表，第 0 位恒为队长
        self.title = ''
        self.min_lv = 0
        self.max_lv = 0
        self.attr_class = 0         # 允许的门派位掩码
        self.message = ''
        self.lineup = 0             # 阵型下标
        self.target = 0             # 队伍目标
        self._self_template = {
            'id': hero_id,
            'name': hero_name,
            'class': hero_class,
            'grade': hero_grade,
            'icon': 0,
            'vip': 0,
            'photo': (0, 0, 0, 0),
            'photo_effect': 0,
            'point': 0,
        }

    # ---------------------------------------------------------------- 查询
    def is_leader(self, pid):
        return self.active and pid == self.leader_id

    def status_of(self, pid):
        """返回该玩家视角的队伍状态（teamui TEAM_MEMBER_*）。

        注意「不在队里的人」必须是 NONE——否则客户端会把路人当队员显示。
        """
        if not self.active:
            return TEAM_NONE
        if pid == self.leader_id:
            return TEAM_LEADER
        if pid in self.member_ids():
            return TEAM_NORMAL
        return TEAM_NONE

    def member_ids(self):
        return [m['id'] for m in self.members]

    def list_for_wire(self):
        """转成 S2CTeamList 需要的样子（补 leave/point 默认值）。"""
        out = []
        for i, m in enumerate(self.members):
            d = dict(m)
            d.setdefault('leave', 0)
            d.setdefault('point', 0)
            if i == 0:
                d['leave'] = 0           # 队长永远在队
            out.append(d)
        return out

    # ---------------------------------------------------------------- 变更
    def create(self):
        """建队：自己成为队长。重复建队是幂等的。"""
        if self.active:
            return False
        self.active = True
        self.members = [dict(self._self_template)]
        self.title = '%s 的队伍' % (self._self_template['name'] or '玩家')
        self.min_lv = 0
        self.max_lv = TEAM_MAX_MEMBERS   # 队伍人数上限（面板 high 输入框显示这个）
        self.attr_class = 0
        self.message = '单机队伍'
        self.lineup = 0
        self.target = 0
        return True

    def dismiss(self):
        """解散（队长离队 / 只剩自己时离开）。"""
        if not self.active:
            return False
        self.active = False
        self.members = []
        return True

    def add_simulated(self, name, grade=1, cls=1, icon=0, pid=None):
        """加入一名「陪同修炼」的队友（单人服专用）。

        返回加入后的成员 dict；队伍已满 / 未建队则返回 None。
        """
        if not self.active or len(self.members) >= TEAM_MAX_MEMBERS:
            return None
        if pid is None:
            pid = max(self.member_ids() + [0]) + 1
        m = {
            'id': pid,
            'name': name,
            'class': cls,
            'grade': grade,
            'icon': icon,
            'vip': 0,
            'photo': (0, 0, 0, 0),
            'photo_effect': 0,
            'point': 0,
            'leave': 0,
        }
        self.members.append(m)
        return m

    def remove(self, pid):
        """移除成员（踢人 / 自己退回单人）。队长不可被移除——解散请用 dismiss。"""
        if not self.active:
            return False
        if pid == self.leader_id:
            return False
        for i, m in enumerate(self.members):
            if m['id'] == pid:
                del self.members[i]
                return True
        return False

    def reposition(self, pid, leave):
        """临时离队(1) / 归队(0)。返回是否命中成员。"""
        for m in self.members:
            if m['id'] == pid:
                if m['id'] == self.leader_id:
                    return False
                m['leave'] = 1 if leave else 0
                return True
        return False
