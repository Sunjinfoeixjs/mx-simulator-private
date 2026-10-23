# -*- coding: utf-8 -*-
"""mapid ↔ 场景名 求解器 + 全图数据表生成器（多地图/传送 M7 的数据来源，可复现）。

原理
----
`src/data/minimap_data.py` 给出每张地图上的「出口标记」：`DATA[mapid] = {(x,y): {name, desc}}`，
其中 `name` 是**该出口通往的场景名**。
`src/data/findpathdata.py` 给出场景名的**邻接关系**：`DATA[名] = (邻接场景名…)`。

于是对任一 mapid，其出口名集合 E 应与「本图场景名 N 的邻接集合 S(N)」高度重合。
用 Jaccard(E, S) 对全部候选名打分，取最高分者即该图的场景名——
Jaccard=1.0 即完全重合（高置信）。

为什么 5 张图能连通、其余「尚未批量跑」
------------------------------------
最初的 M7 只手工挑了 5 张（1001~1004 + 1006）。本脚本把同一套联立**批量**跑到全部 mapid。

选图口径 = **每个场景取一个代表 mapid**（不是「按单个 mapid 的 Jaccard 阈值切」）：

  · 若按 mapid 的 Jaccard ≥ 1.0 切，只得到 51 张、且碎成 **9 个分量**
    （出生图 1001 所在分量仅 5 张）——名义上「高置信」，实际走不通。
  · 原因：城市图的出口名里混了「师道殿/擂台/码头」等**子地点**，把 Jaccard 分母撑大，
    于是明月镇/天水平原/长风岛这些**枢纽**反而落到阈值下——枢纽一没，全图就散。
  · 改为「每场景一个代表」后：60 张、名字天然单射、主分量 **56 张（含出生图 1001）**，
    另有初云峡谷区域 4 张构成真孤岛（其出口「天空要塞」在本库无 mapid）。

代表性由 Jaccard 决定：同一场景名的多个 mapid 取最优者（详见 select()）。

本脚本产出 m4_server.py 的 MAPS / MAP_LINKS / MAP_EXITS / NAME2MAP 四张表。

用法
----
    C:\\Tools\\python38\\python.exe mapdata_resolve.py            # 打印全部高置信结果（复核用）
    C:\\Tools\\python38\\python.exe mapdata_resolve.py 1001 1006  # 只看指定 mapid
    C:\\Tools\\python38\\python.exe mapdata_resolve.py --stats    # 各置信档计数
    C:\\Tools\\python38\\python.exe mapdata_resolve.py --emit     # 生成四张表的 Python 字面量

注意：反编译产物含模块级裸 `return`（uncompyle6 伪语句），导入前须剔除。
"""
import io
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), '客户端源码', 'src', 'data')

# 各图统一 id/造型：传送使（SceneM 会清空对象，故各图可复用同一 id）
TELEPORT_NPC_ID = 1001
TELEPORT_NPC_SHAPE = 4427

# ── 随身 NPC（客户端自动寻路的 NPC 路线）──
# 客户端 `autofindpath.AutoGo` 换图时读 `pathdata.DATA[场景名][目标场景名] = (route, idx)`：
#   route 是整数对 → 走到该图出口坐标（服务端出口自动传送）；
#   route 是 NPC 名 → 走到该 NPC 身边点击，弹目的地菜单。
# 本服原先每图只摆一只「传送使」，pathdata 点名的车夫/船夫/… 根本不存在 → 客户端寻路卡住。
# 这里按 pathdata 的路线名，为每张图补挂同名随身 NPC（造型沿用已验证资源）。
TRAVEL_NPC_ID_BASE = 1200    # id = 1200 + 该图路线名序（每图 ≤2 个，不占用 1001/1000/1002/1100）
# 客户端 `netlogin._vid2ver` + 本服固定 vid=1 → ver=2；npcposdata 记录按 ver2mask 过滤
NPCPOS_VER_MASK = {0: 1, 1: 2, 2: 4}
SIM_SERVER_VERSION = 2

# ── 手工钉死的锚点（既有 5 图）──
# 这 5 张是 M7 落地时逐张真机验证过的：出生点/NPC 站位/出口坐标都不是推断值，
# 而是 npcposdata / minimap_data 的实测点。生成器对它们**原样保留**，
# 并用自动联立的结果反查一致性（links/exits 必须与既有表逐项相等）。
ANCHORS = {
    1001: {'name': '永泽城', 'spawn': (45, 40),
           'npcs': [(1000, 1013, 47, 42, '村长'),
                    (1001, 4427, 60, 55, '传送使'),
                    (1002, 4427, 52, 38, '战斗技能师')],
           'exits': [('新月平原', 342, 19)], 'links': [1006]},
    1006: {'name': '新月平原', 'spawn': (99, 42),
           'npcs': [(1100, 4136, 69, 47, '粘土矿老板'),
                    (1001, 4427, 41, 108, '传送使')],
           'exits': [('永泽城', 44, 139), ('谷雨镇', 95, 5)], 'links': [1001, 1003]},
    1003: {'name': '谷雨镇', 'spawn': (104, 84),
           'npcs': [(1100, 4752, 104, 84, '谷雨镇商人'),
                    (1001, 4427, 46, 40, '传送使')],
           'exits': [('新月平原', 151, 111), ('清溪平原', 147, 4)], 'links': [1006, 1004]},
    1004: {'name': '清溪平原', 'spawn': (106, 48),
           'npcs': [(1100, 4298, 144, 8, '王二'),
                    (1001, 4427, 106, 48, '传送使')],
           'exits': [('清溪城', 152, 29), ('谷雨镇', 12, 113)], 'links': [1003, 1002]},
    1002: {'name': '清溪城', 'spawn': (66, 117),
           'npcs': [(1100, 4205, 39, 11, '公孙闲'),
                    (1001, 4427, 66, 117, '传送使')],
           'exits': [('清溪平原', 5, 141)], 'links': [1004]},
}


def load_data(fname):
    """读 src/data/*.py 取 DATA（剥离模块级裸 `return`，保留缩进）。"""
    src = io.open(os.path.join(SRC, fname), encoding='utf-8').read()
    src = '\n'.join((l[:len(l) - len(l.lstrip())] + 'pass') if l.strip() == 'return' else l
                    for l in src.splitlines())
    ns = {}
    exec(compile(src, fname, 'exec'), ns)
    return ns['DATA']


def dec(x):
    return x.decode('gbk', 'replace') if isinstance(x, (bytes, bytearray)) else x


def npcpos_index(ver=SIM_SERVER_VERSION):
    """mapid → {NPC 名: (x, y)}：复刻客户端 `npcposdata.GetData` 的版本过滤。

    客户端 `GetNpcPosByName` 用 `v[0]`=名、`v[4]`=坐标（见 autofindpath.py:41-53），
    过滤条件是 `v[7] & ver2mask(g_ServerVersion)`；本服固定 vid=1 → ver=2 → mask=4。
    """
    data = load_data('npcposdata.py')
    mask = NPCPOS_VER_MASK.get(ver, 0)
    out = {}
    for mapid, recs in data.items():
        idx = {}
        for v in recs:
            if len(v) >= 8 and int(v[7]) & mask:
                name = dec(v[0]).strip()
                pos = v[4]
                if name and isinstance(pos, (tuple, list)) and len(pos) >= 2:
                    idx.setdefault(name, (int(pos[0]), int(pos[1])))
        out[int(mapid)] = idx
    return out


def travel_dests(maps, name2map, pd, npcpos=None):
    """每图「随身 NPC → (真名, 目的地 mapid 列表)」+ 路线名全集。

    `pathdata.DATA[场景名][目标场景名] = (route, idx)`：只收 `route[0]` 非整数的 NPC 路线；
    整数对是坐标出口（走出口自动传送，无需 NPC）。目标名解析不到 mapid 的（如「集市」「天空要塞」）丢弃。
    """
    travel, names = {}, set(['传送使'])
    routes = {dec(k): {dec(d): rt for d, rt in vs.items()} for k, vs in pd.items()}
    for mid in sorted(maps):
        scene = maps[mid]['name']
        by_name = {}
        for dst_scene, route in (routes.get(scene) or {}).items():
            if not route or isinstance(route[0], int):
                continue
            rname = dec(route[0]).strip()
            dest = name2map.get(dst_scene)
            if rname and dest is not None and dest != mid:
                by_name.setdefault(rname, set()).add(dest)
        if not by_name:
            continue
        entry = {}
        for i, rname in enumerate(sorted(by_name, key=lambda n: n.encode('gbk', 'replace'))):
            entry[TRAVEL_NPC_ID_BASE + i] = (rname, sorted(by_name[rname]))
            names.add(rname)
        travel[mid] = entry
    return travel, names


def travel_pos(mid, name, marker_xy, spawn, base_npcs, npcpos):
    """随身 NPC 落点优先级：npcposdata 实测坐标 → 锚点「传送使」坐标 → 该图任一 minimap 标记（≠spawn）。"""
    p = (npcpos or {}).get(mid, {}).get(name)
    if p:
        return (int(p[0]), int(p[1]))
    for (_nid, _s, x, y, nn) in base_npcs:
        if nn == '传送使':
            return (int(x), int(y))
    for xy in marker_xy:
        if tuple(xy) != tuple(spawn):
            return (int(xy[0]), int(xy[1]))
    return (int(spawn[0]), int(spawn[1]))


def exit_names(mm):
    """mapid → 该图出口指向的场景名集合（剔除占位 "."）。"""
    out = {}
    for mapid, pts in mm.items():
        names = set()
        for _xy, info in pts.items():
            n = dec(info.get('name', b'')).strip()
            if n and n != '.':
                names.add(n)
        out[mapid] = names
    return out


def adjacency(fp):
    """场景名 → 邻接场景名列表（保持 findpathdata 的原始顺序，供 MAP_LINKS 定序）。"""
    return {dec(k): [dec(v) for v in vs] for k, vs in fp.items()}


def resolve(mm, fp):
    """全部 mapid → (best_name, jaccard, exit_names)。"""
    en = exit_names(mm)
    adj = adjacency(fp)
    rows = {}
    for mapid, names in en.items():
        if not names:
            continue
        best = max(((len(names & set(nb)) / float(len(names | set(nb)) if (names | set(nb)) else 1),
                     n) for n, nb in adj.items()))
        rows[mapid] = (best[1], best[0], names)
    return rows, adj


def select(rows, min_jaccard=0.0):
    """每场景取一个代表 mapid（最优 Jaccard，同分取小 mapid），外加钉死的锚点。

    这样选出的集合，其场景名天然两两不同（单射），NAME2MAP 不会撞名；且把
    Jaccard 低分但**名确凿**的城市（明月镇 0.22 / 永泽城 0.25 …）也纳进来——
    它们正是连接各区域的枢纽，缺一张就散一片。
    """
    best = {}                                   # 场景名 → (jaccard, mapid)
    for mid, (name, j, _e) in rows.items():
        if j < min_jaccard:
            continue
        cur = best.get(name)
        if cur is None or j > cur[0] or (j == cur[0] and mid < cur[1]):
            best[name] = (j, mid)
    chosen = {mid for _j, mid in best.values()}
    chosen |= set(ANCHORS)
    return chosen


def build(mm, fp, rows, adj, chosen, strict=True, travel=None, npcpos=None):
    """生成 MAPS / MAP_LINKS / MAP_EXITS / NAME2MAP 四张表。

    新图规则：
      · name/minimap  = 联立结论 / mapid
      · spawn         = 该图 minimap 出口标记里、**通往已解析邻图**的那个标记坐标
                        （真实存在、可站位的点；保证不落在墙里，零新逆向）
      · npcs          = 传送使一只，摆在另一处出口标记上（与 spawn 分开）
      · exits         = 出口标记中、目标名已解析的项（剔除子地点与 "."）
      · links         = findpathdata 邻接中、目标名已解析的项（保持原顺序）
    """
    name2map, maps, links, exits = {}, {}, {}, {}
    for mid in sorted(chosen):
        name = rows[mid][0]
        if name in name2map:
            raise SystemExit('名称冲突：%s 同时解析到 mapid %d 与 %d' % (name, name2map[name], mid))
        name2map[name] = mid

    for mid in sorted(chosen):
        name = rows[mid][0]
        markers = sorted((xy, dec(info.get('name', b'')).strip())
                         for xy, info in mm.get(mid, {}).items())
        # 真门 = 出口标记里、目标名已解析且不是自己的那些
        doors = [(xy, nm) for xy, nm in markers if nm in name2map and name2map[nm] != mid]
        if mid in ANCHORS:
            # 锚点只钉住逐张真机验证过的 name / spawn / npcs；
            # links 与 exits 仍按**全图口径**自动生成——旧手工表是「只有 5 张图」时代的
            # 子集（例：1001 当时只登记了新月平原，实际还接明月镇），照抄会漏掉枢纽、把全图切断。
            a = ANCHORS[mid]
            maps[mid] = {'name': name, 'minimap': mid,
                         'spawn': tuple(a['spawn']),
                         'npcs': [tuple(t) for t in a['npcs']]}
        else:
            # spawn：优先取真门标记；若该图邻图全在选图之外（如 1025 清风村2），
            # 退化为任意一个真实标记——标记一定是该图上真实存在、可站位的点。
            spawn = doors[0][0] if doors else (markers[0][0] if markers else None)
            if spawn is None:
                raise SystemExit('地图 %d(%s) 无任何出口标记，无法定出生点' % (mid, name))
            others = [xy for xy, _nm in doors if xy != spawn]
            others += [xy for xy, _nm in markers if xy != spawn and xy not in others]
            npc_xy = others[0] if others else spawn
            maps[mid] = {'name': name, 'minimap': mid, 'spawn': tuple(spawn),
                         'npcs': [(TELEPORT_NPC_ID, TELEPORT_NPC_SHAPE, npc_xy[0], npc_xy[1], '传送使')]}
        exits[mid] = [(nm, xy[0], xy[1]) for xy, nm in doors]
        lk = []
        for nb in adj.get(name, []):
            t = name2map.get(nb)
            if t is not None and t != mid and t not in lk:
                lk.append(t)
        links[mid] = lk

    # 随身 NPC：锚点/生成定稿之后统一补挂（保证 id 不与既有撞，且不动锚点手写表）
    if travel:
        for mid in sorted(travel):
            if mid not in maps:
                continue
            base = maps[mid]['npcs']
            marker_xy = [xy for xy, _nm in sorted(
                (xy, dec(info.get('name', b'')).strip()) for xy, info in mm.get(mid, {}).items())]
            for nid in sorted(travel[mid]):
                nm, _dests = travel[mid][nid]
                x, y = travel_pos(mid, nm, marker_xy, maps[mid]['spawn'], base, npcpos)
                base.append((nid, TELEPORT_NPC_SHAPE, x, y, nm))

    if strict:
        # 锚点反查：手工表（只有 5 图时挑的邻接）必须是自动全图结果的**子集**，
        # 而不是相等——现在解析出了更多真实邻图（例：1001 多出明月镇）。
        for mid, a in ANCHORS.items():
            if not set(a['links']) <= set(links[mid]):
                raise SystemExit('锚点 %d 的手工 links 不在自动结果内：%r ⊄ %r'
                                 % (mid, a['links'], links[mid]))
            if not set(a['exits']) <= set(exits[mid]):
                raise SystemExit('锚点 %d 的手工 exits 不在自动结果内：%r ⊄ %r'
                                 % (mid, a['exits'], exits[mid]))
    return maps, links, exits, name2map


# ────────────────────────────── 输出 ──────────────────────────────
def connectivity(links, start):
    """沿 MAP_LINKS 从 start 做 BFS，返回 (可达集合, 孤岛列表)。"""
    seen, q = set(), []
    if start in links:
        seen.add(start)
        q.append(start)
    while q:
        x = q.pop()
        for t in links.get(x, []):
            if t not in seen:
                seen.add(t)
                q.append(t)
    return seen, sorted(set(links) - seen)


def emit(maps, links, exits, name2map, start=1001, out=sys.stdout, travel=None, names=None):
    """把四张表打成可直接粘进 m4_server.py 的 Python 字面量。"""
    w = out.write
    seen, stranded = connectivity(links, start)
    w('# ── 全图连通性（BFS，自 mapid %d）──\n' % start)
    w('# 可达 %d / 共 %d 张。以下 %d 张不在出生图所在分量内（自 %d 走不到，只能靠 GM 切图）：\n'
      % (len(seen), len(maps), len(stranded), start))
    w('# 成因：它们唯一的外部出口（如「天空要塞」）在本库没有对应 mapid → 真孤岛，*不是* bug。\n')
    for mid in stranded:
        w('#   %d %s\n' % (mid, maps[mid]['name']))
    w('\n')
    w('# mapid → {\'name\', \'minimap\', \'spawn\', \'npcs\': [(id, shape, x, y, name)]}\n')
    w('MAPS = {\n')
    for mid in sorted(maps):
        d = maps[mid]
        w('    %d: {\'name\': %r, \'minimap\': %d, \'spawn\': %r, \'npcs\': [\n'
          % (mid, d['name'], d['minimap'], tuple(d['spawn'])))
        for (nid, shape, nx, ny, nn) in d['npcs']:
            w('        (%d, %d, %d, %d, %r),\n' % (nid, shape, nx, ny, nn))
        w('    ]},\n')
    w('}\n\n')
    w('# 邻接关系（findpathdata.py 真实连通，仅保留已解析目标）\n')
    w('MAP_LINKS = {\n')
    for mid in sorted(links):
        w('    %d: %r,\n' % (mid, links[mid]))
    w('}\n\n')
    w('# 出口点名 → mapid（用于「走出出口自动传送」）\n')
    w('NAME2MAP = {\n')
    for name in sorted(name2map, key=lambda n: name2map[n]):
        w('    %r: %d,\n' % (name, name2map[name]))
    w('}\n\n')
    w('# 每张图的出口（名, 服务端 x, y）——minimap_data.py 真实标记（仅保留已解析目标）\n')
    w('MAP_EXITS = {\n')
    for mid in sorted(exits):
        w('    %d: %r,\n' % (mid, [tuple(t) for t in exits[mid]]))
    w('}\n\n')
    if travel is not None:
        w('# 随身 NPC：pathdata 路线名 → 目的地 mapid（op96 点该 NPC → 目的地菜单）\n')
        w('TRAVEL_NPCS = {\n')
        for mid in sorted(travel):
            w('    %d: {\n' % mid)
            for nid in sorted(travel[mid]):
                w('        %d: %r,\n' % (nid, travel[mid][nid][1]))
            w('    },\n')
        w('}\n\n')
        w('# 路线 NPC 名字全集（∪传送使）：op96/op98 按「NPC 真名」判定是否走传送菜单\n')
        w('TRAVEL_NPC_NAMES = %r\n\n'
          % (tuple(sorted(names or (), key=lambda n: n.encode('gbk', 'replace'))),))


def main(argv):
    if '--emit' in argv:
        mm, fp = load_data('minimap_data.py'), load_data('findpathdata.py')
        pd = load_data('pathdata.py')
        npcpos = npcpos_index()
        rows, adj = resolve(mm, fp)
        chosen = select(rows)
        maps0, _links, _exits, name2map = build(mm, fp, rows, adj, chosen)
        travel, tnames = travel_dests(maps0, name2map, pd, npcpos)
        maps, links, exits, name2map = build(mm, fp, rows, adj, chosen,
                                             travel=travel, npcpos=npcpos)
        emit(maps, links, exits, name2map, travel=travel, names=tnames)
        sys.stderr.write('[emit] %d 张地图（含锚点 %d 张）；随身 NPC：%d 张图 / 名字 %d 个\n'
                         % (len(maps), len(ANCHORS), len(travel), len(tnames)))
        return 0

    want = set(int(a) for a in argv[1:] if a.isdigit()) or None
    mm, fp = load_data('minimap_data.py'), load_data('findpathdata.py')
    rows, _adj = resolve(mm, fp)

    if '--stats' in argv:
        for thr in (1.0, 0.8, 0.5):
            print('jaccard >= %.1f : %d' % (thr, sum(1 for _n, j, _e in rows.values() if j >= thr)))
        placeholders = [mid for mid, pts in mm.items()
                        if all(dec(i.get('name', b'')).strip() in ('', '.') for i in pts.values())]
        print('jaccard <  0.5 : %d' % sum(1 for _n, j, _e in rows.values() if j < 0.5))
        print('有出口的 mapid : %d' % len(rows))
        print('纯占位 mapid   : %d' % len(placeholders))
        print('选图（每场景一代表）：%d' % len(select(rows)))
        return 0

    rrows = [(mid, n, round(j, 3), sorted(e)) for mid, (n, j, e) in rows.items()]
    rrows.sort(key=lambda r: -r[2])
    print('%-6s %-14s %-8s %s' % ('mapid', 'best_name', 'jaccard', 'exit_names'))
    for mapid, name, j, en in rrows:
        if want and mapid not in want:
            continue
        if j < 1.0 and not want:
            continue
        print('%-6d %-14s %-8s %s' % (mapid, name, j, '/'.join(en)))

    print('\n=== 既有 5 张连通图 ===')
    for mid in (1001, 1006, 1003, 1004, 1002):
        n, j, _e = rows[mid]
        print('%d -> %s (jaccard=%.2f)' % (mid, n, j))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
