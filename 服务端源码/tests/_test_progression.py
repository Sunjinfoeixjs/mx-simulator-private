"""养成闭环冒烟：升级(17) / 加点(16) / 学招式(103) + 教学列表(149) + 存档往返（M9）。

沿用 `_test_task.py` 的做法：直接调 M4Server 方法与帧构造器，用 FakeConn 捕获下发字节，
不起端口、不依赖真实客户端。存档与日志写临时目录，不污染真实存档。

编码约定（客户端源码为据）：
  · 三条上行都不需要专用 S2C 应答，一律用 S2CPropChange(17) 改 ITEM_ALL 对应下标：
      5=m_Grade(1B) 8=m_Exp(4B) 9=m_NextExp(4B) 37=m_Point(2B) 32..36=五维(2B)
  · 学招式要两帧 S2CSkillLevel(65)：sktype=2 学会（写 ST_MAGIC）、sktype=7 装备（写 MAGICREADY）
"""
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(b)


def frame_at(conn, op, nth=0):
    """取第 nth 个指定 opcode 的帧，返回 (body, 整帧)。"""
    hit = [f for f in conn.sent if f[1] == op]
    if len(hit) <= nth:
        return None, None
    return hit[nth][2:], hit[nth]


def props(conn):
    """把本次发出的所有 PropChange 帧解成 {下标: 值}（同下标取最后一次）。"""
    out = {}
    for f in conn.sent:
        if f[1] != m.OP_PROP_CHANGE:
            continue
        t = f[2]
        body = f[3:]
        width = {1: 1, 5: 1, 8: 4, 9: 4, 32: 2, 33: 2, 34: 2, 35: 2, 36: 2,
                 37: 2, 43: 4}.get(t, len(body))
        out[t] = int.from_bytes(body[:width], 'little')
    return out


def make_server(tmp):
    s = m.M4Server('127.0.0.1', 3179, 0x1234, 5, enemies=1, turn_timeout=30,
                   logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    s._loaded = None
    return s


print('=' * 70)
print(' A. 帧格式：S2CNpcLearnList(149)')
print('=' * 70)
fr = m.build_npc_learn_list([(1104, 0, 255, 0), (1105, 1, 255, 2)])
check(fr[1] == m.OP_NPC_LEARN_LIST == 149, 'opcode=149 (got %d)' % fr[1])
check(fr[0] == len(fr) - 1, '帧头长度字段 = 帧长-1（含 opcode）')
body = fr[2:]
check(body[0] == 0 and struct.unpack('<H', body[1:3])[0] == 0,
      'body 前 3B = mode(1B)=0 + icost(2B)=0')
skid, lim, valid, free = struct.unpack('<HBBB', body[3:8])
check((skid, lim, valid, free) == (1104, 0, 255, 0),
      '条目 = skid(2B)+npcLimit(1B)+validLearn(1B)+freeLevel(1B) → %r'
      % ((skid, lim, valid, free),))
check(body[-2:] == b'\x00\x00', '以 skid=0(2B) 哨兵结束')
check(len(body) == 3 + 5 * 2 + 2, '两条目 body 长度 = 3+5×2+2 = %d（实测 %d）'
      % (3 + 5 * 2 + 2, len(body)))

print()
print('=' * 70)
print(' B. 升级 C2S_PROP_LEVELUP(17)')
print('=' * 70)
tmp = tempfile.mkdtemp(prefix='m4prog_')
try:
    s = make_server(tmp)
    c = FakeConn()
    check(s.grade == 1 and s.points == m.POINT_INIT,
          '初始 Lv%d、可分配 %d 点' % (s.grade, s.points))

    s.exp = 100
    check(s._hero_level_up(c) is False and not c.sent,
          '经验不足（100/%d）时拒绝且不下发任何帧' % s.next_exp)

    s.exp = 1000
    c.sent = []
    check(s._hero_level_up(c) is True, '经验够 → 升级成功')
    p = props(c)
    pr = props(c)
    check(p.get(5) == 2, '下发 PropChange(m_Grade=5, 2, 1B)（实得 %r）' % p.get(5))
    check(props(c).get(8) == 0, '剩余经验归零 PropChange(m_Exp=8, 0)')
    check(props(c).get(9) == 2000, '下一级所需 = 1000×等级 → 2000')
    check(pr.get(37) == m.POINT_INIT + m.POINT_PER_LEVEL,
          '点数 %d → %d（每级 +%d）' % (m.POINT_INIT, pr.get(37), m.POINT_PER_LEVEL))
    check(s.grade == 2 and s.exp == 0 and s.next_exp == 2000, '内部状态一致')

    s.grade = m.GRADE_MAX
    s.exp = 10 ** 6
    c.sent = []
    check(s._hero_level_up(c) is False and not c.sent, '满级 %d 时拒绝' % m.GRADE_MAX)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('=' * 70)
print(' C. 加点 C2S_PROP_POINT(16)')
print('=' * 70)
tmp = tempfile.mkdtemp(prefix='m4prog_')
try:
    s = make_server(tmp)
    c = FakeConn()
    s.points = 10
    before = {i: s.attrs[i] for i, _ in m.ATTR_ORDER}
    # 顺序：体(36) 力(32) 魔(33) 魅(34) 敏(35)
    check(s._hero_add_points(c, bytes([2, 1, 3, 0, 0])) is True, '加点成功（体+2 力+1 魔+3）')
    p = props(c)
    check(p.get(36) == before[36] + 2, 'PropChange(m_Con_All=36) → %d' % p.get(36))
    check(props(c).get(32) == before[32] + 1, 'PropChange(m_Str_All=32) → %d'
          % props(c).get(32))
    check(props(c).get(33) == before[33] + 3, 'PropChange(m_Mag_All=33) → %d'
          % props(c).get(33))
    check(props(c).get(37) == 10 - 6, '可用点数 10 → 4')
    touched = set(props(c))
    check(touched == {36, 32, 33, 37},
          '只下发变化的三项 + 点数（实为 %s，未加的两项不刷）' % sorted(touched))

    c.sent = []
    check(s._hero_add_points(c, bytes([100, 0, 0, 0, 0])) is False and not c.sent,
          '点数不足时整包拒绝，不做部分生效')
    check(s.points == 4 and s.attrs[36] == before[36] + 2, '拒绝后状态未变')

    # 上限钳制：客户端判 <255，服务端也不许越过
    s.attrs[36] = m.ATTR_MAX - 1
    s.points = 10
    c.sent = []
    check(s._hero_add_points(c, bytes([5, 0, 0, 0, 0])) is True, '越界加点只吃到上限')
    check(s.attrs[36] == m.ATTR_MAX, '体力钳到 %d（实得 %d）' % (m.ATTR_MAX, s.attrs[36]))
    check(s.points == 9, '只扣实际生效的 1 点（余 %d）' % s.points)

    # 力量 → 普攻伤害
    s.attrs[32] = m.ATTR_BASE
    base_dmg = s._hero_attack_damage()
    s.attrs[32] = m.ATTR_BASE + m.STR_DMG_DIVISOR * 3
    up_dmg = s._hero_attack_damage()
    check(base_dmg == m.ATTACK_DMG,
          '未加点时普攻伤害 == 基准常量 %d（不污染录像校准值）' % m.ATTACK_DMG)
    check(up_dmg == m.ATTACK_DMG + 3, '力量 +%d → 普攻 +3（实得 %d）'
          % (m.STR_DMG_DIVISOR * 3, up_dmg))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('=' * 70)
print(' D. 学招式 C2S_NPC_LEARN_SKILL(103)')
print('=' * 70)
tmp = tempfile.mkdtemp(prefix='m4prog_')
try:
    s = make_server(tmp)
    c = FakeConn()
    s.coins = 1000
    s._send_learn_list(c)
    f = c.sent[-1]
    n = (len(f) - 2 - 3 - 2) // 5
    check(f[1] == 149 and n == len(m.LEARN_MAGIC_IDS),
          '课表含 %d 门招式（%s）' % (n, list(m.LEARN_MAGIC_IDS)))

    c.sent = []
    check(s._hero_learn_magic(c, 1104) is True, '学会 1104（1 级）')
    check(props(c).get(43) == 1000 - s._learn_cost(1),
          '扣学费 %d → 余 %d' % (s._learn_cost(1), props(c).get(43)))
    b1, _ = frame_at(c, m.OP_SKILL_LEVEL, 0)
    b2, _ = frame_at(c, m.OP_SKILL_LEVEL, 1)
    check(b1 is not None and b1[0] == 2, '第一帧 sktype=2（学会 → ST_MAGIC）')
    check(b1 is not None and struct.unpack('<H', b1[1:3])[0] == 1104 and b1[3] == 1,
          'sktype=2：skid=1104 level=1')
    check(b1 is not None and len(b1) == 13,
          'sktype=2 body = sktype(1)+skid(2)+level(1)+bid(2)+point(2)+cash(4)+iHighLv(1) = 13B')
    check(b2 is not None and b2[0] == 7 and struct.unpack('<H', b2[1:3])[0] == 1104,
          '第二帧 sktype=7（装备进战斗菜单 → MAGICREADY）')
    check(b2 is not None and b2[3] == 1, 'sktype=7 带同一等级（否则菜单显示 1 级）')

    c.sent = []
    check(s._hero_learn_magic(c, 1104) is True and s.magics[1104] == 2, '再学一次 → 2 级')
    b2, _ = frame_at(c, m.OP_SKILL_LEVEL, 1)
    check(b2 is not None and b2[3] == 2, '升级后装备帧带 2 级')

    c.sent = []
    check(s._hero_learn_magic(c, 1104) is True and s.magics[1104] == 3, '第三次 → 3 级')
    c.sent = []
    check(s._hero_learn_magic(c, 1104) is False and not c.sent,
          '到 %d 级上限后拒绝' % m.LEARN_MAGIC_MAX_LEVEL)

    c.sent = []
    check(s._hero_learn_magic(c, 4321) is False and not c.sent, '课表外招式拒绝')

    s.coins = 0
    s.magics.pop(1105, None)
    c.sent = []
    check(s._hero_learn_magic(c, 1105) is False and not c.sent, '钱不够拒绝')
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('=' * 70)
print(' E. 战斗经验与存档往返')
print('=' * 70)
tmp = tempfile.mkdtemp(prefix='m4prog_')
try:
    s = make_server(tmp)
    c = FakeConn()
    s.enemies = 3
    s.exp = 0
    s._award_battle_exp(c)
    check(s.exp == m.BATTLE_EXP_PER_ENEMY * 3,
          '胜利发经验 3×%d = %d' % (m.BATTLE_EXP_PER_ENEMY, s.exp))
    check(props(c).get(8) == s.exp, '并同步 PropChange(m_Exp)')

    s.grade, s.points = 7, 12
    s.attrs = {i: 20 + n for n, (i, _x) in enumerate(m.ATTR_ORDER)}
    s.magics[1106] = 2
    s.coins = 777
    s._persist()

    s2 = m.M4Server('127.0.0.1', 3180, 0x1234, 5, enemies=1, turn_timeout=30,
                    logdir=os.path.join(tmp, 'logs2'), savedir=os.path.join(tmp, 'save'))
    check(s2.grade == 7 and s2.points == 12, '重载 grade=%d points=%d' % (s2.grade, s2.points))
    check(all(s2.attrs[i] == 20 + n for n, (i, _x) in enumerate(m.ATTR_ORDER)),
          '重载五维 = %s' % [s2.attrs[i] for i, _x in m.ATTR_ORDER])
    check(s2.magics.get(1106) == 2, '重载学过的招式 1106 → %r 级' % s2.magics.get(1106))
    check(s2.coins == 777, '重载金钱 %d' % s2.coins)
    for _mid in m.TEACH_MAGIC_IDS:
        if _mid not in s2.magics:
            check(False, '基线招式 %d 丢失' % _mid)
            break
    else:
        check(True, '基线 %d 式仍在已学表里（登录仍会下发）' % len(m.TEACH_MAGIC_IDS))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('=' * 70)
print(' 养成闭环回归 %s' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
