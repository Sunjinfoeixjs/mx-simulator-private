# -*- coding: utf-8 -*-
"""战斗核心回归：状态机 / 行动合法性 / 重复指令 / 技能资源 / 胜负结算。

只驱动内核方法（FakeConn），不起端口、不依赖外部服务。每段用独立临时存档
目录，互不污染。

覆盖的真实缺陷（都是「不报错、但玩家会卡住/白挨打/重复领奖」的静默问题）：
  ① 开战不可重入 —— 重复 WarStart 会让客户端 g_WarTimes 只增不减，WarEnd 被丢；
  ② 回合窗口闸门 —— 同窗口第二次指令必须被拒，不能结算两次；
  ③ 超时默认动作 —— 旧实现先消费窗口再调 _hero_attack，被「重复指令」吞掉；
  ④ 行动合法性 —— 非法道具/召唤/保护不消耗本回合；越窗宠物指令不偷主角回合；
  ⑤ 技能资源 —— 未学/资源不足拒绝施放且一分不扣；宠物消耗按实例等级算；
  ⑥ 胜负与战后结算 —— 全灭只结算一次；战败/逃跑清干净运行态，不带进下一场。

用法： python tests/_test_battle_core.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402

RESULTS = []
TMPDIRS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('[PASS] %s %s' if ok else '[FAIL] %s %s') % (name, detail))


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))


def ops(conn, since=0):
    return [f[1] for f in conn.sent[since:]]


def new_server(enemies=1, pet=False):
    tmp = tempfile.mkdtemp(prefix='m4core_')
    TMPDIRS.append(tmp)
    s = m.M4Server('127.0.0.1', 0, 0x1234, 100,
                   logdir=os.path.join(tmp, 'logs'),
                   savedir=os.path.join(tmp, 'save'))
    s.enemies = enemies
    s.enemy_skill_chance = 0.0      # 敌方只普攻，帧序确定
    s.enemy_hurt_bs_chance = 0.0    # 不会心
    s.pet_passive_mute = True       # 宠物不连击/不会心
    if not pet:
        s.pets = {}
        s.active_pet_id = 0
        s.pet_hp = 0
    return s


# ── ① 状态机：开战不可重入 ───────────────────────────────────────────
def case_reentry():
    print('\n── ① 状态机：开战不可重入 ──')
    s = new_server()
    c = FakeConn()
    ok1 = s._start_war(c)
    mark = len(c.sent)
    ok2 = s._start_war(c)
    check('首次开战成功', ok1 is True)
    check('战斗中再次开战被拒', ok2 is False)
    check('重复开战不发任何帧（不会二次 WarStart）', len(c.sent) == mark,
          '新增 %d 帧' % (len(c.sent) - mark))
    check('首次开战后状态为「等待指令」',
          s.in_war and s.await_cmd and not s._cmd_consumed)


# ── ② 重复指令防护 ───────────────────────────────────────────────────
def case_duplicate():
    print('\n── ② 重复指令防护 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    s._arm_cmd_timer()
    check('窗口打开：第一次 _accept_turn 放行', s._accept_turn() is True)
    check('同窗口第二次被拒', s._accept_turn() is False)

    before = dict(s.enemy_hp)
    mark = len(c.sent)
    s._hero_attack(c, m.ENEMY_WAR_ID)
    check('重复普攻不结算伤害', s.enemy_hp == before,
          'hp %s → %s' % (before, s.enemy_hp))
    check('重复普攻不发战斗帧', ops(c, mark) == [], str(ops(c, mark)))

    s._arm_cmd_timer()
    check('开新窗口后再次放行', s._accept_turn() is True)


# ── ③ 超时默认动作 ───────────────────────────────────────────────────
def case_timeout_default():
    print('\n── ③ 超时默认动作不被闸门吞掉 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    before = dict(s.enemy_hp)
    mark = len(c.sent)
    s.cmd_deadline = 0.0
    ok = s._timeout_default_action(c)
    seq = ops(c, mark)
    check('超时默认动作执行', ok is True)
    check('超时默认普攻真的结算了伤害', s.enemy_hp[m.ENEMY_WAR_ID] < before[m.ENEMY_WAR_ID],
          'hp %s → %s' % (before, s.enemy_hp))
    check('超时默认普攻发出 117 演出帧', m.OP_WAR_ATTACK_NORMAL in seq, str(seq))
    check('超时结算后回到新窗口',
          s.await_cmd and not s._cmd_consumed and s.cmd_deadline > 0)


# ── ④ 行动合法性 ─────────────────────────────────────────────────────
def case_legality():
    print('\n── ④ 行动合法性：无效指令不消耗回合 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    before = dict(s.enemy_hp)

    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_war_item(c, m.HERO_WAR_ID, 999999)
    check('不存在的道具 → 只回 WarTurn，不推进回合',
          ops(c, mark) == [m.OP_WAR_TURN] and s.enemy_hp == before,
          str(ops(c, mark)))
    check('道具被拒后窗口重新打开（可直接重选）',
          s.await_cmd and not s._cmd_consumed)

    s.hero_hp = m.HERO_HP
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_war_item(c, m.HERO_WAR_ID, 2007)
    check('满血用回血道具 → 拒绝且不扣数量、不推进回合',
          ops(c, mark) == [m.OP_WAR_TURN]
          and s.items[2007]['qty'] == 5 and s.enemy_hp == before,
          '%s qty=%d' % (ops(c, mark), s.items[2007]['qty']))

    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_war_summon(c, 0, 999999)
    check('召唤不存在的宠物 → 只回 WarTurn，不推进回合',
          ops(c, mark) == [m.OP_WAR_TURN] and s.enemy_hp == before,
          str(ops(c, mark)))

    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_war_protect(c, m.HERO_WAR_ID)
    check('保护自己（非法目标）→ 只回 WarTurn，不推进回合',
          ops(c, mark) == [m.OP_WAR_TURN] and s.enemy_hp == before,
          str(ops(c, mark)))

    # 越窗宠物指令：不能偷走主角的指令窗口
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._dispatch(c, m.C2S_WAR_SUMM_ATTACK,
                bytes([m.PET_WAR_ID, m.ENEMY_WAR_ID]))
    check('越窗宠物指令 → 忽略（不发帧、不消费主角窗口）',
          ops(c, mark) == [] and s.await_cmd and not s._cmd_consumed,
          str(ops(c, mark)))


# ── ⑤ 技能资源校验 ───────────────────────────────────────────────────
def case_skill_resource():
    print('\n── ⑤ 技能资源校验 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)

    unlearned = next(k for k in sorted(m.MAGIC_EXPR)
                     if k in m.MAGIC_INFO and k not in s.magics)
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_perform(c, unlearned, m.ENEMY_WAR_ID)
    check('未学招式施放被拒（只回 WarTurn，无 Perform 帧）',
          m.OP_WAR_PERFORM not in ops(c, mark)
          and m.OP_WAR_TURN in ops(c, mark), str(ops(c, mark)))

    pid = m.TEACH_MAGIC_IDS[0]
    s.magics[pid] = 1
    s.hero_res = {k: 0 for k in s.hero_res}
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_perform(c, pid, m.ENEMY_WAR_ID)
    check('资源不足施放被拒（不扣成负数、不发 Perform）',
          m.OP_WAR_PERFORM not in ops(c, mark)
          and all(v == 0 for v in s.hero_res.values()),
          '%s res=%s' % (ops(c, mark), s.hero_res))

    s.hero_res = m.hero_res_fresh()
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._hero_perform(c, pid, m.ENEMY_WAR_ID)
    check('资源充足施放成功并扣本级消耗',
          m.OP_WAR_PERFORM in ops(c, mark)
          and s.hero_res != m.hero_res_fresh(), str(s.hero_res))

    # 宠物招式消耗按**实例等级**：LV/3 是 Py2 整除，30 级比 1 级更贵
    sp = new_server(pet=True)
    p = sp._pet()
    p['level'] = 1
    need1 = sp._pet_skill_need(415)
    p['level'] = 30
    need30 = sp._pet_skill_need(415)
    check('宠物招式消耗随实例等级增长',
          need30[0] > need1[0], 'lv1=%s lv30=%s' % (need1, need30))


# ── ⑥ 胜负与战后结算 ─────────────────────────────────────────────────
def case_victory():
    print('\n── ⑥ 胜利结算（幂等） ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    s.enemy_hp[m.ENEMY_WAR_ID] = 1       # 一击必杀，缩短链路
    exp0 = s.exp
    mark = len(c.sent)
    s._hero_attack(c, m.ENEMY_WAR_ID)
    seq = ops(c, mark)
    check('击杀后战斗结束', not s.in_war and s.enemy_hp == {})
    check('胜利发经验一次', s.exp - exp0 == m.BATTLE_EXP_PER_ENEMY,
          '+%d' % (s.exp - exp0))
    check('胜利帧含 WarEnd(113)', m.OP_WAR_END in seq, str(seq))
    check('收尾后运行态全部清空',
          s.await_cmd is False and s._cmd_consumed is False
          and s._pending_pet_suffix is None and s._escape_failed is False
          and not s._round_defending and not s._round_protectors
          and s.cmd_deadline == 0.0)

    exp1 = s.exp
    mark = len(c.sent)
    s._after_hero_action(c, dead_ids=[])
    check('战后二次结算被幂等拦下（不重复发经验/WarEnd）',
          s.exp == exp1 and m.OP_WAR_END not in ops(c, mark),
          '%s +%d' % (ops(c, mark), s.exp - exp1))


def case_defeat():
    print('\n── ⑥ 战败与逃跑：清场彻底 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    s.hero_hp = 1
    s._escape_failed = True
    s._round_defending.add(m.HERO_WAR_ID)
    s._round_protectors[m.PET_WAR_ID] = m.HERO_WAR_ID
    s._pending_pet_suffix = [m.ENEMY_WAR_ID]
    exp0 = s.exp
    mark = len(c.sent)
    s._enemy_attack(c, m.ENEMY_WAR_ID)
    check('主角倒地 → 战败收尾', s.hero_hp == 0 and not s.in_war)
    check('战败帧含 WarEnd(113)', m.OP_WAR_END in ops(c, mark), str(ops(c, mark)))
    check('战败不发经验', s.exp == exp0)
    check('战败清空跨场残留状态',
          s.await_cmd is False and s._cmd_consumed is False
          and s._pending_pet_suffix is None and s._escape_failed is False
          and not s._round_defending and not s._round_protectors
          and s.enemy_hp == {})

    # 逃跑失败：只允许敌方白打**一轮**（旧实现会再走一遍速度序）
    s = new_server(enemies=2)
    c = FakeConn()
    s._start_war(c)
    mark = len(c.sent)
    s._escape_failed = True
    s._resolve_hero_round(c, lambda: False)
    seq = ops(c, mark)
    attacks = [op for op in seq if op == m.OP_WAR_ATTACK_NORMAL]
    check('逃跑失败只结算一轮敌方行动', len(attacks) == 2,
          'AttackNormal=%d seq=%s' % (len(attacks), seq))
    check('逃跑失败后交还操作权',
          m.OP_WAR_TURN in seq and s.await_cmd and not s._cmd_consumed)

    # 逃跑成功：走统一清场
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    s._escape_force = 'success'
    s._arm_cmd_timer()
    mark = len(c.sent)
    s._dispatch(c, m.C2S_WAR_ESCAPE, b'')
    check('逃跑成功 → WarEnd 且状态清空',
          m.OP_WAR_END in ops(c, mark) and not s.in_war
          and s.enemy_hp == {} and s._escape_failed is False
          and s._pending_pet_suffix is None,
          str(ops(c, mark)))


def main():
    case_reentry()
    case_duplicate()
    case_timeout_default()
    case_legality()
    case_skill_resource()
    case_victory()
    case_defeat()

    bad = [n for (n, ok) in RESULTS if not ok]
    print('\n%d 项断言，失败 %d 项' % (len(RESULTS), len(bad)))
    if bad:
        print('失败：' + ' / '.join(bad))
    print('=== 战斗核心（状态机/合法性/重复指令/技能资源/胜负结算）%s ==='
          % ('PASS' if not bad else 'FAIL'))
    return 1 if bad else 0


if __name__ == '__main__':
    try:
        rc = main()
    finally:
        for d in TMPDIRS:
            shutil.rmtree(d, ignore_errors=True)
    sys.exit(rc)
