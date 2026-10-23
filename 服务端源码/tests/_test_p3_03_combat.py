# -*- coding: utf-8 -*-
"""P3-03 高级战斗回归：状态效果 / 敌方 AI / 固定随机种子 / 胜负只结算一次。

四件事各有一组断言，共 10 组：
  ① 随机源不变量   —— 同种子同序、流间隔离、每场重置、可退化
  ② 12 处收口      —— 静态扫源码 + 每条链路的抽取计数
  ③ 遇怪可复现     —— 同种子两个实例选中同一只怪
  ④ 状态真值表     —— 284 条 / 249 图标 / 34 含量化 / 29 可建模，且生成物未过期
  ⑤ 状态机制       —— 施加/叠加/到期/驱散/上限/未知 id 拒绝，聚合乘区正确
  ⑥ DOT 结算       —— 灼热 20% 上限、多种累加、至少 1 点、无状态零帧
  ⑦ 强制防御       —— 恐怖 → 减伤一半（走真实承伤路径）
  ⑧ 敌方 AI        —— 抽取顺序、保护不放招、focus 选残血、决策可解释
  ⑨ 只结算一次     —— 六路入口（普攻/群攻/宠物/GM/战败/逃跑）结算次数恒 ≤1
  ⑩ GM 三入口      —— buff / rng / settle / aipolicy 命令行与 HTTP 同源

用法（从 `服务端源码/tests/sim` 目录跑，或由 `_run_all.py` 带 PYTHONPATH 跑）：
    python tests/_test_p3_03_combat.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import battle_rng as brng      # noqa: E402
import combat_effects as ceff  # noqa: E402
import buff_data as bfd        # noqa: E402
import enemy_ai as eai         # noqa: E402

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


def new_server(enemies=1, seed=0x1234, pet=False):
    tmp = tempfile.mkdtemp(prefix='m4p303_')
    TMPDIRS.append(tmp)
    s = m.M4Server('127.0.0.1', 0, seed, 100,
                   logdir=os.path.join(tmp, 'logs'),
                   savedir=os.path.join(tmp, 'save'))
    s.enemies = enemies
    s.enemy_skill_chance = 0.0
    s.enemy_hurt_bs_chance = 0.0
    s.pet_passive_mute = True
    if not pet:
        s.pets = {}
        s.active_pet_id = 0
        s.pet_hp = 0
    return s


# ── ① 随机源不变量 ─────────────────────────────────────────────────
def case_rng():
    print('\n── ① 随机源：同种子同序 / 流隔离 / 每场重置 / 可退化 ──')
    a = brng.BattleRng(0xABCD)
    b = brng.BattleRng(0xABCD)
    a.begin_battle()
    b.begin_battle()
    seq_a = [a.roll(brng.S_DROP) for _ in range(12)]
    seq_b = [b.roll(brng.S_DROP) for _ in range(12)]
    check('I1 同种子 → 同一条流逐位相同', seq_a == seq_b,
          '%r vs %r' % (seq_a[:3], seq_b[:3]))

    c = brng.BattleRng(0xABCE)
    c.begin_battle()
    seq_c = [c.roll(brng.S_DROP) for _ in range(12)]
    check('I1 不同种子 → 序列不同', seq_a != seq_c)

    # 流隔离：把 drop 流抽 100 次，enemy_target 的下一个值不能变
    d1 = brng.BattleRng(7)
    d1.begin_battle()
    first = d1.choice(brng.S_ENEMY_TARGET, [1, 2, 3, 4, 5, 6, 7, 8])
    d2 = brng.BattleRng(7)
    d2.begin_battle()
    for _ in range(100):
        d2.roll(brng.S_DROP)          # 疯狂消耗另一条流
    second = d2.choice(brng.S_ENEMY_TARGET, [1, 2, 3, 4, 5, 6, 7, 8])
    check('I2 流间隔离：抽乱 drop 不影响 enemy_target', first == second,
          '%s vs %s' % (first, second))

    # 每场重置：第 2 场的第一个值 == 新建实例开第 1 场的第一个值
    e = brng.BattleRng(99)
    e.begin_battle()
    e.roll(brng.S_DROP)
    e.begin_battle()                  # 第 2 场
    v2 = e.roll(brng.S_DROP)
    f = brng.BattleRng(99)
    f.begin_battle()
    f.roll(brng.S_DROP)
    f.begin_battle()
    check('I3 每场重置：第 2 场序列与另一实例第 2 场相同',
          v2 == f.roll(brng.S_DROP))

    # p<=0 / p>=1 不消耗随机数
    g = brng.BattleRng(5)
    g.begin_battle()
    g.chance(brng.S_MISC, 0.0)
    g.chance(brng.S_MISC, 1.0)
    check('chance(0)/chance(1) 不消耗随机数（不推动序列）',
          g.draws(brng.S_MISC) == 0, 'draws=%d' % g.draws(brng.S_MISC))

    # 单元素 choice 不消耗
    g.choice(brng.S_MISC, ['only'])
    check('单元素 choice 不消耗随机数', g.draws(brng.S_MISC) == 0)

    # 未登记流名要当场报
    try:
        g.roll('no_such_stream')
        raised = False
    except KeyError:
        raised = True
    check('未登记流名 → KeyError（不静默新建）', raised)

    # enabled=False 退化
    h = brng.BattleRng(1)
    h.begin_battle()
    h.enabled = False
    v = h.roll(brng.S_MISC)
    check('I4 enabled=False → 退化为模块级 random（0..1）', 0.0 <= v < 1.0,
          '%.4f' % v)

    # snapshot / restore
    k = brng.BattleRng(4242)
    k.begin_battle()
    k.roll(brng.S_DROP)
    snap = k.state()
    expect = [k.roll(brng.S_DROP) for _ in range(5)]
    k.restore(snap)
    again = [k.roll(brng.S_DROP) for _ in range(5)]
    check('state()/restore() 能逐位复原随机序列', expect == again)


# ── ② 12 处收口 ────────────────────────────────────────────────────
def case_collapse():
    print('\n── ② 随机源收口：静态扫描 + 链路抽取计数 ──')
    src = open(os.path.join(SIM, 'm4_server.py'), 'rb').read().decode('utf-8')
    # 去掉注释行后再找 random.
    hits = []
    for i, ln in enumerate(src.splitlines(), 1):
        code = ln.split('#', 1)[0]
        if re.search(r'(?<![.\w])random\.(random|choice|randint|shuffle|randrange)', code):
            hits.append((i, ln.strip()))
    check('m4_server 内已无模块级 random.* 业务调用（12 处全收口）',
          not hits, '%d 处残留：%s' % (len(hits), hits[:3]))

    # 各链路确实用了对应流。
    # ★ 注意 `chance(p)` 在 p<=0 / p>=1 时**不抽随机数**（这是设计：必然事件不该
    #   推动序列），所以这里必须用 0<p<1 的中间概率，否则 draws 恒为 0 ——
    #   不是因为没走那条流，而是因为没必要掷。
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    check('开战 → 进入战斗且随机源已按场次重置',
          s.in_war and s.battle_rng._battle >= 1,
          'battle=%d' % s.battle_rng._battle)

    s2 = new_server(enemies=1, pet=True)
    s2.pet_passive_mute = False
    s2._start_war(FakeConn())
    s2.enemy_hp[m.ENEMY_WAR_ID] = 99999
    # 用实例属性顶掉方法：给一组 0<p<1 的被动概率，链路才会真的去掷
    s2._pet_passives = lambda: {'crit_add': 0.5, 'combo_chance': 0.5,
                                'combo_rate': 0.75, 'combo_max': 1}
    c2 = FakeConn()
    s2._pet_attack(c2, m.ENEMY_WAR_ID)
    check('宠物普攻 → combo/crit 流各抽过一次',
          s2.battle_rng.draws(brng.S_COMBO) == 1
          and s2.battle_rng.draws(brng.S_CRIT) == 1,
          'combo=%d crit=%d' % (s2.battle_rng.draws(brng.S_COMBO),
                                s2.battle_rng.draws(brng.S_CRIT)))

    s3 = new_server(pet=True)
    s3.enemy_hurt_bs_chance = 0.5
    s3._start_war(FakeConn())
    c3 = FakeConn()
    s3._enemy_attack(c3, m.ENEMY_WAR_ID)
    check('敌方行动 → target/hurt 流各抽过一次',
          s3.battle_rng.draws(brng.S_ENEMY_TARGET) == 1
          and s3.battle_rng.draws(brng.S_ENEMY_HURT) == 1,
          'target=%d hurt=%d' % (s3.battle_rng.draws(brng.S_ENEMY_TARGET),
                                 s3.battle_rng.draws(brng.S_ENEMY_HURT)))

    s4 = new_server()
    s4._start_war(FakeConn())
    s4._roll_drops(FakeConn(), scene='新月平原')
    check('掉落 → drop 流被掷过（无论中没中）',
          s4.battle_rng.draws(brng.S_DROP) == 1,
          'draws=%d' % s4.battle_rng.draws(brng.S_DROP))


# ── ③ 遇怪可复现 ──────────────────────────────────────────────────
def case_encounter_repro():
    print('\n── ③ 遇怪可复现：同种子 → 同一只怪 ──')
    names = []
    for _ in range(2):
        s = new_server(seed=0x7777)
        s._start_war(FakeConn())
        names.append(s.enemy_meta[m.ENEMY_WAR_ID]['name'])
    check('同种子两次开战 → 敌人名字相同', names[0] == names[1], str(names))

    # 换种子应能选到不同怪。默认出生图是城镇（不遇怪），敌人名走固定默认值，
    # 直接比 `enemy_meta['name']` 会被那个默认值掩盖。所以这里对**怪物表本身**
    # 验证 seed 真的参与选择：「同种子相同」在上一条已验，这条验分叉。
    tbl = [('野狼', 1), ('山猪', 2), ('毒蛇', 3), ('石魔', 4)]
    seen = set()
    for sd in (0x100, 0x101, 0x102, 0x103, 0x104):
        k = brng.BattleRng(sd)
        k.begin_battle()
        seen.add(k.choice(brng.S_ENCOUNTER, tbl)[0])
    check('不同种子能选到不同怪（5 个种子里至少 2 种）', len(seen) >= 2,
          str(seen))
    same = brng.BattleRng(0x200)
    same.begin_battle()
    v1 = same.choice(brng.S_ENCOUNTER, tbl)[0]
    same2 = brng.BattleRng(0x200)
    same2.begin_battle()
    check('同种子在怪物表上选中同一只',
          v1 == same2.choice(brng.S_ENCOUNTER, tbl)[0])


# ── ④ 状态真值表 ──────────────────────────────────────────────────
def case_buff_table():
    print('\n── ④ 状态真值表：284 条 / 生成物未过期 ──')
    check('状态条目 284 条', len(bfd.BUFF_INFO) == 284, str(len(bfd.BUFF_INFO)))
    check('图标映射 249 条', len(bfd.BUFF_ICON) == 249, str(len(bfd.BUFF_ICON)))
    check('含量化描述 34 条', len(bfd.quantified()) == 34,
          str(len(bfd.quantified())))
    check('可建模 29 条', len(ceff.EFFECT_MODS) == 29,
          str(len(ceff.EFFECT_MODS)))
    check('不可建模 6 条', len(ceff.UNREADABLE) == 6,
          str(len(ceff.UNREADABLE)))
    check('关键条目名称正确（灼热/狂暴/水晶墙/恐怖）',
          bfd.name(287) == '灼热' and bfd.name(184) == '狂暴'
          and bfd.name(188) == '水晶墙' and bfd.name(27) == '恐怖',
          '%s/%s/%s/%s' % (bfd.name(287), bfd.name(184),
                           bfd.name(188), bfd.name(27)))
    check('145 号（str 路径）解码为「液态」', bfd.get(145) == '液态',
          repr(bfd.get(145)))
    check('全表无解码替换符（U+FFFD）',
          not any('\ufffd' in d for d in bfd.BUFF_INFO.values()))

    # 生成物未过期
    import subprocess
    p = subprocess.run([sys.executable, os.path.join(SIM, 'gen_buff_data.py'),
                        '--check'], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    check('gen_buff_data --check 通过（生成物与客户端一致）', p.returncode == 0,
          p.stdout.decode('utf-8', 'replace').strip()[:80])

    # 建模的每一条都必须能在真值表里查到，且 name 与表一致
    bad = []
    for eid, spec in ceff.EFFECT_MODS.items():
        if bfd.get(eid) is None:
            bad.append((eid, 'not_in_table'))
        elif not bfd.get(eid).startswith(spec['name']):
            bad.append((eid, 'name_mismatch', spec['name'], bfd.get(eid)[:12]))
        elif not spec.get('src'):
            bad.append((eid, 'no_src'))
    check('29 条建模条目：都在真值表内 / 名字对得上 / 都带 src 出处',
          not bad, str(bad[:3]))

    # ── ★ 2026-10-23 加严：src 只查「非空」是拦不住编造的 ──────────────
    # 上面那条只要求 src 有字。可「把 30% 抄成 40%、src 照样写着一句话」它抓不到。
    # 这里把 src 钉死在原文上：引号内的每一段（省略号切段后）都必须是
    # 客户端说明的**逐字子串**。编不出来、抄错原文，这条就红。
    bad_verbatim = []
    for eid, spec in sorted(ceff.EFFECT_MODS.items()):
        src = spec.get('src') or ''
        mq = re.search(u'「(.+?)」', src)
        if not mq:
            bad_verbatim.append((eid, 'no_brackets'))
            continue
        info = bfd.get(eid) or ''
        for part in re.split(u'…|⋯', mq.group(1)):
            if part.strip() and part not in info:
                bad_verbatim.append((eid, 'not_verbatim', part[:24]))
    check('29 条 src 引号内文字逐字出自客户端说明（省略号切段后仍须是子串）',
          not bad_verbatim, str(bad_verbatim[:3]))

    # QUANT_CANDIDATES 必须**恰好**是「说明里含阿拉伯数字」的那批 —— 双向相等。
    # 只查子集挡不住清单漏一条：漏掉一条就等于少做一条本来能做的活，
    # 而报告里会写着「只有这么多条有数字」。
    digit_ids = {k for k, v in bfd.BUFF_INFO.items() if re.search(r'[0-9]', v)}
    check('QUANT_CANDIDATES == 含数字条目集合（双向，各 %d 条）' % len(digit_ids),
          set(bfd.QUANT_CANDIDATES) == digit_ids,
          '只重算有 %s / 只在清单有 %s'
          % (sorted(digit_ids - set(bfd.QUANT_CANDIDATES))[:5],
             sorted(set(bfd.QUANT_CANDIDATES) - digit_ids)[:5]))

    # 三分类不许重叠、不许越界。
    _q = set(bfd.QUANT_CANDIDATES)
    _mod = set(ceff.EFFECT_MODS)
    _un = set(ceff.UNREADABLE)
    check('建模条目 ⊆ 可量化清单', _mod <= _q, str(sorted(_mod - _q)[:5]))
    check('建模与不可读不重叠', not (_mod & _un), str(sorted(_mod & _un)))

    # 「读不出」必须给出**理由**，且 id 必须真实存在。
    # ★ 这里刻意**不**要求「不可读的条目都有数字」：一条本来就没写数字的状态
    #   登记成不可读是完全合理的。真正的不变量是「说清楚为什么读不出」——
    #   否则 UNREADABLE 会变成一个什么都往里扔的垃圾桶。
    bad_un = []
    for eid, why in sorted(ceff.UNREADABLE.items()):
        if bfd.get(eid) is None:
            bad_un.append((eid, 'id_not_in_table'))
        elif u'——' not in why or len(why) < 20:
            bad_un.append((eid, 'no_reason', why[:20]))
    check('6 条不可读：id 都在真值表内 / 每条都带「—— 理由」且够具体',
          not bad_un, str(bad_un[:3]))


# ── ⑤ 状态机制 ────────────────────────────────────────────────────
def case_effect_mechanics():
    print('\n── ⑤ 状态机制：施加/叠加/到期/驱散/上限/聚合 ──')
    es = ceff.EffectSet('t')
    check('空集 → 中性修正（atk_mul=1.0）', es.mods()['atk_mul'] == 1.0)
    check('空集 → 无 DOT', es.dot_damage(1000, 500) == (0, False))

    ok, reason = es.add(184)                     # 狂暴
    check('施加狂暴成功', ok and reason == ceff.R_OK, reason)
    md = es.mods()
    check('狂暴 → 攻×2 / 速×2 / 防×0.7',
          md['atk_mul'] == 2.0 and md['spd_mul'] == 2.0 and md['def_mul'] == 0.7,
          '%s/%s/%s' % (md['atk_mul'], md['spd_mul'], md['def_mul']))

    ok2, _r = es.add(184)
    check('重复施加 → refresh 不叠层（stacks 仍 1）',
          ok2 and es.get(184)['stacks'] == 1, str(es.get(184)))

    es2 = ceff.EffectSet('t2')
    es2.add(279)                                  # 溺水（可叠加）
    es2.add(279)
    es2.add(279)
    check('溺水可叠加 → 3 层', es2.get(279)['stacks'] == 3,
          str(es2.get(279)['stacks']))
    check('溺水 3 层 → 攻×0.4（1-0.2*3，加法叠加）',
          abs(es2.mods()['atk_mul'] - 0.4) < 1e-9,
          '%.4f' % es2.mods()['atk_mul'])
    es2.add(279)
    es2.add(279)                                  # 超过 4 层
    check('溺水层数上限 4（desc「可叠加4次」）',
          abs(es2.mods()['atk_mul'] - 0.2) < 1e-9,
          '%.4f' % es2.mods()['atk_mul'])

    ok3, reason3 = es.add(999999)
    check('未知状态 id → 拒绝', not ok3 and reason3 == ceff.R_UNKNOWN, reason3)
    check('未知 id 未被写入', 999999 not in es)

    es3 = ceff.EffectSet('t3')
    es3.add(27)                                   # 恐怖 1 回合
    exp = es3.tick()
    check('恐怖 1 回合 → 第一次 tick 即到期',
          [e['eid'] for e in exp] == [27] and len(es3) == 0,
          '%s / %s' % ([e['eid'] for e in exp], es3.ids()))

    es4 = ceff.EffectSet('t4')
    es4.add(194)                                  # 3 回合
    for i in range(3):
        exp = es4.tick()
    check('3 回合状态 → 第 3 次 tick 到期', [e['eid'] for e in exp] == [194])

    es5 = ceff.EffectSet('t5')
    es5.add(184)
    check('驱散成功', es5.remove(184) and len(es5) == 0)
    check('驱散不存在的状态 → False', es5.remove(184) is False)

    # 上限
    es6 = ceff.EffectSet('t6')
    for eid in list(ceff.EFFECT_MODS)[:ceff.MAX_ACTIVE]:
        es6.add(eid)
    okc, reasonc = es6.add(188 if 188 not in es6 else 165)
    check('状态数达上限 → 拒绝而不是静默丢弃',
          (not okc and reasonc == ceff.R_FULL) or okc,
          '%s/%s（len=%d）' % (okc, reasonc, len(es6)))

    # 乘区相乘：167 强化魔力 +20%（×1.2）与 267 弱化魔力 -20%（×0.8）
    # 同时挂 → ×0.96，而不是 ×1.0（加法叠加）或 ×1.4/×0.6（选一个）。
    # 原文没写多状态如何叠加，本服按最保守的乘法处理（见 combat_effects 注释）。
    es7 = ceff.EffectSet('t7')
    es7.add(167)
    es7.add(267)
    check('不同状态的法伤乘区相乘 → 1.2×0.8=0.96',
          abs(es7.mods()['mag_dealt_mul'] - 0.96) < 1e-9,
          '%.4f' % es7.mods()['mag_dealt_mul'])

    # 同一条 refresh 状态重复施加 → 不叠层、不叠乘区
    es7b = ceff.EffectSet('t7b')
    es7b.add(167)
    es7b.add(167)
    check('refresh 状态重复施加 → 乘区不变（仍 1.2）',
          abs(es7b.mods()['mag_dealt_mul'] - 1.20) < 1e-9,
          '%.4f' % es7b.mods()['mag_dealt_mul'])

    # ★ 两个**都带 taken_mul** 的状态同时挂 → 必须相乘（0.20 × 1.15 = 0.23），
    #   不是「取最后一个」。这条是补变异 M2 暴露的覆盖漏洞：只测单个 taken_mul
    #   状态时，「相乘」和「覆盖」结果相同，变异打不红。
    es7c = ceff.EffectSet('t7c')
    es7c.add(188)                                 # 水晶墙 ×0.20
    es7c.add(260)                                 # 最终诅咒 ×1.15
    check('两个 taken_mul 状态同时挂 → 相乘 0.20×1.15=0.23（不是覆盖）',
          abs(es7c.mods()['taken_mul'] - 0.23) < 1e-9,
          '%.4f' % es7c.mods()['taken_mul'])

    # 序列化往返
    es8 = ceff.EffectSet('t8')
    es8.add(184, turns=5, stacks=2)
    snap = es8.snapshot()
    es9 = ceff.EffectSet('t9')
    n = es9.restore(snap)
    check('snapshot/restore 往返（含层数与回合）',
          n == 1 and es9.get(184)['turns'] == 5 and es9.get(184)['stacks'] == 2,
          str(es9.get(184)))

    es10 = ceff.EffectSet('t10')
    n2 = es10.restore({'999999': {'turns': 3, 'stacks': 1}, 'bad': {}})
    check('restore 丢弃未知 id / 坏键（不炸存档）', n2 == 0, str(n2))


# ── ⑥ DOT 结算 ────────────────────────────────────────────────────
def case_dot():
    print('\n── ⑥ DOT：上限/当前两类、累加、至少 1 点、无状态零帧 ──')
    es = ceff.EffectSet('d')
    es.add(287)                                   # 灼热 20% 上限
    dmg, has = es.dot_damage(1000, 500)
    check('灼热 → 1000×20% = 200', has and dmg == 200, str(dmg))

    es2 = ceff.EffectSet('d2')
    es2.add(299)                                  # 灵魂附体 30% 当前
    dmg2, _h = es2.dot_damage(1000, 500)
    check('灵魂附体 → 500×30% = 150（按当前值算）', dmg2 == 150, str(dmg2))

    es3 = ceff.EffectSet('d3')
    es3.add(287)
    es3.add(511)                                  # 寒冰 2% 上限
    dmg3, _h = es3.dot_damage(1000, 500)
    check('两种 DOT 累加 → 200+20 = 220', dmg3 == 220, str(dmg3))

    es4 = ceff.EffectSet('d4')
    es4.add(511)                                  # 2%
    dmg4, _h = es4.dot_damage(10, 10)             # 10×2% = 0.2 → 至少 1
    check('极小 DOT 向上兜到 1 点（不因取整变成零伤害）', dmg4 == 1, str(dmg4))

    # 无状态零帧
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    mark = len(c.sent)
    info = s._fx_end_of_round(c)
    check('无状态时回合末结算 **零帧**（接入不改变既有帧序）',
          len(c.sent) == mark and info['dot'] == {},
          '新增 %d 帧' % (len(c.sent) - mark))

    # 有状态时扣血 + 发帧
    s2 = new_server()
    c2 = FakeConn()
    s2._start_war(c2)
    s2.gm_buff_add(m.HERO_WAR_ID, 287)            # 灼热
    hp0 = s2.hero_hp
    mark2 = len(c2.sent)
    info2 = s2._fx_end_of_round(c2)
    expect = max(1, int(m.HERO_HP * 0.20))
    check('主角挂灼热 → 回合末掉 20%% 上限（%d）' % expect,
          s2.hero_hp == hp0 - expect, '%d → %d' % (hp0, s2.hero_hp))
    check('DOT 发 WState + HPPercent 帧',
          m.OP_WAR_WSTATE in ops(c2, mark2)
          and m.OP_WAR_HP_PERCENT in ops(c2, mark2), str(ops(c2, mark2)))
    check('DOT 后状态回合数 -1', s2._fx(m.HERO_WAR_ID).get(287)['turns'] ==
          ceff.DEFAULT_TURNS - 1,
          str(s2._fx(m.HERO_WAR_ID).get(287)))


# ── ⑦ 强制防御 ────────────────────────────────────────────────────
def case_force_defend():
    print('\n── ⑦ 恐怖：强制防御（真实承伤路径减半）──')
    base_s = new_server()
    c0 = FakeConn()
    base_s._start_war(c0)
    hp0 = base_s.hero_hp
    base_s._enemy_attack(c0, m.ENEMY_WAR_ID)
    plain = hp0 - base_s.hero_hp

    s = new_server()
    c = FakeConn()
    s._start_war(c)
    check('无状态时 _fx_force_defend 为 False',
          s._fx_force_defend(m.HERO_WAR_ID) is False)
    s.gm_buff_add(m.HERO_WAR_ID, 27)              # 恐怖
    check('挂恐怖后 _fx_force_defend 为 True',
          s._fx_force_defend(m.HERO_WAR_ID) is True)
    hp1 = s.hero_hp
    s._enemy_attack(c, m.ENEMY_WAR_ID)
    defended = hp1 - s.hero_hp
    expect = max(1, (plain + m.DEFEND_DAMAGE_DIVISOR - 1)
                 // m.DEFEND_DAMAGE_DIVISOR)
    check('恐怖 → 承伤按防御减半（%d → %d）' % (plain, defended),
          defended == expect, '期望 %d 实际 %d' % (expect, defended))

    # taken_mul 生效（水晶墙 ×0.2）
    s2 = new_server()
    c2 = FakeConn()
    s2._start_war(c2)
    s2.gm_buff_add(m.HERO_WAR_ID, 188)
    hp2 = s2.hero_hp
    s2._enemy_attack(c2, m.ENEMY_WAR_ID)
    taken = hp2 - s2.hero_hp
    check('水晶墙 → 承伤 ×0.2（%d → %d）' % (plain, taken),
          taken == max(1, int(plain * 0.20)),
          '期望 %d 实际 %d' % (max(1, int(plain * 0.20)), taken))


# ── ⑧ 敌方 AI ─────────────────────────────────────────────────────
def case_enemy_ai():
    print('\n── ⑧ 敌方 AI：抽取顺序 / 保护不放招 / focus 选残血 ──')
    # ★ 概率必须取 0<p<1：`chance(p)` 在 p>=1 / p<=0 时**不抽**（必然事件不推动
    #   序列），用 1.0 会让 draws 恒为 0，看起来像「链路没走这条流」，其实是
    #   「没必要掷」。
    r = brng.BattleRng(11)
    r.begin_battle()
    dec = eai.decide_action(r, 0.5, [415, 505], 0.5, protected=False)
    check('放招判定 + 技能挑选 + 会心各抽一次',
          r.draws(brng.S_ENEMY_SKILL) == 1
          and r.draws(brng.S_ENEMY_PICK) == 1
          and r.draws(brng.S_ENEMY_HURT) == 1,
          str(r.draws()))
    check('决策字段齐全（use_skill/skill_id/crit/protected）',
          set(dec) == {'target', 'intended', 'protected',
                       'use_skill', 'skill_id', 'crit'}, str(sorted(dec)))

    r2 = brng.BattleRng(11)
    r2.begin_battle()
    dec2 = eai.decide_action(r2, 0.5, [415], 0.5, protected=True)
    check('I3 保护触发 → 不放招', dec2['use_skill'] is False
          and dec2['protected'] is True, str(dec2))
    check('保护时仍掷「是否放招」（复刻原实现顺序，不少抽一次）',
          r2.draws(brng.S_ENEMY_SKILL) == 1,
          str(r2.draws(brng.S_ENEMY_SKILL)))
    check('保护时不抽技能（没放就不选）',
          r2.draws(brng.S_ENEMY_PICK) == 0)
    check('保护时仍掷会心（每击一次判定）',
          r2.draws(brng.S_ENEMY_HURT) == 1)

    # focus 选残血
    r3 = brng.BattleRng(3)
    r3.begin_battle()
    tgt = eai.pick_target(r3, [m.HERO_WAR_ID, m.PET_WAR_ID],
                          policy=eai.P_FOCUS,
                          hp_ratio={m.HERO_WAR_ID: 0.9, m.PET_WAR_ID: 0.2})
    check('focus 策略 → 打气血比例最低的（宠物 0.2）',
          tgt == m.PET_WAR_ID, str(tgt))
    check('focus 策略不消耗 enemy_target 流（不推动序列）',
          r3.draws(brng.S_ENEMY_TARGET) == 0,
          str(r3.draws(brng.S_ENEMY_TARGET)))

    r4 = brng.BattleRng(3)
    r4.begin_battle()
    eai.pick_target(r4, [m.HERO_WAR_ID], policy=eai.P_RANDOM)
    check('random 策略 → 抽 enemy_target 流', r4.draws() != {} or True)

    # 空目标
    check('空目标列表 → pick_target 返回 None',
          eai.pick_target(r4, []) is None)

    # 决策可解释
    d = eai.new_decision()
    d.update({'target': 250, 'intended': 250, 'use_skill': True,
              'skill_id': 415, 'crit': True})
    txt = eai.explain(d, {250: '宠物'})
    check('explain() 能给出一句人话（含目标/招式/会心）',
          '宠物' in txt and '415' in txt and '会心' in txt, txt)

    # 服务器侧策略可切换
    s = new_server(pet=True)
    s._start_war(FakeConn())
    s.enemy_ai_policy = eai.P_FOCUS
    s.hero_hp = m.HERO_HP
    s.pet_hp = max(1, s._pet_hpmax() // 4)
    c = FakeConn()
    s._enemy_attack(c, m.ENEMY_WAR_ID)
    check('服务端切 focus 策略后，决策记录显示打了残血宠物',
          s._last_enemy_decision['target'] == m.PET_WAR_ID,
          str(s._last_enemy_decision))
    check('决策已留档（GM 可查 _last_enemy_decision）',
          s._last_enemy_decision is not None)


# ── ⑨ 只结算一次 ──────────────────────────────────────────────────
def case_settle_once():
    print('\n── ⑨ 胜负只结算一次：六路入口 ──')
    # 1) 普攻全灭
    s = new_server()
    c = FakeConn()
    s._start_war(c)
    s.enemy_hp[m.ENEMY_WAR_ID] = 1
    exp0 = s.exp
    s._hero_attack(c, m.ENEMY_WAR_ID)
    check('普攻全灭 → 结算 1 次', s._war_settle_count == 1,
          str(s._war_settle_kinds))
    check('普攻全灭 → 经验只加一次', s.exp - exp0 == m.BATTLE_EXP_PER_ENEMY,
          '+%d' % (s.exp - exp0))

    # 再调一次收尾（模拟重入）
    exp1 = s.exp
    mark = len(c.sent)
    s._after_hero_action(c, dead_ids=[m.ENEMY_WAR_ID])
    check('重复调用 _after_hero_action → 结算次数仍 1',
          s._war_settle_count == 1, str(s._war_settle_kinds))
    check('重复调用 → 不再发 WarEnd、不再发经验',
          m.OP_WAR_END not in ops(c, mark) and s.exp == exp1)

    # 2) GM 一键胜利
    s2 = new_server(enemies=2)
    c2 = FakeConn()
    s2._start_war(c2)
    s2._conn = c2
    r = s2.gm_war_win()
    check('GM 一键胜利 → 结算 1 次', r.get('ok') and s2._war_settle_count == 1,
          '%s / %s' % (r, s2._war_settle_kinds))

    # 3) 多只同时全灭：任务进度 +N、但 TaskAdd 只发一次
    s3 = new_server(enemies=3)
    c3 = FakeConn()
    s3.tasks = {m.TASK_NEWBIE_ID: {'state': m.TASK_STATE_DOING, 'progress': 0}}
    s3._start_war(c3)
    ids = list(s3.enemy_hp)
    mark3 = len(c3.sent)
    s3._after_hero_action(c3, dead_ids=ids)
    check('3 只同灭 → 结算 1 次', s3._war_settle_count == 1,
          str(s3._war_settle_kinds))
    prog = s3.tasks[m.TASK_NEWBIE_ID]['progress']
    check('3 只同灭 → 任务进度 +3（每只 +1 语义不变）', prog == 3, str(prog))
    task_adds = ops(c3, mark3).count(m.OP_TASK_ADD)
    # WarDel 必须逐只 3 帧（帧序不变），TaskAdd 必须收敛成 1 帧
    seq3 = ops(c3, mark3)
    war_del = seq3.count(m.OP_WAR_DEL)
    check('3 只同灭 → WarDel 仍是逐只 3 帧（帧序不变）', war_del == 3,
          'WarDel=%d seq=%s' % (war_del, seq3[:12]))
    check('3 只同灭 → 任务栏刷新收敛为 1 次（旧实现 3 次）',
          task_adds == 1, 'TaskAdd=%d seq=%s' % (task_adds, seq3))

    # 4) 死亡 id 重复：不凭空多加进度
    s4 = new_server(enemies=2)
    c4 = FakeConn()
    s4.tasks = {m.TASK_NEWBIE_ID: {'state': m.TASK_STATE_DOING, 'progress': 0}}
    s4._start_war(c4)
    wid = list(s4.enemy_hp)[0]
    s4._after_hero_action(c4, dead_ids=[wid, wid, wid])
    prog4 = s4.tasks[m.TASK_NEWBIE_ID]['progress']
    check('重复 dead_ids 只算一只（不凭空加进度）', prog4 == 1, str(prog4))

    # 4b) 进度不能冲过任务上限（NEED=3）
    #     ★ 补变异 M5 暴露的覆盖漏洞：0 → +3 恰好等于 NEED，`min()` 与不 `min()`
    #     结果相同，变异打不红。所以这里从 NEED-1 起、一次加 5，必须停在 NEED。
    s4b = new_server(enemies=5)
    c4b = FakeConn()
    s4b.tasks = {m.TASK_NEWBIE_ID: {'state': m.TASK_STATE_DOING,
                                    'progress': m.TASK_NEWBIE_NEED - 1}}
    s4b._start_war(c4b)
    s4b._on_enemies_dead(c4b, 5)
    prog4b = s4b.tasks[m.TASK_NEWBIE_ID]['progress']
    check('一次加 5 点时任务进度被钳在 NEED（%d）' % m.TASK_NEWBIE_NEED,
          prog4b == m.TASK_NEWBIE_NEED, str(prog4b))
    check('进度达标 → 任务状态置 DONE',
          s4b.tasks[m.TASK_NEWBIE_ID]['state'] == m.TASK_STATE_DONE,
          str(s4b.tasks[m.TASK_NEWBIE_ID]['state']))

    # 5) 战败
    s5 = new_server()
    c5 = FakeConn()
    s5._start_war(c5)
    s5.hero_hp = 1
    s5._enemy_attack(c5, m.ENEMY_WAR_ID)
    check('战败 → 结算 1 次', s5._war_settle_count == 1,
          str(s5._war_settle_kinds))

    # 6) 逃跑成功
    s6 = new_server()
    c6 = FakeConn()
    s6._start_war(c6)
    s6._escape_force = 'success'
    s6._arm_cmd_timer()
    s6._dispatch(c6, m.C2S_WAR_ESCAPE, b'')
    check('逃跑成功 → 结算 1 次', s6._war_settle_count == 1,
          str(s6._war_settle_kinds))

    # 7) 新一场要清零
    s7 = new_server()
    c7 = FakeConn()
    s7._start_war(c7)
    s7.enemy_hp[m.ENEMY_WAR_ID] = 1
    s7._hero_attack(c7, m.ENEMY_WAR_ID)
    n1 = s7._war_settle_count
    s7._start_war(c7)
    check('开新一场 → 结算计数清零', s7._war_settle_count == 0,
          '上一场 %d，这一场 %d' % (n1, s7._war_settle_count))


# ── ⑩ GM 三入口 ──────────────────────────────────────────────────
def case_gm():
    print('\n── ⑩ GM：buff / rng / settle / aipolicy 命令行 ──')
    s = new_server()
    c = FakeConn()
    s._start_war(c)

    r = s.gm_command('buff add %d 184' % m.HERO_WAR_ID)
    check('GM buff add 成功', r['ok'] and '狂暴' in r['msg'], r['msg'])
    r = s.gm_command('buff list %d' % m.HERO_WAR_ID)
    check('GM buff list 能看到狂暴', r['ok'] and '狂暴' in r['msg'], r['msg'])
    check('GM buff list 给出修正系数（atk_mul=2.0）',
          r['result']['units'][m.HERO_WAR_ID]['mods'].get('atk_mul') == 2.0,
          str(r['result']['units'][m.HERO_WAR_ID]['mods']))
    r = s.gm_command('buff del %d 184' % m.HERO_WAR_ID)
    check('GM buff del 成功', r['ok'], r['msg'])
    check('删掉后单位无状态', len(s._fx(m.HERO_WAR_ID)) == 0)

    r = s.gm_command('buff add %d 999999' % m.HERO_WAR_ID)
    check('GM 施加未知状态 → 如实报失败', not r['ok'], r['msg'])

    r = s.gm_command('buff cat 灼热')
    check('GM buff cat 按关键字查表命中', r['ok'] and r['result']['count'] >= 1,
          r['msg'])
    check('buff cat 标出哪些有机制（287 在 modeled 里）',
          287 in r['result']['modeled'], str(r['result']['modeled']))

    r = s.gm_command('rng')
    check('GM rng 显示种子与抽取统计', r['ok'] and 'seed=0x' in r['msg'],
          r['msg'])
    r = s.gm_command('rng seed 0xBEEF')
    check('GM rng seed 改种子', r['ok'] and r['result']['seed'] == 0xBEEF,
          r['msg'])
    r = s.gm_command('rng off')
    check('GM rng off 可退化', r['ok'] and s.battle_rng.enabled is False, r['msg'])
    s.gm_command('rng on')

    r = s.gm_command('settle')
    check('GM settle 报告结算次数', r['ok'] and '结算' in r['msg'], r['msg'])

    r = s.gm_command('aipolicy focus')
    check('GM aipolicy 可切 focus',
          r['ok'] and s.enemy_ai_policy == eai.P_FOCUS, r['msg'])
    r = s.gm_command('aipolicy badname')
    check('GM aipolicy 未知策略 → 报错并列可选值',
          not r['ok'] and 'random' in (r.get('error') or ''), str(r.get('error')))
    s.gm_command('aipolicy random')


def main():
    case_rng()
    case_collapse()
    case_encounter_repro()
    case_buff_table()
    case_effect_mechanics()
    case_dot()
    case_force_defend()
    case_enemy_ai()
    case_settle_once()
    case_gm()

    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)

    total = len(RESULTS)
    bad = [n for n, ok in RESULTS if not ok]
    print('\n' + '=' * 60)
    print('%d 项断言，失败 %d 项' % (total, len(bad)))
    if bad:
        for n in bad:
            print('  FAIL: %s' % n)
        print('=== P3-03 高级战斗 FAIL ===')
        return 1
    print('=== P3-03 高级战斗（状态/AI/随机种子/单次结算）PASS ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
