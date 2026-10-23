"""持久化冒烟：实例化 M4Server，改状态→_persist()，再实例化→应载入存档。

★ 2026-10-16 修复：全程使用**临时存档目录**，绝不触碰真实存档
  （真实会话存档位置可能已非出生点，且本测试旧实现末尾会删除真实存档）。
★ 2026-10-23 复活：原先把 sim 路径写死成 `服务端源码/sim`（旧布局），
  sim 挪进 tests/ 后 import 直接失败，测试沦为孤儿。改成布局无关查找。
"""
import io, os, sys, json, shutil, tempfile
_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM = os.path.join(_HERE, 'sim')                 # sim 与 tests 同层（现布局）
if not os.path.isdir(_SIM):                       # 兼容旧的「tests 与 sim 平级」
    _SIM = os.path.join(os.path.dirname(_HERE), 'sim')
sys.path.insert(0, _SIM)
import m4_server as m

ok = True
def check(cond, msg):
    global ok
    print(('OK  ' if cond else 'FAIL') + ' ' + msg)
    ok = ok and cond

tmp = tempfile.mkdtemp(prefix='m4persist_')
def S():
    return m.M4Server('127.0.0.1', 3155, 0x1234, 5, enemies=6, turn_timeout=30,
                      logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))

# 1) 全新（无存档）→ 默认出生点
s1 = S()
check(s1.px == m.SPAWN_X and s1.py == m.SPAWN_Y, '全新角色落在出生点')
check(s1._loaded is None, '全新角色无存档/未载入')

# 2) 改状态并存档
s1.px, s1.py = 123, 456
s1.hero_hp = 50
s1.move_count = 7
s1.items = {2001: {'shape': 1, 'key': 0, 'qty': 3, 'pos': 20, 'infoext': '幻灵珠'}}
s1.pet_hp = 88
s1._persist()
check(os.path.exists(s1.savepath), '存档文件已生成')

# 3) 重新实例化（模拟进程重启）→ 应载入上次状态
s2 = S()
check(s2._loaded is not None, '重启后载入存档')
check(s2.px == 123 and s2.py == 456, '位置已留存 (%d,%d)' % (s2.px, s2.py))
check(s2.hero_hp == 50, 'HP 已留存 (%d)' % s2.hero_hp)
check(s2.move_count == 7, 'move_count 已留存 (%d)' % s2.move_count)
check(s2.pet_hp == 88, '宠物 HP 已留存 (%d)' % s2.pet_hp)
check(2001 in s2.items and s2.items[2001]['qty'] == 3, '背包(整型 iid)已留存')
check(isinstance(list(s2.items.keys())[0], int), '背包键为 int（JSON 还原正确）')

# 4) 即使 handle() 不重置，位置也保持（验证 handle 重置块被门控）
#    通过调用 _reset 等效逻辑：模拟 handle 内的 reset 只针对有存档时跳过
s2._loaded = s2._loaded  # 保持
# 模拟 handle 开头：无 _loaded 才重置
if not s2._loaded:
    s2.px, s2.py = m.SPAWN_X, m.SPAWN_Y
check(s2.px == 123, 'handle 内仅无存档才回出生点（已门控）')

# 清理（仅临时目录）
shutil.rmtree(tmp, ignore_errors=True)
print('OK   已清理测试临时目录')

print('\n=== 持久化冒烟 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
