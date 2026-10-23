# 网上资料补全包（2026-10-22）

把「端游公开资料」整理成**可校验、可回归**的数据，用来补模拟服里靠手写常量撑着的部分。

原则只有一条：**能落到客户端数据或官方资料上校验的才收**。每条数据带 `source` 字段；
查不到的写「不可得」，不编、不用相似名字硬凑（疑似错字只给 `candidates`，不自动采用）。

---

## 一、目录内容

| 文件 | 内容 | 条数 | 可信度 |
|---|---|---:|---|
| `pets_carry.json` | 召唤兽携带等级 + 分布场景（含稀有） | 77 | **真值**（官方资料表；线上同表逐条一致） |
| `quests_mainline.json` | 主线剧情任务表（编号/名称/触发 NPC/场景/等级门槛/剧情点） | 24 | **真值**（表结构+门槛）；流程细节另见攻略 |
| `scene_encounters.json` | 场景 → 怪物 → 客户端 shape | 43 场景 / 124 条 | **真值**（资料全库场景表 × monsterdata） |
| `cultivation_exp.json` | 八种修炼的经验表 + 公式（0→30，飞升 35） | 30×2 | **真值**（两处独立来源同式，表由公式重算） |
| `alchemy_recipes.json` | 炼药配方（材料/数量/产物） | 13（7 条完整） | **玩家实测**：产物与比例可信度中；4 个材料名查无此物 → `UNRESOLVED` |
| `boss_hp.json` | 江湖志 1–8 关 / 天界之路 boss 血量 | 13 | **半真值**：攻略实测「左右」口径 |
| `build_data.py` | 上述 JSON 的生成脚本（可重跑） | — | — |

> `scene_encounters.json` 与 `服务端源码/sim/encounter_data.py` 同源：前者是给外部看的快照，
> 后者是运行时真正读的表（由 `gen_encounter_data.py` 生成）。

---

## 二、来源与交叉核对

| # | 来源 | 地址 | 用来做什么 | 核对结果 |
|---|---|---|---|---|
| 1 | 《梦想世界2013资料全库》 | 本地 `客户端数据/_docx_text.txt`（823 KB，官方资料文本） | 场景表、召唤兽携带表、剧情任务表、修炼效果、招式表 | 与客户端 `monsterdata.py` / `itemdata.py` 对得上 |
| 2 | 叶子猪·梦想世界端游资料站 | https://mx.yzz.cn/call/2010,100673.shtml | 「召唤兽分布」：67 条携带等级 + 43 个场景的怪物/掉落 | 与来源 1 逐条一致；线上页少 10 条（缺 85 级以上部分稀有），以资料全库 77 条为准 |
| 3 | 52pk·剧情任务一览表 | https://mxsj.52pk.com/jingyan/6146254.shtml | 23 条剧情（比资料全库多「奇迹的方舟」「黎明的曙光」） | 前 22 条与来源 1 名称/门槛一致（编号错位 1 位：资料来源把「村长的委托」算 #1） |
| 4 | 52pk·修炼经验对照表 + 百度知道 | https://mxsj.52pk.com/xinqing/519630.shtml ・ https://wen.baidu.com/question/408281816.html | 八种修炼的每级经验与公式 | **两处独立来源公式完全同式**；重算 0→1 / 29→30 与原文逐位一致（411/274、43740/29156） |
| 5 | 52pk·炼药配方（两篇） | https://mxsj.52pk.com/xinqing/5424202.shtml ・ /5424203.shtml | 13 条炼药配方 | 材料名 4 个在 `itemdata` 查无（牛藤/知母/半血莲/杀刺豆），已标 `UNRESOLVED` + 疑似候选 |
| 6 | 逗游·江湖志 1–8 关血量 | https://doyo.cn/article/6865 | 8 条 boss HP | 单体口径，作者自述「与实际差异基本不大」 |
| 7 | 52pk·天界之路 | https://mxsj.52pk.com/jingyan/5500606_2.shtml ・ /_4.shtml | 三杀兽 / 小怪 HP | 原文「左右」，按半真值收录 |
| 8 | 台服官网 `mx.lager.com.tw` | http://mx.lager.com.tw/html/mx_game_system18.shtml | 打造/宝石系统规则（旁证） | 与资料全库一致，仅作规则旁证，未入 JSON |
| 9 | 叶子猪·魔界之门/天界之路流程 | https://mx.yzz.cn/plot/2014,902106.shtml ・ https://mx.yzz.cn/plot/2012,567638_all.shtml | 剧情坐标/战斗配置/怪物技能 | 与 52pk 版本互證（坐标一致） |

---

## 三、已经落地的代码改动（不只是资料）

**场景遇怪**（2026-10-22）：
| 文件 | 改动 |
|---|---|
| `服务端源码/sim/encounter_data.py` | 新增（生成物）：43 场景 → 124 条怪物 → 客户端 shape |
| `服务端源码/sim/gen_encounter_data.py` | 新增：从 `drop_data` + `monsterdata` + `world_config` 生成上表，未解析项直接报错 |
| `服务端源码/sim/m4_server.py` | `ENCOUNTER_PER_MAP`、`_encounter_def()`、`_encounter_allowed_here()`；`_start_war` 按本图名单选怪（名字/造型/HP）；移动遇怪加「本图是否允许」闸门；GM 战斗快照带 `scene` 与实际怪名 |
| `服务端源码/sim/world_config.py` / `.json` | 新增 `encounter.per_map`（默认 `true`，老配置缺键不报错） |
| `服务端源码/tests/_test_encounter_map.py` | 新增：数据自洽 + 开怪名字/shape + 城镇不遇怪/野外遇怪/per_map=false 回落 |
| `服务端源码/sim/tests/_run_all.py` | 挂进一键回归 |

**行为变化**：以前任何地图走到第 5 步都刷「野狼」；现在城镇/室内（17 张图）**不遇怪**，
野外/迷宫按本图真值名单出怪（如天水平原：青蛙怪/小兔子/小蜜蜂）。
`encounter.per_map=false` 可一键回到旧行为（录像复刻用）。

**主线剧情 1–3**（同日）：

| 文件 | 改动 |
|---|---|
| `服务端源码/sim/quest_data.py` | 新增：3 条剧情 / 27 步 / 21 个 NPC 摆位 / 物品与奖励 |
| `服务端源码/sim/gen_quest_config.py` | 新增：NPC 摆位 + 5 件剧情必需品入商店（幂等） |
| `服务端源码/sim/m4_server.py` | 剧情状态机（接/推进/交物品/战斗/复命）、`S2CTaskAdd` 逐步刷新、剧情点 `m_StoryPoint(42)`、存档 |
| `服务端源码/tests/_test_quest_chain.py` | 新增：数据自洽 / 等级门槛 / 全链路 / any_shapes / 存档（ALL PASS） |

细节见 `协议资料/报告/主线剧情1-3实现_20261022.md`。

**保持不变**：伤害 50/48/20、HP 400/350、敌方 HP 100 这些录像标定值一个没动——
换的只是「出哪种怪」，不是「数值」。

---

## 四、怎么用

```bash
# 重新生成遇怪表（改了 drop_data 或 monsterdata 后）
python 服务端源码/sim/gen_encounter_data.py

# 重建本目录 JSON
python 协议资料/网上资料_20261022/build_data.py

# 跑遇怪回归
python 服务端源码/tests/_test_encounter_map.py
```

关掉场景遇怪（回到全图野狼）：

```json
"encounter": {"every": 5, "per_map": false}
```

---

## 五、明确拿不到的东西

| 项 | 状态 | 原因 |
|---|---|---|
| 人物升级经验曲线（1→140/150） | **不可得** | 官方资料库无表；端游已停服；录像只有战斗帧无 op17。线上找到的「Level Chart」是另一款同名英文 MMO，已排除 |
| NPC 商店售价 | **不可得** | 客户端 `itemdata` 无价格；`milldata.maxprice` 是工厂自定价上限，不是 NPC 售价 |
| 每场怪物数量 / 出现权重 | **不可得** | 场景表只给「有哪些怪」，不给「几只、多大概率」 |
| 常规怪物 HP / 速度 / 技能 | **不可得**（仅 boss 有攻略口径） | 客户端不存；录像只能反推「几击致死」，分不清死亡/逃跑 |
| 材料 → 产物 的完整工艺配方 | **不可得** | 客户端工艺面板不列材料；线上只有炼药（另一套系统）的玩家实测配方 |
| 修炼系统的客户端入口 | 存疑 | 本版客户端 `src/dialog` 与 `net.py` 里搜不到「修炼」；资料描述的是更高版本，暂不入代码 |

---

## 六、下一步（按“能验证”排序）

1. **炼药配方接线**：`alchemy_recipes.json` 7 条完整配方可直接做成 `C2S_NPC_MAKE` 分支；先解决 4 个错字材料名（需人工确认，不能猜）。
2. **主线剧情**：`quests_mainline.json` 24 条已有 NPC/坐标/门槛。**第 1–3 条已于 2026-10-22 打通**
   （`quest_data.py` + `_test_quest_chain.py`，见 `协议资料/报告/主线剧情1-3实现_20261022.md`）；
   第 4 条起可直接照 `quest_data.py` 的步骤格式续写。
3. **修炼系统**：`cultivation_exp.json` 公式是真值，但本版客户端无修炼入口 —— 先确认客户端是否存在隐藏面板，再决定是否实现。
4. **boss HP**：`boss_hp.json` 可给「江湖志/天界之路」这类固定 boss 用，不建议套到普通野怪。
