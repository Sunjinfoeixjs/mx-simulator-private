# P5-00 基线审计：P1–P4 实际交付与 P5 开工差距

> 生成时间：2026-10-23｜执行者：本轮 P5 开发
> 采集命令：`python 服务端源码/tests/sim/_p5_audit.py`（`--md` / `--json` 可换格式）
> 基线回归：**60/60 通过**（267.3s，退出码 0，`steps_failed=0`）
> ｜R1 修复后（新增 `_test_save_paths`）：**61/61 通过**（首轮 275.1s；最终态复跑 262.4s）
> 原则：**不采信任何历史文档的结论**。下面每个数字都能用上面那条命令或紧随其后的复现步骤重跑。
> 文档与代码冲突时以代码为准，并把「文档过时」本身记为该文档的缺陷。

---

## 0. 结论摘要（先说要紧的）

| # | 结论 | 证据 |
|---|---|---|
| 1 | **P3 完成 3/8、P4 完成 1/9（仅审计）、P5 完成 1/10（本文档）** | §3 阶段到位率，按 `os.path.isfile` 实测 |
| 2 | **发现并修复一个 P0 路径缺陷**：`CLAW` 少算一层，存档/日志目录多套一层 `服务端源码`。存档侧历史 `role.json` 会被静默忽略；**日志侧已实证损坏验收闸门**（闸门读 1288 份旧日志、最新 10-22 17:28，服务端却写到另一目录、最新 10-23 17:16） | §9 R1 / §9.1 / §9.2；修法与验证见 `docs/P5_SAVE_COMPATIBILITY.md` |
| 3 | **真实客户端验收 = 未执行**，不是「通过」 | §8：`人工GUI验收_真实客户端.md` 23 项全为 ⬜、0 项 ✅ |
| 4 | **无版本控制**（不是 git 仓库）→ 无 diff、无回滚、无基线快照 | §2 |
| 5 | 存档结构实测 **37 个落盘键**，迁移链 `0→1→2` 完整，迁移补的字段**无一孤儿** | §5 |
| 6 | 协议面稳定：`DISPATCH_PRE_WAR_OPS` 58、`frames.SPEC` 30、`build_*` 58、C2S 覆盖 134/134 | §6，取回归 `=== STATS ===` |
| 7 | 测试资产：`_test_*.py` **80 → 81** 个、断言点 **2327**（+ 新增 11）、回归 **60 → 61** 步 | §7 |
| 8 | **不得把「C2S 134/134 覆盖」当作「玩法完成率」** — 前者是分派口径，后者见 27 项矩阵（implemented 仅 10 项） | §4、§12 |

---

## 1. 采集方式：可复现

审计脚本 `服务端源码/tests/sim/_p5_audit.py` 只做两件事：**读文件 / 读真实字典**，不解析任何计划或报告。

| 采集项 | 方法 | 是否「实测」 |
|---|---|---|
| 模块是否存在 | `os.path.isfile` | ✅ |
| 模块行数 | 真读文件数行 | ✅ |
| 文档是否存在 | `os.path.isfile` + 字节数 | ✅ |
| `FEATURE_STATUS` | `import m4_server` 读真实字典 | ✅ |
| 存档字段 | `ast` 解析 `_persist()` 里 `data = {…}` 的键 + `data['x']=…` 追加键 | ✅ |
| 存档版本/迁移链 | 读 `SCHEMA_VERSION` / `MIGRATIONS` | ✅ |
| 协议分派 | `len(DISPATCH_PRE_WAR_OPS)` / `len(frames.SPEC)` / `build_*` 计数 | ✅ |
| 测试步数 | 静态读 `sim/tests/_run_all.py` 的 STEPS 元组 | ✅ |
| 客户端证据 | `os.path.isfile(mx.exe)` + 验收记录里的勾选符 | ✅ |

复现：

```bash
cd 服务端源码/tests/sim
python _p5_audit.py            # 人读
python _p5_audit.py --md       # 本文档里的表格
python _p5_audit.py --json     # 机器读
```

---

## 2. 环境事实

| 项 | 实况 | 影响 |
|---|---|---|
| 项目根 | `C:\Users\Administrator\MX` | 无根 `package.json` / 无构建系统，纯脚本工程 |
| 版本控制 | **不是 git 仓库**（无 `.git`） | **最高工程风险**：改动无 diff、坏了无回滚、无基线快照 |
| 服务端内核 | `服务端源码/tests/sim/m4_server.py`（12040 行） | 路径里多一层 `tests/`，指错不报错、只会「跑 0 步」 |
| 启动器 | `服务端源码/tests/sim/singleplayer.py`（591 行） | 唯一正规入口，拉起 `mx.exe` + 内核 |
| 回归入口 | `服务端源码/tests/sim/tests/_run_all.py` | ⚠ 与它跑的测试（`服务端源码/tests/`）**同名不同层** |
| 客户端 | `D:\mxsj2013\client\mx.exe` **可达** | 可执行，但 GUI 验收未运行（§8） |
| 文档根 | `MX/docs/`（P4/P5 文档落这里）+ `协议资料/报告/`（历史报告） | 两处并存，`docs/` 此前几乎为空 |

---

## 3. 阶段到位率

### 3.1 P3：完成 3/8

| 工作包 | 点名模块 | 实际 | 行数 |
|---|---|---|---|
| P3-01 角色养成 | `progression_service.py` / `equipment_rules.py` / `equip_data.py` | ✅ 3/3 | 586 |
| P3-02 宠物养成 | `pet_progression.py` / `pet_skill_service.py` / `pet_skill_data.py` | ✅ 3/3 | 944 |
| P3-03 高级战斗 | `battle_rng.py` / `combat_effects.py` / `buff_data.py` / `enemy_ai.py` | ✅ 4/4 | 1573 |
| P3-04 动态世界 | `spawn_rules.py` / `world_events.py` | ❌ 0/2 | — |
| P3-05 经济 | `economy_service.py` / `economy_ledger.py` | ❌ 0/2 | — |
| P3-06 单人挑战 | `challenge_data.py` / `challenge_engine.py` | ❌ 0/2 | — |
| P3-07 调试工具 | （指令未点名模块） | ❌ 未做 | — |
| P3-08 工程稳定性 | （指令未点名模块） | ❌ 未做 | — |

> **P3-07/08 不算「模块缺失」**：它们在指令里是工具/工程类工作包，没有点名模块名。
> 这里如实写「未做」，而不是用「模块不存在」去暗示它们被漏检。

### 3.2 P4：完成 1/9（只有 P4-00 审计）

| 工作包 | 点名模块 | 模块到位 | 对应文档 |
|---|---|---:|---|
| P4-00 基线审计 | — | — | ✅ `P4_BASELINE_AUDIT.md`（9689 B） |
| P4-01 规则证据库 | `rule_evidence.py` | ❌ | ❌ `P4_RULE_EVIDENCE.md` |
| P4-02 状态一致性 | `state_sync_audit.py` | ❌ | ❌ `P4_STATE_CONSISTENCY.md` |
| P4-03 数值校准 | `balance_simulator.py` | ❌ | ❌ `P4_BALANCE_CALIBRATION.md` |
| P4-04 内容校验 | `content_validator.py` | ❌ | ❌ `P4_CONTENT_VALIDATION.md` |
| P4-05 经济安全 | （指令未点名模块） | — | ❌ `P4_ECONOMY_SAFETY.md` |
| P4-06 存档完整性 | `save_integrity.py` | ❌ | ❌ `P4_SAVE_INTEGRITY.md` |
| P4-07 调试/测试治理 | （指令未点名模块） | — | ❌ `P4_DEBUGGING.md` |
| P4-08 发布质量 | `release_check.py` | ❌ | ❌ `P4_RELEASE_QUALITY.md` |
| （验收） | — | — | ❌ `P4_ACCEPTANCE.md` |

### 3.3 P5：完成 1/10

| 工作包 | 模块 | 文档 |
|---|---|---|
| P5-00 基线审计 | `_p5_audit.py` ✅ 492 行 | ✅ 本文档 |
| P5-01 完整流程验收 | — | ❌ `P5_GAMEPLAY_ACCEPTANCE.md` |
| P5-02 协议兼容 | `protocol_golden.py` ❌ | ❌ `P5_PROTOCOL_COMPATIBILITY.md` |
| P5-03 存档兼容 | `save_integrity.py` ❌ | ❌ `P5_SAVE_COMPATIBILITY.md` |
| P5-04 自动化 | — | ❌ `P5_AUTOMATION.md` |
| P5-05 长时间稳定性 | — | ❌ `P5_STABILITY_REPORT.md` |
| P5-06 体验收敛 | — | ❌ `P5_GAMEPLAY_QUALITY.md` |
| P5-07 诊断 | `diagnostic_export.py` ❌ | ❌ `P5_DIAGNOSTICS.md` |
| P5-08 发布管理 | `version_info.py` / `release_check.py` ❌ | ❌ `P5_RELEASE_MANAGEMENT.md` |
| P5-09 最终验收 | — | ❌ `P5_FINAL_ACCEPTANCE.md` |

---

## 4. 功能完成度矩阵（27 项，指令 §4.3 口径）

**这张表才是「玩法完成率」，不是分派覆盖率。** 27 项来自 `m4_server.FEATURE_STATUS`（`import` 后读真实字典，非文档抄录）。

状态图例取自代码内 `FEATURE_STATUS_LEGEND`（7 档，逐字）：

| 状态 | 含义 |
|---|---|
| `implemented` | 已实现，有回归覆盖 |
| `partial` | 部分实现 |
| `simulated` | 已实现，规则为模拟参数（无真值来源） |
| `blocked` | 机制就位但缺外部真值（数据/协议证据不可得，等资料） |
| `not_implemented` | 协议收得到但业务未写（点了无反应，非 bug） |
| `preview_only` | 只在预览报数、实际操作不生效（UI 给了假承诺，最隐蔽） |
| `ignored_no_counterpart` | 单机无对手方，显式接收并忽略（非缺口） |
| `display_only` | 数值与下发都有，但本版客户端没有消费入口（只显示） |

> 现存 27 项用到 6 档；`preview_only` / `display_only` **当前无人使用**（不是缺失，是确实没有这类功能）。

### 4.1 矩阵

| 功能名称 | 当前状态 | 代码证据 | 客户端证据 | 测试证据 | 存档影响 | 阻塞程度 | 修复方案 |
|---|---|---|---|---|---|---|---|
| 战斗引擎 `battle` | `implemented` | `_dispatch_locked` 战斗分支 + WarTurn 状态机 | `war.py` / `frames.SPEC` | `_test_battle_flow` / `_test_battle_core` / `_e2e_m4` / `_test_p3_03_combat` | 战斗态不落盘（`in_war` 未持久化）→ 中途崩溃=白打一场 | 否 | P5-01 流程 B、P5-05 soak |
| 战斗随机源 `battle_rng` | `implemented` | `battle_rng.py` 265 行，11 条用途流 | — | `_test_p3_03_combat` 106 断言点 | 种子不落盘（每场重置）→ 重放需外部记种子 | 否 | P5-02/05 固定种子跑 |
| 战斗状态效果 `battle_effect` | `partial` | `combat_effects.py` 506 行 + `buff_data.py` 624 行 | 284 条状态名来自客户端 | `_test_p3_03_combat` 106 断言点 | 状态不落盘（战斗内） | **是：数值覆盖 29/284** | P5-06 明示「其余 255 条无数值」 |
| 敌方 AI `enemy_ai` | `simulated` | `enemy_ai.py` 178 行（`pick_target` / `decide_action`） | 无客户端对应物 | `_test_p3_03_combat` | 无 | 否（无真值） | 保持 `simulated` |
| 胜负只结算一次 `war_settle_once` | `implemented` | `_war_settle_count` + 六路入口 | — | `_test_p3_03_combat` 六路全测 | 结算结果落 `exp`/`coins`/`items` ✅ | 否 | P5-03 验证「结算后立即落盘」 |
| 物伤公式 `dmg_model` | `simulated` | `damage_model.py` | 反编译 `_dmg` 形状 | `_test_damage` 31 断言点 | 无 | **是：A/D/P/Lt 为拟合值** | P4-01/03 未做，P5 不校准，只标注 |
| 会心（按造型）`enemy_hurt_bs` | `simulated` | `ENEMY_HURT_BS_BY_SHAPE` / `_CHANCE` | — | `_test_damage` | 无 | **是：仅 1 造型有样本（n=45）** | 同上 |
| 角色最终属性 `hero_progression` | `partial` | `progression_service.final_stats` | `dlgstate.ATTR_POINTS` | `_test_p3_progression` | `hero_res`/`attrs`/`magics` ✅ | **是：装备加成恒 0**（无 `equipdata`） | P5-06 面板/日志明示「装备不加属性」 |
| 主角升级 `hero_levelup` | `implemented` | `_award_battle_exp` / `_hero_level_up` | `hero.py` 属性表 | `_e2e_m9` | `exp`/`next_exp`/`grade`/`points` ✅ | 否 | — |
| 装备槽位/等级门 `equip_slot_rule` | `implemented` | `equipment_rules.py` 123 行 + `equip_data.py` 295 行 | `itemdata.EQUIP2POS` + `ITEMTYPE` | `_test_p3_progression` | `items` 的 `pos` ✅ | 否 | — |
| 装备修理/耐久 `equip_repair` | `blocked` | 无实现 | 文案有（`dlgmake.py` L232）、协议无 repair 命名 | — | — | **是：缺协议 + 物品无耐久字段** | 保持 `blocked`，不做假承诺 |
| 宠物升级 `pet_levelup` | `simulated` | `pet_progression.py` 270 行（三层属性） | `petfightdata` 公式 | `_test_p3_pet_growth` 67 断言点 | `pets[].level/exp/attr_lv/point` ✅ | 否（加点换算 SIM） | 保持 `simulated` |
| 宠物技能表增删 `pet_skill_manage` | `implemented` | `pet_skill_service.py` 214 行 | `petskill.DATA` 404 条 | `_test_p3_pet_growth` | `pets[].skills` ✅ | 否 | — |
| 宠物学技能（要诀）`pet_skill_learn` | `blocked` | — | **要诀→技能映射不存在** | — | — | **是：真值不可得** | 保持 `blocked` |
| 宠物装备进战斗 `pet_equip_effect` | `blocked` | — | op54 线序已有 | `_test_pet_equip` 83 断言点（装备动作链路） | `pets[].equips` ✅ | **是：装备不进战斗数值** | 保持 `blocked`；P5-01 流程 D 如实记录 |
| 宠物资质调整 `pet_aptitude_adjust` | `not_implemented` | `aptitude_adjust` 仅占位 | 无 | — | — | 否 | P5-01 记为「点了没反应」（设计如此） |
| 宠物面板操作 `pet_panel` | `implemented` | 宠物面板协议链 | 客户端面板 | `_test_pet` / `_test_pet_instance` | `pets` 全量 ✅ | 否 | — |
| 宠物战斗召唤/召回 `pet_battle_summon` | `simulated` | `_war_summon` / `_hero_war_summon` | — | `_test_pet_instance` | `active_pet_id` ✅ | 否 | — |
| 队友（假人）`team_dummy` | `simulated` | `team.py` + `_team_*` 6 个入口 | `netteam.S2CPlayerTeam` | `_test_team` / `_test_team_protocol` | `team` ✅（`active`/`lineup`/`target`…） | 否（单机无真队友） | — |
| 任务闭环 `task_chain` | `partial` | `quest_data.py` 1697 行 | `quest_data` / `_docx_text` | `_test_quest_chain` 123 / `_test_quest_story4` 73 / `_test_quest_716` | `tasks`/`quests_done`/`story_point` ✅ | **是：17–22 缺坐标与天空要塞 mapid** | P5-01 流程 C 只走 1–16；17–22 记 `blocked` |
| 剧情点 `story_point` | `implemented` | `self.story_point` / `juqing_skills` | `dlg*` 剧情对话框 | `_test_quest_story4` | `story_point`/`juqing_skills` ✅ | 否 | — |
| 制作/合成 `craft` | `implemented` | `_handle_npc_make` + `CRAFT_DATA` | `craftdata` 真值表 | `_test_craft` 60 断言点 | `items`/`coins`/`huoli` ✅ | 否 | — |
| 制作扣费 `craft_charge` | `implemented` | `MAKE_COST_MONEY`/`_HUOLI`/`_SKLV` | op104 预览面板同额 | `_test_craft` / `_test_dispatch`（op155 数值） | `coins`/`huoli` ✅ | 否 | — |
| 工艺升级 `craft_levelup` | `simulated` | `life_lv`/`life_exp` 成长循环（`_handle_npc_make` 内） | — | `_test_craft` §[9]：10 件→5→6 级 / 溢出归零 / 门禁双向 | **`life_lv`/`life_exp` ✅ 已核实不丢** | 否（阈值曲线 SIM） | 保持 `simulated`；**审计已排除「工艺等级不落盘」的猜测** |
| 活力恢复 `huoli_regen` | `simulated` | `_regen_huoli`（2 个调用点） | — | `_test_craft` **直接测 4 次**（含「战斗胜利不重置活力」） | `huoli` ✅；累加器 `huoli_regen_acc` 不落盘（重启归零，可接受） | 否 | 保持 `simulated` |
| 炼药 `alchemy` | `not_implemented` | 收 `C2S_PLAYER_DRUG` 帧 | **无配方数据表**（检索不到 `drugdata`） | `_test_craft` 只有一条「状态必须仍是 `not_implemented`」的守护断言 | — | **是：材料→药品映射不可得** | 保持 `not_implemented` |
| 社交/交易/帮派 `social` | `ignored_no_counterpart` | 显式接收并忽略 | 单机无对手方 | `_test_feature_status` | — | 否（非缺口） | — |

### 4.2 分档统计

| 状态 | 数量 | 键 |
|---|---:|---|
| `implemented` | 10 | `battle` `battle_rng` `craft` `craft_charge` `equip_slot_rule` `hero_levelup` `pet_panel` `pet_skill_manage` `story_point` `war_settle_once` |
| `simulated` | 8 | `craft_levelup` `dmg_model` `enemy_ai` `enemy_hurt_bs` `huoli_regen` `pet_battle_summon` `pet_levelup` `team_dummy` |
| `partial` | 3 | `battle_effect` `hero_progression` `task_chain` |
| `blocked` | 3 | `equip_repair` `pet_equip_effect` `pet_skill_learn` |
| `not_implemented` | 2 | `alchemy` `pet_aptitude_adjust` |
| `ignored_no_counterpart` | 1 | `social` |

`FEATURE_TITLE` 覆盖率 27/27（无裸键名，GM 面板不会显示 `pet_panel` 这种英文键）。

### 4.3 本表与「分派覆盖率」的区别（必须分开看）

| 口径 | 数值 | 能说明 | 不能说明 |
|---|---|---|---|
| C2S 分派覆盖率 | 134 / 134 | 客户端每个上行 opcode 都有落点（含显式忽略） | 落点后面的**业务对不对**、数值真不真 |
| `FEATURE_STATUS` 玩法完成率 | implemented 10 / 27 = 37% | 有回归覆盖的玩法比例 | — |
| 完整可用（implemented + simulated 可玩） | 18 / 27 = 67% | 「点得动、有结果」 | 数值是否为真值 |

> 这就是指令反复强调的那条：**协议全接通 ≠ 玩法做完**。134/134 是分派口径，玩法口径是 10/27。

---

## 5. 存档结构实测

采集方式：`ast` 解析 `_persist()` 中真实构造的 `data = {…}`，外加其后的 `data['_save_tag']` / `data['_schema']`。

| 项 | 值 |
|---|---|
| `SCHEMA_VERSION` | **2** |
| `MIGRATIONS` | `{0: _migrate_0_to_1, 1: _migrate_1_to_2}` |
| 落盘字段数 | **37**（主 dict 35 + 追加 2） |
| 迁移补的字段 | 20 个（`_migrate_1_to_2` 的 `defaults`） |
| 迁移有、落盘无 | **无**（迁移补的字段全部在落盘集合内，无孤儿字段） |

落盘键全表（按源码字面顺序）：

`px` `py` `hero_id` `hero_name` `move_count` `hero_hp` `hero_res` `items` `pets` `pet_hp`
`active_pet_id` `pet_shape` `hero_shape` `coins` `iid_seq` `mapid` `tasks` `story_point`
`quests_done` `juqing_skills` `sys_switch` `sys_data` `mobile` `private_info` `exp`
`next_exp` `grade` `points` `attrs` `magics` `tili` `huoli` `life_lv` `life_exp` `team`
`_save_tag` `_schema`

### 5.1 一次「内存有 / 存档无」的穷举核对（本轮新增）

把 `M4Server()` 实例的 69 个非私有属性与 37 个落盘键做差集，逐个判定是否丢进度：

| 差集里的字段 | 判定 |
|---|---|
| `pet_res`（`m_FP`/`m_MP`） | **不丢**：`_pets_snapshot()` 每个个体都自带 `hp`/`mp`/`fp`/`life`/`tire`，`pet_res` 只是出战宠镜像 |
| `pet_seq`（=1001） | **不丢**：读档时 `self.pet_seq = max(self.pets)` 重算，不会与既有 pid 冲突 |
| `base_skills`（恒 `{}`） | **不丢**：尚未启用的技能挂钩，从未被填充 |
| `life_lv`/`life_exp`（工艺等级） | **已落盘**，审计排除了「工艺升级重启后退回 1 级」的猜测 |
| `enemy_*` / `give_*` / `npc_*` / `cur_npc*` / `await_cmd` / `cmd_deadline` / `have_role` / `role_listed` / `account` | 会话态，设计上不落盘 |
| `host` / `port` / `seed` / `enemies` / `encounter_every` / `turn_timeout` / `encrypt` / `savedir` / `savepath` / `logpath` | 配置，来自命令行/常量 |
| `in_war` / `enemy_hp` / `battle_rng` / `huoli_regen_acc` | **有意的设计取舍**：战斗不跨进程续战；活力累加器归零可接受 |

> 结论：**没有发现「玩出来的进度会丢」的字段**。`in_war` 不落盘是取舍而非 bug，
> 但它带来一个体验面事实：**战斗中掉线 = 已扣的血保留、这场战斗作废**，需在 P5-03 的故障注入里明示。

### 5.2 存档安全的现状（P5-03 的输入）

| 能力 | 有/无 | 证据 |
|---|---|---|
| 原子替换（tmp + `os.replace` + `fsync`） | ✅ | `_save_role_nolock` |
| 进程内互斥 + 跨进程文件锁 | ✅ | `_path_lock` / `_CrossProcessLock` |
| 跨会话「丢更新」告警 | ✅ | `_save_tag` 比对（`_save_now`） |
| 增量脏键 + 异步写线程 | ✅ | `_persist` → `_save_queue` |
| 迁移链 `0→1→2` | ✅ | `migrate_role` 逐级、不跳版、异常不吞 |
| **存档备份（轮换）** | ❌ | 只有一份 `role_<账号>.json` |
| **存档恢复 / 回滚** | ❌ | 无 |
| **完整性校验（checksum/结构校验）** | ❌ | 无 |

---

## 6. 协议与分派

| 指标 | 值 | 来源 |
|---|---:|---|
| `DISPATCH_PRE_WAR_OPS` | 58 | `len()` 实测 |
| `frames.SPEC` | 30 | `len()` 实测 |
| `build_*` 构造函数 | 58 | `dir()` 计数 |
| C2S 分派覆盖 | 134 / 134 | 回归 `coverage_smoke` 的 STATS |
| 显式忽略的 C2S opcode | 72 | P4-00 基线（本轮未复测） |

---

## 7. 测试资产

| 指标 | 值 | 口径 |
|---|---:|---|
| `_test_*.py` | 80 → **81** | 审计时 80；R1 修复新增 `_test_save_paths.py` |
| 回归步数 | 60 → **61** | 审计时 60；R1 修复后新增 `_test_save_paths` 一步 |
| 回归结果 | **61/61 通过** | 审计基线 60/60（267.3s）；加步后 61/61（275.1s） |
| 断言点 | **2327 → 2337** | `check(`/`chk(` 调用数 + 含 `assert` 的行数 + `fails.append(` 数（静态口径） |
| 单文件最多 | 123 | `_test_quest_chain` |

> 断言点口径说明：三项相加是为了兼容三种写法（`check()` 助手 / 原生 `assert` / `fails.append`）。
> 它不是「测试用例数」，也不是「覆盖率」，只用于横向比较各测试文件的密度。
> 若换口径（例如只数 `check`），数字会变，解释力不变。

分布（前 8）：

| 文件 | 断言点 |
|---|---:|
| `_test_quest_chain` | 123 |
| `_test_tap` | 108 |
| `_test_p3_03_combat` | 106 |
| `_test_dispatch` | 98 |
| `_test_pet_equip` | 83 |
| `_test_quest_story4` | 73 |
| `_test_p3_pet_growth` | 67 |
| `_test_debugger` / `_test_bus` | 65 |

**已知薄弱点**（P5-04 要补，全部本轮实测）：

| 项 | 现状 | 影响 |
|---|---|---|
| **17 个测试文件在代码里写死不存在的路径** `C:\...\MX\服务端源码\sim` | 真实路径是 `服务端源码/tests/sim`；另有 2 个只在注释/文档字符串里提到，不计入 | 其中 **5 个挂在 STEPS 里**（`_test_client_decode` `_test_encounter_map` `_test_map` `_test_returning_login` `_test_time_channels`），能跑只因 runner 注入了 `PYTHONPATH`；**12 个不在 STEPS**（`_test_battle_skill` `_test_debugger` `_test_drop` `_test_enemy_skills` `_test_enemy_speed` `_test_explore` `_test_gm` `_test_progression` `_test_shop` `_test_singleplayer` `_test_task` `_test_truth_audit`）→ **回归根本不跑它们**，单跑必失败 |
| `_test_dispatch` 用 `fails.append` 而非 `check` | 失败信息是自由文本 | 没有「期望/实得」对照，红的时候要人肉读日志 |
| 分派契约几乎全压在 `_test_dispatch`（98 断言点） | 另有 `_test_hard_constraints` 只守 3 条（场景三连 / `HERO_WAR_ID` / WarEnd→resync→award_exp 顺序） | 分派层一旦改坏，能报警的就这两处 |
| `_test_pet` 仅 7 个断言点 | 属早期冒烟 | 不能守护宠物协议细节（已被 `_test_pet_instance` / `_test_pet_equip` 接过） |

> P4-00 的 R7 写「4 个孤儿测试脚本」——**本轮实测：17 个在代码里写死不存在路径，其中 12 个连回归都不跑**。
> 这个数字被低估了 4 倍以上，说明当时的统计也是「凭印象」而非「扫全量」。

---

## 8. 真实客户端证据：**未执行**

**这一节是全文档最容易被误读的地方，所以单独成节。**

| 项 | 实况 |
|---|---|
| 客户端可执行文件 | ✅ `D:\mxsj2013\client\mx.exe` 存在 |
| 验收记录状态 | **`template_unexecuted`** |
| 勾选统计 | **⬜ 23 项 / ✅ 0 项** |
| 历史真机截图 | 7 张（含「真机_进入世界_永泽城」「真机_连接服务器失败」） |

`协议资料/报告/验收/人工GUI验收_真实客户端.md` 原文第 17 行：

> **当前状态：⬜ 未执行（等待人工验收）。** 本文件里的表格为待填模板；§7 是历史基线，只作参照，不代表本次验收。

因此本报告以及后续 P5 文档，凡涉及「画面/交互/按钮响应/是否卡『请等待』」的结论，一律标 **未验证**。

**能证明什么、证明不了什么**（照抄验收文档自己的口径表）：

| | 自动化验收（`_run_all.py`） | 人工 GUI 验收 |
|---|---|---|
| 「客户端」是什么 | 协议级脚本（自己拼帧） | 真客户端（`mxmain.dll` + Py2.6 脚本层） |
| 能证明 | 服务端字节/帧序/字段定义一致 | 客户端画面对不对、按钮响不响应 |
| 证明不了 | 客户端渲染与 UI 状态机 | 服务端内部实现细节 |

> 本轮 60/60 全绿，只覆盖左列。**右列 0 项完成。**

---

## 9. 风险清单（按「会不会让玩家丢东西」排序）

| # | 风险 | 严重度 | 现状（实测） | 归属 |
|---|---|---|---|---|
| **R1** | **`CLAW` 少算一层 → 存档/日志目录错位**（已实证：闸门读不到新会话） | **高** | 见 §9.1 / §9.2，含份数与时间戳 | **P5-03 第 0 步（本轮已修）** |
| R2 | **无版本控制** | **高** | 无 `.git`，改动无 diff、坏了无回滚 | P5-08 需给发布清单；P5-04 需给机器可读报告兜底 |
| R3 | 经济无流水、无幂等 | **高** | 重复提交靠各调用点自觉；`_emit_econ` 只是观测 | P4-05 未做 → P5-03 故障注入覆盖 |
| R4 | 存档无备份/无恢复/无完整性校验 | 中 | §5.2 三项全 ❌ | P5-03 |
| R5 | **真实客户端验收未执行** | 中 | 23 项全 ⬜ | P5-01（需人工），P5-09 不得代签 |
| R6 | 挑战系统完全缺失 | 中 | P3-06 两个模块 0/2 | 不在 P5 范围，如实标注 |
| R7 | 战斗中掉线 = 该场战斗作废（血已扣） | 中 | `in_war` 不落盘是设计取舍 | P5-03 故障注入 + P5-06 明示 |
| R8 | 状态效果数值覆盖 29/284 | 中 | `combat_effects.UNREADABLE` 6 条无数值 | P5-06 标注，P4-01/03 未做 |
| R9 | **17 个测试脚本在代码里写死路径**，其中 12 个不在回归里 | 低 | 见 §7 薄弱点；P4-00 曾记为 4 个 | P5-04 |
| R10 | 18 份文档缺失（P4 九 + P5 九） | 低 | `docs/` 现有 2 份：`P4_BASELINE_AUDIT.md` + 本文档（采集器在本文件落盘前跑，故它报 19 份） | 各包各自补 |

### 9.1 R1 详述：`CLAW` 少算一层（**本轮最重要的发现**）

```python
# m4_server.py:101-105
CLAW    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGDIR  = os.path.join(CLAW, '服务端源码', 'logs')
SAVE_DIR= os.path.join(CLAW, '服务端源码', 'save')
```

`m4_server.py` 实际在 `MX/服务端源码/tests/sim/m4_server.py`，是 **4 层**深；
`dirname × 3` 只能到 `MX/服务端源码`，所以 `CLAW` ≠ `MX`，再拼一次 `'服务端源码'` 就成了重复：

```
期望：MX/服务端源码/save/role.json
实际：MX/服务端源码/服务端源码/save/role.json     ← 多一层
```

实测现场（`ls` / `glob` 证据，2026-10-23 取数）：

| 路径 | 存在 | 内容 |
|---|---|---|
| `MX/服务端源码/save/` | ✅ | `role.json`（2763 B，10-22 02:19）、`role_abcd123.json`（2806 B，10-22 02:49） ← **历史存档在这** |
| `MX/服务端源码/服务端源码/save/` | ❌ | 不存在 |
| `MX/服务端源码/logs/` | ✅ | **1288** 份 `m4_srv_*.log`，最新 **10-22 17:28** ← 闸门读这里 |
| `MX/服务端源码/服务端源码/logs/` | ✅ | **242** 份 `m4_srv_*.log`，最新 **10-23 17:16** ← 服务端写这里 |

运行链路的后果（`singleplayer.py:481` → `savedir = args.save_dir or m.SAVE_DIR`）：

1. 默认启动时存档写到**新的空目录**；
2. `_apply_save` 走 legacy 兜底时读的是 `SAVE_DIR/role.json`，即新的空目录里的 `role.json`；
3. 历史 `MX/服务端源码/save/role.json` **不会被读到**；
4. 玩家视角 = **进度凭空消失**（且没有任何报错，因为 `load_role` 对「文件不存在」返回 `None` 是正常路径）。

### 9.2 R1 已经造成的实际损害：**验收闸门看的是旧日志**

这条不是「将来可能出事」，是「现在就是坏的」。验收链路上的会话闸门
`协议资料/报告/验收/_verify_session_flow.py` 自己算的是**正确**的根：

```python
HERE = .../协议资料/报告/验收
MX   = dirname×3(HERE)                       # → MX   ✅ 对的
DEFAULT_LOG_DIR = os.path.join(MX, '服务端源码', 'logs')   # → 1288 份旧日志
```

于是出现**两头不一致**：

| | 服务端实际写日志到 | 闸门实际去读 |
|---|---|---|
| 路径 | `服务端源码/服务端源码/logs/` | `服务端源码/logs/` |
| 份数 / 最新一份 | 242 份，**10-23 17:16** | 1288 份，**10-22 17:28** |

实测跑一次闸门（无参数）它选中的是 `m4_srv_20261022_172858.log` —— **10-22 的旧会话**，
因此判定 `FAIL`（战斗场数 1 < 2 的要求）。而真正最新的会话在嵌套目录里，
**闸门从来没看过它们**。

> 结论修正：R1 的成本比「某天丢档」更直接 ——
> **它是 P5-01 / P5-09 的前置阻断项**。人工验收做得再对，机器闸门也会拿旧日志判 FAIL。
> 修复后服务端重新写回 `服务端源码/logs`，闸门即能读到新会话（日志不迁移：
> 嵌套目录那 242 份保留原地，只作为历史证据引用）。

同类受影响项：`crypto_capture.py` / `crypto_probe.py` / `m2_server.py` 各自有一份
`LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')` —— 修 `CLAW` 要**一次改全**，
否则抓包工具会把新日志写到另一处。

---

## 10. P5 十包的开工前提

| 包 | 前提 | 现状 | 能否立即开工 |
|---|---|---|---|
| P5-00 基线审计 | 采集器 + 回归 | ✅ 本文件 + 60/60 | 完成 |
| P5-01 完整流程 A–G | 回归绿、客户端可达 | ✅ 60/60；mx.exe 在 | **可**（客户端部分必须人工，否则只能记「协议级通过」） |
| P5-02 协议黄金样本 | `frames.SPEC` 30 + `build_*` 58 | ✅ | **可** |
| P5-03 存档安全 | 迁移链 + 原子写 | ✅ 有；❌ 备份/恢复/校验无 | **可**（工作量最大，且含 R1 修复） |
| P5-04 自动化门禁 | 60 步 runner | ✅ 有；❌ 无机器可读报告 | **可** |
| P5-05 长时间稳定性 | 需 P5-04 报告口径 | 部分 | 可（须在 P5-04 之后定口径） |
| P5-06 体验收敛 | `FEATURE_STATUS` 27 项 | ✅ | **可**（受 29/284 与「装备不加属性」限制） |
| P5-07 诊断包 | 无 | ❌ 需新建 | **可** |
| P5-08 发布管理 | 无 `BUILD_INFO`、无 git | ❌ 需新建 | 可（无 git 是硬限制，只能产出清单+校验，不能给 diff） |
| P5-09 最终验收 | 依赖前九包 | — | 最后做 |

**被外部真值阻塞、P5 内不解决**：`equip_repair`、`pet_skill_learn`、`pet_equip_effect`、`alchemy`、任务 17–22、状态数值 255/284、装备数值表。

---

## 11. 本轮发现并修正的问题（含采集器自身的 bug）

审计工具自己也会错。这一节记录本轮被修掉或拦下的七处，因为**它们的错法会影响结论的正确性**：

| # | 问题 | 错法 | 后果（如果没修） | 修法 |
|---|---|---|---|---|
| 1 | `_p5_audit.py` 根路径少算一层 | `MX = dirname(dirname(HERE))` 只到 `服务端源码` | `docs/` 与 `协议资料/报告/` 全被误报缺失（报「缺 20 份」，且**漏判**已存在的 `P4_BASELINE_AUDIT.md`）；`gui_acceptance_state` 报 `missing`、截图报 0 | 改 `dirname × 3`，并在注释里写明「别数错层」 |
| 2 | 存档字段用正则扫 `d['x'] =` | 扫到几百处零散赋值（临时 dict、事件字段） | 只采到 **7** 个字段，且不是存档字段 → 会得出「存档结构只有 7 个键」的错误结论 | 改 `ast` 解析 `_persist()` 的 dict 字面量 + 下标追加键 → **37** 键，与源码一一对应 |
| 3 | `balance_simulator.py` 归到 P4-05 | 按「P4-05 = 数值」猜的 | 会把数值校准错记成经济安全 | 按文档名对齐：P4-03 = 数值校准，P4-05 = 经济安全 |
| 4 | 差点把 `_test_craft` 写成「1 个断言点」 | 那个 1 其实是**feature 键名提及次数**，不是断言数 | 会让制作链看起来零覆盖，触发一次不必要的「补测试」返工 | 按统一口径重测 = **60 断言点** |
| 5 | 差点把 `huoli_regen` 写成「无直接测试」 | 只搜了键名，没搜函数名 `_regen_huoli` | 会误报一个已覆盖的功能为空白 | 搜函数名 → `_test_craft` 直测 4 次（含「战斗胜利不重置活力」） |
| 6 | P4-00 的「4 个孤儿测试脚本」被低估 | 当时凭印象列了几个 | 实际 **17 个**在代码里写死不存在路径，其中 **12 个不在 STEPS** | 本轮按全量 grep + 逐行剔除注释重算，写入 §7 / R9 |
| 7 | 我第一次把写死数报成「19 个」 | 把 `_test_persist` 的**文档字符串**、`_test_role_persistence` 的**注释**也算成了代码 | 「孤儿脚本」被多报 2 个；孤儿清单里混进两个其实已经修好的文件 | 加「剔除整行注释 + 只认 `sys.path.insert` / `SIM =` 两种形态」的过滤 → **17 个** |

> 第 1、2、4、5、7 条都是同一种错法：**用一个自洽但口径错误的统计去代替实测**。
> 更麻烦的是它们不报错 —— 一个自以为在实测的采集器，正则写歪了照样能编出一份读起来很专业的假报告。
> 所以本轮每条结论都标了「怎么测的」，让复核者能重跑，而不是只能相信。

---

## 12. 不得声称的事项（禁区清单）

以下说法在本轮及后续 P5 文档里**一律禁止**，除非有对应新证据：

| 禁止的说法 | 原因 |
|---|---|
| 「真实客户端验收通过」 | 23 项全 ⬜、0 ✅；只跑了协议级脚本 |
| 「P4 已完成」 | 1/9（仅审计），0/5 点名模块，9 份文档缺失 |
| 「P3 已完成」 | 3/8；P3-04/05/06 模块 0/6，P3-07/08 未做 |
| 「协议 134/134，玩法就齐了」 | 分派口径 ≠ 玩法口径（10/27 implemented） |
| 「存档安全」 | 备份/恢复/完整性校验三项全无（§5.2） |
| 「可回滚」 | 无 git，只能手工备份 |
| 「战斗状态效果已实现」 | 机制通、数值 29/284（`partial`） |
| 「装备能加属性」 | 装备加成恒 0（无 `equipdata`） |
| 「工艺升级重启会丢」 | **反向也不行**：已核实 `life_lv`/`life_exp` 落盘，不丢 |

---

## 13. 下一步

按指令十六节的阶段门禁，进入 **第 1 轮：P5-01 完整流程验收 / P5-02 协议兼容**。

但 **R1（§9.1）优先级高于流程验收**：它属于「会让玩家以为丢档」的一类，
且修它需要一条会先红的测试 —— 这正是 P5-03 的第一步。因此实际执行顺序调整为：

```
P5-00 审计（本文件）
  ↓
R1 修复（P5-03 第 0 步）—— 已完成：先写会红的测试（_test_save_paths，7/11 判红）
  → 改 CLAW 为标志目录定位（proj_root.py）→ 11/11 转绿 → 回归 60 → 61 步
  ↓
P5-01 流程 A–G（客户端部分只能记「协议级通过」）
  ↓
P5-02 协议黄金样本 + 模糊测试
  ↓
P5-03 余下（备份/恢复/校验/8 类故障注入）
  ↓
P5-04 → P5-05 → P5-06 → P5-07 → P5-08 → P5-09
```
