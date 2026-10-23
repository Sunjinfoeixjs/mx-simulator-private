# -*- coding: utf-8 -*-
"""GM 开战/胜利走真实链路 + 帧序标红。

要回答的两个问题
----------------
1. **是真实链路吗？**  `gm_war_start()` 必须真的调进 `_start_war`、
   `gm_war_win()` 必须真的调进 `_after_hero_action` 的胜利分支——
   发出来的帧序列要是「打怪打赢」那条路径的同一份，不是直接置 `in_war` 位。
2. **界面凭什么标红？**  GM 动作前后来一次 `last_seq()`，中间发出的每一帧都归这一段
   （`tap.mark_range`）。这里断言：标红区间**恰好等于**这条链路发的帧，不多不少。

为什么单测这条
--------------
帧序即契约（docs/05-contracts.md 第 14 节）。开战/胜利是帧序最容易错的地方
（#8 每场只发一次 WarStart、#17 经验/掉落必须在 WarEnd + resync 之后），
而 GM 又是唯一能**随时**复现它的入口——它要是走了假路径，
拿它验帧序就等于拿假数据验契约。

用法： python tests/_test_war_chain.py
"""
from __future__ import print_function

import os
import re
import shutil
import struct
import sys
import tempfile
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import tap as T                # noqa: E402
import transport               # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('\u3000' + detail) if detail else ''))


# ── 客户端侧最小收发（与 _test_tap.py 同源）──
def recv_s2c(c, timeout=2.0):
    c.settimeout(timeout)

    def exact(n):
        buf = b''
        while len(buf) < n:
            ch = c.recv(n - len(buf))
            if not ch:
                raise ConnectionError('closed')
            buf += ch
        return buf
    n = exact(1)[0]
    if n == 0xFF:
        n = struct.unpack('<H', exact(2))[0]
    p = exact(n)
    return p[0], p[1:]


def send_c2s(c, op, body=b''):
    c.sendall(T.make_c2s(op, body))


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def drain(c, timeout=0.35):
    out = []
    while True:
        try:
            out.append(recv_s2c(c, timeout=timeout)[0])
        except Exception:                                    # noqa: BLE001
            return out


SEED = 0x12345678


def _join_threads(timeout=5.0):
    """等非守护线程收尾：解释器关闭时还活着的工作线程会与 stdout 争锁。

    守护线程本可以不等，但内核 handle 线程在 stop() 后仍需一点时间退出；
    不等它，进程退出瞬间它可能正卡在 print() 上 —— 这就是
    `_enter_buffered_busy` / 0xC0000409 的来源（测试结论已出，却红在退出码上）。
    """
    deadline = time.time() + timeout
    for th in threading.enumerate():
        if th is threading.current_thread() or th.daemon:
            continue
        left = deadline - time.time()
        if left <= 0:
            break
        th.join(left)


# ── 前端渲染校验（node，无浏览器；stub 与 _test_editor.py 同源）──
UI_STUB = r'''
var ctx = new Proxy({}, {
  get: function (t, k) {
    if (k === 'measureText') return function (s) { return { width: String(s).length * 6 }; };
    return function () {};
  },
  set: function (t, k, v) { t[k] = v; return true; }
});
var W = 900, H = 600, els = {};
function fakeEl(id) {
  if (els[id]) return els[id];
  var e = { id: id, style: {}, value: '', width: W, height: H,
    classList: { add: function () {}, remove: function () {}, toggle: function () {}, contains: function () { return false; } },
    dataset: {},
    innerHTML: '', textContent: '', onclick: null, children: [],
    appendChild: function () {}, removeChild: function () {},
    querySelectorAll: function () { return []; },
    querySelector: function () { return null; },
    addEventListener: function () {}, getContext: function () { return ctx; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: W, height: H }; } };
  els[id] = e; return e;
}
global.document = { getElementById: fakeEl,
  createElement: function () { return fakeEl('tmp' + Math.random()); },
  querySelectorAll: function () { return []; }, addEventListener: function () {},
  querySelector: function (s) { return fakeEl(s); } };
global.window = { addEventListener: function () {} };
global.fetch = function () { return new Promise(function () {}); };
global.location = { hash: '' };
global.history = { replaceState: function () {} };
'''

UI_ASSERT = r'''
var T = [];
function chk(n, ok, d) { T.push([n, ok]); console.log((ok ? '[PASS] ' : '[FAIL] ') + n + ' ' + (d || '')); }

// 造两段链路：开战 seq 10..14，胜利 seq 15..18
S.marks = [
  { lo: 10, hi: 14, label: 'GM 开战', kind: 'war_start' },
  { lo: 15, hi: 18, label: 'GM 胜利', kind: 'war_win' }
];
function F(seq, op, wc) {
  return { seq: seq, op: op, name: 'S2CX', dir: 'S2C', size: 4, summary: 's',
           known: true, impl: true, fit: true, ts: '00:00:00',
           warcmd: wc, rows: [], dropped: false, injected: false };
}

chk('链路帧拿到 warcmd 类（列表涂红）',
    rowCls(F(11, 112, true)).indexOf('warcmd') >= 0, rowCls(F(11, 112, true)));
chk('非链路帧不带 warcmd 类',
    rowCls(F(30, 17, false)).indexOf('warcmd') < 0, rowCls(F(30, 17, false)));
chk('链路帧标签标明「真实链路」',
    rowHTML(F(11, 112, true)).indexOf('真实链路') >= 0);
chk('非链路帧没有链路标签',
    rowHTML(F(30, 17, false)).indexOf('真实链路') < 0);
chk('chainOf 能按 seq 反查开战段',
    (chainOf(13) || {}).label === 'GM 开战', JSON.stringify(chainOf(13)));
chk('chainOf 能按 seq 反查胜利段',
    (chainOf(17) || {}).label === 'GM 胜利', JSON.stringify(chainOf(17)));
chk('chainOf 对区间外返回 null', chainOf(99) === null);
chk('标签带区间与帧数',
    chainLabelOf(F(11, 112, true)).indexOf('10-14') >= 0,
    chainLabelOf(F(11, 112, true)));
chk('warcmd 为真但段落缺失时仍给出标签（不空白）',
    chainLabelOf(F(99, 112, true)).length > 0, chainLabelOf(F(99, 112, true)));

// 「只看链路」过滤：只放行 warcmd 帧
S.onlyChain = true;
chk('只看链路：放行链路帧', pass(F(11, 112, true)) === true);
chk('只看链路：拦掉普通帧', pass(F(30, 17, false)) === false);
S.onlyChain = false;
chk('关掉只看链路后普通帧恢复', pass(F(30, 17, false)) === true);

var bad = []; for (var j = 0; j < T.length; j++) { if (!T[j][1]) bad.push(T[j][0]); }
console.log('CHAIN_UI ' + (T.length - bad.length) + '/' + T.length);
if (bad.length) { console.log('未过: ' + bad.join(', ')); process.exit(1); }
'''


def check_frontend_marks():
    """用 node 加载 debugger.html 的前端脚本 + stub，断言「链路标红」的渲染逻辑。

    为什么要单独测前端：后端把区间标对了、界面没涂红，用户看到的还是「没标」。
    这条链路上前后端各有一半，只测后端等于只做了一半。
    """
    if shutil.which('node') is None:
        print('[SKIP] 前端链路渲染校验（未安装 node）')
        return
    html_path = os.path.join(SIM, 'debugger.html')
    try:
        with open(html_path, encoding='utf-8') as f:
            html = f.read()
    except OSError as e:
        check('读取 debugger.html', False, repr(e))
        return
    mm = re.search(r'<script[^>]*>(.*?)</script>', html, re.S)
    if not mm:
        check('debugger.html 含 <script> 块', False)
        return
    tmp = tempfile.mkdtemp(prefix='warchainui_')
    path = os.path.join(tmp, 'dbg_ui.js')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(UI_STUB + '\n' + mm.group(1) + '\n' + UI_ASSERT)
        node = shutil.which('node')
        # ★ 用文件收输出，不用 capture_output（2026-10-20）：
        #   subprocess 的管道靠守护线程读，进程退出时那条线程可能正持有 stdout 缓冲锁,
        #   于是解释器关闭阶段炸 `_enter_buffered_busy`（退出码 0xC0000409）——
        #   测试其实全过了，红的只是退出码。重定向到文件就没有这条线程。
        out_p = os.path.join(tmp, 'out.txt')
        err_p = os.path.join(tmp, 'err.txt')
        with open(out_p, 'wb') as fo, open(err_p, 'wb') as fe:
            rc = subprocess.call([node, path], stdout=fo, stderr=fe, timeout=60)
        with open(out_p, 'rb') as fo:
            out = fo.read().decode('utf-8', 'ignore')
        with open(err_p, 'rb') as fe:
            err = fe.read().decode('utf-8', 'ignore')
        for ln in out.splitlines():
            if ln.startswith('[PASS]') or ln.startswith('[FAIL]'):
                print('   ' + ln)
        ok = rc == 0 and 'CHAIN_UI' in out
        detail = (out.strip().splitlines() or [''])[-1] if out.strip() else err[-300:]
        check('前端链路标红渲染（node 校验 debugger.html）', ok, detail)
    except (OSError, subprocess.SubprocessError) as e:
        check('前端链路标红渲染（node 校验 debugger.html）', False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    tmp = tempfile.mkdtemp(prefix='warchain_')
    p = T.Protocol()
    tap = T.FrameTap(p)
    tap.install(m)

    # 先落一份空档：第二个实例读到它才不会走「新档」分支
    m.M4Server('127.0.0.1', 0, seed=SEED, encounter_every=0, enemies=3,
               turn_timeout=30, logdir=os.path.join(tmp, 'logs'),
               savedir=os.path.join(tmp, 'save'))._persist()

    srv = m.M4Server('127.0.0.1', 0, seed=SEED, encounter_every=0, enemies=3,
                     turn_timeout=30, logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    srv.have_role = True
    srv.hero_name = '阿飞'
    server_end, client_end = transport.make_memory_pair()
    kernel_th = threading.Thread(target=srv.handle, args=(server_end,), daemon=True,
                                 name='war-chain-kernel')
    kernel_th.start()

    try:
        # ① 握手 + 登录 + 进世界
        op, _d = recv_s2c(client_end)
        check('服务端先发 Hello(op1)', op == 1)
        client_end.sendall(hello_resp(SEED))
        time.sleep(0.15)
        send_c2s(client_end, 1, login_body())
        ops = [recv_s2c(client_end)[0] for _ in range(2)]
        check('登录 → AccountInfo(2)+RoleInfo(3)', ops == [2, 3], repr(ops))
        send_c2s(client_end, 2, struct.pack('<I', 1))
        drain(client_end, 0.4)
        check('进入世界（in_war=False）', srv.in_war is False)

        # ══ ①½ 先走一次**自然遇怪**（移动触发的 _start_war），拿到参照帧序列 ══
        # 这条是「真值」：GM 开战必须发出和它结构一致的一段（除了开场回血/回资源那几帧）。
        # ★2026-10-22：暗雷只在场景表内的野外/迷宫发生，起始图永泽城是城镇 → 不遇怪。
        #   这里直接把内核搬到一张有暗雷的野外图（不产生帧，不影响 natural 帧序列）。
        _wild = next((mid for mid in m.MAP_LINKS[m.START_MAP_ID]
                      if mid in m.ENCOUNTER_BY_MAP), None)
        check('起始图邻接里有暗雷场景（用于自然遇怪）', _wild is not None,
              repr(m.MAP_LINKS[m.START_MAP_ID]))
        srv.mapid = _wild
        srv.encounter_every = 1
        lo_n = tap.last_seq()
        send_c2s(client_end, 54, struct.pack('<HH', 45, 40) + bytes([0]))
        drain(client_end, 0.4)
        natural = [f['op'] for f in tap.frames(since=lo_n)]
        check('移动触发遇怪（自然路径同样走 _start_war）',
              srv.in_war is True and 112 in natural, repr(natural))
        srv.gm_war_win()          # 收掉这一场，回到世界
        drain(client_end, 0.4)
        # ══ ② 开战：gm_war_start 必须走真实 _start_war ══
        # 区间下界取**第一个 WarStart(112)**，而不是调用前的 last_seq()：
        # `_start_war` 开头先发「战斗开场回满血/回满资源」的 PropChange(17)，
        # 那是开场前置同步，不是战斗帧。用 last_seq() 会把它们算进链路，
        # 于是「标红区间的第一帧是 WarStart」这条最该一眼看见的事实就没了。
        # （同一条 `_start_war` 里的帧序契约只关心 112 之后的部分。）
        before = tap.last_seq()
        res = srv.gm_war_start()
        hi = tap.last_seq()
        raw = [f for f in tap.frames(since=before) if f['seq'] <= hi]
        i_first = next(i for i, f in enumerate(raw) if f['op'] == 112)
        lo = raw[i_first]['seq']            # 链路真正的起点 = 第一个 WarStart
        marked = tap.mark_range(lo, hi, 'GM 开战', kind='war_start')
        # ★ tap.frames(since=lo) 是**开区间**（seq > lo），直接拿它取链路会丢掉首帧。
        #   这里用 since=lo-1 把首帧包进来——标红区间两端都是闭的。
        chain_ops = [f['op'] for f in tap.frames(since=lo - 1) if f['seq'] <= hi]
        n_enemy = len(srv.enemy_hp)
        check('开战前有开场同步帧（回满血/资源），且落在链路区间外',
              i_first > 0 and all(f['seq'] < lo for f in raw[:i_first]),
              '序 %r / 链路起点 seq %d' % ([f['op'] for f in raw[:i_first]], lo))

        check('gm_war_start 返回 ok', res.get('ok') is True, repr(res))
        check('gm_war_start 之后处于战斗态', srv.in_war is True)
        check('标记区间非空', hi > lo and marked > 0,
              'seq %d..%d，%d 帧' % (lo, hi, marked))

        # 帧序契约（docs/05-contracts.md #8 / #16）：
        #   第一个战斗帧必须是 WarStart(112)，且**整段只有一次**
        #   WarAdd(115) 紧跟在它后面，最后是 WarTurn(114)
        check('开战帧序列以 WarStart(112) 开头',
              chain_ops[:1] == [112],
              'chain=%r raw=%r lo=%d hi=%d' % (chain_ops[:6], [(x['seq'], x['op']) for x in raw], lo, hi))
        check('每场只发一次 WarStart（契约 #8）',
              chain_ops.count(112) == 1, '出现 %d 次' % chain_ops.count(112))
        check('WarStart 后紧跟 WarAdd(115)',
              chain_ops[1:2] == [115], repr(chain_ops[:6]))
        check('含 WarSummon(129)（宠物/队友入场）', 129 in chain_ops, repr(chain_ops))
        check('以 WarTurn(114) 收尾（回合推进开关）',
              chain_ops[-1:] == [114], repr(chain_ops[-4:]))
        n_add = chain_ops.count(115)
        check('WarAdd 条数 = 我方 + 敌方',
              n_add == n_enemy + 1 + (1 if srv._pet_alive else 0)
              + len(getattr(srv, '_team_war_ids', {}) or {}),
              'WarAdd %d 条，敌方 %d' % (n_add, n_enemy))

        # 标红区间必须**恰好**覆盖这段：区间外的帧一个都不许被标
        marked_frames = [f for f in tap.frames() if f.get('warcmd')]
        in_range = [f for f in tap.frames() if lo <= f['seq'] <= hi]
        check('标红帧 == 区间内的帧（不多不少）',
              len(marked_frames) == len(in_range) == hi - lo + 1,
              '标红 %d / 区间内 %d / 跨度 %d'
              % (len(marked_frames), len(in_range), hi - lo + 1))
        check('首帧就是 WarStart',
              bool(marked_frames) and marked_frames[0]['op'] == 112,
              repr(marked_frames[0]['op']) if marked_frames else 'None')

        marks = tap.marks()
        check('tap.marks() 记下这条链路',
              bool(marks) and marks[-1]['lo'] == lo and marks[-1]['hi'] == hi
              and marks[-1]['label'] == 'GM 开战', repr(marks[-1:]))
        check('mark_of(seq) 能反查所属链路',
              (tap.mark_of(lo) or {}).get('kind') == 'war_start'
              and tap.mark_of(lo - 1) is None)

        # ══ ③ 胜利：gm_war_win 必须走真实胜利链 ══
        exp_before = srv.exp
        # ★ 下界必须严格接在开战链路之后（+1）：tap.last_seq() 返回的是「最后一帧的序号」，
        #   直接拿它当 lo 会让那一帧同时属于两段（区间是闭的）——两段在界面上都标红，
        #   而它其实只属于前一段。
        lo2 = tap.last_seq() + 1
        res2 = srv.gm_war_win()
        hi2 = tap.last_seq()
        marked2 = tap.mark_range(lo2, hi2, 'GM 胜利', kind='war_win')
        ops2 = [f['op'] for f in tap.frames(since=lo2 - 1) if f['seq'] <= hi2]

        check('gm_war_win 返回 ok 且击杀数 == 敌方数',
              res2.get('ok') is True and res2.get('killed') == n_enemy, repr(res2))
        check('胜利后退出战斗态', srv.in_war is False)
        check('胜利链有帧', marked2 > 0,
              'seq %d..%d，%d 帧' % (lo2, hi2, marked2))

        # 契约 #17：WarDel×N → WarEnd → resync(SceneM/AddNpc/GotoM) → 经验/掉落
        check('胜利链以 WarDel(116) 开头', ops2[:1] == [116], repr(ops2[:6]))
        i_end = ops2.index(113) if 113 in ops2 else -1
        check('含 WarEnd(113)', i_end >= 0, repr(ops2))
        check('WarEnd 在 WarDel 之后', i_end > 0)
        i_scene = ops2.index(96) if 96 in ops2 else -1
        check('WarEnd → 场景重同步 SceneM(96)（契约 #6/#17）',
              i_scene > i_end, 'WarEnd@%d SceneM@%d' % (i_end, i_scene))
        check('场景重同步带 AddNpc(99) + GotoM(97)（契约 #5）',
              99 in ops2[i_scene:] and 97 in ops2[i_scene:], repr(ops2[i_scene:]))
        after_end = ops2[i_end + 1:] if i_end >= 0 else []
        check('经验 PropChange(17) 在 WarEnd 之后（契约 #17）',
              17 in after_end or srv.exp > exp_before,
              'WarEnd 后 op 序列 %r；exp %r -> %r'
              % (after_end[:14], exp_before, srv.exp))
        check('击杀后敌方表清空', not srv.enemy_hp, repr(srv.enemy_hp))

        marks2 = tap.marks()
        check('开战/胜利记为两条独立段落',
              len(marks2) >= 2 and marks2[-2]['kind'] == 'war_start'
              and marks2[-1]['kind'] == 'war_win',
              '段落 %r' % [(x['kind'], x['lo'], x['hi']) for x in marks2])
        check('两段区间不重叠', marks2[-1]['lo'] > marks2[-2]['hi'],
              '%r vs %r' % (marks2[-2], marks2[-1]))
        _mk = tap.stats(include_tables=False)['mark_kinds']
        check('mark_kinds 计数正确',
              _mk.get('war_start') == 1 and _mk.get('war_win') == 1, repr(_mk))

        # ══ ④ 失败路径也要如实报，不能被吞成 ok ══
        r3 = srv.gm_war_win()          # 已不在战斗中
        check('不在战斗中时 gm_war_win 返回 ok=False',
              r3.get('ok') is False and '战斗' in (r3.get('error') or ''), repr(r3))
        srv.gm_war_start()
        r4 = srv.gm_war_start()        # 重复开战
        check('已在战斗中时 gm_war_start 返回 ok=False',
              r4.get('ok') is False and '战斗' in (r4.get('error') or ''), repr(r4))
        srv.gm_war_win()

        # ══ ⑤ 控制台 / GM 面板 / HTTP 三入口同源 ══
        c1 = srv.gm_command('war start')
        check('控制台 war start 走同一实现',
              c1.get('ok') is True and c1.get('cmd') == 'war start', repr(c1))
        c2 = srv.gm_command('war win')
        check('控制台 war win 走真实胜利链',
              c2.get('ok') is True and c2.get('cmd') == 'war win', repr(c2))
        check('控制台跑完不在战斗态', srv.in_war is False)

        # ══ ⑥ clear() 要把段落一起清掉（否则代数变换后还留着旧红） ══
        tap.clear()
        check('clear() 清空链路段落', tap.marks() == [] and
              tap.stats(include_tables=False)['mark_kinds'] == {},
              repr(tap.marks()))

        # ══ ⑥½ 前端渲染：warcmd 帧必须真的被涂红，且标签带区间 ══
        check_frontend_marks()
        # ══ ⑦ 变异哨兵：mark_range 变空操作，标红必须失效 ══
        lo3 = tap.last_seq()
        srv.gm_war_start()
        hi3 = tap.last_seq()
        saved = tap.mark_range
        try:
            tap.mark_range = lambda *a, **k: 0
            tap.mark_range(lo3, hi3, 'GM 开战', kind='war_start')
        finally:
            tap.mark_range = saved
        check('变异：mark_range 空操作 → 帧不会被标红',
              not [f for f in tap.frames(since=lo3) if f.get('warcmd')])
        tap.mark_range(lo3, hi3, 'GM 开战', kind='war_start')
        check('变异对照：正常 mark_range → 帧被标红',
              len([f for f in tap.frames(since=lo3) if f.get('warcmd')]) == hi3 - lo3,
              '%d 帧' % (hi3 - lo3))
    finally:
        # ★ 先关客户端再停内核（2026-10-20）：反过来的话内核线程还卡在 recv 里，
        #   stop() 只能等它超时，最后被解释器强杀在 print 上 —— 那是
        #   `_enter_buffered_busy` 致命错误（进程 0xC0000409），测试其实已经跑完了。
        try:
            srv._stop = True          # 让 handle 主循环下一轮自己退出
        except Exception:                                    # noqa: BLE001
            pass
        try:
            client_end.close()        # 对端收到哨兵 → recv 返回 b'' → 连接结束
        except Exception:                                    # noqa: BLE001
            pass
        try:
            srv.stop()
        except Exception:                                    # noqa: BLE001
            pass
        # ★ 必须**等**内核线程真的退出（2026-10-20）：
        #   这条线程是 daemon，解释器不会等它。它退出前若正卡在 print() 上,
        #   就会和解释器关闭争 stdout 锁 → Fatal `_enter_buffered_busy`
        #   （退出码 0xC0000409，测试结论其实早已出）。
        kernel_th.join(5.0)
        _join_threads()

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('GM 真实链路 + 帧序标红：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  \u2717 %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
