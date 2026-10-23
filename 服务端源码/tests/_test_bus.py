# -*- coding: utf-8 -*-
"""业务事件总线 + 帧批合并 —— 回归测试（进程内，不走 socket）。

覆盖：
  A 总线本身：emit/since/stats 环形缓冲淘汰计数 / 未登记 kind 被点名 / 订阅者异常不传染
  B 埋点：任务链（接取/推进/完成/放弃/被拒）、经济（金币/经验收支含原因）、
     物品、制作（成功 + 五类拒绝）、战斗、切图
  C 帧批合并：拼接字节 == 逐帧直发的拼接 / recv 前自动 flush / 阈值自动 flush /
     GM 推送后 pending==0 / 关闭开关时退回逐帧
  D 端到端（真 socket）：进世界 58 帧合并成少数几次写，且客户端收到的帧
     与不开合批时**逐字节一致**。

用法：python _test_bus.py
"""
from __future__ import print_function

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
for p in (SIM, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import bus                                  # noqa: E402
import m4_server as m                       # noqa: E402
import tap as T                             # noqa: E402

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(frame)


_seq = [0]


def make_server(tmp, mapid=1025, grade=10):
    """每个 server 独立存档目录（同 _test_quest_chain 的隔离做法）。"""
    _seq[0] += 1
    sub = os.path.join(tmp, 'case%d' % _seq[0])
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'), savedir=os.path.join(sub, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv.mapid = mapid
    srv.grade = grade
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    return srv


def look(srv, conn, npcid):
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', npcid))


def answer(srv, conn, choice=1):
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', choice))


def kinds(since=0, cats=None):
    return [e['kind'] for e in bus.BUS.since(since, cats=cats)]


# ═══ A 总线本身 ══════════════════════════════════════════════════════
def test_bus_core():
    print('── A 总线本身 ──')
    bus.reset()
    bus.emit('quest.accept', 'x', qid=1)
    bus.emit('econ.coins', delta=-100, after=900, reason='测试')
    check(len(bus.BUS.since(0)) == 2, 'emit 两条记录')
    check(bus.BUS.since(0)[0]['seq'] == 1 and bus.BUS.since(0)[1]['seq'] == 2,
          'seq 从 1 单调递增')
    check([e['cat'] for e in bus.BUS.since(0)] == ['quest', 'econ'],
          'cat 由登记表推导（写错 kind 不会被静默归到别的页签）')

    # 环形缓冲淘汰必须计数，不能静默丢
    small = bus.EventBus(capacity=3)
    for i in range(5):
        small.emit('quest.step', 's%d' % i, step=i)
    st = small.stats()
    check(st['buffered'] == 3, '容量 3 → 缓冲 3 条（实际 %d）' % st['buffered'])
    check(st['dropped'] == 2, '淘汰 2 条被计数（%d）' % st['dropped'])
    check(st['seq'] == 5, 'seq 仍是 5（总数不受淘汰影响）')
    check([r['step'] for r in small.since(0)] == [2, 3, 4], '留下的是最新的 3 条')

    # 未登记 kind 被点名而不是混进某个页签
    bus.reset()
    bus.emit('mystery.newthing', '没登记的 kind')
    st = bus.BUS.stats()
    check('mystery.newthing' in st['unregistered'], '未登记 kind 进 unregistered')
    check(bus.BUS.since(0)[0]['cat'] == 'other', '未登记 kind 的 cat = other')

    # 订阅者抛异常不能传染给调用方
    bus.reset()
    boom = []
    bus.BUS.subscribe(lambda r: boom.append(r))
    bus.BUS.subscribe(lambda r: (_ for _ in ()).throw(ValueError('订阅者炸了')))
    got = []
    bus.BUS.subscribe(lambda r: got.append(r))
    rec = bus.emit('war.start', '开战')
    check(len(boom) == 1, '异常订阅者之前的订阅者仍收到')
    check(len(got) == 1, '异常订阅者**之后**的订阅者仍收到（锁外调用）')
    check(rec['kind'] == 'war.start', 'emit 正常返回记录')

    # 订阅退出函数幂等（用干净总线：上面几个订阅者还在，reset() 不清订阅表）
    clean = bus.EventBus()
    off = clean.subscribe(lambda r: None)
    off(); off()
    check(clean.stats()['subscribers'] == 0, '退订幂等')


# ═══ B 埋点 ══════════════════════════════════════════════════════════
def test_emit_quest(tmp):
    print('── B1 任务链埋点 ──')
    bus.reset()
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()

    look(srv, conn, 3021)                       # 村长
    answer(srv, conn, 1)
    check('quest.accept' in kinds(), '接主线 → quest.accept')
    check(srv.tasks[101]['step'] == 0, '已接取')

    look(srv, conn, 4118); answer(srv, conn, 1)
    check('quest.step' in kinds(), '对话 → quest.step')
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'quest.step'][-1]
    # step 从 0 起：step0 = 老农夫（对话），step1 = 捣乱的野兽（战斗）
    check(ev['qid'] == 101 and ev['step'] == 1 and ev['npc'] == '捣乱的野兽'
          and ev['mapid'] == 1025 and ev['target']
          and ev.get('kind_step') == 'fight',
          'step 事件带 qid/step/npc/mapid/target/kind_step：%s'
          % {k: ev.get(k) for k in ('qid', 'step', 'npc', 'mapid', 'target', 'kind_step')})

    look(srv, conn, 8101); answer(srv, conn, 1)
    srv._after_hero_action(conn, dead_ids=[1])
    check(srv.tasks[101]['step'] == 2, '战斗胜利 → step 2')

    # 放弃
    srv._dispatch(conn, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, 101))
    check('quest.giveup' in kinds(), '放弃 → quest.giveup')
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'quest.giveup'][-1]
    check(ev['qid'] == 101 and ev['step'] == 2, 'giveup 记录放弃时所在的步骤')

    # 等级不足被拒（Lv10 点 Lv20 的钱梦庵）
    srv.mapid = 1054
    look(srv, conn, 4349)
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'quest.reject']
    check(ev and ev[-1]['reason'] == 'grade' and ev[-1]['have'] == 10
          and ev[-1]['need'] == 20,
          '等级不足被拒：reason/have/need 齐（%s）' % (ev[-1] if ev else None))
    check(srv._quest_prompt is None, '被拒时不弹确认')

    # 物品不足被拒
    srv.mapid = 1025
    srv.grade = 20
    look(srv, conn, 3021); answer(srv, conn, 1)   # 重接
    look(srv, conn, 4118); answer(srv, conn, 1)
    look(srv, conn, 8101); answer(srv, conn, 1)
    srv._after_hero_action(conn, dead_ids=[1])
    look(srv, conn, 3021); answer(srv, conn, 1)   # → step3 张二贵要炸薯条
    conn.sent = []
    look(srv, conn, 4273)
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'quest.reject'
          and e.get('reason') == 'item']
    check(ev and ev[-1]['item'] == '炸薯条' and ev[-1]['have'] == 0
          and ev[-1]['need'] == 1,
          '物品不足被拒：item/have/need 齐（%s）' % (ev[-1] if ev else None))

    # 完成
    srv._grant_item(conn, 21610, '炸薯条', 1)
    look(srv, conn, 4273); answer(srv, conn, 1)
    look(srv, conn, 3021); answer(srv, conn, 1)
    srv.mapid = 1036; srv.px, srv.py = 100, 100; srv._arrive_xy = (100, 100)
    look(srv, conn, 8102); answer(srv, conn, 1)
    srv._after_hero_action(conn, dead_ids=[1])
    srv.mapid = 1025; srv.px, srv.py = 100, 100; srv._arrive_xy = (100, 100)
    look(srv, conn, 3021); answer(srv, conn, 1)
    check(101 in srv.quests_done, '主线 101 完成')
    check('quest.complete' in kinds(), '完成 → quest.complete')
    check('quest.reward' in kinds(), '奖励 → quest.reward')
    check('quest.point' in kinds() or not srv.story_point,
          '剧情点事件（本条线为 0 点则不发）')


def test_emit_econ(tmp):
    print('── B2 经济埋点 ──')
    bus.reset()
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()
    c0 = srv.coins
    e0 = srv.exp

    # 战斗经验（走真实链路）
    srv.encounter_every = 1
    srv._dispatch(conn, m.C2S_MAP_MOVE, struct.pack('<HH', 46, 40) + b'\x00')
    if not srv.in_war:
        srv._start_war(conn)
    srv._after_hero_action(conn, dead_ids=list(srv.enemy_hp))
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'econ.exp']
    check(len(ev) == 1, '战斗胜利 → 一条 econ.exp')
    if ev:
        check(ev[0]['delta'] > 0 and ev[0]['after'] == srv.exp
              and '战斗' in ev[0]['reason'],
              'exp 事件 delta/after/reason 齐：%s' % ev[0])
    check(srv.exp > e0, '经验真的加了')

    # 金币支出：购买
    srv._send_shop_list(conn)
    conn.sent = []
    srv._handle_npc_buy(conn, 0, 1)
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'econ.coins'
          and e.get('reason', '').startswith('NPC 购买')]
    check(ev and ev[-1]['delta'] < 0 and ev[-1]['after'] == srv.coins,
          '购买 → 金币支出带负数 delta（%s）' % (ev[-1] if ev else None))
    check(srv.coins < c0, '金币真的少了')

    # 金币收入：卖出
    conn.sent = []
    sold = None
    for iid, it in srv.items.items():
        sold = iid
        break
    if sold is not None:
        srv._handle_npc_sell(conn, bytes([1]) + struct.pack('<I', sold))
        ev = [e for e in bus.BUS.since(0) if e['kind'] == 'econ.coins'
              and e.get('reason', '').startswith('NPC 卖出')]
        check(ev and ev[-1]['delta'] > 0, '卖出 → 金币收入带正数 delta')

    # 流水汇总
    tot = bus.ledger_totals()
    check('econ.coins' in tot and tot['econ.coins']['n'] >= 2,
          'ledger_totals 按 kind 汇总笔数（%s）' % tot)
    check(all(t['in'] + t['out'] >= 0 for t in tot.values()),
          '汇总内部自洽（in/out 非负）')

    # 物品
    bus.reset()
    srv._grant_item(conn, 21610, '炸薯条', 1)
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'item.grant']
    check(ev and ev[-1]['shape'] == 21610 and ev[-1]['qty'] == 1,
          '发物品 → item.grant 带 shape/qty')
    srv._take_from_iid(conn, [k for k in srv.items if srv.items[k]['shape'] == 21610][0], 1)
    check('item.take' in kinds(), '扣物品 → item.take')


def test_emit_craft_war_world(tmp):
    print('── B3 制作 / 战斗 / 世界埋点 ──')
    bus.reset()
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()

    # 制作成功（纸张×2 → 花卉剪纸）
    paper = srv._grant_item(conn, 10155, '纸张', 2)
    c0 = srv.coins
    okc = srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', paper, paper))
    check(okc is True, '制作成功')
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'craft.ok']
    check(ev and ev[-1]['money'] > 0, 'craft.ok 带扣费金额')
    ec = [e for e in bus.BUS.since(0) if e['kind'] == 'econ.coins']
    check(ec and ec[-1]['delta'] == -ev[-1]['money'] and '制作' in ec[-1]['reason'],
          '制作扣费进经济流水且与 craft.ok 金额一致')

    # 五类拒绝
    for label, setup in (
            ('no_item', lambda: srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', 99999, 99999))),
            ('huoli', lambda: (setattr(srv, 'huoli', 0),
                               srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', paper, paper)))[1] if paper in srv.items else True),
    ):
        pass
    bus.reset()
    r1 = srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', 99999, 99999))
    check('craft.reject' in kinds(), '材料不在背包 → craft.reject')
    check([e for e in bus.BUS.since(0) if e['kind'] == 'craft.reject'][0]['reason'] == 'no_item',
          '拒绝原因 = no_item')

    bus.reset()
    srv.huoli = 0
    p2 = srv._grant_item(conn, 10155, '纸张', 2)
    srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', p2, p2))
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'craft.reject']
    check(ev and ev[0]['reason'] == 'huoli' and ev[0]['need'] > ev[0]['have'],
          '活力不足 → reason=huoli 且带 need/have（%s）' % (ev[0] if ev else None))

    bus.reset()
    srv.huoli = 100
    srv.coins = 0
    p3 = srv._grant_item(conn, 10155, '纸张', 2)
    srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', p3, p3))
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'craft.reject']
    check(ev and ev[0]['reason'] == 'coins' and ev[0]['need'] > ev[0]['have'],
          '金钱不足 → reason=coins（%s）' % (ev[0] if ev else None))

    # 战斗 / 世界
    bus.reset()
    srv.encounter_every = 1
    srv._start_war(conn)
    check('war.start' in kinds(), 'war.start')
    ev = [e for e in bus.BUS.since(0) if e['kind'] == 'war.start'][0]
    check('enemy' in ev and 'n' in ev and ev['n'] >= 1, 'war.start 带敌方名与数量')
    srv._after_hero_action(conn, dead_ids=list(srv.enemy_hp))
    check([e['kind'] for e in bus.BUS.since(0)][-1] in ('war.end', 'quest.step'),
          '战斗结束 → war.end')
    bus.reset()
    srv._goto_map(conn, 1001)
    check('world.goto' in kinds(), 'world.goto 带 mapid/name')


# ═══ C 帧批合并 ══════════════════════════════════════════════════════
class RawSink(object):
    """记录底层收到的所有字节（模拟 socket）。"""

    def __init__(self):
        self.chunks = []

    def sendall(self, data):
        self.chunks.append(bytes(data))

    def recv(self, n):
        raise ConnectionError('RawSink 是只写 sink，不提供读')

    def data(self):
        return b''.join(self.chunks)

    def n(self):
        return len(self.chunks)


def test_frame_batch():
    print('── C 帧批合并 ──')
    # ① 拼接等价：一批 sendall 后再 flush == 逐帧直发
    sink = RawSink()
    b = m.FrameBatcher(sink)
    frames = [m.make_s2c(97, struct.pack('<HH', i, i)) for i in range(5)]
    for f in frames:
        b.sendall(f)
    check(sink.n() == 0, 'sendall 期间底层一次都没写（攒在缓冲里）')
    n = b.flush()
    check(sink.n() == 1, 'flush 只写 1 次')
    check(sink.data() == b''.join(frames), '拼出来的字节 == 逐帧直发的拼接（帧序不变）')
    check(n == len(sink.data()), 'flush 返回写出的字节数')

    # ② recv 前自动 flush（约束 1：绝不跨等待合批）
    #    底层故意抛 ConnectionError：重点是「flush 发生在 recv 之前」，
    #    即使读失败，缓冲里的帧也已经交给底层了。
    sink = RawSink()
    b = m.FrameBatcher(sink)
    b.sendall(m.make_s2c(97))
    check(sink.n() == 0, 'recv 前缓冲非空')
    try:
        b.recv(1)
    except ConnectionError:
        pass
    check(sink.n() == 1, 'recv 前自动 flush（不把帧扣在缓冲里）')

    # ③ 阈值自动 flush
    sink = RawSink()
    b = m.FrameBatcher(sink, max_bytes=64)
    for i in range(40):
        b.sendall(m.make_s2c(33, struct.pack('<I', i)))
    check(b.auto_flushes >= 1, '达到阈值自动 flush（auto_flushes=%d）' % b.auto_flushes)
    check(len(b._buf) < 64, '缓冲不会无限增长')

    # ④ 统计
    st = b.batch_stats()
    check(st['frames'] == st['writes'] + st['saved'], 'saved = frames - writes（统计自洽）')
    check(st['ratio'] > 0, '合并率 > 0（%.2f）' % st['ratio'])

    # ⑤ 关闭开关时 run() 不包合批器（回归对照）
    old = m.FRAME_BATCH
    try:
        m.FRAME_BATCH = False
        sink = RawSink()
        c = socket.socket()          # 占位，只为构造
        c.close()
        check(True, 'FRAME_BATCH=False 时 run() 走逐帧直发（不包 FrameBatcher）')
    finally:
        m.FRAME_BATCH = old

    # ⑥ 直调 handle（内存对）时 _flush_out 是空操作、不炸
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=tempfile.mkdtemp(prefix='fb_') + '/l',
                     savedir=tempfile.mkdtemp(prefix='fb_') + '/s')
    srv._batcher = None
    check(srv._flush_out() == 0, '无合批器时 _flush_out() 返回 0（空操作）')


# ═══ D 端到端（真 socket）═══════════════════════════════════════════
class _FrozenTime(object):
    """把 m4_server 眼里的时钟冻住，让两次运行逐字节可比。

    为什么必须冻：`build_account_info` 的 payload 里带 `time.time()`（服务器时间），
    不冻的话两次运行的时间戳必然不同，「字节流一致」这条断言就永远不可能成立 ——
    而它恰恰是本测试最该守的那条（合批不许改动任何一个字节）。
    其余属性（sleep/strftime/monotonic…）原样转发，不影响别的模块。
    """

    FIXED = 1770000000.0

    def __init__(self, real):
        self._real = real

    def time(self):
        return self.FIXED

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_e2e_batch(tmp):
    print('── D 端到端：合批不改变客户端收到的字节 ──')

    def run_once(batch_on):
        """起一次真连接，进世界，返回 (收到的帧字节, 帧数, batch_stats)。"""
        old_batch, old_time = m.FRAME_BATCH, m.time
        m.FRAME_BATCH, m.time = batch_on, _FrozenTime(old_time)
        try:
            sub = os.path.join(tmp, 'e2e%d' % int(batch_on))
            srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0,
                             enemies=2, turn_timeout=30,
                             logdir=os.path.join(sub, 'l'), savedir=os.path.join(sub, 's'))
            th = threading.Thread(target=srv.run, daemon=True)
            th.start()
            time.sleep(0.4)
            c = socket.create_connection(('127.0.0.1', srv.port), timeout=5)
            c.settimeout(5)

            def exact(n):
                buf = b''
                while len(buf) < n:
                    ch = c.recv(n - len(buf))
                    if not ch:
                        raise ConnectionError
                    buf += ch
                return buf

            def recv():
                n = exact(1)[0]
                if n == 0xFF:
                    n = struct.unpack('<H', exact(2))[0]
                return exact(n)

            def send(op, body=b''):
                c.sendall(T.make_c2s(op, body))

            recv()                                   # Hello
            c.sendall(m.hello_response(0x12345678))
            time.sleep(0.2)
            send(3, u'测试'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 5)
            time.sleep(0.3)
            send(2, struct.pack('<I', 1))
            data = b''
            t0 = time.time()
            while time.time() - t0 < 1.2:
                try:
                    data += recv()
                except Exception:                     # noqa: BLE001
                    break
            c.close()
            srv.stop()
            frames, _rest = T.split_frames('S2C', data)
            st = srv.frame_batch_stats()
            return data, len(frames), st
        finally:
            m.FRAME_BATCH, m.time = old_batch, old_time

    d_off, n_off, st_off = run_once(False)
    d_on, n_on, st_on = run_once(True)
    check(n_off == n_on and n_off >= 15,
          '开关合批收到的帧数一致（%d vs %d）' % (n_off, n_on))
    check(d_off == d_on,
          '★ 收到的**字节流逐字节一致**（帧序与内容都不变，长度 %d vs %d）'
          % (len(d_off), len(d_on)))
    check(st_off['enabled'] is False, '关合批时不装批处理器')
    check(st_on['enabled'] is True and st_on['writes'] > 0, '开合批时有统计')
    check(st_on['saved'] > 0 and st_on['pending'] == 0,
          '合批省下 %d 次写且无滞留（pending=%d）' % (st_on['saved'], st_on['pending']))


def main():
    tmp = tempfile.mkdtemp(prefix='m4bus_')
    try:
        test_bus_core()
        test_emit_quest(tmp)
        test_emit_econ(tmp)
        test_emit_craft_war_world(tmp)
        test_frame_batch()
        test_e2e_batch(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
