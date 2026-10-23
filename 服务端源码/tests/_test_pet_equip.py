# -*- coding: utf-8 -*-
"""宠物装备闭环：op54 线序 / C2S 装备路径 / 替换语义 / 原子性 / 存档往返（P1）。

证据基线（全部落地在 m4_server.py 的 PET_EQUIP_ITEMS / _pet_equip 注释里）：
  · op54（S2CSummEquip）线序 = 客户端反编译 netpet.py：pid(4) + 循环
    [pos(1); shape(4); desc_len(1); desc(desc_len)]，pos<=0 结束（A 级）；
  · C2S 入口无独立 opcode：dlgpetitem 右键装备 → netitem.C2SItemSummonUse
    → C2S_ITEM_USE(34) 9 字节 item(4)+summon(4)+flag(1)（net.py C2S 表全量核对）；
  · 规则来自官方攻略 docx（资料库 §22302）：≥20 级可用、同类只能替换、
    旧装备消失；
  · ⚠ shape id 与数值真值**不可得**：PET_EQUIP_ITEMS 初始为空表，C2S 路径
    由测试临时注入 {shape: (pos, name, level)} 驱动机制本身（不猜数据）。

覆盖九组：
  ① op54 线序对账：build_pet_equip ↔ 客户端 Unpack 语义（逐字段/中文 GBK/
     排序/越界滤除/超长截断/帧信封）
  ② GM 入口 gm_pet_equip：三 pos / 坏 pos / 坏值 / 无此宠 / 替换 / 落盘
  ③ C2S 装备路径：9 字节上行 → 帧序 54→道具→48/49/53；等级门（19 拒/20 过）；
     非装备物品不回退；无目标不猜；坏 iid/坏 pid 零帧
  ④ 替换语义：qty=2 连用两次，35 数量帧 → 34 删除帧，装备不叠加
  ⑤ 原子性·同步落盘失败：save_role→False → 回滚 + 零帧 + 磁盘不动
  ⑥ 原子性·异步落盘失败：flush 后 _save_last_ok=False → 同样回滚零帧
  ⑦ 存档往返：equips 逐字段还原；重登 _send_all_pets 补发 54 内容对账
  ⑧ 读档清洗：越界 pos / shape=0 / 非字典值 / 坏键一律丢弃，desc 强制转字符串
  ⑨ 三入口同源：控制台 pet equip（含旧行为 pet <n> 不退化）/ HTTP api/pet/equip

用法：python _test_pet_equip.py
"""
from __future__ import print_function

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import m4_server as m                       # noqa: E402

ok = True

# 测试专用装备 shape（权威源不可得，见 PET_EQUIP_ITEMS 注释；仅驱动机制）
EQ_COLLAR = 29999      # pos 1 项圈
EQ_ARMOR = 30001       # pos 2 铠甲
EQ_AMULET = 30002      # pos 3 护符
EQ_LV = 20             # docx：20 级可用（= PET_EQUIP_LEVEL_MIN）


def check(c, msg, detail=''):
    global ok
    if detail and not c:
        msg += '  ← %r' % (detail,)
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


def s2c_split(raw):
    """剥掉 S2C 帧信封，返回 (opcode, data)。

    ★ 扩展长度帧（M1 协议规格 §1.1〔A〕）：payload ≥255B 时是
      [0xFF][2B u16 LE 长度][payload] —— op54 带长 desc 就会走到这支，
      普通 [1B N] 解析会直接错位。
    """
    if raw[0] == 0xFF:
        n = struct.unpack('<H', raw[1:3])[0]
        assert n == len(raw) - 3, '扩展帧长度不符'
        payload = raw[3:]
    else:
        n = raw[0]
        assert n == len(raw) - 1, '帧长度不符'
        payload = raw[1:]
    return payload[0], payload[1:]


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))

    def ops(self, since=0):
        return [s2c_split(raw) for raw in self.sent[since:]]


def unpack_pet_equip(body):
    """镜像客户端 netpet.S2CSummEquip 的解析语义（线序对账的"另一端"）。

    body = op54 的 data 段（不含 opcode）：pid(4) + 循环[pos(1); shape(4);
    desc_len(1); desc(n)]，pos 读到 <=0 即 break（客户端原样）。
    """
    pid = struct.unpack('<I', body[:4])[0]
    equips = []
    off = 4
    while off < len(body):
        pos = body[off]
        off += 1
        if pos <= 0:
            break
        shape = struct.unpack('<I', body[off:off + 4])[0]
        off += 4
        n = body[off]
        off += 1
        desc = body[off:off + n].decode('gbk', 'replace')
        off += n
        equips.append((pos, shape, desc))
    return pid, equips


_seq = [0]


def make_server(tmp, save_async=False, tag=None):
    _seq[0] += 1
    tag = tag or ('e%d' % _seq[0])
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'log_' + tag),
                     savedir=os.path.join(tmp, 'save_' + tag))
    srv._arm_cmd_timer = lambda: None
    srv._save_async = save_async
    if save_async:
        srv._ensure_save_writer()
    return srv


def disk_pets(path):
    with open(path, 'r', encoding='utf-8') as f:
        sv = json.load(f)
    return {int(e.get('pid')): e for e in (sv.get('pets') or [])}


def use_on_pet(srv, conn, iid, pid, flag=0):
    """走真实上行入口：C2S_ITEM_USE(34) 的 9 字节定向形态。"""
    srv._handle_item_use(conn, struct.pack('<IIB', iid, pid, flag))


class equip_table(object):
    """临时注入 PET_EQUIP_ITEMS（shape 真值不可得，只驱动机制；退出即还原）。"""

    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        for shape, (pos, name, lv) in self.entries.items():
            m.PET_EQUIP_ITEMS[shape] = (pos, name, lv)
        return m.PET_EQUIP_ITEMS

    def __exit__(self, *a):
        for shape in self.entries:
            m.PET_EQUIP_ITEMS.pop(shape, None)
        return False


# ─────────────────────────────────────────────────────────────────────────
def test_wire_format():
    print('── ① op54 线序对账（build ↔ 客户端 Unpack 语义）──')
    # 空装备：pid + 终止位，别的什么都没有
    raw = m.build_pet_equip(7, {})
    op, data = s2c_split(raw)
    check(op == m.OP_SUMMON_EQUIP, '帧信封：op54', op)
    check(data == struct.pack('<IB', 7, 0),
          '空装备 = pid(4) + pos=0 终止位', data.hex())
    pid, eqs = unpack_pet_equip(data)
    check(pid == 7 and eqs == [], '客户端视角：pid=7、零条装备', eqs)
    # 单件：pos|shape|len|desc|终止，中文走 GBK
    raw = m.build_pet_equip(7, {1: (EQ_COLLAR, '碧玉项圈')})
    op, data = s2c_split(raw)
    want = (struct.pack('<I', 7) + struct.pack('<BIB', 1, EQ_COLLAR,
                                               len('碧玉项圈'.encode('gbk')))
            + '碧玉项圈'.encode('gbk') + struct.pack('<B', 0))
    check(data == want, '单件线序 pid|pos|shape|len|desc|终止（中文 GBK）',
          data.hex())
    pid, eqs = unpack_pet_equip(data)
    check(eqs == [(1, EQ_COLLAR, '碧玉项圈')], '客户端解回一件，中文无损', eqs)
    # 多件按 pos 升序（dict 输入顺序无关）
    raw = m.build_pet_equip(9, {3: (EQ_AMULET, '丙'), 1: (EQ_COLLAR, '甲'),
                                2: (EQ_ARMOR, '乙')})
    _op, data = s2c_split(raw)
    _pid, eqs = unpack_pet_equip(data)
    check([e[0] for e in eqs] == [1, 2, 3], '按 pos 升序下发', eqs)
    # 越界 pos 构建端滤掉（客户端 DrawPetEquip 只会跳过，不发必然被丢的数据）
    raw = m.build_pet_equip(9, {0: (1, '零'), 4: (2, '四'), -1: (3, '负'),
                                99: (4, '九九'), 2: (EQ_ARMOR, '乙')})
    _op, data = s2c_split(raw)
    _pid, eqs = unpack_pet_equip(data)
    check(eqs == [(2, EQ_ARMOR, '乙')], '越界 pos 全滤掉，只留合法件', eqs)
    # list 形态同样接受
    raw = m.build_pet_equip(5, [(1, 11, '项'), (2, 22, '铠')])
    _op, data = s2c_split(raw)
    _pid, eqs = unpack_pet_equip(data)
    check(eqs == [(1, 11, '项'), (2, 22, '铠')], 'list 形态等价', eqs)
    # 存储形态 {pos: {'shape':…, 'desc':…}} 也必须认（GM/C2S 透传的就是它）
    raw = m.build_pet_equip(5, {1: {'shape': 11, 'desc': '项圈'}})
    _op, data = s2c_split(raw)
    _pid, eqs = unpack_pet_equip(data)
    check(eqs == [(1, 11, '项圈')], '存储形态（dict 值）等价', eqs)
    # desc 超长截到 255B（len 字段 1 字节封顶）→ payload ≥255B 走扩展帧
    raw = m.build_pet_equip(5, {1: (11, '甲' * 300)})
    check(raw[0] == 0xFF and struct.unpack('<H', raw[1:3])[0] == len(raw) - 3,
          'payload ≥255B → 扩展长度帧 [0xFF][2B u16 LE]（M1 §1.1〔A〕）',
          raw[:4].hex())
    op, data = s2c_split(raw)
    check(op == m.OP_SUMMON_EQUIP and data[9] == 255,
          'desc 超长 → len 字节=255', data[9])
    check(len(data) == 4 + 1 + 4 + 1 + 255 + 1,
          '截断后帧总长=266（desc 255 + 终止位，没粘包）', len(data))
    pid, eqs = unpack_pet_equip(data)
    check(pid == 5 and len(eqs) == 1 and len(eqs[0][2]) == 128,
          '解出 127 个整字 + 1 个半字替换符（不抛异常）', eqs[:1])
    # 不可 GBK 编码的字符不炸（replace 兜底）
    raw = m.build_pet_equip(5, {1: (11, '😀')})
    _op, data = s2c_split(raw)
    _pid, eqs = unpack_pet_equip(data)
    check(eqs and eqs[0][2] == '?', '非 GBK 字符 → ? 兜底', eqs)


def test_gm_entry(tmp):
    print('── ② GM 入口 gm_pet_equip ──')
    srv = make_server(tmp, tag='e2')
    srv._conn = FakeConn()                 # _gm_send 有连接才真发
    p = srv._add_pet(None, 2060, level=25)
    fc = srv._conn
    r = srv.gm_pet_equip(p['pid'], 1, EQ_COLLAR, '碧玉项圈')
    check(r.get('ok') is True, 'GM 装备 pos=1 成功', r)
    check(r['pos'] == 1 and r['shape'] == EQ_COLLAR
          and r['desc'] == '碧玉项圈' and r['replaced'] is False,
          '返回字段齐全（pos/shape/desc/replaced）', r)
    check(p['equips'] == {1: {'shape': EQ_COLLAR, 'desc': '碧玉项圈'}},
          '实例 equips 已更新', p['equips'])
    ops = [op for op, _b in fc.ops()]
    check(ops == [m.OP_SUMMON_EQUIP], 'GM 路径只发 54 一帧（其余面板自刷新）',
          ops)
    # 三个 pos 都能用；desc 缺省有兜底串
    for pos, shape in ((2, EQ_ARMOR), (3, EQ_AMULET)):
        r = srv.gm_pet_equip(p['pid'], pos, shape)
        check(r.get('ok') and r['pos'] == pos and r['desc'],
              'pos=%d 可装备，desc 缺省串 %r' % (pos, r.get('desc')), r)
    check(sorted(p['equips']) == [1, 2, 3], '三件齐（1/2/3）', sorted(p['equips']))
    # 坏 pos / 坏值 / 不存在的宠：一律拒绝且不脏状态
    for bad in (0, 4, -1):
        r = srv.gm_pet_equip(p['pid'], bad, EQ_COLLAR)
        check(r.get('ok') is False and r.get('reason') == 'bad_pos',
              'pos=%r → bad_pos' % (bad,), r)
    r = srv.gm_pet_equip(p['pid'], 'x', EQ_COLLAR)
    check(r.get('reason') == 'bad_value', 'pos 非整数 → bad_value', r)
    r = srv.gm_pet_equip(999999, 1, EQ_COLLAR)
    check(r.get('reason') == 'no_pet', '宠物不存在 → no_pet', r)
    check(sorted(p['equips']) == [1, 2, 3], '失败调用没脏掉装备表',
          sorted(p['equips']))
    n_before = len(fc.ops())
    # 替换语义：同 pos 再装备 = 换掉，不是叠加
    r = srv.gm_pet_equip(p['pid'], 1, 29998, '翡翠项圈')
    check(r.get('ok') and r.get('replaced') is True, '同 pos 再装备 = 替换', r)
    check(p['equips'][1] == {'shape': 29998, 'desc': '翡翠项圈'},
          '旧装备被替换（项圈 29999 → 29998）', p['equips'][1])
    check(len(p['equips']) == 3, '替换不增加条目数', p['equips'])
    check(len(fc.ops()) == n_before + 1, '替换也只发一帧 54',
          len(fc.ops()) - n_before)
    # 落盘：equips 以字符串 pos 为键
    d = disk_pets(srv.savepath)
    check(d[p['pid']]['equips'] == {
        '1': {'shape': 29998, 'desc': '翡翠项圈'},
        '2': {'shape': EQ_ARMOR, 'desc': '召唤兽装备(%d)' % EQ_ARMOR},
        '3': {'shape': EQ_AMULET, 'desc': '召唤兽装备(%d)' % EQ_AMULET}},
          '磁盘 equips 键为字符串 pos', d[p['pid']].get('equips'))


def test_c2s_equip(tmp):
    print('── ③ C2S 装备路径（9 字节上行 → _pet_equip）──')
    srv = make_server(tmp, tag='e3')
    with equip_table({EQ_COLLAR: (1, '碧玉项圈', EQ_LV)}):
        p = srv._add_pet(None, 2060, level=EQ_LV)
        iid = srv.gm_add_item(EQ_COLLAR, 1, '碧玉项圈')
        c = FakeConn()
        use_on_pet(srv, c, iid, p['pid'])
        ops = [op for op, _b in c.ops()]
        check(ops == [m.OP_SUMMON_EQUIP, m.OP_ITEM_DEL,
                      m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
              '帧序 54 → 道具扣除(34) → 48/49/53', ops)
        eq_frame = [b for op, b in c.ops() if op == m.OP_SUMMON_EQUIP][0]
        wpid, weqs = unpack_pet_equip(eq_frame)
        check(wpid == p['pid'] and weqs == [(1, EQ_COLLAR, '碧玉项圈')],
              '54 内容对账（pid/pos/shape/desc）', (wpid, weqs))
        check(p['equips'] == {1: {'shape': EQ_COLLAR, 'desc': '碧玉项圈'}},
              '实例已佩带', p['equips'])
        check(iid not in srv.items, 'qty=1 → 道具整件删除', list(srv.items))
        d = disk_pets(srv.savepath)
        check(d[p['pid']]['equips'].get('1', {}).get('shape') == EQ_COLLAR,
              '磁盘同步（落盘确认后才发的帧）', d[p['pid']].get('equips'))
        # 等级门：19 拒（回 48 三帧重同步，不扣道具），20 整过
        p2 = srv._add_pet(None, 2060, level=EQ_LV - 1)
        iid2 = srv.gm_add_item(EQ_COLLAR, 2, '碧玉项圈')
        c2 = FakeConn()
        use_on_pet(srv, c2, iid2, p2['pid'])
        ops2 = [op for op, _b in c2.ops()]
        check(m.OP_SUMMON_EQUIP not in ops2, '19 级 → 不下发 54', ops2)
        check(ops2 == [m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
              '拒绝也回 48/49/53（面板可能已本地预亮，防状态分叉）', ops2)
        check(srv.items[iid2]['qty'] == 2, '被拒 → 道具未扣除',
              srv.items.get(iid2))
        check(p2['equips'] == {}, '被拒 → 宠物没被装备', p2['equips'])
        p2['level'] = EQ_LV                   # 整 20 级：门内
        c2b = FakeConn()
        use_on_pet(srv, c2b, iid2, p2['pid'])
        check(m.OP_SUMMON_EQUIP in [op for op, _b in c2b.ops()],
              '20 级整 → 放行', [op for op, _b in c2b.ops()])
        check(p2['equips'].get(1, {}).get('shape') == EQ_COLLAR,
              '放行后实例正确', p2['equips'])
        # 非装备物品不回退：回血类照旧走回血，不出现 54
        p3 = srv._add_pet(None, 2060, level=5)
        p3['hp'] = 10
        iid3 = srv.gm_add_item(21101, 1, '小还丹')
        c3 = FakeConn()
        use_on_pet(srv, c3, iid3, p3['pid'])
        ops3 = [op for op, _b in c3.ops()]
        check(m.OP_SUMMON_EQUIP not in ops3, '回血类道具不触发装备帧', ops3)
        check(p3['hp'] == 210, '小还丹回血 200（10 → 210）', p3['hp'])
        check(iid3 not in srv.items, '回血道具照常扣除', list(srv.items))
        # 装备物品但没给目标：不猜该给谁装，拒用且不扣
        iid4 = srv.gm_add_item(EQ_COLLAR, 1, '碧玉项圈')
        c4 = FakeConn()
        use_on_pet(srv, c4, iid4, 0)
        check(not c4.ops(), '无目标 → 一帧不发（不猜该给谁装）')
        check(srv.items[iid4]['qty'] == 1, '无目标 → 道具不扣',
              srv.items.get(iid4))
        # 坏 iid / 宠物不在栏内：零帧
        c5 = FakeConn()
        use_on_pet(srv, c5, 999999, p['pid'])
        check(not c5.ops(), '道具不存在 → 零帧')
        c6 = FakeConn()
        r = srv._pet_equip(c6, iid, 999999)
        check(r is False and not c6.ops(), '宠物不在栏内 → False + 零帧')


def test_replace_semantics(tmp):
    print('── ④ 替换语义：qty=2 连用两次 ──')
    srv = make_server(tmp, tag='e4')
    with equip_table({EQ_COLLAR: (1, '碧玉项圈', EQ_LV)}):
        p = srv._add_pet(None, 2060, level=EQ_LV)
        iid = srv.gm_add_item(EQ_COLLAR, 2, '碧玉项圈')
        c = FakeConn()
        use_on_pet(srv, c, iid, p['pid'])
        ops = [op for op, _b in c.ops()]
        check(ops == [m.OP_SUMMON_EQUIP, m.OP_ITEM_QUANTITY,
                      m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
              '第一次：qty 2→1 发 35 数量帧', ops)
        check(srv.items[iid]['qty'] == 1, '剩余 1', srv.items.get(iid))
        use_on_pet(srv, c, iid, p['pid'])
        ops = [op for op, _b in c.ops()][5:]
        check(ops == [m.OP_SUMMON_EQUIP, m.OP_ITEM_DEL,
                      m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
              '第二次：qty 1→0 发 34 删除帧', ops)
        check(iid not in srv.items, '道具耗尽被删', list(srv.items))
        check(p['equips'] == {1: {'shape': EQ_COLLAR, 'desc': '碧玉项圈'}},
              '仍是 1 条装备（替换不叠加、旧的不留）', p['equips'])


def test_sync_rollback(tmp):
    print('── ⑤ 原子性·同步落盘失败 → 回滚 + 零帧 ──')
    srv = make_server(tmp, tag='e5')
    with equip_table({EQ_COLLAR: (1, '碧玉项圈', EQ_LV)}):
        p = srv._add_pet(None, 2060, level=EQ_LV)
        iid = srv.gm_add_item(EQ_COLLAR, 2, '碧玉项圈')
        srv._persist()                       # 先成功落一次，磁盘有基线
        with open(srv.savepath, 'rb') as f:
            disk0 = f.read()
        real_save = m.save_role
        m.save_role = lambda *a, **k: False   # 之后落盘必然失败
        try:
            c = FakeConn()
            use_on_pet(srv, c, iid, p['pid'])
            check(not c.ops(), '一帧都没发（54/道具/48 全无）')
            check(p['equips'] == {}, '装备回滚', p['equips'])
            check(srv.items[iid]['qty'] == 2, '道具数量还原',
                  srv.items.get(iid))
            with open(srv.savepath, 'rb') as f:
                check(f.read() == disk0, '磁盘一个字节都没动')
        finally:
            m.save_role = real_save
        # 恢复后能正常装备（回滚没留下后遗症）
        c = FakeConn()
        use_on_pet(srv, c, iid, p['pid'])
        check([op for op, _b in c.ops()] ==
              [m.OP_SUMMON_EQUIP, m.OP_ITEM_QUANTITY, m.OP_SUMMON_INFO,
               m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
              '恢复落盘后正常交付', [op for op, _b in c.ops()])


def test_async_rollback(tmp):
    print('── ⑥ 原子性·异步落盘失败 → 回滚 + 零帧 ──')
    srv = make_server(tmp, save_async=True, tag='e6')
    with equip_table({EQ_COLLAR: (1, '碧玉项圈', EQ_LV)}):
        p = srv._add_pet(None, 2060, level=EQ_LV)
        iid = srv.gm_add_item(EQ_COLLAR, 2, '碧玉项圈')
        srv._persist()
        srv.flush_saves(5.0)                  # 等基线真正写完
        with open(srv.savepath, 'rb') as f:
            disk0 = f.read()
        real_save = m.save_role
        m.save_role = lambda *a, **k: False
        try:
            c = FakeConn()
            use_on_pet(srv, c, iid, p['pid'])
            check(not c.ops(), '异步失败同样零帧')
            check(p['equips'] == {}, '装备回滚', p['equips'])
            check(srv.items[iid]['qty'] == 2, '道具数量还原',
                  srv.items.get(iid))
            with open(srv.savepath, 'rb') as f:
                check(f.read() == disk0, '磁盘未变')
            check(srv._save_last_ok is False,
                  '_save_last_ok=False（异步确认通道如实上报）',
                  srv._save_last_ok)
        finally:
            m.save_role = real_save
        c = FakeConn()
        use_on_pet(srv, c, iid, p['pid'])
        check(len(c.ops()) == 5, '恢复后异步交付正常', len(c.ops()))
        srv.stop()


def test_save_roundtrip(tmp):
    print('── ⑦ 存档往返：equips 持久化 + 重登补发 54 ──')
    srv = make_server(tmp, tag='e7')
    p = srv._add_pet(None, 2060, level=30)
    srv.gm_pet_equip(p['pid'], 1, EQ_COLLAR, '碧玉项圈')
    srv.gm_pet_equip(p['pid'], 2, EQ_ARMOR, '玄铁铠甲')
    srv.gm_pet_equip(p['pid'], 3, EQ_AMULET, '幽魂护符')
    srv._persist()
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_e7b'),
                      savedir=os.path.dirname(srv.savepath))
    q = srv2.pets.get(p['pid'])
    check(q is not None, '重登后宠物还在', q and q['pid'])
    check(q['equips'] == {1: {'shape': EQ_COLLAR, 'desc': '碧玉项圈'},
                          2: {'shape': EQ_ARMOR, 'desc': '玄铁铠甲'},
                          3: {'shape': EQ_AMULET, 'desc': '幽魂护符'}},
          '读档 equips 逐字段还原', q['equips'])
    c = FakeConn()
    srv2._send_all_pets(c)
    frames = [b for op, b in c.ops() if op == m.OP_SUMMON_EQUIP]
    check(len(frames) == 1, '重登补发 1 帧 54（只有带装备的那只）', len(frames))
    wpid, weqs = unpack_pet_equip(frames[0])
    check(wpid == p['pid'] and [e[0] for e in weqs] == [1, 2, 3]
          and weqs[0][2] == '碧玉项圈',
          '重登 54 内容对账', (wpid, weqs))
    check(m.OP_SUMMON_EQUIP in [op for op, _b in c.ops()]
          and sum(1 for op, _b in c.ops() if op == m.OP_SUMMON_EQUIP) == 1,
          '54 恰好一帧（无装备宠物不补发）',
          sum(1 for op, _b in c.ops() if op == m.OP_SUMMON_EQUIP))


def test_load_cleaning(tmp):
    print('── ⑧ 读档清洗：坏 equips 一律丢弃 ──')
    srv = make_server(tmp, tag='e8')
    p = srv._add_pet(None, 2060, level=EQ_LV)
    srv._persist()
    sub = os.path.join(tmp, 'save_eq')
    shutil.copytree(os.path.dirname(srv.savepath), sub)
    sv = json.load(open(os.path.join(sub, 'role.json'), encoding='utf-8'))
    entry = [e for e in sv['pets'] if int(e['pid']) == p['pid']][0]
    entry['equips'] = {
        '1': {'shape': EQ_COLLAR, 'desc': '碧玉项圈'},   # 合法 → 留
        '2': {'shape': EQ_ARMOR, 'desc': 12345},         # desc 非串 → 强转留
        '3': {'shape': 0, 'desc': '零造型'},              # shape=0 → 丢
        '9': {'shape': 30009, 'desc': '越界位'},          # pos 9 → 丢
        '4': '不是字典',                                  # 非字典值 → 丢
        '5': None,                                        # None → 丢
        'x': {'shape': 30007, 'desc': '键坏'},            # 键非整数 → 丢
    }
    with open(os.path.join(sub, 'role.json'), 'w', encoding='utf-8') as f:
        json.dump(sv, f, ensure_ascii=False)
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_e8b'), savedir=sub)
    q = srv2.pets.get(p['pid'])
    check(q['equips'] == {1: {'shape': EQ_COLLAR, 'desc': '碧玉项圈'},
                          2: {'shape': EQ_ARMOR, 'desc': '12345'}},
          '只留合法件；desc 强制转字符串', q['equips'])


def test_console_http(tmp):
    print('── ⑨ 三入口同源：控制台 / HTTP / gm_pet_equip ──')
    srv = make_server(tmp, tag='e9')
    p = srv._add_pet(None, 2060, level=EQ_LV)
    # 控制台
    r = srv.gm_command('pet equip %d 1 %d 碧玉项圈' % (p['pid'], EQ_COLLAR))
    check(r.get('ok') and r.get('cmd') == 'pet equip', '控制台装备成功',
          r.get('msg'))
    check(r['result']['desc'] == '碧玉项圈' and '项圈' in r['msg'],
          'msg 带部位、result 带名称', r['msg'])
    check(p['equips'].get(1, {}).get('shape') == EQ_COLLAR, '实例已更新',
          p['equips'])
    # 旧行为不退化：pet <n> 仍是设宠物 HP
    r = srv.gm_command('pet 50')
    check(r.get('ok') and r['result'].get('pet_hp') == 50,
          'pet 50 仍是设宠物 HP（子指令分流不回退）', r)
    # 坏 pos / 无此宠 / 参数不足：如实拒绝
    r = srv.gm_command('pet equip %d 9 %d' % (p['pid'], EQ_COLLAR))
    check(not r.get('ok') and r['result'].get('reason') == 'bad_pos',
          '控制台坏 pos 如实上报', r)
    r = srv.gm_command('pet equip 999999 1 %d' % EQ_COLLAR)
    check(not r.get('ok') and r['result'].get('reason') == 'no_pet',
          '控制台无此宠如实上报', r)
    r = srv.gm_command('pet equip %d 1' % p['pid'])
    check(not r.get('ok') and r.get('error'), '参数不足 → 拒绝', r)
    # HTTP（POST api/pet/equip，与面板同源）
    srv.log = lambda *a, **k: None
    srv.serve_background(0, web=True, web_port=0)
    try:
        time.sleep(0.5)
        url = 'http://127.0.0.1:%d/api/pet/equip' % srv._web_port
        req = urllib.request.Request(
            url, data=json.dumps({'pid': p['pid'], 'pos': 2,
                                  'shape': EQ_ARMOR,
                                  'desc': '玄铁铠甲'}).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        d = json.loads(urllib.request.urlopen(req, timeout=5).read())
        check(d.get('ok') and d['result']['pos'] == 2, 'HTTP 装备成功', d)
        check(p['equips'].get(2, {}).get('shape') == EQ_ARMOR,
              'HTTP 与实例同源（同一 gm_pet_equip）', p['equips'])
        req2 = urllib.request.Request(
            url, data=json.dumps({'pid': p['pid'], 'pos': 9,
                                  'shape': 1}).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req2, timeout=5)
            check(False, '坏 pos 应返回 400')
        except urllib.error.HTTPError as e:
            check(e.code == 400, '坏 pos → HTTP 400', e.code)
            d2 = json.loads(e.read())
            check(d2.get('reason') == 'bad_pos', '400 body 带 reason', d2)
    finally:
        srv.stop()


def main():
    tmp = tempfile.mkdtemp(prefix='m4equip_')
    try:
        test_wire_format()
        test_gm_entry(tmp)
        test_c2s_equip(tmp)
        test_replace_semantics(tmp)
        test_sync_rollback(tmp)
        test_async_rollback(tmp)
        test_save_roundtrip(tmp)
        test_load_cleaning(tmp)
        test_console_http(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = ok is not True
    print('\n=== 宠物装备闭环 %s ===' % ('FAIL' if bad else 'PASS'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
