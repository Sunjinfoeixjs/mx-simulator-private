"""多地图/传送冒烟：SceneM(96) 帧格式 + 传送使切图 + 出口自动传送 + 落脚点豁免 + 持久化。

M7 时期只有 5 张图；现在 MAPS 由 mapdata_resolve.py 批量联立 minimap_data × findpathdata
得到 60 张（56 张自出生图连通，初云区域 4 张真孤岛）。本测试因此分两层：
  ① 全图数据自洽（规模/单射/双向/出口可解析/连通性）；
  ② 具体交互（SceneM 帧、传送使切图、踩出口、豁免、持久化）；
  ③ 逐图巡检（60 张逐张核对 SceneM/整包/每个出口/切图落点/spawn 可溯源）。

沿用 _test_shop.py 方式：直接调 M4Server 方法/帧构造器，FakeConn 捕获下发字节，不依赖客户端。
"""
import os, shutil, struct, sys, tempfile
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m

# 逐图巡检要拿 minimap_data 的真实出口标记做「spawn 可溯源」对照；
# 客户端源码不在本机时优雅跳过这一项（其余巡检不依赖它）。
try:
    import mapdata_resolve as R
    _MARKERS = {mid: set(pts) for mid, pts in R.load_data('minimap_data.py').items()}
    _ANCHORS = set(R.ANCHORS)
except Exception as _e:                                          # noqa: BLE001
    _MARKERS, _ANCHORS = {}, set()
    print('OK   （跳过 spawn 可溯源）mapdata_resolve/minimap_data 不可用：%r' % _e)

# 随身 NPC（客户端自动寻路的 NPC 路线）：期望表**现算**，不写字面量
try:
    _NPCPOS = R.npcpos_index()
    _TRAVEL, _TRAVEL_NAMES = R.travel_dests(m.MAPS, m.NAME2MAP,
                                            R.load_data('pathdata.py'), _NPCPOS)
except Exception as _e2:                                          # noqa: BLE001
    _TRAVEL, _TRAVEL_NAMES, _NPCPOS = {}, (), {}
    print('OK   （跳过随身 NPC 巡检）pathdata/npcposdata 不可用：%r' % _e2)

ok = True
def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c

class FakeConn:
    def __init__(self): self.sent = []
    def sendall(self, b): self.sent.append(b)

def ops(c): return [f[1] for f in c.sent]

def parse_scene(frame):
    """S2CMapSceneM：scene(2) mapid(2) minimapid(2) x(2) y(2) + 场景名(变长\0结尾)。"""
    body = frame[2:]
    scene, mapid, mini, x, y = struct.unpack('<HHHHH', body[:10])
    name = body[10:].rstrip(b'\x00').decode('gbk')
    return scene, mapid, mini, x, y, name

# ── 1) 数据自洽：邻接目标均在 MAPS 中，且每图都有传送使 ──────────────
for src, dests in m.MAP_LINKS.items():
    check(src in m.MAPS and all(d in m.MAPS for d in dests),
          'MAP_LINKS %d 目标均为已定义地图 %s' % (src, dests))
for mid, d in m.MAPS.items():
    ids = [n[0] for n in d['npcs']]
    check(m.TELEPORT_NPC_ID in ids, '%s(%d) 摆有传送使' % (d['name'], mid))
    check(len(ids) == len(set(ids)), '%s(%d) NPC id 无重复' % (d['name'], mid))
check(set(m.NAME2MAP.values()) <= set(m.MAPS), 'NAME2MAP 目标均为已定义地图')

# ── 1b) 全图规模与连通性（口径 = mapdata_resolve.py --emit）─────────────
check(len(m.MAPS) == 60, '全图 60 张（实得 %d）' % len(m.MAPS))
check(len(m.NAME2MAP) == len(m.MAPS) and set(m.NAME2MAP.values()) == set(m.MAPS),
      'NAME2MAP 与 MAPS 一一对应（单射且满射）')
asym = [(a, b) for a, bs in m.MAP_LINKS.items() for b in bs if a not in m.MAP_LINKS.get(b, [])]
check(not asym, 'MAP_LINKS 双向（findpathdata 本身对称；非对称 %r）' % asym)
bad_ex = [(mid, t) for mid, ex in m.MAP_EXITS.items() for (t, _x, _y) in ex
          if t not in m.NAME2MAP or m.NAME2MAP[t] == mid]
check(not bad_ex, '出口目标均为他图（坏 %r）' % bad_ex)
_seen, _q = {m.START_MAP_ID}, [m.START_MAP_ID]
while _q:
    _x = _q.pop()
    for _t in m.MAP_LINKS.get(_x, []):
        if _t not in _seen:
            _seen.add(_t)
            _q.append(_t)
# ★ 2026-10-22：初云区域（1158/1159/1176/1177）已通过「1054 ↔ 1159」这座桥接入主图，
#   不再是孤岛；因此这里改成「全图可达、无孤岛」。
check(sorted(set(m.MAPS) - _seen) == [], '无孤岛（实得 %r）' % sorted(set(m.MAPS) - _seen))
check(len(_seen) == len(m.MAPS), '自出生图可达 %d / 共 %d' % (len(_seen), len(m.MAPS)))

# ── 2) 帧格式 ─────────────────────────────────────────────────────────
sc, mapid, mini, x, y, name = parse_scene(m.build_scene_m(1006))
check(sc == m.SCENE_ID and mapid == 1006 and mini == 1006,
      'SceneM(1006) scene/minimap 正确 (got %d/%d/%d)' % (sc, mapid, mini))
check((x, y) == m.MAPS[1006]['spawn'] and name == '新月平原',
      'SceneM(1006) 坐标/场景名 (got %s @%d,%d)' % (name, x, y))
check(m.build_scene_m(1006)[1] == m.OP_MAP_SCENE_M == 96, 'SceneM opcode=96')

# ── 3) 传送使切图 ────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix='m4map_')
s = m.M4Server('127.0.0.1', 3169, 0x1234, 5,
               logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
s._loaded = None
s.mapid, s.px, s.py = m.START_MAP_ID, m.SPAWN_X, m.SPAWN_Y
s.cur_npc = m.TELEPORT_NPC_ID

conn = FakeConn()
s._send_scene(conn, s.px, s.py)
# 永泽城 NPC：村长(1000) + 传送使(1001) + 战斗技能师(1002，M9 新增)
#        + 随身 NPC（pathdata 路线名，如「车夫」id=1200；见 mapdata_resolve.travel_dests）
n_npc = len(s._visible_npcs())          # 可见集（quest-spawn 未到步骤不下发）
check(ops(conn)[0] == m.OP_MAP_SCENE_M and ops(conn).count(m.OP_MAP_ADD_NPC) == n_npc
      and ops(conn)[-1] == m.OP_MAP_GOTO_M,
      '进图整包 = SceneM + AddNpc×%d + GotoM (got %s)' % (n_npc, ops(conn)))

msg = s._teleport_msg()
check('新月平原' in msg, '1001 传送页含邻接「新月平原」')

# 目的地顺序以 MAP_LINKS 为准（源自 findpathdata 真实连通；全图落表后 1001 首项是明月镇）
d1 = m.MAP_LINKS[m.START_MAP_ID][0]
conn2 = FakeConn()
s._teleport_choice(conn2, 1)          # 选第 1 个目的地
check(s.mapid == d1, '1001 选第 1 目的地 → %d（实得 %d）' % (d1, s.mapid))
sc, got_map, _mini, gx, gy, gname = parse_scene(conn2.sent[0])
check(got_map == d1 and gname == m.MAPS[d1]['name'] and (gx, gy) == m.MAPS[d1]['spawn'],
      '切图 SceneM 指向 %s(%d) @%d,%d' % (gname, got_map, gx, gy))
check(ops(conn2).count(m.OP_MAP_ADD_NPC) == len(s._visible_npcs()),
      '切图重发该图 NPC×%d（ClearAllObject 后必须补）' % len(s._visible_npcs()))
check((s.px, s.py) == m.MAPS[d1]['spawn'], '坐标落在目标图 spawn')

d2 = m.MAP_LINKS[d1][0]
conn3 = FakeConn()
s._teleport_choice(conn3, 1)          # d1 的第 1 个邻接
check(s.mapid == d2, '再传一次 → %d（实得 %d）' % (d2, s.mapid))

# 越界选项 → 不切图
s.mapid = 1002
conn4 = FakeConn()
s._teleport_choice(conn4, 99)
check(s.mapid == 1002 and all(f[1] != m.OP_MAP_SCENE_M for f in conn4.sent),
      '越界选项不切图')

# ── 4) 出口自动传送（坐标取自 minimap_data 真实出口标记）─────────────
cases = [(1006, 95, 5, 1003), (1006, 44, 139, 1001), (1003, 147, 4, 1004),
         (1004, 152, 29, 1002), (1002, 5, 141, 1004)]
for mid, ex, ey, expect in cases:
    s.mapid, s.px, s.py = mid, ex, ey
    check(s._check_exit() == expect, '%d @(%d,%d) 出口 → %d' % (mid, ex, ey, expect))

s.mapid, s.px, s.py = 1001, 100, 100
check(s._check_exit() is None, '非出口坐标不触发传送')
s.mapid, s.px, s.py = 1001, 342, 19
check(s._check_exit() == 1006, '1001 出口(342,19) → 1006')

# ── 4b) 落脚点豁免：spawn 恰在出口标记上的图，落地后不能一迈步就被弹走 ──
s.mapid = m.START_MAP_ID
conn6 = FakeConn()
s._goto_map(conn6, 1035)              # 落雪冰原 spawn=(230,6) 正是它唯一的出口标记
check((s.px, s.py) == m.MAPS[1035]['spawn'],
      '落地在 1035 spawn（实得 %d,%d）' % (s.px, s.py))
check(s._check_exit() is None, '落在出口标记上时不触发传送（落脚点豁免）')
s.px = m.MAPS[1035]['spawn'][0] + m.EXIT_RADIUS + 1
check(s._check_exit() is None, '走出半径这一步不触发（同时解除豁免）')
s.px = m.MAPS[1035]['spawn'][0] + 1
_got = s._check_exit()
check(_got == m.NAME2MAP['修罗森林'], '离开后再走回出口 → 正常触发（实得 %r）' % _got)

# 未知地图：拒绝且不下发
s.mapid = 1001
conn5 = FakeConn()
s._goto_map(conn5, 9999)
check(s.mapid == 1001 and not conn5.sent, '未知地图被拒绝且不发帧')

# ── 5) 持久化 ────────────────────────────────────────────────────────
s.mapid = 1003
s.px, s.py = m.MAPS[1003]['spawn']
s._persist()
sv = m.load_role(s.savepath)
check(sv.get('mapid') == 1003, '存档含当前地图 (got %s)' % sv.get('mapid'))
s2 = m.M4Server('127.0.0.1', 3170, 0x1234, 5,
                logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
check(s2.mapid == 1003 and (s2.px, s2.py) == m.MAPS[1003]['spawn'],
      '重载服务端恢复地图与坐标')

# ── 6) 逐图巡检：60 张逐张核对（上面 2~4 节只抽查了原有那 5 张）──────────
# 巡检要产生 60 图 × 数百次切图的日志，会淹没断言输出 —— 巡检期间静音。
s.log = lambda *a, **k: None


def parse_addnpc(frame):
    """S2CMapAddNpc(99)：id(4) shape(2) x(2) y(2) … ——前 10 字节即可核对三要素。"""
    return struct.unpack('<IHHH', frame[2:12])


for mid in sorted(m.MAPS):
    d = m.MAPS[mid]
    bad = []
    # 帧：SceneM 反解必须与 MAPS/mapid 逐字段一致
    sc, gid, gmini, gx, gy, gname = parse_scene(m.build_scene_m(mid))
    if (sc, gid, gmini, gx, gy, gname) != (m.SCENE_ID, mid, d['minimap'],
                                           d['spawn'][0], d['spawn'][1], d['name']):
        bad.append('SceneM=%r' % ((sc, gid, gmini, gx, gy, gname),))
    # 整包：SceneM + AddNpc×N + GotoM；NPC 帧可解、域内、id 唯一、含传送使
    s.mapid = mid
    cn = FakeConn()
    s._send_scene(cn, d['spawn'][0], d['spawn'][1])
    o = ops(cn)
    npcs = [f for f in cn.sent if f[1] == m.OP_MAP_ADD_NPC]
    if not (o and o[0] == m.OP_MAP_SCENE_M and o[-1] == m.OP_MAP_GOTO_M
            and len(npcs) == len(s._visible_npcs())):
        bad.append('整包 ops=%r' % o)
    # ★ 场景帧只发**可见** NPC：quest_data 里标 quest-spawn 的 NPC 未到对应步骤不出现，
    #   故这里比较 _visible_npcs() 而不是 MAPS[mid]['npcs'] 全量（见 _test_quest_chain ⑥）。
    ids = []
    for fr in npcs:
        nid, shape, nx, ny = parse_addnpc(fr)
        if not (shape <= 0xFFFF and nx <= 0xFFFF and ny <= 0xFFFF):
            bad.append('NPC %d 超 2 字节域' % nid)
        ids.append(nid)
    if m.TELEPORT_NPC_ID not in ids:
        bad.append('缺传送使')
    if len(ids) != len(set(ids)):
        bad.append('NPC id 重复')
    # 出口：每个标记必须真的触发，且指向 NAME2MAP 里的目标
    for (t, ex, ey) in m.MAP_EXITS.get(mid, []):
        s.mapid, s.px, s.py = mid, ex, ey
        s._arrive_xy = None                 # 排除落脚点豁免的干扰
        if s._check_exit() != m.NAME2MAP[t]:
            bad.append('出口(%s@%d,%d)不触发' % (t, ex, ey))
    # 传送使：每个邻接都列出，且逐个选项切图后落点 = 目标图 spawn
    s.mapid = mid
    msg = s._teleport_msg()
    for i, nb in enumerate(m.MAP_LINKS.get(mid, [])):
        if ('前往%s。' % m.MAPS[nb]['name']) not in msg:
            bad.append('传送页缺邻接 %d' % nb)
        # ★ 每次切图都会改 self.mapid，必须重置，否则下一跳会拿错图的邻接
        s.mapid = mid
        cn = FakeConn()
        s._teleport_choice(cn, i + 1)
        if s.mapid != nb or (s.px, s.py) != m.MAPS[nb]['spawn']:
            bad.append('切图 %d 落点=%d@%d,%d' % (nb, s.mapid, s.px, s.py))
    # 随身 NPC（客户端寻路）：pathdata 点名的路线 NPC 必须在图上，且点它→目的地菜单→切图落点正确
    for nid in sorted(_TRAVEL.get(mid, {})):
        nm, dests = _TRAVEL[mid][nid]
        ent = [t for t in d['npcs'] if t[0] == nid]
        if not ent or ent[0][4] != nm:
            bad.append('缺随身 NPC %d(%s)' % (nid, nm))
            continue
        if any(nid == t[0] for t in d['npcs'] if t[4] != nm):
            bad.append('随身 NPC id %d 重复' % nid)
        if not set(dests) <= set(m.MAP_LINKS.get(mid, [])):
            bad.append('随身 NPC %s 目的地 %r ⊄ MAP_LINKS' % (nm, dests))
        want = _NPCPOS.get(mid, {}).get(nm)
        if want is not None and tuple(ent[0][2:4]) != tuple(want):
            bad.append('随身 NPC %s 位置 %r != npcposdata %r'
                       % (nm, tuple(ent[0][2:4]), tuple(want)))
        # op96 → 对话框名 = NPC 真名、含每个目的地；op98 → 逐选项切图落点 = 目标图 spawn
        s.mapid, s.cur_npc, s.cur_npc_menu = mid, None, None
        cn2 = FakeConn()
        s._dispatch(cn2, m.C2S_NPC_LOOK, struct.pack('<I', nid))
        chat = cn2.sent[-1] if cn2.sent else b''
        if nm.encode('gbk') not in chat:
            bad.append('随身 NPC %s 对话框名缺失' % nm)
        for i, dd in enumerate(dests):
            if ('前往%s。' % m.MAPS[dd]['name']).encode('gbk') not in chat:
                bad.append('随身 NPC %s 菜单缺 %d' % (nm, dd))
            s.mapid, s.px, s.py = mid, m.MAPS[mid]['spawn'][0], m.MAPS[mid]['spawn'][1]
            s._arrive_xy = None
            s._dispatch(cn2, m.C2S_NPC_ANSWER, struct.pack('<I', i + 1))
            if s.mapid != dd or (s.px, s.py) != m.MAPS[dd]['spawn']:
                bad.append('随身 NPC %s 选项%d 落点=%d@%d,%d'
                           % (nm, i + 1, s.mapid, s.px, s.py))
    # spawn 可溯源：非锚点图的 spawn 必须是该图 minimap 的真实出口标记
    if _MARKERS and mid not in _ANCHORS and tuple(d['spawn']) not in _MARKERS.get(mid, set()):
        bad.append('spawn %r 不在该图出口标记里' % (tuple(d['spawn']),))
    # 入边（除出生图外无死胡同）+ 名称 GBK ≤255 字节（变长字符串 1B 前缀不溢出）
    # ★ 2026-10-22：客户端缺 `map/<id>.map` 的图（1073-1076）不接传送图，允许无入边
    #   （它们保留 MAPS 定义只为出口名可解析，不参与可达性）。
    if (mid != m.START_MAP_ID and m.map_file_exists(mid)
            and not any(mid in v for v in m.MAP_LINKS.values())):
        bad.append('无入边')
    if len(d['name'].encode('gbk')) > 255:
        bad.append('名称过长')
    check(not bad, '%d %s 逐图巡检%s' % (mid, d['name'], '（%s）' % '；'.join(bad) if bad else ''))

# ── 7) 鼠标导航：小地图图面点击 → C2SPlayerWizard(151) 'goto scene,x,y' ──────
s.mapid, s.px, s.py = 1001, m.MAPS[1001]['spawn'][0], m.MAPS[1001]['spawn'][1]
cn = FakeConn()
s._dispatch(cn, m.C2S_PLAYER_WIZARD, b'goto 1001,60,44')
check(s.mapid == 1001 and (s.px, s.py) == (60, 44) and ops(cn)[-1] == m.OP_MAP_GOTO_M,
      '鼠标导航·同图：goto 1001,60,44 → %d@%d,%d %s' % (s.mapid, s.px, s.py, ops(cn)))
nb = m.MAP_LINKS[1001][0]
cn = FakeConn()
s._dispatch(cn, m.C2S_PLAYER_WIZARD, b'goto %d,7,8' % nb)
check(s.mapid == nb and (s.px, s.py) == (7, 8) and ops(cn)[0] == m.OP_MAP_SCENE_M,
      '鼠标导航·跨图：goto %d,7,8 → %d@%d,%d %s' % (nb, s.mapid, s.px, s.py, ops(cn)))

shutil.rmtree(tmp, ignore_errors=True)
print('\n=== 多地图/传送冒烟 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
