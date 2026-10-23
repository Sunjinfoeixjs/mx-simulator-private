# P5-03 存档安全与回滚（第 0 步：根路径缺陷修复）

> 生成时间：2026-10-23
> 触发：P5-00 基线审计（`docs/P5_BASELINE_AUDIT.md` §9 R1）发现 `CLAW` 少算一层
> 状态：**第 0 步已完成并全绿；备份/恢复/完整性校验/8 类故障注入尚未开始**（见 §7）
> 回归：**61/61 通过**（新增 `_test_save_paths` 一步）；最终文件状态下复跑 262.4s、退出码 0

---

## 0. 结论摘要

| # | 结论 |
|---|---|
| 1 | 存档/日志根路径 `CLAW` 原先 `dirname×3`，而 `m4_server.py` 在 `MX/服务端源码/tests/sim/` 是 **4 层**深 → 目录多套一层 `服务端源码` |
| 2 | 存档侧：历史 `服务端源码/save/role.json` 会被**静默忽略**（`load_role` 对「文件不存在」返回 `None` 属正常路径，不报错） |
| 3 | 日志侧：**已经实际损坏验收闸门** —— 闸门读 `服务端源码/logs`（1288 份，最新 **10-22 17:28**），服务端却写到嵌套目录（242 份，最新 **10-23 17:16**）→ 闸门永远看不到新会话 |
| 4 | 修法：不再数层数，改成**按标志目录定位**（`proj_root.py`：同时含 `服务端源码/` 与 `协议资料/` 的那一层） |
| 5 | 四个同源模块（`m4_server` / `m2_server` / `crypto_capture` / `crypto_probe`）**一次改全**，并有断言守着「不许再分叉」 |
| 6 | 先写会红的测试再改代码：修复前 **7/11**、修复后 **11/11**；两次变异分别判 **7 项红** / **1 项红** |
| 7 | 这条是 **P5-01 / P5-09 的前置阻断项**：人工验收做得再对，机器闸门也会拿旧日志判 FAIL |

---

## 1. 缺陷本体

### 1.1 原代码

```python
# 四个文件里一模一样的一行
CLAW = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGDIR   = os.path.join(CLAW, '服务端源码', 'logs')
SAVE_DIR = os.path.join(CLAW, '服务端源码', 'save')
```

它隐含假设「本文件位于 `MX/<a>/<b>/x.py`」，此时 `dirname×3` 恰好是 `MX`。
文件实际在 `MX/服务端源码/tests/sim/x.py`，是 **4 层**：

| 表达式 | 结果 |
|---|---|
| `abspath(__file__)` | `MX/服务端源码/tests/sim/m4_server.py` |
| `dirname ×1` | `MX/服务端源码/tests/sim` |
| `dirname ×2` | `MX/服务端源码/tests` |
| `dirname ×3` | **`MX/服务端源码`** ← 已经是 `CLAW`，但它被当成 `MX` 用 |
| 再拼 `'服务端源码'` | **`MX/服务端源码/服务端源码`** ← 多一层 |

### 1.2 为什么它是「静默」的

三条路径都对「目录/文件不存在」做了正常化处理，没有一条会报错：

| 环节 | 行为 |
|---|---|
| `load_role(path)` | 文件不存在 → 返回 `None`，与「新角色」完全同一条路径 |
| `save_role()` → `_save_role_nolock()` | `os.makedirs(directory, exist_ok=True)` → 目录不存在就建，成功 |
| `_has_char_saves()` | `os.listdir` 抛 `OSError` → `except OSError: return False` |

于是「路径错了」和「你是新玩家」在代码里**长得一模一样**。

### 1.3 后果分两侧，严重度不同

**存档侧（潜在，尚未发生）**

`MX/服务端源码/服务端源码/save/` 至今**不存在** —— 说明路径错位以来没有一次带角色进度的落盘。
一旦发生：默认启动写到新空目录，历史 `role.json` 读不到，玩家以为进度丢了。

**日志侧（已发生，可量化）**

| | 服务端实际写 | 闸门实际读 |
|---|---|---|
| 路径 | `服务端源码/服务端源码/logs/` | `服务端源码/logs/` |
| 份数 | **242** | **1288** |
| 最新一份 | `m4_srv_20261023_171613.log`（**10-23 17:16**） | `m4_srv_20261022_172858.log`（**10-22 17:28**） |

会话闸门 `协议资料/报告/验收/_verify_session_flow.py` 自己算的根是**对的**
（`MX = dirname×3(HERE)`，HERE 在 `协议资料/报告/验收`，×3 正好是 MX）。
两头不一致的直接结果：无参数跑闸门，它选中 **10-22 的旧会话**，判定

```
**会话闸门判定：FAIL**
（连续性 ✅ 末次会话内走完；战斗场数 1 —— 要求 ≥2）
```

而真正最新的 10-23 会话它**从来没看过**。

> 所以这不是「以后可能出事」，是「现在就是坏的」，而且坏在最要命的地方：
> **验收结论的机器依据**。

---

## 2. 修法：按标志目录定位，不数层数

新增 `服务端源码/tests/sim/proj_root.py`：

```python
ROOT_MARKERS = ('服务端源码', '协议资料')   # 两个都必须在
MAX_UP = 8

def find_project_root(start):
    """向上找同时含两个标志目录的那一层；找不到返回 None。"""
    p = os.path.abspath(start)
    if not os.path.isdir(p):
        p = os.path.dirname(p)
    cur = p
    for _ in range(MAX_UP):
        if all(os.path.isdir(os.path.join(cur, m)) for m in ROOT_MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:      # 到盘符根
            break
        cur = parent
    return None

def fallback_root(start, levels):
    """标志目录都不在时的兜底：向上 levels 层。只为「别让起服直接崩」。"""
```

调用点（四个文件统一成同一行，且都在顶格，便于文档索引扫到）：

```python
CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
```

### 2.1 三个设计取舍

| 取舍 | 选择 | 理由 |
|---|---|---|
| 为什么用**双**标志 | `服务端源码` + `协议资料` 都必须在 | 单标志会误判：旧布局下 `MX/服务端源码/` 自己就可能含同名子目录 —— **这正是本 bug 的成因** |
| 找不到时返回什么 | `None`（不猜） | 猜出来的路径能让程序继续跑，但存取档落到没人预期的位置 —— 比崩更坏的失败 |
| 那为什么还留 `fallback_root` | 只在标志目录都不在时用，且显式写在调用点 | 起服不该因为目录布局变了就崩；但这个决定必须**留在代码里看得见**，不能藏在工具函数里 |

### 2.2 为什么不再「数层数」

这个文件**已经挪过一次**（`sim/` → `tests/sim/`），就是那次让 `dirname×3` 失效的。
只要还可能挪，数层数就会再错一次；标志目录随文件一起走，挪层不影响判定。

---

## 3. 测试：先红后绿

`服务端源码/tests/_test_save_paths.py`（11 项断言，已并入回归 STEPS）

| # | 断言 | 想守住什么 |
|---|---|---|
| 前置 1-2 | 项目根下有 `服务端源码/`、`tests` 与 `sim` 同层 | 测试自己的前提，防止在错目录里跑出「假绿」 |
| ① | `m4_server.CLAW == 项目根` | 根路径本身 |
| ② | `SAVE_DIR` 相对根**恰为** `服务端源码/save`；`LOGDIR` 恰为 `服务端源码/logs` | 分量里不得出现两个 `服务端源码`（用 `relpath` 拆段比对，不做字符串包含） |
| ③ | `SAVE_PATH` 必须位于 `SAVE_DIR` 内 | 防「只改一半」 |
| ④ | 磁盘上存在 `服务端源码/save/role.json` 时，`SAVE_DIR` 必须指向它 | **结果导向**：不关心怎么算的，只关心玩家那份档找不找得到 |
| ⑤ | 四个同源模块的 `CLAW` 行必须**同式** | 防「修一处漏三处」，日志再次分裂 |

### 3.1 期望值独立推导（关键）

```python
PROJ = os.path.dirname(os.path.dirname(HERE))   # tests → 服务端源码 → MX
```

**刻意不复用** `m4_server` 的算法：如果用被测对象算期望值，它错的时候期望值跟着一起错，
测试永远绿。这里 `PROJ` 只依赖「本测试文件在哪」，与被测模块无关。

### 3.2 红 → 绿实录

修复前（`CLAW` 仍是 `dirname×3`）：

```
[FAIL] m4_server.CLAW == 项目根　CLAW=C:\Users\Administrator\MX\服务端源码  期望=C:\Users\Administrator\MX
[FAIL] SAVE_DIR 相对项目根恰为 服务端源码/save　SAVE_DIR → 服务端源码/服务端源码/save
[FAIL] LOGDIR 相对项目根恰为 服务端源码/logs　LOGDIR → 服务端源码/服务端源码/logs
[FAIL] 历史存档 role.json 落在 SAVE_DIR 里　磁盘上有 …\服务端源码\save\role.json，但 SAVE_DIR=…\服务端源码\服务端源码\save
存档/日志根路径契约：7/11 通过        退出码=1
```

修复后：

```
[PASS] m4_server.CLAW == 项目根　CLAW=C:\Users\Administrator\MX  期望=C:\Users\Administrator\MX
[PASS] SAVE_DIR 相对项目根恰为 服务端源码/save　SAVE_DIR → 服务端源码/save
[PASS] LOGDIR 相对项目根恰为 服务端源码/logs　LOGDIR → 服务端源码/logs
[PASS] SAVE_PATH 位于 SAVE_DIR 内
[PASS] 历史存档 role.json 落在 SAVE_DIR 里
[PASS] m4_server.py / m2_server.py / crypto_capture.py / crypto_probe.py 的 CLAW 与 m4_server 同式
存档/日志根路径契约：11/11 通过       退出码=0
```

---

## 4. 变异验证

按 P3-03 定下的纪律：**变异打不红 = 覆盖漏洞，或变异构造错**。两次变异都记在这里，包括那次**打不红的**。

| # | 变异 | 期望 | 实测 | 判定 |
|---|---|---|---|---|
| M1 | `CLAW = fallback_root(__file__, 2)`（退回旧的「数层数」） | 根路径类断言全红 | **7 项红**：①②×2 + ④ + ⑤×3，得分 4/11，退出码 1 | ✅ 判红 |
| M2a | `SAVE_PATH = os.path.join(CLAW,'服务端源码','save','role.json')` | 应红 | **11/11 全绿** | ❌ **变异构造错** |
| M2b | `SAVE_PATH = os.path.join(CLAW,'save','role.json')` | 只 ③ 红 | **只 ③ 红**，得分 10/11，退出码 1 | ✅ 判红 |

### 4.1 关于 M2a 打不红

`os.path.join(CLAW,'服务端源码','save','role.json')` 与
`os.path.join(SAVE_DIR,'role.json')` 在 `SAVE_DIR` 正确时是**同一个路径**，
所以它不是「覆盖漏洞」，是**变异本身没有改变行为** —— 与 P3-03 的 M2（只挂一个
`taken_mul` 状态、相乘与覆盖结果相同）属于同一类错法。

换成 M2b（真的让路径跑到 `CLAW/save`）后精确只红 ③，说明 ③ 这一项不是冗余。

### 4.2 变异后的还原

两次变异都在跑完后立刻还原，并逐字节核对：

```python
126: CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
127: LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')
129: SAVE_DIR = os.path.join(CLAW, '服务端源码', 'save')
130: SAVE_PATH = os.path.join(SAVE_DIR, 'role.json')
```

还原后 `_test_save_paths` 11/11、退出码 0。

---

## 5. 连带验证

| 验证 | 结果 |
|---|---|
| `_test_persist` | rc=0 PASS（它显式传 `savedir=`/`logdir=`，不受默认路径影响） |
| `_test_role_persistence` | rc=0，Ran 9 tests OK |
| `_test_save_lock` | rc=0，18/18 |
| `_test_save_async` | rc=0，ALL PASS |
| `_test_schema_migration` | rc=0，20/20 |
| `_test_server_stop` | rc=0，6/6 |
| **全量回归** | **61/61 通过**，`steps_failed=0`，退出码 0。首轮 275.1s；两次变异还原后**在最终文件状态复跑 262.4s** 再绿 |
| 文档三向体检 `_check_anchors` | 三向一致（方向 A/B/C；手写值核对 24 处） |

### 5.1 为什么这些测试没抓到原 bug

`grep -rn "SAVE_DIR\|SAVE_PATH\|LOGDIR\|CLAW" _test_*.py` → **零命中**。

也就是说：**80 个测试文件里没有一个引用过这三个模块级路径常量**。
所有存档测试都用 `savedir=<tmp>` 显式隔离（这本身是对的、必须保持），
代价是「默认路径算错了」这件事完全没有观测点 —— 直到 P5-00 用 `import` 把值打出来看。

> 教训写进 P5-04：**「测试都用 tmp 隔离」不等于「默认路径正确」**。
> 隔离是为了不碰真档，但不能替代对默认值的断言。

---

## 6. 修复后系统会怎么走

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 默认起服（`singleplayer.py`，无 `--save-dir`） | 存档写 `…/服务端源码/服务端源码/save/` | 写 `…/服务端源码/save/`（历史档所在处） |
| 读历史 `role.json` | 读不到（读的是新的空目录） | 读得到 |
| 服务端写日志 | 写 `…/服务端源码/服务端源码/logs/` | 写 `…/服务端源码/logs/` |
| 会话闸门（读 `…/服务端源码/logs/`） | 只能看到 10-22 之前的会话 → 判 FAIL | 能看到最新会话 |
| 抓包工具 `crypto_capture` / `crypto_probe` | 产物落嵌套目录 | 落 `…/服务端源码/logs/` |
| `m2_server` | 同上 | 同上 |

**日志不迁移**：嵌套目录里那 242 份保留原地，作为历史证据引用，不移动、不删除。
新会话从修复时刻起写正确位置。

---

## 7. P5-03 剩余工作（**尚未开始，不得声称完成**）

指令要求的 8 类故障注入与备份/恢复/回滚，当前状态：

| 能力 | 现状 | 备注 |
|---|---|---|
| 原子替换（tmp + `os.replace` + `fsync`） | ✅ 已有 | `_save_role_nolock` |
| 进程内互斥 + 跨进程文件锁 | ✅ 已有 | `_path_lock` / `_CrossProcessLock` |
| 跨会话丢更新告警 | ✅ 已有 | `_save_tag` 比对 |
| 迁移链 `0→1→2` | ✅ 已有 | 逐级、不跳版、异常不吞 |
| **备份轮换** | ❌ 未做 | 目前只有一份 `role_<账号>.json` |
| **从备份恢复 / 回滚** | ❌ 未做 | |
| **完整性校验（checksum/结构）** | ❌ 未做 | |
| **8 类故障注入** | ❌ 未做 | 计划见 §7.1 |

### 7.1 8 类故障注入计划（P5-03 主体）

| # | 故障 | 期望行为 |
|---|---|---|
| 1 | 落盘中途进程被杀（`os.replace` 前） | 原文件**一字节不动**，重启读到旧档 |
| 2 | 磁盘满 / 目录只读 | `save_role` 返回 False，上层不误报成功，内存继续可玩 |
| 3 | 存档文件被截断（JSON 半截） | `load_role` 返回 None 且**不覆盖**原文件，进入 `_save_blocked` |
| 4 | 存档字段类型被改坏（如 `items` 变 list） | 读档报错、拒绝自动保存，不静默清空进度 |
| 5 | `_schema` 是未来版本（如 99） | 拒绝载入，不降级写入 |
| 6 | 迁移函数抛异常 | 不吞异常、不写半迁移档 |
| 7 | 并发两进程同写同账号 | 文件锁排他；后写者打告警；不产生半档 |
| 8 | 战斗中掉线（`in_war=True` 时进程退出） | 已扣 HP 保留、战斗作废；**不得**出现「血量回满但敌人消失」以外的第三种状态 |

---

## 8. 不得声称的事项

| 说法 | 为什么不行 |
|---|---|
| 「P5-03 完成」 | 备份/恢复/校验/8 类故障注入**一项都没做**，本轮只做了第 0 步 |
| 「存档安全了」 | 现在只是**路径对了**；没有备份、没有恢复、没有完整性校验 |
| 「可以回滚」 | 无 git、无备份轮换 —— 改坏了只能手工恢复 |
| 「日志侧的 242 份已迁移」 | 没有迁移，是有意留在原地 |
| 「验收闸门现在会 PASS」 | 闸门现在能**看到**新日志；是否 PASS 取决于会话内容，本轮没跑真机会话 |

---

## 9. 变更清单

| 文件 | 变更 |
|---|---|
| `服务端源码/tests/sim/proj_root.py` | **新增**：`find_project_root` / `fallback_root` |
| `服务端源码/tests/sim/m4_server.py` | `CLAW` 改为标志目录定位（含 ImportError 兜底）；`LOGDIR`/`SAVE_DIR`/`SAVE_PATH` 不变 |
| `服务端源码/tests/sim/m2_server.py` | 同上 |
| `服务端源码/tests/sim/crypto_capture.py` | 同上 |
| `服务端源码/tests/sim/crypto_probe.py` | 同上 |
| `服务端源码/tests/_test_save_paths.py` | **新增**：11 项断言 |
| `服务端源码/tests/sim/tests/_run_all.py` | STEPS 新增「存档根路径 _test_save_paths」，60 → 61 步 |
| `协议资料/报告/模拟服全链路代码解析.md` | §12.1 步数 60 → 61；新增一行步骤说明 |
| `协议资料/报告/docs/*` | 由 `_split_docs.py` 重新生成 |
| `docs/P5_BASELINE_AUDIT.md` | §9 R1 / §9.1 / §9.2 补实测数据与「已造成损害」结论 |
