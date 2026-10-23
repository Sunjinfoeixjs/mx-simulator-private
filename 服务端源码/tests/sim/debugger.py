# -*- coding: utf-8 -*-
"""可视化调试台 —— 协议帧流 / 地图 / 状态 / 覆盖 四合一本地 Web 界面。

它解决什么
----------
《项目完成度评估》把「C2S 覆盖 30.7% / S2C 21.6%」列为头号待办，而在此之前
**没有任何一处在看真实字节流**：`m4_server` 只有 `self.log(...)` 的文字描述，
未识别的指令只留一行「（未识别 op=N）」，既不知道对方发了什么，也不知道自己
回了什么结构。本调试台把那条字节流变成可读、可搜、可注入、可统计的界面。

四个页签
--------
帧流      上下行实时帧列表（opcode / 名称 / 摘要 / 方向），点开看逐字段表 + hex。
          未识别、未实现、布局不符的帧会被标红——这就是待完善清单。
地图      把 SceneM / AddNpc / MapTrack / GotoM 翻译成一张实时地图：
          玩家位置、NPC、地图出口、战斗单位。
协议覆盖  本会话出现过的 C2S/S2C 编码 vs 全表，三态（已实现/未实现/未出现）。
状态/GM   实时状态 + 一键 GM 动作 + 帧注入 + 丢帧。等价于 GM 面板但走进程内直调。

架构
----
    debugger.html  ←HTTP/SSE→  debugger.py  ─┬─ FrameTap（tap.py）   ← 真实字节流
                                             ├─ M4Server 实例（进程内直调，无 HTTP 中转）
                                             └─ world_config.json    ← 地图/NPC/出口
    singleplayer.py 负责拉起内核与客户端，本模块只「挂上去」：`attach(srv, port)`。

安全边界：只绑 127.0.0.1，与 GM 面板一致（调试面不暴露局域网）。

用法::
    python debugger.py                     # 一键：内核 + 客户端 + 调试台
    python debugger.py --no-client         # 只起内核 + 调试台（脚本联调）
    python debugger.py --port 8090 --no-encounter
    python debugger.py --dump 会话.jsonl   # 退出时自动导出会话

仅使用标准库。
"""
import argparse
import io
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m4_server as m            # noqa: E402
import tap as T                  # noqa: E402
import bus                       # noqa: E402   业务事件总线（任务链/经济页签）

HTML_PATH = os.path.join(HERE, 'debugger.html')
DEFAULT_PORT = 8090
BASE_HINT = 'http://127.0.0.1:%d'

# 全局调试台状态（单进程单实例；HTTP 处理器通过它拿到内核与抓包器）
STATE = {
    'tap': None,
    'srv': None,
    'maps': [],
    'started': time.time(),
    'dump': '',
    'client_dir': '',
}


# ══════════════════════════════════════════════════════════ 挂载 ══
def attach(srv, port=DEFAULT_PORT, dump='', client_dir=''):
    """把调试台挂到已就绪的内核上，返回实际使用的端口。

    必须在拉起客户端**之前**调用：`install()` 换掉的是 `M4Server.handle`，
    只对之后接入的连接生效。
    """
    tap = T.FrameTap()
    installed = tap.install(m)
    STATE['tap'] = tap
    STATE['srv'] = srv
    STATE['dump'] = dump
    STATE['client_dir'] = client_dir
    STATE['maps'] = _load_maps()
    Handler.broadcaster = Broadcaster(tap)

    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    # ★ 回填实际端口（2026-10-20）：port=0 时由 OS 分配。
    #   旧实现拿入参打日志并 return，传 0 就打印「http://127.0.0.1:0/」、
    #   返回 0，调用方根本连不上（测试里表现为 URLError 10049）。
    port = httpd.server_address[1]
    STATE['httpd'] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name='debugger-http').start()
    print('[调试台] http://127.0.0.1:%d/ （仅回环）' % port)
    print('[调试台] 协议字典：%s%s'
          % ('已加载 C2S %d / S2C %d'
             % (len(tap.proto.table('C2S')), len(tap.proto.table('S2C')))
             if tap.proto.ok else '加载失败 → %s（仅 hex 视图）' % tap.proto.error,
             '' if installed else '（此前已挂载，未重复安装）'))
    return port


def _load_maps():
    """取世界地图定义，供地图页签画底图（失败则返回空，界面退化为纯轨迹）。"""
    try:
        import world_config
        cfg = world_config.load_config()
        if not cfg:
            return []
        return [{'mapid': mp['mapid'], 'name': mp['name'], 'minimap': mp['minimap'],
                 'spawn': mp['spawn'], 'npcs': mp['npcs'], 'exits': mp['exits'],
                 'links': mp['links']}
                for mp in cfg['world']['maps']]
    except Exception as e:                        # noqa: BLE001
        print('[调试台] 世界配置读取失败（地图页签将为空）：%r' % e)
        return []


def _srv():
    return STATE['srv']


def _tap():
    return STATE['tap']


# ══════════════════════════════════════════════════════════ SSE ══
class Broadcaster:
    """把抓包器的逐帧回调攒成批次，按 ~10Hz 推给所有 SSE 订阅者。

    逐帧直推会让浏览器每秒重排上百次；批推把「帧列表」做成稳定的流。
    """

    BEAT_IDLE = 2.0    # 平时 state/stats 心跳间隔
    BEAT_WAR = 0.3     # 战斗中：回合制一步一变，2s 会让战斗页明显滞后

    def __init__(self, tap, interval=0.12):
        self.tap = tap
        self.interval = interval
        self._buf = []
        self._subs = set()
        self._lock = threading.Lock()
        self._next_beat = 0.0
        # ★ 批推溢出计数（2026-10-20）：_on_frame 有 2000 条上限兜底，
        #   超了就删最老的。这同样意味着「浏览器收到的帧不是全部」——
        #   不计数的话，界面看着正常，实际漏了帧没人知道。
        #   与 tap._evicted 的区别：那是**环形缓冲**淘汰（长期历史），
        #   这是**批推暂存**淘汰（两次 SSE 推送之间的积压）。两者独立计。
        self._sse_dropped = 0
        # 业务事件总线的推送游标：只推「上次之后的新事件」，不重发历史
        self._bus_seq = 0
        tap.subscribe(self._on_frame)
        threading.Thread(target=self._loop, daemon=True,
                         name='debugger-sse').start()

    def _on_frame(self, rec):
        with self._lock:
            self._buf.append(rec)
            if len(self._buf) > 2000:             # 兜底：订阅者迟迟不来
                _over = len(self._buf) - 2000
                del self._buf[:_over]
                self._sse_dropped += _over

    def add(self, sock):
        with self._lock:
            self._subs.add(sock)

    def drop(self, sock):
        with self._lock:
            self._subs.discard(sock)

    def _loop(self):
        while True:
            time.sleep(self.interval)
            with self._lock:
                batch, self._buf = self._buf, []
                subs = list(self._subs)
            if not subs:
                continue
            payloads = []
            if batch:
                # 帧批里附带当前批推溢出计数：让 UI 能在**每次**推流时判断
                # 「这波是不是漏了帧」，而不是等 2s 一次的低频 stats 心跳。
                payloads.append(('frames', {'frames': batch,
                                            'sse_dropped': self._sse_dropped}, None))
            # 业务事件（2026-10-22）：与帧流同一条 SSE 推，但**独立游标**——
            #   事件是业务因果，帧是表现，两者的节奏不同（拒绝路径一帧不发但有事件）。
            try:
                _ev = bus.BUS.since(self._bus_seq)
            except Exception:                     # noqa: BLE001
                _ev = []
            if _ev:
                self._bus_seq = _ev[-1]['seq']
                payloads.append(('bus', {'events': _ev}, None))
            # 状态与统计低频跟随（复用同一条 SSE，省一个轮询接口）。
            # ★ 用显式节拍而不是「挂钟秒数的奇偶」：后者一旦循环被拖慢就会整拍漏推，
            #   而且没法按运行状态调速——而战斗中恰恰是最需要跟得紧的时候。
            now = time.monotonic()
            if now >= self._next_beat:
                in_war = bool((self.tap.world or {}).get('in_war'))
                self._next_beat = now + (self.BEAT_WAR if in_war else self.BEAT_IDLE)
                srv = _srv()
                if srv is not None:
                    try:
                        payloads.append(('state', srv.gm_state_dict(), None))
                        # 战斗调试快照（2026-10-21）：与 GM 面板 /api/battle 同源。
                        # ★ 谁不快谁不慢？跟 state 同一节拍；srv.gm_battle_dict 自取
                        #   _lock，与内核线程互斥，不会读到半更新。
                        payloads.append(('battle', srv.gm_battle_dict(), None))
                        # 不带全表：静表由界面开机拉一次，心跳只推计数
                        _st = _tap().stats(include_tables=False)
                        _st['sse_dropped'] = self._sse_dropped
                        payloads.append(('stats', _st, None))
                    except Exception:             # noqa: BLE001
                        pass
            if not payloads:
                continue
            for sock in subs:
                for name, data, _ in payloads:
                    if not self._write(sock, name, data):
                        self.drop(sock)
                        break

    @staticmethod
    def _write(sock, event, data):
        try:
            body = ('event: %s\ndata: %s\n\n'
                    % (event, json.dumps(data, ensure_ascii=False)))
            sock.write(body.encode('utf-8'))
            sock.flush()
            return True
        except (BrokenPipeError, ConnectionError, OSError, ValueError):
            return False


# ══════════════════════════════════════════════════════════ GM 动作 ══
# ── 真实链路归因 ──
# 这几种动作是「一条命令连发一整个帧序列」：调用前后各取一次 last_seq()，就把这条
# 链路**整段**圈出来了（gm_war_* 全程持内核 _lock，期间不会有别的线程插帧）。
# 标出来的意义是界面上能直接看到「开战/胜利到底发了哪些帧、什么顺序」——
# 帧序即契约，顺序错了客户端就是卡「请等待」/地图全空，光读代码看不出来。
CHAIN_ACTIONS = {
    'war_start': ('GM 开战', 'war_start'),
    'war_win':   ('GM 胜利', 'war_win'),
}


def _run_action(srv, name, body):
    """执行一个 GM 动作；属于真实链路的自动按 seq 区间标红并回填结果。

    返回 (结果值, 链路信息或 None)。结果值若本身是 dict（gm_war_* 返回的），
    直接透传给前端——否则「已在战斗中」这类失败会被吞成 {'ok': True}。
    """
    tap = _tap()
    chain = CHAIN_ACTIONS.get(name)
    if chain is None:
        return GM_ACTIONS[name](srv, body), None
    label, kind = chain
    lo = tap.last_seq() if tap is not None else 0
    res = GM_ACTIONS[name](srv, body)
    info = None
    if tap is not None:
        hi = tap.last_seq()
        # ★ 只有「真的发出帧了」才记段落。失败路径（已在战斗中/不在战斗中）一帧都不发，
        #   这时 hi == lo，若照样记一条，段落表里就多出一条 frames=0 的空段：
        #   界面上显示「GM 胜利 #86-86（0 帧）」，统计里 war_win 还 +1 ——
        #   看的人会以为自己点漏了，或者以为链路没发帧，而实际是**这条命令被拒了**。
        ok = bool(res.get('ok', True)) if isinstance(res, dict) else True
        if hi > lo:
            n = tap.mark_range(lo + 1, hi, label, kind=kind)
            info = {'label': label, 'kind': kind, 'lo': lo + 1, 'hi': hi,
                    'frames': n, 'marked': True}
        else:
            info = {'label': label, 'kind': kind, 'lo': lo, 'hi': hi,
                    'frames': 0, 'marked': False}
    return res, info


GM_ACTIONS = {
    'hp':       lambda srv, b: srv.gm_set_hp(b.get('value', 0)),
    'coins':    lambda srv, b: srv.gm_set_coins(b.get('value', 0)),
    'resource': lambda srv, b: srv.gm_set_res(b.get('name'), b.get('value', 0)),
    'pos':      lambda srv, b: srv.gm_set_pos(b.get('x', 0), b.get('y', 0)),
    'map':      lambda srv, b: srv.gm_goto_map(b.get('mapid', 0), b.get('x'), b.get('y')),
    'pet':      lambda srv, b: srv.gm_set_pet_hp(b.get('value', 0)),
    'item_add': lambda srv, b: srv.gm_add_item(b.get('shape', 0), b.get('qty', 1),
                                               b.get('name', '')),
    'item_del': lambda srv, b: srv.gm_del_item(b.get('iid')),
    'encounter': lambda srv, b: srv.gm_set_encounter(b.get('every', 0)),
    'heal':     lambda srv, b: srv.gm_heal(),
    'refresh':  lambda srv, b: srv.gm_refresh(),
    'save':     lambda srv, b: srv.gm_save(),
    # ★ 战斗两条 GM 动作（2026-10-20）：与 GM 面板 api/war/start|win 同源，
    #   走的是内核**真实链路**（_start_war / _after_hero_action），不是直接置位：
    #     war_start → WarStart 112 → WarAdd 115×N → WarSummon 129 → WarTurn 114
    #     war_win   → WarDel 116×N → WarEnd 113 → _resync_scene
    #                 → 经验 PropChange → 掉落 ItemAdd
    #   返回 dict（ok/error/…）而不是 None，调用方据此报错。
    'war_start': lambda srv, b: srv.gm_war_start(),
    'war_win':   lambda srv, b: srv.gm_war_win(),
    # ── 战斗调试动作（2026-10-21）：与 GM 面板 api/battle/* 同源 ──
    #   battle 是纯只读快照（不打帧、不当链路），参数与 gm_battle_dict 一致。
    'battle':      lambda srv, b: srv.gm_battle_dict(),
    'cp_set':      lambda srv, b: srv.gm_set_cp(b.get('atk'), b.get('def'), b.get('dis')),
    'escape_force': lambda srv, b: srv.gm_set_escape_force(b.get('mode')),
    'pet_level':   lambda srv, b: srv.gm_set_pet_level(b.get('level'), b.get('factor_idx')),
    'enemy_level': lambda srv, b: srv.gm_set_enemy_level(b.get('level')),
}


# 控制台指令 → 链路种类。`war end` 与 `war win` 是同一实现（gm_command 里
# 二者都走 gm_war_win 的真实胜利链），所以也归到 war_win。
_CONSOLE_CHAIN_KINDS = {'war start': 'war_start', 'war win': 'war_win'}


def _console_chain_kind(cmd):
    return _CONSOLE_CHAIN_KINDS.get((cmd or '').strip().lower())


def _console_chain_label(kind):
    return 'GM 开战' if kind == 'war_start' else 'GM 胜利'

# ══════════════════════════════════════════════════════════ HTTP ══
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    broadcaster = None

    # ── 工具 ──
    def _send(self, body, ctype='application/json; charset=utf-8', code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, *a):
        pass

    # ── GET ──
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ('/', '/index.html'):
            try:
                with io.open(HTML_PATH, 'rb') as f:
                    return self._send(f.read(), 'text/html; charset=utf-8')
            except OSError as e:
                return self._send('<h1>debugger.html 读取失败：%s</h1>' % e,
                                  'text/html; charset=utf-8', 500)
        if path == '/api/hello':
            return self._send(self._hello())
        if path == '/api/frames':
            since = int((qs.get('since') or ['0'])[0])
            tap = _tap()
            # 带上 marks：链路段落是**事后**回填的，已渲染过的旧行不会自己变红，
            # 前端拿这份区间 + 帧记录上的 warcmd 位就能重新着色。
            return self._send({'frames': tap.frames(since=since),
                               'last': tap.last_seq(),
                               'marks': tap.marks(),
                               'generation': tap.stats(include_tables=False)['generation']})
        if path == '/api/bus':
            # 业务事件（任务链 / 经济页签）：支持 since + cats 过滤 + limit
            since = int((qs.get('since') or ['0'])[0])
            cats = (qs.get('cats') or [''])[0]
            limit = int((qs.get('limit') or ['200'])[0])
            cats = [c for c in cats.split(',') if c] or None
            return self._send({'events': bus.BUS.since(since, cats=cats, limit=limit),
                               'stats': bus.BUS.stats(),
                               'ledger': bus.ledger(limit=200),
                               'totals': bus.ledger_totals(),
                               'labels': bus.CAT_LABEL})
        if path == '/api/marks':
            tap = _tap()
            return self._send({'marks': tap.marks(),
                               'kinds': tap.stats(include_tables=False)['mark_kinds']})
        if path == '/api/stats':
            return self._send(_tap().stats())
        if path == '/api/state':
            srv = _srv()
            return self._send(srv.gm_state_dict() if srv else {'error': 'no server'})
        if path == '/api/maps':
            return self._send({'maps': STATE['maps']})
        if path == '/api/table':
            d = (qs.get('dir') or ['C2S'])[0].upper()
            rows = _tap().proto.table(d)
            # ★ 给每行带上「特性状态」（2026-10-20）：覆盖页据此把
            #   ignored_no_counterpart 的 opcode 标灰——它们是单机无对手方、
            #   显式接收并忽略的，**不是缺口**，不该催人补。
            #   只在 C2S 侧有意义（忽略表是 C2S 的）。
            feat = {}
            if d == 'C2S':
                try:
                    import m4_server as _m4
                    feat = _m4.op_feature_map()
                except Exception:                  # noqa: BLE001
                    feat = {}
            for r in rows:
                f = feat.get(r.get('op'))
                if f:
                    r['feature'] = f
            return self._send({'dir': d, 'rows': rows,
                               'feature_legend': {
                                   'ignored_no_counterpart':
                                       '单机无对手方，显式接收并忽略（非缺口）'}})
        if path == '/api/export':
            return self._download()
        if path == '/api/stream':
            return self._stream()
        return self._send({'error': 'not found'}, code=404)

    def _hello(self):
        srv = _srv()
        tap = _tap()
        maps = STATE['maps']
        return {
            'proto_ok': tap.proto.ok,
            'proto_error': tap.proto.error,
            'tables': {'C2S': len(tap.proto.table('C2S')),
                       'S2C': len(tap.proto.table('S2C'))},
            'overlay': len(tap.proto.table('S2C', overlay_only=True)),
            'kernel': {'host': getattr(srv, 'host', '?'),
                       'port': getattr(srv, 'port', '?'),
                       'seed': '0x%08X' % getattr(srv, 'seed', 0)},
            'save': getattr(srv, 'savepath', ''),
            'log': getattr(srv, 'logpath', ''),
            'client_dir': STATE['client_dir'],
            'maps': [{'mapid': mp['mapid'], 'name': mp['name']} for mp in maps],
            'installed': tap.installed,
            'uptime': round(time.time() - STATE['started'], 1),
        }

    def _download(self):
        tap = _tap()
        path = os.path.join(HERE, 'debug_session_%s.jsonl'
                            % time.strftime('%Y%m%d_%H%M%S'))
        n = tap.export_jsonl(path)
        return self._send({'ok': True, 'path': path, 'frames': n,
                           'size': os.path.getsize(path)})

    def _stream(self):
        """SSE：一次连接持续推 frames / state / stats 三类事件。"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.close_connection = True              # SSE 独占连接，别交回 keep-alive
        bc = Handler.broadcaster
        if bc is None:
            return
        bc.add(self.wfile)
        try:
            while True:
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        finally:
            bc.drop(self.wfile)

    # ── POST ──
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        tap, srv = _tap(), _srv()
        if path == '/api/action':
            name = body.get('action')
            fn = GM_ACTIONS.get(name)
            if fn is None:
                return self._send({'ok': False, 'error': '未知动作 %r' % name}, 404)
            if srv is None:
                return self._send({'ok': False, 'error': '内核尚未就绪'}, 503)
            try:
                res, chain = _run_action(srv, name, body)
            except Exception as e:                # noqa: BLE001
                return self._send({'ok': False, 'error': repr(e)}, 400)
            out = {'ok': True, 'state': srv.gm_state_dict()}
            if isinstance(res, dict):
                out.update(res)                   # gm_war_* 自带 ok/error/enemies/killed
                if not res.get('ok', True):
                    out['action'] = name
                    return self._send(out, 400)
            if chain is not None:
                out['chain'] = chain
            return self._send(out)
        if path == '/api/inject':
            direction = (body.get('dir') or 'S2C').upper()
            try:
                op = int(body.get('op'))
                payload = bytes.fromhex((body.get('hex') or '').replace(' ', ''))
            except (TypeError, ValueError):
                return self._send({'ok': False, 'error': 'op 或 hex 不合法'}, 400)
            ok, msg = tap.inject(direction, op, payload)
            return self._send({'ok': ok, 'msg': msg}, 200 if ok else 400)
        if path == '/api/drop':
            direction = (body.get('dir') or 'S2C').upper()
            n = tap.drop_next(direction)
            return self._send({'ok': True, 'msg': '%s 待丢弃 %d 帧' % (direction, n)})
        if path == '/api/clear':
            tap.clear()
            return self._send({'ok': True})
        if path == '/api/gm/cmd':
            # 控制台命令行：走内核 gm_command(line) 真实路径解析器。
            # 与 GM 面板的 api/* 路由是同一套 gm_* 方法（真实链路，非伪指令）。
            line = (body.get('line') or '').strip()
            if srv is None:
                return self._send({'ok': False, 'error': '内核尚未就绪'}, 503)
            try:
                # 控制台里的 `war start` / `war win` 走的也是真实链路，同样要标红。
                # 不能只在 /api/action 上标：两条入口都能开战，漏标一条就等于
                # 「帧流里有一半链路没标」，而界面不会报错——最坏的那种失败。
                lo = tap.last_seq() if tap is not None else 0
                res = srv.gm_command(line)
            except Exception as e:                # noqa: BLE001
                return self._send({'ok': False, 'error': repr(e)}, 400)
            if tap is not None and isinstance(res, dict):
                _kind = _console_chain_kind(res.get('cmd'))
                if _kind is not None:
                    hi = tap.last_seq()
                    # 同 /api/action：被拒的命令一帧不发，别记空段落（hi > lo 才算数）
                    if hi > lo:
                        n = tap.mark_range(lo + 1, hi, _console_chain_label(_kind),
                                           kind=_kind)
                        res['chain'] = {'label': _console_chain_label(_kind),
                                        'kind': _kind, 'lo': lo + 1, 'hi': hi,
                                        'frames': n, 'marked': True}
                    else:
                        res['chain'] = {'label': _console_chain_label(_kind),
                                        'kind': _kind, 'lo': lo, 'hi': hi,
                                        'frames': 0, 'marked': False}
            return self._send(res)
        return self._send({'error': 'not found'}, code=404)


# ══════════════════════════════════════════════════════════ 入口 ══
def build_parser():
    ap = argparse.ArgumentParser(
        description='可视化调试台：协议帧流 / 地图 / 状态 / 覆盖')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT,
                    help='调试台 HTTP 端口（只绑 127.0.0.1，默认 %d）' % DEFAULT_PORT)
    ap.add_argument('--dump', default='',
                    help='退出时把会话导出到该 JSONL 路径（默认不导出）')
    ap.add_argument('--no-client', action='store_true',
                    help='只起内核 + 调试台，不拉客户端（脚本联调 / 自测）')
    ap.add_argument('--fresh', action='store_true', help='删档从零开始')
    ap.add_argument('--no-encounter', action='store_true', help='关闭遇怪')
    ap.add_argument('--encounter-every', type=int, default=5,
                    help='每移动几次遇怪（默认 5）')
    ap.add_argument('--enemies', type=int, default=m.ENEMY_COUNT_DEFAULT)
    ap.add_argument('--turn-timeout', type=int, default=m.TURN_TIMEOUT)
    ap.add_argument('--seed', default=None)
    ap.add_argument('--client', default=None, help='客户端目录（含 mx.exe）')
    ap.add_argument('--client-exe', default=None, help='直接指定 mx.exe 路径')
    ap.add_argument('--save-dir', default=None)
    ap.add_argument('--kernel-port', type=int, default=0,
                    help='内核监听端口；0 = 自动挑空闲端口（默认 0）')
    ap.add_argument('--web', action='store_true',
                    help='额外启动旧版 GM 面板（默认关：本调试台已含「状态/GM」页签）')
    ap.add_argument('--web-port', type=int, default=8081,
                    help='旧版 GM 面板端口（需配合 --web；默认 8081，'
                         '刻意避开 HTTP 劫持占用的 8080）')
    ap.add_argument('--http-port', type=int, default=8080,
                    help='本地 HTTP 劫持端口（客户端 update.ini 指向它）')
    ap.add_argument('--no-http', action='store_true',
                    help='不启动本地 HTTP 劫持（端口冲突或脚本联调时用）')
    ap.add_argument('--exit-with-client', action='store_true',
                    help='客户端退出即整体退出；默认**不退**——客户端关掉后调试台常驻，'
                         '抓包缓冲与统计都还在，正好用来复盘刚才那一局')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    import singleplayer

    def on_ready(srv, _kernel_port, client_dir):
        return attach(srv, port=args.port, dump=args.dump, client_dir=client_dir)

    try:
        rc = singleplayer.main(
            ['--no-client'] if args.no_client else [],
            host='127.0.0.1', port=args.kernel_port,
            encounter_every=args.encounter_every,
            no_encounter=args.no_encounter,
            enemies=args.enemies, turn_timeout=args.turn_timeout,
            seed=args.seed, client=args.client, client_exe=args.client_exe,
            save_dir=args.save_dir,
            web=args.web, web_port=args.web_port,
            http_port=args.http_port, no_http=args.no_http, fresh=args.fresh,
            on_ready=on_ready,
        )
        # 客户端退出即整体退出是「玩游戏」的语义；调试工具要的是「关掉客户端之后
        # 还能复盘刚才那一局」。所以默认驻留，Ctrl-C 才走（--dump 这时才导出）。
        tap = _tap()
        if not args.exit_with_client and tap is not None and tap.last_seq() > 0:
            print('[调试台] 客户端已结束（rc=%s），调试台继续驻留：' % rc)
            print('[调试台]   %s/ 仍有 %d 帧会话与完整统计；'
                  % (BASE_HINT % args.port, tap.last_seq()))
            print('[调试台]   Ctrl-C 退出%s。'
                  % ('，退出时自动导出会话' if args.dump else '（可加 --dump 令退出时导出）'))
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print('\n[调试台] 收到 Ctrl-C，收工。')
    finally:
        if args.dump and _tap() is not None:
            n = _tap().export_jsonl(args.dump)
            print('[调试台] 会话已导出 %s（%d 帧）' % (args.dump, n))
    return rc


if __name__ == '__main__':
    sys.exit(main())
