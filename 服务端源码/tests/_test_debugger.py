# -*- coding: utf-8 -*-
"""可视化调试台端到端回归 —— tests/_test_debugger.py

**真起进程、真连内核、真走 HTTP/SSE**：不 mock 任何一层，验证使用者实际会走的路径。
（数据层单测见 tests/_test_tap.py；本文件只测「工具作为产品跑起来对不对」。）

被验证的链条：
    debugger.py 子进程
      → CPU 内内核（自动端口）
      → HTTP API（hello / maps / stats / state / frames / table / export）
      → SSE 推送（frames / state / stats 三类事件）
      → 帧注入与丢帧（真实客户端 socket 收得到 / 收不到）
      → singleplayer.on_ready 挂载点确实在「客户端接入前」生效
"""
import atexit
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):                  # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
# ★ 原服加密通道（2026-10-21 修复接线缺失）：singleplayer 默认 --encrypt=True，
#   内核在 Hello 交付后走「C2S 加密 / S2C 先 LZSS 压缩再加密」的两向流
#   （与 _test_crypto_mode.py 同一套：发=crypt_tx，收=解密+解压长度前缀）。
#   脚本客户端不接这套，内核解析不出帧、AccountInfo 永远不来 ——
#   本测试此前因此超时（加密阶段遗留）。
import mx_crypto as mxc  # noqa: E402
import lzss  # noqa: E402

DPORT = 8097
BASE = 'http://127.0.0.1:%d' % DPORT

ok = True


def check(cond, msg):
    global ok
    print(('OK  ' if cond else 'FAIL') + ' ' + msg)
    ok = ok and bool(cond)
    return cond


def get(path, timeout=3):
    """GET 并解析 JSON；4xx/5xx 也要能拿到响应体（用于断言错误码本身）。"""
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:                                    # noqa: BLE001
            return {'error': 'HTTP %d' % e.code}


def post(path, payload, timeout=3):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:                                    # noqa: BLE001
            return {'ok': False, 'error': 'HTTP %d' % e.code}


# ── 客户端侧最小收发 ──
# ★ CIPHER 非 None = 已进原服加密通道（Hello 交付后置位）：
#     C2S：帧先 crypt_tx 再发出（不压缩，与 _test_crypto_mode 同口径）
#     S2C：「先 LZSS 压缩、长度前缀=压缩长、再 crypt_rx」→ 读到后解密+解压
CIPHER = None


def recv_s2c(c, timeout=3.0):
    c.settimeout(timeout)

    def exact(n):
        buf = b''
        while len(buf) < n:
            ch = c.recv(n - len(buf))
            if not ch:
                raise ConnectionError('closed')
            buf += ch
        return buf
    if CIPHER is None:
        n = exact(1)[0]
        if n == 0xFF:
            n = struct.unpack('<H', exact(2))[0]
        p = exact(n)
        return p[0], p[1:]
    n = CIPHER.crypt_rx(exact(1))[0]
    if n == 0xFF:
        n = struct.unpack('<H', CIPHER.crypt_rx(exact(2)))[0]
    p = lzss.decompress(CIPHER.crypt_rx(exact(n)))
    return p[0], p[1:]


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


def send_c2s(c, op, body=b''):
    if len(body) >= 0xFF:
        frame = bytes([op]) + b'\xFF' + struct.pack('<H', len(body)) + body
    else:
        frame = bytes([op, len(body)]) + body
    c.sendall(frame if CIPHER is None else CIPHER.crypt_tx(frame))


def drain(c, timeout=0.5):
    out = []
    while True:
        try:
            out.append(recv_s2c(c, timeout)[0])
        except Exception:                                    # noqa: BLE001
            return out


# ── SSE 订阅（后台线程收事件）──
class Sse:
    def __init__(self, path='/api/stream'):
        self.events = []
        self.raw = []
        self.err = None
        self.stop = False
        self._t = threading.Thread(target=self._run, args=(path,), daemon=True)
        self._t.start()

    def _run(self, path):
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                event = None
                for line in r:
                    if self.stop:
                        return
                    s = line.decode('utf-8', 'replace').rstrip('\r\n')
                    self.raw.append(s)
                    if s.startswith('event:'):
                        event = s[6:].strip()
                    elif s.startswith('data:'):
                        try:
                            self.events.append((event, json.loads(s[5:].strip())))
                        except ValueError:
                            self.events.append((event, None))
        except Exception as e:                               # noqa: BLE001
            if not self.stop:
                self.err = repr(e)

    def names(self):
        return [e[0] for e in self.events]

    def of(self, name):
        return [d for n, d in self.events if n == name]


print('=' * 70)
print(' 0. 前端静态校验（界面引用的元素/动作必须真的存在）')
print('=' * 70)
_html = io.open(os.path.join(SIM, 'debugger.html'), encoding='utf-8').read()
_py = io.open(os.path.join(SIM, 'debugger.py'), encoding='utf-8').read()
_ids = set(re.findall(r'id="([\w-]+)"', _html))
_refs = set(re.findall(r"""[$]\('#([\w-]+)'\)""", _html))
check(not (_refs - _ids),
      '界面 $(\'#id\') 引用的 %d 个元素都存在（缺 %r）' % (len(_refs), sorted(_refs - _ids)))
_tabs = set(re.findall(r'data-tab="([\w-]+)"', _html))
_panels = set(re.findall(r'<div class="tab[^"]*" id="tab-([\w-]+)"', _html))
check(_tabs == _panels,
      '每个页签都有对应面板（页签 %r / 面板 %r）'
      % (sorted(_tabs), sorted(_panels - _tabs) or sorted(_panels)))
_acts = set(re.findall(r'data-act="([\w-]+)"', _html))
_gm = set(re.findall(r"'(\w+)':\s*lambda", _py))
check(not (_acts - _gm),
      '界面 GM 按钮都有后端动作实现（缺 %r）' % sorted(_acts - _gm))
_apis = set(re.findall(r"""['"]/api/([\w]+)""", _html))
_routes = {r.split('/')[-1]
           for r in re.findall(r"""['"]/(api/[\w]+)""", _py)}
check(not (_apis - _routes),
      '界面调用的 API 后端都有路由（前端 %r / 后端 %r / 缺 %r）'
      % (sorted(_apis), sorted(_routes), sorted(_apis - _routes)))
check('war' in _panels and 'function renderWar' in _html,
      '战斗面板与渲染函数齐备')

print()
print('=' * 70)
print(' A. 启动调试台子进程（等价于使用者双击启动脚本）')
print('=' * 70)

# ★ 先确认端口干净。若已被占用，多半是上次崩溃残留的调试台——此时 /api/hello 会
#   命中那个旧进程，内核端口却是失效的，报错会变成莫名其妙的 ConnectionRefused。
#   宁可在这儿直说，也不要连到别人身上测出张冠李戴的结果。
try:
    _p = socket.create_connection(('127.0.0.1', DPORT), timeout=0.5)
    _p.close()
    check(False, '端口 %d 已被占用：疑似上次崩溃残留的调试台，请先结束它再跑' % DPORT)
    print('\n=== 调试台端到端 FAIL ===')
    sys.exit(1)
except OSError:
    pass

tmp = tempfile.mkdtemp(prefix='m4dbg_')
py = sys.executable
cmd = [py, os.path.join(SIM, 'debugger.py'),
       '--no-client', '--no-http', '--port', str(DPORT), '--kernel-port', '0',
       '--save-dir', os.path.join(tmp, 'save'), '--fresh']
proc = subprocess.Popen(cmd, cwd=SIM, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True,
                        encoding='utf-8', errors='replace')
# 中途任何异常都要收掉子进程与临时目录：否则进程一直占着 8097，把下一次运行也带偏
atexit.register(lambda: proc.poll() is None and proc.kill())
atexit.register(lambda: shutil.rmtree(tmp, ignore_errors=True))
boot = []
threading.Thread(target=lambda: [boot.append(ln) for ln in proc.stdout],
                 daemon=True).start()

hello = None
t0 = time.time()
while time.time() - t0 < 25:
    if proc.poll() is not None:
        break
    try:
        hello = get('/api/hello')
        break
    except Exception:                                        # noqa: BLE001
        time.sleep(0.4)
check(hello is not None,
      '调试台在 25s 内就绪（子进程 rc=%r）' % proc.poll())
if hello is None:
    print('\n'.join(boot[-25:]))
    proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)
    print('\n=== 调试台端到端 FAIL ===')
    sys.exit(1)

kport = hello['kernel']['port']
check(isinstance(kport, int) and kport > 0, '内核已自动选中空闲端口 %r' % kport)
check(hello['proto_ok'] and hello['tables']['C2S'] >= 120
      and hello['tables']['S2C'] >= 160 and hello['overlay'] >= 35,
      '协议字典就绪：C2S %d / S2C %d / 精确布局 %d'
      % (hello['tables']['C2S'], hello['tables']['S2C'], hello['overlay']))
check(hello['installed'] is True,
      'on_ready 挂载点在「客户端接入前」已生效（install=True）')


print()
print('=' * 70)
print(' B. 只读 API')
print('=' * 70)
maps = get('/api/maps')['maps']
# 2026-10-21：MAPS 已由 world_config.json 全量驱动（60 张，出生点仍永泽城）。
# 原断言写死 len==5，被配置扩展冲掉 —— 改成「首图正确 + 规模合理」，
# 并顺带校验 mapid 唯一。
check(maps and maps[0]['name'] == '永泽城' and len(maps) >= 5
      and len({m['mapid'] for m in maps}) == len(maps),
      '地图定义：%d 张（world_config 驱动），首图=%s'
      % (len(maps), maps[0]['name'] if maps else None))
st0 = get('/api/state')
check('hero_hp' in st0 and st0['connected'] is False, 'GM 状态可读（未连接时 connected=False）')
cov = get('/api/stats')
check(cov['frames'] == 0 and cov['seen'] == {'C2S': 0, 'S2C': 0}, '开局覆盖统计为空')
check(cov['total_ops']['C2S'] >= 120 and cov['total_ops']['S2C'] >= 160,
      '覆盖表分母与字典一致：%r' % cov['total_ops'])
tbl = get('/api/table?dir=C2S')['rows']
check(len(tbl) >= 120 and any(r['op'] == 54 for r in tbl), 'C2S 全表可拉取（%d 条）' % len(tbl))
check(get('/api/table?dir=S2C')['dir'] == 'S2C', 'S2C 全表可拉取')
check(isinstance(get('/api/frames')['frames'], list), '增量取帧接口可用')


print()
print('=' * 70)
print(' C. SSE 推送（真连一条流，验证三类事件）')
print('=' * 70)
sse = Sse()
time.sleep(1.2)
check(sse.err is None, 'SSE 连接建立（err=%r）' % sse.err)
check('stats' in sse.names() or 'state' in sse.names(),
      'SSE 主动推送 state/stats：%r' % sorted(set(sse.names())))


print()
print('=' * 70)
print(' D. 真实客户端接入 → 帧被抓到并推送')
print('=' * 70)
c = socket.create_connection(('127.0.0.1', kport), timeout=5)
op, data = recv_s2c(c)
seed = struct.unpack('<I', data[:4])[0]
check(op == 1, '客户端先收到 Hello(op1)')
c.sendall(hello_resp(seed))
# ★ Hello 交付后切到加密通道（NakedSend 仅限 Hello 三字节回应）：
#   客户端镜像内核 CryptoConn 的方向映射（内核 send=crypt_rx / recv=crypt_tx，
#   客户端两个同序），此后 send_c2s/recv_s2c/drain 照旧传 `c`。
_c_raw = c
# ★ Hello 交付后切到原服加密通道：RealCipher(seed)（seed 为 Hello 明文里的
#   原始种子，而非 25B 回应里的混淆值）。此后 send_c2s/recv_s2c 走 CIPHER 分支。
CIPHER = mxc.RealCipher(seed)
check(True, '已切到加密通道（seed=0x%08X）' % seed)
time.sleep(0.15)
send_c2s(c, 1, login_body())
time.sleep(0.3)
op, data = recv_s2c(c)
check(op == 2, '登录后收到 AccountInfo(2)')
if data[0] == 0:
    # 全新存档没有角色（--fresh）：先建角，走「PlayerAdd + AccountInfo(1) + RoleInfo」
    check(True, '存档为空 → 走建角分支（AccountInfo count=0）')
    send_c2s(c, 3, '调试阿飞'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    ops = [recv_s2c(c)[0] for _ in range(3)]
    check(ops == [6, 2, 3], '建角后收到 PlayerAdd(6)+AccountInfo(2)+RoleInfo(3)：%r' % ops)
else:
    ops = [recv_s2c(c)[0] for _ in range(2)]
    check(ops == [2, 3], '已有角色 → AccountInfo(1)+RoleInfo(3)：%r' % ops)

send_c2s(c, 2, struct.pack('<I', 1))          # 选角进世界
time.sleep(1.2)
drain(c, 0.6)

got = get('/api/frames')
frames = got['frames']
check(len(frames) > 10, 'HTTP 拉到 %d 帧（last=%d）' % (len(frames), got['last']))
s2c_ops = [f['op'] for f in frames if f['dir'] == 'S2C']
c2s_ops = [f['op'] for f in frames if f['dir'] == 'C2S']
check(1 in c2s_ops and 2 in c2s_ops, '上行帧被记录：%r' % c2s_ops)
check(16 in s2c_ops and 96 in s2c_ops and 97 in s2c_ops,
      '下行帧被记录（含 PropInit/SceneM/GotoM）：%d 帧' % len(s2c_ops))
scene = [f for f in frames if f['dir'] == 'S2C' and f['op'] == 96][0]
check(scene['src'] == 'overlay' and scene['fit'] and
      any(r['name'] == 'mapname' or r['value'] == '永泽城' for r in scene['rows']),
      'SceneM 用精确布局解码且吃满（%r）' % [(r['name'], r['value']) for r in scene['rows']][:3])

# 增量语义：since=last 应为空，since=0 应给全量
check(get('/api/frames?since=%d' % got['last'])['frames'] == [], 'since=last 增量取帧为空')

time.sleep(1.5)
names = set(sse.names())
check('frames' in names, 'SSE 推送了 frames 事件')
check('battle' in names, 'SSE 心跳附带 battle 调试快照（GM 战斗调试工具）')
# 'frames' 事件载荷 = {'frames': [...], 'sse_dropped': N}（2026-10-20 起的形状）：
# 展平要取 payload['frames']，直接把 payload 当列表迭代会拿到 dict 的键名。
sse_frames = [f for p in sse.of('frames') for f in (p.get('frames') or [])]
check(len(sse_frames) > 10, 'SSE 推送到 %d 帧' % len(sse_frames))
check(any(f['dir'] == 'S2C' and f['op'] == 96 for f in sse_frames),
      'SSE 推的帧里含 SceneM（事件与拉取两条路径内容一致）')

cov = get('/api/stats')
check(cov['seen']['C2S'] >= 2 and cov['seen']['S2C'] >= 8,
      '覆盖统计随会话增长：seen=%r' % cov['seen'])
check(cov['world']['mapid'] == 1001 and cov['world']['mapname'] == '永泽城',
      '世界模型：%r(%r)' % (cov['world']['mapname'], cov['world']['mapid']))
check(cov['world']['x'] == 45 and cov['world']['y'] == 40,
      '世界模型坐标 (%r,%r)' % (cov['world']['x'], cov['world']['y']))
check(len(cov['world']['npcs']) >= 2,
      '世界模型 NPC %d 个：%r' % (len(cov['world']['npcs']),
                                 [n['name'] for n in cov['world']['npcs']]))

st = get('/api/state')
check(st['connected'] is True and st['px'] == 45 and st['py'] == 40,
      'GM 状态反映真实会话：connected=%r pos=(%r,%r)'
      % (st['connected'], st['px'], st['py']))


print()
print('=' * 70)
print(' E. GM 动作（进程内直调）')
print('=' * 70)
r = post('/api/action', {'action': 'hp', 'value': 123})
check(r.get('ok') and r['state']['hero_hp'] == 123, '设 HP → %r' % r.get('state', {}).get('hero_hp'))
drain(c, 0.3)
r = post('/api/action', {'action': 'pos', 'x': 50, 'y': 41})
check(r.get('ok') and (r['state']['px'], r['state']['py']) == (50, 41),
      '传送 → (%r,%r)' % (r['state']['px'], r['state']['py']))
drain(c, 0.3)
r = post('/api/action', {'action': 'coins', 'value': 7777})
check(r.get('ok') and r['state']['coins'] == 7777, '设金钱 → %r' % r['state']['coins'])
drain(c, 0.3)
r = post('/api/action', {'action': 'item_add', 'shape': 10411, 'qty': 2, 'name': '调试道具'})
check(r.get('ok') and any(i['name'] == '调试道具' for i in r['state']['items']),
      '发道具 → 背包 %d 件' % len(r['state']['items']))
drain(c, 0.3)
iid = [i['iid'] for i in r['state']['items'] if i['name'] == '调试道具'][0]
r = post('/api/action', {'action': 'item_del', 'iid': iid})
check(r.get('ok') and not any(i['iid'] == iid for i in r['state']['items']),
      '删道具 iid=%d → 背包 %d 件' % (iid, len(r['state']['items'])))
drain(c, 0.3)
r = post('/api/action', {'action': 'heal'})
check(r.get('ok') and r['state']['hero_hp'] == r['state']['hero_hp_max'],
      '满血 → %r/%r' % (r['state']['hero_hp'], r['state']['hero_hp_max']))
drain(c, 0.3)
r = post('/api/action', {'action': 'nonsense'})
check(not r.get('ok'), '未知动作被拒绝（%r）' % r.get('error'))


print()
print('=' * 70)
print(' F. 帧注入 / 丢帧（HTTP → 进程内 → 真实客户端）')
print('=' * 70)
before = get('/api/stats')['frames']
r = post('/api/inject', {'dir': 'C2S', 'op': 8, 'hex': ''})     # 伪造一次 PING
check(r.get('ok'), 'HTTP 注入上行 C2S：%s' % r.get('msg'))
got = drain(c, 3.0)
check(got == [250], '内核把注入帧当客户端指令读走并应答 250 → %r' % got)

# S2CNpcChatExt：ranKey(2) npctype(4) name(20 定长) msg(变长 GBK+\0)
chat = (struct.pack('<HI', 0, 0) + 'GM'.encode('gbk').ljust(20, b'\x00')
        + '注入测试'.encode('gbk') + b'\x00')
r = post('/api/inject', {'dir': 'S2C', 'op': 152, 'hex': chat.hex(' ')})
check(r.get('ok'), 'HTTP 注入下行 S2C：%s' % r.get('msg'))
op, data = recv_s2c(c, 3.0)
check(op == 152 and '注入测试'.encode('gbk') in data, '客户端收到注入的 S2C op=%d' % op)
time.sleep(0.5)
fr = get('/api/frames')['frames']
inj = [f for f in fr if f['injected']]
check(len(inj) >= 2 and {f['dir'] for f in inj} == {'C2S', 'S2C'},
      '注入帧在帧流里被标成 injected（%d 条）' % len(inj))

r = post('/api/drop', {'dir': 'S2C'})
check(r.get('ok'), '布下丢帧：%s' % r.get('msg'))
send_c2s(c, 8)
got = drain(c, 1.2)
check(got == [], '被丢的 S2C 帧客户端确实收不到 → %r' % got)
send_c2s(c, 8)
check(drain(c, 3.0) == [250], '丢帧只影响一帧，链路自动恢复')
after = get('/api/stats')
check(after['dropped']['S2C'] == 1 and after['armed'] == {'C2S': 0, 'S2C': 0},
      '丢帧统计正确：dropped=%r armed=%r' % (after['dropped'], after['armed']))
check(after['frames'] > before, '帧计数在注入/丢帧后仍在增长（%d → %d）'
      % (before, after['frames']))


print()
print('=' * 70)
print(' G. 导出 / 清空')
print('=' * 70)
r = get('/api/export')
check(r.get('ok') and r['frames'] > 10 and r['size'] > 0 and os.path.isfile(r['path']),
      '导出会话：%d 帧 / %d B → %s' % (r['frames'], r['size'],
                                       os.path.basename(r['path'])))
with open(r['path'], encoding='utf-8') as f:
    first = json.loads(f.readline())
check({'seq', 'dir', 'op', 'name', 'rows', 'hex', 'fit'} <= set(first),
      '导出记录可被逐行解析且字段完整')
os.remove(r['path'])

r = post('/api/clear', {})
check(r.get('ok') and get('/api/stats')['frames'] == 0 and
      get('/api/frames')['frames'] == [],
      '清空会话缓冲（不影响运行中的内核）')
check(get('/api/stats')['world']['mapid'] is None, '清空同时重置世界模型')
send_c2s(c, 8)
check(drain(c, 3.0) == [250], '清空后链路照常工作（内核未被波及）')

check(post('/api/inject', {'dir': 'C2S', 'op': 'x', 'hex': ''}).get('ok') is False,
      '非法 opcode 被拒绝（400 而非 500）')
check(get('/api/nope') == {'error': 'not found'}, '未知路径返回 404 结构')


print()
print('=' * 70)
print(' H. 退出')
print('=' * 70)
c.close()
sse.stop = True
proc.terminate()
try:
    rc = proc.wait(timeout=10)
except subprocess.TimeoutExpired:                            # noqa: BLE001
    proc.kill()
    rc = proc.wait(timeout=5)
check(proc.poll() is not None, '子进程已退出（rc=%r）' % rc)
log = '\n'.join(boot)
check('[调试台]' in log and 'http://127.0.0.1:%d' % DPORT in log,
      '启动横幅打印了调试台地址')

shutil.rmtree(tmp, ignore_errors=True)
print('OK   已清理测试临时目录')
print('\n=== 调试台端到端 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
