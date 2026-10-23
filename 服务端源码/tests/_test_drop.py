# -*- coding: utf-8 -*-
"""掉落真值表 + 战斗掉落冒烟（M8）。

验证三件事：
  1. `drop_data.py` 的池来自真值：每个 item id 都能在客户端 itemdata 里查到，
     且表里登记的名字与客户端的名字一致（防手改/防错位）。
  2. 目标场景（新月平原 / 清溪平原）池条目与《资料全库》一致；
     城镇（永泽城）没有池，不掉落。
  3. `_roll_drops` 下发的 S2CItemAdd(33) 合法：bag id、shape、pos 均在契约内，
     且 shape 必须落在当前场景池里；背包满时安全跳过。
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m
import drop_data as dd
from gen_drop_data import parse_items

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


class FakeConn:
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(b)

    def ops(self):
        return [f[1] for f in self.sent]


# ── 1) 真值校验：池里的 id/名字必须与客户端 itemdata 对得上 ──────────────
items = parse_items()
bad = []
for scene, d in dd.DROP_BY_SCENE.items():
    for token, how, hit in d['pools']:
        for iid, name in hit:
            real = items.get(iid)
            if real is None or real[0] != name:
                bad.append((scene, token, iid, name, real[0] if real else None))
check(not bad, '全部 %d 个场景的掉落 id/名称都能在客户端 itemdata 中对上 (errors=%d)'
      % (len(dd.DROP_BY_SCENE), len(bad)))
if bad:
    for b in bad[:5]:
        print('    mismatch:', b)

check(dd.UNRESOLVED == [], '资料表掉落物名 0 条未解析 (got %d)' % len(dd.UNRESOLVED))

# ── 2) 两个野外场景的池与资料表一致 ─────────────────────────────────────
xy = dd.scene_pool('新月平原')
qx = dd.scene_pool('清溪平原')
check(xy is not None and [t for t, _, _ in xy['pools']] == ['20级武器', '20级防具', '梦见草'],
      '新月平原池 = 20级武器 / 20级防具 / 梦见草')
check(xy['level'] == 20 and xy['monsters'] == ['小山猪', '金花鼠', '刺猬'],
      '新月平原 场景等级 20、怪物 小山猪/金花鼠/刺猬')
check(qx is not None and [t for t, _, _ in qx['pools']] == ['20级防具', '天心果'],
      '清溪平原池 = 20级防具 / 天心果')
check(dd.scene_pool('永泽城') is None, '永泽城（城镇）没有掉落池')

xy_ids = {iid for _t, _h, hit in xy['pools'] for iid, _n in hit}
check(50030 in xy_ids or any(name == '青霜剑' for _t, _h, hit in xy['pools']
                              for _i, name in hit),
      '新月平原池含真实物品（如 20 级剑）')

# ── 3) _roll_drops 行为 ────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix='m8drop_')
s = m.M4Server('127.0.0.1', 3167, 0x1234, 5, enemies=6, turn_timeout=30,
               logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
s.mapid = m.NAME2MAP['新月平原']

m.DROP_CHANCE = 1.0
conn = FakeConn()
got = s._roll_drops(conn)
check(len(got) == 1, '保证掉落时掷出 1 件 (got %d)' % len(got))
adds = [f for f in conn.sent if f[1] == m.OP_ITEM_ADD]
check(len(adds) == 1, '下发 1 帧 S2CItemAdd(op33)')
if adds:
    body = adds[0][2:]          # 帧头 [len][opcode]
    wtype = body[0]
    bag_id = struct.unpack('<I', body[1:5])[0]
    shape = struct.unpack('<I', body[5:9])[0]
    qty = body[11]
    pos = body[12]
    check(wtype == m.ITEM_WTYPE_NORMAL, 'type=普通物品 (got %d)' % wtype)
    check(bag_id == got[0], '帧里的 bag id 与入包 id 一致 (%d)' % bag_id)
    check(shape in xy_ids, '掉落 shape=%d 在新月平原池内' % shape)
    check(qty == 1, '数量为 1 (got %d)' % qty)
    check(m.ITEM_POS_BAG_START <= pos < m.ITEM_POS_BAG_END,
          '掉落落在背包区间 (pos=%d)' % pos)
    check(s.items[got[0]]['shape'] == shape, '入包记录的 shape 与帧一致')
    check(s.items[got[0]]['infoext'] == items[shape][0], '入包记录的名字来自 itemdata')

before = len(s.items)
m.DROP_CHANCE = 0.0
s._roll_drops(FakeConn())
check(len(s.items) == before, 'DROP_CHANCE=0 时不掉落')

# 城镇：切到永泽城
s.mapid = m.NAME2MAP['永泽城']
m.DROP_CHANCE = 1.0
n_before = len(s.items)
conn2 = FakeConn()
s._roll_drops(conn2)
check(len(s.items) == n_before and not [f for f in conn2.sent if f[1] == m.OP_ITEM_ADD],
      '城镇（永泽城）不掉落、不发 ItemAdd')

# 背包满：安全跳过，不覆盖已有物品
s.mapid = m.NAME2MAP['新月平原']
for pos in range(m.ITEM_POS_BAG_START, m.ITEM_POS_BAG_END):
    s.items[20000 + pos] = {'shape': 12, 'key': 0, 'qty': 1, 'pos': pos, 'infoext': ''}
n_full = len(s.items)
conn3 = FakeConn()
s._roll_drops(conn3)
check(len(s.items) == n_full, '背包满时不新增物品')
check(not [f for f in conn3.sent if f[1] == m.OP_ITEM_ADD], '背包满时不发 ItemAdd')

print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
sys.exit(0 if ok else 1)
