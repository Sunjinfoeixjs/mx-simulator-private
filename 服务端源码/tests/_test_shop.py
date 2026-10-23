"""NPC 商店冒烟：build_npc_buylist 字节格式 + _handle_npc_buy 扣钱/入背包逻辑。"""
import io, os, sys, struct, shutil, tempfile
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m

ok = True
def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c

# ★ 2026-10-16：全程临时存档目录，杜绝真实会话存档（物品/金钱）干扰计数
tmp = tempfile.mkdtemp(prefix='m4shop_')

# 1) 商店列表帧格式（op147, mode2）
items = [(22100, 1, 100), (12, 1, 200)]
frame = m.build_npc_buylist(0, items)
op = frame[1]
body = frame[2:]
check(op == m.S2C_NPC_BUYLIST, 'buylist opcode=147 (got %d)' % op)
check(body[0] == 2, 'mode==2')
check(struct.unpack('<I', body[1:5])[0] == 0, 'ticketid==0')
check(body[5] == 0, 'ext==0')
# 第一条：shape=22100(4B) num=1(2B) price=100(4B)
sh1, num1, pr1 = struct.unpack('<IHI', body[6:16])
check(sh1 == 22100 and num1 == 1 and pr1 == 100, 'entry1 22100/1/100')
# 第二条：shape=12 num=1 price=200
sh2, num2, pr2 = struct.unpack('<IHI', body[16:26])
check(sh2 == 12 and num2 == 1 and pr2 == 200, 'entry2 12/1/200')
# 终止符：4 字节 0
check(body[26:30] == b'\x00\x00\x00\x00', 'terminator shape==0')

# 2) 购买逻辑（直接调用 handler，捕获 sendall）
class FakeConn:
    def __init__(self): self.sent = []
    def sendall(self, b): self.sent.append(b)

s = m.M4Server('127.0.0.1', 3166, 0x1234, 5, enemies=6, turn_timeout=30,
               logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
conn = FakeConn()
coins0 = s.coins
# 买 幻灵珠（index 2, shape 10411, price 500）× 2
s._handle_npc_buy(conn, 2, 2)
check(s.coins == coins0 - 1000, '购买后扣钱 %d→%d' % (coins0, s.coins))
# 应新增 2 件 shape=10411 的物品
bought = [i for i, v in s.items.items() if v['shape'] == 10411 and i >= 9001]
check(len(bought) == 2, '背包新增 2 件幻灵珠 (got %d)' % len(bought))
check(all(m.ITEM_POS_BAG_START <= s.items[i]['pos'] < m.ITEM_POS_BAG_END
          for i in bought), '购买品进入背包空格而非装备位')
# 应下发 2 次 S2CItemAdd(op33) + 1 次确认聊天(op152)
ops = [f[1] for f in conn.sent]
check(ops.count(m.OP_ITEM_ADD) == 2, '下发 2 次 ItemAdd (op33)')
check(m.OP_NPC_CHAT_EXT in ops, '下发购买确认聊天(op152)')
gold = [f for f in conn.sent if f[1] == m.OP_PROP_CHANGE
        and f[2] == m.PROP_IDX_GOLD]
check(len(gold) == 1 and struct.unpack('<I', gold[0][3:7])[0] == s.coins,
      '购买后同步客户端金币属性')

# 3) 金钱不足保护
s.coins = 10
conn2 = FakeConn()
s._handle_npc_buy(conn2, 3, 1)  # 三级鉴定符 800 > 10
check(s.coins == 10, '金钱不足时不扣钱')
check(all(f[1] != m.OP_ITEM_ADD for f in conn2.sent), '金钱不足不入背包')

# 背包满时交易原子失败，不扣钱、不分配物品
s.coins = 50000
for pos in range(m.ITEM_POS_BAG_START, m.ITEM_POS_BAG_END):
    s.items[10000 + pos] = {'shape': 12, 'key': 0, 'qty': 1,
                            'pos': pos, 'infoext': ''}
before_coins, before_items = s.coins, len(s.items)
conn3 = FakeConn()
s._handle_npc_buy(conn3, 2, 1)
check(s.coins == before_coins and len(s.items) == before_items,
      '背包满时购买整体失败')
check(all(f[1] != m.OP_ITEM_ADD for f in conn3.sent),
      '背包满不发送 ItemAdd')

# 4) id 分配不冲突
i1 = s._alloc_iid(); i2 = s._alloc_iid()
check(i1 != i2 and i1 not in s.items and i2 not in s.items, 'iid 分配不冲突')

# 5) NPC 收购（op148 列表 + op106 成交）—— M10
# 用独立存档目录，避免上面的「背包满」用例干扰
s2 = m.M4Server('127.0.0.1', 3168, 0x1234, 5, enemies=6, turn_timeout=30,
                logdir=os.path.join(tmp, 'logs2'), savedir=os.path.join(tmp, 'save2'))
s2._loaded = None

sl = m.build_npc_sell_list([(12, 15, 50), (10411, 6, 125)])
check(sl[1] == m.S2C_NPC_SELLLIST == 148, 'selllist opcode=148 (got %d)' % sl[1])
sb = sl[2:]
check(sb[0] == 1, 'mode==1（普通收购；mode 0 客户端不弹面板）')
sh1, am1, pr1 = struct.unpack('<IHI', sb[1:11])
check((sh1, am1, pr1) == (12, 15, 50), 'entry1 = shape(4)+amount(2)+price(4) → 12/15/50')
sh2, am2, pr2 = struct.unpack('<IHI', sb[11:21])
check((sh2, am2, pr2) == (10411, 6, 125), 'entry2 → 10411/6/125')
check(sb[21:] == b'\x00\x00\x00\x00', '终止符 shape==0')

check(m.sell_price_of(10411) == 125 and m.sell_price_of(12) == 50,
      '收购价 = 售价 1/4（幻灵珠 500→125、鹿茸 200→50）')
check(m.sell_price_of(999999) == m.SELL_PRICE_FALLBACK,
      '未上架造型按兜底价 %d 收' % m.SELL_PRICE_FALLBACK)

# 背包按造型汇总（同造型多摞要合并）
s2.items = {
    9001: {'shape': 12, 'key': 0, 'qty': 10, 'pos': 20, 'infoext': ''},
    9002: {'shape': 12, 'key': 0, 'qty': 5, 'pos': 21, 'infoext': ''},
    9003: {'shape': 10411, 'key': 0, 'qty': 3, 'pos': 22, 'infoext': ''},
}
c4 = FakeConn()
s2._send_sell_list(c4)
check(s2._sell_cache == [(12, 15, 50), (10411, 3, 125)],
      '收购清单按 shape 汇总并缓存：%r' % (s2._sell_cache,))
check(c4.sent[-1][1] == 148, '下发 S2CNpcSellList')

# flag=1：按实例卖整摞
s2.coins = 1000
c4.sent = []
check(s2._handle_npc_sell(c4, bytes([1]) + struct.pack('<I', 9003)) is True,
      'flag=1 按实例 id 卖出成功')
ops4 = [f[1] for f in c4.sent]
check(m.OP_ITEM_DEL in ops4, '整摞卖光 → S2CItemDel(34)')
check(m.OP_PROP_CHANGE in ops4 and s2.coins == 1000 + 3 * 125,
      '金钱 +%d → %d' % (3 * 125, s2.coins))
check(9003 not in s2.items, '卖掉的实例已从背包移除')

# flag=0：按下标 + 数量卖，跨摞扣减
before = s2.coins
c4.sent = []
check(s2._handle_npc_sell(c4, bytes([0, 0]) + struct.pack('<H', 12)) is True,
      'flag=0 按下标卖 12 件（跨两摞）')
qty12 = sum(v['qty'] for v in s2.items.values() if v['shape'] == 12)
check(qty12 == 3, '鹿茸 15→3（实得 %d）' % qty12)
check(s2.coins == before + 12 * 50, '金钱 +%d' % (12 * 50))
check(any(f[1] == m.OP_ITEM_QUANTITY for f in c4.sent), '部分扣减 → S2CItemQuantity(35)')

# 卖空后：清单顺序不变（否则客户端「点 A 卖成 B」），再卖同一项被拒
c4.sent = []
check(s2._handle_npc_sell(c4, bytes([0, 0]) + struct.pack('<H', 99)) is True,
      '把剩下 3 件也卖掉')
check(s2._sell_cache[0][0] == 12 and s2._sell_cache[0][1] == 0,
      '清单第 0 项仍是鹿茸、件数归零：%r' % (s2._sell_cache[0],))
coins_before = s2.coins
c4.sent = []
check(s2._handle_npc_sell(c4, bytes([0, 0]) + struct.pack('<H', 1)) is False,
      '卖空的条目再卖 → 拒绝')
check(s2.coins == coins_before and len(c4.sent) == 1 and c4.sent[0][1] == 152,
      '拒绝时只回一句对话，不加钱')

# 越界下标 / 未知实例
check(s2._handle_npc_sell(FakeConn(), bytes([0, 99]) + struct.pack('<H', 1)) is False,
      '清单下标越界 → 拒绝')
check(s2._handle_npc_sell(FakeConn(), bytes([1]) + struct.pack('<I', 888888)) is False,
      '卖出不存在的实例 → 拒绝')

print('\n=== NPC 商店冒烟 %s ===' % ('PASS' if ok else 'FAIL'))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if ok else 1)
