# -*- coding: utf-8 -*-
"""GM 战斗调试工具回归（2026-10-21）：gm_battle_dict / gm_set_cp /
gm_set_escape_force / gm_set_pet_level / gm_set_enemy_level / 控制台指令 /
debugger 动作与 _GMHandler 路由的「接线」存在性。

全是单元级（不起 socket）：M4Server.__init__ 不绑端口，直接构造即可。
★ 数值分层是本工具的核心契约（见 §7.5.2）：
    *_COMBAT（reverse 标定 0..255，伤害公式用）不得被 GM 改动；
    *_DISPLAY（petfightdata 真值公式 0..65535）才随 petlevel 重算。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))


class FakeConn(object):
    """替身连接：只收集 sendall 字节，供 _start_war 干跑。"""

    def __init__(self):
        self.buf = []
        self.frames = 0

    def sendall(self, b):
        self.buf.append(b)
        self.frames += 1


def main():
    tmp = os.path.join(tempfile.gettempdir(), 'opencode', '_test_gm_battle')
    os.makedirs(tmp, exist_ok=True)
    s = m.M4Server(logdir=tmp, savedir=tmp)

    # ── 1) 快照结构与默认值 ──
    b = s.gm_battle_dict()
    for k in ('in_war', 'dmg', 'snapshots', 'hero', 'pet', 'enemy',
              'units', 'control_points', 'escape'):
        check('battle 快照含 %s' % k, k in b)
    check('battle 快照 dmg 参数为 reverse 默认',
          b['dmg']['model'] == 'reverse' and b['dmg']['gain'] == 1.3
          and b['dmg']['rounding'] == 'trunc', json.dumps(b['dmg']))
    check('控制点默认 30/30/30（对NPC，资料库半真值）',
          b['control_points']['init'] == {'atk': 30, 'def': 30, 'dis': 30},
          json.dumps(b['control_points']))
    check('逃跑名义率 0.8（50%+30% NPC 加成）', b['escape']['nominal_rate'] == 0.8)
    check('逃跑默认不强制的 rate 与 last',
          b['escape']['force'] is None and b['escape']['last'] == {},
          json.dumps(b['escape']))

    # ── 2) 数值分层契约（核心！）────
    p = b['pet']
    check('宠物战斗层 = reverse 标定 37/15/4/49/36',
          (p['combat']['at'], p['combat']['df'], p['combat']['dp'],
           p['combat']['speed'], p['combat']['magdef']) == (37, 15, 4, 49, 36),
          json.dumps(p['combat']))
    check('宠物显示层 = petfightdata 真值（579/185/414/185）',
          (p['display']['at'], p['display']['df'], p['display']['speed'],
           p['display']['magdef']) == (579, 185, 414, 185),
          json.dumps(p['display']))
    check('真值表 6 项齐备 + FactorData 51（野猪）',
          set(p['true_stats']) >= {'hp', 'at', 'df', 'mg', 'md', 'sp'}
          and p['factor_idx'] == 51,
          json.dumps(p['true_stats']))

    # ── 3) 控制点：部分键设定 + 当前值同步 ──
    r = s.gm_set_cp(40, None, 10)
    check('cp 部分键设定（atk=40 dis=10，def 不动）',
          r['init']['atk'] == 40 and r['init']['def'] == 30 and r['init']['dis'] == 10,
          json.dumps(r))
    check('cp 同步改当前值', r['current']['atk'] == 40 and r['current']['dis'] == 10)
    so = s._control_points
    _r = s.gm_set_cp(255, 0, 255)
    check('cp 范围钳制 0..255', _r['init']['def'] == 0 and _r['init']['atk'] == 255)
    s._control_points = so

    # ── 4) 开战重置：_start_war 取 _cp_init ──
    s.gm_set_cp(7, 8, 9)
    fc = FakeConn()
    war_was = s.in_war
    s._start_war(fc)
    check('开战后控制点 = GM 设的 7/8/9',
          s._control_points == {'atk': 7, 'def': 8, 'dis': 9},
          json.dumps(s._control_points))
    check('开战置 in_war 并发帧', s.in_war and fc.frames > 0,
          'frames=%d' % fc.frames)
    check('battle 快照单位表有敌方',
          len(s.gm_battle_dict()['units']) == s.enemies)
    # 收尾：把战斗态还原，防影响后续断言
    s.in_war = war_was
    s.enemy_hp = {}
    s.enemy_meta = {}

    # ── 5) 逃跑强制：success/fail/概率/auto ──
    r = s.gm_set_escape_force('success')
    ok1 = r['mode'] == 'success' and s._try_escape(None) is True
    r = s.gm_set_escape_force('fail')
    ok2 = r['mode'] == 'fail' and s._try_escape(None) is False
    r = s.gm_set_escape_force(1.0)
    ok3 = s._try_escape(None) is True           # 概率 1.0 必成（确定性）
    r = s.gm_set_escape_force(0.0)
    ok4 = s._try_escape(None) is False          # 概率 0.0 必败（确定性）
    r = s.gm_set_escape_force('auto')
    ok5 = r['mode'] == 'auto' and s._escape_force is None
    check('逃跑强制 success/fail/1.0/0.0/auto 全部生效',
          ok1 and ok2 and ok3 and ok4 and ok5,
          '%s/%s/%s/%s/%s' % (ok1, ok2, ok3, ok4, ok5))
    last = s._escape_last
    check('最近判定被记录（time/rate/forced/success）',
          set(last) >= {'time', 'rate', 'forced', 'success'}, json.dumps(last))
    r = s.gm_set_escape_force(75)
    check('逃跑强制百分比 75 → 0.75',
          abs(s._escape_force - 0.75) < 1e-9, r['mode'])
    s._escape_force = None

    # ── 6) 宠物等级重算：DISPLAY 动、COMBAT/HP/MP/FP 不动 ──
    old_at, old_df = m.PET_AT, m.PET_DF
    old_hp, old_mp, old_fp = m.PET_HP, m.PET_MP, m.PET_FP
    old_d = (m.PET_AT_DISPLAY, m.PET_DF_DISPLAY, m.PET_SPEED_DISPLAY,
             m.PET_MAGDEF_DISPLAY)
    r = s.gm_set_pet_level(30)
    check('petlevel 30 → 真值表变化',
          r['level'] == 30 and r['stats']['at'] > old_d[0],
          'at=%s df=%s sp=%s' % (r['stats']['at'], r['stats']['df'], r['stats']['sp']))
    check('petlevel 不动战斗计算值（37/15）',
          (m.PET_AT, m.PET_DF) == (old_at, old_df))
    check('petlevel 不动 HP/MP/FP（录像/配置所有）',
          (m.PET_HP, m.PET_MP, m.PET_FP) == (old_hp, old_mp, old_fp))
    b2 = s.gm_battle_dict()
    # ★ 2026-10-22：宠物数值改成**实例**所有，模块常量只剩「录像标定基准」身份
    #   （PET_AT/PET_AT_DISPLAY 不再随 GM 变化）。面板快照要与实例一致，
    #   而模块常量必须一动不动 —— 后者是录像 48 伤害的锚，被 GM 改掉就复现不了。
    check('battle 快照 display 与出战实例一致',
          b2['pet']['display']['at'] == (s._pet().get('stats') or {}).get('at'),
          (b2['pet']['display'], s._pet().get('stats')))
    check('GM 改等级不动模块标定常量（录像锚点）',
          (m.PET_AT_DISPLAY, m.PET_DF_DISPLAY, m.PET_SPEED_DISPLAY,
           m.PET_MAGDEF_DISPLAY) == old_d,
          str(old_d))
    # 带 factor_idx 变体：写的是**实例**的覆盖值，模块常量不动
    r = s.gm_set_pet_level(1, factor_idx=63)
    check('petlevel 换 FactorData（实例覆盖 63 狐狸仙子）',
          s._pet().get('factor_idx') == 63 and r['factor_name'] == '狐狸仙子',
          (s._pet().get('factor_idx'), r['factor_name']))
    # 还原实例覆盖，防污染同进程后续测试
    s.gm_set_pet_level(1, factor_idx=0)
    check('还原后实例按造型查表（factor_idx=0 → 造型映射）',
          s._pet().get('factor_idx') == 0 and r['level'] == 1,
          s._pet().get('factor_idx'))

    # ── 7) 敌方等级重算：at/df 随等级、快照跟随 ──
    old_el, old_ea, old_ed = m.ENEMY_LEVEL, m.ENEMY_AT, m.ENEMY_DF
    old_snap = m.ATTACK_DMG
    r = s.gm_set_enemy_level(9)
    check('enemylevel 9 → at/df 随等级变化',
          r['level'] == 9 and (r['at'], r['df']) != (old_ea, old_ed),
          'at=%s df=%s' % (r['at'], r['df']))
    check('伤害快照随敌方常量重算',
          m.ATTACK_DMG != old_snap,
          'snapshot %s→%s' % (old_snap, m.ATTACK_DMG))
    # ★ 故意不标「回原值」：重新缩放回 lv1 得到的是公式值（11/6），
    #   而原值可能来自 world_config 覆盖（31/0）——GM 覆盖配置直到 reload 是**照进的
    #   语义**（gm_set_enemy_level 文档里写了）。测试里直接手工还原到导入值。
    check('enemylevel 回 lv1 → 与 lv9 不同',
          (s.gm_set_enemy_level(old_el)['at'] != r['at']), 'level 回 %s' % old_el)
    m.ENEMY_AT, m.ENEMY_DF = old_ea, old_ed
    m.recompute_dmg_snapshots()
    check('手工还原后快照回原值', m.ATTACK_DMG == old_snap)

    # ── 8) 控制台指令 ──
    for line in ('battle', 'cp', 'cp 50 50 50', 'escape auto',
                 'petlevel 10', 'enemylevel 3'):
        rr = s.gm_command(line)
        check('gm_command %r → ok' % line, rr.get('ok'), rr.get('error', ''))
    check('gm_command escape 显示强制',
          s.gm_command('escape success').get('ok'))
    s._escape_force = None
    check('gm_command help 含新指令',
          all(x in s._GM_CMD_HELP for x in ('battle', 'cp ', 'escape ', 'petlevel', 'enemylevel ')))

    # ── 9) 接线存在性（canary）：debugger 动作 + GM 面板路由 ──
    try:
        import debugger as dbg
        for k in ('battle', 'cp_set', 'escape_force', 'pet_level', 'enemy_level'):
            check('debugger.GM_ACTIONS 含 %s' % k, k in dbg.GM_ACTIONS)
    except Exception as e:  # noqa: BLE001 —— debugger 缺依赖时降级为源码检查
        with open(os.path.join(SIM,
                               'debugger.py'), 'r', encoding='utf-8') as f:
            src = f.read()
        for k in ('battle', 'cp_set', 'escape_force', 'pet_level', 'enemy_level'):
            check('debugger 源码含动作 %s' % k, "'%s'" % k in src)
    with open(os.path.join(SIM,
                           'm4_server.py'), 'r', encoding='utf-8') as f:
        src = f.read()
    for route in ("/api/battle", "api/battle/cp", "api/battle/escape",
                  "api/battle/petlevel", "api/battle/enemylevel"):
        check('_GMHandler 路由 %s' % route, route in src)
    with open(os.path.join(SIM,
                           'gm_dashboard.html'), 'r', encoding='utf-8') as f:
        html = f.read()
    check('GM 面板有战斗调试卡片',
          'id="battleCard"' in html and 'bdPetCD' in html and 'bdEscLast' in html)
    check('GM 面板按错乱分层渲染（battle 值/显示值分列）',
          'bdHeroCD' in html and 'bdEnemyD' in html)

    # ── 结果 ──
    fails = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (sum(1 for _, ok in results if ok), len(results)))
    if fails:
        print('未过:', fails)
        sys.exit(1)


if __name__ == '__main__':
    main()
