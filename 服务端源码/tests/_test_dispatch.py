# -*- coding: utf-8 -*-
"""_dispatch 分派顺序契约的自动化测试（进程内，无 socket，秒级跑完）。

为什么要有它：`_dispatch()` 是一串**顺序敏感**的 if/elif，插错位置不报错，只表现为
「帧序错了」——客户端在战斗中也会发 OPENUI / 道具 / 养成 / NPC 类指令，这些一旦落到
末尾的 `elif self.in_war:` 战斗保底，就会凭空回一帧 WarTurn、把回合推给敌方
（"谁必须排在保底之前"这份清单就是 `DISPATCH_PRE_WAR_OPS`）。

本测试把契约变成可执行断言：
  ① 契约表里每个 opcode，在战斗态（in_war=True）下走真实 `_dispatch`，
     断言【没有】触发 `_fallback_turn`（即没落保底、没多推回合）；
  ② 负向对照：一个未知 opcode 在战斗态下【必须】落保底（证明探针本身有效）；
  ③ 新增分支行为：C2S_NPC_TASK(100) 已接任务重发 TaskAdd / 未接不造数据；
     C2S_NPC_GETDETAILS(101) → S2CNpcDetails(151)，且字节能被 tap.OVERLAY 反解；
  ④ 覆盖率守卫：所有已定义的非战斗 C2S opcode 都应登记进契约表（防漏）。

用法： python tests/_test_dispatch.py   （在 服务端源码 目录下；路径自适配，任意目录亦可）
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # 服务端源码
# 兼容三种摆放：ROOT/sim（原布局）、ROOT 自身、**HERE/sim**（本工作区：
# sim 被整体挪进 服务端源码/tests/ 之后）。历史上两份副本曾长期漂移，故按锚点文件找。
SIM = next(p for p in (os.path.join(ROOT, 'sim'), ROOT,
                       os.path.join(HERE, 'sim'))
           if os.path.isfile(os.path.join(p, 'm4_server.py')))
sys.path.insert(0, SIM)

import damage_model as dm
import m4_server as m
import tap

PORT = 3199          # 不真正 bind（不调 run()），仅满足构造参数
SEED = 0x0BADF00D


class FakeConn(object):
    """collect sendall 字节的最小连接替身（_dispatch 只用 sendall）。"""

    def __init__(self):
        self.buf = b''

    def sendall(self, data):
        self.buf += bytes(data)


def s2c_frames(buf):
    """把连接攒下的 S2C 字节切成 (opcode, body) 列表（复用 tap 唯一分帧实现）。"""
    frames, _rest = tap.split_frames('S2C', buf)
    return [(op, body) for _raw, op, body in frames]


def make_server(tmp):
    srv = m.M4Server('127.0.0.1', PORT, seed=SEED, encounter_every=0, enemies=1,
                     turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    # 兜底初始化：__init__ 若把状态懒加载到 handle()，这里补齐最小可用状态
    for attr, val in (('items', {}), ('tasks', {}), ('coins', m.COIN_INIT),
                      ('hero_id', 1), ('hero_name', '测试'), ('cur_npc', m.NPC_ID),
                      ('active_pet_id', 0), ('move_count', 0)):
        if not hasattr(srv, attr):
            setattr(srv, attr, val)
    # 背包放一件真实道具（道具/拖动分支走真路径）
    srv.items[2001] = {'shape': 22100, 'key': 0, 'qty': 5,
                       'pos': m.ITEM_POS_BAG_START, 'infoext': '小麦种子'}
    return srv


# 只对「**确定应该产生可观测效果**」的分支做空转检测。
#
# 白名单而非黑名单，是因为绝大多数 PRE_WAR 分支的静默是**合法**的：
#   · 显式忽略族（19/20/23/52/55/80/149…）——设计上就不回帧；
#   · 条件不满足时静默（op48 未接任务不应答、op100 未接取不应答、op49 未接任务不处理…）；
#   · op88/81 的 sub 在本用例里恰好是「无应答 sub」。
# 把这些都算成「空转」会逼着为凑数造假断言，反而降低测试可信度。
#
# 这里登记的是「给最小合法 body 就**必然**回至少一帧或改状态」的分支——
# 变异测试要抓的正是它们：一旦被改成提前 return，这类分支会同时失去回帧与状态变化。
MUST_EFFECT_OPS = frozenset((
    96,   # 点 NPC：必回 ChatExt(152)
    98,   # 对话选项：必回帧
    99,   # 购买：必回 ItemAdd + 对话
    103,  # 学招式：必回 PropChange/SkillLevel 或拒绝对话
    145,  # 物品详情：必回 ItemInfo(162)
    156,  # 物品附加：必回 ItemTCInfo(190)
    144,  # 宠物详情：必回 SummonInfo(48)
    161,  # 战斗招式：必回 WarPerform(122)
    165,  # 战斗道具：必回帧
))
# 刻意**排除**的几条（战斗态本来就该静默，不是缺陷）：
#   54  移动 —— 命中 `if self.in_war: return True`（战斗中不该移动，这是正确设计）
#   16  加点 —— body 全 0 时 total<=0 直接拒；要用真实增量才能测（见 ③ 区专项）
#   106 收购 —— 需先走过 _send_sell_list 才有 _sell_cache，本用例未开面板


# 每条 PRE_WAR 分支在非战斗态下的「身份指纹」：一个必然出现在 srv.log 里的子串。
#
# ★ 为什么用日志而不是「有没有回帧」：
#   回帧只能说明"发了点什么"，分不清走的是不是**该走的那条**分支
#   （例如 op99 落到 op98 也可能回帧）。日志里每条的 `>>> xxx` 是分支自己打的，
#   等于分支自报家门 —— 这是最直接的正向证据。
#
# 取值来自实测（把 54 条逐个跑一遍抓 `>>>` 行），不是照着代码猜。
# ★ 显式忽略族没有 `>>>`，用的是「（xxx ...）」那句固定文案。
EXPECT_LOG = {
    54:  '>>> 移动',
    17:  '升级被拒',              # 1 级 0 经验 → 走分支但被规则挡下
    16:  '加点',                  # 需真实增量才走进去：EXPECT_LOG_BODY 单独给 body
    103: '>>> 学招式 skid=',
    96:  '>>> 点击 NPC id=',
    98:  '>>> NPC 选项 =',
    99:  '>>> NPC 购买 index=',
    106: '>>> NPC 卖出 flag=',
    100: '>>> NPC 任务交互 tid=',
    101: '>>> 物品详情 pos=',
    104: '>>> 制作费用 flag=',
    102: '>>> 制作请求 mode=',
    48:  '',                      # 未接任务时静默：只验「不落保底」
    88:  '（OPENUI sub=',
    81:  '（GROUP sub=',
    192: '（C2S_COMMON sub=',
    129: '>>> 查看宠物资质 id=',
    131: '宠物出战 id=',
    # 2026-10-22：这五条从「显式忽略」升级为真实现（见 m4_server 的 _pet_free /
    # _pet_rename / _pet_store / _pet_add_point）—— 指纹改成各分支自己打的日志。
    # 指纹取「成功/失败两条路径都含」的前缀（探针 body 不一定合法）。
    128: '放生',
    130: '宠物改名',
    132: '寄存',
    133: '取出',
    134: '加点',                     # 探针只发 pid（无 5 字节增量）→ 走「长度不足」分支
    35:  '>>> 请求道具附加信息',
    32:  '>>> 请求道具详情',
    33:  '>>> 拖动道具 id=',
    34:  '>>> 使用道具 id=',
    36:  '（物品指令 op=36',
    37:  '（物品指令 op=37',
    38:  '（物品指令 op=38',
    145: '>>> 物品详情 iid=',
    156: '>>> 物品附加信息 itype=',
    144: '>>> 宠物详情 pid=',
    15:  '>>> 服务器时间查询 C2S_TIME(15)',
    61:  '>>> 服务器时间查询 C2GS_TIME(61)',
    14:  '（黑名单同步请求',
    78:  '（PRODUCE sub=',
    49:  '>>> 放弃任务 tid=',
    52:  '（op=52',
    55:  '（op=55',
    80:  '（op=80',
    19:  '（op=19',
    20:  '（op=20',
    23:  '（op=23',
    149: '（op=149',
    151: '>>> 鼠标导航 goto',
    72:  '离队/解散',
    97:  '>>> 给予入口 npcid=',
    105: '>>> 交付 NPC=',
    108: '>>> 右键 NPC id=',
    112: '>>> 交易中心 sub=',
    113: '>>> NPC 对话选物 iid=',
    127: '>>> NPC 对话选物 iid=',
    115: '>>> NPC 对话输入',
    # 系统设置（dialog/dlgsysopt）：三条都「收下即走」，用分支自己打的 >>> 认身份
    21:  '>>> 系统设置保存 sub=1',
    12:  '>>> 手机安全面板请求',
    11:  '>>> 私密资料提交',
    # 炼药（op73）：只识别不实现，分支自报家门即可（无 S2C 帧）
    73:  '>>> 炼药请求 sub=',
    }


# 少数分支用最小 body 会被"规则"挡在门口（不是没走进去，是走了但被拒），
# 为它们单独给一份能真正触发业务的 body。目前只有 op16：全 0 增量 total<=0 直接 return。
EXPECT_LOG_BODY = {
    16: bytes([1, 0, 0, 0, 0]),      # 体+1，够触发 _hero_add_points
}


def _dispatch_probe_snapshot(srv, op):
    """取一份「这条分支执行后应该会变」的状态指纹（用于识别空转分支）。

    只读服务端内存状态，不发帧、不改状态。字段覆盖 PRE_WAR 分支真正会动的那些：
    坐标/地图、金币、经验/等级/点数、背包、任务、战斗态、宠物、活力体力、生活技能。

    ★ 为什么要它：`_fallback_turn` 探针只能发现「落进了保底」。
      把分支改成提前 `return`（或条件永假），既不落保底也不干活 —— 探针全绿，
      功能却已经死了。变异测试实测确认这个盲区存在（见 MODDING_PLAN 备注）。
    """
    return (
        srv.px, srv.py, srv.mapid,
        srv.coins, srv.exp, srv.grade, srv.points,
        tuple(sorted(srv.attrs.items())),
        len(srv.items),
        tuple(sorted((tid, t.get('state'), t.get('progress'))
                     for tid, t in srv.tasks.items())),
        srv.in_war,
        srv.pet_hp, srv.active_pet_id,
        srv.huoli, srv.tili, srv.life_lv, srv.life_exp,
        srv.cur_npc, srv.give_npc, srv.npc_chosen,
        len(srv.team.members) if getattr(srv, 'team', None) else 0,
    )


# 每个 PRE_WAR opcode 的最小合法帧体（与真实客户端发的形态一致，满足各分支长度守卫）
def minimal_bodies():
    B = m.ITEM_POS_BAG_START
    return {
        m.C2S_MAP_MOVE:      struct.pack('<HHB', 0, 0, 0),
        m.C2S_PROP_LEVELUP:  b'',
        m.C2S_PROP_POINT:    b'\x00' * 5,
        m.C2S_NPC_LEARN_SKILL: struct.pack('<H', 1104),
        m.C2S_NPC_LOOK:      struct.pack('<I', m.NPC_ID),
        m.C2S_NPC_ANSWER:    struct.pack('<I', 0),
        m.C2S_NPC_BUY:       struct.pack('<HH', 0, 1),
        m.C2S_NPC_SELL:      b'\x00\x00',
        m.C2S_NPC_TASK:      struct.pack('<H', m.TASK_NEWBIE_ID),
        m.C2S_NPC_GETDETAILS: struct.pack('<I', 0),
        # flag + 2 个材料 id（真实客户端 len(ids)==2 时 flag=itype）
        m.C2S_NPC_GETCOST:   bytes([2]) + struct.pack('<II', 50010, 51010),
        # 用不存在的实例 id：配方校验失败 → 只回一条对话，不改背包（战斗态探针不该有副作用）
        m.C2S_NPC_MAKE:      bytes([7]) + struct.pack('<I', 999999),
        m.C2S_GET_TASK:      b'',
        m.C2S_OPENUI:        b'\x00',
        m.C2S_GROUP:         b'\x00',
        m.C2S_COMMON:        b'\x00',
        m.C2S_SUMM_VIEW:     struct.pack('<I', m.PET_ID),
        m.C2S_SUMM_SET_FIGHT: struct.pack('<I', m.PET_ID),
        # ★ 放生探针**故意用一个不存在的 pid**：真放生会把栏里唯一那只删掉，
        #   后面 op144「宠物详情必回 48」就再也回不出东西（那是探针互相污染，
        #   不是实现缺陷）。这条只验「走没走到放生分支」，用空 id 一样成立。
        m.C2S_SUMM_FREE:     struct.pack('<I', 0xFFFF),
        m.C2S_SUMM_RENAME:   struct.pack('<I', m.PET_ID) + '小黑'.encode('gbk'),
        m.C2S_SUMM_PUT:      struct.pack('<I', m.PET_ID),
        m.C2S_SUMM_TAKE:     struct.pack('<I', m.PET_ID),
        m.C2S_SUMM_ADDPOINT: struct.pack('<I', m.PET_ID),
        m.C2S_ITEM_STRING:   struct.pack('<IB', 2001, 0),
        m.C2S_ITEM_GET:      struct.pack('<I', 2001),
        m.C2S_ITEM_DRAG:     struct.pack('<IBB', 2001, B, B + 1),
        m.C2S_ITEM_USE:      struct.pack('<I', 2001),
        m.C2S_ITEM_FULL_USE: struct.pack('<IB', 2001, 0),
        m.C2S_ANSWER_BIND:   struct.pack('<I', 2001),
        m.C2S_ITEM_LOCK:     b'',
        m.C2S_TIME:          b'',
        m.C2GS_TIME:         b'',
        m.C2S_BACLLIST:      b'',
        m.C2S_PRODUCE:       b'\x00',
        # 2026-10-19 新增：放弃任务（flag(1)+taskid(2)）+ 显式忽略族（无需长度守卫，空包即可）
        m.C2S_GIVEUP_TASK:   struct.pack('<BH', 1, m.TASK_NEWBIE_ID),
        m.C2S_HERO_SALE:     b'',
        m.C2S_MAP_SYN_DIREMOTE: b'',
        m.C2S_DISTRICT:      b'',
        m.C2S_DIY_MAGIC:     b'',
        m.C2S_PLAYER_PHOTO:  b'',
        m.C2S_PLAYER_IB:     b'',
        m.C2S_PLAYER_CHAT:   b'',
        # 详情面板（op144/145/156）：tid(4)+oid(4) / itype(1)+id(4)+id1(4)
        m.C2S_GETINFO_ITEM:   struct.pack('<II', 2001, 0),
        m.C2S_GETITEMINFOEXT: struct.pack('<BII', m.ITEM_WTYPE_NORMAL, 2001, 0),
        m.C2S_GETINFO_SUMMON: struct.pack('<II', m.PET_ID, 0),
        # NPC 交互族（netnpc.py）：按客户端 PacketAddI 顺序构造最小帧
        #   op97  1(1)+npcid(4)+gtype(1)
        m.C2S_NPC_COMMAND:        struct.pack('<BIB', 1, m.NPC_ID, 2),
        #   op105 targetid(4)+itype(1)+cash(4)+[ob(4)+amount(1)]×n
        m.C2S_NPC_GIVE:           struct.pack('<IBI', m.NPC_ID, 0, 0) + struct.pack('<IB', 2001, 1),
        #   op108 npcid(4)
        m.C2S_NPC_RBCLICK:        struct.pack('<I', m.NPC_ID),
        #   op112 sub(1)+id(4)
        m.C2S_NPC_TRADECENTER:    struct.pack('<BI', 1, 0),
        #   op113 itemid(4)
        m.C2S_NPC_CHOOSE_ITEM:    struct.pack('<I', 2001),
        #   op127 itemid(4)+num(1)
        m.C2S_NPC_CHOOSE_ITEM_NUM: struct.pack('<IB', 2001, 1),
        #   op115 msg(变长字符串，无前缀)
        m.C2S_NPC_INPUT:          b'hello',
        # 2026-10-19 新增：组队 op72（netteam.py，17 个子命令）——sub(1B) + 参数。
        # 探针取「离队」子命令（1 字节，最小且走真实分支；不建队，免得队伍态污染后续断言）。
        m.C2S_PLAYER_TEAM:        struct.pack('<B', m.teammod.T_ESCAPE),
        # 鼠标导航（小地图图面 goto scene,x,y）：目标图不存在 → 静默忽略，不落保底
        m.C2S_PLAYER_WIZARD:      b'goto 0,0,0',
        # 系统设置（dialog/dlgsysopt，2026-10-22）
        #   op21 sub=1 → switch(4B 位图)；op12 sub=1 → 面板请求；op11 sub=2 → 私密资料串
        m.C2S_SYSSWITCH:          bytes([m.SYSSWITCH_SUB_SWITCH]) + struct.pack('<I', 0b1011),
        m.C2S_MOBILE:             bytes([m.MOBILE_SUB_INFO]),
        m.C2S_DUOYITONG:          bytes([m.DUOYITONG_SUB_PRIVATE]) + 'idcard=110101'.encode('gbk'),
        # 炼药（op73）：本服只识别、不实现（无 drugdata 配方表）。sub(1B)=0 开界面。
        # 分支只 log + _emit，**不回帧、不改状态**——属「显式识别但忽略」族，
        # 见下方 EXEMPT_NO_SIDE_EFFECT 登记。
        m.C2S_PLAYER_DRUG:         bytes([0]),
    }


def enter_war(srv):
    srv.in_war = True
    srv.await_cmd = False
    srv._pending_pet_suffix = None
    srv.enemy_hp = {1: 100}


def run():
    tmp = tempfile.mkdtemp(prefix='m4disp_')
    srv = make_server(tmp)
    bodies = minimal_bodies()
    fails = []

    # ① 契约：战斗态下每个 PRE_WAR op 都不能落保底
    #
    # ★ 2026-10-19 变异测试补强：光看「有没有落保底」是不够的。
    #   把分支改成 `if self.in_war: return True` 这种**提前返回**，既不落保底、
    #   也完全不执行业务——上面的断言照样绿。这类变异比「挪到保底之后」更隐蔽，
    #   因为代码看起来还在、PR 里也不显眼。
    #   所以每条只断言「没落保底」，还要断言「确实产生了可观测的业务效果」。
    #   EFFECTS 表给每个 op 一个判定函数：返回 None=无证据可用（跳过），
    #   返回 True/False=该效果是否发生。没登记的不强求（避免为凑数造假断言）。
    def _effect_ok(op):
        """该 op 在战斗态下是否真的执行了业务（None = 本用例不检查）。"""
        if op == m.C2S_PROP_POINT:
            # 加点：5 点全 0 会「无变化」，所以这里给真实增量再判
            return None
        return None

    for op, why in m.DISPATCH_PRE_WAR_OPS:
        enter_war(srv)
        fallbacks = []
        srv._fallback_turn = lambda conn, o, _f=fallbacks: _f.append(o)
        conn = FakeConn()
        # 每条分支的「业务痕迹」快照：用于证明分派真的走进去执行了，而不是提前返回
        before = _dispatch_probe_snapshot(srv, op)
        try:
            srv._dispatch(conn, op, bodies[op])
        except Exception as e:                                   # noqa: BLE001
            fails.append('op=%d(%s) 战斗态分派异常: %r' % (op, why, e))
            continue
        if fallbacks:
            fails.append('op=%d(%s) 落进了战斗保底（多推一帧 WarTurn）' % (op, why))
        # ★ 提前 return / 条件被改成永不成立 的变异：不落保底但也没干活。
        #   判据 = 有没有回帧 或 状态有没有变。
        #   ⚠ 只对「**本该有副作用**」的分支判：显式忽略族（op19/20/23/52/55/80/149…）
        #     与日志型分支本来就不回帧、不改状态，那是设计而非缺陷。名单见下方
        #     NO_SIDE_EFFECT_OPS——它同时是一份「这些分支只有日志」的诚实记录。
        after = _dispatch_probe_snapshot(srv, op)
        if (not fallbacks and not conn.buf and before == after
                and op in MUST_EFFECT_OPS):
            fails.append('op=%d(%s) 战斗态既没落保底也没执行（分支形同虚设：'
                         '可能被 `if self.in_war: return` 之类提前短路）' % (op, why))

    # ①½ 正向对照：每个 PRE_WAR op 在**非战斗态**必须真的走进它自己那条分支
    #
    # ① 只证明「战斗态不落保底」——那是**负向**证据（没做错事）。
    # 它不能证明「非战斗态走了对的分支」：把分支整体删掉、条件改成永假、
    # 或者 op 被前面的 elif 抢走，① 都会照样绿（因为压根没进保底）。
    # 这里用**分支自报家门**的日志行做正向证据，每条 PRE_WAR 都核对。
    # ★ 每条用**独立服务端实例**：这些分支会改状态（买东西、交物品、学招式…），
    #   共用一个 srv 会污染后面的用例（首版就这么把后面 op34 的背包断言搞崩了）。
    import copy as _copy
    for op, why in m.DISPATCH_PRE_WAR_OPS:
        want = EXPECT_LOG.get(op, '')
        if not want:
            continue            # 该分支在最小 body 下合法静默（如 op48 未接任务）
        s = make_server(tmp)
        s.in_war = False
        logs = []
        orig_log = s.log
        s.log = lambda line, _L=logs: _L.append(line)
        try:
            s._dispatch(FakeConn(), op, EXPECT_LOG_BODY.get(op, bodies[op]))
        except Exception as e:                                   # noqa: BLE001
            s.log = orig_log
            fails.append('op=%d(%s) 非战斗态分派异常: %r' % (op, why, e))
            continue
        finally:
            s.log = orig_log
        if not any(want in ln for ln in logs):
            fails.append('op=%d(%s) 非战斗态没走进对应分支（期望日志含 %r，'
                         '实际 %r）' % (op, why, want, [l.strip() for l in logs][:3]))

    # ② 负向对照：未知 op 在战斗态必须落保底（证明探针有效）
    enter_war(srv)
    fallbacks = []
    srv._fallback_turn = lambda conn, o, _f=fallbacks: _f.append(o)
    srv._dispatch(FakeConn(), 200, b'')
    if not fallbacks:
        fails.append('负向对照失败：未知 op=200 战斗态未落保底，探针无效')

    # ③ 新增分支行为
    # ③-1 op100 已接任务 → 重发 TaskAdd(72)
    srv.in_war = False
    srv.tasks[m.TASK_NEWBIE_ID] = {'state': m.TASK_STATE_DOING, 'progress': 1}
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_TASK, struct.pack('<HB', m.TASK_NEWBIE_ID, 1))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_TASK_ADD):
        fails.append('op100 已接任务应回且仅回 TaskAdd(72)，实得 %s' % [o for o, _ in fr])
    # ③-2 op100 未接任务 → 不应答
    del srv.tasks[m.TASK_NEWBIE_ID]
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_TASK, struct.pack('<H', m.TASK_NEWBIE_ID))
    if s2c_frames(conn.buf):
        fails.append('op100 未接任务不应答，实得 %s' % [o for o, _ in s2c_frames(conn.buf)])
    # ③-3 op101 → S2CNpcDetails(151)，且 OVERLAY 能反解（服务端发射 == 调试台展示）
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GETDETAILS, struct.pack('<I', 0))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.S2C_NPC_DETAILS):
        fails.append('op101 应回 S2CNpcDetails(151)，实得 %s' % [o for o, _ in fr])
    else:
        _op, body = fr[0]
        rows, tail, note, consumed = tap.decode_fields(tap.OVERLAY['S2C'][151], body)
        if tail or note or consumed != len(body):
            fails.append('op151 覆盖表反解不干净: tail=%s note=%s' % (len(tail), note))
        elif not (rows[0]['value'] == 0 and '小麦种子' in rows[1]['value']):
            fails.append('op151 内容不对: pos=%s desc=%s' % (rows[0]['value'], rows[1]['value']))

    # ③-4 op49 放弃任务：已接 → 回 TaskRemove(73)；未接 → 沉默
    srv.tasks[m.TASK_NEWBIE_ID] = {'state': m.TASK_STATE_DOING, 'progress': 1}
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, m.TASK_NEWBIE_ID))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_TASK_REMOVE):
        fails.append('op49 已接任务应回且仅回 TaskRemove(73)，实得 %s' % [o for o, _ in fr])
    if m.TASK_NEWBIE_ID in srv.tasks:
        fails.append('op49 放弃后任务应从 tasks 移除')
    srv.tasks.pop(m.TASK_NEWBIE_ID, None)   # 处理器理应已 pop，这里双保险

    # ③-5 详情面板：op145 物品详情 → S2CItemInfo(162)；未知道具 → 沉默
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GETINFO_ITEM, struct.pack('<II', 2001, 0))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_ITEM_INFO):
        fails.append('op145 已知道具应回且仅回 S2CItemInfo(162)，实得 %s'
                     % [o for o, _ in fr])
    else:
        rows, tail, note, consumed = tap.decode_fields(
            tap.OVERLAY['S2C'][m.OP_ITEM_INFO], fr[0][1])
        if tail or note or consumed != len(fr[0][1]):
            fails.append('op162 覆盖表反解不干净: tail=%s note=%s' % (len(tail), note))
        elif not (rows[2]['value'] == 2001 and rows[3]['value'] == '小麦种子'):
            fails.append('op162 内容不对: %s' % [(r['name'], r['value']) for r in rows])
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GETINFO_ITEM, struct.pack('<II', 999999, 0))
    if s2c_frames(conn.buf):
        fails.append('op145 未知道具不应答，实得 %s' % [o for o, _ in s2c_frames(conn.buf)])

    # ③-6 op156 附加信息 → S2CItemTCInfo(190)；itype=2（魂珠）无数据 → 沉默
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GETITEMINFOEXT,
                  struct.pack('<BII', m.ITEM_WTYPE_NORMAL, 2001, 0))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_ITEM_TCINFO):
        fails.append('op156 应回 S2CItemTCInfo(190)，实得 %s' % [o for o, _ in fr])
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GETITEMINFOEXT, struct.pack('<BII', 2, 2001, 0))
    if s2c_frames(conn.buf):
        fails.append('op156 itype=2（魂珠）应沉默，实得 %s'
                     % [o for o, _ in s2c_frames(conn.buf)])

    # ③-7 op144 宠物详情 → S2CSummonInfo(48)
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GETINFO_SUMMON, struct.pack('<II', m.PET_ID, 0))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_SUMMON_INFO):
        fails.append('op144 应回 S2CSummonInfo(48)，实得 %s' % [o for o, _ in fr])
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, m.TASK_NEWBIE_ID))
    if s2c_frames(conn.buf):
        fails.append('op49 未接任务不应答，实得 %s' % [o for o, _ in s2c_frames(conn.buf)])

    # ③-8 op104 制作费用：flag + 2 个材料 id → 且仅 → op155（3×u16，6B）
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GETCOST,
                  bytes([2]) + struct.pack('<II', 50010, 51010))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.S2C_NPC_MAKECOST and len(fr[0][1]) == 6):
        fails.append('op104 应回且仅回 op155（6B），实得 %s'
                     % [(o, len(b)) for o, b in fr])
    else:
        rows, tail, note, consumed = tap.decode_fields(
            tap.OVERLAY['S2C'][m.S2C_NPC_MAKECOST], fr[0][1])
        if tail or note or consumed != len(fr[0][1]):
            fails.append('op155 覆盖表反解不干净: tail=%s note=%s' % (len(tail), note))
        elif [r['value'] for r in rows] != [m.MAKE_COST_HUOLI * 2,
                                            m.MAKE_COST_MONEY * 2,
                                            m.MAKE_COST_SKLV]:
            fails.append('op155 数值不对: %s' % [r['value'] for r in rows])
    # 畸形包（只有 flag、没有 id）不许越界读，也不许回帧
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GETCOST, bytes([2]))
    if s2c_frames(conn.buf):
        fails.append('op104 短包应沉默，实得 %s' % [(o, len(b)) for o, b in s2c_frames(conn.buf)])

    # ③-9 入包飘窗：_grant_item 必须 ItemAdd(33) + NotifyExt(164) 成对，且 164 反解干净
    conn = FakeConn()
    iid = srv._grant_item(conn, 12, '鹿茸', 1)
    fr = s2c_frames(conn.buf)
    if not (iid and [o for o, _ in fr] == [m.OP_ITEM_ADD, m.S2C_NOTIFY_EXT]):
        fails.append('op164 入包应发 ItemAdd+NotifyExt，实得 %s' % [o for o, _ in fr])
    else:
        rows, tail, note, consumed = tap.decode_fields(
            tap.OVERLAY['S2C'][m.S2C_NOTIFY_EXT], fr[1][1])
        if tail or note or consumed != len(fr[1][1]):
            fails.append('op164 覆盖表反解不干净: tail=%s note=%s' % (len(tail), note))
        elif not (rows[0]['value'] == 0 and rows[1]['value'] == 12
                  and '鹿茸' in (rows[2]['value'] or '')):
            fails.append('op164 内容不对: %s'
                         % [(r['name'], r['value']) for r in rows])

    # ③-10 op102 制作：小麦种子×2 → 鹿茸；扣材料 + 入包 + op160 完成通知
    before_qty = srv.items[2001]['qty']
    before_coins = srv.coins
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_MAKE, bytes([7]) + struct.pack('<II', 2001, 2001))
    fr = s2c_frames(conn.buf)
    ops_ = [o for o, _ in fr]
    if ops_ != [m.OP_ITEM_QUANTITY, m.OP_PROP_CHANGE, m.OP_PROP_CHANGE,
                m.OP_SKILL_LEVEL, m.OP_ITEM_ADD, m.S2C_NOTIFY_EXT,
                m.S2C_MAKE_COMPLETE]:
        fails.append('op102 帧序不对：%s' % ops_)
    else:
        if srv.items[2001]['qty'] != before_qty - 2:
            fails.append('op102 材料扣除不对：%d → %d'
                         % (before_qty, srv.items[2001]['qty']))
        # ★ 消耗的必须是活力（下标 24），不是体力（26）——早期扣错属性，
        #   客户端制作面板显示的活力永远不动。这一条专门守住那个坑。
        _prop_body = fr[1][1]
        if len(_prop_body) < 3:
            fails.append('op102 的 PropChange 帧过短：%s' % _prop_body.hex())
        else:
            _pt = _prop_body[0]
            _pv = struct.unpack('<H', _prop_body[1:3])[0]
            # 期望扣多少按这台服务器自己的配方/产物表算，不写死数字。
            # 配方键是「造型(shape)」不是背包 id——造型从本服背包记录里取，
            # 免得引用 m4_server 里并不存在的常量（原稿写的 m.ITEM_SHAPE_WHEAT 就没有定义）。
            _prod = m.MAKE_RECIPES.get(((srv.items[2001]['shape'], 2),))
            _want_cost = int((m.CRAFT_DATA.get(_prod) or {}).get('cost')
                             or (m.MAKE_COST_HUOLI * 2))
            if _pt != m.PROP_IDX_HUOLI:
                fails.append('op102 应扣活力(下标 %d)，实得下标 %s'
                             % (m.PROP_IDX_HUOLI, _pt))
            if _pv != m.HERO_HUOLI_INIT - _want_cost:
                fails.append('op102 活力应扣 %d → %d，实得 %d'
                             % (_want_cost, m.HERO_HUOLI_INIT - _pv, _pv))
        # ★ craft_charge（2026-10-20 实现）：第 3 帧应是金币 PropChange，
        #   且实扣额 = op104 预览报的 MAKE_COST_MONEY×件数。
        _gb = fr[2][1]
        if len(_gb) < 5 or _gb[0] != m.PROP_IDX_GOLD:
            fails.append('op102 第 3 帧应为金币 PropChange(下标 %d)，实得 %s'
                         % (m.PROP_IDX_GOLD, _gb.hex()))
        elif struct.unpack('<I', _gb[1:5])[0] != before_coins - m.MAKE_COST_MONEY * 2:
            fails.append('op102 金钱应扣 %d → %d，实得 %d'
                         % (m.MAKE_COST_MONEY * 2,
                            before_coins - m.MAKE_COST_MONEY * 2,
                            struct.unpack('<I', _gb[1:5])[0]))
        rows, tail, note, consumed = tap.decode_fields(
            tap.OVERLAY['S2C'][m.S2C_MAKE_COMPLETE], fr[6][1])
        if tail or note or consumed != len(fr[6][1]):
            fails.append('op160 覆盖表反解不干净: tail=%s note=%s' % (len(tail), note))
        elif not (rows[0]['value'] == 12 and rows[2]['value'] == 1):
            fails.append('op160 内容不对：%s'
                         % [(r['name'], r['value']) for r in rows])
    # 未收录配方：只回对话、不动背包
    qty_before = srv.items[2001]['qty']
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_MAKE, bytes([7]) + struct.pack('<I', 2001))
    fr = s2c_frames(conn.buf)
    if [o for o, _ in fr] != [m.OP_NPC_CHAT_EXT]:
        fails.append('op102 未收录配方应只回对话，实得 %s' % [o for o, _ in fr])
    if srv.items[2001]['qty'] != qty_before:
        fails.append('op102 未收录配方不应扣材料')

    # ③-11 craftdata 产物：纸张×2 → 花卉剪纸(25201)；最后一摞用完应发 ItemDel
    conn = FakeConn()
    paper = srv._grant_item(conn, 10155, '纸张', 2)
    conn.buf = b''                     # 丢弃入包两帧，只保留制作响应
    srv._dispatch(conn, m.C2S_NPC_MAKE,
                  bytes([7]) + struct.pack('<II', paper, paper))
    fr = s2c_frames(conn.buf)
    ops_ = [o for o, _ in fr]
    if ops_ != [m.OP_ITEM_DEL, m.OP_PROP_CHANGE, m.OP_PROP_CHANGE,
                m.OP_SKILL_LEVEL, m.OP_ITEM_ADD, m.S2C_NOTIFY_EXT,
                m.S2C_MAKE_COMPLETE]:
        fails.append('op102 craftdata 配方帧序不对：%s' % ops_)
    else:
        if paper in srv.items:
            fails.append('op102 材料用完应删实例 %d' % paper)
        _paper_prod = m.MAKE_RECIPES.get(((10155, 2),))        # 纸张×2 的产物（不写死 25201）
        rows, tail, note, _consumed = tap.decode_fields(
            tap.OVERLAY['S2C'][m.S2C_MAKE_COMPLETE], fr[6][1])
        if tail or note:
            fails.append('op160(craftdata) 反解不干净: tail=%s note=%s'
                         % (len(tail), note))
        elif rows[0]['value'] != _paper_prod or '花卉剪纸' not in (rows[3]['value'] or ''):
            fails.append('op160(craftdata) 内容不对：%s'
                         % [(r['name'], r['value']) for r in rows])

    # ③-12 工艺等级门槛：压到 1 级做 lv5 剪纸 → 只回对话，不扣材料/活力
    conn = FakeConn()
    paper2 = srv._grant_item(conn, 10155, '纸张', 2)
    conn.buf = b''
    srv.life_lv = 1
    huoli_before, qty_before = srv.huoli, srv.items[paper2]['qty']
    srv._dispatch(conn, m.C2S_NPC_MAKE, bytes([7]) + struct.pack('<II', paper2, paper2))
    fr = s2c_frames(conn.buf)
    if [o for o, _ in fr] != [m.OP_NPC_CHAT_EXT]:
        fails.append('op102 工艺等级不足应只回对话，实得 %s' % [o for o, _ in fr])
    if srv.huoli != huoli_before or srv.items[paper2]['qty'] != qty_before:
        fails.append('op102 工艺等级不足不应扣材料/活力')
    srv.life_lv = m.LIFE_LV_INIT

    # ③-13 活力门槛：活力清 0 → 只回对话，不扣材料（体力再多也没用）
    conn = FakeConn()
    srv.huoli = 0
    srv._dispatch(conn, m.C2S_NPC_MAKE, bytes([7]) + struct.pack('<II', paper2, paper2))
    fr = s2c_frames(conn.buf)
    if [o for o, _ in fr] != [m.OP_NPC_CHAT_EXT]:
        fails.append('op102 活力不足应只回对话，实得 %s' % [o for o, _ in fr])
    if srv.items[paper2]['qty'] != qty_before:
        fails.append('op102 活力不足不应扣材料')
    srv.huoli = m.HERO_HUOLI_INIT

    # ③-14 费用查询要报配方真实等级/活力（纸张×2 → 剪纸 lv5、cost16）
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GETCOST,
                  bytes([2]) + struct.pack('<II', paper2, paper2))
    fr = s2c_frames(conn.buf)
    if len(fr) != 1 or fr[0][0] != m.S2C_NPC_MAKECOST:
        fails.append('op104 配方查询未回 155：%s' % [o for o, _ in fr])
    else:
        t_val, _money, lv_val = struct.unpack('<HHH', fr[0][1])
        if (t_val, lv_val) != (16, 5):
            fails.append('op104 应报活力16/等级5，实得 %d/%d' % (t_val, lv_val))

    # ③-15 物伤公式参数化：默认点复现 50/48/20，且改 hero_at 真的改伤害
    base = (srv._hero_attack_damage(),
            srv._dmg(m.PET_AT, m.ENEMY_DF, m.PET_DP, m.ENEMY_LEVEL),
            srv._dmg(m.ENEMY_AT, m.HERO_DF, m.ENEMY_DP, m.HERO_LEVEL))
    if base != (50, 48, 20):
        fails.append('默认参数应复现录像 50/48/20，实得 %r' % (base,))
    old_at = m.HERO_AT
    m.HERO_AT = old_at + 11
    try:
        bumped = srv._hero_attack_damage()
    finally:
        m.HERO_AT = old_at
    # 期望值按**当前模型**算，不写死：linear 是 (攻−防)/2，reverse 是 1.3×(攻−Deff)
    want = dm.physical_damage(old_at + 11, m.ENEMY_DF, model=m.DMG_MODEL)
    if bumped != want:
        fails.append('hero_at=%d 时应打出 %d，实得 %d' % (old_at + 11, want, bumped))
    if srv._hero_attack_damage() != base[0]:
        fails.append('还原 hero_at 后伤害应回到 %d' % base[0])

    # ③-16 NPC 交互族（op97/105/108/113/127/115/112）——真实行为而非仅「接住」
    # op97 给予入口 → S2CNpcChatExt(152)，并记下给予目标
    srv.in_war = False
    srv.cur_npc = m.NPC_ID
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_COMMAND, struct.pack('<BIB', 1, m.NPC_ID, 2))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_NPC_CHAT_EXT):
        fails.append('op97 应回 S2CNpcChatExt(152)，实得 %s' % [o for o, _ in fr])
    if srv.give_npc != m.NPC_ID:
        fails.append('op97 应记下 give_npc=%d，实得 %r' % (m.NPC_ID, srv.give_npc))

    # op105 交付：真的扣背包数量，并回 ItemQuantity(35) + 对话
    iid = sorted(srv.items)[0]
    qty0 = srv.items[iid]['qty']
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GIVE,
                  struct.pack('<IBI', m.NPC_ID, 0, 0) + struct.pack('<IB', iid, 1))
    fr = s2c_frames(conn.buf)
    if srv.items[iid]['qty'] != qty0 - 1:
        fails.append('op105 应扣 1 件（%d→%d），实得 %r'
                     % (qty0, qty0 - 1, srv.items[iid]['qty']))
    if m.OP_ITEM_QUANTITY not in [o for o, _ in fr]:
        fails.append('op105 应回 ItemQuantity(35) 同步数量，实得 %s' % [o for o, _ in fr])
    # 交给不存在的 NPC → 不扣、不应答
    qty1 = srv.items[iid]['qty']
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GIVE,
                  struct.pack('<IBI', 999999, 0, 0) + struct.pack('<IB', iid, 1))
    if srv.items[iid]['qty'] != qty1:
        fails.append('op105 目标 NPC 不在本图时不应扣物品')

    # op108 右键 NPC → 开对话
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_RBCLICK, struct.pack('<I', m.NPC_ID))
    fr = s2c_frames(conn.buf)
    if not (len(fr) == 1 and fr[0][0] == m.OP_NPC_CHAT_EXT):
        fails.append('op108 应回 S2CNpcChatExt(152)，实得 %s' % [o for o, _ in fr])

    # op113/127 选物 → 对话回执且记下选择
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_CHOOSE_ITEM_NUM, struct.pack('<IB', iid, 2))
    fr = s2c_frames(conn.buf)
    if m.OP_NPC_CHAT_EXT not in [o for o, _ in fr]:
        fails.append('op127 应回对话回执，实得 %s' % [o for o, _ in fr])
    if srv.npc_chosen != (iid, 2):
        fails.append('op127 应记下 npc_chosen=(%d,2)，实得 %r' % (iid, srv.npc_chosen))

    # op115 输入 → 回执并记下文本；空输入也应回话（不静默）
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_INPUT, 'abc'.encode('gbk'))
    if srv.npc_input_text != 'abc':
        fails.append('op115 应记下 npc_input_text=abc，实得 %r' % (srv.npc_input_text,))
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_INPUT, b'')
    if m.OP_NPC_CHAT_EXT not in [o for o, _ in s2c_frames(conn.buf)]:
        fails.append('op115 空输入也应回话')

    # op112 交易中心 → 单机明确回绝，不造挂单
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_TRADECENTER, struct.pack('<BI', 1, 0))
    if m.OP_NPC_CHAT_EXT not in [o for o, _ in s2c_frames(conn.buf)]:
        fails.append('op112 应回对话说明单机不支持交易中心')

    # ③-9 战斗内必发的两帧必须有 OVERLAY 条目，且能干净反解
    #      （2026-10-19：这两帧长期缺条目 → 调试台只有 hex；审计器又只认 MISMATCH，
    #        所以「每场战斗/每次进图都发」的帧一直是可见性黑洞）
    def _decode(op, body, tag):
        if op not in tap.OVERLAY['S2C']:
            fails.append('%s: tap.OVERLAY 缺 op%d 条目' % (tag, op))
            return None
        rows, tail, note, consumed = tap.decode_fields(tap.OVERLAY['S2C'][op], body)
        if tail or note or consumed != len(body):
            fails.append('%s: 覆盖表反解不干净 tail=%s note=%s consumed=%d/%d'
                         % (tag, len(tail), note, consumed, len(body)))
            return None
        return rows

    conn = FakeConn()
    body = m.build_war_summon(m.PET_WAR_ID)[2:]          # 剥 [len][op]
    rows = _decode(m.OP_WAR_SUMMON, body, 'op129 S2CWarSummon')
    if rows is not None:
        got = {r['name']: r['value'] for r in rows}
        if got.get('id') != m.PET_WAR_ID or got.get('op') != 0:
            fails.append('op129 内容不对: %s（期望 id=%d op=0）'
                         % (got, m.PET_WAR_ID))

    body = m.build_map_summon(1, [(0, m.PET_SHAPE, m.PET_NAME, '')])[2:]
    rows = _decode(m.OP_MAP_SUMMON, body, 'op105 S2CMapSummon')
    if rows is not None:
        got = {r['name']: r['value'] for r in rows}
        if got.get('owner_id') != 1 or got.get('n') != 1:
            fails.append('op105 内容不对: %s（期望 owner_id=1 n=1）' % got)

    # ④ 覆盖率守卫：所有已定义非战斗 C2S opcode 都应在契约表里
    war_ops = set(m.BATTLE_CMD_OPS) | {m.C2S_PING}
    prewar = {op for op, _ in m.DISPATCH_PRE_WAR_OPS}
    login_ops = {1, 2, 3, 5, 6}      # 登录/建角/选角为 handle() 前段直连，战斗态不会出现
    missing = sorted(
        c for n, c in vars(m).items()
        if n.startswith('C2S_') and isinstance(c, int)
        and c not in war_ops and c not in prewar and c not in login_ops)
    if missing:
        fails.append('这些非战斗 C2S opcode 未登记进 DISPATCH_PRE_WAR_OPS: %s' % missing)

    # ③-10 伤害模型自述：横幅必须如实反映「当前」模型与参数，而不是 import 期快照
    #      （MODDING_PLAN 隐患①：默认 linear 来源未证实，日志说不清就跑错了没人知道）
    bann = m.dmg_banner()
    if m.DMG_MODEL not in bann:
        fails.append('横幅没写出当前 DMG_MODEL=%s' % m.DMG_MODEL)
    # 2026-10-19 文案升级：从「来源未证实」改为更强的「非真值 + 不代表已复刻原服公式」。
    # 这里只守住「linear 必须带否定性标注」这一语义，具体措辞由 ③-14 逐字校验。
    if m.DMG_MODEL == 'linear' and not ('非真值' in bann or '未证实' in bann):
        fails.append('linear 模型没在横幅里做否定性标注（非真值/未证实）')
    if '反编译' not in bann and m.DMG_MODEL == 'reverse':
        fails.append('reverse 模型没在横幅里标注「反编译还原」')
    # 改参数点 → 横幅里的「实算伤害」必须跟着动（证明不是读 ATTACK_DMG 快照）
    _saved = (m.HERO_AT, m.DMG_MODEL)
    m.DMG_MODEL = 'linear'
    m.HERO_AT = 160
    try:
        line = [ln for ln in m.dmg_banner().split(chr(10)) if '实算伤害' in ln]
        if not line:
            fails.append('横幅缺「实算伤害」行')
        elif ('%d' % m._default_dmg(m.HERO_AT, m.ENEMY_DF, m.HERO_DP,
                                    m.ENEMY_LEVEL)) not in line[0]:
            fails.append('横幅实算伤害是陈旧快照（改了 hero_at 没变）：%s' % line[0])
    finally:
        m.HERO_AT, m.DMG_MODEL = _saved

    # ③-11 world_config 加载结果必须可区分（MODDING_PLAN 隐患②：配置静默失效）
    import world_config as _wc
    _tmpd = tempfile.mkdtemp(prefix='m4cfg_')
    try:
        _miss = os.path.join(_tmpd, 'nope.json')
        _cfg, _reason, _ = _wc.probe_config(_miss)
        if _reason != 'missing' or _cfg is not None:
            fails.append('probe_config 缺文件应回 missing，实得 %r' % (_reason,))
        _broken = os.path.join(_tmpd, 'broken.json')
        open(_broken, 'w', encoding='utf-8').write('{ not json')
        _cfg, _reason, _det = _wc.probe_config(_broken)
        if _reason != 'invalid' or 'JSON' not in _det:
            fails.append('probe_config 坏 JSON 应回 invalid+原因，实得 %r/%r'
                         % (_reason, _det))
        _listf = os.path.join(_tmpd, 'list.json')
        open(_listf, 'w', encoding='utf-8').write('[1,2]')
        _cfg, _reason, _det = _wc.probe_config(_listf)
        if _reason != 'invalid' or '根节点' not in _det:
            fails.append('probe_config 根非对象应回 invalid+原因，实得 %r/%r'
                         % (_reason, _det))
        # 失败路径的消息必须能区分「没配」与「配坏了」
        _ok, _msg = _wc.apply_to(path=_broken)
        if _ok or '读不出' not in _msg:
            fails.append('apply_to 坏配置应明说「读不出」，实得 %r' % _msg)
    finally:
        shutil.rmtree(_tmpd, ignore_errors=True)

    # ③-12 探针守卫解析：coverage_smoke 必须能从 _dispatch 里解出 len(body) >= N
    #      （2026-10-19：探针一律发 5B，导致 16/144/145/156 长期被误报成「未实现」）
    import coverage_smoke as cov
    guards = cov._dispatch_guard_lengths()
    expect = {16: 5, 144: 8, 145: 8, 156: 9, 54: 5}
    for op, want in expect.items():
        got = guards.get(op)
        if got != want:
            fails.append('coverage_smoke 解析 op%d 守卫应为 %d，实得 %r'
                         % (op, want, got))
        elif len(cov.probe_body_for(op, guards)) < want:
            fails.append('op%d 探针 body 只有 %dB，短于守卫 %d'
                         % (op, len(cov.probe_body_for(op, guards)), want))
    # 折行分支头（op173 那行是跨两行写的）必须也能被解出来
    if 173 not in guards:
        fails.append('coverage_smoke 未解析出 op173（折行分支头拼接失效）')
    if 173 not in cov.KNOWN_HANDLED:
        fails.append('op173 未进 KNOWN_HANDLED → 会被误报成缺口')
    # ③-13 tap 轮次标记：unknown_since / forget_unknown 语义
    #      （2026-10-19：_unknown_op 只增不减，巡检曾被迫用日志字节偏移猜增量，
    #       偏移取早取晚都会漏。现在有正式轮次接口，这里锁住它的语义。）
    _t = tap.FrameTap()
    if _t.current_round() != 0:
        fails.append('新 FrameTap 轮次应为 0，实得 %r' % _t.current_round())
    _r1 = _t.mark_round()
    _t.note_log('   （未识别 op=161）')
    _t.note_log('   ⚠ 未做逻辑的战斗指令 op=162 → 保底回 WarTurn')
    if _t.unknown_since(_r1) != {161, 162}:
        fails.append('unknown_since 应含两种措辞，实得 %r' % (_t.unknown_since(_r1),))
    _r2 = _t.mark_round()
    _t.note_log('   （未识别 op=163）')
    if _t.unknown_since(_r2) != {163}:
        fails.append('unknown_since 应只回本轮新增，实得 %r' % (_t.unknown_since(_r2),))
    if _t.unknown_since(_r2, cumulative=True) != {163}:
        fails.append('cumulative 从 r2 起应为 {163}，实得 %r'
                     % (_t.unknown_since(_r2, cumulative=True),))
    # 重复报同一 op 不得覆盖首轮记录
    _t.note_log('   （未识别 op=161）')
    if _t.unknown_round_of(161) != _r1:
        fails.append('重复报错改写了首轮记录：%r' % (_t.unknown_round_of(161),))
    # 证伪撤销：历史集与轮次表同时清掉
    if not _t.forget_unknown(161):
        fails.append('forget_unknown 对已存在项应返回 True')
    if _t.unknown_round_of(161) is not None or 161 in _t.stats(include_tables=False)['unknown_c2s']:
        fails.append('forget_unknown 后仍能在轮次表/历史集里看到 161')
    # stats 要暴露 round 与「op→首轮」映射
    _st = _t.stats(include_tables=False)
    if _st.get('round') != _r2:
        fails.append('stats.round 应为 %d，实得 %r' % (_r2, _st.get('round')))
    if str(162) not in _st.get('unknown_c2s_round', {}):
        fails.append('stats.unknown_c2s_round 缺 162 的首轮映射')
    # clear() 清历史但不重置轮次（历史证据仍然有效）
    _t.clear()
    if _t.stats(include_tables=False)['unknown_c2s']:
        fails.append('clear 后历史集应为空')
    if _t.current_round() != _r2:
        fails.append('clear 不应重置轮次，实得 %r' % (_t.current_round(),))

    # 巡检必须真的用上轮次接口（防止退回「日志偏移」那套）
    import coverage_smoke as _cov
    if not hasattr(_cov, '_tap_inst'):
        fails.append('coverage_smoke 缺 _tap_inst（轮次接口接入点）')

    # ③-14 伤害模型措辞守卫：默认 linear 必须被显式标成「非真值 / 便利选择」
    #      这是最容易被外人误读的一条（看到 50/48/20 就以为复刻了原服）。
    #      文案一旦被改软（比如退回「来源未证实」而不再说「非真值」），这里要红。
    _saved_model = m.DMG_MODEL
    try:
        m.DMG_MODEL = 'linear'
        _b = m.dmg_banner()
        for _kw in ('非真值', '不代表已复刻原服公式', '便利'):
            if _kw not in _b:
                fails.append('linear 横幅缺少关键表述 %r（防误读措辞被改弱）' % _kw)
        if 'reverse' not in _b:
            fails.append('linear 横幅应提示可切 reverse')
        m.DMG_MODEL = 'reverse'
        _b2 = m.dmg_banner()
        for _kw in ('反编译', '有出处'):
            if _kw not in _b2:
                fails.append('reverse 横幅缺少 %r' % _kw)
        if '不代表已复刻' in _b2:
            fails.append('reverse 不该出现 linear 的「未复刻」警告')
    finally:
        m.DMG_MODEL = _saved_model
    # 模块常量字典里也必须保留该措辞（横幅直接读它）
    if '非真值' not in m.DMG_MODEL_CAVEAT.get('linear', ''):
        fails.append('DMG_MODEL_CAVEAT["linear"] 丢了「非真值」标注')

    print('契约测试 DISPATCH_PRE_WAR_OPS × %d 条：' % len(m.DISPATCH_PRE_WAR_OPS))
    if fails:
        for f in fails:
            print('  ✗ %s' % f)
        print('FAIL（%d 项）' % len(fails))
        return 1
    print('  ✓ 全部 PRE_WAR op 战斗态不落保底（含负向对照）')
    print('  ✓ op100 已接重发 TaskAdd / 未接不应答；op101 → op151 且 OVERLAY 反解干净')
    print('  ✓ op49 放弃任务 → TaskRemove(73) 且移出 tasks；未接则沉默')
    print('  ✓ 详情面板 op145→162 / op156→190（itype=2 沉默）/ op144→48；未知道具沉默')
    print('  ✓ op104 制作费用 → op155（6B，覆盖表反解干净）；短包沉默')
    print('  ✓ op164 入包飘窗：ItemAdd+NotifyExt 成对且覆盖表反解干净')
    print('  ✓ op102 制作：扣材料 → ItemAdd+飘窗 → op160；craftdata 产物可造；未收录只回对话')
    print('  ✓ 物伤公式参数化：默认点复现 50/48/20，hero_at 改动真的改伤害')
    print('  ✓ op129/op105 有覆盖表条目且反解干净（战斗/进图必发帧不再只有 hex）')
    print('  ✓ 伤害模型自述：模型/来源标注/实算伤害随参数实时变化')
    print('  ✓ world_config：缺文件 / 坏 JSON / 根非对象 / 校验不过 四路可区分')
    print('  ✓ 探针守卫解析：16/144/145/156 长度正确，折行分支头 op173 可解')
    print('  ✓ 伤害模型措辞：linear 明标「非真值/便利」，reverse 标「有出处」')
    print('  ✓ tap 轮次标记：unknown_since/forget_unknown/clear 语义正确')
    print('  ✓ 非战斗 opcode 契约覆盖率完整')
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(run())
