"""对战录像复刻：敌方放招式（WarPerform 122 替代普攻）+ 回合仍正常收尾（2026-10-16）。

对齐录像：野狼放「乱舞冲击」→ 技能弹字+特效（客户端由 WarPerform 自动播放）→
WState(123) 命中飘字 → 回合交还。FakeConn 直接驱动内核方法，不依赖真实端口。
"""
import os, shutil, sys, tempfile
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m

ok = True
def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c

class FakeConn:
    def __init__(self): self.sent = []
    def sendall(self, b): self.sent.append(b)
def ops(c): return [f[1] for f in c.sent]

tmp = tempfile.mkdtemp(prefix='m4skill_')
s = m.M4Server('127.0.0.1', 3171, 0x1234, 5,
               logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
s._loaded = None
s.in_war = True
s.enemy_hp = {1: 100, 2: 100}
s.enemy_speed = {1: 110, 2: 105}
s.hero_hp = 400
s.pet_hp = 350
s._pet_alive = True

# ── 1) 敌方放招式（强制概率 1）──
s.enemy_skill_chance = 1.0
conn = FakeConn()
s._enemy_attack(conn, 1)
o = ops(conn)
check(m.OP_WAR_PERFORM in o, '敌方行动含 WarPerform(122)')
check(m.OP_WAR_ATTACK_NORMAL not in o, '放招式时不发 AttackNormal(117)')
check(m.OP_WAR_WSTATE in o, '命中伤害 WState(123) 保留')
check(m.OP_WAR_HP_PERCENT in o, '友方血条 HPPercent(125) 保留')
check(m.OP_WAR_ATTACK_NORMAL_END in o, '收尾 AttackNormalEnd(119) 保留')
perf = conn.sent[o.index(m.OP_WAR_PERFORM)][2:]
check(len(perf) >= 3 and perf[0] == 1, 'WarPerform id=敌方1 (got %s)' % perf[:3])
# 目标在主角/宠物之间，伤害只作用于一方
hp_total = s.hero_hp + s.pet_hp
check(hp_total < 750, '伤害已结算 (hero=%d pet=%d)' % (s.hero_hp, s.pet_hp))

# ── 2) 敌方普攻（强制概率 0）──
s.enemy_skill_chance = 0.0
conn2 = FakeConn()
s._enemy_attack(conn2, 2)
o2 = ops(conn2)
check(m.OP_WAR_ATTACK_NORMAL in o2 and m.OP_WAR_PERFORM not in o2,
      '普攻路径：AttackNormal 无 Perform (got %s)' % o2)

# ── 3) 回合结算：敌方放招式后仍交还操作权 ──
# ★ 回合末的 WarTurn(0) **不是**「主角动作完就发」：宠物速度 49 < 主角 100，
#   宠物落在「慢位」→ 回合按设计**挂起**等宠物指令（C2S 168-174），此刻不发
#   WarTurn(0)（客户端停在「请等待」）；宠物指令到达 → _finish_pet_turn →
#   结算剩余慢位 → WarTurn(0)。契约见《模拟服全链路代码解析.md》§7.3。
#   （本测试写于 2026-10-16，早于该三段式设计落地，故按现行契约重写。）
s.enemy_skill_chance = 1.0
s.hero_hp, s.pet_hp = 400, 350
conn3 = FakeConn()

def hero_act():
    conn3.sendall(m.build_war_attack_normal(m.HERO_WAR_ID, 1))
    dead = s._damage_frames(conn3, 1, 60)
    conn3.sendall(m.build_war_attack_normal_end(m.HERO_WAR_ID))
    return s._after_hero_action(conn3, dead_ids=[1] if dead else [])

s._resolve_hero_round(conn3, hero_act)
o3 = ops(conn3)
check(getattr(s, '_pending_pet_suffix', None) is not None,
      '宠物在慢位 → 回合挂起等宠物指令 (pending=%s)'
      % (getattr(s, '_pending_pet_suffix', None),))
check(o3.count(m.OP_WAR_TURN) == 0,
      '挂起期间不发回合末 WarTurn（客户端应停在「请等待」）(turns=%d)'
      % o3.count(m.OP_WAR_TURN))

# 宠物指令到达 → 结算剩余慢位 → WarTurn(0) 交还操作权
s._finish_pet_turn(conn3, m.C2S_WAR_SUMM_ATTACK, bytes([m.PET_WAR_ID, 1]))
o3 = ops(conn3)
turns3 = [f for f in conn3.sent if f[1] == m.OP_WAR_TURN]
check(len(turns3) >= 1, '宠物指令结算后 WarTurn 交还操作权 (turns=%d)' % len(turns3))
check(bool(turns3) and turns3[-1][2:] == b'\x00',
      '回合末 WarTurn lastcmd=0（交还操作权 → ShowHeroMenu）')
check(m.OP_WAR_ATTACK_NORMAL in o3, '宠物按 168 指令普攻（AttackNormal 117）')

# ── 4) 三段式：宠物**之前**还有慢位 → 先发 WarTurn(1) 再挂起 ──
s.enemy_hp = {1: 100, 2: 100, 3: 100}
s.enemy_speed = {1: 110, 2: 105, 3: 60}   # 敌3 速度 60：主角(100) 与宠物(49) 之间
s.enemy_skill_chance = 0.0
s.hero_hp, s.pet_hp = 400, 350
conn4 = FakeConn()

def hero_act4():
    conn4.sendall(m.build_war_attack_normal(m.HERO_WAR_ID, 1))
    dead = s._damage_frames(conn4, 1, 60)
    conn4.sendall(m.build_war_attack_normal_end(m.HERO_WAR_ID))
    return s._after_hero_action(conn4, dead_ids=[1] if dead else [])

s._resolve_hero_round(conn4, hero_act4)
turns4 = [f for f in conn4.sent if f[1] == m.OP_WAR_TURN]
check(len(turns4) == 1 and turns4[0][2:] == b'\x01',
      '宠物之前有慢位 → 先发 WarTurn(1)（还有慢位要结算）(turns=%d)' % len(turns4))
check(getattr(s, '_pending_pet_suffix', None) is not None,
      '结算完慢位后仍挂起等宠物指令')
s._finish_pet_turn(conn4, m.C2S_WAR_SUMM_ATTACK, bytes([m.PET_WAR_ID, 1]))
turns4b = [f for f in conn4.sent if f[1] == m.OP_WAR_TURN]
check(len(turns4b) == 2 and turns4b[-1][2:] == b'\x00',
      '宠物指令后 → WarTurn(0) 回合收尾 (turns=%d)' % len(turns4b))

shutil.rmtree(tmp, ignore_errors=True)
print('\n=== 敌方招式复刻冒烟 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
