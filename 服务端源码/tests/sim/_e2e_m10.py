# -*- coding: utf-8 -*-
"""M10 冒烟测试：NPC 买卖闭环（买 op147/99 + 卖 op148/106），全走真实 socket。

逐条断言：
  ① 点村长 → 152 菜单含「卖」选项（4 个 Q 选项，离开排最后）
  ② 选项 2 → S2CNpcBuyList(147)；买 1 件 → S2CItemAdd(33) + 金币减少
  ③ 选项 3 → S2CNpcSellList(148)；清单按造型汇总，价格 = 售价/4
  ④ C2S 106 flag=0（列表下标 + 数量）→ ItemQuantity/ItemDel + PropChange(m_Gold=43)
  ⑤ C2S 106 flag=1（实例 id）→ 卖整摞，ItemDel
  ⑥ 卖空的清单项再卖 → 只回一句 152，不加钱（清单顺序不变）

用法：python _e2e_m10.py
"""
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m
import _e2e_m4 as E

PORT = 3140
SEED = 0x0BADF00D
steps = []


def expect(c, op_want, what):
    op, data = E.recv_s2c(c)
    assert op == op_want, '%s: 期望 op=%d 实得 %d (%s)' % (what, op_want, op, data.hex())
    return data


def drain(c, timeout=0.4):
    out = []
    c.settimeout(timeout)
    try:
        while True:
            op, data = E.recv_s2c(c)
            out.append((op, data))
    except (socket.timeout, ConnectionError):
        pass
    finally:
        c.settimeout(6)
    return out


def op_of(frames):
    return [o for o, _ in frames]


def gold_in(frames):
    """取最后一个 PropChange(m_Gold=43) 的值。"""
    val = None
    for op, body in frames:
        if op == m.OP_PROP_CHANGE and body[0] == m.PROP_IDX_GOLD:
            val = int.from_bytes(body[1:5], 'little')
    return val


def parse_sell_list(body):
    """S2CNpcSellList body → (mode, [(shape, amount, price)])。"""
    mode = body[0]
    i, out = 1, []
    while True:
        shape = struct.unpack('<I', body[i:i + 4])[0]
        if shape == 0:
            break
        amount = struct.unpack('<H', body[i + 4:i + 6])[0]
        price = struct.unpack('<I', body[i + 6:i + 10])[0]
        out.append((shape, amount, price))
        i += 10
    return mode, out


def parse_item_add(body):
    """S2CItemAdd body → (iid, shape, qty, pos)。"""
    _type = body[0]
    iid = struct.unpack('<I', body[1:5])[0]
    shape = struct.unpack('<I', body[5:9])[0]
    qty = body[11]
    pos = body[12]
    return iid, shape, qty, pos


def run():
    tmp = tempfile.mkdtemp(prefix='m4m10_')
    srv = m.M4Server('127.0.0.1', PORT, seed=SEED, encounter_every=0, enemies=1,
                     turn_timeout=30, logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    srv._loaded = None
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.4)

    c = socket.create_connection(('127.0.0.1', PORT), timeout=6)
    op, data = E.recv_s2c(c)
    assert op == 1, op
    c.sendall(E.hello_resp(SEED))
    E.send_c2s(c, 1, E.login_body())
    op, data = E.recv_s2c(c)
    assert op == 2 and data[0] == 0, (op, data.hex())
    E.send_c2s(c, 3, '测试角色'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4)
    expect(c, 6, '建角')
    drain(c, 0.25)
    E.send_c2s(c, 2, struct.pack('<I', 1))
    while True:                             # 跳到世界就绪
        op, data = E.recv_s2c(c)
        if op == 97:
            break
        assert op in (2, 3, 6, 16, 17, 18, 19, 65, 96, 99), ('进世界意外帧 op=%d' % op)
    bag = drain(c, 0.3)                    # 宠物帧 + 背包 ItemAdd
    items = [parse_item_add(b) for o, b in bag if o == m.OP_ITEM_ADD]
    assert items, '登录后应收到背包 ItemAdd'
    steps.append('进世界：背包 %d 件' % len(items))

    # ① 村长菜单
    E.send_c2s(c, 96, struct.pack('<I', m.NPC_ID))
    data = expect(c, 152, '村长菜单')
    assert '卖'.encode('gbk') in data, data.hex()
    assert data.count(b'Q') == 4, ('菜单应有 4 个选项', data.count(b'Q'))
    steps.append('村长菜单 4 选项（任务/买/卖/离开）')

    # ② 先买 1 件幻灵珠（保证后面有东西可卖）
    gold0 = srv.coins
    E.send_c2s(c, 98, struct.pack('<I', 2))
    expect(c, 152, '「看看想要点什么」')
    expect(c, 147, '购买列表')
    shop_idx = next(i for i, (shape, _n, _p, _q) in enumerate(m.SHOP_ITEMS) if shape == 10411)
    E.send_c2s(c, 99, struct.pack('<HH', shop_idx, 1))
    fr = drain(c, 0.5)
    assert m.OP_ITEM_ADD in op_of(fr), op_of(fr)
    assert gold_in(fr) == gold0 - 500, (gold_in(fr), gold0)
    bought = parse_item_add([b for o, b in fr if o == m.OP_ITEM_ADD][0])
    steps.append('买入 10411：金币 %d→%d' % (gold0, gold_in(fr)))

    # ③ 卖东西：选项 3 → 148
    E.send_c2s(c, 98, struct.pack('<I', 3))
    expect(c, 152, '「有什么要卖的」')
    body = expect(c, 148, '收购列表')
    mode, entries = parse_sell_list(body)
    assert mode == 1, mode
    assert entries, entries
    shapes = [e[0] for e in entries]
    assert shapes == sorted(shapes), ('清单应造型升序', shapes)
    assert all(p >= 1 for _s, _q, p in entries), entries
    steps.append('收购清单 %d 种（mode=1，%s）'
                 % (len(entries), ', '.join('%d×%d@%d' % e for e in entries[:3])))

    # ④ flag=0：按列表下标卖 1 件
    idx = 0
    shape0, qty0, price0 = entries[0]
    gold_before = srv.coins
    E.send_c2s(c, 106, bytes([0, idx]) + struct.pack('<H', 1))
    fr = drain(c, 0.5)
    got = op_of(fr)
    assert m.OP_ITEM_QUANTITY in got or m.OP_ITEM_DEL in got, got
    assert gold_in(fr) == gold_before + price0, (gold_in(fr), gold_before, price0)
    steps.append('按下标卖出 shape=%d ×1 → +%d 金' % (shape0, price0))

    # ⑤ flag=1：按实例 id 卖整摞（用刚买的那件，qty=1）
    iid, shape_b, qty_b, _pos = bought
    gold_before = srv.coins
    E.send_c2s(c, 106, bytes([1]) + struct.pack('<I', iid))
    fr = drain(c, 0.5)
    got = op_of(fr)
    assert m.OP_ITEM_DEL in got, got
    assert gold_in(fr) == gold_before + m.sell_price_of(shape_b) * qty_b, \
        (gold_in(fr), gold_before, shape_b, qty_b)
    assert iid not in srv.items, '卖掉的实例应离开背包'
    steps.append('按实例卖 %d ×%d → +%d 金' % (iid, qty_b, gold_in(fr) - gold_before))

    # ⑥ 卖空项再卖：只回 152，不加钱（清单顺序不得变）
    #   先记住当前清单第 0 项，把它清空
    E.send_c2s(c, 98, struct.pack('<I', 3))
    expect(c, 152, '「有什么要卖的」')
    _mode, entries2 = parse_sell_list(expect(c, 148, '刷新收购列表'))
    shape_c, qty_c, _p = entries2[0]
    E.send_c2s(c, 106, bytes([0, 0]) + struct.pack('<H', qty_c))
    drain(c, 0.5)
    gold_after_clear = srv.coins
    E.send_c2s(c, 106, bytes([0, 0]) + struct.pack('<H', 1))
    fr = drain(c, 0.4)
    assert op_of(fr) == [152], ('卖空后再卖应只回对话', op_of(fr))
    assert srv.coins == gold_after_clear, '拒绝时不得加钱'
    assert srv._sell_cache[0][0] == shape_c and srv._sell_cache[0][1] == 0, \
        ('清单顺序必须保持', srv._sell_cache)
    steps.append('卖空条目拒绝且清单不重排（首项仍 shape=%d）' % shape_c)

    c.close()
    # ★ 显式收尾（2026-10-20）：daemon 线程必须在解释器 finalizing 之前结束。
    #   否则它若正卡在 log() 的 print 里，会与关闭流程争 stdout 的 BufferedWriter 锁，
    #   触发 Fatal Python error: _enter_buffered_busy，进程以 0xC0000409 退出
    #   ——**测试其实已经 PASS**，崩在收尾，看起来像随机失败。
    #   实测：不做显式收尾约 27% 概率崩；stop()+join() 后 0/20。
    try:
        srv.stop()
        for _th in threading.enumerate():
            if _th is not threading.current_thread() and _th.daemon:
                _th.join(timeout=2.0)
    except Exception:                      # noqa: BLE001
        pass
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    run()
    print()
    print('PASS：' + ' → '.join(steps))
    print('全部 %d 步断言通过' % len(steps))
