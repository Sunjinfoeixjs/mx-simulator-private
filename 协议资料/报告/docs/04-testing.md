# 测试与回归 + 一致性审计

> 本册是《模拟服全链路代码解析》的拆分分册：**一键回归的四层防线、退出码审计、build_* ↔ OVERLAY 三角一致性**。
> 完整版（唯一现状依据）：[`../模拟服全链路代码解析.md`](../模拟服全链路代码解析.md)。
> 拆分只为聚焦阅读，内容与主文档逐字一致；**改内容请改主文档**，然后重跑
> `python 协议资料/报告/_split_docs.py` 重新生成本册。
>
> 文档双向体检：`python 协议资料/报告/_check_anchors.py`（已挂进 `tests/_run_all.py` 第 ④ 层）。

---
## 12. 测试与回归

### 12.1 一键入口

`python 服务端源码/tests/sim/tests/_run_all.py`，默认配置 **65 个步骤**、分四层执行，全绿（步数由方向 C 与 `len(STEPS)` 比对；耗时见 STATS 块）：

| 层 | 步骤 | 断言 |
|---|---|---|
| ① 协议对账（静态） | `_extract_wire.py --audit` | 客户端 `Unpack*` ↔ `OVERLAY`：不一致 0 |
| ① | `_audit_build_overlay.py` | `build_*` ↔ `OVERLAY`：MISMATCH / NO_OVERLAY / CALL_FAIL 全为 0（退出码三者都判红，见 §13.1） |
| ① | `_c2s_coverage.py` | 上行覆盖 **134/134**；扫描段守卫：分派链 if/elif < 40 条直接 rc=2（分支体被重构搬走时大声红，详见 §12.1.4） |
| ② 分派契约（进程内） | `tests/_test_dispatch.py` | **58 条** PRE_WAR op 三重对照：①战斗态**不落保底**（负向）②非战斗态**走进自己那条分支**（正向，逐条核 `>>>` 日志指纹）③空转检测（不落保底也没干活=形同虚设）；另有未知 op 必落保底、op129/op105 覆盖表、伤害横幅、配置四路失效 |
| ② 数据层 | `tests/_test_damage.py` | 物伤公式：分支切换点 / 截断语义 / 不 clamp / if-vs-max 分叉（22 条断言） |
| ② 数据层 | `tests/_test_time_channels.py` | 时间双通道：op15 必须 10000Hz **单调**滴答（墙钟回跳不误报 007）/ op192 必须 Unix 秒 / 通道不混（7 项） |
| ② 数据层 | `tests/_test_tap.py` | 23 个真实帧恰好吃满 + 字段名逐项对齐 + 分帧自愈 + 世界/战斗模型 + **字节翻转 fuzz 940 次**（不许「fit 却说有问题」）（101 条断言） |
| ② 数据层 | `tests/_test_editor.py` | 世界配置校验/保存/重置 + 编辑器前端逻辑（14 项） |
| ② 数据层 | `tests/_test_schema_migration.py` | **18 项**：老档升级、逐级迁移、字段完整、拒绝未来版本/非法版本/缺迁移函数、条目规范化 |
| ② 数据层 | `tests/_test_save_lock.py` | **18 项**：并发写不坏、同账号冲突告警、跨进程锁排他（起子进程）、锁按路径分桶 |
| ② 存档路径 | `tests/_test_save_paths.py` | **11 项**：`CLAW` 必须等于项目根、`SAVE_DIR`/`LOGDIR` 相对根恰为 `服务端源码/{save,logs}`（分量里不得出现两个 `服务端源码`）、`SAVE_PATH` 落在 `SAVE_DIR` 内、磁盘上有历史 `save/role.json` 时 `SAVE_DIR` 必须指向它、四个同源模块的 `CLAW` 行必须同式（防修一处漏三处） |
| ② 数据层 | `tests/_test_speed_stability.py` | **17 项**：7/8/9/10 敌排序、10 敌钳位同速、同速 ≥5 组×20 次确定性、插入序敏感性 |
| ② 战斗新链 | `tests/_test_hero_magic.py` | 玩家招式：逐级消耗/威力/效率、群攻目标数 `slv//6+2`、三种拒绝**一分不扣**、校准表 11 个命中点不漂 |
| ② 战斗新链 | `tests/_test_pet_skill.py` | 宠物招式：`type` 分流、415 群攻、FP 不足回落普攻、必杀/连击被动、被动可整层静音 |
| ② 战斗新链 | `tests/_test_battle_flow.py` | 战斗全链路：每帧都用 `frames.unpack_s2c` 解（顺带守住「字段定义与收发包同源」） |
| ② 战斗新链 | `tests/_test_battle_core.py` | 状态机：开战不可重入 / 窗口闸门 / 超时默认动作不被吞 / 非法指令不消耗回合 / 战败逃跑清干净 |
| ② 战斗新链 | `tests/_test_war2.py` | 自带内核的战斗深化：道具 165 扣数量 + 提交宠物指令完成回合 |
| ② 内容/剧情 | `tests/_test_encounter_map.py` | 场景遇怪：43 场景 / 124 怪全解析、城镇不遇怪、`per_map=False` 回落全图野狼 |
| ② 内容/剧情 | `tests/_test_quest_chain.py` / `_test_quest_story4.py` / `_test_quest_716.py` | 主线 1–16：接取门（等级/前置/单主线）、全链路、战败重打、放弃、重登恢复、剧情点学剧情技能；7–16 另测坐标双源 / 降级步 / 任务态可见性 / 17–22 不可得 |
| ② 内容/剧情 | `tests/_test_content.py` | 内容扩充：60 图全可达、新药可买可吃、宠物目录切换后 `WarAdd/SummonInfo` 用新名新造型 |
| ② 观测/存档 | `tests/_test_bus.py` | 事件总线（环形覆盖/分类/订阅锁外/异常隔离）+ 帧批合并（含**真 socket 逐字节一致**） |
| ② 观测/存档 | `tests/_test_save_async.py` | 存档增量+异步：脏键 noop、写线程合批、`flush_saves` 收口、跨写者告警、`save_stats` |
| ② 观测/存档 | `tests/_test_persist.py` | 持久化冒烟（2026-10-23 复活，旧路径失效沦为孤儿）：改状态→落盘→重实例化逐字段回读 |
| ② 观测/存档 | `tests/_test_role_persistence.py` | 原子存档（复活）：`save_role` 原子替换、损坏原文件不被覆盖、无存档可写；与存档锁互补（单次写要么完整要么不动） |
| ② 观测/存档 | `tests/_test_state_consistency.py` | 状态一致性（P0）：`_persist` 快照深拷贝（活引用 `items`/`tasks`/`hero_res` 原地修改必须落盘，否则 noop 分支静默跳过→磁盘过期）、宠物运行时镜像各同步点自洽、存档往返等价、异步不赛跑；变异验证（换回浅拷→9 项判红） |
| ② 观测/存档 | `tests/_test_sysopt.py` | 系统设置 op21/12/11：收下即走（不回帧）、存盘回读、战斗态**不落保底**、日志脱敏 |
| ② 观测/存档 | `tests/_test_pet_instance.py` | 宠物实例：多宠隔离、加点、经验升级、存盘重登逐只下发、倒地不参战、属性真的进战斗 |
| ② 观测/存档 | `tests/_test_pet_delivery.py` | 宠物交付（P1）：pid 唯一性（判重+读档去重+造型目录校验）+ 交付原子性（落盘确认后才发 48/49/53，失败回滚零帧、原因如实）；变异验证（先发帧后判落盘→3 项判红） |
| ② 养成 | `tests/_test_p3_progression.py` | 角色深度养成（P3-01）：最终属性唯一入口（派生值只算不存）、加点 //4 整除语义、装备槽位/等级门（252 条真值表）、非法拖拽回弹不扣资源、旧档缺字段回落；变异验证 4 次全红 |
| ② 养成 | `tests/_test_p3_pet_growth.py` | 宠物深度养成（P3-02，80 项）：三层属性收口 `pet_progression`（默认宠物 = 录像读数 ×1.0、加点面板/战斗双层联动）、`pet_normalize` ×100 幂等、非法培养零副作用、技能服务（绑定归一 10415≡415 / 白名单 / 遗忘 / 替换 / 上限）、遗忘「连击」→ `combo_chance` 归零、旧档字符串键收口、重登派生值不叠加、GM 三入口；变异验证 4 次全红 |
| ② 战斗 | `tests/_test_p3_03_combat.py` | 高级战斗（P3-03，106 项）：随机源 11 条流（同种子同序 / 流间隔离 / 每场重置 / 可退化）、状态真值 284 条（34 量化 / 29 可建模 / 生成物未过期 / 逐条带 `src`）、**转录防伪三条**（`src` 引号内文字逐字出自客户端说明、`QUANT_CANDIDATES` 与「含数字」集合双向相等、建模⊆量化清单且与不可读不重叠、6 条不可读必须带「—— 理由」）、状态机制（refresh vs add / 到期 / 驱散 / 上限 / 未知 id 拒绝 / 乘区相乘）、DOT（20% 上限 / 30% 当前 / 累加 / 兜底 1 点 / **无状态零帧**）、强制防御（恐怖减半、水晶墙 ×0.2）、敌方 AI（抽取顺序 / 保护不放招 / focus 选残血 / 决策可解释）、**六路入口结算次数恒 ≤1** 且 3 只同灭时 TaskAdd 收敛 1 次而 WarDel 仍逐只 3 帧、GM 四指令；变异验证 7 次全红（含「src 抄错数字」「去掉不可读理由」两次） |
| ② 观测/存档 | `tests/_test_pet_equip.py` | 宠物装备（P1，83 项）：op54 线序 ↔ 客户端 Unpack 语义对账（含 `0xFF` 扩展长度帧 / GBK / 越界 pos 滤除）、C2S 9 字节上行（无独立装备 opcode）、≥20 等级门、同类替换旧装备消失、落盘失败回滚零帧、存档往返补发 54、读档清洗、控制台/HTTP/GM 三入口同源；变异验证（54 帧挪到落盘确认前→7 项判红；禁用写失败基线回滚→2 项判红） |
| ③ socket 冒烟 | `_e2e_m4/m5/m7/m9/m11` + `_test_war2` + `coverage_smoke` | 真 socket + 线上字节；探针按守卫补长，误报自动闭环 |
| ④ 文档三向 | `_check_anchors.py` | A：1042 处引用符号全存在（含 docs/ 全部分册）；B：§17 表与源码逐行一致；C：手写指标值（步数/契约表条数/`build_*` 个数/覆盖率）与源码实况一致 |

`_run_all.py` 每个子测试独立子进程、独立端口、独立 tmp 存档。

### 12.1.1 退出码审计：非 0 不许被吞

回归最阴险的失败模式不是「红」，而是**「看起来全绿」**：子测试返回非 0，
却被 `|| true`、`check=False`、管道、或一个宽松的通过准则吞掉。

`_run_all.py` 现在对此做三层审计（`verify_exit_codes`）：

| 层 | 查什么 | 例子 |
|---|---|---|
| 静态 | STEPS 里每条命令是否含吞码写法 | `|| true` / `; true` / `| tail` / `check=False` / `shell=True` |
| 运行时 | `rc != 0` 却被判 PASS，且未登记 | 某步骤自定准则返回 True |
| 登记 | `EXIT_CODE_EXEMPT` 是否有失效条目 | 步骤改名了，例外还留着 |

**例外必须显式登记**（`EXIT_CODE_EXEMPT`，写明理由）。

### 当前状态：例外 **0 条**（2026-10-20）

```python
EXIT_CODE_EXEMPT = {}
```

曾经这里挂着一条：线序对账 `_extract_wire --audit` 一直返回 rc=1，
理由是「工具把既有积压也算进 problems」。

后来查清那 4 个「需动态未配」**根本不是积压**：

| op | 真相 | 处理 |
|---|---|---|
| op26 `S2CPropAdvXinfa` | 覆盖表真是静态的（`ids:T` 原始块） | 补齐成 `L{count:n}` |
| op99 `S2CMapAddNpc` | 已用 `P(title_len)` 表达可变结构，判定器没识别 | 判定器认 `P()` |
| op105 `S2CMapSummon` | 已用 `L{count:n}` 表达，判定器没识别 | 判定器认 `L{}` |
| op16 `S2CPropInit` | 分支只在客户端本地逻辑分叉，两路解包序列相同 | 判定器认「序列重复」 |

修完这四类，工具自然返回 0，豁免失去存在理由。

现在汇总里不再有 `PASS*`，全部是干净的 `PASS`：

```
PASS      0.1s  线序对账 _extract_wire --audit
…
退出码审计：通过（无非 0 被吞，0 条例外均已登记）
```

> 这条经历值得记住：**豁免往往是「问题没查清」的遮羞布**。
> 当初写「工具把积压算进 problems」时，其实没查过那 4 个 op 到底是什么——
> 查完发现 3 个是判定器缺陷、1 个是真缺口，没有一个是「合理积压」。
> 将来若又要加例外，先问「非 0 的真正原因是什么、能不能修掉」。

### 判定器怎么识别「已表达的可变结构」

客户端有分支/循环 ≠ 覆盖表必须换成 `_OV_DYN`。三条判据：

1. 覆盖表含 `L{}`（循环）→ 可变结构已表达；
2. 覆盖表含 `P()`（变长前缀）→ 已表达；
3. 分支只影响本地逻辑（两路解包序列相同）→ 线序唯一，静态规格够用。

第 3 条的实现：把解包序列从中间一切为二，两半相同即判定「重复表达同一线序」
（op16 就是 `if hero.g_ID: <解包>` 之后又无条件 `<同样解包>`）。

审计有违规时**直接 exit 1**，即使步骤本身恰好全过：

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
 退出码审计：发现 1 处「非 0 退出码被吞」
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  ✗ [静态] 假绿步骤：命令含 '|| true'（shell 里 || true 会把退出码抹成 0）
```

> 两个实现细节：① 死条目校验只在**跑全量**时做——按关键字筛选时没选中的步骤
> 本来就不在列表里，否则满屏假红。② 步骤「真的失败」时审计**不报**，
> 让问题归因保持单一（失败就是失败，不用再叠一层「退出码被吞」）。

### 12.1.2 存档并发：账号级锁 + 丢更新检测

`save_role` 原本只有 `mkstemp + os.replace` 的**原子性**——它保证「文件不会是半截」，
但**保证不了「后写的覆盖先写的」**。MultiServer 下同一账号可以有多个会话
（重连残留、双开、GM 另开一个），它们各持一份内存状态，落档时都写同一个文件。

实测（2026-10-19，未加锁）：两个线程各写 200 次 → **29 次写失败**
（Windows 上并发 `os.replace` 会抛 `PermissionError`）。

现在两层锁：

| 层 | 实现 | 防什么 |
|---|---|---|
| 进程内 | `threading.Lock`，按**绝对路径**分桶（`_path_lock`） | 同进程多会话互斥 |
| 跨进程 | `<path>.lock` + `msvcrt.locking`/`fcntl.flock`（`_CrossProcessLock`） | 另开一个 python 进程跑模拟服 |

拿不到跨进程锁时**降级而不阻塞**：仍持进程内锁写入，不无声跳过。

#### 锁防不住的：丢更新

锁解决「写坏」，解决不了「两个会话各持内存状态、后者整体覆盖前者」。
这是业务语义问题，不是并发原语能修的。因此加了**检测**：

每次落档把 `_save_tag`（账号 + pid + 会话标识）写进存档；落档前读上一次的 tag，
若与自己不同 → 打显式告警并置 `_save_conflict`：

```
⚠ 存档被另一会话写过（100860001-12388-6103328 → 本会话 100860001-12388-6097280）：本会话落档将覆盖其进度
```

> **不阻止写入**是有意的：单人游戏不该因为并发就丢档。但至少不再静默——
> 事后能从日志里看出「这段进度是被另一个会话覆盖的」。

回归：`tests/_test_save_lock.py`（18 项），含**真起子进程**验跨进程锁排他性
（只测 `acquired=True` 是自欺，那连「锁是个全局变量」都能通过）。

### 12.1.3 存档 schema 版本与迁移

存档是**用户资产**，字段随功能增加（M9 养成 / M10 买卖 / M13 制作…）。没有版本号时，
「老档缺新字段」只能靠 `d.get(k, 默认)` 在每个读取点各写一遍；一旦某字段需要**转换**
（不只是补默认值），就散落成隐式约定，改一处漏一处。

| 机制 | 说明 |
|---|---|
| `SCHEMA_VERSION` | 当前版本号（现为 2），每次落盘写进 `_schema` |
| `MIGRATIONS` | `{源版本: 函数}`，**逐级**升级，不跳版（0→1→2 每步可单测） |
| `migrate_role` | 跑迁移链，返回 `(data, steps)`；异常**不吞** |
| `normalize_items` | **独立于版本**，每次读档都跑 |

#### 三条安全约定

1. **拒绝比程序新的档**：`_schema > SCHEMA_VERSION` → `load_role` 返回 `None`
   → 上层 `_save_blocked=True` 禁止自动保存。用旧程序打开新档再落盘，等于把新字段全删了。
2. **不半迁移**：任一步抛异常就整体判为不可读，不带着可疑状态继续跑。
3. **已是最新版不重跑**：迁移不保证幂等，所以只在 `cur < SCHEMA_VERSION` 时调用。

#### 踩到的坑：迁移修不了「版本对、内容坏」

首版把背包条目规范化写进 `_migrate_1_to_2`。结果 `_test_tap` 崩了——
报 `struct.error: required argument is not an integer`，根因是存档标着 `_schema=2`
却带 `key="0"`（字符串）：**迁移按版本跳过，坏值原样进 `build_item_add`**，
于是进世界时 socket 线程异常、客户端卡住。

手写存档、外部工具改过的档、早期代码写的档都可能出现这种组合。所以：

> **迁移按版本触发，但结构校验不能按版本**。条目规范化是幂等的，每次读档都跑一次，
> 代价可忽略，换来的是「任何来源的档都不会把非整数送进 struct.pack」。

回归：`tests/_test_schema_migration.py`（**18 项**），含
「版本已最新但条目键是字符串 → 仍被规范化」这条专门用例。

### 12.1.4 STATS 块：报告数字的唯一来源（2026-10-20）

报告里反复引这类数字——契约表条数、回归步数、`build_*` 个数、覆盖率分子分母。
过去每次都要分别跑 `_run_all.py`、`_c2s_coverage.py`、`grep '^def build_'`、
`len(DISPATCH_PRE_WAR_OPS)` 四个地方手工抄，抄漏一个报告就过期
（§18.1 那条快照就是这么来的）。

现在 `_run_all.py` 每跑完回归，**末尾自动打一块固定格式 STATS**：

```
=== STATS ===
build_star_count     : 58
coverage_denominator : 134
coverage_numerator   : 134
dispatch_pre_war_ops : 58
elapsed_sec          : 226.5
mode                 : full
steps_failed         : 0
steps_passed         : 56
steps_total          : 56
=== END STATS ===
```

> 示例为某次实跑的值；权威值以当次回归输出为准（§12.1.4 引用规则：文档不手写这些数字）。

| 键 | 来源 | 稳定性 |
|---|---|---|
| `steps_*` / `elapsed_sec` | 本次回归实测 | elapsed 随负载抖动，属预期 |
| `mode` | full / fast / slow / filter:关键字 | **diff 前先看它**：筛选跑的 steps_total 不可与全量比 |
| `coverage_*` | `_c2s_coverage.compute()` 静态口径 | 可 diff |
| `build_star_count` | `m4_server.py` 源码 `^def build_` 计数 | 可 diff |
| `dispatch_pre_war_ops` | `len(DISPATCH_PRE_WAR_OPS)` | 可 diff |

事后重打：`python tests/_stats.py --log <回归输出>`（容忍 UTF-8 与 PowerShell `>`
产出的 UTF-16LE+BOM——不认编码会静默丢 steps 三项，mode 还会回落成 `full` 误导）。
格式契约自检：`python tests/_stats.py --self-test`（已挂回归 ② 层）。

### 文档数字规则：手写允许，方向 C 守护（2026-10-20）

上表九个键的值，正文里有两种合法写法：

- **手写数值**（可读性优先）——每次回归由 `_check_anchors.py` **方向 C** 与源码实况
  逐个比对（基准：`len(STEPS)` / `len(DISPATCH_PRE_WAR_OPS)` / `^def build_` 计数 /
  `_c2s_coverage` 静态口径），不一致判红，修法二选一：改成新值，或改为键名引用；
- **键名引用**（免跟改）——「条数以 STATS 块 `dispatch_pre_war_ops` 为准」。

为什么需要方向 C：手写值必然过期——2026-10-20 一天就抓到三处过期的（步数、覆盖率、
契约表条数），覆盖率那次更是 100%→14% 静默漂移。方向 C 把「过期」从静默变成红。
两点豁免：围栏代码块（示例输出，含上面的 STATS 示例）与 §18 复核快照（历史记录，
数字为当日值，不该被刷新）。`elapsed_sec` 随每次运行变化、静态不可校验 → **禁止手写**，
只能写「见 STATS 块」。

**不在 STATS 块里的数字**（仍手写，各有来源或守护）：各测试的断言数与项数、模块行数、
符号引用数（方向 A 输出）、录像场次、地图张数、协议参数（超时/间隔）。
它们要收进 STATS 是增量——候选键 anchor_refs / module_loc（真加的那天，记得同步方向 C 的规则）。

### 顺带抓到的真问题：覆盖率扫描器静默错读

做 STATS 时第一次实跑 `_c2s_coverage.py`，输出是 **19/134 = 14%**，不是报告里的 100%。
根因：当天早前 `_dispatch` 被重构成「包装方法 + `_dispatch_locked`」（RLock 临界区），
分支体整体搬走，而扫描器只扫 `def _dispatch(` 到下一个方法——**100+ 条分支全部漏掉**。
更糟的是这个脚本**没挂回归**，所以 33/33 全绿没人发现，报告数字早已过期。

三件事堵住这个洞：

1. 扫描段延伸到 `_dispatch_locked` 末尾（存在时）；
2. **分支数下限守卫**：扫到的 if/elif < 40 条直接判红（实测全量 110 条，余量充足）——
   分支体再被搬走就大声失败，不给一个难看的百分比；
3. `_c2s_coverage.py` 挂进回归 ① 层（静态，1.3s 量级）。

> 教训与 §18.1 同源：**「工具没挂回归」等于「工具的输出没人验证」**。
> 这次是 STATS 需求顺手把它拉出来的；下次加分析脚本，先问「它挂了没有」。

---

### 12.2 直接驱动内核的两类测试

| 类型 | 手法 | 代表 |
|---|---|---|
| 进程内 `FakeConn` | 真构 `M4Server`，**直驱 `_dispatch`**，无 socket，秒级 | `_test_dispatch.py` |
| 进程内管道 | `transport.make_memory_pair()` | `_test_tap.py` |

### 12.2.1 分派契约的三重对照（为什么一条不够）

`_test_dispatch.py` 对一个 PRE_WAR 分支同时做三个方向的断言。缺任何一条，
都会有一类真实缺陷**静默通过**：

| 对照 | 断言 | 缺了会漏掉什么 |
|---|---|---|
| ① 负向 | 战斗态下**不落保底**（哨兵替换 `_fallback_turn`） | —— 这是最基础的一条 |
| ② 正向 | 非战斗态下**走进自己那条分支**（逐条核 `>>>` 日志指纹） | 分支体被删空/改成 `pass`：不落保底、不回帧，①照样绿 |
| ③ 空转 | 不落保底 **且** 不执行 ⇒ 判红（仅对 9 条必然有副作用的分支） | `if self.in_war: return` 提前短路：①②都可能绿，功能已死 |

变异测试实测（2026-10-19）：

| 注入的缺陷 | ① | ② | ③ |
|---|:--:|:--:|:--:|
| 把 op16 移到战斗保底之后 | ✗红 | | |
| 把 op16 移到链尾 `else`（战斗态不可达） | ✗红 | | |
| `op108` 分支体清空成 `pass` | 绿 | **✗红** | |
| `op96` 条件改成永假（被后面分支抢走） | ✗红 | ✗红 | ✗红 |
| `op96` 加 `if self.in_war: return` | 绿 | 绿 | **✗红** |

> ② 用**日志指纹**而不是「有没有回帧」：回帧只能说明发了点什么，分不清走的是不是
> **该走的那条**（op99 落到 op98 也可能回帧）。每条分支自己打的 `>>> xxx` 才是自报家门。
> 指纹表 `EXPECT_LOG` 的取值是把 58 条逐个跑一遍实测出来的，不是照代码猜的。
>
> ③ 只对 `MUST_EFFECT_OPS`（9 条）生效：多数分支的静默是**合法**的
> （显式忽略族、未接任务不应答、战斗中不该移动…），一刀切会逼出造假断言。

`transport.py` 提供三种连接替身（`LoopbackListener` / `_PipeEnd` / `RejectingConn`），
全部鸭子类型兼容内核的 `sendall/recv/settimeout/close`。**它不引入新协议、不做加密**——
帧格式仍由 `make_s2c` / `recv_c2s` 定义。

### 12.2.2 真机测试：已跑通，但有一个端口陷阱

**结论先说**：真机 GUI 测试已跑通（2026-10-20），游戏完整进入世界、地图正常渲染、
移动/宠物/背包全链路正常。但踩了一个**非代码缺陷**的坑，值得写下来。

#### 前提：必须提权

`mx.exe` 的清单声明 `requestedExecutionLevel level="requireAdministrator"`：

```
$ python singleplayer.py
[客户端] 未提权会话 → 请求 UAC 提权启动 mx.exe（请在弹出的提示中点「是」）
```

无人值守环境里 UAC 没人点，客户端秒退——这不是 bug，是环境前提。

#### ★ 端口陷阱：`local.ini` 与 `server7_local.bin` 必须同时改

客户端有**两个**地方记录本地服端口，手工起内核时极易只改一个：

| 文件 | 作用 | 谁改写 |
|---|---|---|
| `local.ini` | 客户端**直连**用的服务器列表（选服后立即用） | `rewrite_local_ini()` |
| `server7_local.bin` | HTTP 劫持返回的列表（点「刷新列表」才拉） | `rebuild_server7_bin()` |

**客户端优先读 `local.ini`**。只更新 `server7_local.bin` 时，界面上的列表是新的，
但点进去连的还是 `local.ini` 里的旧端口：

```
现象：选服界面 → 「正在连接服务器中....」→ 「连接服务器失败」
抓包：TCP 127.0.0.1:18484 127.0.0.1:15829 SYN_SENT   ← 连的是旧端口
      而内核实际在 3134 监听，3134 上没有任何连接
```

这个坑特别隐蔽，因为：

- 列表能正常刷新（`server7_local.bin` 是新的），看不出问题；
- 界面显示的服务器名完全正确；
- 只有**抓包**才看得到它在连一个不存在的端口。

> `singleplayer.py` 里两个都会改（`rewrite_local_ini` + `rebuild_server7_bin`），
> 所以走它的正常流程不会踩。**手工起内核**（调试、自动化脚本）才会漏。
> 修法：`singleplayer.rewrite_local_ini(client_dir, port)`。

#### 跑通后的完整链路（内核日志实录）

```
② Hello PASS ✓（16 ed 4b）
③ op1  → 登录: abcd123
③ op3  → 建角: 卡萨丁开始（icon=1 color=0 major=0 → 造型 111）
③ op2  → 选角进入 id=1
→ 场景 永泽城(1001)：SceneM + AddNpc×4 + GotoM(250,58) + 随行宠物
进世界：PropInit + Resist/Power + 养成同步 + 招式9 + 场景 + GotoM
+ 宠物(梦想企鹅 shape=4005) SummonInfo + SummonSkill×6
+ 背包 ItemAdd×17（背包 pos=20.. 战斗栏 pos=100..）
③ op61 → 服务器时间查询 → 回 S2CTime(192)
③ op88 → 小地图 NPC 标记 → 回 AddMinimapNpc(182)：4 条
③ op129 → 查看宠物资质 → 回 PropView(51)：资格[1797,1792,1880,1828,1909]
③ op54 → 移动 (250,58)→(249,66) [第 669 次]
```

TCP：`127.0.0.1:3134 ← 127.0.0.1:18519 Established`

画面确认：标题栏 `梦想世界 - 本地测试服`、左上坐标 `永泽城 X:249 Y:68`（与日志吻合）、
地图/NPC/建筑完整渲染、底部聊天栏与功能按钮齐全。

三张过程截图存于 `截图/`：

| 文件 | 阶段 | 说明 |
|---|---|---|
| `真机_连接服务器失败.png` | 端口陷阱 | 列表刷新正常但连不上（连的是旧端口） |
| `真机_正在连接服务器.png` | 修复后 | 「正在连接服务器中....」 |
| `真机_进入世界_永泽城.png` | 成功 | 进入永泽城，坐标 X:249 Y:68 |

#### 离线对照：真机之外的第二道防线

真机要验的核心是「服务端发的帧，客户端能不能解开」。这个问题**不需要 GUI**——
`tests/_test_client_decode.py` 用客户端解码语义复现一遍，喂服务端真实帧：

| 帧 | 客户端语义（按 `OVERLAY` 实际布局） | 断言 |
|---|---|---|
| `Hello` | `seed(4) + serverNum(2)` | 解出 seed 且吃满 |
| `SceneM` | `scene(2) mapid(2) minimap(2) x(2) y(2) name(C串)` | 六字段全对且吃满 |
| `WarStart` | `scene(2) alineup(1) elineup(1) wtype(1) x(2) y(2)` | 坐标与阵营标记正确 |
| `WarAdd` | `id(1) shape(2) desc(D) bf(allied=7,…) owner_id(1) cls(1) grade(1) name(C串)` | `allied` 从**位域**取 |

> **为什么这比 e2e 强**：e2e 是「服务端按自己的 `OVERLAY` 解自己发的帧」——自证。
> 这里是「客户端代码解服务端的帧」——交叉验证。`OVERLAY` 写错了，e2e 照样绿，
> 这个测试会红。

它实际抓到了三个布局错误（都是「推测的字段顺序」写错）：

| 帧 | 推测 | 实际 | 症状 |
|---|---|---|---|
| `SceneM` | `mapid(4) + name + x + y` | `scene(2) mapid(2) minimap(2) x(2) y(2) name` | `mapid=65601537`、剩 8B |
| `WarStart` | `scene(4) + a/b/c + 坐标` | `scene(2) alineup(1) elineup(1) wtype(1) x(2) y(2)` | 越界 |
| `WarAdd` | `id(4) shape(2) allied(1) name` | `id(1) shape(2) desc(D) bf(…) …` | `id=28516` |

第三个还揭示 `allied` 在**位域**里（bit7、MSB-first），不是独立字节。

> 教训：**推测的字段顺序会错，字节流不会撒谎**。
> 任何「我觉得应该是这样」的布局，都该用一次真实字节验证。

#### 真机不可替代的部分

离线对照能验协议，但验不了：渲染（画面/动画/UI）、交互时序（点击响应、菜单弹出）、
客户端本地状态机（登录流程、场景切换副作用）。这些仍需人工真机确认。

两道防线的分工：**离线对照在真机之前把协议层问题清掉，真机只用来验表现层**。

### 12.3 测试隔离三条军规

1. **存档隔离**：`_e2e_m4.tmp_savedir()` 复制真实存档到 tmp；并归一化易变项
   （剥 `tasks`、`move_count` 归零），否则一跑就覆盖真机进度。
2. **帧数不写死**：进世界序列长度随存档状态变（招式数 / 地图 NPC 数 / 背包件数），
   一律 `drain_quiet()` 抽干或「读到某 op 为止」。
3. **数值从服务端推**：伤害 = `srv._hero_attack_damage()`、招式帧数 = `2 × len(srv.magics)`、
   防御回合受击 = `ceil(ENEMY_ATTACK_DMG/2)`。

---

## 13. `build_*` ↔ `OVERLAY` 一致性审计

第 2.4 节是「客户端 `Unpack*` ↔ 覆盖表」；本节是它的**服务端镜像**：验证每个
`build_*` 真正跑出来的字节，能否被 `tap.OVERLAY` 干净反解。

**为什么必要**：`OVERLAY` 的 docstring 明说「逐字段对齐 `build_*`」。但 `build_*` 改了
而覆盖表忘改，调试台会把字段读错位——这种漂移**不会报错**，只在可视化里悄悄串字段。

审计器 `_audit_build_overlay.py` 用哑参真跑每个 `build_*`、剥帧头、用 `OVERLAY` 反解。
结论分级：`OK` / `HAS_DYN` / `NO_OVERLAY` / `MISMATCH` / `CALL_FAIL`。

最近一次结果（2026-10-19 收口后，55 个 `build_*`）：

```
build_* ↔ OVERLAY 一致性：OK 55 / MISMATCH 0 / NO_OVERLAY 0 / HAS_DYN 0 / CALL_FAIL 0
```

收口前是 `OK 53 / NO_OVERLAY 1 / CALL_FAIL 1`。那两项**是真实缺口**，而且当时回归是绿的——
因为审计器的退出码只按 `MISMATCH` 判定（`return 0 if mismatch == 0 else 1`），
`NO_OVERLAY` / `CALL_FAIL` 都不会让 `_run_all.py` 变红。这正是「漂移不报错、
只在可视化里悄悄串字段」的现代版本：

| 结论 | 函数 | 当时的实情 | 现在 |
|---|---|---|---|
| `NO_OVERLAY` | `build_war_summon`（**op129**） | `tap.OVERLAY` 里没有 129 条目 → 调试台看不到「宠物被标记为可指挥召唤单位」这一帧。而它**每场战斗必发** | 已补 `129: ('S2CWarSummon', 'id:1 op:1')` |
| `CALL_FAIL` | `build_map_summon`（**op105**） | 审计器 `_LIST_SHAPES` 只有 3/5 元组，`build_map_summon` 要 **4 元组** `(style, shape, name, title)` → 必然 `ValueError`；且 op105 也不在 `OVERLAY` 里 | 已补显式哑参 + `105: ('S2CMapSummon', 'owner_id:4 flag:1 n:1 entries:T')` |

> **只加 4 元组形状是不够的**：形状对了但 `name=0`（int 没有 `.encode`）仍会 `CALL_FAIL`。
> 所以 `MANUAL_DUMMY` 里给的是 `(1, [(0, 4005, 'x', '')])` —— 名字/称号必须是 `str`。

### 13.1 退出码口径（2026-10-19 收紧）

```python
bad = mismatch + no_ov + call_fail
return 0 if bad == 0 else 1
```

`HAS_DYN` 仍算「合法但宽度无法静态确定」，不判失败。`NO_OVERLAY`（缺条目 → 只有 hex）
与 `CALL_FAIL`（跑不通/反解异常）都属于「三角一致性缺一环」，一并判红。

配套回归：`tests/_test_dispatch.py` 新增两条正向断言——
op129/op105 必须**有**覆盖表条目且字节能干净反解（不能只靠审计器兜底）。
已做过负向验证：手动删掉 `OVERLAY[105]` 后该测试返回 1 并报
`✗ op105 S2CMapSummon: tap.OVERLAY 缺 op105 条目`。

两个 op 的实际线序（已按客户端解析器逐字节验证）：

```
op105 S2CMapSummon : owner_id(4) flag(1) n(1)
                     每只: style(1) shape(2) desc(D) namelen(1)+name titlelen(1)+title?
                     实测 body 01000000010100a50f00017800 → owner=1 flag=1 n=1
                     条目 style=0 shape=4005 name='x' title=''，13B 吃满
op129 S2CWarSummon : id(1) op(1)      实测 body 03 81 fa 00 → id=250 op=0
```

> `n` 之后的条目是**循环结构**，现已用 `L{count:n:...}` 结构化展开（2026-10-20，见 §11.2.2），
> 不再是 `T` 原始块。实测多宠物（n>1）、带称号、空列表（n=0）、中文名均能正确解出。

其中 9 个动态布局 op（17/48/65/96/164 相关发射体）由 `_OV_DYN` 真验证，不再只是跳过。

本轮补的覆盖表缺口（详见 `tap.py`）：

| opcode | 函数 | 修复 |
|---|---|---|
| 148 | `build_npc_sell_list` | `mode:1 items:L{sentinel:shape:...}`（哨兵循环，结构化展开） |
| 149 | `build_npc_learn_list` | `mode:1 icost:2 entries:L{sentinel:skid:...}`（结构化展开） |
| 16 | `build_prop_init` | 132B 模糊块 → 67 个具名字段（220B） |
| 53 | `build_pet_key` | 6B → `pid:4 key:2 birthkey:1` |
| 51 | `build_pet_prop_view` | 还原为 67 字段合法形态 |
| 121 | `build_war_protect` | 新增 `protector_id:1 protege_id:1` |
| 151 | `build_npc_details` | 新增 `pos:1 desc:s` |
| 155 | `build_npc_make_cost` | 新增 `iTili:2 iMoney:2 sklv:2`（op104 的应答） |
| 160 | `build_make_complete` | 新增 `shape:4 id:4 amount:1 desc:s`（op102 的应答） |
| 65 | `build_skill_level_magic` / `build_skill_equip` | `_OV_DYN[65]`：按 sktype 分八分支 |
| 96 | `build_scene_m` | `_OV_DYN[96]`：scene 首字段三态分派（普通/65535/65534） |

> 147/148/149 的列表已用 `L{}` 结构化展开（2026-10-20，见 §11.2.2），不再是原始块；
> 循环/哨兵语义写进 `_OV_NOTE`。

---
