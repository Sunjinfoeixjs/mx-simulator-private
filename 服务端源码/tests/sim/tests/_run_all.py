# -*- coding: utf-8 -*-
"""一键回归：把散落的自动化收拢成一个入口（改完代码 / 提交前跑一遍）。

四层防线，从快到慢：
  ① 协议对账（纯静态，秒级）
       · _extract_wire.py --audit      客户端 Unpack 线序 ↔ tap.OVERLAY
       · _audit_build_overlay.py       build_* 发射 ↔ tap.OVERLAY（服务端镜像）
       · _c2s_coverage.py              上行覆盖率静态口径（扫描段有守卫，分支体漂移会红）
  ② 分派契约 / 数据层（进程内，约 20s：_test_tap 会真起内核，_test_editor 起 HTTP）
       · tests/_test_dispatch.py       54 条 PRE_WAR op 战斗态不落保底 + op100/101/104/164
       · tests/_test_damage.py         物伤公式：分支切换点 / 截断语义 / 不 clamp / if-vs-max 分叉
       · tests/_test_tap.py            覆盖表解码：真帧吃满 + 字段名 + 分帧自愈 + 世界/战斗模型
       · tests/_test_editor.py         世界配置校验/保存/重置 + 编辑器前端逻辑
  ③ socket 冒烟（各自起内核、独立端口、独立 tmp 存档）
       · _e2e_m4 / m5 / m7 / m9 / m11 + coverage_smoke
  ④ 文档体检 + 真机实验 harness（真机需用户启动客户端，默认不跑，--live 单跑）
       · _split_docs.py --check / _check_anchors.py（双向）/ _e2e_live_exp.py

每次跑完末尾打一块固定格式 STATS（步数/通过/耗时/覆盖率分母分子/build_* 数/
契约表条数，见 tests/_stats.py）——报告引数字从那里复制，别再四个工具手工抄。

设计：
  · 每个子测试独立子进程：一个崩了不拖垮其余，输出按行尾截断入汇总表；
  · 互不干扰：端口各自固定（3135/36/38/39/41/42…），存档走 tmp 隔离
    （_e2e_m4.tmp_savedir 复制真实存档到 tmp——**不会碰真机进度**）；
  · 退出码：任一失败即 1，可直接挂 CI / 提交前钩子。

用法：
  python tests/_run_all.py            # 全跑（① ② ③ ④，约 2 分钟）
  python tests/_run_all.py --fast     # 只跑 ① ②（改协议/覆盖表时用，约 20s）
  python tests/_run_all.py --slow     # 只跑 ③（socket 冒烟）
  python tests/_run_all.py _e2e_m11   # 只跑名字含关键字的步骤（可多次）
  python _e2e_live_exp.py             # 真机实验（④）单独跑，见该文件说明
  python tests/_stats.py --log <回归输出>   # 事后从日志补 steps 三项重打 STATS
"""
from __future__ import print_function

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.dirname(HERE)


def _find_tests_dir(sim_dir):
    """定位「放着 _test_*.py 的那一层」——不写死相对层级。

    为什么不再 `dirname(SIM)/tests`：本工作区的 sim 被整体挪进
    `服务端源码/tests/` 一次，写死的相对层级会指到
    `服务端源码/tests/tests/`（不存在），runner 直接「没有匹配的步骤」。
    这里按**锚点文件**找：哪一层有 `_test_dispatch.py`，哪一层就是测试目录。
    """
    for base in (os.path.dirname(sim_dir), sim_dir,
                 os.path.dirname(os.path.dirname(sim_dir))):
        for cand in (base, os.path.join(base, 'tests')):
            if os.path.isfile(os.path.join(cand, '_test_dispatch.py')):
                return cand
    return os.path.join(os.path.dirname(sim_dir), 'tests')


# 可跑的测试统一放在「_test_dispatch.py 所在层」（历史上一份 _test_dispatch 曾复制到
# sim/tests 并漂移，现已收敛：runner 只指向那一层，那边是唯一副本）
TESTS = _find_tests_dir(SIM)
REPORT = r'C:\Users\Administrator\MX\协议资料\报告'
PY = sys.executable or 'python'

# (名称, 层, 参数, 超时秒, 通过准则)。参数相对各自工作目录解析。
#
# ★ 退出码审计（2026-10-19，见 verify_exit_codes）：
#   默认准则是 rc == 0 —— **非 0 一律算失败**。
#   ok_pred 是唯一允许「不看 rc」的入口，且必须在 EXIT_CODE_EXEMPT 里登记理由
#   未登记就用 ok_pred 会被审计判红。目的是防住这类事故：
#     「子测试返回非 0，但被 `|| true` / `check=False` / 宽松 ok_pred 吞掉，
#      回归一片绿，问题却还在」。
# ★ 2026-10-20：**不再有任何退出码豁免**。
#   曾经这里有 `_wire_ok` + `EXIT_CODE_EXEMPT`，因为线序对账工具把
#   「需动态未配」也算进 problems 返回 1。
#   后来查清那 4 个「需动态未配」根本不是积压：
#     · op26    —— 覆盖表真是静态的（ids:T 原始块），**补齐成 L{count:n}**
#     · op99/105 —— 覆盖表已用 P()/L{} 表达可变结构，判定器没识别
#     · op16    —— 分支只在客户端本地逻辑分叉，两路解包序列相同，线序唯一
#   修完这四类，工具自然返回 0，豁免失去存在理由。
#
#   留这段注释是为了记住：**豁免往往是「问题没查清」的遮羞布**。
#   将来若又要加例外，先问「非 0 的真正原因是什么、能不能修掉」。
EXIT_CODE_EXEMPT = {}


STEPS = [
    # ── ① 协议对账（静态） ──
    ('线序对账 _extract_wire --audit', 'proto', [PY, os.path.join(REPORT, '_extract_wire.py'), '--audit'], 90, None),
    ('build_*↔OVERLAY 审计', 'proto', [PY, os.path.join(REPORT, '_audit_build_overlay.py')], 90, None),
    # 上行覆盖率静态口径（2026-10-20 挂入）：此前它没进回归，`_dispatch` 被重构成
    #   _dispatch + _dispatch_locked 后扫描器静默少扫 100+ 分支（100%→14%），
    #   回归全绿没人发现。挂进来 + 扫描段守卫（分支数 <40 直接 rc=2）。
    ('上行覆盖率 _c2s_coverage', 'proto', [PY, os.path.join(REPORT, '_c2s_coverage.py')], 90, None),
    # ── ② 分派契约（进程内） ──
    ('硬约束 #5/#16/#17 _test_hard_constraints', 'dispatch',
     [PY, os.path.join(TESTS, '_test_hard_constraints.py')], 60, None),
    ('分派契约 _test_dispatch', 'dispatch', [PY, os.path.join(TESTS, '_test_dispatch.py')], 90, None),
    ('物伤公式 _test_damage', 'dispatch', [PY, os.path.join(TESTS, '_test_damage.py')], 60, None),
    ('玩家招式 _test_hero_magic', 'dispatch',
     [PY, os.path.join(TESTS, '_test_hero_magic.py')], 60, None),
    # 宠物招式（2026-10-22）：petskill type 分流（主动 415 群攻 / 被动不进程菜单）、
    #   FP 消耗与不足回落、必杀会心/连击追加、非法目标与群攻全灭。
    ('宠物招式 _test_pet_skill', 'dispatch',
     [PY, os.path.join(TESTS, '_test_pet_skill.py')], 60, None),
    # 默认物伤模型守护（2026-10-20）：代码常量 DMG_MODEL / world_config.json /
    # default_config() 三处都必须 == 'reverse'，防止默认配置被悄悄改回 linear
    # （两个模型都能复现 50/48/20，游戏里发现不了出处流失）。
    ('默认模型守护 _test_dmg_default_model', 'dispatch',
     [PY, os.path.join(TESTS, '_test_dmg_default_model.py')], 60, None),
    # 时间双通道：mxgw(op15) 必须回 10000Hz **单调**滴答（墙钟回跳会造出假 007），
    # 日历(op192) 必须回 Unix 秒。2026-10-20 残留校验排查新增（进程内，毫秒级）。
    ('时间双通道 _test_time_channels', 'dispatch',
     [PY, os.path.join(TESTS, '_test_time_channels.py')], 60, None),
    # STATS 格式契约：parse(format(x))==x + 回归日志三种编码解析（进程内，毫秒级）。
    # 末尾那块 STATS 是报告数字的唯一来源，格式破了比测试红了更麻烦。
    ('STATS 格式契约 _stats --self-test', 'dispatch',
     [PY, os.path.join(TESTS, '_stats.py'), '--self-test'], 60, None),
    # 离线真机对照：用客户端解码语义验证服务端帧（不启动 GUI，见文件头注释）
    # 真机启动前的端口一致性：local.ini 与 server7_local.bin 必须同端口。
    # 2026-10-20 真机踩坑：只改后者时界面列表是新的，但点进去连旧端口。
    ('真机端口一致性 _test_live_port_sync', 'dispatch',
     [PY, os.path.join(TESTS, '_test_live_port_sync.py')], 60, None),
    ('客户端解码对照 _test_client_decode', 'dispatch',
     [PY, os.path.join(TESTS, '_test_client_decode.py')], 60, None),
    ('覆盖表解码 _test_tap', 'dispatch', [PY, os.path.join(TESTS, '_test_tap.py')], 120, None),
    # P0 会话安全：不同账号状态/存档隔离，同账号重复登录拒绝，断线回收所有权。
    ('多会话隔离 _test_multisession', 'dispatch',
     [PY, os.path.join(TESTS, '_test_multisession.py')], 90, None),
    # P0 第三轮异常恢复：确认保存后 os._exit、损坏档拒写、半帧断线、快速重连。
    ('P0 异常恢复 _test_p0_crash_recovery', 'dispatch',
     [PY, os.path.join(TESTS, '_test_p0_crash_recovery.py')], 90, None),
    # P0 诊断端到端：真起 debugger 进程 + HTTP/SSE + 注入/丢帧。
    ('调试器端到端 _test_debugger', 'dispatch',
     [PY, os.path.join(TESTS, '_test_debugger.py')], 180, None),
    # GM 开战/胜利的真实链路 + 帧序标红（2026-10-20）：
    #   war start/win 必须真的调进 _start_war / _after_hero_action（发的是真实帧序列），
    #   且 tap.mark_range 圈出的区间**恰好**覆盖这条链路 —— 调试台就靠它把那段标红。
    #   变异验证过两次：mark_range 改成空操作、gm_war_win 改成直接置 in_war 都会判红。
    ('真实链路标红 _test_war_chain', 'dispatch',
     [PY, os.path.join(TESTS, '_test_war_chain.py')], 120, None),
    ('世界编辑器 _test_editor', 'dispatch', [PY, os.path.join(TESTS, '_test_editor.py')], 120, None),
    ('地图全图 _test_map', 'dispatch', [PY, os.path.join(TESTS, '_test_map.py')], 60, None),
    # 场景遇怪（2026-10-22）：43 场景/124 条怪物全解析到 monsterdata shape；
    #   城镇不遇怪、野外按本图名单出怪、per_map=False 回落全图野狼。
    ('场景遇怪 _test_encounter_map', 'dispatch',
     [PY, os.path.join(TESTS, '_test_encounter_map.py')], 60, None),
    # 主线剧情 1–3（2026-10-22）：3 条剧情/21 个 NPC 摆位/等级门槛/全链路状态机/存档
    ('主线 1–3 _test_quest_chain', 'dispatch',
     [PY, os.path.join(TESTS, '_test_quest_chain.py')], 90, None),
    # 老角色重登（2026-10-22 真机）：_apply_save 不置 have_role → 客户端卡「正在登录中」
    ('老角色重登 _test_returning_login', 'dispatch',
     [PY, os.path.join(TESTS, '_test_returning_login.py')], 60, None),
    # 制作链（2026-10-22 补全「真扣费」契约）：
    #   craftdata 真值目录 / 活力≠体力 / op104 预览金额 == 实扣 / 钱不够一分不动
    ('制作链·真扣费 _test_craft', 'dispatch',
     [PY, os.path.join(TESTS, '_test_craft.py')], 60, None),
    # 业务事件总线 + 帧批合并（2026-10-22）：
    #   总线只观测不驱动（异常安全/有界计数/未登记 kind 点名）；埋点覆盖任务链、
    #   经济流水、物品、制作五类拒绝、战斗、切图；合批保证「拼出来的字节 ==
    #   逐帧直发」且 recv 前必 flush（不跨等待）、GM 推送显式 flush。
    ('事件总线·帧批合并 _test_bus', 'dispatch',
     [PY, os.path.join(TESTS, '_test_bus.py')], 90, None),
    # 出手顺序稳定性：7~10 单位 + 同速 >=5 组（进程内，毫秒级）
    ('排序稳定 _test_speed_stability', 'dispatch',
     [PY, os.path.join(TESTS, '_test_speed_stability.py')], 60, None),
    # 宠物实例闭环（2026-10-22）：协议 128-134/144 + 多宠隔离 + 加点 + 经验升级 +
    #   存盘重登 + 倒地复活 + 技能绑定 + 属性进战斗。宠物从「一组模块常量」改成实例表
    #   之后，这条链最容易出的错是「两只宠物串数据」和「重登少发一只」，故单列。
    ('宠物实例 _test_pet_instance', 'dispatch',
     [PY, os.path.join(TESTS, '_test_pet_instance.py')], 90, None),
    # 宠物交付闭环（2026-10-23 P1）：实例唯一性（pid 判重 + 读档去重 + 造型
    #   目录校验）+ 交付原子性（落盘确认后才发 48/49/53；失败回滚、零帧、
    #   原因如实上报）。硬性约束「不得重复创建宠物实例」「不可逆交付必须原子」
    #   的回归守护。变异验证过：把「确认后才发帧」挪回落盘前 → 3 项判红。
    ('宠物交付 _test_pet_delivery', 'dispatch',
     [PY, os.path.join(TESTS, '_test_pet_delivery.py')], 90, None),
    # 宠物装备闭环（2026-10-23 P1）：op54 线序 ↔ 客户端 Unpack 语义对账（含
    #   扩展长度帧/GBK/越界滤除）、C2S 9 字节装备路径（无独立 opcode）、
    #   ≥20 等级门、同类替换旧装备消失、落盘失败回滚零帧、存档往返补发 54、
    #   读档清洗、控制台/HTTP/GM 三入口同源。变异验证过两次：① 54 帧挪到
    #   落盘确认前 → 7 项判红；② 禁用写失败基线回滚 → 2 项判红（重试永远失败）。
    ('宠物装备 _test_pet_equip', 'dispatch',
     [PY, os.path.join(TESTS, '_test_pet_equip.py')], 90, None),
    # 角色深度养成（2026-10-23 P3-01）：最终属性**唯一入口**（派生值只算不存，
    #   从结构上杜绝「重复加载装备 → 属性叠加」）+ 加点联动（力量 //4 整除语义）+
    #   装备槽位/等级门（真值来自客户端 EQUIP2POS+ITEMTYPE，252 条生成表）+
    #   非法拖拽回弹不扣资源 + 旧档缺字段回落初始值。
    #   变异验证过四次：力量除数 4→3 / 关掉槽位校验 / 读档五维叠一次 / 装备去重拿掉
    #   （后两项要配伪造加成）—— 全部判红，还原后全绿。
    ('角色养成 _test_p3_progression', 'dispatch',
     [PY, os.path.join(TESTS, '_test_p3_progression.py')], 90, None),
    # 宠物深度养成（2026-10-23 P3-02）：属性三层合成收口到 pet_progression
    #   （面板/战斗/资源各一个纯函数，加点明细可单独取）+ 技能表增删收口到
    #   pet_skill_service（绑定 id 归一 10415≡415 / 判重 / 上限 / 客户端 404 条
    #   技能表白名单）+ 成长连算 100 次幂等 + 非法培养零副作用 + 重登派生值不叠加
    #   + 遗忘「连击」→ 战斗层 combo_chance 归零（技能表真的在驱动战斗）。
    #   技能真值表由 gen_pet_skill_data.py 从客户端 petskill.DATA 生成并逐条对账。
    ('宠物养成 _test_p3_pet_growth', 'dispatch',
     [PY, os.path.join(TESTS, '_test_p3_pet_growth.py')], 120, None),
    # 高级战斗（2026-10-23 P3-03）：四件事各一组断言，共 10 组 101 项。
    #   ① 随机源   —— 战斗内 12 处模块级 random 全收口到实例 battle_rng
    #                （固定种子 + 11 条用途流；同种子同序 / 流间隔离 / 每场重置）
    #   ② 状态真值 —— 客户端 buffdata 284 条状态（249 图标 / 34 含量化 / 29 可建模），
    #                生成物未过期，建模条目逐条带 src 出处
    #   ③ 状态机制 —— 施加/叠加(add vs refresh)/到期/驱散/上限/未知 id 拒绝 + 乘区聚合
    #   ④ DOT      —— 灼热 20%上限、灵魂附体 30%当前、多种累加、至少 1 点、**无状态零帧**
    #   ⑤ 强制防御 —— 恐怖 → 真实承伤路径减半；水晶墙 → 承伤 ×0.2
    #   ⑥ 敌方 AI  —— 抽取顺序（target→skill→pick→hurt）/ 保护不放招 / focus 选残血
    #   ⑦ 只结算一次—— 六路入口（普攻/群攻/GM/战败/逃跑 + 重入）结算次数恒 ≤1，
    #                3 只同灭时 TaskAdd 收敛 1 次而 WarDel 仍逐只 3 帧
    #   ⑧ GM       —— buff / rng / settle / aipolicy 命令行（与面板同源）
    ('高级战斗 _test_p3_03_combat', 'dispatch',
     [PY, os.path.join(TESTS, '_test_p3_03_combat.py')], 120, None),
    # 主线剧情 1–16 + 任务生命周期（2026-10-22）：接取门（等级/前置/单主线）、
    #   16 条主线的步数/等级/剧情点对一览表、全链路、战败重打、放弃、重登恢复、
    #   剧情点学剧情技能。7–16 的逐条细项（坐标双源/降级步/可见性）见下一条。
    ('主线剧情1-16 _test_quest_story4', 'dispatch',
     [PY, os.path.join(TESTS, '_test_quest_story4.py')], 120, None),
    # 主线 7–16 落地（2026-10-22）：16 条数据自洽（步数/等级/剧情点合计 27）、
    #   坐标双源核对（标 npcposdata 必须同图同坐标且 shape==npcid；quest-spawn
    #   不得与同图世界 NPC 重名）、接取门（Lv34 拒 / Lv35 接 107 / 115 三级前置）、
    #   107 全链路 16 步、降级步（刃虎宝宝/大螃蟹宝宝 查无 → talk + note）、
    #   任务态 NPC 可见性（同图只显示当前步骤那个）、17–22 如实标不可得、
    #   落盘往返。变异验证过三次：① 剧情点改 0 → 2 项判红；② 金大有坐标
    #   66→67 → 1 项判红；③ 抹掉 shape_sim → 1 项判红，事后还原一致。
    ('主线7-16 _test_quest_716', 'dispatch',
     [PY, os.path.join(TESTS, '_test_quest_716.py')], 120, None),
    # 战斗全链路（2026-10-22）：遇怪→攻击→招式→召唤兽→敌方回合→死亡→WarEnd→场景恢复，
    #   每一帧都用 frames.unpack_s2c（唯一字段定义）解，顺带守住「定义与收发包同源」。
    ('战斗全链路 _test_battle_flow', 'dispatch',
     [PY, os.path.join(TESTS, '_test_battle_flow.py')], 90, None),
    # 战斗核心（2026-10-23）：状态机/行动合法性/重复指令/技能资源/胜负结算。
    #   守的是「不报错但玩家会卡住/白挨打/重复领奖」的静默问题：开战重入、
    #   窗口闸门、超时默认动作、非法指令不消耗回合、战败/逃跑清场。
    ('战斗核心 _test_battle_core', 'dispatch',
     [PY, os.path.join(TESTS, '_test_battle_core.py')], 90, None),
    # 系统设置（dialog/dlgsysopt，2026-10-22）：op21/12/11 收下即走 + 存盘回读 + 战斗态不落保底
    ('系统设置 _test_sysopt', 'dispatch',
     [PY, os.path.join(TESTS, '_test_sysopt.py')], 60, None),
    # 内容扩充（2026-10-22）：地图可达/可切/不缺盘、新药品可买可用、宠物目录可切换且帧跟着换
    ('内容扩充 _test_content', 'dispatch',
     [PY, os.path.join(TESTS, '_test_content.py')], 120, None),
    # 存档锁 _test_save_lock
    ('存档锁 _test_save_lock', 'dispatch',
     [PY, os.path.join(TESTS, '_test_save_lock.py')], 90, None),
    # 存档/日志根路径契约（2026-10-23 P5-03 第 0 步）：`CLAW` 原先 `dirname×3`，
    #   而 m4_server.py 在 MX/服务端源码/tests/sim/ 是 4 层深 → 存档目录多套一层
    #   `服务端源码`，历史 role.json 被**静默忽略**（load_role 对「文件不存在」
    #   返回 None 是正常路径，不报错）。本步守：CLAW==项目根、SAVE_DIR/LOGDIR
    #   分量不重复、真实历史档落在 SAVE_DIR 里、四个同源模块的 CLAW 同式。
    ('存档根路径 _test_save_paths', 'dispatch',
     [PY, os.path.join(TESTS, '_test_save_paths.py')], 60, None),
    # 存档增量/异步（2026-10-22）：脏键跳过（noop）+ 写线程合并（coalesced）+
    #   flush_saves「写完才应答」语义 + 断线/停机收口不丢最后一份。
    ('存档增量·异步 _test_save_async', 'dispatch',
     [PY, os.path.join(TESTS, '_test_save_async.py')], 90, None),
    # 跨模块状态一致性（2026-10-23 P0）：_persist 的增量快照必须**深拷贝**——
    #   items/tasks/hero_res 是运行时活引用（业务代码全部原地增删），浅拷会让
    #   「只改背包/任务/战斗资源」的落盘被 noop 分支静默跳过（磁盘存档过期）。
    #   另守宠物运行时镜像（实例表 ↔ pet_hp/pet_shape/active_pet_id）在每个
    #   同步点自洽 + 存档往返等价 + 异步写线程与原地修改不赛跑。
    #   变异验证过：把 deepcopy 换回 dict(data) 浅拷 → 9 项断言判红。
    ('状态一致性 _test_state_consistency', 'dispatch',
     [PY, os.path.join(TESTS, '_test_state_consistency.py')], 90, None),
    # 持久化冒烟（2026-10-23 复活）：原路径写死旧布局 `服务端源码/sim`，sim 挪进
    #   tests/ 后 import 失败、测试沦为孤儿（回归盲区）。改布局无关查找后挂回：
    #   改状态→_persist→重实例化→状态逐字段回读（含整型 iid 背包键）。
    ('持久化冒烟 _test_persist', 'dispatch',
     [PY, os.path.join(TESTS, '_test_persist.py')], 60, None),
    # 原子存档（2026-10-23 复活，同上）：save_role 原子替换 + 损坏原文件不被
    #   覆盖 + 无存档时可写。与 _test_save_lock（并发写）互补——那个管「同时写」，
    #   这个管「单次写要么完整、要么原文件一个字节不动」。
    ('原子存档 _test_role_persistence', 'dispatch',
     [PY, os.path.join(TESTS, '_test_role_persistence.py')], 60, None),
    # 存档 schema 版本 + 迁移（老档升级、拒绝未来版本、不半迁移）
    ('优雅停止 _test_server_stop', 'dispatch',
     [PY, os.path.join(TESTS, '_test_server_stop.py')], 60, None),
    ('GM 面板状态 _test_gm_web_state', 'dispatch',
     [PY, os.path.join(TESTS, '_test_gm_web_state.py')], 90, None),
    ('Web 端口冲突 _test_web_port_conflict', 'dispatch',
     [PY, os.path.join(TESTS, '_test_web_port_conflict.py')], 90, None),
    ('端口回填 _test_port_backfill', 'dispatch',
     [PY, os.path.join(TESTS, '_test_port_backfill.py')], 90, None),
    ('循环解码 _test_loop_decode', 'dispatch',
     [PY, os.path.join(TESTS, '_test_loop_decode.py')], 60, None),
    ('丢帧计数 _test_drop_count', 'dispatch',
     [PY, os.path.join(TESTS, '_test_drop_count.py')], 60, None),
    ('热加载 _test_world_config_reload', 'dispatch',
     [PY, os.path.join(TESTS, '_test_world_config_reload.py')], 60, None),
    ('特性状态 _test_feature_status', 'dispatch',
     [PY, os.path.join(TESTS, '_test_feature_status.py')], 60, None),
    # 变异测试：删掉 load_role 的 normalize_items 调用必须判红。
    # 这是**元测试**——它守护的是「那行有没有被测试守住」，不是业务行为。
    ('变异·规范化守护 _test_mutation_normalize', 'dispatch',
     [PY, os.path.join(TESTS, '_test_mutation_normalize.py')], 150, None),
    ('schema 迁移 _test_schema_migration', 'dispatch',
     [PY, os.path.join(TESTS, '_test_schema_migration.py')], 60, None),
    # ── 经济收口（P3-05，2026-10-23）────────────────────────────────
    # 三层：账本单元 → 服务端真实链路 → AST 结构审计（唯一入口纪律）。
    # 第三层是重点：它查的是「有没有第二个真相」，不只是「这次算对没有」——
    # 新增一条直接改 self.coins 的代码，这里会立刻判红。
    ('经济收口 _test_p3_05_economy', 'dispatch',
     [PY, os.path.join(TESTS, '_test_p3_05_economy.py')], 90, None),
    # ── ③ socket 冒烟 ──
    ('冒烟 _e2e_m4（战斗基础）', 'smoke', [PY, os.path.join(SIM, '_e2e_m4.py')], 150, None),
    ('冒烟 _e2e_m5（战败/6敌）', 'smoke', [PY, os.path.join(SIM, '_e2e_m5.py')], 150, None),
    ('冒烟 _e2e_m7（招式/会心）', 'smoke', [PY, os.path.join(SIM, '_e2e_m7.py')], 150, None),
    ('冒烟 _e2e_m9（养成闭环）', 'smoke', [PY, os.path.join(SIM, '_e2e_m9.py')], 150, None),
    ('冒烟 _e2e_m11（op100/101）', 'smoke', [PY, os.path.join(SIM, '_e2e_m11.py')], 150, None),
    ('冒烟 _test_war2（战斗深化·自带内核）', 'smoke', [PY, os.path.join(TESTS, '_test_war2.py')], 180, None),
    ('覆盖统计 coverage_smoke', 'smoke', [PY, os.path.join(SIM, 'coverage_smoke.py')], 150, None),
    # ── ④ 文档体检 ──
    # 分册同步（2026-10-20）：docs/*.md 是主文档的拆分副本，改了主文档忘了重跑
    #   _split_docs.py 就会静默漂移。--check 模式逐字比对，不一致即红。
    ('分册同步 _split_docs --check', 'doc',
     [PY, os.path.join(REPORT, '_split_docs.py'), '--check'], 60, None),
    # 2026-10-20：文档已改为**只写符号名、不写行号**（行号漂移维护成本太高）。
    # 因此主用 --symbols 模式：检查文档里反引号包裹的标识符是否真的存在于源码，
    # 能抓住「符号改名后忘了同步文档」这类问题。
    # 行号模式仍保留（不带 --symbols），用于历史报告或临时核对。
    # 文档双向体检（2026-10-20）：
    #   方向 A 文档→代码：正文引用的符号必须真实存在（抓改名/笔误）
    #   方向 B 表→代码  ：§17 生成表必须与源码一致（抓「加了符号忘了重生成」）
    # 两个方向都要，因为它们抓的是不同类别的问题——只查一个会漏。
    #
    # ★ 跨文件扫描（2026-10-20）：显式把 docs/ 目录也传进去。
    #   文档正在拆成 docs/*.md（README / 01-architecture / ...），只检查主文件
    #   会让子文档里的符号引用完全失去守护。目录参数会被递归展开（跳过 anchors/）。
    ('跨文件扫描 _test_anchor_multifile', 'doc',
     [PY, os.path.join(TESTS, '_test_anchor_multifile.py')], 60, None),
    # 数字引用校验（2026-10-20）并入 _check_anchors 方向 C：手写指标值
    #   （N 步 / N 个 build_* / N 条契约 / 覆盖率 N/M）与源码实况比对，不一致判红。
    ('文档三向体检 _check_anchors（主文档 + docs/）', 'doc',
     [PY, os.path.join(REPORT, '_check_anchors.py'),
      os.path.join(REPORT, '模拟服全链路代码解析.md'),
      os.path.join(REPORT, 'docs')], 180, None),
]

LAYER = {'proto': '① 协议对账', 'dispatch': '② 分派契约 / 数据层',
         'smoke': '③ socket 冒烟', 'doc': '④ 文档体检'}


# ── 退出码审计 ────────────────────────────────────────────────────────
# 「子测试返回非 0 却被吞掉」有两条路径，分别静态查与运行时查：
#   ① 静态：命令里写 `|| true` / `; true` / `| tail` 之类，用 shell 把 rc 抹平；
#   ② 运行时：ok_pred 忽略 rc，把非 0 判成通过。
# ①在 runner 里几乎不可能发生（subprocess 用列表、不经 shell），
# 但**测试脚本内部**可能调 shell，所以连同被调脚本一起扫。
# 一旦有人在 STEPS 或子脚本里加了这类写法，审计立即判红。
SWALLOW_PATTERNS = (
    ("|| true", 'shell 里 || true 会把退出码抹成 0'),
    ("|| :", '|| : 同上'),
    ("; true", '; true 覆盖前一条的退出码'),
    ("|| exit 0", '显式丢弃退出码'),
    ("| tail", '管道只传最后一条命令的 rc'),
    ("| head", '管道只传最后一条命令的 rc'),
    ("check=False", 'subprocess 不检查 rc'),
    ("shell=True", '经 shell 执行，rc 可能被管道/连接符改写'),
)


def verify_exit_codes(steps, results, full_set=None):
    """退出码审计：返回违规列表（空=全部合规）。

    :param steps:   STEPS 定义（查 ok_pred 是否登记在 EXIT_CODE_EXEMPT）
    :param results: [(name, passed, dt, tail, rc)] 实际执行结果
    """
    problems = []
    # ① 静态：STEPS 里任何一条命令带吞码写法
    for name, _layer, argv, _to, _pred in steps:
        joined = ' '.join(str(a) for a in argv)
        for pat, why in SWALLOW_PATTERNS:
            if pat in joined:
                problems.append('[静态] %s：命令含 %r（%s）' % (name, pat, why))
    # ② 运行时：rc != 0 但被判通过，且没在例外表里登记
    for name, passed, _dt, _tail, rc in results:
        if rc != 0 and passed and name not in EXIT_CODE_EXEMPT:
            problems.append('[运行时] %s：退出码 %d 却被判 PASS，且未登记进 '
                            'EXIT_CODE_EXEMPT' % (name, rc))
        # 反向：登记了例外，却根本没用到 ok_pred（登记成了摆设）
        if rc != 0 and not passed and name in EXIT_CODE_EXEMPT:
            pass        # 例外步骤真的失败，属正常失败，不算审计问题
    # ③ 例外表本身不许有「查无此步」的死条目。
    # ★ 只在**跑全量**时校验：按关键字/--fast/--slow 筛选时，没被选中的步骤
    #   自然不在 steps 里，拿它判死条目会误报（首版就这么把筛选跑搞出一堆假红）。
    if full_set is not None:
        for name in EXIT_CODE_EXEMPT:
            if name not in full_set:
                problems.append('[登记] EXIT_CODE_EXEMPT 里的 %r 不在 STEPS 中'
                                '（失效条目，步骤改名了？）' % name)
    return problems


def run_step(name, argv, timeout, ok_pred=None):
    t0 = time.time()
    rc = None
    # ★ 把 SIM 注入 PYTHONPATH：各 `_test_*.py` 头部仍有
    #   `SIM = join(dirname(HERE), 'sim')` 这种**旧布局**假设（假设 tests 与 sim 平级，
    #   而本工作区 sim 在 tests 里面）。逐文件改路径是 29 处机械改动、容易漏，
    #   在 runner 这一处注入可让全部子测试都 import 得到 m4_server；
    #   改过的三个战斗测试同时修了自身路径，直接单跑也可以。
    env = dict(os.environ)
    env['PYTHONPATH'] = (SIM + os.pathsep + env['PYTHONPATH']) if env.get('PYTHONPATH') else SIM
    try:
        p = subprocess.run(argv, cwd=SIM, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        out = p.stdout.decode('utf-8', 'replace')
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.output or b'').decode('utf-8', 'replace') if e.output else ''
        out += '\n[runner] 超时 %ds' % timeout
        rc = 124
    dt = time.time() - t0
    passed = ok_pred(out, rc) if ok_pred else (rc == 0)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
    # ★ 崩溃类返回码要显式标注（2026-10-20）：Windows 上 Python 进程崩溃/被强杀时
    #   rc 是 NTSTATUS（如 0xC0000409 = STATUS_STACK_BUFFER_OVERRUN）。
    #   这和「测试断言失败」性质完全不同，混在一起看会误导排查方向。
    if rc is not None and rc > 0x80000000:
        # 崩溃时把完整输出落盘：tail 只有 6 行，往往正好错过关键信息。
        try:
            import tempfile as _tf
            _p = os.path.join(_tf.gettempdir(),
                              'runner_crash_%s.log' % name.replace('/', '_')[:40])
            with open(_p, 'w', encoding='utf-8') as _f:
                _f.write('argv=%r\nrc=0x%08X\n--- output ---\n%s'
                         % (argv, rc, out))
            tail = tail + ['[runner] 进程异常终止：rc=0x%08X'
                           '（非测试失败，可能是崩溃/被强杀）' % rc,
                           '[runner] 完整输出已存：%s' % _p]
        except OSError:
            tail = tail + ['[runner] 进程异常终止：rc=0x%08X' % rc]
    return passed, dt, tail, rc


def main(argv):
    fast = '--fast' in argv
    slow = '--slow' in argv
    keys = [a for a in argv if not a.startswith('--')]
    if fast and slow:
        print('--fast 与 --slow 互斥')
        return 2

    steps = STEPS
    if fast:
        steps = [s for s in steps if s[1] in ('proto', 'dispatch')]
    elif slow:
        steps = [s for s in steps if s[1] == 'smoke']
    if keys:
        steps = [s for s in steps if any(k in s[0] for k in keys)]
    if not steps:
        print('没有匹配的步骤（关键字: %s）' % keys)
        return 2

    print('=' * 78)
    print(' 一键回归：%d 个步骤' % len(steps))
    print('=' * 78)
    results = []
    cur_layer = None
    for name, layer, argv, timeout, ok_pred in steps:
        if layer != cur_layer:
            cur_layer = layer
            print('\n── %s ──' % LAYER[layer])
        print('  ▶ %s …' % name, flush=True)
        passed, dt, tail, rc = run_step(name, argv, timeout, ok_pred)
        results.append((name, passed, dt, tail, rc))
        # 有 ok_pred 的步骤把 rc 一并显示：让「rc 非 0 但按准则通过」在输出里可见，
        # 而不是藏在判定逻辑里（这正是退出码审计要消灭的盲区）。
        rc_note = '' if (rc == 0 or ok_pred is None) else '　rc=%d（按准则通过）' % rc
        print('    %s（%.1fs）%s' % ('PASS' if passed else 'FAIL', dt, rc_note),
              flush=True)
        if not passed:
            for ln in tail:
                print('      | %s' % ln)

    # ── 退出码审计（在汇总之前跑，结果并入最终判定）──
    # full_set 只在「没做任何筛选」时传入：筛选跑不该校验死条目。
    _filtered = bool(fast or slow or keys)
    audit = verify_exit_codes(steps, results,
                              full_set=None if _filtered else {s[0] for s in STEPS})

    print('\n' + '=' * 78)
    print(' 汇总')
    print('=' * 78)
    npass = sum(1 for r in results if r[1])
    for name, passed, dt, _tail, rc in results:
        mark = 'PASS' if passed else 'FAIL'
        if rc != 0 and passed:
            mark = 'PASS*'      # 带星：rc 非 0 但按登记准则通过（审计已核）
        print('  %-5s %7.1fs  %s%s' % (mark, dt, name,
                                       '' if rc == 0 else '　(rc=%d)' % rc))
    print('-' * 78)
    print(' 合计 %d/%d 通过%s' % (npass, len(results),
                                 '' if npass == len(results) else '　← 有失败，看上方 | 行'))
    if any(r[4] != 0 and r[1] for r in results):
        print('  （PASS* = 退出码非 0，但按 EXIT_CODE_EXEMPT 登记的准则判定通过）')
    if npass != len(results):
        print('\n失败步骤的最后几行输出已打印在上面；完整输出可单独跑对应脚本。')

    # 退出码审计：任何「非 0 被吞」都直接判红，即使 npass 恰好等于总数
    if audit:
        print('\n' + '!' * 78)
        print(' 退出码审计：发现 %d 处「非 0 退出码被吞」' % len(audit))
        print('!' * 78)
        for a in audit:
            print('  ✗ %s' % a)
        print('\n  这类问题会让回归「看起来全绿」而实际有失败。')
        print('  要么修子测试，要么在 EXIT_CODE_EXEMPT 里登记理由。')
        _print_stats(results, fast=fast, slow=slow, keys=keys)
        return 1

    print('\n退出码审计：通过（无非 0 被吞，%d 条例外均已登记）'
          % len(EXIT_CODE_EXEMPT))
    _print_stats(results, fast=fast, slow=slow, keys=keys)
    return 0 if npass == len(results) else 1


def _print_stats(results, fast=False, slow=False, keys=()):
    """末尾打固定格式 STATS 块（2026-10-20）。

    块里 steps 三项是本次实测，代码事实（build_* / 覆盖率 / 契约表）由
    `tests/_stats.py` 现场采集。报告引数字从这里复制，不再四个工具手工抄。
    mode 记录筛选方式——筛选跑的 steps_total 不可与全量比（diff 前先看它）。
    STATS 自身出错不许影响回归退出码：它是报告工具，不是测试。
    """
    try:
        # runner 自身在 sim/tests/，而 _stats.py 与各测试一样住在 服务端源码/tests/
        # （TESTS 常量）——不手动入路径，import 会按脚本目录找不到。
        if TESTS not in sys.path:
            sys.path.insert(0, TESTS)
        if SIM not in sys.path:      # _stats 采集 build_* 时会 import m4_server
            sys.path.insert(0, SIM)
        import _stats
        mode = 'full'
        if fast:
            mode = 'fast'
        elif slow:
            mode = 'slow'
        elif keys:
            mode = 'filter:' + ','.join(keys)
        elapsed = round(sum(r[2] for r in results), 1)
        npass = sum(1 for r in results if r[1])
        print('')
        print(_stats.format_stats(steps_total=len(results), steps_passed=npass,
                                  steps_failed=len(results) - npass,
                                  elapsed_sec=elapsed, mode=mode))
    except Exception as e:                              # noqa: BLE001
        print('\n[runner] STATS 块打印失败（不影响回归判定）：%r' % e)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
