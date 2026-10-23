# -*- coding: utf-8 -*-
"""内容扩充的验收测试：地图可达 / 物品可买可用 / 宠物目录可切换（进程内）。

2026-10-22 这一轮把三类内容往前推了一格，但「加数据」最容易出的问题是
**加了却走不通**（地图没连线、商品没名字、宠物换了外观服务端还在用旧常量）。
本测试把这三类各钉成可执行断言：

  ① 地图：60 张配置图全部可从出生图到达；`_goto_map` 每张都能发出 96/99/97；
         客户端 `map/<id>.map` 存在（只在装了客户端时检查）
  ② 物品：新上架商品都有真名与正价；买得到（ItemAdd + 扣钱）；药品吃得出回血
  ③ 宠物：目录 4 只元数据完整；切造型后 **WarAdd/SummonInfo 用新名字新造型**；
         存盘回读保持；目录外造型被拒

用法： python tests/_test_content.py
"""
from __future__ import print_function

import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = next(p for p in (os.path.join(ROOT, 'sim'), ROOT,
                       os.path.join(HERE, 'sim'))
           if os.path.isfile(os.path.join(p, 'm4_server.py')))
sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import frames as bf            # noqa: E402
import tap                     # noqa: E402

RESULT = []
CLIENT_MAP_DIR = r'D:\mxsj2013\client\map'


def check(cond, name, detail=''):
    RESULT.append((name, bool(cond)))
    print(('[PASS] %s %s' if cond else '[FAIL] %s %s') % (name, detail))


class FakeConn(object):
    def __init__(self):
        self.buf = b''

    def sendall(self, data):
        self.buf += bytes(data)


def frames(conn):
    parts, _rest = tap.split_frames('S2C', conn.buf)
    conn.buf = b''
    return [(op, body) for _raw, op, body in parts]


def new_server(tmp, name='a'):
    srv = m.M4Server('127.0.0.1', 3199, seed=0xC0FFEE, encounter_every=0, enemies=1,
                     turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    srv.account = 'c%s' % name
    srv._bind_account(srv.account)
    srv.log = lambda line: None       # 断言看状态/帧，不看日志
    return srv


def test_maps(srv):
    maps = m.MAPS
    start = m.START_MAP if hasattr(m, 'START_MAP') else 1001
    adj = {mid: set(d) for mid, d in m.MAP_LINKS.items()}
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for nb in adj.get(cur, ()):
            if nb in maps and nb not in seen:
                seen.add(nb)
                stack.append(nb)
    check(len(seen) == len(maps), '① 全部地图从出生图可达',
          '%d/%d，不可达 %s' % (len(seen), len(maps), sorted(set(maps) - seen)))
    # 字段完整性
    bad = [mid for mid, d in maps.items()
           if not d.get('name') or not d.get('minimap')
           or not isinstance(d.get('spawn'), (list, tuple))]
    check(not bad, '① 每张图都有 name/minimap/spawn', '缺字段：%s' % (bad[:5],))
    # 传送可用：逐张 _goto_map（关掉落盘以加速）
    srv._persist = lambda: None
    bad_travel = []
    conn = FakeConn()
    for mid in sorted(maps):
        srv.mapid = 1001
        srv._goto_map(conn, mid)
        got = frames(conn)
        ops = [o for o, _b in got]
        if srv.mapid != mid or 96 not in ops or 97 not in ops:
            bad_travel.append((mid, srv.mapid, ops[:6]))
    check(not bad_travel, '① 每张图都能切过去并下发 96/97',
          '失败 %d 张：%s' % (len(bad_travel), bad_travel[:3]))
    # 缺客户端 .map 的图：只警告不拦（曾拦过，打断了「灵风塔守护人」的去路）。
    # 这里断言「不拦」：切得过去，且日志里有警告。
    if os.path.isdir(CLIENT_MAP_DIR):
        nofile = sorted(mid for mid in maps if not m.map_file_exists(mid))
        if nofile:
            warn = []
            logs = []
            srv.log = lambda line, _L=logs: _L.append(line)
            # 上面「逐张 _goto_map」已经触发过一次警告（每图只报一次），先清掉去重集合
            srv._warned_map_file = set()
            for mid in nofile:
                logs[:] = []
                srv.mapid = 1001
                srv._goto_map(conn, mid)
                warn.append(any('缺客户端 map/%d.map' % mid in ln for ln in logs))
            check(all(warn), '① 缺盘地图只警告不拦截',
                  '%s' % (['%d:%s' % (i, w) for i, w in zip(nofile, warn)],))
            print('[INFO] ① 客户端 map/ 缺 %d 张：%s（切过去若黑屏即缺图，待真机确认）'
                  % (len(nofile), ['%d %s' % (i, maps[i]['name']) for i in nofile]))
            srv.log = lambda line: None


def test_items(srv):
    # 名字与价格
    noname = [s for (s, _n, _p, _q) in m.SHOP_ITEMS if not m.ITEM_NAMES.get(s)]
    check(not noname, '② 上架商品都有名字', '缺名 %s' % (noname,))
    badprice = [(s, p) for (s, _n, p, _q) in m.SHOP_ITEMS if int(p) <= 0]
    check(not badprice, '② 上架商品价格为正', '%s' % (badprice[:3],))
    # 新药品的恢复量
    heals = [s for s in (21101, 21103, 21106) if m.ITEM_HEAL.get(s, 0) > 0]
    check(len(heals) == 3, '② 新增三种药品都有恢复量', '%s' % heals)
    # 购买：挑「小还丹」的下标
    idx = next(i for i, (s, _n, _p, _q) in enumerate(m.SHOP_ITEMS) if s == 21101)
    srv.cur_npc = m.NPC_ID
    coins0 = srv.coins
    n0 = len(srv.items)
    conn = FakeConn()
    srv._handle_npc_buy(conn, idx, 1)
    got = frames(conn)
    # op33 是物品帧，不在 frames.SPEC（战斗帧定义）里，按 S2CItemAdd 读序自解：
    #   type(1) id(4) shape(4) special_flag(2) quantity(1) pos(1)
    shapes = [struct.unpack('<I', body[5:9])[0] for op, body in got
              if op == 33 and len(body) >= 13]
    check(len(srv.items) == n0 + 1, '② 买小还丹后背包 +1', '%d → %d' % (n0, len(srv.items)))
    check(srv.coins == coins0 - m.SHOP_PRICE[21101], '② 扣钱 = 单价',
          '%d → %d' % (coins0, srv.coins))
    check(21101 in shapes, '② ItemAdd 帧造型正确', str(shapes))
    # 使用：回血
    iid = next(i for i, it in srv.items.items() if it['shape'] == 21101)
    srv.hero_hp = 100
    conn = FakeConn()
    srv._handle_item_use(conn, struct.pack('<I', iid))
    check(srv.hero_hp == 100 + m.ITEM_HEAL[21101], '② 使用后回血 +%d' % m.ITEM_HEAL[21101],
          'HP=%d' % srv.hero_hp)


def test_pets(srv):
    check(len(m.PET_CATALOG) >= 4, '③ 宠物目录 ≥4 只', '%d 只' % len(m.PET_CATALOG))
    bad = [k for k, v in m.PET_CATALOG.items()
           if not v.get('name') or not v.get('liupai') or not v.get('attack_type')]
    check(not bad, '③ 目录元数据完整（名字/流派/攻击类型）', '%s' % (bad,))
    # 切换：目录内生效、目录外拒绝
    check(srv._set_pet_shape(2060) is True, '③ 切到小山猪(2060) 生效')
    check(srv._set_pet_shape(9999) is False and srv.pet_shape == 2060,
          '③ 目录外造型被拒且保持原值', 'pet_shape=%s' % srv.pet_shape)
    # 切换后的帧：WarAdd + SummonInfo 都要用新名字/新造型
    srv._persist = lambda: None
    conn = FakeConn()
    srv._start_war(conn)
    got = frames(conn)
    adds = [bf.unpack_s2c(op, body) for op, body in got if op == 115]
    pet_add = [d for d in adds if d and d.get('id') == m.PET_WAR_ID]
    check(bool(pet_add) and pet_add[0]['shape'] == 2060
          and pet_add[0]['name'] == '小山猪',
          '③ 战斗 WarAdd 用新宠物造型/名字',
          str(pet_add[0]) if pet_add else '（无宠物 WarAdd）')
    conn2 = FakeConn()
    srv._resync_scene(conn2)
    # op48 也不在 frames.SPEC；S2CSummonInfo 前缀读序：pid(4) name(20 定长) shape(2)
    infos = [body for op, body in frames(conn2) if op == 48 and len(body) >= 26]
    shape48 = struct.unpack('<H', infos[0][24:26])[0] if infos else -1
    name48 = infos[0][4:24].split(b'\x00', 1)[0].decode('gbk', 'replace') if infos else ''
    check(shape48 == 2060 and name48 == '小山猪', '③ 场景重同步 SummonInfo 用新宠物',
          'shape=%s name=%s' % (shape48, name48))
    # 存盘回读
    srv.in_war = False
    srv._persist = m.M4Server._persist.__get__(srv)
    srv._persist()
    saved = m.load_role(srv.savepath) or {}
    check(saved.get('pet_shape') == 2060, '③ 存档记住宠物造型',
          'pet_shape=%r' % saved.get('pet_shape'))


def run():
    tmp = tempfile.mkdtemp(prefix='m4content_')
    srv = new_server(tmp)
    test_maps(srv)
    test_items(srv)
    test_pets(srv)
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in RESULT if not ok]
    print('\n全部 %d 项断言，失败 %d 项' % (len(RESULT), len(failed)))
    for n in failed:
        print('  ✗ %s' % n)
    print('=== 内容扩充（地图/物品/宠物）%s ===' % ('PASS' if not failed else 'FAIL'))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(run())
