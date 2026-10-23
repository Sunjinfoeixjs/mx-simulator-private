# 业务链：世界 / NPC / 战斗 / 养成 / 存档

> 本册是《模拟服全链路代码解析》的拆分分册：**从进世界到存档的完整业务生命周期，每条规则都注明真机依据**。
> 完整版（唯一现状依据）：[`../模拟服全链路代码解析.md`](../模拟服全链路代码解析.md)。
> 拆分只为聚焦阅读，内容与主文档逐字一致；**改内容请改主文档**，然后重跑
> `python 协议资料/报告/_split_docs.py` 重新生成本册。
>
> 文档双向体检：`python 协议资料/报告/_check_anchors.py`（已挂进 `tests/_run_all.py` 第 ④ 层）。

---
## 3. 登录 → 选角 → 进世界

### 3.1 上行帧

| op | 常量 | body | 服务端动作 |
|---|---|---|---|
| 1 | `C2S_LOGIN_ACCOUNT_ENTER` | `ver(4) + name(32) + pwd(20) + …` | 只取 `body[4:36]` 当名字；有角色 → 推列表，否则 `AccountInfo(count=0)` |
| 3 | `C2S_LOGIN_PLAYER_ADD` | `name(20) + shape(1) + color(2) + major(1)` | 建角 → `PlayerAdd(6)` + 角色列表 |
| 5 | `C2S_ACCOUNT_MANAGE` | `sub(1)` | `sub==16` 时补推角色列表，否则回 `op7` |
| 6 | `C2GS_PBZLIST` | — | 回 `BandWord(29)` 空表 |
| 2 | `C2S_LOGIN_PLAYER_ENTER` | `hero_id(4)` | **进世界**（见 3.2） |

### 3.2 进世界帧序（顺序即契约）

`_enter_world`固定发下列序列，少一帧或换序都会出现具体故障：

| 序 | 帧 | 为什么不能省 |
|---|---|---|
| 1 | `PropInit(16)` | 220B 定长属性块，`assert len(p)==220`；少一字节后面全错位 |
| 2 | `PropResist(18)` | 5 项抗性；不发则 `hero.CreateProp` 把末 8 项留 0 |
| 3 | `PropPower(19)` | `m_At/m_Df/m_Dp`；不发 → **所有 df>0 的招式被判「无法使用」** |
| 4 | `PropChange×N` | 覆盖写死的 1 级 / 0 经验 / 1000 金，否则重登回档 |
| 5 | `SkillLevel(65) sktype=2` + `sktype=7` | 前者「学会」，后者「进战斗招式菜单」；只发一个 = 学会但打不出 |
| 6 | `SceneM(96)` | 客户端会 `ClearAllObject()` |
| 7 | `AddNpc(99) ×N` | 承接上一帧的 Clear，否则地图上没有任何对象 |
| 8 | `GotoM(97)` | 落点 |
| 9 | `SummonInfo(48)` / `SummonSkill(49)` / `SetFight(50)` / `SummonKey(53)` | 建宠、技能格、出战、变异角标；资质 `op51` 由面板主动请求 |
| 10 | `ItemAdd(33) ×N` | 没有「整包背包」帧，只能逐件推 |

### 3.3 `PropInit` 的 220B

`build_prop_init`按 `hero.ITEM_ALL` 顺序逐项 `pack`，最后
`assert len(p) == 220`。这是全项目唯一带 `assert` 的组包函数——因为它一旦错位，
后面所有 `S2CPropChange(type)` 都会按错误下标 `setattr`，表现为「属性面板数字乱跳」。

关键下标（`hero.ITEM_ALL`）：

| idx | 字段 | 宽度 |
|---:|---|---|
| 8 | `m_Exp` | 4B |
| 9 | `m_NextExp` | 4B |
| 16 | `m_HP` | 2B |
| 18 | `m_MP` | 2B |
| 20 | `m_FP` | 2B |
| 32/33/34/35/36 | `m_Str/m_Mag/m_Cha/m_Dex/m_Con` | 2B |
| 37 | `m_Point` | 2B |
| 43 | `m_Gold` | 4B |
| 72/73/74 | `m_At/m_Df/m_Dp` | 1B |

---

## 5. 世界模型：移动与传送

### 5.1 数据来源

全部取自客户端还原脚本，不是臆造：

| 数据 | 来源 |
|---|---|
| 地图名 | `minimap_data` 出口点名 × `findpathdata` 邻接表联立（`mapdata_resolve.py` 可复现，Jaccard 1.0） |
| 出生点 / NPC 坐标 | `npcposdata.py` 真实站位（保证落在可行走区） |
| 出口坐标 | `minimap_data.py` 真实出口标记 |

### 5.2 移动：`C2S 54` → `S2C 86`

```
C2S op54: x(2) y(2) trace_len(1) trace(n×1)
  方向表 MOVE_OFFSETS：1 右上 / 2 左上 / 3 左下 / 4 右下 / 5 上 / 6 左 / 7 下 / 8 右
  → 终点 = 起点 + 各方向增量
S2C op86: id(4) x(2) y(2) dx(1 有符号) dy(1 有符号) move_type(1)
```

**`dx/dy` 是「起点 → 终点」的增量，不是绝对坐标**。旧实现恒发 `dx=dy=0`，
客户端把角色拉回原地（回弹）——这是真机截图定位出来的。

每 10 步落盘一次；`move_count % encounter_every == 0` 触发遇怪。

### 5.3 出口与传送使

| 路径 | 逻辑 |
|---|---|
| 踩出口 | `_check_exit`：`abs(px-ex) <= EXIT_RADIUS` 且 `abs(py-ey) <= EXIT_RADIUS`（半径 4）→ 目标图 |
| 找传送使 | 点 `id=1001` → 按 `MAP_LINKS` 生成本图邻接场景选项 → `_teleport_choice` |

切图统一走 `_goto_map`：改 `mapid`/坐标 → `_send_scene` → 落档。
`_send_scene`固定三连：`SceneM`（含场景名/小地图）+ 该图全部
`AddNpc` + `GotoM`。

### 5.4 场景重同步契约

两条「客户端会自己清场」的路径，服务端必须补数据：

| 触发 | 客户端行为 | 服务端补偿 |
|---|---|---|
| `S2CMapSceneM` | `world.ClearAllObject()` | 紧跟全部 `AddNpc` + `GotoM` |
| `war.WarEnd` | `world.ClearAllObject()`，且**不会自行重新请求** | `_resync_scene`：`SummonInfo` + `SceneM` + `AddNpc` + `GotoM` |

漏掉后者的现象：回到场景地图全空，且战斗单位/「请等待」残留。

### 5.5 场景遇怪表：哪张图能踩雷，踩到什么（2026-10-22）

以前是「任何地图走到第 N 步都刷野狼」——一个占位实现，城镇里也能遇怪。
现在是**按图查表**：

```
走到第 N 步 → _encounter_allowed_here()          ← 城镇/室内直接 False
                 └→ _encounter_def()             ← encounter_data.ENCOUNTER_BY_MAP[mapid]
                       └→ random.choice(monsters) ← (名字, shape) 二元组
                             └→ enemy_meta[wid] = {spawn_index, speed, shape, name}
```

| 项 | 真值 / 模拟 | 来源 |
|---|---|---|
| 场景名单（43 张野外/迷宫） | **真值** | 《资料全库》暗雷/明雷场景表 |
| 怪物名 → shape | **真值** | 客户端 `src/data/monsterdata.py` |
| 场景名 → mapid | **真值** | `world_config.json` |
| 每张图刷几只、各占多少权重 | **模拟** | 场景表只写「有哪些怪」，不写数量/权重 |
| 怪物 HP / 速度 / 技能 | **模拟** | 沿用 `ENEMY_HP` 等代表值 |

城镇与室内（17 张图）**不在表内 → 不遇怪**，这才是原版行为。
开关是 `ENCOUNTER_PER_MAP`：置 `False` 回到旧的「全图野狼」，
专门给逐帧断言的老测试用（它们断言的是帧序，不是刷怪表）。

> 一个容易踩的点：**场景等级 ≠ 怪物等级**。场景表里给的是「该图适合多少级」，
> 不是怪本身的等级，两者不能互相代入。表里因此只存名字与造型，不存等级。

---

## 6. NPC / 对话 / 商店 / 任务

### 6.1 对话：不是自由文本

`S2CNpcChatExt(152)` 的 `msg` 是客户端 `ParseWord1` 的行语法：

```
P\n 页头正文
Q 选项一
Q 选项二
```

服务端用 `cur_npc` 记住「最近点的是谁」，`C2S_NPC_ANSWER(98)` 的 `choice` 是**选项序号**，
不是文本。这是全服唯一的「对话上下文」。

村长菜单：`1=任务 / 2=买 / 3=卖 / 4=离开`；传送使：选项 = 邻接场景；技能师：`1=课表 / 2=离开`。

### 6.2 商店 / 收购 / 详情

| 帧 | 布局 | 终止 |
|---|---|---|
| `S2CNpcBuyList(147)` | `mode=2 + ticketid(4) + ext(1) + [shape(4) num(2) price(4)]…` | `shape=0` |
| `S2CNpcSellList(148)` | `mode(1) + [shape(4) amount(2) price(4)]…` | `shape=0` |
| `S2CNpcDetails(151)` | `pos(1) + desc(GBK\0)` | — |

两个必须记住的坑：

1. **148 的第一列是 `shape` 不是实例 id**。客户端拿它 `itemdata.GetData(shape)` 取名，
   再去背包里按 `m_Shape` 找实例。发实例 id 会让面板显示错误物品名。
2. **卖出请求带的是「收购列表下标」**（`flag=0` 时 `pos` 是面板格位，不是背包格号）。
   所以服务端必须缓存自己发出去的那份清单（`_sell_cache`），且**卖出后只改数量、
   绝不重排/删条目**——一旦重排，玩家点「鹿茸」就会卖成下一项。

`C2S_NPC_SELL(106)` 的两种形态（`netnpc.C2SSellNpc`）：

| flag | body | 语义 |
|---|---|---|
| 1 | `itemid(4)` | 卖**该实例整摞**（客户端这条路径不带数量） |
| 0 | `pos(1) + amount(2)` | 按**列表下标**卖，`pos` 不是背包格号 |

`flag=1` 必须扣**玩家点的那一个实例**（`_take_from_iid`）：按造型从低位格子扣，
背包里同造型多摞时会卖掉别的一摞。

### 6.3 任务闭环

```
选项 1（未接）  → tasks[tid] = {DOING, 0} + TaskAdd(72)
每击杀一只      → progress+1 → 重发 TaskAdd（同 tid 客户端先 Remove 再重建，进度随之刷新）
progress == 3   → state = DONE → NPC 首页文案变「来领赏吧」
选项 1（DONE）  → TaskRemove(73) + 金钱 + 经验 + 道具
选项 1（DOING） → 进度提示文案
```

`C2S_NPC_TASK(100)` 只对「已接取 / 已达成」重发 `TaskAdd`，未接取**不造数据**——
避免客户端弹出无中生有的任务。

### 6.4 制作 / 合成（M13）

制作不是一条请求，而是「查询费用 → 玩家确认 → 服务端校验并结算」两阶段：

| 阶段 | C2S → S2C | 关键处理 |
|---|---|---|
| 费用预览 | `C2S_NPC_GETCOST(104)` → `S2CNpcMakeCost(155)` | 解析模式及材料实例 ID；按配方找产物，再读取 `CRAFT_DATA` 的技能等级/体力消耗。未找到时回落到本服默认费用 |
| 执行制作 | `C2S_NPC_MAKE(102)` → `S2CMakeCompleteNotify(160)` | 识别五类客户端 body 形态；把实例 ID 解析为造型与件数，匹配 `MAKE_RECIPES` 后做等级、体力、背包空间校验 |

成功路径在 `_handle_npc_make`：先聚合并扣除所选实例的材料，
扣体力、给工艺熟练度（每次 +1 的本服规则），再创建产物并下发 `ItemAdd(33)` /
`NotifyExt(164)`，最后发送完成通知并持久化。失败路径回 `NpcChatExt(152)` 提示，
校验失败时不扣材料或体力。成本表中的产物、物品 ID、技能等级和体力消耗取自客户端数据；
材料组合/件数规则仍是生成器推导的模拟配方（见 §10）。

**两项曾经「说了不做」的，现在都做了（2026-10-22）：**

| 项 | 旧行为 | 现行为 | 性质 |
|---|---|---|---|
| 金钱 | 预览帧 `155` 报「金钱 M」，制作成功**一分不扣** | `_handle_npc_make` 同步 `self.coins -= MAKE_COST_MONEY×件数` 并发 `PropChange(金币)`，预览与实扣同源 | 金额值是**模拟**（客户端 `craftdata` 无价格字段），扣款**行为**已兑现 |
| 工艺升级 | `life_exp += 1` 但 `life_lv` 恒为 5，lv>5 的产物永远做不了 | 熟练度满本级阈值 → `life_lv += 1`、溢出累计；`craftdata` 里 lv>5 的产物真的能做出来 | 升级**曲线**是**模拟**（服务端侧数据，仓库无真值），循环本身已跑通 |

因此 `FEATURE_STATUS` 里 `craft_charge` 是 `implemented`、`craft_levelup` 是
`simulated`——前者行为已兑现，后者规则无真值。

还剩两项与制作链相关、但**不是**制作的缺口：

| 键 | 状态 | 原因 |
|---|---|---|
| `huoli_regen` | `simulated` | 活力按**在线时长**恢复（`HUOLI_REGEN_PER_SEC`，每点一个固定间隔）；速率是 SIM |
| `alchemy` | `not_implemented` | 炼药（op73）客户端入口在，但**本版客户端没有配方数据表**（检索不到 `drugdata.py`），缺「材料→药品」真映射，只识别子指令不回 `S2CDrug*` 帧 |

> 炼药这条特别说明：资料不确定的材料**不自动替换物品 ID**。宁可标
> `not_implemented` 让玩家知道「点了没反应」，也不猜一个形状号糊上去——
> 猜错的后果是玩家按错误配方白烧材料。

### 6.5 主线剧情 1–16 与剧情技能（2026-10-22 落地 1–6，2026-10-23 扩到 16 条）

任务分两层，**不是同一套东西**：

| 层 | 载体 | 数据 | 状态 |
|---|---|---|---|
| 新手任务 | `tasks[tid] = {DOING/DONE, progress}` | 内联常量（击败毛草团 0/3） | `implemented` |
| 主线剧情 1–16 | `quests` 状态机 + `story_point` | `quest_data.py`（1697 行，手写） | `partial`（17–22 未落地，见 §6.5.1） |

> `quest_data.py` 现有 16 条任务 / 201 步 / 120 条 NPC 摆位（27 世界常显 +
> 93 任务态 `quest-spawn`）/ 24 个剧情物品。等级上限随之从 30 抬到 **120**
> （`m4_server.GRADE_MAX` 与 `world_config.json` 的 `progression.grade_max` 必须同时改，
> 改一个会被 `apply_to()` 的先后顺序吃掉，见该行注释）。

主线是**步骤机**，不是「打几只」计数器：

```
C2S_NPC_ANSWER(98) 选「接任务」
  → _quest_grant(qid)      等级门 / 前置门 / 已有主线进行中 → 拒
  → _quest_step()          当前步骤类型：对话 / 交物品 / 战斗
        ├ 对话 → 推 S2CTaskAdd，等下一次 NPC 交互
        ├ 交物品 → 校验数量 → 扣除 → （可能发剧情点）→ 下一步
        └ 战斗 → _quest_battle = {name, shape, level} → _start_war
  → _quest_advance()       最后一步 → TaskRemove(73) + 奖励 + quests_done
```

**真值与模拟的边界（写死在 `quest_data.py` 头）：**

| 项 | 性质 | 说明 |
|---|---|---|
| 剧情编号 / 步骤数 / 等级门 / 前置 / 剧情点合计 | **真值** | 与《资料全库》主线一览表逐条对齐，`_test_quest_story4` 逐条验 |
| NPC 摆位、剧情物品 shape | **真值** | 摆位取自 `npcposdata`，shape 能在客户端 `itemdata` 查到 |
| 剧情战斗的怪物造型 | **模拟** | 字段名 `shape_sim` 就是标注，原服怪造型不可得 |
| 组队战斗 | **模拟** | 原服是多人协作，本服按 1 只怪单挑 |
| 国家/阵营校验 | **未校验** | 单机只有一个角色，无从判定 |

**剧情点去哪了**：`m_StoryPoint` 不是摆设。客户端 `dialog/dlglearnskill.py`
对技能 2001–2029 把「花费」列换成剧情点，上限与每级消耗在 `juqingdata`。
所以剧情点的真实用途是**在 NPC 处学/升剧情技能**：

```
C2S_NPC_LEARN_SKILL(103)  skid=2001..2029
  → JUQING_SKILLS[skid]（juqing_skill_data.py，由 gen_juqing_skills.py 生成）
  → 校验 max_lv / dist（每级消耗）/ story_point 余额
  → S2CSkillLevel(65) sktype=3
```

> 这条链路曾被误标成 `display_only`（「本版客户端没有消费入口」）。
> 复核 `dlglearnskill.py` 后改成 `implemented`——**误标的代价是有人会去「补」
> 一个根本不缺的功能**，所以 `_test_quest_story4` 的第 ⑨ 条专门守这个状态值。

#### 6.5.1 7–16 怎么落地的（2026-10-23）

7–16 是飞升前主线（等级门 35/40/45/50/55/60/65/70/75/80），qid 107–116。

| 项 | 做法 |
|---|---|
| 流程 / 步骤 | 解析 `_docx_text.txt` 逐条「流程」串（`A→→B→→C（战斗，奖励…）→→…`），144 个资料节点 → **134 步**（首节点并入 `accept`，战斗/交物/对话分 kind） |
| 坐标 | 75 条摆位：17 个世界 NPC 取 `npcposdata` 真值，58 个 `quest-spawn`（任务态可见）取资料坐标表 |
| **交叉校验** | 流程里散落的「剧情点 N 点」逐条相加 = 一览表 `story_point`，**7–16 合计 27 点**、144 节点全吻合——两处独立表述互证 |
| 场景别名 | 资料「清风村」= 本库 1025（`清风村2`，`findpathdata` 出口一致）；「珊瑚迷宫二层」= 本库单张 `珊瑚迷宫`(1036) |
| 前置 | 15 的「完成 10/11/12/13/14」写成 `QUESTS[115]['prev'] = (110,…,114)`——**引擎按 qid 比，不是编号** |
| 降级步 | 刃虎宝宝 / 大螃蟹宝宝属召唤兽，本版 `itemdata` 连近似物都没有 → 该步降为 `talk` + `note`，**不造假 id、不让流程卡死** |
| 交付物上架 | 12 件交付物（丝绸手绢/神隐护腕/大力丸/荧光草/火神花/七星戒指/京剧脸谱/紫砂茶壶/回命散/驱邪丸/甘草/仙灵石）写进 `gen_quest_config.SHOP_ADD`——**不在商店就没人买得到，流程会断** |
| 奖励 | 物品名与本版不一致 8 处逐条记 `note`（N级宝石包→N级如意宝石包、40/80 级召唤兽装备、N级大点金石、40–50 级招式启蒙、60 级打造装备、80 级制造指南书、空白经验心得），查无实物的奖励记 `None` 不编 id |

**7–16 的回归**：`_test_quest_716.py` 8 组 57 条断言——数据自洽 / 坐标双源（标
`npcposdata` 的必须同图同坐标且 `shape == npcid`，标 `quest-spawn` 的不得与同图世界
NPC 重名）/ 接取门（Lv34 拒、Lv35 接 107、115 三级前置）/ 107 全链路 16 步 /
降级步 / 任务态可见性 / 17–22 不可得 / 落盘往返。**变异验证三次**（剧情点改 0、
金大有坐标 66→67、抹掉 `shape_sim`）全部判红，还原后重跑全绿。

#### 6.5.2 17–22 为什么还挂起

不是没流程（`_docx_text.txt` 里 6 条的流程串都在），是**坐标与场景双双不可得**：

| 剧情 | 卡点 |
|---|---|
| 17 沙漠的尘埃 | 枢纽 NPC「老狼」在 30 个节点里出现 14 次，无任何坐标来源 |
| 18 冰封的大地 | 「大地之树」串联 6 个节点，无坐标 |
| 19 天界之路 | 三杀兽·牙/角/鳞 无坐标 |
| 20 神秘之地 | 场景「天空要塞」本库**无该 mapid**（`npcposdata` 记 1157，本库只有 1158/1159/1176/1177） |
| 21 魔界之门 | 圣灵·冰龙/炎虎/雪凰/玄龟 无坐标 |
| 22 低沉的乌云 | 沐斯·栖风/夜痕/冷云 无坐标；资料流程串里连收尾地点都没写 |

这些名字在客户端 `npcposdata.py`（世界 NPC 表）与 `monsterdata.py`（105 个造型）
里**一处都没有**。按「不猜坐标、不造数据」整条挂起，登记在
`quest_data.NOT_IMPLEMENTED`（含 `blocked` 一句话卡点 + `missing_npcs` 缺失名单），
做到「可查、不假装已做」，谁拿到资料照着名单补即可。

> 曾经的理由是「载体缺」（召唤兽系统没落地）。2026-10-23 复核后**这条理由不再成立**：
> 召唤兽装备机制已通（§8.2），「交召唤兽」靠降级步绕过。真正的墙是坐标与天空要塞
> mapid——写清楚是哪个墙，比含糊说「做不了」有用。

### 6.6 角色养成：最终属性只有一个入口（P3-01，2026-10-23）

**核心设计：派生值只算不存。** 角色属性 = 基础 + 等级成长 + 加点 + 装备 的
**纯函数**结果（`progression_service.final_stats`），任何地方都不做
`self.atk += 加成`。于是「重复穿同一件装备」「重登重放装备帧」「读档后再 apply
一次」这三种必然叠加的场景，在结构上就不可能发生——算 100 遍结果一样。

副产品：**存档不用加任何字段**。装备的真实来源就是背包位置 `pos ∈ 1..9`
（客户端 `item.MAX_EQUIP_POS = 9`），等级/五维本来就存了，派生值不落盘。

| 层 | 来源 | 等级 |
|---|---|---|
| 基础 atk39/df15/dp4/hp400 | 47 条录像伤害校核（A=39、敌 D=0 → ⌊1.3×39⌋=50） | **A 真值**（`world_config` 可覆盖） |
| 力量每 +4 → 普攻 +1 | 本服约定，**加在公式之外**（混进 atk 会被 1.3 倍放大） | C 约定 |
| 等级成长、体质→气血、敏捷→速度 | 原服曲线在服务端，本库无数据，默认系数 **全 0**（= 关闭） | S 模拟 |
| 装备加成 | 客户端 `itemdata` 每条只有 `(名称, 文学描述)`，**无任何攻防字段**，也没有 `equipdata` 表 | **G 缺口** |

**装备能做的部分是"能不能穿"，不是"加多少"。** 这部分全是真值：
槽位语义 = `itemdata.EQUIP2POS`（cap→1 weapon→2 cloth→3 sash→4 cuff→5
magic→6 necklace→7 ring→8 shoes→9），类型 id 区间 = `ITEMTYPE`，
等级门 = 描述里的 `【等级】N`。`gen_equip_data.py` 把 252 件装备提取成
`equip_data.py`，并在**生成时逐项对账**客户端源码（不一致直接抛，不静默写错）。

协议入口没变：人物装备走 **`C2S_ITEM_DRAG(33)`**（`dest` 0..9 即装备位）。
`net.py` 138 条上行里没有独立的装备/强化/修理 opcode。以前这条路径是**纯位置搬运**
（头盔能拖进鞋位、1 级能穿 80 级装），现在加一道纯函数判定
（`equipment_rules.check_equip`）：失败**不动任何状态**，只回一帧原位让图标弹回去。

> 「非法操作不扣资源」在这里是**结构保证**：判定函数没有副作用，
> 通过之后调用方才动背包，不需要每个调用点自觉记得先校验。

**修理/耐久为什么没做**：客户端文案确实写了「修理：支付一定的费用就可以使装备
焕然一新（耐久度补满）」（`dlgmake.py`，按 GBK 字面量还原），但 (a) 物品没有耐久
字段；(b) `C2S_NPC_MAKE(102)` 的子指令只暴露 `C2SMake(type,…)`，type 取值
0/1/2/3/6/7/8/11/12，**没有 repair 命名**，无法确认哪一个才是修理。
不猜子指令 → 不做（`equipment_rules.REPAIR_GAP`）。

### 6.7 宠物养成：三层属性 + 技能表增删（P3-02，2026-10-23）

和角色养成同一条原则——**派生值只算不存**——但宠物多了「三层数值」这个坑。
三层合成统一收口到 `pet_progression`，`m4_server` 只保留同名别名：

| 层 | 函数 | 公式来源 | 喂给谁 | 等级 |
|---|---|---|---|---|
| 面板层 | `panel_stats()` | `petfightdata` 真值公式（`calc_pet_stats`，**不复制公式**）+ 五维加点 | `S2CSummonInfo(48)` 的 damage/defense/speed/magdef | 公式 **A 真值**；加点换算 **S 模拟** |
| 战斗层 | `combat_stats()` | 录像标定值 (AT37/DF15/DP4/SP49/MAGDEF36) × 真值**相对倍率** | `_dmg()` | C 约定（标定值来自录像反解） |
| 资源层 | `resource_max()` | 录像 350/380/30 × 倍率（**FP 恒定**，无面板来源） | 48 的 m_HP/m_MP/m_FP | A 真值（录像读数） |

为什么非要过一层倍率：reverse 物伤模型是在 0..255 档上拟合的，而真值公式给的是
0..65535 档（同一只 1 级企鹅面板 at≈579）。直接把面板值喂进伤害公式会**大两个
数量级**。用「实例真值 ÷ 参考宠真值」过桥，同时保住两件事：默认宠物恒为 ×1.0
（录像 48 伤害原样复现）、换宠/升级/加点倍率真的变。

> 「为什么面板 hp 305、实际上限 350」这类疑问在 `gm petstats` 里写着：
> `hp/mg` 是**资质基值**，面板真正的 m_HP/m_MP 走资源层。两个数并排却不解释，
> 排查的人一定以为哪里算错了。

**技能表增删收口到 `pet_skill_service`。** 协议事实：客户端 `netpet` 只有
放生/查看/改名/出战/存取/加点，**没有**「宠物学技能」上行 opcode，技能表只能由
服务端经 `S2CSummonSkill(49)` 下发 —— 所以服务端就是权威入口，调用方只有
GM（`petskill` / `petforget`）与以后的兽诀道具。

`gen_pet_skill_data.py` 从客户端 `petskill.DATA` 提取 **404 条**技能（被动 168 /
主动攻击 233 / 主动辅助 3）成 `pet_skill_data.py`，名字与描述是 **GBK 字节串**，
必须 `escape_decode` 再 gbk 解码（直接 grep 中文搜不到）。有了这张表才谈得上：

- **id 白名单**：学到表外 id，客户端菜单显示 `未知<id>`、点了没反应 →
  默认拒收（`unknown_skill`），而不是收下再说；
- **绑定技能归一**：`S2CSummonSkill` 里 ≥10000 的 id 是「绑定技能」
  （客户端 `pet.UpdateSkill` 会 -10000），判重必须 `10415 ≡ 415`；
- **手写 desc 断言可核对**：`PET_PASSIVE_EFFECTS` 里「必杀 8%」「连击 40%/75%」
  「迅捷 15%」「恐怖 10%」都能在 404 条真表里逐条查到原文（回归里就这么断言）。

顺带修掉一个隐形的帧字段越界：`hf_from` 是「从第几格起算护符技能」
（`S2CSummonSkill` 的 x 字段，客户端算 `hfskill = n - x`）。删技能后 n 变小、
x 若还停在 6 就会 > n，客户端拿到**负数格数** → 面板错位。遗忘路径现在维持
`0 ≤ hf_from ≤ len(skills)`。

三个缺口照实标（不编）：

| 缺口 | 状态 | 卡在哪 |
|---|---|---|
| 召唤兽装备进战斗 | `pet_equip_effect = blocked` | `itemdata` 无属性字段 → 装备对属性贡献恒为 0 |
| 魔兽要诀→技能 | `pet_skill_learn = blocked` | itemdata 只有单一道具 25405，**没记录每本要诀教哪个技能** |
| 资质洗练/调整 | `pet_aptitude_adjust = not_implemented` | 客户端 `dlgpet` 无此入口 |

而「技能表增删」这个**机制**本身是 `pet_skill_manage = implemented`：
学/遗忘/替换（要诀「冲掉」那一步）+ 去重 + 上限 + 绑定归一 + 白名单校验，
全在服务层且失败零副作用。机制是真的，数据来源不可得——与 P3-01 里
`equip_slot_rule`（implemented）vs `hero_progression`（partial）同一种切分。

### 6.8 高级战斗：随机源 / 状态效果 / 敌方 AI / 单次结算（P3-03，2026-10-23）

四件事，证据强度不同，分开说。

**① 随机源：12 处 `random.*` → 11 条用途流**

此前战斗里所有随机都直接调模块级 `random`。后果不是「不准」，而是**不可复现**：
同一场战斗重放一次，连击/会心/掉落/敌方选目标全变，「刚才那次为什么赢」无法回答。
更隐蔽的是**互相污染**——全局流是一条线，多触发一次掉落判定就把后面「敌方打谁」
的随机数挤走，于是「关掉掉落」这样一个无关改动会让敌方行为整体换一套。

`battle_rng.BattleRng` 按用途切流：

| 流 | 用途 | 原调用点数 |
|---|---|---|
| `encounter` | 遇怪选怪 | 1 |
| `combo` / `crit` | 宠物连击 / 会心 | 2 |
| `drop` / `drop_pick` | 掉落触发 / 池与条目挑选 | 3 |
| `enemy_target` / `enemy_skill` / `enemy_pick` / `enemy_hurt` | 敌方选目标 / 放不放招 / 挑招 / 会心 | 4 |
| `escape` | 逃跑判定 | 2 |

三条设计要点：

- **必然事件不抽随机数**：`chance(p)` 在 `p<=0` / `p>=1` 时直接返回，不消耗；
  `choice()` 单候选时同理。否则「把某个概率从 0 调成 1」会连带改变后续所有流的
  取值，排查时极难解释。
- **每场重置**：`begin_battle()` 用 `_mix32(主种子, 场次×0x9E3779B1)` 派生本场种子。
  不这么做，第 1 场掉过一次装备，后面每场的序列都被那次历史挤了位。
- **`_mix32` 用 FNV-1a 不用内置 `hash()`**：字符串 hash 在 Python 3 里带进程级
  随机化，同一流名跨进程会派生不同种子，「跨进程可复现」就没了。

**② 状态效果：284 条真值，29 条可算**

客户端 `src/data/buffdata.py` 有 `DATA`（284 条，`{id: {'desc': <GBK bytes>}}`）与
`ICONTABLE`（249 条）。`gen_buff_data.py` 逐条解码生成 `buff_data.py`。

解码有个坑：283 条走 `b'...'` 字面量（`escape_decode` 回字节再 gbk），唯独 **145 号**
被反编译器写成了 str（`'\u04ba\u032c'` = GBK 的 `D2 BA CC AC`），要 `encode('utf-8')`
再 gbk。漏一条就少一条状态。

`combat_effects.py` 只转录**说明里写明数字**的条目：

| 层级 | 数量 | 证据 |
|---|---|---|
| 有 id/名字/说明 | 284 | TRUE（客户端直读） |
| 说明含 ASCII 数字 | 34 | 候选 |
| 可转录成修正 | **29** | PARTIAL（从说明文字转录，逐条带 `src` 抄原文） |
| 有数字但不可转录 | 5 | UNKNOWN（等级门槛 24/41、只说「降低」235/253、解除条件 282） |
| 整片无数字 | 250 | UNKNOWN（如 20 燃烧「每回合对承受者造成伤害」，连百分比都没写） |

「大幅度提高」一律不编：157 三花聚顶只录 `turns=6`，倍率留空；418/419 原文是
「修炼 +10」，本服没有修炼表，倍率写 1.0（中性）而不是臆造。

机制按**单位**分表（主角/宠物/每只敌方各一个 `EffectSet`）：全局单表会让
「敌方身上的燃烧」与「主角身上的燃烧」共用计时，tick 一次两边一起掉。
叠加规则来自转录表 `stack` 字段（`refresh` 续时长不叠层 / `add` 累层）。
溺水 279 单独收口——desc 明写「**用加法叠加**…最低可被减至20%」，
与其它乘区语义不同（1 - 0.2×层），混在乘区表里会算错。

接入战斗三个点，**默认零影响**（硬要求，录像复现帧序列被逐帧断言）：

| 接入点 | 默认行为 |
|---|---|
| `_fx_taken()` 承伤修正 | 无状态时**原值返回**，50/48/20 逐位不变 |
| `_fx_force_defend()` | 无状态时 `False`，走原逻辑 |
| `_fx_end_of_round()` 回合末 DOT+计时 | 无状态时**零帧** |

`battle_effect` 标 **partial** 而不是 implemented：机制通用，但数值只覆盖 29/284。

**③ 敌方 AI：决策从帧编排里抽出来**

原来四个判断（打谁/放不放招/哪招/会心）夹在 `_enemy_attack` 的 40 行发帧代码中间。
现在拆成 `pick_target()` + `decide_action()` 两个纯函数。

**为什么拆两步不是一个 `decide()`**：保护关系要先知道「打谁」才能 `pop`，而
「是否放技能」又依赖「有没有发生保护」。顺序天然是 选目标 → 查保护 → 决定动作，
塞进一个函数只能变成回调或**重复抽取**（初版就是调了两次 `decide`，随机消耗翻倍、
同种子复现失效，被测试抓到）。

抽取顺序与原实现一致：`target → skill_chance → skill_pick → hurt`。保护触发时
**仍掷「是否放招」再否决**，不是直接跳过——否则随机消耗比原来少一次。

两种策略：`random`（默认，逐位复刻既有行为）/ `focus`（集火残血）。
`focus` **不消耗** `enemy_target` 流：策略是确定性的，这里若掷一次骰，后续所有流
都会跟着漂，「只换个目标」的策略会顺手改掉会心/掉落的运气。
`enemy_ai` 标 **simulated**：原服怪怎么选目标不可得，两种策略都是本服模拟。

**④ 胜负只结算一次**

`_after_hero_action` 原有 `if not self.in_war` 守卫，但守卫只挡得住「收尾之后的
重复调用」，挡不住「同一批死亡单位在收尾**过程中**被二次结算」。现在加显式计数
`_war_settle_count` + `_war_settle_kinds`，重复结算打 warn 栈。

顺带修掉两个真 bug：

- `_after_hero_action` 原来在循环里逐只调 `_on_enemy_dead()`，一次全灭 3 只发
  **3 遍 TaskAdd** + 落盘 3 次。改成先全部 WarDel、再 `_on_enemies_dead(conn, n)`
  统一推进：**帧序不变**（WarDel 仍逐只 3 帧）、**进度语义不变**（每只 +1）、
  TaskAdd 与落盘各收敛到 1 次。
- `dead_ids` 里有重复或已死 id 时，旧实现照样推一格任务进度（凭空多加）。
  现在只算真正从 `enemy_hp` pop 掉的。

六路入口都测结算次数：普攻全灭 / 3 只同灭 / GM win / 战败 / 逃跑成功 / 收尾后重入。

---

## 7. 战斗引擎

### 7.1 开战序列

```python

    def _start_war(self, conn):
        if self.in_war:                             # 不可重入（g_WarTimes 只增不减）
            return False
        self.in_war = True
        self._pending_pet_suffix = None
        self._cmd_consumed = False
        self._battle_round = 0
        self._team_war_ids = {}
        self.hero_hp = HERO_HP                      # 每场开战回满血，并同步 g_Prop.m_HP
        self._pet_alive = (self.active_pet_id in self.pets
                           and int(self.pet_hp) > 0) # 倒地宠物不参战
        conn.sendall(build_prop_change_hp(self.hero_hp))
        self._reset_hero_res()                      # 战斗资源同样回满
        for (name, idx, width) in COST_PROPS:
            conn.sendall(build_prop_change(idx, self.hero_res[name], width))
        self._reset_pet_res()
        ...
        # 场景选怪：剧情战斗 > 本图场景表 > 全局兜底（城镇/室内不遇怪）
        _enc = self._encounter_def()
        self.enemy_meta = {ENEMY_WAR_ID + i: {'spawn_index': i, 'speed': None,
                                              'shape': _e_shape, 'name': _e_name}
                           for i in range(self.enemies)}
        conn.sendall(build_war_start(self.mapid - 1001, 1, 1, 0, self.px, self.py))
        conn.sendall(build_war_add(HERO_WAR_ID, self.hero_shape, True, self.hero_name or '你'))
        if self._pet_alive:
            _pname, _pshape, _pdef = self._pet_info()   # ← 出战实例，不是模块常量
            conn.sendall(build_war_add(PET_WAR_ID, _pshape, True, _pname,
                                       owner_id=HERO_WAR_ID))   # owner_id → 宠物排主角前排
            conn.sendall(build_war_summon(PET_WAR_ID))          # op129：标记为可指挥召唤单位
        if st.active:                                           # 队友只做表现，不进行动序
            for i, m in enumerate(st.members[1:TEAM_WAR_SLOTS + 1]):
                conn.sendall(build_war_add(TEAM_WAR_ID + i, TEAM_ALLY_SHAPE, True, ...))
        for wid in self._enemy_ids():                           # ← 按 spawn_index，不排序
            _meta = self.enemy_meta.get(wid, {})
            conn.sendall(build_war_add(wid, _meta.get('shape', ENEMY_SHAPE), False,
                                       _meta.get('name', ENEMY_NAME)))
        conn.sendall(build_war_turn(0))
        self._arm_cmd_timer()                       # 交出操作权 → 30s 倒计时
```

**三处「看起来只是写法不同、其实是契约」的地方**（都改过一次）：

| 写法 | 曾经 | 为什么必须现在这样 |
|---|---|---|
| `self._enemy_ids()` | `sorted(self.enemy_hp)` | 排序依赖「id 数值恰好等于入场序」这个**分配方式的副作用**，不是契约。某处重排 `enemy_hp`（存档还原、调试脚本）就会让 WarAdd 顺序与速度序错位 |
| `self._pet_info()` | `PET_SHAPE` / `PET_NAME` 模块常量 | 宠物改成实例表后，常量只是「默认宠物」的快照；直接用会「换宠/升级后战场造型与名字不变」 |
| `self._pet_alive` 现算 | 恒 `True` | 倒地宠物跟着进场会显示一个 0 血单位，且召唤菜单点它直接报错 |

> 注意主角那一帧用的是 **`self.hero_shape`**（建角时由 `icon/major` 映射出的 12 个合法
> 人物造型之一），不是写死的 `1`。写 `1` 会让客户端按 `hero/0001` 取贴图，主角渲染异常。
> `build_war_summon(PET_WAR_ID)` 也不能省：没有 op129，客户端不会在主角交完指令后
> 弹宠物指令菜单，服务端就永远等不到 `C2S 168-174`。

| 项 | 取值 | 说明 |
|---|---|---|
| 场景字段 | `mapid - 1001` | 客户端读时 `+1001` 还原 |
| 主角 id | `100` | **必须非 0**（见下） |
| 敌方 id | `1..N` | `ENEMY_WAR_ID + i` |
| 宠物 id | `250` | 1 字节，避开主角/敌方 |
| 敌方数量 | 默认 1 | 依录像改为 1 只（其余狼是场上明雷怪） |

> **主角 id 不能是 0**：客户端 `war.Add` 用 `hero.g_Prop.m_Name == obj.m_Name` 命中主角并
> `war.g_HeroID = obj.m_ID`，而客户端到处 `if war.g_HeroID and ...`。Python 里 `0` 为假
> ⇒ 整条「玩家可操作」链路失效：指令菜单不出现、点/右键敌人什么都不发。
> 服务端因此收不到 `C2SWarAttack(160)`，表现为「战斗菜单不出现、无法出手」。

开战还会做三件事：回满血并 `PropChange HP`、回满战斗资源并逐项 `PropChange`、
按造型解析本场速度/技能/会心率。

### 7.2 三个位域/符号约定（全部由客户端源码定死）

```python

def build_war_wstate(wid, hurt, life, ward, hp_chg):
    """S2CWarWState（op123，tag18）：id(1) bitfield(1) hp_chg(4 signed)。
    ...
        hurt = bit7..4（高 4 位）、life = bit3..2、ward = bit1..0（低 2 位）。
        bitfield = (hurt<<4) | (life<<2) | ward
    """
```

| 字段 | 取值 | 错发的后果 |
|---|---|---|
| `ward` | 1=HIT / 2=DEFEND / 3=EVADE | 造成伤害必须发 1；发 0 客户端走 `Evade()`，演成闪避且无打击特效 |
| `life` | 2=DEAD | 只有 `== 2` 才 `obj.Die()` 播死亡动画 |
| `hp_chg` | **正数 = 扣血** | 负数被 `CreateModifyHP` 当回血，走 `war\hp\` 数字 |
| `hurt` | 1 = 会心（红字） | 纯表现层，不改数值 |

`WarAdd(115)` 的 `allied` 在 **bit7（MSB）**：

```
bitfield: allied=bit7(0x80), type=bit6, child=bit5, reserved=bit4..0
```

依据是 `mxmain.dll` 的 `CNetClient.UnpackBit`（VA `0x41a4d0`）为 MSB-first。
历史 bug：按 LSB 发 → 所有单位 `allied=0` →「玩家在对面」。

### 7.3 回合结算：速度序 + 宠物挂起

`_resolve_hero_round`把一回合拆成三段：

```
order  = 存活单位按速度降序（稳定排序；同速时主角在前）
prefix = order 中快于主角的单位        → 立即结算
hero   = 主角指令（普攻/招式/防御/逃跑/道具/保护）
suffix = order 中慢于主角的单位
   ├─ 宠物在 suffix：
   │    先结算「宠物之前」的慢位 → WarTurn(1)
   │    _pending_pet_suffix = 宠物之后的慢位；arm 30s，等 C2S 168-174
   │    收到宠物指令 → _finish_pet_turn → 结算剩余 → WarTurn(0)
   └─ 宠物不在 suffix：结算全部 → WarTurn(0) → 重新 arm
```

**为什么必须拆**：客户端是「先提交主角指令 → 再打开宠物菜单 → 提交宠物指令」，
两帧异步到达。服务端若一次结算完，宠物指令到达时回合已推进，客户端卡「请等待」。

速度真值（依录像）：

| 单位 | 速度 |
|---|---:|
| 主角 | 100 |
| 宠物 | 49 |
| 敌方 shape 1（企鹅类） | 115 起，逐个 -5 |
| 敌方 shape 2130（野狼） | 40 起，逐个 -5 |
| 下限 | 1（录像最大阵容 6，更大无真值） |

> **7–10 单位阵容的实证（2026-10-19）**：`ENEMY_SPEED_STEP = 5` 递减 + `ENEMY_SPEED_MIN = 1` 钳位，
> 在 **10 敌时必然产生同速**（敌9 与敌10 都被钳到 1）。这是目前唯一能在自然梯度下
> 观察到同速的常规阵容。排序稳定性由 `tests/_test_speed_stability.py` 守着（17 项）——
> 变异测试确认：改成「按 uid 决胜」「排序键取反」「用 set 破坏稳定性」三种写法都会判红，
> 其中 set 那版会把钳位段变成 `…8, 10, 9`，**序错了但不报错**。
>
> ⚠ **隐式契约**：同速次序目前**取决于 `enemy_hp` 的插入顺序**（Python 稳定排序）。
> 现有构建路径都是有序的，所以线上正常；但存档合并 / GM 加怪若乱序重建该字典，
> 同速次序会跟着变，而客户端不显示序号——出错只表现为「某只怪先动了」。
> 测试里专门有一条 `插入顺序会决定同速次序` 把这个契约显式化。

同速时**不要用 uid 决胜**：主角 id 从 0 改成 100 后，用 uid 会让同速的主角排到敌方之后。

### 7.4 敌方行动与保护

`_enemy_attack`在「主角 / 存活宠物」中随机选目标，
`_round_protectors` 命中则改打保护者，并**强制走普攻表现**——客户端只在
`AttackNormal` 命令里查找保护者。

固定帧序：

```
WarPerform(122)  或  AttackNormal(117)
  [→ WarProtect(121) + WState(ward=DEFEND, hp_chg=0)]
  → WState(123)  伤害飘字
  → HPPercent(125)  友方血条
  → AttackNormalEnd(119)  归位 + 推进命令队列
  → PropChange(17)  主角真实血量（仅主角）
```

`HPPercent(125)` **只能对友方发**：敌方单位没有 `m_HPFull` 对象（`warrior.Create` 只在
`allied` 分支创建），对敌方发会 `AttributeError`，被 `war.Process` 吞掉后 systrace。

敌方有 `ENEMY_SKILL_CHANCE = 0.4` 概率放招式；技能**按造型**取
（`ENEMY_SKILLS_BY_SHAPE = {1: [1103], 2130: [415]}`，兜底 `[415]`）。
会心概率**按造型查表**，未登记造型回落全局：`ENEMY_HURT_BS_BY_SHAPE = {2130: 0.38}`，
兜底 `ENEMY_HURT_BS_CHANCE = 0.35`。

> 这张表的历史值得记一笔，因为它是「先立结论、后补证据」的典型：
> 最早据「野狼必红」把 2130 写进表，随后发现该推断来自**错误的 LSB 位域拆解**
> 与混合录像族，一度把表清空。按 MSB-first 且只取新族录像复核后，野狼有效样本
> **45 命中 / 17 会心 ≈ 0.38**——这才重新登记。
>
> 但 `n=45` 与全局 0.35 **在统计上不可区分**（差值在噪声内），所以
> `FEATURE_STATUS['enemy_hurt_bs']` 仍是 `simulated` 而不是 `implemented`：
> 表在、值有、机制跑通，但「原服真的按造型分」这条**规则本身未证实**。
> 补证据的门槛是同造型 ≥30 次有效命中且录像族一致
> （见《客户端网络层解析_20261018.md》§5.4）。

### 7.5 结束链（顺序错就丢奖励）

```
WarDel(116) ×N  →  WarEnd(113)
                     ↓ 客户端 ClearAllObject()
              _resync_scene: SummonInfo(48) + SceneM(96) + AddNpc + GotoM
                     ↓ 场景重建完成后
              _award_battle_exp  → PropChange(经验)
              _roll_drops        → ItemAdd(33) + NotifyExt(164) 飘窗
```

经验与掉落必须在 `WarEnd + 场景重同步`之后，否则 `PropChange` / `ItemAdd`
会被场景重建冲掉（表现为「打了怪没经验/没掉落」）。

战败（主角 HP≤0）：`WarDel ×N` → `WarEnd` → 清战斗态 → `_resync_scene`，无奖励。

### 7.5.1 GM 开战 / 胜利：走真实链路，并把帧序标红（2026-10-20）

调试台和 GM 面板的「强制开战 / 一键胜利」**不是**直接改状态位：

| 动作 | 调进 | 发出的帧（与打怪打赢完全同源） |
|---|---|---|
| `war start` | `_start_war(conn)` | 回血/回资源 `PropChange` ×6 → `WarStart(112)` → `WarAdd(115)`×(我方+敌方) → `WarSummon(129)` → `WarTurn(114)` |
| `war win` | `_after_hero_action(conn, dead_ids=全部敌方)` | `WarDel(116)`×N → `WarEnd(113)` → `_resync_scene`(48/96/99…/97) → 经验 `PropChange(17)` → 掉落 `ItemAdd(33)` |

为什么这条要单独记：**GM 是唯一能随时复现帧序的入口**。它要是走了捷径
（直接置 `in_war = False`），拿它验契约就等于拿假数据验契约。
`tests/_test_war_chain.py` 用变异测试钉死了这一点——把 `gm_war_win` 改成直接
置位，8 条断言立刻判红。

#### 帧序标红怎么做：事后按 seq 区间回填

发帧时打不了标（`m4_server` 不认识 `tap`，也不该认识）。改为**事后回填**：

```python
lo = tap.last_seq()
r  = srv.gm_war_start()          # 或 gm_war_win()
hi = tap.last_seq()
tap.mark_range(lo + 1, hi, 'GM 开战', kind='war_start')
```

能这么做的前提是 `gm_war_*` 全程持 `self._lock` 同步跑完——期间没有别的线程插帧，
所以「调用前后的 `last_seq()`」正好圈住这条链路（锁的这条保证见 §4.1）。

三个容易踩的点，都写进了 `tap.mark_range` 与测试里：

| 坑 | 现象 | 处理 |
|---|---|---|
| `tap.frames(since=N)` 是**开区间**（`seq > N`） | 直接拿它取链路会丢掉首帧 | 取区间用 `since=lo-1` |
| 两段链路共用 `last_seq()` 当边界 | 同一帧同时属于开战段和胜利段，两段都标红 | 下界取 `last_seq() + 1` |
| 被拒的命令（已在战斗中/不在战斗中）一帧不发 | 记下一条 `frames=0` 的空段落，`mark_kinds` 还 +1，界面显示「GM 胜利 #86-86（0 帧）」 | `hi > lo` 才记段落 |

界面侧：`.row.warcmd` 左缘红条 + 淡红底，标签写「真实链路·GM 开战 #59-73（15 帧）」，
详情面板显示「本帧是第几帧」；顶部有「只看链路」开关。
因为标红是**事后**回填的，已渲染过的行不会自己变红——GM 动作返回后
前端调 `refreshMarks()` + `repaintMarks()` 按区间就地重绘（不整表重建，免得丢滚动位置）。

> 前端这半也有测试：`check_frontend_marks()` 用 node 加载 `debugger.html` 的脚本
> 断言 `rowCls` 出 `warcmd` 类、标签带区间、`chainOf` 能按 seq 反查段落。
> 只测后端等于只测了一半——后端标对了、界面没涂红，用户看到的还是「没标」。

### 7.5.2 物伤公式（反向还原，**已接入，参数化**）

2026-10-19 反向还原出的「基础物伤」原函数：

```
Deff = D × (1 − P/(30·Lt))
Deff ≥ A  → 0.1A
Deff < A  → 1.3 × (A − Deff)
```

参数候选：`A=m_At`、`D=m_Df`、`P=m_Dp（破防）`、`Lt=等级`；**映射本身仍未确认**。

已落成纯函数 `sim/damage_model.py` + 边界单测 `tests/_test_damage.py`（已挂进回归），
可行域求解在 `协议资料/报告/_damage_feasible.py`。用 47 条录像真值（50×23 / 48×13 / 20×11）
算出的结论：

| 结论 | 依据 |
|---|---|
| **取整只能是截断** | 四舍五入给不出 50/48/20（实得 51/48/21）；截断下 gap 唯一 = 39 / 37 / 16 |
| 1.3 与数据自洽，但**不被数据证明** | 能同时产出三值的最小 gain 区间为 [1.2973, 1.3077)；扫描 0.5~3.0 还有 38 段同样可行 |
| `if` 写法在 `Deff=A` 处**不单调** | D=99→伤害 1，D=100→伤害 10：防御更高反而更疼；`max(0.1A, …)` 写法无此问题 |
| 保底分支**零样本** | 47 条全在正常分支（最低伤害 20） |
| `(A,D,P,Lt)` **严重欠定** | 只有三个伤害值，没有跨等级/跨防御的对照；P 是破防还是等级也无法判别 |

#### 接入方式（A 方案 + 参数化 + 双模型）

`(A,D,P,Lt)` 欠定，所以取**能精确复现录像的可行点**，并把每个数都外置到
`world_config.json` 的 `battle` 段——回归时可随时调参。现在有**两个模型**，
各有一套参数点，都能复现 50/48/20。

> ### ⚠️ 默认模型是 `reverse`，但「有出处」≠「已复刻」
>
> **这一节最容易被误读的一句话**：看到模拟服打出 50 / 48 / 20 就和录像对上了，
> 于是认为「原服公式已经复刻」。**这个推论是错的。**
>
> 两个模型**都能**精确复现那三个数——也就是说，**光看伤害数字分不出谁对**。
> 它们的区别不在「准不准」，而在**有没有出处**：

| 模型 | 来源 | 证据强度 | hero_at / hero_df | pet_at | enemy_at | 斜率 |
|---|---|---|---|---|---|---|
| `reverse`（**默认**） | 反编译 `mxmain.dll` 得到的原函数形状 | **有出处，可复核** | 39 / 15 | 37 | 31 | 1.3 |
| `linear` | 一份「外部受控变法实验表」 | **来源不可考** | 100 / 60 | 96 | 100 | 0.5 |

★ 2026-10-20 起默认翻转为 `reverse`：linear 当初当默认的唯一理由是「便利」
（加减法 `(攻−防)/2` 直观、调参时改一个数就能预测结果，而反编译模型的
`Deff = D×(1−P/(30·Lt))` 三常数联动、不好手动试）——**便利 ≠ 可信**，
默认值应该站在有出处的那一边。linear 保留为可选项，切过去**必须同步换参数点**，
否则差 2.6 倍（0.5 vs 1.3 的斜率差）。

`linear` 不可考到什么程度：

- 该表量级（攻击 690 / 防御 330）**超出本作 TBYTE(0..255) 上限**——本作属性根本存不下这么大的数；
- 同批资料里的「撕天 / 威力293 / 效率28」在本作 `petskill`/`magicdata` 中**查无此项**
  （本作效率取值只有 90%~140%）；
- **原始出处文件不在本仓库**，任何人无法复核这份表到底是什么。

但 reverse 的「有出处」也**仅指公式形状**：`(A,D,P,Lt)` 到属性的映射仍然欠定
（47 条录像只有三个伤害值，多个点都能拟合），参数取值依旧是**模拟参数**——
「公式有出处 + 参数欠定」合起来才是完整结论，缺一半都会误导。

起服横幅把当前模型与参数点打出来（reverse 还会打出公式常数）；切到 linear 时
追加一行否定性标注：

```
伤害模型：reverse（反编译还原（mxmain.dll 基础物伤函数，有出处；47 条录像可精确复现））
  参数点：hero A/D/P/Lt=39/15/4/1  pet=37/15/4/1  enemy=31/0/0/1
  公式常数：gain=1.3 floor=0.1 p_div=30.0 mode=if rounding=trunc
  实算伤害：主角普攻 50 / 宠物 48 / 敌方打我 20
```
```
伤害模型：linear（**非真值**：来源不可考（外部实验表，量级超本作 TBYTE），
          仅因加减法直观而保留为可选项；默认模型是 reverse）
  ⚠ 当前用的是便利选项 linear（**非默认**），来源不可考，**不代表已复刻原服公式**；默认模型是 reverse
```

切到便利选项 linear（记得同时换参数点，否则差 2.6 倍）：

```json
// world_config.json → battle
{ "dmg_model": "linear",
  "hero_at": 100, "hero_df": 60, "hero_dp": 4,
  "pet_at": 96, "pet_df": 60, "pet_dp": 4,
  "enemy_at": 100, "enemy_df": 0, "enemy_dp": 0 }
```

> 若将来拿到 `linear` 那份实验表的原始出处（并证明它属于本作），linear 才够资格竞争默认；
> 在那之前，默认站在有出处的 reverse 一边——但它的参数点仍是模拟值。

**怎么补证**：见《战斗录像采集清单》（`协议资料/报告/战斗录像采集清单.md`）。
它按「**能区分什么**」而不是「填满网格」组织——48 个网格点里只有 3 个真正有辨识力，
其余 41 个会重复产出同一个值。清单给出 18 场核心采集（K/N 组）+ 操作规范 + 成功判据。

| 配置键 | 默认 | 说明 |
|---|---|---|
| `dmg_model` | `'reverse'` | `'reverse'`（默认，反编译出处）/ `'linear'`（可选，来源不可考），切换**必须同时换参数点**，否则差 2.6 倍 |
| `hero_at / hero_df / hero_dp` | 39 / 15 / 4 | reverse 点：主角打怪 ⌊1.3×(39−0)⌋=50；`df` 须与 `pet_df` 相同（录像里怪打人/打宠都是 20）；`dp≥4` 是 `warui` 技能可用下限 |
| `pet_at / pet_df / pet_dp` | 37 / 15 / 4 | 宠物打怪 ⌊1.3×37⌋=48 |
| `enemy_at / enemy_df / enemy_dp` | 31 / 0 / 0 | 怪打人 ⌊1.3×(31−15)⌋=20；敌方防御取 0 使主角/宠物的攻防差就是 `A` |
| `hero_level / pet_level / enemy_level` | 1 / 1 / 1 | `reverse` 模型里 `P=0` 一侧不参与，Lt 仅占位 |
| `dmg_gain / dmg_floor / dmg_p_div` | 1.3 / 0.1 / 30.0 | 反向还原三常数，只对 `reverse` 生效 |
| `dmg_rng_lo / dmg_rng_hi` | 1.0 / 1.0 | 伤害浮动。**默认关闭**：47 条录像同一来源零方差，真有 ±5% 浮动不可能一个 49/51 都不出现；想试外部表的 ±5% 就填 0.95/1.05 |

```python
# m4_server.py：一次伤害的唯一入口（模型/参数/浮动全部可被配置覆盖）
def _dmg(self, atk, df, dp, lv):
    base = dmgm.physical_damage(atk, df, dp, lv, gain=DMG_GAIN,
                                floor_ratio=DMG_FLOOR, p_div=DMG_P_DIV,
                                mode=DMG_MODE, rounding=DMG_ROUNDING,
                                model=DMG_MODEL)
    return int(base * dmgm.rng_factor(DMG_RNG_LO, DMG_RNG_HI))
```

三个接入点：`_hero_attack_damage()`（主角普攻/招式）、`_pet_attack` / `_pet_perform`、
`_enemy_attack`（分主角/宠物两路）。同时 `build_prop_power` 改发这三围
（原来是占位 `100/100/100`）——客户端 `warui` 用同一组数判断招式可用性，两边必须是同一份。

**力量加成必须加在公式之外**：加成是「每 4 点力量 +1 点伤害」的约定值，若加进 `A` 会被
斜率放大（linear 0.5 / reverse 1.3），且与录像口径（未加点 = 50）不一致。

> 旧常量 `ATTACK_DMG / ENEMY_ATTACK_DMG / PET_DAMAGE` 仍在，但已降级为
> **当前参数下的快照**（50 / 20 / 48），只供旧断言与文档对照；配置里对应的
> `attack_dmg / enemy_attack_dmg` 两个键已删除——它们不再驱动任何计算。
> ★ 这三个快照必须在 `world_config.apply_to()` **之后**计算，否则会出现
> 「快照 50、实算 19」的自相矛盾（早先就是这样，已修）。

### 7.5.3 真值战斗公式落地（2026-10-21：petfightdata.py + petdata.py + 资料库）

#### 7.5.3.1 宠物战斗属性真值公式
客户端 `petfightdata.py` 直接给出了宠物战斗属性的成长公式（✅ 真值）：

```
hp  = lv²·5 + lv·100
at  = lv¹·⁵·1.5 + lv·40
df  = lv¹·⁵·1.4 + lv·30
mg  = lv¹·⁵·1.5 + lv·40   # 法攻/灵力
md  = lv¹·⁵·1.4 + lv·30   # 法防
sp  = 1.13^(lv-1) · 30    # 速度
```

每个宠物种族有 `FactorData`（种族修正系数）和 `CorrectData`（基础修正值），
最终属性 = 公式(lv) × factor + correct。`sim/battle_formulas.py` 完整收录了这两表（28 种族）。

**接入方式**：把「战斗计算值」与「面板显示值」分离（`m4_server.py`）：
- `*_COMBAT`（`PET_AT_COMBAT` 等）供伤害公式用，保持 reverse 标定值（0..255，兼容录像 50/48/20）
- `*_DISPLAY`（`PET_AT_DISPLAY` 等）发给客户端面板，用真值公式计算（0..65535，TWORD）

#### 7.5.3.2 敌方属性等级缩放
资料库："普通怪物：攻击和防御能力受到等级影响" ⚠️ 半真值。
`sim/battle_formulas.py` 提供 `calc_monster_stats(level)`，基础值可被 `world_config` 覆盖。

#### 7.5.3.3 控制点系统落地
资料库 §9128-9134："控制点是用于控制战斗节奏的一种辅助手段，共有3种：攻击点、防御点、干扰点。"
- 对NPC战斗：初始各 30 点
- 对玩家战斗：初始各 0 点
- 攻击类招式消耗攻击点，回复类消耗防御点，封印类消耗干扰点
- `m4_server.py` 初始化 `_control_points = {atk:30, def:30, dis:30}`，预留招式消耗/回复接口

#### 7.5.3.4 逃跑机制真值化
资料库 §30998/31023/31030："逃跑并非百分百成功，有一定几率失败，失败的话则会被对方白打一回合。"
- 基础成功率 50% + NPC战斗加成 30% - 队长压制 + 自身技能加成
- 失败惩罚：敌方获得免费攻击回合（`_escape_failed` 标记 → 额外敌方行动序列）
- `_try_escape()` 在 `m4_server.py` 实现，`C2S_WAR_ESCAPE` 处调用

#### 7.5.3.5 配置验证范围修正
客户端 `hero.ITEM_ALL` 明确 `m_Att_All`/`m_Def_All`/`m_Spe_All`/`m_Magdef_All` 等为 **TWORD (2 字节, 0..65535)**。
`world_config.py` 的验证注释修正为：战斗计算值保持 0..255（兼容 reverse 公式），面板显示值允许 0..65535。

### 7.5.4 GM 战斗调试工具（2026-10-21 完工）

GM 调试工具提供战斗时的**全状态快照 + 实时控制**，所有动作都走**真实内核链路**（同 `gm_*` 方法、控制台、调试台），**无任何伪造指令**。

| 入口 | 动作 | 内核方法 | 说明 |
|---|---|---|---|
| GM 面板 ⚙ 战斗调试卡 | 实时轮询 `/api/battle`（1.2s） | `gm_battle_dict()` | 单次锁内取全量：`in_war`、单位层（hero/pet/enemy 分层）、参数层、运行态、真值属性表 |
| 控制台 `gm_command` | `battle` / `cp [a d d]` / `escape <mode>` / `petlevel <n>` / `enemylevel <n>` | 对应 `gm_*` 方法 | 交互式 CLI，同面板一致 |
| 调试台 SSE | `battle` 事件（每帧推送） | `Broadcaster` 广播 `gm_battle_dict()` | `sim/debugger.py` 的 `GM_ACTIONS` 扩展：`battle`、`cp_set`、`escape_force`、`pet_level`、`enemy_level` |
| HTTP API | `GET /api/battle` `POST /api/battle/cp\|escape\|petlevel\|enemylevel` | `_GMHandler` 路由 | 面板/脚本调用，返回 `{ok, result, state}` |

**可观测字段（`gm_battle_dict` 返回）：**
- `in_war`：是否在战斗
- `dmg`：当前模型/常数/快照常量（`ATTACK_DMG`/`PET_DAMAGE`/`ENEMY_ATTACK_DMG`）
- `hero/pet/enemy`：各单位 `at/df/dp/level`，**宠物分 `combat`（0..255，伤害用）与 `display`（0..65535，面板用）**，并暴露 `true_stats`（petfightdata 全 6 项 + 疲劳）与 `attr_lv`（资质等级）
- `enemies[]`：本战敌方单位（hp/speed/shape/name/spawn_index）
- `control_points`：当前控制点 `{atk, def, dis}`（0..255 钳位）
- `escape_force`：逃跑强制模式 `null/'success'/'fail'/0..1`
- `escape_nominal`：名义成功率（固定 0.8 = 50%+30% NPC）
- `escape_last`：最近一次逃跑判定 `{time, rate, forced, success}`

**可控动作：**
| 动作 | 参数 | 内核行为 |
|---|---|---|
| `gm_set_cp` | `atk/dfn/dis` 任意子集 0..255 | 部分键更新 `_control_points`，**不触发招式消耗**（技能消耗 CP 仍待接线） |
| `gm_set_escape_force` | `success` / `fail` / `auto` / `0..1` | 设置 `_escape_force`；`_try_escape` 共用路径，记录 `_escape_last` |
| `gm_set_pet_level` | `level, factor_idx?` | `recompute_pet_display()`：按宠物等级投影资质等级，只动 `*_DISPLAY` 四项 + `_PET_STATS`，**不动 `*_COMBAT` 与 HP/MP/FP**（保持 50/48/20） |
| `gm_set_enemy_level` | `level` | `recompute_enemy_constants()`：按等级重算 `ENEMY_AT/ENEMY_DF`，**不动 `ENEMY_HP`**（仍为配置/模拟代表值），同步 `recompute_dmg_snapshots()` |

> **设计约束**：改宠物等级/敌方等级**只影响面板/名义属性**，不破坏录像 50/48/20 的伤害复现（`*_COMBAT` 与 `_dmg()` 走的 reverse 标定值保持不变）。

**回归测试**：`tests/_test_gm_battle.py`（55 条，全部通过）。

---

### 7.6 启动自述：伤害模型 + 参数点 + 配置来源

起服时横幅会紧跟在内核/多会话 banner 后面打印一段固定格式（`dmg_banner()`）：  <!-- # legacy: 行号指向空行/注释，符号漂移后未对齐 -->

```
[20:11:55] 伤害模型：reverse（反编译还原（mxmain.dll 基础物伤函数，有出处；47 条录像可精确复现））
[20:11:55]   参数点：hero A/D/P/Lt=39/15/4/1  pet=37/15/4/1  enemy=31/0/0/1
[20:11:55]   公式常数：gain=1.3 floor=0.1 p_div=30.0 mode=if rounding=trunc
[20:11:55]   实算伤害：主角普攻 50 / 宠物 48 / 敌方打我 20（浮动 1.0~1.0）
[20:11:55]   参数来源：已应用 world_config.json（60 张地图，updated=2026-10-20T16:05:00）
```

切到 `linear` 时格式不同（加减法那套参数 + 一行否定性标注），因为两个模型用的参数根本不是一个语义：

```
伤害模型：reverse（反编译还原（mxmain.dll 基础物伤，47 条录像可精确复现））
  参数点：hero A/D/P/Lt=39/15/4/1  pet=37/15/4/1  enemy=31/0/0/1
  公式常数：gain=1.3 floor=0.1 p_div=30.0 mode=if rounding=trunc
  实算伤害：主角普攻 50 / 宠物 48 / 敌方打我 20（浮动 1.0~1.0）
```

#### 两条隐患怎么被消掉的

| 隐患 | 旧状 | 现在 |
|---|---|---|
| 「当前用的是哪套参数」 | 只有读 §7.5.2 长文档才知道；跑起来看不出用的是哪套 | 横幅第一行就是来源标注（`DMG_MODEL_CAVEAT`）：`reverse` 明写「反编译还原、有出处」并附参数点与公式常数；`linear`（可选）明写「**非真值**、来源不可考」，并**额外单独一行**警告「不代表已复刻原服公式」 |
| 「配置静默失效」 | `apply_to` 失败时什么都不打印，改配置毫无反应 | 加载结果**无条件**打印（成功 `✓` / 失败 `✗`），并记进 `WORLD_CONFIG_STATE`；失败时横幅还会多一行 ⚠ 提示 |

#### 三个实现要点（都是踩出来的）

1. **实算伤害必须现算，不能读 `ATTACK_DMG` 等快照常量**。那三个是 import 期算的，
   一旦 `DMG_MODEL`/参数点被 `world_config` 或热改覆盖，快照就是陈旧值——横幅会打出
   「模型说 reverse、伤害却是 linear 的 50」。回归里有专门一条断言守着这点。
2. **打印位置在 `run()` 而不是 `__init__`**。多会话模式下 `__init__` 每连一个客户端跑一次，
   横幅会刷屏；`run()` 只在真正起监听时调用一次。
3. **`WORLD_CONFIG_STATE` 区分五种状态**：`applied` / `missing` / `invalid` / `failed` /
   `disabled`（`--no-world-config`），另有 `error`（配置层抛异常）。

### 7.7 保底与「请等待」

客户端「请等待」只在两种情况下消失：收到 `WarTurn`（→ ShowHeroMenu → ShowTimer）
或 `WarEnd`（→ Hide）。所以战斗中对任何未做逻辑的指令都必须至少回一帧 `WarTurn`：

```python

    def _fallback_turn(self, conn, op):
        """战斗中对本服无逻辑的指令：至少回一个 WarTurn，避免客户端永久停在「请等待」。"""
```

反过来，**非战斗帧绝不能落到这里**——这就是 `DISPATCH_PRE_WAR_OPS` 存在的理由。

另一个死锁：重复发 `WarStart` 会让客户端 `g_WarTimes` 累积（`WarEnd` 才 -1），
`g_WarTimes != 0` 时后续 `WarEnd` 被直接忽略 → 战斗无法结束。所以每场只发一次 `WarStart`。

### 7.8 玩家招式：逐级消耗 / 威力 / 效率（2026-10-22）

招式链路以前是「1 级口径」：不管练到几级，消耗与伤害都不变。现在全部按
**当前等级 `slv`** 从 `magicdata` 的表达式逐级求值。

```
C2SWarPerform(162) pid + target
  → _accept_turn()                    窗口闸门（§7.10）
  → pid in self.magics?               未学 → WarTurn(0)，一分不扣
  → _magic_shortage(pid)              资源不足 → WarTurn(0)，一分不扣
  → _magic_targets(pid, target)       群攻按 slv//6+2 取 N 个
  → _resolve_hero_round(act)          进速度序
        act: WarPerform(122) → 每目标 WState(123)
             → _magic_damage(pid)     伤害
             → _spend_magic_cost(pid) 扣本级消耗 + PropChange(17) ×5
```

**三个取值口，全部逐级：**

| 量 | 函数 | 表达式来源 | 性质 |
|---|---|---|---|
| 等级 | `_magic_level` | 存档 `m_Level`，钳到 `magicdata.max_lv` | 真值（上限由客户端表定） |
| 消耗 | `_magic_cost` → `magic_cost_at` | `mana`/`fp`/`at`/`df`/`dp` 表达式 | **真值**（公式本体在客户端表里） |
| 伤害 | `_magic_damage` | `pow` / `powrate` 表达式 | **SIM**（模型名 `sim-pow-eff-anchored-v1`） |

伤害模型只有一行：

```
dmg = ⌊ 普攻伤害 × (pow(slv)/pow(1)) × powrate(slv)/100 ⌋
```

分母 `pow(1)` 是**锚点**——把威力归一化到该招式自己的 1 级值，于是
「1 级 = 1.0 倍普攻」这条基线自动成立，不需要为每个招式手工标定系数。
乘数钳在 `[0.05, MAGIC_DAMAGE_MAX_MULT]`（5.0），防止表里某个离谱表达式
把伤害顶到天上。

> **这个模型有多可信？** 语料是 11 次放招命中、**伤害全是 50**——对「威力」
> 零辨识力，而且语料是本服自录的。所以它只保证「随等级单调增长、1 级≈普攻」，
> **不外推、也不声称是原服公式**。校准表 `MAGIC_CALIBRATION_HITS` 记的就是
> 每招实测命中次数（1101/1301/2101/2401/2501），容差 ±3；它的用途是
> 「改动后这 11 个点不许漂」，不是「证明公式对」。

**群攻目标数** = `slv//6+2`（`magic_target_limit`）。注意客户端算消耗用的是
**同一个式子**，所以「服务端打 3 个、客户端按 3 个扣」天然同源；改一处必须改两处，
否则玩家看到「扣了 3 个的蓝、只打了 1 个」。

**目标回落**：指定目标非法/已死 → `_resolve_target` 回落到首个存活敌方，
并且**把回落后的 id 写进 122 帧**。客户端按帧里的 `vic_id` 播受击动画，
所以「服务端打谁」和「画面上谁挨打」永远是同一只。

**三种拒绝都是「零成本重选」**：未学 / 资源不足 / 无存活目标（后者直接走收尾）。
旧实现里资源不足也照扣、或直接不回应，前者让玩家白亏蓝，后者让客户端停在「请等待」。

### 7.9 宠物招式：按 `type` 分流，不做的一律回落普攻（2026-10-22）

宠物技能表 `petskill` 的 `type` 决定它能不能出现在战斗菜单：

| type | 含义 | 本服处理 |
|---:|---|---|
| 0 | 被动 | **不进菜单**；若客户端发上来 = 非法指令 |
| 1 | 主动攻击 | 走 `PET_SKILL_EFFECTS` 结算 |
| 2 | 主动辅助 | 无证据，**回落普攻**并写明原因 |

`_pet_perform` 的五道闸门，**任何一步失败都回落普攻**：

```
① 在本宠物技能表内？     否 → 客户端发错了表
② PET_SKILL_EFFECTS 有登记？ 否 → 未实现，不假装有
③ type 是 1/2？          否 → 被动不进程菜单
④ 宠物 FP/MP 够？        否 → 与客户端 PetMagicInvalid 同口径
⑤ 目标解析（非法/已死 → 首个存活敌方）
```

设计原则一句话：**宁可打出一个「看起来像普攻」的数字，也不打出一个
「看起来像招式、其实是普攻」的数字**。前者玩家能理解，后者是欺骗。

**已登记的效果（只有 1 条）：**

```python
PET_SKILL_EFFECTS = {
    415: {'name': '乱舞冲击', 'type': 1, 'effect': 'attack_group',
          'targets': 2, 'pow_rate': 1.0, ...},
}
```

`targets=2` 是 SIM（`petskill` 的 desc 只说「攻击多个目标」，不给数字）；
`pow_rate=1.0` 也是 SIM——415 没有威力/效率字段，**不凭空加倍**。

**被动**（`PET_PASSIVE_EFFECTS`）只有两条有数值证据，其余显式禁用并写明理由：

| id | 效果 | 状态 | 依据 |
|---:|---|---|---|
| 112 | 必杀（`crit_up`） | **启用** | desc 给了加成值 |
| 102 | 连击（`combo`） | **启用** | desc 真值：40% 几率追加**一次**普攻，**每次**伤害降为 75% |
| 317 / 319 / 505 | —— | **禁用** | desc 无可用数值，或语义不明 |

> 连击那条特别容易读错：是「每次伤害都降为 75%」，**首击也跟着降**，
> 不是只有追加那一击降。`_pet_attack` 里 `rate` 在两次攻击前就算好。

被动按**出战宠物自己的技能表**算：换一只没学「连击」的宠物就不该连击。
`pet_passive_mute = True` 可整层关掉——与 `enemy_skill_chance = 0` 同性质，
逐帧断言的老测试需要确定性，别让 40% 的连击把帧序搅乱。

**资源扣减走 `S2CSummonInfo(48)` 回推**：`pet.UpdateInfo` 对已存在的宠物只调
`CPet.UpdateProp` 原地改属性（不重建对象、不碰战场 warrior），所以战斗内重发 48 是安全的。
不发就会出现「服务端 FP=0 拒绝施放、客户端菜单还是亮的」——玩家点了技能看到普攻动画。

### 7.10 战斗核心状态机：一个闸门 + 一个收尾口（2026-10-22）

战斗态的问题几乎全出在「**该拒的没拒**」和「**该清的没清**」上，两者都不报错，
后果都在下一场才显现。现在收成两个函数：

**闸门 `_accept_turn()`** —— 所有主角动作入口的第一句：

```python
if not self._accept_turn():
    self.log('   ⚠ xxx 指令越窗/重复 → 忽略')
    return
```

放行条件三条：`in_war` 且 `await_cmd` 且本窗口 `_cmd_consumed == False`。
放行后置 `_cmd_consumed = True`，窗口由 `_arm_cmd_timer()` 在每回合开局重开。

它同时提供两层保障：

| 层 | 挡什么 | 不挡的后果 |
|---|---|---|
| 合法性 | 战斗已结束 / 窗口已关 / 结算途中的指令 | 误推进回合、误扣资源 |
| 防护 | 同窗口第二条指令（重发 / 双发 / 超时默认动作与玩家指令撞车） | **一次操作被结算两次** |

`_arm_cmd_timer()` 做三件事：`await_cmd = True`、`_cmd_consumed = False`、
`cmd_deadline = now + TURN_TIMEOUT(30)`（对齐客户端 `warui.ShowTimer(30)`）。

**收尾 `_reset_war_state()`** —— 任何「战斗结束」路径的唯一入口：

| 残留字段 | 不清的后果 |
|---|---|
| `_escape_failed` | 下一场第一回合白挨一轮敌方攻击 |
| `_round_defending` / `_round_protectors` | 下一场开局自带防御 / 保护 |
| `_cmd_consumed` | 下一场第一次行动被误判「重复指令」直接吞掉 |
| `_pending_pet_suffix` | 宠物窗口跨场挂起，指令串台 |

`enemy_meta` **故意保留**：GM 战斗面板战后仍要显示上一场的单位造型与速度；
它的清理只发生在下一次 `_start_war`（整表重建）。

**非法指令不消耗回合**，这是本轮改的一条重要语义：

| 指令 | 非法条件 | 处理 |
|---|---|---|
| 战斗道具 165 | 道具不在背包 / 无回血效果 / 目标非法 / 目标满血 | 只回 `WarTurn(0)` 重开窗口 |
| 召唤 166 | sub 越界 / 宠物不在栏 / 寄存 / 气血为 0 / 场上无宠物可召回 | 同上 |
| 保护 121 | 目标是自己 / 非友方 / 已倒地宠物 | 同上 |
| 招式 162 | 未学 / 资源不足 | 同上（**一分资源都不扣**） |
| 特技等 163/164/167 | —— | 合法但无专属演出 → 走 `_resolve_hero_round` 推进整回合 |

旧实现里这些一律「推进整回合」，等于**点错一次白送敌方一轮**。
反过来说，合法的「无演出指令」**必须**走 `_resolve_hero_round` 而不是保底
`WarTurn`——否则敌方不会行动、回合也不推进。

**超时默认动作有个坑**：`_timeout_default_action` 里**不能**先 `_accept_turn()`。
真正的动作入口（`_hero_attack` / `_finish_pet_turn`）自己会过闸门；先在超时分支
消费一次窗口，后者看到 `_cmd_consumed=True` 直接判「重复指令」忽略，默认普攻被吞掉，
客户端停在「请等待」，战斗永远不推进。

---

## 8. 养成闭环（M9）

| 上行 | 语义 | 回推 |
|---|---|---|
| `17 C2S_PROP_LEVELUP` | 空包；经验够就升一级 | `PropChange(grade/exp/next/point)` |
| `16 C2S_PROP_POINT` | 5×1B **增量**，顺序 体/力/魔/魅/敏 | `PropChange(五维 + 点数)` |
| `103 C2S_NPC_LEARN_SKILL` | `skid(2)` | `PropChange(金币)` + `SkillLevel(65) sktype=2` + `sktype=7` |

规则：

| 项 | 值 | 性质 |
|---|---|---|
| 初始五维 | 10 | 本服约定 |
| 初始可分配点 | 5 | 让面板一开局可用 |
| 每级发点 | 5 | 本服约定 |
| 等级上限 | 30 | **本服模拟上限**；原服资料：飞升前 140 / 飞升后 150（客户端 `m_Grade`/`m_GradeLimit` 为 TBYTE，0–255） |
| 升下一级 | `1000 × 当前等级` | 本服规则 |
| 战斗经验 | `60 × 敌数` | **模拟参数**（录像无经验值） |
| 力量加成 | 每比初始高 4 点，普攻 +1 | **本服约定** |

**生活技能 / 体力（M13）**——制作链的两项前置状态，`_enter_world` 各推一帧：

| 项 | 值 | 下发方式 | 性质 |
|---|---|---|---|
| 工艺技术等级 | 起始 5，上限 120 | `SkillLevel(65) sktype=1`：`skid(2)=1117 + sklevel(1) + sklimit(1) + truelv(1) + point(4) + point_max(4)` | skilldata 的 id 是**真值**；起始等级是模拟 |
| 体力 / 活力 | 体力上限 100 起始满；活力上限 100 起始满 | `PropChange(17)` `idx=26/27`（`m_Tili` / `m_Tili_Max`）+ `idx=24/25`（`m_Huoli` / `m_Huoli_Max`），均 TWORD | **真值（属性定义）**：客户端 `src/hero.py` 的 `ITEM_ALL` 明确定义了 `m_Huoli`/`m_Huoli_Max`(24/25) 与 `m_Tili`/`m_Tili_Max`(26/27)，下标与类型由客户端定死；**上限数值 100 仍为模拟**（资料表无该数值） |
| 活力消耗（制作） | 每次制作扣 `craftdata[产物].cost`（16–58） | 成功后推 `PropChange(24)`（`m_Huoli`） | **真值**：cost 取自客户端 `craftdata`（服务端 `MAKE_COST_HUOLI` 仅兜底） |
| 体力恢复 | 每场战斗胜利 +30，封顶 | `_award_battle_exp` 推 `PropChange(26)`（`m_Tili`） | **模拟规则**（数值资料表无） |

> `hero.UpdateSkill` 首句 `sktype -= 1`，所以**线序 sktype=1 → 客户端 ST_SKILL(0)**；
> 发 0 或负等级会让客户端**删除**该技能条目。制作失败一律回 `S2CNpcChatExt`，不动背包与体力。

两个细节：

1. **加点包是增量不是绝对值**。客户端面板先把点数加在本地 `m_Points`，确认时一次发
   5 个增量（`dlgstate` msgid 4）。点数不足整包拒绝，不做部分生效——否则客户端显示
   与服务端错位。
2. **学费必须服务端自己算一遍**，且与客户端 `LearnCostStd` 同式：
   `x = level³×3.5 − level²×20 + (level−1)×40 + 30`，`cost = max(1, int(x*0.1+50))`。
   口径不同 → 玩家看到「显示 51 扣 200」。

### 8.1 宠物实例表：从「一组模块常量」到「多只独立个体」（2026-10-22）

以前的宠物是**全局常量**：`PET_LEVEL` / `PET_AT_COMBAT` / `PET_SHAPE` 各一份，
所有宠物共享，连「第二只宠物」都不存在。现在是实例表 `self.pets[pid] = {...}`：

```
pet_new(pid, shape, level)     ← 建实例（等级/资质/五维/技能/资源全在个体里）
pet_normalize(p)               ← 每次改字段后重算派生值（hpmax/at/dp/…）
pet_true_stats(p)              ← 面板真值层（petfightdata 公式 + 加点加成）
```

| 常量 | 值 | 性质 |
|---|---:|---|
| `PET_SLOT_MAX` | 8 | **模拟**（原服宠物栏格数无该数据） |
| `PET_LEVEL_MAX` | 120 | **模拟 guardrail**（客户端字段为 TBYTE，0–255） |
| `PET_SKILL_MAX` | 8 | 客户端 `dlgpet` 技能格数 |
| `PET_POINT_PER_LEVEL` | 5 | **模拟**（每级发 5 点，与主角同口径） |
| `PET_EXP_SHARE` | 1.0 | **模拟**：出战宠物拿与主角同额的经验 |
| `PET_KEY_BOUND` | 64 | 绑定键上限（防存档键爆炸） |

**升级链**：`_pet_award_exp` → `pet_gain_exp` → 逐级 `pet_exp_need` 判门槛 →
升完 `_pet_frames` 重发 48/49/53（面板 + 技能 + 出战标记）。

`pet_exp_need` 的公式形状是**真值**（客户端 `petfightdata.GetPetData` 的
`exp_need` 表达式），但有一步是 SIM 解释：客户端这份公式**按 6 项资质各算一份**，
面板只显示一个「升级所需经验」。本服取 **hp 那一项（v=3）** 当门槛——
它是 6 项里量级最小、语义最贴「气血资质的成长成本」。挑哪一项是 SIM，公式本身是真值。

**战斗属性也来自实例**：`_pet_combat('at')` / `_pet_level()` 全取**出战宠物**，
所以「换一只宠物」或「升一级」普攻伤害真的会变（`_test_pet_instance` 第 ⑧ 组守着：
同一只宠物 20 级普攻 226 > 1 级 48）。

**召唤/召回与「休憩」是两件事**（易混，单列）：

| 动作 | 入口 | 影响本场 | 影响出战设置 | 保留气血 |
|---|---|---|---|---|
| 召唤 / 召回 | `C2SWarSummon(166)` | ✅ | ❌ | ✅（召回不是倒地） |
| 休憩 / 出战切换 | `C2S 131` / `C2S 132-133` | ❌ | ✅ | ✅ |

召回发 `WarDel(250)` 且**把当前气血写回实例**（`_sync_pet_runtime`），
下回合可再召唤；气血为 0 的宠物不能被召唤进场（`_summon_reject_reason` 直接拒）。
召唤扣 `SUMMON_CP_COST = 4` 控制点——**4 是客户端真值**（召唤前弹「控制点不足」的阈值），
**扣多少无证据**，本服按阈值扣且允许不足时照常召唤只扣到 0。

---

### 8.2 召唤兽装备：op54 下行 + `C2S_ITEM_USE(34)` 上行（2026-10-23 P1）

**两端都不是新 opcode**——这是本节最容易做错的地方：

| 方向 | 帧 | 线序 | 证据 |
|---|---|---|---|
| S2C | `op54`（`netpet.S2CSummEquip` → `pet.UpdateEquip`） | `pid(4)` + 循环 `[pos(1) shape(4) desc_len(1) desc(n)]`，`pos<=0` 结束 | A：客户端反编译 `netpet.py` 逐字段；`dlgpetitem.DrawPetEquip` 只认 pos∈{1,2,3} |
| C2S | `C2S_ITEM_USE(34)` 的 **9 字节**形态 | `item(4) + summon(4) + flag(1)` | A：`dlgpetitem` 右键装备 → `netitem.C2SItemSummonUse`；net.py C2S 表全量核对，**无**独立装备 opcode |

payload ≥255B 时帧信封走扩展长度 `[0xFF][2B u16 LE][payload]`（M1 §1.1〔A〕）——
带长 desc 的 54 就会碰到，按 `[1B N]` 解会直接错位（`_test_pet_equip` ① 守着）。

**规则（官方攻略 docx，资料库 §22302，逐条可引）**：

- 召唤兽 ≥20 级才可使用所有召唤兽装备（`PET_EQUIP_LEVEL_MIN`）；
- 一旦装备就**不能取下**，只能用新装备替换、旧装备消失（实现即「替换」语义）；
- pos 1/2/3 = 项圈 / 铠甲 / 护符（`PET_EQUIP_POS`，来自 `dlgpetitem.pos2obj`）。

**数据边界（重要）**：`shape id` 与**数值效果**双双不可得——客户端
`itemdata.py` 的 `ITEMTYPE` 没有召唤兽装备类别，docx 装备表只有「名称/等级/效果」
三列，且效果是定性的（项圈「提升攻击、灵力或防御」、铠甲「提升防御」、护符
「附加 1~2 种召唤兽技能」），没有任何数值。因此：

- `PET_EQUIP_ITEMS` **初始为空表**，等权威 shape 源出现再按
  `{shape: (pos, name, level)}` 补数据；
- **不把装备接进战斗公式**——猜数值比不做更糟（硬性约束：不得猜测装备属性真值）；
- 机制由 `gm_pet_equip` / 控制台 `pet equip` / `POST api/pet/equip` 三条**同源**入口
  驱动，回归用临时注入 `PET_EQUIP_ITEMS[shape]` 驱动 C2S 真路径（不改生产数据）。

**交付原子性**（与 `_add_pet` 同款）：校验（不动状态）→ 改内存（装备位 + 道具 −1）
→ `_persist()` 拿确定结果（同步看返回值 / 异步 flush 后看 `_save_last_ok`）
→ **确认成功才发** `54` + 道具扣除帧 + `48/49/53`；失败回滚（装备还原、道具不扣）
且**零帧**。等级不足被拒时也回 `48/49/53`——客户端右键后面板可能已本地预亮，
不回就是状态分叉。

**持久化**：`pet_new` 带 `equips: {pos: {'shape', 'desc'}}`；`_pets_snapshot` 以
字符串 pos 落档；`_load_pets` 清洗（pos 越界 / `shape<=0` / 非字典值 / 坏键一律丢弃，
desc 强制转字符串）；登录 `_send_all_pets` 对带装备的宠物补发 54——客户端 `m_Equips`
是**每只宠物实例自带**的字段，切宠不需要重发。

---

## 9. 持久化

```python

    def _persist(self):
        if self._save_blocked:
            ...
        data = {
            'px': self.px, 'py': self.py, 'move_count': self.move_count,
            # 角色身份：登录回读时 _apply_save 靠它恢复（漏存 → 老角色卡「正在登录中」）
            'hero_id': self.hero_id, 'hero_name': self.hero_name,
            'hero_hp': self.hero_hp, 'hero_res': self.hero_res,
            'items': self.items,
            'pets': self._pets_snapshot(),   # 实例表；pet_hp/pet_shape 另存一份给旧档兜底
            'pet_hp': self.pet_hp, 'active_pet_id': self.active_pet_id,
            'pet_shape': self.pet_shape, 'hero_shape': self.hero_shape,
            'coins': self.coins, 'iid_seq': self._iid_seq, 'mapid': self.mapid,
            'tasks': self.tasks,
            'story_point': self.story_point, 'quests_done': sorted(self.quests_done),
            'juqing_skills': {...},          # 剧情点花到哪儿去了，必须看得见
            'sys_switch': self.sys_switch, 'sys_data': {...},
            'mobile': {...}, 'private_info': self.private_info,
            'exp': self.exp, 'next_exp': self.next_exp,
            'grade': self.grade, 'points': self.points,
            'attrs': {...}, 'magics': {...},
            'tili': self.tili, 'huoli': self.huoli,
            'life_lv': self.life_lv, 'life_exp': self.life_exp,
            'team': {...},                   # 只存开关/文案/阵型/目标；队友是生成数据
            '_save_tag': self._save_tag, '_schema': SCHEMA_VERSION,
        }
        # 增量：与上次落盘快照逐键比，只把变化的键交给写线程；全同则直接返回
        dirty = [k for k, v in data.items() if k not in last or last[k] != v]
        ...
```

**两个字段的缺失会直接表现为「玩家视角的 bug」**，所以单列：

| 字段 | 漏存的后果 |
|---|---|
| `hero_id` / `hero_name` | `_apply_save` 恢复不出身份 → 客户端卡在「正在登录中」，老角色永远进不去 |
| `_schema` | 下次读档被当成「版本 0 老档」→ 白跑一遍迁移；迁移不保证幂等，重复跑会丢字段 |

写盘是**原子替换**：同目录 `mkstemp` → `json.dump` → `flush` → `fsync` → `os.replace`。
任一步失败 → 保留旧档、临时文件清理、返回 `False`，绝不留下半截 JSON。

载入侧（`load_role`）四道防御：

1. 非 `dict` 根节点 → 拒绝；
2. `hero_res / items / tasks` 非 `dict` → 拒绝；
3. JSON 字符串键转回 `int`（背包 `iid` / 任务 `tid`）；
4. **现存存档不可读 → `_save_blocked = True`，本次运行禁用自动保存**，
   避免默认新状态覆盖掉可能还能救的旧进度。

触发点：每 10 步移动、切图、买卖、制作、加点/升级/学招式、任务变更、剧情推进、
系统设置变更、生活技能/体力/活力变化、战斗结束、连接断开、进程退出。
存档同时保留 `tili`/`huoli`/`life_lv`/`life_exp`，否则制作消耗、工艺等级与熟练度
会在重启后回到默认值。

**存档是「进程级单文件 + 会话级 tag」**：文件按账号命名（`role_<账号>.json`），
文件锁保证并发写不坏，`_save_tag` 保证「谁最后写的」可查（见 §9.1）。

### 9.1 增量 + 异步：把「走一步存一次」从卡顿里救出来（2026-10-22）

原来每次 `_persist` 都做三件重活：**整份 JSON 序列化 + `fsync` + 一次「读回文件只为拿
`_save_tag`」的完整解析**。触发点却有二十几个（每 10 步移动、每次战斗结束……），
结果是游戏循环被磁盘 IO 掐住。现在拆成三级：

```
_persist()
  ├ ① 脏键比对：data 与上次落盘的 _save_snapshot 逐键比，只留变化的键
  │     └ 没变化 且 磁盘指纹未变 → _save_noop += 1，**不排任何写任务**
  ├ ② 同步路径（_save_async=False 或无写线程）→ _save_now(data, dirty)
  └ ③ 异步路径 → 只把 {键: 值} 放进队列，_save_writer 线程去写
```

| 机制 | 解决的问题 | 关键字段 |
|---|---|---|
| 脏键比对 | 「走一步存一次」里大量落盘点其实什么都没变 | `_save_snapshot` / `_save_dirty` |
| 磁盘指纹 | 内存没变但**别人刚写过**（多会话）→ 必须进入写路径发告警 | `_save_file_stamp`（`mtime_ns, size`） |
| 合批 | 队列里连续排队的多次写只落最后一次 | `_save_pending` |
| 异步落盘 | 不阻塞游戏循环 | `_save_queue` / `_save_thread` |
| 停机收口 | 进程退出时把队列写完 | `flush_saves()` |
| 自证 | 「到底省了多少」可量化 | `save_stats()` |

**三条边界条件，都是踩出来的：**

1. **没有写线程时保持同步语义**。`M4Server` 也被协议测试、数据迁移工具、离线脚本
   直接实例化——这些入口不进 `run()`，没有后台写线程。向尚未创建的队列 `put` 会挂，
   临时起线程又会让调用方在不知情的情况下读到旧档。
2. **`_save_tag` 的检测搬到写线程**。它原本要读一次文件（完整 JSON 解析）才能比对
   「上次是谁写的」；现在同步路径上只剩内存快照比对。
3. **跨写者必须告警，但不能阻止写入**。文件锁保证「不会写坏」，保证不了
   「另一个会话的进度不被覆盖」——两个会话各持一份内存状态，后落档的会盖掉先落档的。
   判据是 `_save_tag`（会话级，随档落盘）；发现不是最后一个写者时打显式告警，
   **不阻止**（单人游戏不该因为并发就丢档）。

> 回归 `tests/_test_save_async.py` 七段：①同步 ②异步 flush ③noop ④合批 ⑤停机收口
> ⑥跨写者告警 ⑦`save_stats`。第 ③ 段是关键——它验的是「没变化时**真的**没写」，
> 而不是「看起来没写」。

---
