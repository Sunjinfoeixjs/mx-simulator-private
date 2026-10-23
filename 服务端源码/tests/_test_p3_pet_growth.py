# -*- coding: utf-8 -*-
"""P3-02 宠物深度养成——回归测试（进程内，不走 socket）。

守的是 P3-02 的四条验收项：
  · 成长计算可重复（连算 100 次一字不差）
  · 非法培养不扣资源（加点/学技能失败后，五维/点数/技能表零变化）
  · 退出重登状态完整（加点/技能/装备/等级落盘后读回来一致，且派生值不叠加）
  · 宠物属性只有一个入口（三层合成全在 pet_progression，别名与本体同一对象）

覆盖：
  ① 常量对账：pet_progression / pet_skill_service 与 m4_server 是**同一份**；
     技能真值表与客户端 petskill.DATA 逐条一致（重跑生成器 --check）；
     `PET_SKILL_EFFECTS` / `PET_PASSIVE_EFFECTS` 里手写的 desc 断言能对上真表。
  ② 面板层不变量：默认宠物 = 参考宠 = 录像读数（×1.0），战斗层 = 标定值，
     资源层 = 350/380/30，资质五项 = 录像值。任何漂移都判红。
  ③ 加点联动：Con +4 → 面板 hp +40；Str +4 → 面板 at +8 且**战斗层 at 也跟着变**；
     点数不足整包拒、不扣点。
  ④ 幂等：pet_normalize 连调 100 次 stats/hpmax/expnext/attr_lv 全等；
     panel_stats 不改入参。
  ⑤ 非法培养零副作用：加点 4 种非法 + 学技能 4 种非法，状态快照一字不变。
  ⑥ 技能服务：绑定 id 归一（10415≡415）、白名单拒收、遗忘、替换、数量上限。
  ⑦ 技能真的影响战斗：遗忘「连击」(102) → _pet_passives 的 combo_chance 归零。
  ⑧ 旧档兼容：缺 attr_lv/五维/技能的旧宠物 dict 走一遍 normalize 不崩、属性正确；
     attr_lv 的字符串键被收成 int（存档 JSON 会把键变成 '3'）。
  ⑨ 重登不叠加：加点 + 学技能 + 升级 → 落盘 → 新实例读档，派生值逐字段一致。
  ⑩ GM 入口：petstats / petskill / petforget 三条指令可用且如实报拒因。

用法：python _test_p3_pet_growth.py
"""
from __future__ import print_function

import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import m4_server as m            # noqa: E402
import pet_progression as ppg   # noqa: E402
import pet_skill_service as pss  # noqa: E402
import pet_skill_data as psd     # noqa: E402

ok = True


def check(c, msg, detail=None):
    global ok
    line = ('OK  ' if c else 'FAIL') + ' ' + msg
    if detail is not None and not c:
        line += '  ← %r' % (detail,)
    print(line)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(bytes(frame))


def ops(conn):
    """解出 (opcode, payload)。帧形：`[1B 长度][1B opcode][payload]`；
    长度 0xFF 时是 `[0xFF][u16 opcode][payload]`。**长度在前、opcode 在后**。"""
    out = []
    for f in conn.sent:
        if f[0] == 0xFF:
            out.append((struct.unpack('<H', f[1:3])[0], f[3:]))
        else:
            out.append((f[1], f[2:]))
    return out


def skill_frames(conn):
    """op49 → [(pid, n, hf_from, ids)]。"""
    out = []
    for op, body in ops(conn):
        if op != m.OP_SUMMON_SKILL or len(body) < 6:
            continue
        pid = struct.unpack('<I', body[:4])[0]
        n, x = body[4], body[5]
        ids = list(struct.unpack('<%dH' % n, body[6:6 + 2 * n])) if n else []
        out.append((pid, n, x, ids))
    return out


def make(tmp, tag):
    s = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                   logdir=os.path.join(tmp, 'log_' + tag),
                   savedir=os.path.join(tmp, 'save_' + tag))
    s.log = lambda *a, **k: None
    # 保留回合窗口语义（只去掉计时器副作用）——与 _test_pet_instance.make 同款
    def _reopen_window():
        s.await_cmd = True
        s._cmd_consumed = False
    s._arm_cmd_timer = _reopen_window
    return s


def snap(p):
    """宠物实例的**状态快照**（不含派生值）：用来断言「失败后一字未动」。"""
    keys = ('level', 'exp', 'point', 'str', 'con', 'mag', 'cha', 'dex',
            'hp', 'mp', 'fp', 'life')
    d = dict((k, p.get(k)) for k in keys)
    d['skills'] = list(p.get('skills') or [])
    d['attr_lv'] = dict(p.get('attr_lv') or {})
    d['equips'] = dict((k, dict(v)) for k, v in (p.get('equips') or {}).items())
    return d


def derived(p):
    """派生值快照（可重复性断言用）。"""
    return {'stats': dict(p.get('stats') or {}),
            'hpmax': p.get('hpmax'), 'mpmax': p.get('mpmax'),
            'fpmax': p.get('fpmax'), 'expnext': p.get('expnext'),
            'attr_lv': dict(p.get('attr_lv') or {})}


def main():
    tmp = tempfile.mkdtemp(prefix='p3pet_')

    # ══════════════════ ① 常量对账 ══════════════════
    print('── ① 常量与真值表对账 ──')
    check(m.PET_POINT_EFFECT is ppg.POINT_EFFECT
          and m.PET_POINT_ORDER is ppg.POINT_ORDER
          and m.PET_POINT_BASE is ppg.POINT_BASE,
          '五维换算/顺序/出厂值在 m4_server 与 pet_progression 是同一份对象')
    check(tuple(ppg.POINT_ORDER) == ('con', 'str', 'mag', 'cha', 'dex'),
          '加点字节顺序 Con 在前（dlgpet._id2attr）', ppg.POINT_ORDER)
    check(m.PET_POINT_BASE == {'str': m.PET_STR, 'con': m.PET_CON, 'mag': m.PET_MAG,
                              'cha': m.PET_CHA, 'dex': m.PET_DEX},
          '出厂五维与录像面板值一致（10/10/10/10/10）', m.PET_POINT_BASE)
    check(m.PET_SKILL_MAX == pss.SKILL_MAX and m.PET_LEVEL_MAX == ppg.LEVEL_MAX,
          '技能格/等级上限同一来源（%d / %d）' % (pss.SKILL_MAX, ppg.LEVEL_MAX))
    check(m.PET_ATTR_IDXS == ppg.ATTR_IDXS and m.PET_ATTR_NAME == ppg.ATTR_NAME,
          '资质下标与名称同一来源')

    rc = subprocess.call([sys.executable,
                          os.path.join(SIM, 'gen_pet_skill_data.py'), '--check'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check(rc == 0, 'pet_skill_data.py 与客户端 petskill.DATA 一致（生成器 --check）',
          'rc=%r' % rc)
    check(len(psd.PET_SKILL_INFO) == 404,
          '技能真值表 404 条', len(psd.PET_SKILL_INFO))
    missing = [s for s in m.PET_SKILLS if not psd.known(s)]
    check(not missing, '录像那套技能（%s）都在真值表里' % (m.PET_SKILLS,), missing)

    # ★ 手写的 desc 断言 vs 客户端真表：这两张表是本服战斗行为的依据，
    #   必须能对着原文核对，而不是「注释里这么写」。
    for sid, frag in ((112, u'8%'), (102, u'40%'), (102, u'75%'),
                      (317, u'15%'), (505, u'10%')):
        d = m.PET_PASSIVE_EFFECTS[sid]
        check(frag in psd.PET_SKILL_INFO[sid][2],
              '被动 %d「%s」的数值 %s 能在 petskill desc 里找到'
              % (sid, d['name'], frag), psd.PET_SKILL_INFO[sid][2])
    check(psd.PET_SKILL_INFO[415][2] == u'群体物理攻击'
          and psd.PET_SKILL_INFO[415][1] == 1,
          '415 乱舞冲击 = 主动攻击(type=1) + desc「群体物理攻击」')

    # ══════════════════ ② 面板层不变量 ══════════════════
    print('── ② 默认宠物必须逐位等于录像读数（×1.0 锚）──')
    p = m.pet_new(m.PET_ID)
    check(p['stats'] == m._PET_REF_TRUE,
          '默认宠物面板 = 参考宠（%s）' % (m._PET_REF_TRUE,), p['stats'])
    cb = m.pet_combat_stats(p)
    check(cb == {'at': m.PET_AT_COMBAT, 'df': m.PET_DF_COMBAT, 'dp': m.PET_DP_COMBAT,
                 'speed': m.PET_SPEED_COMBAT, 'magdef': m.PET_MAGDEF_COMBAT},
          '战斗层 = 录像标定值（37/15/4/49/36）', cb)
    check(m.pet_resource_max(p) == {'hp': 350, 'mp': 380, 'fp': 30},
          '资源层 = 录像 350/380/30', m.pet_resource_max(p))
    check(m.pet_ability(p) == m.PET_ABILITY,
          '资质五项 = 录像 1797/1792/1880/1828/1909', m.pet_ability(p))
    check(ppg.point_bonus(p) == {},
          '出厂五维不算加点加成（否则 ×1.0 立刻破）', ppg.point_bonus(p))

    # ══════════════════ ③ 加点联动 ══════════════════
    print('── ③ 加点：面板层精确、战斗层跟着动 ──')
    p = m.pet_new(m.PET_ID)
    p['point'] = 20
    base_hp = p['stats']['hp']
    base_hm = p['hpmax']
    okk, why = m.pet_add_points(p, [4, 0, 0, 0, 0])          # Con +4
    check(okk, 'Con +4 加点成功', why)
    check(p['stats']['hp'] == base_hp + 40,
          '体质 +4 → 面板 hp +40（10/点，SIM 换算）',
          (base_hp, p['stats']['hp']))
    check(p['hpmax'] > base_hm,
          '气血上限随资质同步抬高（%d → %d）' % (base_hm, p['hpmax']))
    want_hm = ppg.resource_max(m.pet_resource_base(),
                               ppg.resource_ratios(p['stats'], m._PET_REF_TRUE))['hp']
    check(p['hpmax'] == want_hm, '上限 = 录像 350 × 真值倍率（%d）' % want_hm, p['hpmax'])

    base_at = p['stats']['at']
    base_cat = m.pet_combat_stats(p)['at']
    m.pet_add_points(p, [0, 4, 0, 0, 0])                     # Str +4
    check(p['stats']['at'] == base_at + 8, '力量 +4 → 面板 at +8（2/点）',
          (base_at, p['stats']['at']))
    check(m.pet_combat_stats(p)['at'] > base_cat,
          '力量加点**真的**改变战斗层 at（%d → %d）—— 加点不是只改面板'
          % (base_cat, m.pet_combat_stats(p)['at']))

    # ══════════════════ ④ 幂等 ══════════════════
    print('── ④ 派生值只算不存：连算 100 次一字不差 ──')
    first = derived(p)
    for _ in range(100):
        m.pet_normalize(p)
    check(derived(p) == first, 'pet_normalize ×100 幂等', derived(p))
    src = {'hp': 305, 'at': 579, 'df': 185, 'mg': 289, 'md': 185, 'sp': 414}
    keep = dict(src)
    ppg.panel_stats(src, {'con': 20, 'str': 20, 'mag': 10, 'cha': 10, 'dex': 10})
    check(src == keep, 'panel_stats 不改入参（纯函数）', src)

    # ══════════════════ ⑤ 非法培养零副作用 ══════════════════
    print('── ⑤ 非法培养：不扣点、不改五维、不动技能表 ──')
    p = m.pet_new(m.PET_ID)
    p['point'] = 3
    for deltas, want in (([-1, 0, 0, 0, 0], 'negative'),
                         ([0, 0, 0, 0, 0], 'empty'),
                         ([9, 0, 0, 0, 0], 'not_enough'),
                         ([1, 1], 'bad_length')):
        before = snap(p)
        got_ok, got_why = m.pet_add_points(p, deltas)
        check(not got_ok and got_why == want,
              '加点 %r 被拒（%s）' % (deltas, want), (got_ok, got_why))
        check(snap(p) == before, '加点被拒后状态零变化（%r）' % (deltas,), snap(p))
    check(m.pet_add_points(None, [1, 0, 0, 0, 0]) == (False, 'no_pet'),
          '无宠物时加点回 no_pet')

    before = snap(p)
    for sid, want in ((999999, 'unknown_skill'), (415, 'already'),
                      ('abc', 'bad_id'), (0, 'bad_id')):
        got_ok, got_why = m.pet_learn_skill(p, sid)
        check(not got_ok and got_why == want,
              '学技能 %r 被拒（%s）' % (sid, want), (got_ok, got_why))
    check(snap(p) == before, '学技能被拒后技能表零变化', p.get('skills'))
    full_pet = m.pet_new(9999, skills=[101, 102, 103, 104, 105, 106, 107, 108])
    got_ok, got_why = m.pet_learn_skill(full_pet, 110)
    check(not got_ok and got_why == 'full',
          '技能格满（%d）时拒收' % len(full_pet['skills']), got_why)
    check(len(full_pet['skills']) == 8, '拒收后仍是 8 格', len(full_pet['skills']))
    ok_strict, why_strict = m.pet_learn_skill(full_pet, 999999, strict=False)
    check(not ok_strict and why_strict == 'full',
          'strict=False 只放宽白名单，不绕过容量（仍回 full）', why_strict)

    # ══════════════════ ⑥ 技能服务 ══════════════════
    print('── ⑥ 技能服务：归一 / 遗忘 / 替换 / 上限 ──')
    check(pss.normalize(10415) == (415, True),
          '10415 归一成 415 且记「绑定」', pss.normalize(10415))
    check(pss.normalize(415) == (415, False), '415 不是绑定技能')
    check(pss.normalize('abc') == (0, False) and pss.normalize(0) == (0, False),
          '非法 id 归一成 0（不抛异常）')
    check(pss.ids_of([415, 10415, 0, None, 'x']) == [415, 415],
          'ids_of 跳过脏值、两种写法都归一成 415',
          pss.ids_of([415, 10415, 0, None, 'x']))
    check(pss.has([10415], 415), '绑定形式也算「已有该技能」')
    sk = [415, 102]
    fok, fwhy, _n = pss.forget(sk, 10415)
    check(fok and sk == [102], '遗忘用 10415 也能删掉 415', (fok, fwhy, sk))
    check(pss.forget(sk, 415) == (False, pss.R_ABSENT, 415),
          '遗忘不存在的技能回 absent')
    rep = [415, 102]
    rok, rwhy, _rn, victim = pss.replace(rep, 103, victim=415)
    check(rok and rep == [102, 103] and victim == 415,
          '替换：冲掉 415、换成 103（要诀语义的机制面）', (rok, rwhy, rep, victim))
    chk_ok, chk_why, _ = pss.check_learn([108], 108)
    check(not chk_ok and chk_why == 'already', '判重先于容量检查')
    desc = pss.describe([415, 10415, 999999])
    check(desc[0]['name'] == u'乱舞冲击' and desc[1]['bound'] is True
          and desc[2]['known'] is False and u'未知' in desc[2]['name'],
          'describe 给出真名 + 绑定标记；表外 id 如实标「未知」', desc)
    check(pss.active_ids([415, 102]) == [415],
          '招式菜单只认 type 1/2（被动不进菜单）', pss.active_ids([415, 102]))

    # ══════════════════ ⑦ 技能真的影响战斗 ══════════════════
    print('── ⑦ 遗忘「连击」→ 连击被动归零 ──')
    srv = make(tmp, 'a')
    pa = srv.pets.get(m.PET_ID)
    check(pa is not None and srv._pet() is pa, '新服默认出战宠物就是它')
    before_combo = srv._pet_passives()['combo_chance']
    check(abs(before_combo - 0.40) < 1e-9,
          '带着 102 连击时 combo_chance = 0.40（petskill desc 真值）',
          before_combo)
    c = FakeConn()
    srv._pet_frames(c, pa)
    frames0 = skill_frames(c)
    check(frames0 and frames0[-1][1] == 6,
          'op49 下发 6 个技能、hf_from=6', frames0)
    fok, fwhy = m.pet_forget_skill(pa, 102)
    check(fok, '遗忘 102 成功', fwhy)
    after_combo = srv._pet_passives()['combo_chance']
    check(after_combo == 0.0,
          '遗忘后 combo_chance 归零（技能表是真在驱动战斗）', after_combo)
    c = FakeConn()
    srv._pet_frames(c, pa)
    frames1 = skill_frames(c)
    check(frames1 and frames1[-1][1] == 5 and 102 not in frames1[-1][3],
          'op49 立刻少一格且不再含 102', frames1)
    check(frames1[-1][2] <= frames1[-1][1],
          'hf_from 被钳到 n 以内（客户端 hfskill = n - x 不会变负）', frames1[-1])

    # ══════════════════ ⑧ 旧档兼容 ══════════════════
    print('── ⑧ 旧档：缺字段不崩、键类型收口 ──')
    old = {'pid': 7, 'name': u'老档宠', 'shape': 4005, 'level': 1, 'skills': [415]}
    m.pet_normalize(old)
    check(old['stats'] == m._PET_REF_TRUE,
          '缺五维/资质的旧宠物按出厂值算（= 参考宠）', old.get('stats'))
    check(old.get('point', 0) == 0 and old.get('str', m.PET_STR) == m.PET_STR,
          '缺失字段按出厂值/0 处理（不凭空造点数）',
          (old.get('point'), old.get('str')))
    check(m.pet_add_points(old, [1, 0, 0, 0, 0]) == (False, 'not_enough'),
          '旧档没有 point 字段 → 加点判 not_enough（不静默送点）')
    check(set(old['attr_lv']) == set(ppg.ATTR_IDXS),
          'attr_lv 键成 int 3..8（存档 JSON 的 "3" 会被收口）', old['attr_lv'])
    strkeyed = {'pid': 8, 'shape': 4005, 'level': 3,
                'attr_lv': {'3': 9, '4': 9, '5': 9, '6': 9, '7': 9, '8': 9}}
    m.pet_normalize(strkeyed)
    check(all(isinstance(k, int) for k in strkeyed['attr_lv']),
          '字符串键全部转成 int', strkeyed['attr_lv'])
    check(strkeyed['attr_lv'][3] == 9,
          '只增不减：手工调高的资质不被压回等级值', strkeyed['attr_lv'])

    # ══════════════════ ⑨ 重登不叠加 ══════════════════
    print('── ⑨ 落盘 → 新实例读档：状态一致、派生值不叠加 ──')
    srv.pets[m.PET_ID]['point'] = 10
    m.pet_add_points(srv.pets[m.PET_ID], [2, 2, 0, 0, 0])
    m.pet_learn_skill(srv.pets[m.PET_ID], 103)
    srv._persist()
    srv.flush_saves(5.0)
    path = srv.savepath
    p1 = srv.pets[m.PET_ID]
    want = derived(p1)
    srv.stop()

    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_b'),
                      savedir=os.path.dirname(path))
    srv2.log = lambda *a, **k: None
    p2 = srv2.pets.get(m.PET_ID)
    check(p2 is not None, '读档后有同一只宠物')
    check(derived(p2) == want, '派生值逐字段一致（不叠加、不漂移）', derived(p2))
    check(p2['skills'] == p1['skills'], '技能表保留 %s' % (p1['skills'],))
    check(p2['point'] == p1['point'] and p2['con'] == p1['con']
          and p2['str'] == p1['str'],
          '剩余点/五维保留（point=%s con=%s str=%s）'
          % (p1['point'], p1['con'], p1['str']))
    check(p2['hpmax'] == want['hpmax'],
          '气血上限读回来还是 %s（不是翻倍）' % (want['hpmax'],), p2['hpmax'])
    srv2.stop()

    # ══════════════════ ⑩ GM 入口 ══════════════════
    print('── ⑩ GM：petstats / petskill / petforget ──')
    srv3 = make(tmp, 'c')
    r = srv3.gm_command('petstats')
    check(r['ok'] and '面板层' in r['msg'] and '战斗层' in r['msg']
          and '资源层' in r['msg'],
          'petstats 给出三层来源明细', r['msg'][:60])
    check('加点贡献' in r['msg'] or '加点 +0' in r['msg'],
          'petstats 列出「公式 vs 加点」差值')
    r = srv3.gm_command('petskill 101')
    check(r['ok'], 'petskill 101 成功', r.get('msg'))
    r = srv3.gm_command('petskill 999999')
    check(not r['ok'] and 'unknown_skill' in (r.get('msg') or ''),
          'petskill 表外 id 如实报 unknown_skill', r.get('msg'))
    r = srv3.gm_command('petforget 101')
    check(r['ok'], 'petforget 101 成功', r.get('msg'))
    r = srv3.gm_command('petforget 101')
    check(not r['ok'] and 'absent' in (r.get('msg') or ''),
          '重复遗忘如实报 absent', r.get('msg'))
    r = srv3.gm_command('petstats 424242')
    check(not r['ok'] and r.get('error') == 'no_pet',
          'petstats 查不存在的宠物回 no_pet', r)
    srv3.stop()

    print('')
    print('=' * 60)
    if not ok:
        print('P3-02 宠物深度养成：有断言未通过')
        return 1
    print('P3-02 宠物深度养成：全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
