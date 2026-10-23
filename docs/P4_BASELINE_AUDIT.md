# P4-00 基线审计：P1–P3 验收与差距

> 2026-10-23｜采集方式：`python 服务端源码/tests/sim/_p4_audit.py`
> 基线回归：**59/59 通过**（262.7s，退出码 0）｜基线日志 `/tmp/p4_baseline.log`
> 原则：**不采信历史文档结论**。下面每个数字都能用上面那条命令重跑复现。

## 1. 环境事实（先说要紧的）

| 项 | 实况 | 影响 |
|---|---|---|
| 版本控制 | **不是 git 仓库**（`fatal: not a git repository`） | 无 diff/回滚/基线快照。改动前只能手工备份，**这是 P4 最大的工程风险** |
| 项目根 | `C:\Users\Administrator\MX` | 无根 `docs/`，本轮新建（P4 文档落在 `MX/docs/`） |
| 解析文档 | 实为 `协议资料/报告/模拟服全链路代码解析.md` | 指令里写的《模拟服全链路代码解析(6).md》**不存在**；(6) 疑为版本号，实际只有一份主文档 + `docs/` 九册 |
| 服务端内核 | `服务端源码/tests/sim/m4_server.py`（12040 行） | 注意路径里多一层 `tests/` |
| 客户端源码 | `客户端源码/src/`（`data/` 有 9 张真值表） | `equipdata.py` **不存在**（P3-01 已结论，装备无数值） |

## 2. P3 实际完成度：**3 / 8**（P3-03 已于本轮补齐，见 §2.1）

| 工作包 | 点名模块 | 实际 |
|---|---|---|
| P3-01 角色养成 | `progression_service.py` / `equipment_rules.py` / `equip_data.py` | ✅ **完成**（168 / 123 / 295 行） |
| P3-02 宠物养成 | `pet_progression.py` / `pet_skill_service.py` / `pet_skill_data.py` | ✅ **完成**（270 / 214 / 460 行） |
| P3-03 高级战斗 | `battle_rng.py` / `combat_effects.py` / `buff_data.py` / `enemy_ai.py` | ✅ **完成**（265 / 506 / 624 / 178 行；`partial` — 状态数值覆盖 29/284） |
| P3-04 动态世界 | `spawn_rules.py` / `world_events.py` | ❌ **不存在** |
| P3-05 经济 | `economy_service.py` / `economy_ledger.py` | ❌ **不存在** |
| P3-06 单人挑战 | `challenge_data.py` / `challenge_engine.py` | ❌ **不存在** |
| P3-07 调试工具 | — | ❌ 未做（P3-01/02 已备 `progress_stats(explain=True)` / `gm_pet_stats`） |
| P3-08 工程稳定性 | — | ❌ 未做 |

> 指令 §4.3 的规则在这里落地：P3-03（战斗状态/AI）→ P4-01/02 不阻塞但**战斗数值校准必须有状态机**；
> P3-06（挑战）→ **P4-04 的挑战整合阻塞**。已按「P4 必需的先补齐」排期。

## 3. 功能差距矩阵

| 功能 | 当前状态 | 客户端证据 | 服务端证据 | 测试证据 | 缺口 | P4 优先级 | 阻塞 | 处理 |
|---|---|---|---|---|---|---|---|---|
| 角色属性 | `partial` | `dlgstate.ATTR_POINTS` | `progression_service.final_stats` | `_test_p3_progression` 全绿 | 装备加成恒 0（无 `equipdata`） | P0 | 否 | 校准（P4-01）/ 保持标注 |
| 角色升级 | `implemented` | `hero.py` 属性表 | `_award_battle_exp` | `_e2e_m9` PASS | — | — | — | 不动 |
| 宠物属性 | `simulated` | `petfightdata` 公式 | `pet_progression` 三层 | `_test_p3_pet_growth` 80 项 | 加点换算 SIM | P1 | 否 | 标注清楚即可 |
| 宠物技能 | `implemented`（机制）/ `blocked`（来源） | `petskill.DATA` 404 条 | `pet_skill_service` | 同上 | 要诀→技能映射不存在 | P2 | 否 | 暂缓 |
| 战斗引擎 | `implemented` | `war.py` / `frames.SPEC` | `_dispatch_locked` 战斗分支 | `_test_battle_flow` / `_e2e_m4` / `_test_p3_03_combat` | **无状态效果数值**（机制已有，数值覆盖 29/284） | **P0** | 否 | P3-03 已补机制；数值待 P4-01 校准 |
| 物伤公式 | `simulated` | 反编译 `_dmg` 形状 | `damage_model` | `_test_damage` 22 项 | A/D/P/Lt 映射是拟合值 | P0 | 否 | 校准（P4-01） |
| 会心 | `simulated` | — | `ENEMY_HURT_BS_BY_SHAPE` | `_test_damage` | 仅 1 个造型有样本（n=45） | P1 | 否 | 保持 PARTIAL 标注 |
| 任务 1–16 | `partial` | `quest_data` / `_docx_text` | `quest_data.py` 1697 行 | `_test_quest_716` | 17–22 缺地图/NPC | P1 | 否 | 静态可达性校验（P4-04） |
| 任务 17–22 | `blocked` | 一览表有，坐标缺 | `NOT_IMPLEMENTED` | `_test_quest_716` 断言不可得 | 缺天空要塞 mapid + 6 条枢纽 NPC 坐标 | P2 | 否 | 暂缓，等资料 |
| 制作 | `implemented` | `craftdata` | `_handle_npc_make` | `_test_craft` | 材料配比 SIM | P1 | 否 | 标注（P4-01） |
| 经济 | 无 `FEATURE_STATUS` 条目 | `itemdata` 价格 | 散在 `_handle_npc_*` | `_test_item` | **无流水、无防重复事务、无统计** | **P0** | **是** | **P4-05**（P3-05 未做，需补） |
| 挑战 | ❌ 完全没有 | 无客户端入口证据 | — | — | P3-06 未做 | P1 | **P4-04 阻塞** | 先补 P3-06 最小闭环 |
| 存档 | `implemented` | — | `SCHEMA_VERSION=2` + `MIGRATIONS={0,1}` | `_test_save_async` | **无备份轮换、无损坏检测** | **P0** | 否 | P4-06 |
| 启动 | `implemented`（`singleplayer.py` 591 行） | — | `singleplayer.py` | `_test_live_port_sync` | 无健康检查聚合、无失败诊断分类 | P1 | 否 | P4-08 |

## 4. FEATURE_STATUS 现状（27 项，P3-03 后）

| 状态 | 数量 | 键 |
|---|---:|---|
| `implemented` | 10 | battle / **battle_rng** / craft / craft_charge / equip_slot_rule / hero_levelup / pet_panel / pet_skill_manage / story_point / **war_settle_once** |
| `simulated` | 8 | craft_levelup / dmg_model / enemy_hurt_bs / **enemy_ai** / huoli_regen / pet_battle_summon / pet_levelup / team_dummy |
| `blocked` | 3 | equip_repair / pet_equip_effect / pet_skill_learn |
| `partial` | 3 | **battle_effect** / hero_progression / task_chain |
| `not_implemented` | 2 | alchemy / pet_aptitude_adjust |
| `ignored_no_counterpart` | 1 | social |

> 加粗的 4 项是 P3-03 新增。`FEATURE_TITLE` 同时补齐了 `pet_panel` / `pet_battle_summon`
> 两个历史缺失的中文标题（审计发现 GM 面板会显示裸键名）。

**发现两个小缺陷（本轮顺手修）**：`pet_panel` / `pet_battle_summon` 缺中文标题，
GM 面板会显示裸键名（`feature_label` 回落）。

## 5. 回归基线

59 步，五层：`proto` 3 / `dispatch` 40 / `smoke` 7 / `doc` 3 / 其余。全绿。

| 指标 | 值 |
|---|---|
| steps_total | 59 |
| build_* 个数 | 58 |
| C2S 覆盖率 | 134 / 134 |
| DISPATCH_PRE_WAR_OPS | 58 |
| 显式忽略的 C2S opcode | 72 |
| `_test_*.py` 文件数 | 79（挂进回归的 59 步里有 40 步是进程内测试） |

## 6. 风险清单（按「会不会让玩家丢东西」排序）

| # | 风险 | 严重度 | 现状 | 归属 |
|---|---|---|---|---|
| R1 | **无版本控制** | **高** | 改动无法 diff/回滚，回归红时定位成本高 | 全程 → P4-08 需给发布清单 |
| R2 | ~~战斗随机源不可复现~~ | ~~高~~ | **已修（P3-03）**：12 处模块级 `random` 全部收口到实例 `battle_rng`（11 条用途流，同种子逐位可复现） | 关闭 |
| R3 | ~~胜利结算可能重入~~ | ~~高~~ | **已修（P3-03）**：`_war_settle_count` 显式计数 + 六路入口全测，恒 ≤1；顺带修掉「N 只同灭 → N 次 TaskAdd」 | 关闭 |
| R4 | **经济无流水/无幂等** | **高** | 重复提交靠各调用点自觉 | P4-05 |
| R5 | **存档无备份轮换** | 中 | 原子替换有了，但只有一份 `role_*.json` | P4-06 |
| R6 | 挑战系统缺失 | 中 | P4-04 阻塞 | P3-06 |
| R7 | 4 个孤儿测试脚本 | 低 | `_test_pet.py` / `_test_gm.py` 路径写死；`_test_provenance.py` 断言过期；`_test_save_async` 修过 | P4-07 |

## 7. 真实客户端验收状态

**未验证。** 本轮只跑了 socket 级与进程内测试：

- `_e2e_m4/m5/m7/m9/m11`、`_test_war2`、`coverage_smoke` 都是**脚本客户端**（自己拼字节），
  证明「服务端按协议回帧」，**不证明**「真实客户端画面正确」。
- 真实客户端需要 `singleplayer.py` 拉起 `mx.exe` + GUI 观察，本轮**未运行**。
- 因此凡涉及「画面/交互」的结论，本报告一律标 **未验证**，不冒充。

## 8. 本轮执行顺序（已与用户确认）

```
P4-00 审计（本文件）
  ↓
P3-03 高级战斗（用户指定优先：状态效果 / 敌方 AI / 固定随机种子 / 胜负只结算一次）
  ↓
P4-01 规则证据库 → P4-02 状态一致性 → 其余 P4 工作包
```

> 为什么先补 P3-03 而不是直接 P4-01：P4-01 要校准「伤害/命中/会心/速度排序」，
> 而这些值现在**没有稳定的观测口径**——战斗里随机源不固定、结算可能重入，
> 校准出来的数字第二天就可能因为一次重入而漂移。**先让战斗可复现，才有校准的意义。**

### 8.1 P3-03 完成情况（2026-10-23，本轮已收口）

| 子项 | 状态 | 落地 |
|---|---|---|
| 固定随机种子 | `implemented` | `battle_rng.py`：12 处 `random.*` → 11 条用途流，同种子逐位可复现（流间隔离 / 每场重置 / 可退化） |
| 状态效果 | `partial` | `combat_effects.py` + `buff_data.py`（284 条真值）：机制全通，**数值覆盖 29/284** |
| 敌方 AI | `simulated` | `enemy_ai.py`：`pick_target` / `decide_action` / `explain`，默认策略逐位复刻既有行为 |
| 胜负只结算一次 | `implemented` | `_war_settle_count` + 六路入口全测；顺带修掉「N 只同灭 → N 次 TaskAdd」 |

R2（随机源不可复现）、R3（结算重入）**关闭**。回归 59 → **60 步全绿**，
单测 **101 项断言**，变异验证 **5/5 判红**。

对 P4-01 的影响：现在「同种子重跑整场战斗」能拿到逐位相同的伤害/会心/掉落序列，
校准可以复现了。**剩余阻塞**是状态数值（29/284），P4-01 只能校准这 29 条，
其余 255 条状态在证据库中应标 `UNKNOWN`。
