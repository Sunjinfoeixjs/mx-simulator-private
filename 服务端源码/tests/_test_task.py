"""任务闭环冒烟：S2CTaskRemove(73) 帧格式 + 接/计数/交任务逻辑 + 奖励与持久化（M6）。

沿用 _test_shop.py 的方式：直接调用 M4Server 方法与帧构造器，用 FakeConn 捕获下发字节，
不依赖真实客户端/端口。存档与日志写入临时目录，避免污染真实存档。
"""
import os, shutil, struct, sys, tempfile
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
import m4_server as m

ok = True
def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c

class FakeConn:
    def __init__(self): self.sent = []
    def sendall(self, b): self.sent.append(b)

def ops(conn): return [f[1] for f in conn.sent]

# ── 1) 帧格式 ─────────────────────────────────────────────────────────
fr = m.build_task_remove(m.TASK_NEWBIE_ID)
check(fr[1] == m.OP_TASK_REMOVE == 73, 'S2CTaskRemove opcode=73 (got %d)' % fr[1])
check(fr[2:4] == struct.pack('<H', 1), 'TaskRemove body = tid(2B)')

pc = m.build_prop_change(m.PROP_IDX_EXP, 700, 4)
check(pc[1] == m.OP_PROP_CHANGE, 'PropChange opcode=17')
check(pc[2] == 8 and struct.unpack('<I', pc[3:7])[0] == 700, 'PropChange(m_Exp=8, 700, 4B)')
pcg = m.build_prop_change(m.PROP_IDX_GOLD, 50500, 4)
check(pcg[2] == 43 and struct.unpack('<I', pcg[3:7])[0] == 50500, 'PropChange(m_Gold=43, 4B)')

# ── 2) 逻辑：接任务 → 击杀计数 → 达成 → 交任务 ────────────────────────
tmp = tempfile.mkdtemp(prefix='m4task_')
s = m.M4Server('127.0.0.1', 3167, 0x1234, 5, enemies=6, turn_timeout=30,
               logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
s._loaded = None            # 忽略可能存在的存档，保证从零开始
s.tasks = {}
conn = FakeConn()

check(s._task_state() == m.TASK_STATE_NONE, '初始无任务')
check(s._npc_welcome_msg() == m.NPC_CHAT_WELCOME, '无任务时首页 = 默认欢迎页')

# 未接任务时点「开始打」不应计数
s._on_enemy_dead(conn)
check(s._task_state() == m.TASK_STATE_NONE, '未接任务时击杀不计数')

# 选项 1 → 接任务
s._npc_task_choice(conn)
check(s._task_state() == m.TASK_STATE_DOING, '接任务后 state=DOING')
check(s.tasks[m.TASK_NEWBIE_ID]['progress'] == 0, '接任务后进度 0')
check(m.OP_TASK_ADD in ops(conn), '接任务下发 S2CTaskAdd(72)')
check('我还在打' in s._npc_welcome_msg(), '进行中首页含进度选项')

# 击杀 3 只 → 达成
for _ in range(m.TASK_NEWBIE_NEED):
    s._on_enemy_dead(conn)
check(s.tasks[m.TASK_NEWBIE_ID]['state'] == m.TASK_STATE_DONE, '3 击杀后 state=DONE')
check(s._task_desc() == '击败毛草团（3/3）', '任务描述带进度 (got %s)' % s._task_desc())
check('交任务' in s._npc_welcome_msg(), '达成后首页含「交任务」')

# 达成后再击杀：不越界、不重复完成
s._on_enemy_dead(conn)
check(s.tasks[m.TASK_NEWBIE_ID]['progress'] == m.TASK_NEWBIE_NEED, '超额击杀不加进度')

# 交任务 → TaskRemove + 奖励
coins0, exp0 = s.coins, s.exp
items0 = len(s.items)
conn2 = FakeConn()
s._npc_task_choice(conn2)
check(s._task_state() == m.TASK_STATE_NONE, '交任务后任务被移除')
check(m.OP_TASK_REMOVE in ops(conn2), '交任务下发 S2CTaskRemove(73)')
check(s.coins == coins0 + m.TASK_REWARD_COINS, '金钱 +%d (%d→%d)' % (m.TASK_REWARD_COINS, coins0, s.coins))
check(s.exp == exp0 + m.TASK_REWARD_EXP, '经验 +%d (%d→%d)' % (m.TASK_REWARD_EXP, exp0, s.exp))
check(len(s.items) == items0 + 1, '奖励道具入背包 (got %d)' % (len(s.items) - items0))
check(ops(conn2).count(m.OP_ITEM_ADD) == 1, '下发 1 次 ItemAdd(33)')
check(m.OP_PROP_CHANGE in ops(conn2), '下发 PropChange 同步金钱/经验')

# ── 3) 持久化：tasks / exp 落盘可回读 ────────────────────────────────
s.tasks = {m.TASK_NEWBIE_ID: {'state': m.TASK_STATE_DOING, 'progress': 2}}
s.exp = 1234
s._persist()
sv = m.load_role(s.savepath)
check(sv is not None and sv.get('tasks', {}).get(m.TASK_NEWBIE_ID, {}).get('progress') == 2,
      '存档含任务进度（键已还原为 int）')
check(sv.get('exp') == 1234, '存档含经验 (got %s)' % sv.get('exp'))

s2 = m.M4Server('127.0.0.1', 3168, 0x1234, 5,
                logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
check(s2.tasks.get(m.TASK_NEWBIE_ID, {}).get('progress') == 2, '重载服务端恢复任务进度')
check(s2.exp == 1234, '重载服务端恢复经验')

shutil.rmtree(tmp, ignore_errors=True)
print('\n=== 任务闭环冒烟 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
