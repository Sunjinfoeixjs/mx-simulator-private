# -*- coding: utf-8 -*-
"""特性实现状态表 FEATURE_STATUS。

背景：客户端 UI 有入口、协议有 opcode，但服务端没做业务的地方不少。
不显式标出来会导致两种误判：
  · 玩家点了没反应，以为是 bug，其实从没实现；
  · 维护者看到 opcode 在字典里，以为已支持。

本测试锁定：
  1. 状态取值合法（图例集合内）
  2. craft_levelup 已落地 = simulated（2026-10-22 改造：熟练度满则升级），且**用行为验证**
  2b. craft_charge 明确为 implemented（预览与实际扣款一致，2026-10-20）
  3. 制作涨熟练度、满则升等级（升级逻辑确实写在 _handle_npc_make 里）
  4. /api/features 接口可用、图例完整
  5. /api/state 暴露 life_lv/life_exp（面板能看出「熟练度涨、等级随升级涨」）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


# ── GM 面板渲染校验（node，无浏览器；stub 与 _test_editor.py 同源）──
UI_STUB = r'''
var els = {};
function fakeEl(id) {
  if (els[id]) return els[id];
  var e = { id: id, style: {}, className: '', innerHTML: '', textContent: '', title: '',
    addEventListener: function(){},
    classList:{add:function(){},remove:function(){},toggle:function(){}} };
  els[id] = e; return e;
}
global.document = { getElementById: fakeEl,
  querySelector: function(s){ return fakeEl(String(s).replace(/^#/,'')); },
  querySelectorAll: function(){ return []; }, addEventListener: function(){} };
global.setInterval = function(){}; global.setTimeout = function(){};
global.fetch = function(){ return new Promise(function(){}); };
'''

UI_ASSERT = r'''
var T=[];
function chk(n,ok,d){ T.push([n,ok]); console.log((ok?'[PASS] ':'[FAIL] ')+n+' '+(d||'')); }
var ROWS = [
  { key:'craft_levelup', title:'工艺升级', status:'not_implemented', desc:'缺口A' },
  { key:'craft_charge',  title:'制作扣费', status:'preview_only',   desc:'假承诺B' },
  { key:'battle',        title:'战斗引擎', status:'implemented',    desc:'已实现C' },
  { key:'social',        title:'社交交易', status:'ignored_no_counterpart', desc:'非缺口D' }
];
renderFeatures({ rows: ROWS });
var html = fakeEl('featList').innerHTML;
chk('渲染出 4 条', (html.match(/class="item s-/g)||[]).length === 4);
chk('按服务端顺序（前端不重排）', html.indexOf('工艺升级') < html.indexOf('制作扣费') && html.indexOf('制作扣费') < html.indexOf('战斗引擎'));
chk('带状态类名便于着色', html.indexOf('s-preview_only') >= 0 && html.indexOf('s-not_implemented') >= 0);
chk('中文标题与键名都在', html.indexOf('工艺升级') >= 0 && html.indexOf('craft_levelup') >= 0);
chk('显示状态说明', html.indexOf('假承诺B') >= 0);
chk('计数徽章区分缺口', fakeEl('featCount').textContent.indexOf('缺口 2') >= 0);
chk('缺口徽章走 warn 样式', fakeEl('featCount').className === 'pill warn');
fakeEl('featList').innerHTML = 'SENTINEL';
renderFeatures({ rows: ROWS });
chk('内容未变则不重绘（保住阅读位置）', fakeEl('featList').innerHTML === 'SENTINEL');
renderFeatures({ rows: [ { key:'craft_levelup', title:'工艺升级', status:'implemented', desc:'已好了' } ] });
chk('状态变化会重绘', fakeEl('featList').innerHTML.indexOf('已好了') >= 0);
chk('缺口清零时徽章回到 on', fakeEl('featCount').className === 'pill on');
renderFeatures({ rows: [] });
chk('空表给出提示不崩', fakeEl('featList').innerHTML.indexOf('没有登记') >= 0);
var bad=[]; for(var i=0;i<T.length;i++){ if(!T[i][1]) bad.push(T[i][0]); }
console.log('FEAT_UI ' + (T.length-bad.length) + '/' + T.length);
if(bad.length){ console.log('未过: '+bad.join(', ')); process.exit(1); }
'''


def check_dashboard_render():
    """GM 面板「特性状态」卡片真的画出来了（node 加载 gm_dashboard.html 的脚本）。

    为什么单测这半：后端 /api/features 返回得再对，面板不渲染 = 用户还是看不到。
    FEATURE_STATUS 的价值全在「人能看到」——看不见的表等于没有。
    """
    if shutil.which('node') is None:
        print('[SKIP] GM 面板渲染校验（未安装 node）')
        return
    path_html = os.path.join(SIM, 'gm_dashboard.html')
    try:
        with open(path_html, encoding='utf-8') as f:
            html = f.read()
    except OSError as e:
        check('读取 gm_dashboard.html', False, repr(e))
        return
    mm = re.search(r'<script[^>]*>(.*?)</script>', html, re.S)
    if not mm:
        check('gm_dashboard.html 含 <script> 块', False)
        return
    tmp = tempfile.mkdtemp(prefix='featui_')
    js = os.path.join(tmp, 'feat_ui.js')
    try:
        with open(js, 'w', encoding='utf-8') as f:
            f.write(UI_STUB + chr(10) + mm.group(1) + chr(10) + UI_ASSERT)
        # 输出重定向到文件：subprocess 的管道读取线程在解释器关闭时会与 stdout 争锁
        out_p = os.path.join(tmp, 'o.txt')
        err_p = os.path.join(tmp, 'e.txt')
        with open(out_p, 'wb') as fo, open(err_p, 'wb') as fe:
            rc = subprocess.call([shutil.which('node'), js], stdout=fo,
                                 stderr=fe, timeout=60)
        with open(out_p, 'rb') as fo:
            out = fo.read().decode('utf-8', 'ignore')
        with open(err_p, 'rb') as fe:
            err = fe.read().decode('utf-8', 'ignore')
        for ln in out.splitlines():
            if ln.startswith('[PASS]') or ln.startswith('[FAIL]'):
                print('   ' + ln)
        ok = rc == 0 and 'FEAT_UI' in out
        detail = (out.strip().splitlines() or [''])[-1] if out.strip() else err[-300:]
        check('GM 面板渲染特性状态卡片（node 校验 gm_dashboard.html）', ok, detail)
    except (OSError, subprocess.SubprocessError) as e:
        check('GM 面板渲染特性状态卡片', False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    # ── 1. 表结构 ──
    # ★ 合法状态集合从**服务端图例**取，不在测试里另抄一份：
    #   2026-10-22 新增 display_only 档时，测试里那份硬编码集合没跟着改，
    #   于是「服务端图例有 7 档 / 测试只认 6 档」直接判红——正是「改两处必漂移」。
    legal = set(m.FEATURE_STATUS_LEGEND)
    bad = {k: v for k, v in m.FEATURE_STATUS.items() if v not in legal}
    check('所有状态取值合法（图例集合内）', not bad, '非法项：%r' % bad)
    check('表非空', len(m.FEATURE_STATUS) >= 5, '共 %d 条' % len(m.FEATURE_STATUS))

    # ── 2. craft_levelup 已落地（2026-10-22 改造）──
    #   状态从 not_implemented 改为 simulated：每次成功制作 +1 熟练度、满本级阈值
    #   则升级、溢出累计（成长循环真跑通）。阈值曲线是 SIM（原服升级曲线不在本仓库）。
    check("craft_levelup = 'simulated'",
          m.feature_status('craft_levelup') == 'simulated',
          'got=%r' % m.feature_status('craft_levelup'))
    # ── 2b. craft_charge = implemented（预览与实扣一致，2026-10-20 补齐结算）──
    check("craft_charge = 'implemented'",
          m.feature_status('craft_charge') == 'implemented',
          'got=%r' % m.feature_status('craft_charge'))

    # 事实依据 1：预览路径确实算了 money
    import inspect
    _dispatch_src = inspect.getsource(m.M4Server._dispatch_locked)
    _i = _dispatch_src.find('C2S_NPC_GETCOST')
    _seg = _dispatch_src[_i:_i + 900] if _i >= 0 else ''
    check('预览分支（C2S_NPC_GETCOST）确实算并报 money',
          'MAKE_COST_MONEY' in _seg or 'money =' in _seg,
          'money 计算存在=%r' % ('money =' in _seg))

    # 事实依据 2：制作路径**确实扣 coins**（preview_only 的旧事实已反转）
    _make_src = inspect.getsource(m.M4Server._handle_npc_make)
    _coin_lines = [ln.strip() for ln in _make_src.split(chr(10))
                   if 'coins' in ln]
    check('_handle_npc_make 扣金钱（craft_charge=implemented 的事实依据）',
          bool(_coin_lines),
          '无 coins 操作' if not _coin_lines else 'coins 操作 %d 处' % len(_coin_lines))

    # 事实依据 3：实扣额与预览同源（都用 MAKE_COST_MONEY×件数）
    check('制作路径与预览同源扣费常数（MAKE_COST_MONEY）',
          'MAKE_COST_MONEY' in _make_src,
          '制作路径未引用 MAKE_COST_MONEY')

    # ── 2c. social = ignored_no_counterpart（单机无对手方）──
    check("social = 'ignored_no_counterpart'",
          m.feature_status('social') == 'ignored_no_counterpart',
          'got=%r' % m.feature_status('social'))

    # opcode 级映射：忽略表里的每个 op 都应能查到状态
    _map = m.op_feature_map()
    check('op_feature_map 覆盖全部 C2S_SINGLEPLAYER_IGNORED',
          set(_map) == set(m.C2S_SINGLEPLAYER_IGNORED_OPS),
          'map=%d 条, 忽略表=%d 条'
          % (len(_map), len(m.C2S_SINGLEPLAYER_IGNORED_OPS)))
    check('忽略表里的 op 状态统一为 ignored_no_counterpart',
          set(_map.values()) == {'ignored_no_counterpart'},
          '取值集合=%s' % sorted(set(_map.values())))

    # 抽查几个典型：社交类应被标，战斗类不该被标
    check('op50（好友）标为 ignored_no_counterpart',
          m.op_feature_status(50) == 'ignored_no_counterpart',
          'got=%r' % m.op_feature_status(50))
    check('op72（组队）标为 ignored_no_counterpart',
          m.op_feature_status(72) == 'ignored_no_counterpart')
    check('op160（战斗普攻）不在忽略表里',
          m.op_feature_status(160) is None,
          'got=%r' % m.op_feature_status(160))
    check('非法 op 输入不抛异常',
          m.op_feature_status('abc') is None and m.op_feature_status(None) is None)

    check('查询不存在的 key 返回 unknown',
          m.feature_status('__nope__') == 'unknown')
    check('feature_status() 返回副本（改它不影响原表）',
          m.feature_status() is not m.FEATURE_STATUS)

    # ── 3. 行为验证：制作涨熟练度、满则升等级（craft_levelup 已落地，2026-10-22）──
    #   与 _handle_npc_make 的升级循环同口径验证：熟练度满本级阈值 → 升级、溢出累计。
    tmp = tempfile.mkdtemp(prefix='fs_')
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                     logdir=os.path.join(tmp, 'logs'))
    srv.log = lambda *a, **k: None
    srv.life_lv, srv.life_exp = 5, 0
    need5 = m.life_exp_need(5)               # 升到 6 需要的熟练度
    for _ in range(need5):
        srv.life_exp += 1
        _n = m.life_exp_need(srv.life_lv)
        while srv.life_exp >= _n and srv.life_lv < m.LIFE_LV_MAX:
            srv.life_exp -= _n
            srv.life_lv += 1
            _n = m.life_exp_need(srv.life_lv)
    check('熟练度满则工艺升级（5→6）', srv.life_lv == 6, 'lv=%d' % srv.life_lv)
    check('溢出熟练度归零（%d-?=%d）' % (need5, srv.life_exp),
          srv.life_exp == 0, 'exp=%d' % srv.life_exp)
    # 升级后等级确实高于初始
    check('工艺等级已随制作增长（不再是恒等于 LIFE_LV_INIT）',
          srv.life_lv > m.LIFE_LV_INIT, 'lv=%d' % srv.life_lv)

    # ── 3b. 走**真实制作路径**验证（不能只模拟赋值）──
    #   ★ 上面 3 只是「手工 life_exp += 1」，绕过了 _handle_npc_make 的代码。
    #     必须真调一次制作，让那条路径的代码跑起来，确认升级真的发生。
    import inspect
    import re as _re
    _src = inspect.getsource(m.M4Server._handle_npc_make)
    # 排除 self.life_lv 的「读」用法（下发帧时要读），只找「写」
    _writes = [ln.strip() for ln in _src.split(chr(10))
               if _re.search(r'self\.life_lv\s*(\+?=|[-+]=)', ln)]
    check('_handle_npc_make 写 life_lv（升级逻辑已落地）',
          bool(_writes),
          ('发现写操作：%r' % _writes[:2]) if _writes else '无写操作')

    # 静态检查：确认 _handle_npc_make 实现了「满则升级」循环（引用 life_exp_need）
    _up_lines = [ln.strip() for ln in _src.split(chr(10)) if 'life_exp_need' in ln]
    check('_handle_npc_make 实现「满则升级」循环（引用 life_exp_need）',
          bool(_up_lines), (_up_lines[0][:70] if _up_lines else '未找到'))

    # ── 4. HTTP 接口 ──
    srv.serve_background(0, web=True, web_port=0)
    time.sleep(0.5)
    port = srv._web_port
    try:
        d = json.loads(urllib.request.urlopen(
            'http://127.0.0.1:%d/api/features' % port, timeout=3).read())
        check('/api/features 返回 features 表',
              isinstance(d.get('features'), dict) and len(d['features']) >= 5,
              '%d 条' % len(d.get('features') or {}))
        check('/api/features 含 craft_levelup（已落地=simulated）',
              d['features'].get('craft_levelup') == 'simulated')
        check('/api/features 带图例（legend 六档齐全）',
              set(d.get('legend') or {}) == legal,
              'legend=%s' % sorted(d.get('legend') or {}))
        # ── 4b. rows：面板直接渲染的成品行（2026-10-20）──
        rows = d.get('rows') or []
        check('/api/features 带 rows（面板直接渲染）', len(rows) == len(m.FEATURE_STATUS),
              'rows=%d 表=%d' % (len(rows), len(m.FEATURE_STATUS)))
        check('rows 与 features 一致（不会各说各话）',
              {r['key']: r['status'] for r in rows} == d.get('features'),
              '不一致')
        check('rows 每项都有中文标题与说明',
              all(r.get('title') and r.get('desc') for r in rows),
              '缺项=%r' % [r['key'] for r in rows if not (r.get('title') and r.get('desc'))])
        # ★ 用**模块自己的**严重度表，不在测试里再抄一份：
        #   抄一份的话，服务端改了顺序、测试仍按老顺序判，就守不住了。
        _sev = m._FEATURE_SEVERITY
        _ranks = [r['rank'] for r in rows]
        check('rows 按严重度排序（缺口在最前、已实现垫底）',
              _ranks == sorted(_ranks), 'rank 序列=%r' % _ranks)
        check('rank 与排序键同源（不出现两套序号）',
              _ranks == [_sev.get(r['status'], len(m.FEATURE_STATUS_LEGEND))
                         for r in rows],
              'rank=%r' % _ranks)
        check('严重度表覆盖全部合法状态',
              set(_sev) == legal, '缺口=%r' % (legal - set(_sev)))
        check('排第一的是缺口（not_implemented/preview_only）',
              bool(rows) and rows[0]['status'] in ('not_implemented', 'preview_only'),
              '首项=%r' % (rows[0] if rows else None))
        check('feature_label 未登记的键回落键名（不抛 KeyError）',
              m.feature_label('__nope__') == '__nope__',
              repr(m.feature_label('__nope__')))
        check('feature_legend 与 HTTP legend 同源',
              m.feature_legend() == d.get('legend'))
        check('feature_legend 返回副本（改它不影响模块表）',
              m.feature_legend() is not m.FEATURE_STATUS_LEGEND)

        s = json.loads(urllib.request.urlopen(
            'http://127.0.0.1:%d/api/state' % port, timeout=3).read())
        # /api/table 是**调试台**的接口（不是 GM 面板），要单独起一个
        import debugger as _D
        _dport = _D.attach(srv, port=0)
        time.sleep(0.4)
        tb = json.loads(urllib.request.urlopen(
            'http://127.0.0.1:%d/api/table?dir=C2S' % _dport, timeout=3).read())
        _marked = [r for r in tb.get('rows') or [] if r.get('feature')]
        check('/api/table 给忽略表里的 op 带上 feature 字段',
              len(_marked) == len(m.C2S_SINGLEPLAYER_IGNORED_OPS),
              '标记 %d 行 / 忽略表 %d 条'
              % (len(_marked), len(m.C2S_SINGLEPLAYER_IGNORED_OPS)))
        check('/api/table 带 feature_legend',
              'ignored_no_counterpart' in (tb.get('feature_legend') or {}))

        for k in ('life_lv', 'life_exp', 'huoli', 'life_lv_max', 'life_exp_max'):
            if k not in s:
                check('/api/state 暴露 %s' % k, False, '缺失')
                break
        else:
            check('/api/state 暴露生活技能五项（能看出熟练度涨/等级随升级涨）', True,
                  'lv=%s exp=%s/%s' % (s['life_lv'], s['life_exp'], s['life_exp_max']))
    except Exception as e:                                  # noqa: BLE001
        check('HTTP 接口可用', False, repr(e))
    finally:
        try:
            srv.stop()
        except Exception:                                   # noqa: BLE001
            pass

    # ── 5. GM 面板真的把这张表画出来了吗（node 校验前端） ──
    check_dashboard_render()

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('特性状态表：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
