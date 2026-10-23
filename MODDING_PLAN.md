# 梦想世界 2013（MX）改服 / mod 计划

> 依据：`um scan` 两次实测 + `协议资料/Claw_README.md` 成果现状 + 目录实地核对（2026-10-18）。
> 这份文件回答的是「走哪条路、边界在哪、下一步做什么」，不是教程。

## 一、目标与现状

- 对象：多益网络《梦想世界2013》客户端 v1.1.50（**已停服**，无在线服务可影响）
- 目标：本地离线可玩 —— 自有客户端 + 自研模拟服，不改动任何在线系统
- 现状：模拟服 M1–M7 已跑通（登录 → 进世界 → 移动/传送 → NPC → 回合战斗 → 任务 → 背包/商店/宠物 → 持久化），协议级测试 72/72、GM Web 面板可用
- **M9 养成闭环（2026-10-18）**：升级(`C2S 17`) / 加点(`C2S 16`) / 学招式(`C2S 103` + `S2C 149`)
  三条上行落地，战斗胜利发经验 → 属性面板升级得点 → 加点涨力量影响普攻 → 找永泽城「战斗技能师」(id 1002)
  学新招式（1104-1109，学费与客户端同式）。回归：`tests/_test_progression.py`（49 项）+
  `sim/_e2e_m9.py`（6 步，真 socket）。经验值与伤害加成是**模拟参数**，见 `m4_server.BATTLE_EXP_PER_ENEMY`。
- **M10 NPC 买卖闭环（2026-10-18）**：`C2S 106 NPC_SELL` + `S2C 148 S2CNpcSellList` 落地，
  村长菜单加「我想卖点东西」（选项 3）。收购价无真值（客户端 itemdata 无价格字段），
  按售价 1/4 收（`m4_server.sell_price_of`）。回归：`tests/_test_shop.py`（41 项）+
  `sim/_e2e_m10.py`（7 步，真 socket，含「卖空条目拒绝且清单不重排」）。

## 二、侦察结论（um scan）

| 项 | 结果 |
|---|---|
| 路径 | `D:\mxsj2013\client`（282 文件 / 1.16 GB，机外，不在 MX 内） |
| 引擎 | `native`，置信度 **10%** —— 无任何已知引擎签名（自研 x86 引擎，非 Unity/UE/Godot） |
| 可执行体 | `mx.exe` / `gameplayer.exe` / `mxupd.exe` / `mxupdate_unicode.exe`，全部 **x86、非托管** |
| 反作弊 | 未发现（客户端内无驱动、无保护壳证据）；因已停服，无服务端校验风险 |
| mod loader | 无（无 Steam Workshop、无 BepInEx/ModLoader 类框架） |
| 索引文件数 | 290（未截断） |

扫描给出的默认路线是「proxy-DLL + 函数 Hook（MinHook/SafetyHook）+ Ghidra/x64dbg」，**本项目不走这条** —— 见第四节。

真正的「引擎」在逻辑层，扫描器看不到：

- 15 个 `data/*.fls` 容器，内含 **Python 2.6 字节码 VM**（四层加密链：0SLF → zlib → PRNG 解混淆 → marshal + 条目 XOR + 分段密钥轮换 + 重映射 opcode）
- 脚本 **485/485 全部还原为可读 .py**（`#165` 亦已解开；总数 330,684 行量级，`src/` 下 484 个模块文件）
- opcode 映射 104→85 **100% 验证**（17,017 代码对象 / 1,536,528 指令，0 未映射）
- 原生层 `mxmain.dll` 反编译完成：7,416 函数 / 292,560 行 C
- 网络层为明文/轻量编码的私有协议，已按 `S2C/C2S` opcode 建字典并逐帧复现

## 三、路线判定

| 候选路线 | 判定 |
|---|---|
| loader API | ✗ 无社区 loader，游戏无 mod 生态 |
| 数据 / 资源替换 | △ 可用，但 `data/*.fls` 受容器加密约束，需走自研管线 |
| **协议重实现（自建服务器）** | ✅ **已选**。客户端只认协议，服务端完全自持；改动面最小、可回滚、可测试 |
| 脚本层修改（改 .fls 内 .py） | ✅ 备用。需重打包加密链，成本高于改服务端 |
| native Hook / proxy-DLL | ✗ 不必要。逻辑都在 Py 层和协议层，硬 Hook 等于自找麻烦 |

**选定路线**：以 `服务端源码/sim/singleplayer.py` 为唯一运行时，客户端保持**原版不动**（仅 `local.ini` / `update.ini` 指向本地）。mod 内容通过协议帧 + 服务端数据表实现，不 patch 二进制。

## 四、关键路径

| 用途 | 路径 |
|---|---|
| 运行时客户端（原版） | `D:\mxsj2013\client`（`mx.exe`、`data/*.fls`） |
| 模拟服入口 | `MX\服务端源码\sim\singleplayer.py`（`start_singleplayer.bat`） |
| 服务端存档 | `MX\服务端源码\save\role.json` |
| 客户端配置 | `D:\mxsj2013\client\local.ini`、`update.ini`（改前留 `.bak`） |
| 协议规格 | `MX\协议资料\规格\`（`M1~M4_协议规格.md`、`M4_协议规格.md`） |
| 客户端源码（还原） | `MX\客户端源码\src\`（484 个 .py） |
| 反编译原生层 | `MX\客户端源码\dll_export\`（mxmain.dll） |
| 解密后的资源 | `MX\客户端数据\fls_decrypted\`（1.1 GB） |

## 五、实验台（Lab）规则

1. 动客户端前先备份：`data\` 全量 + `local.ini` / `mx.ini` / `update.ini`（已有 `.bak_singleplayer`）
2. 服务端一律 `--fresh` 起干净存档；测试脚本用临时存档目录，别污染 `save/role.json`
3. 端口：模拟服 3134、GM 面板 8080 —— 起服前确认无残留进程占用
4. 先在协议级测试过了再开真机（`tests/_test_*.py`），真机只看现象、不改客户端
5. 不引入任何在线连接；`local_http.py` 只服务本地资源

## 六、待解清单（按优先级）

1. ~~`0x43cc50` 加密算法未还原~~ —— **已解决（2026-10-19）**：纯 Python 实现见
   `服务端源码/sim/mx_crypto.py`。生成器 `0x43CA70` 位于 .text、**未被虚拟化**，
   静态即可完整还原；已用原版 DLL + unicorn 逐字节交叉验证（15 种子 × 2 方向）。
   详见 `服务端源码/sim/re_crypto/还原报告.md`。回归：`tests/_test_mx_crypto.py`。
   同日 **上行覆盖 51% → 100%**（134/134）：根因是 `C2S_SINGLEPLAYER_IGNORED`
   定义了却没接线；现已接进 `_dispatch` 战斗保底之前，其中 7 个 NPC 交互做成真业务。
   **组队闭环 + 多角色/多会话**（同日）：
   - 组队 `op72→op82`：建队/名册/阵型/踢人/队伍信息/目标/离队全通，
     队友按 war_id 入场（只做表现，不参与行动序）。`sim/team.py` + `tests/_test_team*.py`
   - 多会话：新增 `MultiServer`（每连接一个 `M4Server`），并发客户端互不干扰；
     存档按账号分文件 `role_<账号>.json`，旧 `role.json` 由**第一个**账号认领、不克隆。
     `tests/_test_multisession.py`。单会话仍可用 `--single-session` 退回。
   - **制作配方真值边界已厘清**（`sim/make_recipes.py` 头 + `tests/_test_craft.py`）：
     产物目录（48 条 name/lv/cost）与 12 个材料 id 是**真值**；「材料→产物」对应关系
     **不可还原**（客户端工艺面板从不列材料，《资料全库》工艺表也只有名称/等级/介绍三列，
     原版配方在已停服的服务端）。
     顺带修掉一个真 bug：craftdata 的 `cost` 扣的是**活力 m_Huoli(下标24)**，
     之前误扣成**体力**(下标26)，两者初始值都是 100 所以一直没暴露。
2. ~~`script.fls` #165 单条目未解~~ —— **已解决（485/485 全部还原）**
2. 掉落 / 经验 / 金钱曲线：掉落已改为真值池（`drop_data.py`，见八）；经验/金钱仍为手写常量。
   （2026-10-18：**战斗经验**已接通并可配（`BATTLE_EXP_PER_ENEMY=60`），但曲线本身仍是模拟参数——
   录像只有伤害/血量/掉落，没有经验值；升级门槛用 `1000×等级` 这条本服规则。）
   **2026-10-18 更正**：原计划"按 `expdata` 类数据表反推"**前提不成立** —— 全库无 `expdata`（485 脚本零 import），
   且 `m_NextExp` 由服务端下发、客户端只显示；`itemdata/monsterdata/taskdata` 只有文本字段，无规则数值。
   真值只能来自 `.mwr` 录像解析或实测，详见 `协议资料/报告/数值与协议真值审计_20261018.md` 第六、七节
   > **2026-10-20 收口：经验/金钱曲线已确认「三路皆断」，并改为显式 SIM 配置。**
   > - 路径①数据表：无 `expdata`（已证）。路径②录像：实扫 198 份 `.mwr`，
   >   出现的 op 只有 `112/113/114/115/116/117/119/122/123/125/129`（纯战斗帧），
   >   **op17(PropChange) 一次都没有** → 拿不到 `m_Exp`/`m_NextExp`。路径③资料全库：无等级-经验表。
   > - 处置：新增 `world_config.json` 的 **`progression` 段**（12 项，全部标 SIM：
   >   `exp_per_enemy` / `next_exp_init` / `exp_per_level_step` / `point_per_level` /
   >   `grade_max` / `coin_init` / `task_reward_*` / `tili_init` / `huoli_init` /
   >   `battle_tili_regen`），改配置即改升级节奏，不再散落在代码常量里。
   >   该段**可选**：老配置缺段不报错、保留模块默认（否则 60 张地图会跟着一起丢）。
   >   回归：`tests/_test_progression_config.py`。
   > - 顺带修一个真 bug：**任务键读档后没还原成 int**。`_migrate_0_to_1` 里做过 int 还原，
   >   但只对 `_schema<1` 触发；而新档已标 `_schema=2` → 重启后 `tasks.get(1)` 永远查不到，
   >   表现为「任务栏消失 / 进度归零」，**不报错不崩，纯静默**。
   >   已加 `normalize_tasks()`（与 `normalize_items` 同口径，每次读档无条件跑），
   >   并把「变异测试」扩成两条（items/tasks 各一条），`_test_mutation_normalize.py` 双红。
3. 战斗公式：伤害/命中/抗性已跑通，**已与 137 场真机录像校核**（2026-10-18）——
   > **2026-10-18 口径更正**：这 137 份里有 8 份录自「位域 LSB 打包」的旧模拟服
   > （0021/0035/0037/0106/0112/0113/0114/0118），位域统计不可用。`_mwr_export.py`
   > 现在按「hurt 合法性」逐份判定、默认跳过，**有效语料 129 场**。
   > 伤害/血量这些**数值**不受影响（`hp_chg` 与位域无关），但任何按 hurt 分组的结论
   > 必须用剔除后的语料重算。详见 `客户端网络层解析_20261018.md` §5.3–5.4。
   主角普攻 50 / 敌方 20 / 宠物 48、主角 HP 400、宠物 HP 350 全部落在真值区间内；
   会心（hurt）确认只是表现层，不改数值，但**会心率随怪物走**（野狼 11/11 全红、企鹅 0/63 不红，不是全局 35%）；
   > ⚠ **2026-10-18 复核推翻**：那组数字来自 `_mwr_export.py` 的错位序拆解（LSB）+ 混用两套编码的录像。
   > 按二进制实证的 MSB-first、只取新族重算：企鹅 0/12、4005 0/13、野狼 **1/2**（不是 11/11）。
   > 原「野狼必红」结论作废。详见 `协议资料/报告/客户端网络层解析_20261018.md` §5.4。
   > **已处理（10-18 13:45）**：`ENEMY_HURT_BS_BY_SHAPE` 清空为 `{}`，所有造型统一走全局 0.35；
   > 按造型登记的机制保留，注释写明补证门槛（同造型 ≥30 次有效命中，且只用新族录像）。
   **技能同样按造型，已落地**：企鹅用 1103 落英缤纷、野狼用 415 乱舞冲击 →
   `ENEMY_SKILLS_BY_SHAPE = {1: [1103], 2130: [415]}`（`ENEMY_SKILLS` 兜底）+ 配置项 `battle.enemy_skills_by_shape`
   + 回归测试 `tests/_test_enemy_skills.py`（含 WarPerform 帧 pid 端到端）。**出手序也已校核并落地**——出手速度随怪物种类不同（企鹅 shape 1 快于主角、
   野狼 2130 慢于主角，且同种怪按索引递减），已改为 `ENEMY_SPEED_BY_SHAPE = {1: 115, 2130: 40}`（`ENEMY_SPEED_BASE` 仅兜底），
   配置项 `battle.enemy_speed_by_shape`、回归测试 `tests/_test_enemy_speed.py` 同步到位。
   录像格式已破解（`客户端源码/tools/_mwr_parse.py`），帧数据集见 `协议资料/报告/录像真值_战斗帧_20261018.csv`
4. ~~客户端残留在线校验：`mxgw` 时间漂移检查已修，其他心跳/校验路径未全量排查~~
   —— **已解决（2026-10-20）**：全量排查 15 条路径分 A/B/C/D/E 五组（活跃 4 条全有解、
   服务端可触发 3 条天然关闭、本地检查 5 条由官方 `nocheck` 逃生舱一键关闭、更新/刷新
   早已缓解、网页 URL 全是死链）。顺带修了两件事：mxgw 时间源改 `time.monotonic()`
   （墙钟回跳会造出假 007，新回归 `tests/_test_time_channels.py` 守着，突变验证过）；
   `prepare_local_client.py` 此前把 local.ini 写成三层加密格式——客户端只读明文，
   加密会弹「服务器列表需要更新」（与 `rewrite_local_ini` 的真机实证相悖）。
   完整清单见 `协议资料/报告/模拟服全链路代码解析.md` §15.3。
5. ~~场景覆盖：5 张图已连通，其余地图的 `minimap_data` × `findpathdata` 联立尚未批量跑~~
   —— **已解决（2026-10-19）**：`sim/mapdata_resolve.py --emit` 批量联立得 60 张场景，
   **56 张自出生图连通**（初云区域 4 张为真孤岛，出口「天空要塞」在本库无 mapid）；
   MAPS / MAP_LINKS / MAP_EXITS / NAME2MAP 全量落表（旧手工 5 图降为其中 5 张），
   `world_config.json` 同步重建；新增「落脚点豁免」——不少新图 spawn 恰是它自己的出口标记，
   不豁免则一落地就迈步被弹走。回归：`tests/_test_map.py`（全图数据自洽 + 豁免 + 持久化，
   已并入 `sim/tests/_run_all.py`）。

## 七、下一步（垂直切片）

最小可验证增量：**把「掉落表」从手写常量换成从客户端数据表反推的真值，并在协议级 + 真机各验一次**。
理由：它同时踩到「客户端数据解析」「服务端数据表」「战斗结算」三条链路，做完这条，后面的数值类 mod 都是重复劳动。

## 八、网上资料补全（2026-10-22）

用公开端游资料补模拟服里靠手写常量撑的部分。**只收能落到客户端/官方资料校验的内容**，
每条带 source；查不到的写「不可得」。

**已落地（改运行逻辑）——场景遇怪**

- 新增 `sim/encounter_data.py`（生成物）：43 个野外/迷宫场景 → 124 条怪物 → 客户端 `monsterdata` 的真 shape；
  17 张城镇/室内不在表内 → **不再遇怪**（之前任何地图走到第 5 步都刷野狼）。
- 生成器 `sim/gen_encounter_data.py`：三份真值联立（资料全库场景表 × monsterdata × world_config.maps），
  未解析即 `exit 1`；`encounter_resolve.tsv` 留逐条明细。
- `m4_server`：`ENCOUNTER_PER_MAP` / `_encounter_def()` / `_encounter_allowed_here()`，`_start_war` 按本图名单选怪；
  GM 战斗快照带 `scene` 与实际怪名。
- `world_config.json` 新增 `encounter.per_map`（默认 true；false = 回到全图野狼，录像复刻用）。
- 回归 `tests/_test_encounter_map.py`（数据自洽 / 开怪名字+shape / 城镇不遇怪 / 野外遇怪 / per_map=false 回落），
  已挂进 `sim/tests/_run_all.py`。伤害 50/48/20、HP 400/350/100 一个没动。

**只入数据、未接线**

| 数据 | 文件 | 条数 | 状态 |
|---|---|---:|---|
| 召唤兽携带等级 | `网上资料_20261022/pets_carry.json` | 77 | 真值 |
| 主线剧情表（NPC/场景/门槛/剧情点） | `quests_mainline.json` | 24 | 真值；缺每步对话/战斗状态机 |
| 修炼经验公式 + 0→30 表 | `cultivation_exp.json` | 30×2 | 真值（双源同式）；本版客户端无修炼入口 |
| 炼药配方 | `alchemy_recipes.json` | 13（7 完整） | 玩家实测；4 个材料名查无此物，标 UNRESOLVED |
| boss 血量（江湖志/天界之路） | `boss_hp.json` | 13 | 半真值（「左右」口径） |

**仍然不可得**：人物升级经验曲线（线上同名 Level Chart 是另一款 MMO，已排除）、NPC 售价、
常规怪 HP/速度/技能、每场怪物数量与权重、完整工艺配方。

详见 `协议资料/报告/网上资料补全_20261022.md` 与 `协议资料/网上资料_20261022/README.md`。

## 九、主线剧情 1–3 打通（2026-10-22）

**范围**：村长的委托（Lv10）/ 游历四国（Lv20）/ 失落的银爵（Lv30），共 27 步、21 个剧情 NPC、13 张图。

- 数据 `sim/quest_data.py`：步骤状态机（talk / fight / item / finish）+ NPC 摆位。
  真值：剧情名/触发 NPC/坐标/等级门槛/剧情点/物品需求；模拟：台词、战斗怪造型（8 处显式 `shape_sim`）、
  金钱经验数值。
- 引擎 `m4_server`：`_npc_quest_dialog / _quest_answer / _quest_accept / _quest_advance /
  _quest_fight / _quest_complete / _quest_grant / _quest_battle_won`；任务栏 `S2CTaskAdd(72)`
  逐步刷新、完成 `S2CTaskRemove(73)`；剧情点走 `m_StoryPoint(42)`；`tasks.step` / `story_point` /
  `quests_done` 全部持久化。
- 摆位 `gen_quest_config.py`：21 个 NPC 幂等写入 `world_config.json`（8 个取 npcposdata 真造型，
  13 个 quest-spawn）；顺带上架 5 件剧情必需品（炸薯条/辟邪草/黑椒牛柳/金疮药/青霜剑）。
- 物品名校正 3 处（黑辣牛柳→黑椒牛柳、初级魔兽要诀→魔兽要诀、1级宝石包→一级如意宝石包），
  「幸运转转符」本版查无 → 奖励记空，不编 id。
- 顺带修：`C2S_GET_TASK(48)` 原来只重发新手任务，重登后主线任务栏会消失；现在全量重发。
- GM：控制台 `grade <n>`（门槛 10/20/30，免刷级）；`gm_state_dict` 暴露 grade/story_point/quests。
- 回归 `tests/_test_quest_chain.py`（8 组，ALL PASS），已挂进 `sim/tests/_run_all.py`。

### 九·补 剧情 2/3 全链路 + 任务态 NPC 可见性 + 制作真扣费（2026-10-22 二轮）

- **剧情 2/3 全链路 e2e**（`_test_quest_chain.py` ⑦⑧）：
  「游历四国」9 步、`「失落的银爵」11 步逐步走通 —— 对话/战斗/交物品三类步骤全覆盖，
  含**数量不足被拒**（辟邪草 3/10、金疮药 4/5）、**交错东西被拒**（黑椒牛柳冒充 30 级武器）、
  `any_shapes` 命中任意 30 级武器；奖励逐项核对（一级还童丹 ×4 / 飞行旗补充包 ×2 /
  称心宝石包 / 宠物作战心得 / 魔兽要诀 ×2 / 领悟招式）；剧情点逐笔核对（102 共 2 点、
  103 共 3 点，与资料一致）；「幸运转转符」本版查无 → 明确记空、不编 id。
- **quest-spawn 过滤**（资料原文：「部分 NPC 必须在接了任务的情况下才能看到」）：
  `quest_data.QUEST_SPAWN_IDS`（12 个）+ `m4_server._quest_target_npc_name / _visible_npcs /
  _visible_npc_ids / _refresh_spawns_if_changed`。场景帧、小地图 NPC 列表（op89 sub182）、
  NPC 点击校验**三处同源**；接取/推进/完成/放弃四个时机自动重发场景
  （`S2CMapSceneM` 会 `ClearAllObject`，不补发地图上就没对象）。回归 ⑥ 组 20 项。
- **制作真扣费**（`_test_craft.py` 新增 [7][8] 段，并首次挂进 `_run_all.py`）：
  锁死「op104 预览报多少、实扣就是多少」——`S2CNpcMakeCost(155)` 的 iMoney ==
  `MAKE_COST_MONEY × 件数` == 实际扣款；金钱不足 → 拒绝且材料/活力/金钱一分不动、只回一条对话。
- 连带修正：`_test_map.py` 的 NPC 计数改为比可见集（原来比 `MAPS[mid]['npcs']` 全量）；
  `_test_tap.py` 的遇怪段改为「城镇不遇怪 + 切到野外图才踩雷」，并修掉一处
  `seen_s2c += _wild` 重复累加导致的帧序列不变量误判。

**已知边界**：组队战斗按 1 敌模拟；第 3 条国籍门槛不校验（本服无国籍）；台词为改写；
剧情点暂无消费端；`_hero_perform` 仍用普攻伤害（`pow/powrate/速度/抗性` 未接入）。

详见 `协议资料/报告/主线剧情1-3实现_20261022.md`。
