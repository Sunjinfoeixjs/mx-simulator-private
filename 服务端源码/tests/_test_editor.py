# -*- coding: utf-8 -*-
"""编辑器服务端（editor_server.py）接口级测试。

在隔离目录起 editor_server 实例（独立的 world_config.json / save-dir），
逐一校验：配置读取 → 合法保存 → 非法拒绝 → 还原默认 → 存档读写 → 实时代理降级。
不依赖游戏客户端，也不触碰仓库里真实的 world_config.json。

用法（由回归脚本或手动运行）：python tests/_test_editor.py
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PORT = 8092
BASE = 'http://127.0.0.1:%d' % PORT
HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
PY = sys.executable

if SIM not in sys.path:
    sys.path.insert(0, SIM)
import m4_server as m          # noqa: E402 —— 给「内置默认」的地图数做不写死的对照（全图 60 张）

# ── 前端核心逻辑（命中判定 / 高亮重绘 / 面板填充）的 node 校验素材 ──
# 由 check_ui_logic() 拼成单文件（同作用域）后交给 node 执行。
UI_STUB = r"""
var arcCalls = [];
var ctx = new Proxy({}, {
  get: function (t, k) {
    if (k === 'measureText') return function (s) { return { width: String(s).length * 6 }; };
    return function () { if (k === 'arc') arcCalls.push(Array.prototype.slice.call(arguments)); };
  },
  set: function (t, k, v) { t[k] = v; return true; }
});
var W = 900, H = 600, els = {};
function fakeEl(id) {
  if (els[id]) return els[id];
  var e = { id: id, style: {}, value: '', width: W, height: H,
    classList: { add: function () {}, remove: function () {}, toggle: function () {} },
    innerHTML: '', textContent: '', onclick: null,
    appendChild: function () {}, querySelectorAll: function () { return []; },
    querySelector: function () { return null; },
    addEventListener: function () {}, getContext: function () { return ctx; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: W, height: H }; } };
  els[id] = e; return e;
}
global.document = { getElementById: fakeEl,
  createElement: function () { return fakeEl('tmp' + Math.random()); },
  querySelectorAll: function () { return []; }, addEventListener: function () {} };
global.window = { addEventListener: function () {} };
global.fetch = function () { return new Promise(function () {}); };
"""

UI_ASSERT = r"""
S.cfg = { world: { start_map: 1001, spawn: [45, 40], exit_radius: 4, maps: [{
    mapid: 1001, name: '永泽城', minimap: 1001, spawn: [45, 40],
    npcs: [[1000, 1013, 47, 42, '村长'], [1001, 4427, 60, 55, '传送使']],
    exits: [['新月平原', 342, 19]], links: [1006] }] },
  battle: {}, encounter: { every: 5 } };
S.maps = S.cfg.world.maps; S.selMap = 0; S.sel = null;
computeBounds(); resize(); fit();
var T = [];
function chk(n, ok, d) { T.push([n, ok]); console.log((ok ? '[PASS] ' : '[FAIL] ') + n + ' ' + (d || '')); }
var pt = toScreen(47, 42);
var h1 = hitTest(pt[0], pt[1]);
chk('点圆点命中 NPC', !!h1 && h1.t === 'npc' && h1.i === 0, JSON.stringify(h1));
var h2 = hitTest(pt[0] + 39, pt[1] + 4);
chk('点文字标签也能命中 NPC', !!h2 && h2.t === 'npc' && h2.i === 0, JSON.stringify(h2));
chk('点空白处不命中', hitTest(pt[0] + 400, pt[1] + 400) === null);
function rings() { var n = 0; for (var i = 0; i < arcCalls.length; i++) { if (Math.abs(arcCalls[i][2] - 11) < 0.001) n++; } return n; }
S.sel = null; arcCalls.length = 0; draw(); var b0 = rings();
S.sel = { t: 'npc', i: 0 }; arcCalls.length = 0; draw(); var a1 = rings();
chk('选中后画出高亮圆环', b0 === 0 && a1 >= 1, 'before=' + b0 + ' after=' + a1);
renderObjProps();
chk('选中后面板表单填充', fakeEl('o_id').value === 1000 && fakeEl('o_shape').value === 1013 &&
  fakeEl('o_name').value === '村长' && fakeEl('o_x').value === 47 && fakeEl('o_y').value === 42,
  'id=' + fakeEl('o_id').value + ' name=' + fakeEl('o_name').value);
var bad = []; for (var j = 0; j < T.length; j++) { if (!T[j][1]) bad.push(T[j][0]); }
console.log('UI_LOGIC ' + (T.length - bad.length) + '/' + T.length);
if (bad.length) { console.log('未过: ' + bad.join(', ')); process.exit(1); }
"""


def check_ui_logic():
    """用 node 加载 editor.html 的前端脚本 + stub，断言前端核心逻辑。未装 node 则跳过。"""
    if shutil.which('node') is None:
        print('[SKIP] 前端逻辑校验（未安装 node）')
        return
    with open(os.path.join(SIM, 'editor.html'), encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'<script[^>]*>(.*?)</script>', html, re.S)
    if not m:
        check('editor.html 含 <script> 块', False)
        return
    tmp = tempfile.mkdtemp(prefix='m4edjs_')
    path = os.path.join(tmp, 'ed_ui.js')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(UI_STUB + '\n' + m.group(1) + '\n' + UI_ASSERT)
        r = subprocess.run(['node', path], capture_output=True, timeout=60)
        out = (r.stdout or b'').decode('utf-8', 'ignore')
        for ln in out.splitlines():
            if ln.startswith('[PASS]') or ln.startswith('[FAIL]'):
                print(ln)
        ok = r.returncode == 0 and 'UI_LOGIC' in out
        detail = out.strip().splitlines()[-1] if out.strip() else (r.stderr or b'').decode('utf-8', 'ignore')[-200:]
        check('前端核心逻辑（node 校验 editor.html）', ok, detail)
    except (OSError, subprocess.SubprocessError) as e:
        check('前端核心逻辑（node 校验 editor.html）', False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))


def http(path, method='GET', body=None, timeout=5):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8'))
        except Exception:                       # noqa: BLE001
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {'error': str(e)}


def wait_port(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(('127.0.0.1', port))
            return True
        except OSError:
            time.sleep(0.25)
        finally:
            s.close()
    return False


def main():
    tmp = tempfile.mkdtemp(prefix='m4editor_')
    cfg_path = os.path.join(tmp, 'world_config.json')
    save_dir = os.path.join(tmp, 'save')
    os.makedirs(save_dir, exist_ok=True)
    proc = subprocess.Popen(
        [PY, os.path.join(SIM, 'editor_server.py'),
         '--port', str(PORT), '--config', cfg_path, '--save-dir', save_dir,
         '--live', 'http://127.0.0.1:59999'],       # 故意指向不存在的 GM 端口
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            out = proc.stdout.read().decode('utf-8', 'ignore') if proc.stdout else ''
            check('编辑器服务启动', False, out[-300:])
            return 1

        # ── 1. 无配置时按内置默认生成 ──
        st, r = http('/api/config')
        cfg = r.get('config') or {}
        check('GET /api/config → 按内置默认生成', st == 200 and r.get('generated') is True
              and not r.get('errors') and len(cfg.get('world', {}).get('maps', [])) == len(m.MAPS),
              'maps=%s（期望 %d）errors=%s'
              % (len(cfg.get('world', {}).get('maps', [])), len(m.MAPS), r.get('errors')))
        base_atk = cfg['battle']['hero_at']

        # ── 2. 合法保存（改攻击力） ──
        cfg['battle']['hero_at'] = 55
        st, r = http('/api/config', 'POST', cfg)
        check('POST /api/config 合法配置 → 保存成功', st == 200 and r.get('ok') is True,
              'errors=%s' % r.get('errors'))
        on_disk = json.load(open(cfg_path, encoding='utf-8')) if os.path.isfile(cfg_path) else {}
        check('保存已落盘（hero_at=55）', on_disk.get('battle', {}).get('hero_at') == 55,
              'disk=%s' % on_disk.get('battle', {}).get('hero_at'))

        # ── 3. 重新读取反映改动 ──
        st, r = http('/api/config')
        check('重读配置反映改动', r.get('config', {}).get('battle', {}).get('hero_at') == 55)

        # ── 4. 非法配置被拒（并保持文件不变） ──
        bad = json.loads(json.dumps(cfg))
        bad['battle']['hero_at'] = -1
        st, r = http('/api/config', 'POST', bad)
        still = json.load(open(cfg_path, encoding='utf-8')).get('battle', {}).get('hero_at')
        check('非法配置被拒且文件未变', st == 400 and r.get('ok') is False and still == 55,
              'status=%s errors=%s disk=%s' % (st, r.get('errors'), still))

        # ── 4b. 配置版本落后 → 整体跳过 + 横幅告警（2026-10-20）──
        #   为什么必须跳过而不是部分应用：旧版配置缺新字段时，apply_to 的
        #   setattr 循环会静默跳过那些键，结果「文件在、应用了、但一半参数没生效」，
        #   比直接跳过更难查。
        import world_config as _W
        _cfg_old = json.loads(json.dumps(cfg))
        _cfg_old['version'] = _W.VERSION - 1
        _p_old = os.path.join(tmp, 'cfg_old.json')
        json.dump(_cfg_old, open(_p_old, 'w', encoding='utf-8'), ensure_ascii=False)
        _c, _reason, _detail = _W.probe_config(_p_old)
        check('版本落后 → probe_config 返回 version_stale',
              _reason == 'version_stale' and _c is None,
              'reason=%r detail=%r' % (_reason, _detail))
        _ok_old, _msg_old = _W.apply_to(m, _p_old)
        check('版本落后 → apply_to 拒绝应用',
              _ok_old is False and '版本落后' in _msg_old,
              'ok=%r msg=%r' % (_ok_old, _msg_old))
        check('版本落后 → 消息含「已跳过」', '已跳过' in _msg_old, _msg_old)

        # 横幅必须明确告警（这是用户唯一能看见的地方）
        _saved_state = dict(m.WORLD_CONFIG_STATE)
        try:
            m.WORLD_CONFIG_STATE.update(applied=False,
                                         reason='version_stale',
                                         message=_msg_old)
            _banner = m.dmg_banner()
            # ★ 必须查**告警行**（以 ⚠⚠ 开头），不能只查「参数来源：」那行——
            #   后者是普通信息行，不告警时也可能含同样的文字，会假绿。
            _warn_lines = [l for l in _banner.split(chr(10))
                           if '⚠⚠' in l and '版本' in l]
            check('横幅有独立的 ⚠⚠ 告警行（不是普通信息行）',
                  bool(_warn_lines),
                  '告警行=%r' % (_warn_lines[:1],))
            check('告警行含「配置版本落后，已跳过」',
                  any('配置版本落后，已跳过' in l for l in _warn_lines),
                  _warn_lines[:1])
            check('横幅说明当前跑的是内置默认值',
                  '内置默认值' in _banner)
            check('横幅给出修复方式',
                  '重新生成' in _banner or '删掉该文件' in _banner)
        finally:
            m.WORLD_CONFIG_STATE.clear()
            m.WORLD_CONFIG_STATE.update(_saved_state)

        # 版本超前同样跳过（程序太旧）
        _cfg_new = json.loads(json.dumps(cfg))
        _cfg_new['version'] = _W.VERSION + 5
        _p_new = os.path.join(tmp, 'cfg_new.json')
        json.dump(_cfg_new, open(_p_new, 'w', encoding='utf-8'), ensure_ascii=False)
        _c2, _r2, _d2 = _W.probe_config(_p_new)
        check('版本超前 → probe_config 返回 version_newer',
              _r2 == 'version_newer' and _c2 is None,
              'reason=%r' % (_r2,))
        _ok_new, _msg_new = _W.apply_to(m, _p_new)
        check('版本超前 → apply_to 拒绝应用且提示升级',
              _ok_new is False and '比程序新' in _msg_new,
              'msg=%r' % (_msg_new,))

        # 当前版本 → 正常应用（不能把校验写成「一律拒绝」）
        _cfg_ok = json.loads(json.dumps(cfg))
        _cfg_ok['version'] = _W.VERSION
        _p_okv = os.path.join(tmp, 'cfg_ok.json')
        json.dump(_cfg_ok, open(_p_okv, 'w', encoding='utf-8'), ensure_ascii=False)
        _c3, _r3, _d3 = _W.probe_config(_p_okv)
        check('当前版本 → probe_config 返回 ok',
              _r3 == 'ok' and isinstance(_c3, dict), 'reason=%r' % (_r3,))

        # ── 5. 出口指向不存在的地图 → 被拒 ──
        bad2 = json.loads(json.dumps(cfg))
        bad2['world']['maps'][0]['exits'][0][0] = '不存在的场景'
        st, r = http('/api/config', 'POST', bad2)
        check('出口目标不存在 → 校验拦截', st == 400 and r.get('ok') is False,
              'errors=%s' % r.get('errors'))

        # ── 6. 还原内置默认 ──
        st, r = http('/api/config/reset', 'POST', {})
        check('还原内置默认（hero_at 回到 %d）' % base_atk,
              st == 200 and r.get('ok') is True
              and r.get('config', {}).get('battle', {}).get('hero_at') == base_atk,
              'now=%s' % r.get('config', {}).get('battle', {}).get('hero_at'))

        # ── 7. 存档读写 ──
        st, r = http('/api/role', 'POST', {'px': 77, 'py': 88, 'hero_hp': 321, 'coins': 999})
        check('POST /api/role 写入存档', st == 200 and r.get('ok') is True, 'errors=%s' % r.get('errors'))
        st, r = http('/api/role')
        role = r.get('role') or {}
        check('GET /api/role 回读一致', role.get('px') == 77 and role.get('hero_hp') == 321,
              'px=%s hp=%s' % (role.get('px'), role.get('hero_hp')))
        st, r = http('/api/role', 'POST', {'px': 'abc'})
        check('存档非法字段被拒', st == 400 and r.get('ok') is False, 'errors=%s' % r.get('errors'))

        # ── 8. 实时代理不可达时优雅降级 ──
        st, r = http('/api/live')
        check('GM API 不可达 → 503 且带 error（不崩）', st == 503 and r.get('error'), 'r=%s' % r)
        st, r = http('/api/meta')
        check('meta 报告 live_ok=false', st == 200 and r.get('live_ok') is False, 'meta=%s' % r)

        # ── 9. 前端页面可访问 ──
        req = urllib.request.Request(BASE + '/')
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8')
        check('GET / 返回编辑器页面', resp.status == 200 and '模拟服数据可视化编辑器' in html
              and 'canvas' in html, 'len=%d' % len(html))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:                       # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    check_ui_logic()

    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
