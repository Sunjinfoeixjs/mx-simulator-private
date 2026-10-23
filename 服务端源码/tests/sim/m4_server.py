# -*- coding: utf-8 -*-
"""单机测试模拟服 v4 —— 移动 + NPC 对话 + 回合制战斗 + 任务 最小可玩闭环。

在 m2_server.py（进世界）基础上扩展 M4：
  移动：C2S_MAP_MOVE(54) → S2CMapTrack(86) 回显；每 N 次移动触发一次遇怪
  地图：多地图 + 传送（M7→全图）。60 张场景由 mapdata_resolve.py 批量联立
        minimap_data × findpathdata 得出（56 张自出生图连通，初云区域 4 张为真孤岛）：
        传送使 NPC 选目的地切图；走到出口坐标自动切图；
        切图 = SceneM(96)（含场景名/小地图）+ 该图全部 AddNpc(99) + GotoM(97)
  NPC ：进场景摆村长(id=1000)；C2S_NPC_LOOK(96) → S2CNpcChatExt(152) 对话
        C2S_NPC_ANSWER(98) → 按选项序号下发下一页/接任务
  战斗：遇怪 → S2CWarStart(112) + S2CWarAdd(115)×2 + S2CWarTurn(114)
        主角动作(160-165) → 速度序结算 → 宠物动作(168-174) → 回合/结束
  任务：C2S_GET_TASK(48) → S2CTaskAdd(72)；NPC 选项接任务 → 战斗击杀计数 → 达成 → 交任务
        → S2CTaskRemove(73) + 奖励（金钱/经验/道具）（M6 闭环）

帧格式（真机实测；★两向不对称，详见 协议资料/规格/M1_协议规格.md §1）：
  S2C：[1B 长度 N][opcode 1B][data]，N 含 opcode        （make_s2c）
  C2S：[opcode 1B][1B 长度 N][body N]，N 不含 opcode    （recv_c2s）
变长字符串 = GBK 字节 + \0（C 字符串，**无长度前缀**，见 var_gbk；旧注释写的
「[1B len][GBK]」是错的，2026-10-15 真机截图已推翻）；UnpackDesc 空值 = 单字节 0x00

用法:
  python m4_server.py [--seed 0x12345678] [--port 3134] [--encounter-every 5]
                      [--enemies 6] [--turn-timeout 30]

回合制约（2026-10-16 依客户端录像补）：
  · 出手顺序：单位按速度降序结算（主角 HERO_SPEED；敌方按**造型**取基值
    ENEMY_SPEED_BY_SHAPE 再逐个递减，未登记造型回落 ENEMY_SPEED_BASE），
    快于主角的敌方先动；宠物指令在客户端提交后结算，再推进慢于宠物的单位。
    客户端不显示序号，顺序体现为帧到达次序。
  · 指令窗口：交出操作权后 30s 倒计时（对齐 warui.ShowTimer），
    主角或宠物指令超时均默认普攻。
"""
import argparse
import copy
import json
import os
import random
import re
import socket
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:                                    # 掉落真值表（gen_drop_data.py 生成，勿手改）
    from drop_data import scene_pool
except ImportError:                     # 缺表时降级为「不掉落」，不阻断起服
    scene_pool = None

try:                                    # 场景遇怪表（gen_encounter_data.py 生成，勿手改）
    from encounter_data import MAP_ENCOUNTERS as ENCOUNTER_BY_MAP
except ImportError:                     # 缺表时降级为「全图统一野狼」，不阻断起服
    ENCOUNTER_BY_MAP = {}

import damage_model as dmgm             # 物伤公式纯函数（反向还原版，见 damage_model.py）
import team as teammod                  # 组队状态 + S2C_PLAYER_TEAM(82) 帧编码
import lzss                            # 自定义 LZSS（S2C 压缩；原版 DLL 加密模式下必需）
import frames as bf                    # ★ 战斗帧的**唯一字段定义** + 生成的 pack/unpack
                                       #   （build_war_* 与战斗指令解析都走这里，见 frames.py 头部）
import quest_data as questmod          # 主线剧情 1–6 数据（NPC 摆位/步骤/奖励）
import juqing_skill_data as jqmod      # 剧情技能表（剧情点的唯一消费入口，生成物）
import bus                             # 业务事件总线（调试台「任务链/经济」页签的数据源）
import economy_ledger as eled          # ★ P3-05 经济流水账本（只记不改，可对账）
import economy_service as esvc         # ★ P3-05 金钱/活力/体力变动的**唯一入口**
import equipment_rules as eqr          # ★ P3-01 装备判定（槽位/等级门，真值见 equip_data.py）
import progression_service as prog     # ★ P3-01 最终属性**唯一入口**（派生值只算不存）
import pet_progression as ppg          # ★ P3-02 宠物属性**唯一入口**（三层：面板/战斗/资源）
import pet_skill_service as pss        # ★ P3-02 宠物技能表增删（客户端无上行 opcode）
import pet_skill_data as psd           # ★ P3-02 宠物技能真值表（生成物，404 条）
import battle_rng as brng              # ★ P3-03 战斗随机源（固定种子 + 流分离）
import combat_effects as ceff          # ★ P3-03 状态效果机制（284 条真值 / 29 条可量化）
import buff_data as bfd                # ★ P3-03 状态真值表（生成物，284 条）
import enemy_ai as eai                 # ★ P3-03 敌方行动决策（策略化）

# ───────────────────────────── 真值战斗公式导入 ─────────────────────────────
# ✅ 真值：客户端 petfightdata.py、petdata.py 直接给出的公式与常数
try:
    from battle_formulas import (
        PET_FORMULAS, PET_ATTR_IDX, FACTOR_DATA, CORRECT_DATA,
        PET_SKILL_DATA, PET_BASE_INFO,
        calc_pet_stats, calc_pet_zz, calc_monster_stats,
        CONTROL_POINTS, ESCAPE_MECHANICS, RAGE_SYSTEM, MONSTER_LEVEL_SCALING,
    )
    _HAS_TRUE_FORMULAS = True
except ImportError:
    _HAS_TRUE_FORMULAS = False
    PET_FORMULAS = {}
    FACTOR_DATA = {}
    CORRECT_DATA = {}
    PET_BASE_INFO = {}

try:                                    # 加密链路：原版 mxmain.dll（+0x72=1）时启用
    import mx_crypto
    from transport import CryptoConn
except ImportError:                     # 缺加密件时降级明文，不阻断起服
    mx_crypto = None
    CryptoConn = None

# ── 项目根 / 日志 / 存档目录 ────────────────────────────────────────
# ★ 2026-10-23 P5-00 修：原先写 `dirname×3`，而这文件在 `MX/服务端源码/tests/sim/`
#   是 4 层深 → CLAW 只到 `MX/服务端源码`，再拼 `'服务端源码'` 就成了
#   `MX/服务端源码/服务端源码/save/role.json`（多一层）。
#   后果不报错：`load_role()` 对「文件不存在」返回 None 属正常路径，
#   默认启动读到空档，历史 `服务端源码/save/role.json` 被**静默忽略**。
#   改成按标志目录找（见 proj_root.py），以后挪文件不会重犯。
#   `fallback_root(…, 4)` 只在标志目录都不在时才用：宁可用一个确定的兜底路径
#   起服，也不要因为定位失败直接崩。
try:
    from proj_root import find_project_root, fallback_root
except ImportError:                     # 单文件拷贝/极度精简部署时的兜底
    def find_project_root(_start):
        return None

    def fallback_root(start, levels):
        p = os.path.abspath(start)
        if not os.path.isdir(p):
            p = os.path.dirname(p)
        for _ in range(max(0, int(levels))):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return p
CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
LOGDIR = os.path.join(CLAW, '服务端源码', 'logs')
# 持久化存档（角色/背包/宠物/资源跨进程重启留存）
SAVE_DIR = os.path.join(CLAW, '服务端源码', 'save')
SAVE_PATH = os.path.join(SAVE_DIR, 'role.json')


def char_savepath(savedir, account):
    """多角色存档路径：savedir/role_<账号>.json。

    账号原样进文件名不安全（可能含 / \\ : * ? " < > | 或非 ASCII 空格），
    这里只保留 [A-Za-z0-9_-]，其余折叠成 '_'；空账号退回旧的 role.json。
    """
    acc = (account or '').strip()
    if not acc:
        return os.path.join(savedir, 'role.json')
    safe = re.sub(r'[^0-9A-Za-z_-]', '_', acc)[:64] or 'anon'
    return os.path.join(savedir, 'role_%s.json' % safe)


# ══════════════════════════════════════════════════════════════════════
# 存档 schema 版本与迁移
# ══════════════════════════════════════════════════════════════════════
# 为什么需要：存档是**用户资产**，字段会随功能增加（M9 养成、M10 买卖、M13 制作…）。
# 没有版本号时，「老档缺新字段」只能靠 d.get(key, 默认值) 在每个读取点各写一遍，
# 一旦某字段需要**转换**（不只是补默认值，比如单位变化、结构重组），
# 就会散落成一堆隐式约定，改一处漏一处。
#
# 约定：
#   · 存档里 _schema 记版本号（整数）。缺失 = 版本 0（即加版本号之前的所有老档）。
#   · MIGRATIONS 是 {源版本: 迁移函数}，把「版本 N」的 dict 升级成 N+1。
#   · 只支持**升级**。真正要防的是「新程序读到比它还新的档」（见 load_role）。
#
# ★ 版本比程序还新时：不猜、不静默。返回 None 让上层认为「存档不可读」并
#   _save_blocked=True（禁止自动保存），保住用户文件。这是有意的——
#   用旧程序打开新档再落盘，等于把新字段全删了。
SCHEMA_VERSION = 2

# ── 存档落盘策略（2026-10-22）──────────────────────────────────────────
# 异步：落盘走后台写线程，游戏循环不阻塞在磁盘 IO 上。代价是「_persist() 返回」
#   并不代表文件已写好——所以断线/停机/测试必须调 flush_saves() 收口。
# 增量：与上次快照逐键比对，全同则跳过整次落盘。
SAVE_ASYNC = True


def _migrate_0_to_1(d):
    """版本 0 → 1：规范化 items/tasks 的键类型。

    0 版存档里这两个 dict 的 JSON 键是字符串，早期代码在**各个读取点**分别转换；
    这里统一转一次，后续迁移可以假定「键已是 int」。
    """
    if isinstance(d.get('items'), dict):
        fixed = {}
        for k, v in d['items'].items():
            try:
                fixed[int(k)] = v
            except (TypeError, ValueError):
                continue          # 坏键（理论上不会出现）→ 丢弃，不污染背包
        d['items'] = fixed
    if isinstance(d.get('tasks'), dict):
        fixed = {}
        for k, v in d['tasks'].items():
            try:
                fixed[int(k)] = v
            except (TypeError, ValueError):
                continue
        d['tasks'] = fixed
    d['_schema'] = 1
    return d


def _migrate_1_to_2(d):
    """版本 1 → 2：把前几轮陆续加进来的字段补成**显式默认值**。

    这些字段原先靠各读取点的 d.get(k, 默认) 兜底；迁移里显式写出来，
    好处是「迁移后的档 = 完整档」——将来读档不必再到处写默认值。
    ★ 默认值必须与 __init__ 里的同名字段同源（引用常量，不写字面量），
      否则改常量后迁移出的老档会与新建角色不一致。
    """
    defaults = {
        'hero_hp': HERO_HP,
        'pet_hp': PET_HP,
        'active_pet_id': PET_ID,
        'hero_shape': DEFAULT_HERO_SHAPE,
        'coins': COIN_INIT,
        'iid_seq': 9000,
        'mapid': START_MAP_ID,
        'move_count': 0,
        'exp': 0,
        'next_exp': 1000,
        'grade': 1,
        'points': 5,
        'tili': HERO_TILI_INIT,
        'huoli': HERO_HUOLI_INIT,
        'life_lv': LIFE_LV_INIT,
        'life_exp': LIFE_EXP_INIT,
        'team': {},
        'tasks': {},
        'items': {},
        '_save_tag': '',
    }
    for k, v in defaults.items():
        if k not in d:
            d[k] = v
    # ★ hero_res 不给字面量默认值：它的 At/Df/Dp 必须取**当前**全局
    #   （world_config 会在 import 后覆盖 HERO_AT 等），这里写死会让老档
    #   带着旧参数点复活。存 None 作为「待按当前参数重算」的显式标记，
    #   由 M4Server.__init__ / _apply_save 调 hero_res_fresh() 补上。
    if not isinstance(d.get('hero_res'), dict):
        d['hero_res'] = None
    # 五维/招式在旧档里可能是字符串键（JSON 固有），统一成字符串键 + int 值
    if isinstance(d.get('attrs'), dict):
        d['attrs'] = {str(k): int(v) for k, v in d['attrs'].items()}
    if isinstance(d.get('magics'), dict):
        d['magics'] = {str(k): int(v) for k, v in d['magics'].items()}
    d['_schema'] = 2
    return d


# 迁移链：{源版本: 函数}。函数接收 dict、返回 dict（可就地改，也可新建）。
MIGRATIONS = {
    0: _migrate_0_to_1,
    1: _migrate_1_to_2,
}


def migrate_role(d, from_version=None):
    """把存档 dict 逐级升到 SCHEMA_VERSION。返回 (data, applied_steps)。

    设计要点：
      · **逐级**跑，不跳版：0→1→2 每一步都能单独测试，比「0 直跳 2」可靠。
      · 迁移函数抛异常时**不吞**：让上层把该档当作不可读（→ 禁止自动保存），
        而不是带着半迁移的状态继续跑——那会写坏用户档。
      · 迁移**不保证幂等**，所以只在当前版本 < SCHEMA_VERSION 时调用；
        已是最新版的档绝不重跑。
    """
    if not isinstance(d, dict):
        return None, []
    cur = d.get('_schema')
    if cur is None:
        cur = 0
    elif not isinstance(cur, int) or isinstance(cur, bool):
        # 版本号本身坏了 → 不猜它是第几版，按不可读处理
        raise ValueError('存档 _schema 非法：%r' % (cur,))
    if from_version is not None:
        cur = from_version
    if cur > SCHEMA_VERSION:
        raise ValueError('存档版本 _schema=%d 高于本程序支持的 %d'
                         % (cur, SCHEMA_VERSION))
    steps = []
    while cur < SCHEMA_VERSION:
        fn = MIGRATIONS.get(cur)
        if fn is None:
            raise ValueError('缺少 _schema=%d → %d 的迁移函数' % (cur, cur + 1))
        d = fn(d)
        steps.append('%d→%d' % (cur, cur + 1))
        cur += 1
    if d.get('_schema') != SCHEMA_VERSION:
        d['_schema'] = SCHEMA_VERSION
    return d, steps


def normalize_items(items):
    """规范化背包条目：补齐缺失字段、把值转成正确类型。**每次读档都跑**。

    ★ 为什么不做成「迁移的一步」（2026-10-19 踩坑）：
      迁移只在 _schema < SCHEMA_VERSION 时触发。而「版本对、内容坏」完全可能出现：
      手写存档、早期代码写的档、外部工具改过的档，都可以标着 _schema=2 却带着
      key="0"（字符串）这种值。此时迁移不跑 → 字符串原样进 build_item_add
      → struct.error → 进世界时 socket 线程异常 → 客户端卡住。
      条目级规范化是**幂等的**，每次读都跑一次最稳妥，代价可忽略。

    注：pos 缺省用 20 而不是 ITEM_POS_BAG_START —— 本函数与常量定义之间存在
    导入顺序约束（常量在模块后段），写字面量并在此注明。
    """
    if not isinstance(items, dict):
        return {}
    out = {}
    for iid, info in items.items():
        if not isinstance(info, dict) or "shape" not in info:
            continue                       # 坏条目：丢弃，不让它炸进世界
        try:
            out[int(iid)] = {
                "shape": int(info.get("shape") or 0),
                "key": int(info.get("key") or 0),
                "qty": max(1, int(info.get("qty") or 1)),
                "pos": int(info.get("pos") if info.get("pos") is not None else 20),
                "infoext": info.get("infoext") or "",
            }
        except (TypeError, ValueError):
            continue                       # 转不成 int 的坏条目：丢弃
    return out


def normalize_tasks(tasks):
    """规范化任务进度：**每次读档都跑**，不依赖 _schema。

    ★ 2026-10-20 踩坑（与 normalize_items 同源，但当时只修了背包）：
      任务键的 int 还原原先只写在 `_migrate_0_to_1` 里，而迁移只在
      `_schema < SCHEMA_VERSION` 时触发。于是**任何已标 _schema=2 的档**读回来
      `tasks` 的键都是 JSON 固有的字符串（`{'1': ...}`），
      `self.tasks.get(TASK_NEWBIE_ID)`（int 1）永远查不到 →
      「接了任务、重启后任务栏消失 / 进度归零」，而且不报错、不崩，
      只是静默失效（tests/_test_task.py 的持久化两条就是被这个打红的）。

    所以键归一化属于**JSON 格式层**，不属于 schema 迁移层：每次读都跑一遍。
    未知字段原样保留（将来加字段不会被这里吃掉）。
    """
    if not isinstance(tasks, dict):
        return {}
    out = {}
    for tid, info in tasks.items():
        if not isinstance(info, dict):
            continue                       # 坏条目：丢弃
        try:
            key = int(tid)
        except (TypeError, ValueError):
            continue                       # 键不是数字：丢弃
        item = dict(info)                  # 保留未知字段
        try:
            item['state'] = int(item.get('state') or 0)
            item['progress'] = max(0, int(item.get('progress') or 0))
        except (TypeError, ValueError):
            continue
        out[key] = item
    return out


def load_role(path=None, migrate=True):
    """读取角色存档；不存在/损坏则返回 None。path=None 用模块默认 SAVE_PATH。

    ★ 2026-10-19：加 schema 版本与迁移（见上方 SCHEMA_VERSION / MIGRATIONS）。
    迁移失败（含「档比程序新」）一律返回 None —— 上层据此 _save_blocked=True
    禁止自动保存，保住用户文件；**绝不带着可疑状态继续跑**。
    传 migrate=False 可拿到原始 dict（供诊断工具/迁移测试用）。
    """
    try:
        with open(path or SAVE_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        if any(key in d and not isinstance(d[key], dict)
               for key in ('hero_res', 'items', 'tasks')):
            return None
        if not migrate:
            return d            # 原始档：连规范化都不做，供诊断/测试
        # ★ 无条件规范化背包与任务键（幂等）：不依赖 _schema，见两个 normalize_* 的说明。
        # ★ 但「规范化把内容全丢光」不能当成『空档』：那说明原档是坏的。
        #   如果这时返回一个空 dict，上层会 _save_blocked=False 并**当场覆盖用户文件**，
        #   等于把用户的档静默清空。所以「原本非空、规范化后为空」一律判为损坏档 →
        #   返回 None → _save_blocked=True → 只读不写，保住原文件。
        raw_items = d.get('items')
        norm_items = normalize_items(raw_items)
        if isinstance(raw_items, dict) and raw_items and not norm_items:
            return None
        d['items'] = norm_items
        try:
            d, _steps = migrate_role(d)
        except (ValueError, KeyError, TypeError):
            return None
        # 迁移之后再跑一次任务规范化：迁移可能补出 tasks={}，
        # 而老档里已经是字符串键的要在这里统一转回 int。
        raw_tasks = d.get('tasks')
        norm_tasks = normalize_tasks(raw_tasks)
        if isinstance(raw_tasks, dict) and raw_tasks and not norm_tasks:
            return None
        d['tasks'] = norm_tasks
        return d
    except (OSError, ValueError):
        return None

# ── 存档并发控制（账号级锁）─────────────────────────────────────────────
# 为什么需要：MultiServer 下同一账号可以有多个会话（重连残留、双开、GM 另开一个），
# 它们各自持有一份内存状态，落档时都写同一个 role_<账号>.json。
# 仅靠 mkstemp + os.replace 的**原子性**只能保证「文件不会是半截」，
# 保证不了「后写的把先写的覆盖掉」——那是**丢失更新**，不是文件损坏。
# 实测（2026-10-19）：两个线程各写 200 次，出现 29 次写失败（Windows 上
# os.replace 与并发读/写撞上会抛 PermissionError）。
#
# 两层锁：
#   ① 进程内 threading.Lock，按**文件路径**分桶——同一进程的多个会话互斥；
#   ② 跨进程文件锁（Windows: msvcrt.locking / POSIX: fcntl.flock）——
#      防止「同时开了两个 python 进程跑模拟服」这种意外。
# 拿不到跨进程锁时不阻塞死等（那会让保存变成卡死），而是**降级**：
# 仍持进程内锁写入，并在日志里说明「跨进程锁未获取」。
_LOCK_REGISTRY = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _path_lock(path):
    """取该路径对应的进程内互斥锁（惰性建，按绝对路径分桶）。"""
    key = os.path.abspath(path)
    with _LOCK_REGISTRY_GUARD:
        lk = _LOCK_REGISTRY.get(key)
        if lk is None:
            lk = threading.Lock()
            _LOCK_REGISTRY[key] = lk
        return lk


class _CrossProcessLock(object):
    """跨进程独占锁（基于 <path>.lock 旁路文件）。

    Windows 用 msvcrt.locking（阻塞模式需要重试，这里用非阻塞 + 超时轮询），
    POSIX 用 fcntl.flock(LOCK_EX | LOCK_NB)。两者都拿不到就返回 False，
    由调用方决定降级策略——**绝不无声无息地跳过加锁**。
    """

    def __init__(self, path, timeout=1.0, poll=0.01):
        self.lock_path = path + '.lock'
        self.timeout = timeout
        self.poll = poll
        self._fh = None
        self.acquired = False

    def __enter__(self):
        try:
            self._fh = open(self.lock_path, 'a+b')
        except OSError:
            return self
        deadline = time.time() + self.timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return self
            except (OSError, IOError):
                if time.time() >= deadline:
                    return self          # 拿不到：acquired=False，调用方降级
                time.sleep(self.poll)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if self.acquired:
                    if os.name == 'nt':
                        import msvcrt
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        return False


def save_role(data, path=None, lock=True):
    """原子替换角色存档；失败返回 False。path=None 用模块默认 SAVE_PATH。

    ★ 2026-10-19 加锁：同一账号的并发落档此前会**丢更新**（后写的覆盖先写的），
    且 Windows 上并发 os.replace 本身就会失败。现在默认走「进程内互斥 + 跨进程文件锁」，
    详见上方 _LOCK_REGISTRY / _CrossProcessLock 的说明。
    传 lock=False 可跳过（仅供离线工具/测试做纯写入，不参与线上多会话）。
    """
    path = path or SAVE_PATH
    if not lock:
        return _save_role_nolock(data, path)
    with _path_lock(path):
        with _CrossProcessLock(path) as xl:
            ok = _save_role_nolock(data, path)
            if not xl.acquired:
                # 不抛异常、不阻塞：进程内锁已经防住了本进程的多会话，
                # 跨进程只可能是「有人另开了一个服务端」，降级并让上层可见。
                pass
            return ok


def _save_role_nolock(data, path):
    tmp_path = None
    fd = None
    try:
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix='.%s.' % os.path.basename(path), suffix='.tmp',
            dir=directory)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            fd = None
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

# ★ 解释器关闭标志（2026-10-20）：atexit 置位后，log() 不再写 stdout。
#   根因见 M4Server.log 的注释：daemon 线程在 shutdown 阶段 print 会触发
#   _enter_buffered_busy 致命错误（进程以 0xC0000409 退出）。
_SHUTTING_DOWN = False

SEED_DEFAULT = 0x12345678
SERVER_NUM = 1

# 传输加密/压缩开关（与客户端 mxmain.dll 补丁状态一一对应）：
#   True  = 原版 DLL（+0x72=1，加密+压缩开启）：S2C 需 LZSS 压缩、收发需加密
#   False = 补丁 DLL（+0x72=0，明文）：裸帧（默认，向后兼容现有测试/补丁客户端）
# 该标志是「进程级全局」，因为同一份客户端 DLL 要么打过补丁、要么没打过——
# 由 M4Server.handle() 按 self.encrypt 在握手后设置。绝大多数字段在单连接
# 单机场景下无并发；多会话下所有会话共享同一 encrypt 值，结果一致。
_CRYPTO_MODE = False


def set_crypto_mode(enabled):
    global _CRYPTO_MODE
    _CRYPTO_MODE = bool(enabled)


def crypto_mode():
    return _CRYPTO_MODE

# ── 场景（多地图 + 传送，M7）──
# 数据来源（全部取自客户端还原脚本，非臆造）：
#   · 场景名 = minimap_data[mapid] 的出口点名集合 × findpathdata 邻接表 联立求解
#     （服务端源码/sim/mapdata_resolve.py 可复现；Jaccard 1.0 高置信。永泽城另由资料库坐标佐证：
#       资料全库「南遥国——永泽城：师道殿(241,149)」≈ minimap_data[1001] 师道殿(245,151)）
#   · spawn / NPC 坐标 = npcposdata.py 真实 NPC 站位（保证落在可行走区内）
#   · 出口坐标 = minimap_data.py 真实出口标记
SCENE_ID = 1                 # 客户端 scene 标识（服务端只用 1）
START_MAP_ID = 1001          # 出生图
MAP_ID = START_MAP_ID        # 兼容旧引用

# 原「孤岛」子图：1158/1159/1176/1177 只在内部互通，原版外部入口「天空要塞」
# 在本库没有对应 mapid。★ 2026-10-22 在 1054↔1159 之间补了一条双向边（见 MAP_LINKS），
# 这 4 张图现在**自出生图可达**；集合保留，只用于 GM 面板标注「地理上独立的一块」。
ISLAND_MAP_IDS = frozenset((1158, 1159, 1176, 1177))
MINIMAP_ID = START_MAP_ID
SPAWN_X, SPAWN_Y = 45, 40    # 出生图初始坐标（沿用现有实测可用值）

# ── 全图连通性（BFS，自 mapid 1001）──
# 可达 60 / 共 60 张：1158 初云峡谷 / 1159 初云平原 / 1176 魔晶要塞 / 1177 圣痕峡谷
# 原本唯一外部出口是「天空要塞」（本库无该 mapid），现由 1054↔1159 一条**模拟边**接回，
# 属模拟便利而非原版地理。连通性由 tests/_test_content.py 逐张 BFS + _goto_map 守着。

# mapid → {'name', 'minimap', 'spawn', 'npcs': [(id, shape, x, y, name)]}
MAPS = {
    1001: {'name': '永泽城', 'minimap': 1001, 'spawn': (45, 40), 'npcs': [
        (1000, 1013, 47, 42, '村长'),
        (1001, 4427, 60, 55, '传送使'),
        (1002, 4427, 52, 38, '战斗技能师'),
        (1200, 4427, 71, 11, '车夫'),
    ]},
    1002: {'name': '清溪城', 'minimap': 1002, 'spawn': (66, 117), 'npcs': [
        (1100, 4205, 39, 11, '公孙闲'),
        (1001, 4427, 66, 117, '传送使'),
    ]},
    1003: {'name': '谷雨镇', 'minimap': 1003, 'spawn': (104, 84), 'npcs': [
        (1100, 4752, 104, 84, '谷雨镇商人'),
        (1001, 4427, 46, 40, '传送使'),
    ]},
    1004: {'name': '清溪平原', 'minimap': 1004, 'spawn': (106, 48), 'npcs': [
        (1100, 4298, 144, 8, '王二'),
        (1001, 4427, 106, 48, '传送使'),
    ]},
    1005: {'name': '天水平原', 'minimap': 1005, 'spawn': (236, 35), 'npcs': [
        (1001, 4427, 112, 6, '传送使'),
        (1200, 4427, 113, 3, '车夫'),
    ]},
    1006: {'name': '新月平原', 'minimap': 1006, 'spawn': (99, 42), 'npcs': [
        (1100, 4136, 69, 47, '粘土矿老板'),
        (1001, 4427, 41, 108, '传送使'),
    ]},
    1007: {'name': '香叶城', 'minimap': 1007, 'spawn': (13, 200), 'npcs': [
        (1001, 4427, 20, 17, '传送使'),
        (1200, 4427, 13, 19, '车夫'),
    ]},
    1008: {'name': '芳草镇', 'minimap': 1008, 'spawn': (11, 20), 'npcs': [
        (1001, 4427, 145, 114, '传送使'),
    ]},
    1009: {'name': '古木神庙', 'minimap': 1009, 'spawn': (189, 16), 'npcs': [
        (1001, 4427, 189, 16, '传送使'),
    ]},
    1010: {'name': '香叶丛林', 'minimap': 1010, 'spawn': (9, 24), 'npcs': [
        (1001, 4427, 128, 8, '传送使'),
    ]},
    1011: {'name': '绿湾林地', 'minimap': 1011, 'spawn': (61, 144), 'npcs': [
        (1001, 4427, 180, 7, '传送使'),
    ]},
    1012: {'name': '萤火森林', 'minimap': 1012, 'spawn': (6, 122), 'npcs': [
        (1001, 4427, 93, 76, '传送使'),
    ]},
    1013: {'name': '天苍城', 'minimap': 1013, 'spawn': (384, 23), 'npcs': [
        (1001, 4427, 390, 175, '传送使'),
        (1200, 4427, 380, 152, '车夫'),
    ]},
    1014: {'name': '云雀谷', 'minimap': 1014, 'spawn': (136, 113), 'npcs': [
        (1001, 4427, 145, 7, '传送使'),
    ]},
    1015: {'name': '平安港', 'minimap': 1015, 'spawn': (12, 124), 'npcs': [
        (1001, 4427, 12, 124, '传送使'),
    ]},
    1016: {'name': '荒羽半岛', 'minimap': 1016, 'spawn': (12, 111), 'npcs': [
        (1001, 4427, 145, 10, '传送使'),
    ]},
    1017: {'name': '天苍高地', 'minimap': 1017, 'spawn': (18, 140), 'npcs': [
        (1001, 4427, 33, 12, '传送使'),
    ]},
    1018: {'name': '太息林地', 'minimap': 1018, 'spawn': (12, 133), 'npcs': [
        (1001, 4427, 15, 9, '传送使'),
        (1200, 4427, 72, 15, '灵风塔守护人'),
    ]},
    1019: {'name': '海底神殿', 'minimap': 1019, 'spawn': (9, 173), 'npcs': [
        (1001, 4427, 14, 9, '传送使'),
        (1200, 4427, 9, 173, '船夫'),
    ]},
    1020: {'name': '琉璃港', 'minimap': 1020, 'spawn': (6, 74), 'npcs': [
        (1001, 4427, 134, 114, '传送使'),
    ]},
    1021: {'name': '星霜城', 'minimap': 1021, 'spawn': (186, 32), 'npcs': [
        (1001, 4427, 186, 32, '传送使'),
    ]},
    1022: {'name': '落日海湾', 'minimap': 1022, 'spawn': (20, 8), 'npcs': [
        (1001, 4427, 141, 108, '传送使'),
    ]},
    1023: {'name': '幻境海岸', 'minimap': 1023, 'spawn': (35, 114), 'npcs': [
        (1001, 4427, 154, 32, '传送使'),
    ]},
    1024: {'name': '流光岛', 'minimap': 1024, 'spawn': (15, 115), 'npcs': [
        (1001, 4427, 188, 18, '传送使'),
    ]},
    1025: {'name': '清风村2', 'minimap': 1025, 'spawn': (16, 114), 'npcs': [
        (1001, 4427, 197, 119, '传送使'),
        (1200, 4427, 16, 98, '车夫'),
    ]},
    1026: {'name': '黎明镇', 'minimap': 1026, 'spawn': (6, 5), 'npcs': [
        (1001, 4427, 7, 85, '传送使'),
    ]},
    1027: {'name': '夜幕镇', 'minimap': 1027, 'spawn': (110, 4), 'npcs': [
        (1001, 4427, 114, 79, '传送使'),
    ]},
    1028: {'name': '长风岛', 'minimap': 1028, 'spawn': (153, 99), 'npcs': [
        (1001, 4427, 12, 7, '传送使'),
    ]},
    1029: {'name': '疾风谷', 'minimap': 1029, 'spawn': (13, 113), 'npcs': [
        (1001, 4427, 21, 3, '传送使'),
    ]},
    1030: {'name': '寒月森林', 'minimap': 1030, 'spawn': (13, 144), 'npcs': [
        (1001, 4427, 191, 6, '传送使'),
    ]},
    1031: {'name': '影狼原', 'minimap': 1031, 'spawn': (20, 144), 'npcs': [
        (1001, 4427, 194, 8, '传送使'),
    ]},
    1032: {'name': '碎梦岭', 'minimap': 1032, 'spawn': (4, 140), 'npcs': [
        (1001, 4427, 6, 5, '传送使'),
    ]},
    1033: {'name': '金绿沙洲', 'minimap': 1033, 'spawn': (9, 126), 'npcs': [
        (1001, 4427, 148, 77, '传送使'),
    ]},
    1034: {'name': '修罗森林', 'minimap': 1034, 'spawn': (8, 131), 'npcs': [
        (1001, 4427, 274, 23, '传送使'),
    ]},
    1035: {'name': '落雪冰原', 'minimap': 1035, 'spawn': (230, 6), 'npcs': [
        (1001, 4427, 230, 6, '传送使'),
    ]},
    1036: {'name': '珊瑚迷宫', 'minimap': 1036, 'spawn': (13, 82), 'npcs': [
        (1001, 4427, 13, 82, '传送使'),
    ]},
    1039: {'name': '灵石塔一层', 'minimap': 1039, 'spawn': (5, 4), 'npcs': [
        (1001, 4427, 5, 46, '传送使'),
    ]},
    1040: {'name': '灵石塔二层', 'minimap': 1040, 'spawn': (5, 48), 'npcs': [
        (1001, 4427, 72, 14, '传送使'),
    ]},
    1041: {'name': '灵石塔三层', 'minimap': 1041, 'spawn': (8, 11), 'npcs': [
        (1001, 4427, 107, 85, '传送使'),
    ]},
    1042: {'name': '灵石塔四层', 'minimap': 1042, 'spawn': (8, 83), 'npcs': [
        (1001, 4427, 21, 6, '传送使'),
    ]},
    1043: {'name': '古木祭坛一层', 'minimap': 1043, 'spawn': (46, 78), 'npcs': [
        (1001, 4427, 113, 3, '传送使'),
    ]},
    1044: {'name': '古木祭坛二层', 'minimap': 1044, 'spawn': (6, 86), 'npcs': [
        (1001, 4427, 29, 2, '传送使'),
    ]},
    1045: {'name': '古木祭坛三层', 'minimap': 1045, 'spawn': (42, 83), 'npcs': [
        (1001, 4427, 115, 13, '传送使'),
    ]},
    1046: {'name': '古木祭坛四层', 'minimap': 1046, 'spawn': (51, 3), 'npcs': [
        (1001, 4427, 109, 85, '传送使'),
    ]},
    1047: {'name': '灵石塔五层', 'minimap': 1047, 'spawn': (109, 9), 'npcs': [
        (1001, 4427, 109, 9, '传送使'),
    ]},
    1048: {'name': '古木祭坛五层', 'minimap': 1048, 'spawn': (73, 52), 'npcs': [
        (1001, 4427, 73, 52, '传送使'),
    ]},
    1049: {'name': '沙漠迷宫一层', 'minimap': 1049, 'spawn': (43, 92), 'npcs': [
        (1001, 4427, 154, 10, '传送使'),
    ]},
    1050: {'name': '沙漠迷宫二层', 'minimap': 1050, 'spawn': (182, 108), 'npcs': [
        (1001, 4427, 195, 6, '传送使'),
    ]},
    1051: {'name': '沙漠迷宫三层', 'minimap': 1051, 'spawn': (3, 25), 'npcs': [
        (1001, 4427, 93, 50, '传送使'),
        (1200, 4427, 93, 50, '牛魔统领'),
    ]},
    1052: {'name': '兔神殿遗址', 'minimap': 1052, 'spawn': (5, 20), 'npcs': [
        (1001, 4427, 188, 14, '传送使'),
    ]},
    1053: {'name': '兔坦卡门陵墓', 'minimap': 1053, 'spawn': (231, 151), 'npcs': [
        (1001, 4427, 231, 151, '传送使'),
    ]},
    1054: {'name': '明月镇', 'minimap': 1054, 'spawn': (9, 5), 'npcs': [
        (1001, 4427, 260, 95, '传送使'),
        (1200, 4427, 224, 18, '船夫'),
    ]},
    1073: {'name': '灵风塔一层', 'minimap': 1073, 'spawn': (4, 7), 'npcs': [
        (1001, 4427, 218, 142, '传送使'),
    ]},
    1074: {'name': '灵风塔二层', 'minimap': 1074, 'spawn': (4, 80), 'npcs': [
        (1001, 4427, 23, 3, '传送使'),
    ]},
    1075: {'name': '灵风塔三层', 'minimap': 1075, 'spawn': (114, 4), 'npcs': [
        (1001, 4427, 114, 4, '传送使'),
    ]},
    1076: {'name': '光明之家一层', 'minimap': 1076, 'spawn': (4, 20), 'npcs': [
        (1001, 4427, 4, 20, '传送使'),
    ]},
    1158: {'name': '初云峡谷', 'minimap': 1158, 'spawn': (5, 4), 'npcs': [
        (1001, 4427, 147, 115, '传送使'),
    ]},
    1159: {'name': '初云平原', 'minimap': 1159, 'spawn': (55, 15), 'npcs': [
        (1001, 4427, 141, 115, '传送使'),
        (1200, 4427, 55, 26, '神羿'),
    ]},
    1176: {'name': '魔晶要塞', 'minimap': 1176, 'spawn': (29, 106), 'npcs': [
        (1001, 4427, 120, 25, '传送使'),
        (1200, 4427, 120, 8, '神翎'),
    ]},
    1177: {'name': '圣痕峡谷', 'minimap': 1177, 'spawn': (140, 2), 'npcs': [
        (1001, 4427, 140, 2, '传送使'),
    ]},
}


# ── quest-spawn 并入地图（2026-10-22）──────────────────────────────────────
# 1–3 的剧情 NPC 原本是手写进上面 MAPS 字面量里的（8101 在 1025、8102 在 1036…）。
# 加到 4 条以后不该继续在两个字面量表里各抄一份：`quest_data.NPC_SPAWNS` 才是
# 唯一来源（map / npcid / shape / x / y / name / 出处全在那一张表）。
# 规则：同图同 id 已存在则跳过 → 对 1–3 是无操作，对新增剧情自动生效，可重复执行。
def _merge_quest_spawns():
    merged = 0
    for (mid, nid, shape, x, y, name, _src) in questmod.NPC_SPAWNS:
        d = MAPS.get(mid)
        if d is None:                    # 资料里有、本服未收录的地图：跳过（不静默造图）
            continue
        if any(npc[0] == nid for npc in d['npcs']):
            continue
        d['npcs'].append((nid, shape, x, y, name))
        merged += 1
    return merged


_MERGED_QUEST_NPCS = _merge_quest_spawns()

# 邻接关系（findpathdata.py 真实连通，仅保留已解析目标）
MAP_LINKS = {
    1001: [1054, 1006],
    1002: [1004],
    1003: [1006, 1004],
    1004: [1003, 1002],
    1005: [1025, 1054],
    1006: [1001, 1003],
    1007: [1054, 1010],
    1008: [1010, 1011],
    1009: [1011],
    1010: [1007, 1008],
    1011: [1008, 1009],
    1012: [1023, 1043, 1029],
    1013: [1054, 1017],
    1014: [1017, 1016],
    1015: [1016],
    1016: [1014, 1015],
    1017: [1013, 1014],
    1018: [1054, 1039, 1073, 1023],
    1019: [1054, 1022],
    1020: [1022, 1024],
    1021: [1024],
    1022: [1019, 1020],
    1023: [1018, 1012],
    1024: [1020, 1021],
    1025: [1028, 1005],
    1026: [1029, 1030],
    1027: [1031, 1032],
    1028: [1025, 1036],
    1029: [1012, 1026, 1031],
    1030: [1026, 1032],
    1031: [1029, 1027],
    1032: [1027, 1030, 1033],
    1033: [1032, 1049, 1034],
    1034: [1033, 1035],
    1035: [1034],
    1036: [1028],
    1039: [1018, 1040],
    1040: [1039, 1041],
    1041: [1040, 1042],
    1042: [1041, 1047],
    1043: [1012, 1044],
    1044: [1043, 1045],
    1045: [1044, 1046],
    1046: [1045, 1048],
    1047: [1042],
    1048: [1046],
    1049: [1033, 1050],
    1050: [1049, 1051],
    1051: [1050, 1052],
    1052: [1051, 1053],
    1053: [1052],
    1054: [1005, 1018, 1001, 1013, 1007, 1019, 1076, 1159],
    1073: [1018, 1074],
    1074: [1073, 1075],
    1075: [1074],
    1076: [1054],
    1158: [1159],
    # ★ 2026-10-22：初云峡谷/初云平原/魔晶要塞/圣痕峡谷 原本是一块**孤立**的子图
    #   （1158↔1159↔1176↔1177 只在内部互通，传送使从任何主图都到不了）。
    #   这里在传送枢纽 1054 与 1159 之间加一条双向边，让这 4 张图可达。
    #   原版的地理入口未知（属模拟便利，非真值）。
    1159: [1158, 1176, 1054],
    1176: [1159, 1177],
    1177: [1176],
    # ★ 2026-10-22：灵风塔 1-3 层（1073-1075）与光明之家一层（1076）**接回**传送图。
    #   曾因「客户端 map/ 缺 .map」短暂摘掉，但太息林地(1018) 有位随身 NPC
    #   「灵风塔守护人」，其目的地就是 1073——说明这 4 张图本来就是设计中的室内区域，
    #   只是入口不走传送使。缺文件更可能是 map.fls 兜底或打包差异，不该由服务端拦。
    #   `_goto_map` 里只对缺盘的图打一次警告（见 map_file_exists），不拦。
    1073: [1018, 1074],
    1074: [1073, 1075],
    1075: [1074],
    1076: [1054],
}

# 出口点名 → mapid（用于「走出出口自动传送」）
NAME2MAP = {
    '永泽城': 1001,
    '清溪城': 1002,
    '谷雨镇': 1003,
    '清溪平原': 1004,
    '天水平原': 1005,
    '新月平原': 1006,
    '香叶城': 1007,
    '芳草镇': 1008,
    '古木神庙': 1009,
    '香叶丛林': 1010,
    '绿湾林地': 1011,
    '萤火森林': 1012,
    '天苍城': 1013,
    '云雀谷': 1014,
    '平安港': 1015,
    '荒羽半岛': 1016,
    '天苍高地': 1017,
    '太息林地': 1018,
    '海底神殿': 1019,
    '琉璃港': 1020,
    '星霜城': 1021,
    '落日海湾': 1022,
    '幻境海岸': 1023,
    '流光岛': 1024,
    '清风村2': 1025,
    '黎明镇': 1026,
    '夜幕镇': 1027,
    '长风岛': 1028,
    '疾风谷': 1029,
    '寒月森林': 1030,
    '影狼原': 1031,
    '碎梦岭': 1032,
    '金绿沙洲': 1033,
    '修罗森林': 1034,
    '落雪冰原': 1035,
    '珊瑚迷宫': 1036,
    '灵石塔一层': 1039,
    '灵石塔二层': 1040,
    '灵石塔三层': 1041,
    '灵石塔四层': 1042,
    '古木祭坛一层': 1043,
    '古木祭坛二层': 1044,
    '古木祭坛三层': 1045,
    '古木祭坛四层': 1046,
    '灵石塔五层': 1047,
    '古木祭坛五层': 1048,
    '沙漠迷宫一层': 1049,
    '沙漠迷宫二层': 1050,
    '沙漠迷宫三层': 1051,
    '兔神殿遗址': 1052,
    '兔坦卡门陵墓': 1053,
    '明月镇': 1054,
    '灵风塔一层': 1073,
    '灵风塔二层': 1074,
    '灵风塔三层': 1075,
    '光明之家一层': 1076,
    '初云峡谷': 1158,
    '初云平原': 1159,
    '魔晶要塞': 1176,
    '圣痕峡谷': 1177,
}

# ── 客户端地图数据是否在盘（2026-10-22）─────────────────────────────────
# 客户端的地图**不是** fls 条目，而是 `map/<id>.map` 散文件（与 §散文件回退 同一族路径）。
# 配置里有 4 张图（1073-1075 灵风塔、1076 光明之家）**没有**对应文件——要么由 map.fls
# 兜底、要么真缺。这里只做「有没有盘上文件」的判定，供传送时告警，不参与业务判定。
CLIENT_MAP_DIR = os.environ.get('MX_MAP_DIR', r'D:\mxsj2013\client\map')
_MAP_FILE_CACHE = {}


def map_file_exists(mapid):
    """`map/<id>.map` 是否存在于客户端目录。

    目录不存在（没装客户端 / 换了机器）→ 返回 True（无法判定，宁可不误报）。
    """
    if not os.path.isdir(CLIENT_MAP_DIR):
        return True
    mid = int(mapid)
    if mid not in _MAP_FILE_CACHE:
        _MAP_FILE_CACHE[mid] = os.path.isfile(
            os.path.join(CLIENT_MAP_DIR, '%d.map' % mid))
    return _MAP_FILE_CACHE[mid]

# 每张图的出口（名, 服务端 x, y）——minimap_data.py 真实标记（仅保留已解析目标）
MAP_EXITS = {
    1001: [('明月镇', 47, 12), ('新月平原', 342, 19)],
    1002: [('清溪平原', 5, 141)],
    1003: [('清溪平原', 147, 4), ('新月平原', 151, 111)],
    1004: [('谷雨镇', 12, 113), ('清溪城', 152, 29)],
    1005: [('明月镇', 236, 35)],
    1006: [('永泽城', 44, 139), ('谷雨镇', 95, 5)],
    1007: [('香叶丛林', 13, 200), ('明月镇', 20, 17)],
    1008: [('绿湾林地', 11, 20), ('香叶丛林', 145, 114)],
    1009: [('绿湾林地', 189, 16)],
    1010: [('芳草镇', 9, 24), ('香叶城', 128, 8)],
    1011: [('古木神庙', 61, 144), ('芳草镇', 180, 7)],
    1012: [('幻境海岸', 6, 122), ('古木祭坛一层', 93, 76), ('疾风谷', 162, 145)],
    1013: [('天苍高地', 384, 23), ('明月镇', 390, 175)],
    1014: [('天苍高地', 136, 113), ('荒羽半岛', 145, 7)],
    1015: [('荒羽半岛', 12, 124)],
    1016: [('云雀谷', 12, 111), ('平安港', 145, 10)],
    1017: [('天苍城', 18, 140), ('云雀谷', 33, 12)],
    1018: [('幻境海岸', 12, 133), ('明月镇', 15, 9), ('灵石塔一层', 59, 121), ('灵风塔一层', 72, 2)],
    1019: [('明月镇', 9, 173), ('落日海湾', 14, 9)],
    1020: [('流光岛', 6, 74), ('落日海湾', 134, 114)],
    1021: [('流光岛', 186, 32)],
    1022: [('琉璃港', 20, 8), ('海底神殿', 141, 108)],
    1023: [('萤火森林', 35, 114), ('太息林地', 154, 32)],
    1024: [('星霜城', 15, 115), ('琉璃港', 188, 18)],
    1025: [('天水平原', 16, 114), ('长风岛', 197, 119)],
    1026: [('疾风谷', 6, 5), ('寒月森林', 7, 85)],
    1027: [('影狼原', 110, 4), ('碎梦岭', 114, 79)],
    1028: [('珊瑚迷宫', 153, 99)],
    1029: [('影狼原', 13, 113), ('萤火森林', 21, 3), ('黎明镇', 192, 115)],
    1030: [('碎梦岭', 13, 144), ('黎明镇', 191, 6)],
    1031: [('夜幕镇', 20, 144), ('疾风谷', 194, 8)],
    1032: [('金绿沙洲', 4, 140), ('夜幕镇', 6, 5), ('寒月森林', 194, 16)],
    1033: [('修罗森林', 9, 126), ('沙漠迷宫一层', 148, 77), ('碎梦岭', 230, 5)],
    1034: [('落雪冰原', 8, 131), ('金绿沙洲', 274, 23)],
    1035: [('修罗森林', 230, 6)],
    1036: [('长风岛', 13, 82)],
    1039: [('太息林地', 5, 4), ('灵石塔二层', 5, 46)],
    1040: [('灵石塔三层', 5, 48), ('灵石塔一层', 72, 14)],
    1041: [('灵石塔四层', 8, 11), ('灵石塔二层', 107, 85)],
    1042: [('灵石塔五层', 8, 83), ('灵石塔三层', 21, 6)],
    1043: [('萤火森林', 46, 78), ('古木祭坛二层', 113, 3)],
    1044: [('古木祭坛三层', 6, 86), ('古木祭坛一层', 29, 2)],
    1045: [('古木祭坛四层', 42, 83), ('古木祭坛二层', 115, 13)],
    1046: [('古木祭坛三层', 51, 3), ('古木祭坛五层', 109, 85)],
    1047: [('灵石塔四层', 109, 9)],
    1048: [('古木祭坛四层', 73, 52)],
    1049: [('沙漠迷宫二层', 43, 92), ('金绿沙洲', 154, 10)],
    1050: [('沙漠迷宫三层', 182, 108), ('沙漠迷宫一层', 195, 6)],
    1051: [('沙漠迷宫二层', 3, 25), ('兔神殿遗址', 93, 50)],
    1052: [('兔坦卡门陵墓', 5, 20), ('沙漠迷宫三层', 188, 14)],
    1053: [('兔神殿遗址', 231, 151)],
    1054: [('天水平原', 9, 5), ('太息林地', 260, 95)],
    1073: [('灵风塔二层', 4, 7), ('太息林地', 218, 142)],
    1074: [('灵风塔三层', 4, 80), ('灵风塔一层', 23, 3)],
    1075: [('灵风塔二层', 114, 4)],
    1076: [('明月镇', 4, 20)],
    1158: [('初云平原', 5, 4)],
    1159: [('魔晶要塞', 55, 15), ('初云峡谷', 141, 115)],
    1176: [('圣痕峡谷', 29, 106), ('初云平原', 120, 25)],
    1177: [('魔晶要塞', 140, 2)],
}

# 随身 NPC：pathdata 路线名 → 目的地 mapid（op96 点该 NPC → 目的地菜单）
TRAVEL_NPCS = {
    1001: {
        1200: [1054],
    },
    1005: {
        1200: [1025],
    },
    1007: {
        1200: [1054],
    },
    1013: {
        1200: [1054],
    },
    1018: {
        1200: [1073],
    },
    1019: {
        1200: [1054],
    },
    1025: {
        1200: [1005],
    },
    1051: {
        1200: [1052],
    },
    1054: {
        1200: [1001, 1007, 1013, 1019],
    },
    1159: {
        1200: [1176],
    },
    1176: {
        1200: [1159],
    },
}

# 路线 NPC 名字全集（∪传送使）：op96/op98 按「NPC 真名」判定是否走传送菜单
TRAVEL_NPC_NAMES = ('车夫', '传送使', '船夫', '灵风塔守护人', '牛魔统领', '神羿', '神翎')


EXIT_RADIUS = 4              # 走到出口标记 EXIT_RADIUS 格内即触发传送

# 移动 trace 方向表（对齐客户端 src/nethero.py C2SMapMove 的 `_offset`）：
#   1 右上 / 2 左上 / 3 左下 / 4 右下 / 5 上 / 6 左 / 7 下 / 8 右
MOVE_OFFSETS = ((1, -1), (-1, -1), (-1, 1), (1, 1), (0, -1), (-1, 0), (0, 1), (1, 0))

# ---- NPC ----
NPC_ID = 1000
NPC_NAME = '村长'
# 永泽城(1001)真实 NPC 造型（npcposdata.py：武器店=1013），资源确认存在
NPC_SHAPE = 1013
TELEPORT_NPC_ID = 1001       # 传送使（各图 id 统一 1001，SceneM 会清空对象故可复用）
TELEPORT_NPC_SHAPE = 4427    # npcposdata 真实造型「组队集结人」，资源确认存在
TELEPORT_NPC_NAME = '传送使'
# 战斗技能师（M9）：教招式。点击下发 S2CNpcChatExt，选项 1 下发 S2CNpcLearnList(149)。
TEACHER_NPC_ID = 1002
TEACHER_NPC_NAME = '战斗技能师'
# 可教学招式：只收客户端 skilldata/magicdata 里确实存在的 id（否则面板取名会炸）。
# freeLevel=0 → 客户端按自己的费用表算价（dlglearnskill._SkillScore → LearnCostStd）。
# validLearn 填 255 = 标准费用档；npcLimit=0 = 无门派限制。
LEARN_MAGIC_IDS = (1104, 1105, 1106, 1107, 1108, 1109)
LEARN_MAGIC_MAX_LEVEL = 3

# ---- 帮派（C2S_GROUP 80/81 应答用）----
FAMILY_NAME = '本地测试帮'

# ---- 战斗 ----
# ★ 主角战斗单位 id **必须非 0**（2026-10-17 真机定位）：
#   客户端 `war.Add` 在 WarAdd 时用 `hero.g_Prop.m_Name == obj.m_Name` 命中主角并
#   `war.g_HeroID = obj.m_ID`；而客户端**到处用真值判断** `if war.g_HeroID and ...`
#   （core/warrior.py:1550 右键/ctrl 指挥、warrior.py:1556 右键出手、main.py:976 快捷键、
#   warui.py:2143/2211/2261 指令菜单与自动战斗）。
#   Python 里 `0` 为假 ⇒ 主角 id=0 会让客户端整条「玩家可操作」链路失效：
#   `warui.ShowHeroMenu` 走 `else: world.NormalSelect()` 分支、不武装 `AttackSelect(PhyAttack)`，
#   于是**点击/右键敌人什么都不发**（服务端因此从收不到 C2SWarAttack(160)/Perform(161)），
#   表现为「战斗指令菜单不出现、玩家无法出手」。
#   改用非 0 且不与敌方(1..)/宠物(250)冲突的 id。
HERO_WAR_ID = 100      # 我方（主角）单位 id —— 必须非 0，见上
ENEMY_WAR_ID = 1       # 首个敌方单位 id（后续敌方 id = ENEMY_WAR_ID + i）
ENEMY_COUNT_DEFAULT = 1  # 敌方单位数；★ 2026-10-18 依录像改为 1 只（录像中与「测试+梦想企鹅」交战的
                         # 只有 1 只「野狼」，其余狼是场上明雷怪，不参与本场战斗）；可用 --enemies 调整
# 遇怪间隔默认值（命令行 --encounter-every 优先；可由 world_config.json 的 encounter.every 覆盖）
ENCOUNTER_EVERY_DEFAULT = 5
# ★ 场景遇怪（2026-10-22）：是否按当前地图的「场景表」选怪。
#   True  = 野外/迷宫按真值场景表出怪，城镇/室内（不在表内）不遇怪；
#   False = 回到全图统一 ENEMY_SHAPE/ENEMY_NAME（旧行为，录像复刻用）。
#   可由 world_config.json 的 encounter.per_map 覆盖。
#   真值边界：怪物名单/场景等级是真值（资料全库场景表，叶子猪同表交叉核对），
#   但**每场几只、出现权重、怪物 HP/速度/技能仍是模拟**——原服规则不可得。
ENCOUNTER_PER_MAP = True
# ★ 2026-10-18 依录像改为「野狼」：造型取 monsterdata/petdata 的 2130「狼」——
#   其图鉴描述「带有卡通风格的狼，颜色不像现实中的狼那么灰，而是带有一些青蓝色」，
#   与录像中蓝灰色、白吻白腹的四足狼完全吻合（2210 霜月狼是朱红色、2490 影狼藏身烟雾，
#   2730 灰灰狼偏灰，均不符）。名字按录像中的飘字标签写「野狼」。
#   历史教训：101/2010/1013 缺战斗资源，会让单位渲染异常、战斗流程卡顿。
ENEMY_SHAPE = 2130
ENEMY_NAME = '野狼'
# ★ 100 是**模拟代表值**，不是录像反推出来的固定 HP。
#   2026-10-21 复核 14 场新族野狼局：WAR_DEL 前的累计伤害有 50 / 98 / 146 / 148 四种
#   （多数 50+48+50=148），且分不清 WAR_DEL 是死亡还是逃跑；同造型血量显然随个体/场景变化。
#   取 100 的理由只是它能复现多数「主角 50 → 宠物 48 → 主角 50」的三击序列。
ENEMY_HP = 100

# ── 玩家角色造型（人物素材）──────────────────────────────────────────────
# 客户端 `walker.ChangeShape`（src/walker.py:166）白名单里的 12 个「人物造型」码：
#   111 逍遥侠 / 112 霹雳火 / 121 天仙子 / 122 云无心 / 131 蝶仙子 / 132 艳如风
#   211 凌波燕 / 212 风铃儿 / 221 玉罗刹 / 222 淡清秋 / 231 千幻蝶 / 232 紫嫣然
# 旧的硬编码 shape=1 不是合法人物造型 → 主角在地图/战斗里渲染成错误贴图。
ROLE_SHAPES = (111, 112, 121, 122, 131, 132, 211, 212, 221, 222, 231, 232)
# 建角 C2SLoginPlayerAdd(name, icon, color, major) 的 (icon, major) → 造型码；
# 表取自客户端 `login/createrole.py` 的 `_info`（color 恒 0，不参与判别）。
ROLE_SHAPE_BY_ICON_MAJOR = {
    (1, 0): 111, (3, 0): 112, (1, 1): 121, (3, 1): 122, (1, 2): 131, (3, 2): 132,
    (2, 0): 211, (4, 0): 212, (2, 1): 221, (4, 1): 222, (2, 2): 231, (4, 2): 232,
}
DEFAULT_HERO_SHAPE = 111          # 逍遥侠：无建角造型信息时的回退

# ══════════════════════════════════════════════════════════════════════
# ★★ 重要：默认的 linear 模型**不是本作的真值**，别把它当「复刻了原服」★★
# ══════════════════════════════════════════════════════════════════════
# 两个模型都能精确复现 47 条录像的 50 / 48 / 20，但**证据强度完全不同**：
#
#   reverse —— 反编译 mxmain.dll 得到的原函数形状，**有出处**：
#               Deff = D×(1−P/(30·Lt));  Deff≥A ? 0.1A : 1.3×(A−Deff)
#               （协议资料/报告/_damage_feasible.py 可复算）
#   linear  —— 伤害 = (攻−防)/2，斜率 0.5。**仅**来自一份「外部受控变法实验表」，
#               该表量级（攻击 690/防御 330）远超本作 TBYTE(0..255) 上限，
#               同批资料里的「撕天/威力293/效率28」在本作 petskill/magicdata 中
#               查无此项。**原始出处文件不在本仓库**，无法复核。
#
# ★ 2026-10-20 起默认翻转为 **reverse**：linear 当初当默认的唯一理由是「便利」
#   （加减法直觉、调参直观），那是工程理由；reverse 的公式形状有反编译出处——
#   默认值应该站在有出处的那一边。linear 保留为可选项，但切过去**必须同步换
#   参数点**，否则差 2.6 倍（0.5 vs 1.3 的斜率差）。
#
# 两套参数点（各自都能复现 50/48/20，但含义不同、不可混用）：
#   reverse: hero_at=39  hero_df=15 pet_at=37 enemy_at=31    ← 默认（反编译，有出处）
#   linear : hero_at=100 hero_df=60 pet_at=96 enemy_at=100   ← 可选（便利，来源不可考）
# 约束：m_At/m_Df/m_Dp 同时是「招式可用性」判据（`warui` 要求三者 ≥4，
#       且 ≥ 招式自身 at/df/dp，本作最高 27/25/25）。
# 全部可被 world_config.json 的 battle 段覆盖（hero_at / hero_df / … / dmg_model）。
HERO_AT, HERO_DF, HERO_DP = 39, 15, 4      # reverse 参数点（与 linear 点差 2.6 倍，勿混用）
# PET_AT/PET_DF/PET_DP 现在在上方由真值公式动态计算（PET_FACTOR_IDX=51）
# ENEMY_AT/ENEMY_DF/ENEMY_DP 现在在上方由等级缩放动态计算
HERO_LEVEL, ENEMY_LEVEL = 1, 1        # 宠物等级在 PET_LEVEL 处定义（面板也用它）

# ───────────────────────────── 敌方真值属性动态计算 ─────────────────────────────
# ⚠️ 半真值：资料库 "普通怪物：攻击和防御能力受到等级影响"，具体公式客户端无
#   使用 battle_formulas.py 中的 calc_monster_stats() 进行等级缩放
#   基础值可被 world_config.json 的 battle.enemy_hp/at/df 覆盖
def _enemy_base_stats(level):
    """返回敌方基础属性 {hp, atk, def}"""
    if _HAS_TRUE_FORMULAS:
        return calc_monster_stats(level, base_atk=10, base_def=5, base_hp=50)
    return {'hp': 50 + level * 5, 'atk': 10 + level, 'def': 5 + level}

_ENEMY_BASE = _enemy_base_stats(ENEMY_LEVEL)
ENEMY_AT = _ENEMY_BASE.get('atk', 31)
ENEMY_DF = _ENEMY_BASE.get('def', 0)
ENEMY_DP = 0


def recompute_enemy_constants(level=None):
    """⚠️ 半真值重算：按等级重算敌方基础三围（资料库「攻防受等级影响」，系数为模拟）。

    GM 调试工具入口（gm_set_enemy_level）。会覆盖 world_config 的 enemy_at/enemy_df，
    直到下一次 /api/reload 热加载或重启佐证恢复。ENEMY_HP **不动**——它还挂着
    「100 = 模拟代表值 / 配置可覆盖」的标注，与等级缩放是两回事。
    同步重算三个伤害快照常量（ATTACK_DMG 等），避免「快照与实算不一致」。
    :return: {'level','at','df'}（重算后的值）
    """
    global ENEMY_LEVEL, ENEMY_AT, ENEMY_DF, _ENEMY_BASE
    if level is not None:
        ENEMY_LEVEL = max(1, min(120, int(level)))
    _ENEMY_BASE = _enemy_base_stats(ENEMY_LEVEL)
    ENEMY_AT = int(_ENEMY_BASE.get('atk', ENEMY_AT))
    ENEMY_DF = int(_ENEMY_BASE.get('def', ENEMY_DF))
    recompute_dmg_snapshots()
    return {'level': ENEMY_LEVEL, 'at': ENEMY_AT, 'df': ENEMY_DF}

# ★ 默认 reverse：公式形状有反编译出处（详见上方大段说明）；A/D/P/Lt 的**映射**
#   仍是欠定的模拟参数（§7.5.1）——「有出处」指公式形状，不指参数取值。
#   'reverse'（默认，反编译还原） | 'linear'（可选，加减法，来源不可考）
DMG_MODEL = 'reverse'
DMG_GAIN, DMG_FLOOR, DMG_P_DIV = 1.3, 0.1, 30.0
DMG_MODE, DMG_ROUNDING = 'if', 'trunc'    # 取整由录像唯一判定；if/max 待原作者确认
# 伤害浮动：默认**关闭**（1.0/1.0）。47 条录像同一来源零方差，真要有 ±5%
# 浮动不可能一个 49/51 都不出现；想试外部表的 ±5% 就改成 0.95/1.05。
DMG_RNG_LO, DMG_RNG_HI = 1.0, 1.0


def _default_dmg(atk, df, dp, lv):
    """默认参数下的伤害（只给三个「快照常量」用，供测试/诊断对照）。"""
    return dmgm.physical_damage(atk, df, dp, lv, gain=DMG_GAIN,
                                floor_ratio=DMG_FLOOR, p_div=DMG_P_DIV,
                                mode=DMG_MODE, rounding=DMG_ROUNDING,
                                model=DMG_MODEL)


def recompute_dmg_snapshots():
    """重算三个伤害快照常量（在配置/gm_set_enemy_level 改完参数后调用）。

    ★ 抽出来给 reload_world_config 与 GM 敌方等级重算共用，避免两处各写一份
      「必须重算」的逻辑、日后只改一处漏掉另一处。
    """
    global ATTACK_DMG, ENEMY_ATTACK_DMG, PET_DAMAGE
    ATTACK_DMG = _default_dmg(HERO_AT, ENEMY_DF, HERO_DP, ENEMY_LEVEL)
    ENEMY_ATTACK_DMG = _default_dmg(ENEMY_AT, HERO_DF, ENEMY_DP, HERO_LEVEL)
    PET_DAMAGE = _default_dmg(PET_AT, ENEMY_DF, PET_DP, ENEMY_LEVEL)
    return {'attack_dmg': ATTACK_DMG, 'pet_damage': PET_DAMAGE,
            'enemy_attack_dmg': ENEMY_ATTACK_DMG}

# ---- WState 位域取值（★ 2026-10-15 由客户端源码定死，见 docs/M4_协议规格.md §3.1）----
# ward（受击姿态）驱动 warrior.BeginHit() 的分支：
#   ward=1 HIT → Hit(False) 受击      ward=2 DEFEND → Hit(True) 防御
#   ward=3 EVADE → Evade() 闪避       ward=0 NONE → 落入 `phy and not bprotect` ⇒ 仍走 Evade()
# 故「造成伤害」必须发 ward=1；ward=0 会让客户端演成闪避且不播打击特效。
WARD_NONE, WARD_HIT, WARD_DEFEND, WARD_EVADE = 0, 1, 2, 3
# hurt（伤害表现）：0=普通（war\hit\）1=BS 会心（war\shit\ 带 eff.spr）
HURT_NONE, HURT_BS = 0, 1
# life（生死）：0=正常 1=不省人事 2=死亡 3=删除；只有 ==2 时客户端才走 obj.Die() 播死亡动画
LIFE_NORMAL, LIFE_INSENSIBLE, LIFE_DEAD, LIFE_DEL = 0, 1, 2, 3
# hp_chg（血量变化，4B）：★ **正数 = 扣血**，负数 = 回血。
#   依据 1：官方自带测试台 src/testwar.py 全部伤害样例均为正数
#           （State(11, WARRIOR_WARD_HIT, ..., 330) / 100 / 934）
#   依据 2：src/card_game.py `if hp: cmd.ward=1 else: cmd.ward=3` + `cmd.hp_chg = hp`（伤害为正），
#           而 DoAddHp（回血）用 `cmd.hp_chg = -hp`、ward=0
#   依据 3：src/core/warrior.py:CreateModifyHP —— hp>=0 走 war\hit\（伤害数字），
#           hp<0 走 war\hp\（回血数字）
HP_CHG_DAMAGE_SIGN = 1   # +1：正数表示扣血（本模拟服采用）；-1 可翻转为「负数扣血」

# ---- 属性补帧数值（见 build_prop_power 注释）----
# ★ 不再写死 100：直接取公式参数里的三围，保证「客户端看到的属性」与
#   「服务端算伤害用的属性」是同一组数。world_config 覆盖 HERO_AT/DF/DP 后，
#   build_prop_power 在**调用时**读全局，因此也会跟着变。

# ---- 授予的招式：从《资料全库》每流派挑一个主修低阶招式（均含真实消耗/属性）----
# 让「招式」面板有真实多选项，战斗可试不同流派技能（消耗扣减走 MAGIC_COST）。
# ★ 2026-10-18 依录像补：v_01 的招式列表恰为「流水行云 / 眉来眼去 / 落英缤纷」三式剑法，
#   且面板「等级」显示 1 —— 故补入 1102/1103，等级改 1（与 magic_cost_data 的 slv=1 求值同口径）。
#   其余流派招式保留作可玩性冗余；若要与录像逐行列一致，可只留前三项。
TEACH_MAGIC_IDS = [1101, 1102, 1103, 2101, 1401, 1301, 2401, 2501]   # 剑×3 + 佛/刀/拳/巫/阴阳
TEACH_MAGIC_LEVEL = 1

# ---- M5：敌方回合 ----
# 主角 m_HP/m_HPMax 由 PropInit 下发（见 build_prop_init 的 16/17 项，均为 400）。
HERO_HP = 400
DEFEND_DAMAGE_DIVISOR = 2   # 本模拟服防御姿态的伤害缩减比例（1/2）
# 战斗中召唤宠物的控制点开销（半真值）：
#   ✅ 阈值来自客户端：warui 在召唤前判 `hero.g_Prop.m_At/m_Df/m_Dp < 4` 并弹
#     「控制点不足，无法召唤宠物，是否确定召唤？」—— 4 是客户端真值。
#   ⚠「每个点扣 4」是本服约定：资料库只写「召唤消耗控制点」，没给数量。
#     不足时按客户端的「确定召唤」语义照常召唤、只扣到 0（不硬拒）。
SUMMON_CP_COST = 4
# ★ 敌方招式库（2026-10-18 依 137 场录像重定）：技能**按怪物造型**不同 ——
#   shape 2130（野狼）→ 415「乱舞冲击」（petskill，type=1 主动群体物理）；
#   shape 1（梦想企鹅一类）→ 1103「落英缤纷」（magicdata 人物招式）。
#   两条弹字链路都成立：`core/magicmgr.ShowMagicName` 先查 magicdata（查得到就用它），
#   查不到才回退 `petskill.GetData(pid)`，所以两种来源都能正确弹字。
#   历史：10-16 曾据 v_09（野狼局）把 1103 判为"临时替代"并全局改成 415 —— 那只对野狼成立；
#   6 只企鹅的录像里敌人 17 次施法全部是 1103（证据见报告 §8.2）。
ENEMY_SKILLS = [415]              # 未登记造型的兜底技能库
ENEMY_SKILL_CHANCE = 0.4
# 造型 → 技能库（真值）。同 ENEMY_SPEED_BY_SHAPE：表优先，ENEMY_SKILLS 兜底。
ENEMY_SKILLS_BY_SHAPE = {1: [1103], 2130: [415]}
# 会心（WState hurt=1 → war\shit\ 数字 + eff.spr）概率：录像中宠物受到的 14 为红字，
#   敌方命中我方时按此概率出会心红字，其余为普通橙字（war\hit\）。
ENEMY_HURT_BS_CHANCE = 0.35       # 全局会心率（未按造型登记时的取值）
#
# 按造型登记会心率的钩子。**当前故意留空**：无证据支持任何「某造型必红/必不红」。
#
# 曾经填过 `{1: 0.0, 2130: 1.0}`（企鹅不会心、野狼必红），依据是「137 场录像」统计出的
#   野狼 11/11 全红、企鹅 0/63 不红。那份统计已作废，两个独立原因叠加：
#   1) 拆位错：`_mwr_export.py` 按 LSB 拆（bits&0xF 当 hurt），而 `mxmain.dll:0x41A4D0`
#      实证为 MSB-first（`mask |= 0x80 >> (off+i)`）→ 读出来的「hurt」根本不是 hurt；
#   2) 语料混族：137 份 .mwr 都是本项目 2026-10-15~18 自录的，其中 ≤0118 那 8 个文件
#      是「旧模拟服按 LSB 打包」时期录的（字节恒为 0x40/0x60）。而野狼只出现在新族、
#      企鹅样本主要在旧族 → 族别与造型完全重合，「编码差异」被当成了「怪物差异」。
#   按正确位序 + 只取新族重算：企鹅 0/12、4005 0/13、野狼 1/2 —— 野狼远不是「必红」，
#   样本量也不足以定任何按造型的取值。详见 `协议资料/报告/客户端网络层解析_20261018.md` §5.4。
#
# 要重新启用：补足同一造型 ≥30 次命中的有效样本（须为新族录像），再按实测填入。
# ★ 2026-10-20 重算（198 份录像 / 采用 190 份，已剔除 8 份旧族；
#   口径见 `客户端源码/tools/_mwr_export.py --stats`）：按「攻击者造型」统计
#   有效命中（hp_chg>0）与 WState hurt=1——
#       野狼(2130)  45 命中 / 17 会心 = 37.8%   ← 达到 ≥30 门槛，按实测填入
#       其余造型（1/111/211/4005）在本语料里**只以我方身份**出现；
#       敌方一侧只有 2130 一种造型 → 本表只能登记它一项。
#   ⚠ 强度声明：37.8% 与全局 0.35 在 45 样本下**不可区分**
#     （Wilson 95% CI ≈ [25%, 52%]，0.35 落在区间内）。「是否真按造型」
#     由单一造型语料无法证实。此值记为**实测半真值**，不是原服真值，
#     也不构成「某造型必红 / 必不红」的结论。
ENEMY_HURT_BS_BY_SHAPE = {2130: 0.38}

# ---- 出手顺序（速度）与回合超时（2026-10-16 依客户端录像补） ----
# 客户端（2013 版）不在单位头顶显示顺序序号（那是新版特性），出手顺序体现为
# **服务端下发行动帧的先后**：本服按速度降序结算每回合。
#   快于主角的敌方 → 先动；主角指令 → 按其速度槽结算；慢于主角的敌方 → 后动。
# ★ 行动序的阵营档位（2026-10-20）：同速时按此先后。
#   数值本身无意义，只要保持 主角 < 宠物 < 敌方 的序关系。
#   为什么主角优先：客户端把主角画在最前，同速时若主角后手，玩家会觉得「卡了」。
SIDE_HERO = 0
SIDE_PET = 1
SIDE_ENEMY = 2

HERO_SPEED = 100           # 主角速度
# 2026-10-18 依录像校准：野狼慢于主角(100)与宠物(49) ⇒ 40；企鹅一类小怪快于主角 ⇒ 见下方造型表。
# 造型表优先，BASE 只作未登记造型的兜底。
ENEMY_SPEED_BASE = 40      # 未登记造型的兜底速度（world_config 仍可改此值）
ENEMY_SPEED_STEP = 5       # 多敌时逐个 -5
# ★ 2026-10-18 依 137 场真机录像定：出手速度**随怪物造型不同**，单一基值复现不了
#   （证据见 `协议资料/报告/数值与协议真值审计_20261018.md` §8.1）。
#     · shape 1（梦想企鹅一类人形小怪）—— 6 只时恰好 3 只快于主角(100)：
#       115/110/105 在前，100(=主角同速，主角先手)/95/90 在后，与录像 `EEEH…` 一致；
#     · shape 2130（野狼）—— 慢于主角(100)与宠物(49)，取 40，与 M7 录像
#       「主角 → 宠物 → 野狼」一致。
#   同种怪之间仍按 ENEMY_SPEED_STEP 递减（这是录像里"恰好 3 只在前"的必要条件）。
ENEMY_SPEED_BY_SHAPE = {1: 115, 2130: 40}
ENEMY_SPEED_MIN = 1        # 速度下限：录像最大阵容 6 敌，更大阵容无真值，防止递减到 0/负数
TURN_TIMEOUT = 30          # 回合指令超时（秒），对齐客户端 warui.ShowTimer(30)
                           # 超时行为对齐客户端自动战斗路径 warui.DefaultAttack()：默认普攻
# ITEM_ALL 中 m_HP 的下标（`hero.ITEM_ALL[16]`），用于 S2CPropChange 同步真实血量
PROP_IDX_HP = 16

# ---- 战斗资源（施法消耗）：ITEM_ALL 下标 → 字宽 ----
# 客户端 S2CPropChange(17) 按 `hero.ITEM_ALL[type]` 取属性并 setattr，故任意下标都可运行时更新。
PROP_IDX_MP = 18        # m_MP  TWORD 2B
PROP_IDX_FP = 20        # m_FP  TWORD 2B
PROP_IDX_AT = 72        # m_At  TBYTE 1B
PROP_IDX_DF = 73        # m_Df  TBYTE 1B
PROP_IDX_DP = 74        # m_Dp  TBYTE 1B
# ── 生活技能（工艺技术）与「活力」：制作链的前置状态 ───────────────────────
# ★ 活力与体力是**两个不同属性**，别混：
#     活力 m_Huoli = 下标 24（TWORD 2B）—— 工艺/烹饪/炼药/家具全部消耗它
#     体力 m_Tili  = 下标 26（TWORD 2B）—— 战斗/移动用
#   依据：客户端每张生产面板都跟 m_Huoli 比：
#     dlgcraft.py:80  `if info['cost'] > hero.g_Prop.m_Huoli: color = '#R'`
#     dlgcook.py / dlgdrug.py / dlgfurnish.py 同式
#   下标取自 `src/hero.py:ITEM_ALL`（75 项），_ITEM_ALL_SPEC 里 m_Huoli:2 排第 25 个（下标 24）。
PROP_IDX_HUOLI, PROP_IDX_HUOLI_MAX = 24, 25   # 活力
PROP_IDX_TILI, PROP_IDX_TILI_MAX = 26, 27     # 体力（与活力相邻，勿混用）
LIFE_SKILL_CRAFT = 1117   # skilldata.DATA[1117] = 工艺技术（type=1 生产技能，真值 id）
LIFE_LV_INIT = 5          # 起始工艺等级：够做最低档产物（craftdata 最低 lv=5）
LIFE_LV_MAX = 120         # 与 craftdata 最高 lv 对齐

# ══════════════════════════════════════════════════════════════════════
# 特性实现状态（2026-10-20）
#
# 为什么需要这张表：客户端 UI 有入口、协议有 opcode，但服务端没做业务逻辑的
# 地方不少。不显式标出来，会出现两种误判：
#   · 玩家/测试点了没反应，以为是 bug，其实是从没实现；
#   · 维护者看到 opcode 在字典里，以为已支持，结果调不出效果。
#
# 每条是一个 dict：
#   'status'              取值约定——
#       'implemented'     已实现，有回归覆盖
#       'not_implemented' 协议收得到、但业务逻辑没写（点了无反应，不是 bug）
#       'simulated'       实现了，但规则是模拟参数（无真值来源，见 provenance）
#       'partial'         部分实现（如只处理了一种形态）
#       'preview_only'    **只在预览里报数、实际操作不生效**（2026-10-20 新增）
#                         这类最坑：UI 显示了数字，玩家以为会扣，结果不扣/不加。
#                         比 not_implemented 更隐蔽——后者点了没反应，前者给了假承诺。
#       'ignored_no_counterpart'  **显式接收并忽略**：协议能收、但单机没有对手方
#                         （社交/交易/帮派/排行榜等）。与 not_implemented 的区别：
#                           · not_implemented = 该做、没做（是缺口）
#                           · 这一档 = 单机架构下**本来就不需要**做（不是缺口）
#                         标出来是为了让覆盖页把这类 opcode 标灰，别混进「未实现」
#                         去催人补——它们永远不会实现，补了也没有对手方。
# 状态取值是**封闭集合**，见 FEATURE_STATUS_LEGEND（图例与 HTTP 应答共用一份）。
#
# 用法：查询 API 见 feature_status() / feature_label() / feature_legend()；
#   HTTP：GET /api/features（GM 面板「特性状态」卡片的数据源）。
#   ★ 这张表是**代码事实**，不随会话变，所以不进 gm_state_dict（那是每 1.2s 轮询的
#     运行时快照）——只走 /api/features，开机拉一次即可。
# ══════════════════════════════════════════════════════════════════════
# 状态图例：取值 → 人话。★ 单一来源——GM 面板的卡片、HTTP 应答的 legend 都读这里。
# 以前图例写死在 _GMHandler.do_GET 里一份、文档里一份，改档位要改两处，
# 迟早漂移。放模块级后，新增一档只要动这里。
FEATURE_STATUS_LEGEND = {
    'implemented': '已实现，有回归覆盖',
    'not_implemented': '协议收得到但业务未写（点了无反应，非 bug）',
    'simulated': '已实现，规则为模拟参数（无真值来源）',
    'partial': '部分实现',
    'preview_only': '只在预览报数、实际操作不生效（UI 给了假承诺，最隐蔽）',
    'ignored_no_counterpart': '单机无对手方，显式接收并忽略（非缺口）',
    # 2026-10-22 新增：数值有、协议也在，但**本版客户端没有使用它的入口**。
    # 例子：剧情点（m_StoryPoint）只在 hero 属性表与角色出售面板里被读，
    # 客户端没有「剧情技能」窗口。这类不标 not_implemented（服务端无从实现），
    # 也不标 implemented（玩家没有可用之处）——如实标成「只显示」。
    'display_only': '数值与下发都有，但本版客户端没有消费入口（只显示）',
    # 2026-10-23（P3）新增：**机制该做的都做了，缺的是外部真值**。
    # 与 not_implemented 的区别：后者是「还没写」，改代码能解决；
    # blocked 是「写了也不对，因为缺数据/缺协议证据」，只能等资料。
    # 例：装备数值（itemdata 无属性字段）、修理子指令（无法确认）。
    'blocked': '机制就位但缺外部真值（数据/协议证据不可得，等资料）',
}

# 每项的**人话标题**：GM 面板按这个显示，不显示 'craft_levelup' 这种裸键名。
# 缺项时 feature_label() 回落到键名本身（不会 KeyError，也不会显示 None）。
#
# 为什么标题和状态要分开放：状态是**代码事实**（会变），标题是**固定名称**
# （不随实现进度变）。混在一起的话，把 craft_levelup 从 not_implemented 改成
# implemented 时很容易顺手把标题也改坏。
FEATURE_TITLE = {
    'craft': '制作/合成',
    'craft_levelup': '工艺升级',
    'craft_charge': '制作扣费',
    'huoli_regen': '活力恢复',
    'alchemy': '炼药',
    'battle': '战斗引擎',
    'dmg_model': '物伤公式',
    'enemy_hurt_bs': '会心（按造型）',
    'hero_levelup': '主角升级',
    'hero_progression': '角色最终属性',
    'equip_slot_rule': '装备槽位/等级门',
    'equip_repair': '装备修理/耐久',
    'pet_levelup': '宠物升级',
    'pet_equip_effect': '宠物装备进战斗',
    'pet_skill_learn': '宠物学技能(要诀)',
    'pet_skill_manage': '宠物技能表增删',
    'pet_panel': '宠物面板操作',
    'pet_battle_summon': '宠物战斗召唤/召回',
    'pet_aptitude_adjust': '宠物资质调整',
    'social': '社交/交易/帮派',
    'team_dummy': '队友（假人）',
    'task_chain': '任务闭环',
    'story_point': '剧情点',
    # ── 高级战斗（P3-03）──
    'battle_rng': '战斗随机源（固定种子）',
    'battle_effect': '战斗状态效果',
    'enemy_ai': '敌方行动 AI',
    'war_settle_once': '胜负只结算一次',
    'economy': '经济收口（账本/幂等/原子）',
}
FEATURE_STATUS = {
    # ── 生活技能 / 制作链 ──
    # 制作本身（扣材料 → 产物入包 → op160 通知）已实现。
    'craft': 'implemented',
    # ★ 工艺升级已落地：每次成功制作 +1 熟练度，满本级阈值则升级、溢出累计。
    #   阈值曲线是 SIM（原服升级曲线在服务端、本仓库无数据），但「做→攒→升」这条
    #   **长期成长循环**是真跑通的——craftdata 里 lv>5 的产物现在能做出来。
    #   缺失项：原服是否有「失败率 / 制作品质」这层（客户端 craftdata 无此字段）。
    'craft_levelup': 'simulated',
    # ★ 制作扣费（2026-10-20 修复）：op104 费用查询报「金钱」，_handle_npc_make
    #   现在**同步扣款**——self.coins -= MAKE_COST_MONEY×件数，并发 PropChange(金币)，
    #   预览显示与实际扣款一致，不再有「面板显示 100 金、做完一分不扣」的假承诺。
    #   历史：早先只扣材料+活力、不扣金钱，曾标 preview_only；同日补上结算后改 implemented。
    #   回归：tests/_test_craft.py「制作后金币减少」。
    'craft_charge': 'implemented',
    # 活力恢复：2026-10-22 改造为**基于在线时长的独立机制**（约 30s 回 1 点，
    #   满约 50 分钟）。以前注释误写「每场战斗回满」且代码根本没重置过活力——
    #   那不是原服规则，也从不生效。恢复速率 HUOLI_REGEN_PER_SEC 是 SIM 参数。
    'huoli_regen': 'simulated',
    # 炼药（C2S_PLAYER_DRUG=73）：2026-10-22 核实——客户端**入口存在**
    # （dialog/dlgdrug.py + net.py:57），子指令 0/1/2/3（开界面/参悟/炼制/删配方），
    # 材料形状区间 21200-21299、药品难度表 DRUGDIFF（21100-21118）。
    # 但本版客户端**没有配方数据表**（检索不到 drugdata.py），缺少「材料→药品」的
    # 真映射，没法忠实实现。按用户要求：资料不确定的材料**不自动替换物品 ID**，
    # 所以这里 only 识别、不实现（dispatch 记子指令、回 True、不发 S2CDrug* 帧）。
    # 真做需要：从客户端抽出 drugdata（材料/数量/产物/成功率）再落地。
    'alchemy': 'not_implemented',
    # ── 战斗 ──
    'battle': 'implemented',
    'dmg_model': 'simulated',        # 默认 reverse（公式形状有出处），A/D/P/Lt 映射仍是模拟参数
    # 造型级会心表当前登记 2130:0.38（实测半真值，45 命中/17 会心），未登记造型回落
    # 全局 0.35；规则是否真按造型**未证实**（n=45，与 0.35 不可区分），故仍记 simulated。
    'enemy_hurt_bs': 'simulated',
    # ── 养成 ──
    'hero_levelup': 'implemented',   # 主角升级 / 加点 / 学招式（M9）
    # ★ P3-01（2026-10-23）：最终属性收敛到 progression_service.final_stats 一个入口，
    #   派生值只算不存（重登不可能叠加）；加点联动普攻（力量 //4，加在公式外）。
    #   装备部分**只能做到一半**：槽位/等级门是真值（客户端 EQUIP2POS+ITEMTYPE，
    #   252 条生成表），但**攻防数值不可得**（itemdata 无属性字段、无 equipdata 表）
    #   → 装备对属性贡献恒为 0。故记 partial 而不是 implemented：
    #   "能穿"是真的，"穿上加多少"没有依据，不编。
    'hero_progression': 'partial',
    'equip_slot_rule': 'implemented',  # 槽位/等级门校验（失败零副作用、不扣资源）
    # 修理/耐久：客户端有文案，但物品无耐久字段 + 无法确认 C2S_NPC_MAKE 的修理子指令
    'equip_repair': 'blocked',
    # ★ 2026-10-22：宠物从「一组模块常量」改成实例表，升级链路打通
    #   （战斗经验 → 自动升级 → 资质/属性重算 → 面板 48 重发 → 存档）。
    #   升级门槛用客户端 petfightdata 的 exp_need 公式（真值形状），但
    #   「出战宠物拿几成战斗经验」（PET_EXP_SHARE）与「加点换算」（PET_POINT_EFFECT）
    #   是 SIM —— 所以标 simulated 而不是 implemented。
    'pet_levelup': 'simulated',
    # ★ P3-02（2026-10-23）：宠物属性已分三层且都有出处，合成逻辑收口到
    #   `pet_progression`（面板/战斗/资源三个纯函数 + 加点明细）：
    #   ① 面板层 pet_true_stats = petfightdata 真值公式 × 资质 + 五维加点（加点换算 SIM）；
    #   ② 战斗层 pet_combat_stats = 录像标定值 × 真值相对倍率
    #      （面板档是 0..65535，直接喂 reverse 公式会大两个数量级）；
    #   ③ 资源层 pet_resource_max = 录像 HP/MP × 倍率，FP 恒定。
    #   三层已分别被 _test_pet_instance / _test_pet_skill / _test_p3_pet_growth 守着。
    # 缺口一：召唤兽装备**不参与战斗**——装备数值真值不可得（itemdata 无属性字段）。
    'pet_equip_effect': 'blocked',
    # 缺口二：用「魔兽要诀」学技能。资料写明「和魔兽要诀一起合成可学会**要诀上记载的
    #   技能**，有一定几率冲掉原有技能」，但本版 itemdata 只有单一道具 25405，
    #   **没有记录每本要诀对应哪个技能** → 学会什么是不可知的。
    'pet_skill_learn': 'blocked',
    # ★ P3-02：技能表的**机制**（学/遗忘/替换 + 去重 + 上限 + 绑定 id 归一 +
    #   客户端 404 条技能的 id 白名单校验）已落地，入口在 pet_skill_service，
    #   调用方是 GM（petskill / petforget）与以后的兽诀道具。
    #   与 pet_skill_learn 的区别就是 P3-01 里 equip_slot_rule 与 hero_progression 的区别：
    #   **机制是真的，数据来源不可得**（要诀→技能那条映射仍 blocked）。
    'pet_skill_manage': 'implemented',
    # 缺口三：资质调整/洗练——客户端无对应入口（dlgpet 只有加点与查看），未实现。
    'pet_aptitude_adjust': 'not_implemented',
    'pet_panel': 'implemented',      # 128 放生/129 查看/130 改名/131 出战/132-133 寄存/134 加点
    'pet_battle_summon': 'simulated',  # 166 召唤/召回已实现；召唤的控制点消耗量是 SIM
    # ── 其他 ──
    # ★ 社交/交易/帮派（2026-10-20）：单机没有对手方，协议能收但业务无意义。
    #   实现在 C2S_SINGLEPLAYER_IGNORED（54 条显式忽略表），不是遗漏。
    #   标 ignored_no_counterpart 而不是 not_implemented：后者会被理解成「待办」，
    #   但这批 opcode 永远不会做——没有第二个玩家，好友/摆摊/帮派无从谈起。
    'social': 'ignored_no_counterpart',
    'team_dummy': 'simulated',       # 队友是假人，只做表现不参与行动序
    # 主线剧情：1–6 已落地（接取/推进/放弃/失败重打/复命领奖/重登恢复全通）。
    # 7–22 的一览表与逐条流程原文都在 客户端数据/_docx_text.txt 里，但 7 起出现
    # 「交召唤兽（刃虎宝宝）」「40级召唤兽装备」这类本服还没有的载体 → 未落地。
    'task_chain': 'partial',
    # ★ 剧情点（m_StoryPoint）**有**真实消费入口（2026-10-22 纠正，曾误标 display_only）：
    #   客户端 `dialog/dlglearnskill.py` 对技能 2001–2029 把花费列换成 m_StoryPoint，
    #   上限/每级消耗在 juqingdata.py（max/dist）。所以剧情点用来在 NPC 处
    #   学/升「剧情技能」（skilldata type=3，回执 S2CSkillLevel sktype=3）。
    #   数据见 sim/juqing_skill_data.py（由 gen_juqing_skills.py 从客户端两个表生成）。
    'story_point': 'implemented',
    # ── 高级战斗（P3-03，2026-10-23）──
    # ★ 随机源：战斗内 12 处模块级 random 全部收口到实例 `battle_rng`（固定种子
    #   + 按用途分流）。「同种子可复现」是确定的；但「原服随机怎么实现的」不可得，
    #   所以分几条流是本服工程决定（SIM）。标 implemented 是因为**机制确实在跑**，
    #   不是因为它等于原服。
    'battle_rng': 'implemented',
    # 状态效果：机制（施加/到期/叠加/驱散/系数聚合/DOT）已落地并可 GM 验证；
    #   **数值**只覆盖客户端说明里写明数字的 29 条（284 条真值里），其余 255 条
    #   只有名字与说明、没有战斗效果 → 整体是 partial，不是 implemented。
    #   缺的是「原服状态参数表」（服务端才有，客户端拿不到）。
    'battle_effect': 'partial',
    # 敌方 AI：决策已抽成可解释策略（选目标/放招/会心），默认策略逐位复刻既有行为。
    #   但「原服怪怎么选目标」不可得，两种策略都是本服模拟 → simulated。
    'enemy_ai': 'simulated',
    # 胜负只结算一次：结算计数 + 死亡单位批量推进任务进度（N 次落盘收敛到 1 次）。
    #   多路入口（普攻/招式/宠物/队友/GM/逃跑/战败）都被测试锁住结算次数 ≤1。
    'war_settle_once': 'implemented',
    # ── 经济收口（P3-05，2026-10-23）──
    # 金钱的**唯一写入口**已落地：原先 8 处玩法/GM 代码直接 `self.coins += / -=`，
    #   现在全部走 economy_service（护栏：不许为负 / 多资源原子 / 幂等），
    #   每笔进 economy_ledger 账本（可倒查、可对账）。
    #   制作（活力+金钱）改成交一次扣一次：全成功或全不动，且扣费排在扣材料之前，
    #   避免「钱扣了、材料也吃了、但活力不够白亏」。
    #   顺带补上了两处既有的谎报：交付 NPC 钱不够时日志仍写「已付 N 金」；
    #   GM 改钱不下发 PropChange（客户端数字不刷新）。
    #
    # ★ 为什么标 partial 而不是 implemented：金币这一路是真的收干净了
    #   （有 AST 结构断言守着，任何新增的裸改都会判红）；但
    #     ① 活力/体力只有「制作扣费」走了服务，被动恢复（时间回活力、
    #        战斗后回体力）仍是直接赋值 → 这两个资源不可对账；
    #     ② C2S 帧没有请求序号，**网络级重放防护做不到**，幂等键只用在
    #        有业务主键的一次性事件（交任务）上，商店/制作无法防重发；
    #     ③ 价格/奖励的**数值**仍是模拟（world_config 的 provenance 段标注）。
    'economy': 'partial',
}


def feature_status(key=None):
    """查特性实现状态。不传 key 返回整表副本。

    放在模块级而不是 M4Server 上：这是**代码事实**，不随会话变。
    """
    if key is None:
        return dict(FEATURE_STATUS)
    return FEATURE_STATUS.get(key, 'unknown')

def feature_label(key):
    """特性的中文名（GM 面板显示用）。未登记则回落键名本身。"""
    return FEATURE_TITLE.get(key, key)


def feature_legend():
    """状态取值 → 人话。HTTP 应答与 GM 面板共用这一份。"""
    return dict(FEATURE_STATUS_LEGEND)


# 严重度序（越小越靠前）：**缺口优先**，已实现垫底。
# 为什么这个顺序有意义：面板要回答的是「还有哪些没做完」，不是「做了哪些」。
#   not_implemented / preview_only 是真缺口 → 最前
#   partial                         → 次之
#   simulated / ignored_no_counterpart → 不是缺陷（一个没真值、一个没对手方）
#   implemented                     → 垫底
_FEATURE_SEVERITY = {'not_implemented': 0, 'preview_only': 1,
                     # blocked：机制就位但缺外部真值（装备数值/修理子指令）。
                     # 和 not_implemented 一样是**没做完**，但对玩家/维护者的含义不同：
                     # 改代码没用，只能等资料。排在 partial 之前：它连"部分生效"都没有。
                     'blocked': 2, 'partial': 3,
                     'simulated': 4, 'ignored_no_counterpart': 5,
                     # display_only：不是缺口、也不是「已实现」——数值与下发都有，
                     # 只是本版客户端没有使用它的入口（剧情点在加剧情技能之前就是这类）。
                     # 排在 implemented 之前一档：比 simulated 更接近「做完了」。
                     'display_only': 6,
                     'implemented': 7}

def feature_rows():
    """GM 面板「特性状态」卡片要的成品行：key / title / status / desc / rank。

    为什么在服务端拼好而不是让前端自己拼：
      · 前端不该知道状态取值集合（新增一档就要改两处，必然漂移）；
      · 排序也该由服务端定死（按严重度排，缺口在前），别让前端排序一改就乱。
    """
    rows = []
    for key, st in FEATURE_STATUS.items():
        rows.append({'key': key,
                     'title': feature_label(key),
                     'status': st,
                     'desc': FEATURE_STATUS_LEGEND.get(st, st),
                     # rank = 严重度序号（= 排序键），前端可据此分组/着色，
                     # 不必自己再维护一份状态→颜色的映射表。
                     # ★ 与排序**同一个数**：以前这里算图例下标、排序另用一张 _pri 表，
                     #   两套序号并存，加一档就要改两处（漏一处就排错且不报错）。
                     'rank': _FEATURE_SEVERITY.get(st, len(FEATURE_STATUS_LEGEND))})
    rows.sort(key=lambda r: (r['rank'], r['key']))
    return rows


def op_feature_status(op):
    """查某个 C2S opcode 的**特性状态**（调试台覆盖页标灰用）。

    返回 None 表示「不适用」——该 opcode 不在任何特殊状态表里，按常规处理
    （已实现 / 未实现由运行时证据决定，不由本表决定）。

    目前只覆盖一类：C2S_SINGLEPLAYER_IGNORED（单机无对手方，显式忽略）。
    这类在覆盖页应**标灰**——不是缺口，不该催人补。
    """
    try:
        op = int(op)
    except (TypeError, ValueError):
        return None
    if op in C2S_SINGLEPLAYER_IGNORED_OPS:
        return 'ignored_no_counterpart'
    return None


def op_feature_map():
    """opcode → 特性状态的完整映射（只含有特殊状态的 op）。

    调试台开机拉一次，覆盖页据此把对应行标灰/加注。
    """
    return {op: 'ignored_no_counterpart'
            for op in sorted(C2S_SINGLEPLAYER_IGNORED_OPS)}
LIFE_EXP_INIT, LIFE_EXP_MAX = 0, 100
HERO_TILI_INIT, HERO_TILI_MAX = 100, 100      # ★ 模拟参数：资料表无体力上限
HERO_HUOLI_INIT, HERO_HUOLI_MAX = 100, 100    # ★ 模拟参数：资料表无活力上限
BATTLE_TILI_REGEN = 30    # 每场战斗胜利恢复（★ 模拟参数）
# ── 工艺熟练度 → 技能等级（craft_levelup，2026-10-22 实现）─────────────────
# ★ 真值缺口：客户端 craftdata 只给「产物表」（id/lv/cost/desc），**没有**升级曲线，
#   原版每级要多少熟练度在服务端、本仓库带不进来。下面这套是**占位的 SIM 曲线**，
#   作用是把「做一件 → 熟练度 +1 → 满则升级、溢出累计」这条**循环规则**跑通，
#   让 higher-lv 产物（craftdata 绝大多数 lv>5）真的能做出来；具体每级阈值不是真值。
LIFE_EXP_BASE = 5                 # 升到 lv+1 的熟练度基础值
def life_exp_need(lv):
    """升到 lv+1 需要的熟练度（SIM 曲线，见上方注释）。lv5→6 需 10，每级 +1。"""
    return LIFE_EXP_BASE + int(lv)
# ── 活力恢复（huoli_regen，2026-10-22 改造）────────────────────────────────
# ★ 旧口径（已作废）：注释里写「每场战斗回满」—— 但本服**从来没有**在战斗里重置
#   活力，那行注释是错的（活力只会越做越少、永不恢复）。原服规则是活力**随时间**自然
#   恢复 + 特定行为（休憩/升级）补充，与战斗无关。这里改成**基于真实在线时长的独立恢复
#   机制**，不挂在战斗上，避免把「每战回满」误当成原服规则。
HUOLI_REGEN_PER_SEC = 1.0 / 30.0   # 约 30 秒回 1 点、在线 ~50 分钟回满（★ SIM 速率）
COST_PROPS = (('m_MP', PROP_IDX_MP, 2), ('m_FP', PROP_IDX_FP, 2),
              ('m_At', PROP_IDX_AT, 1), ('m_Df', PROP_IDX_DF, 1), ('m_Dp', PROP_IDX_DP, 1))
# 与 build_prop_init 的初值一致（16-27 段：HP400 MP100 FP100 SP80 火力100 体力100；
# m_At/m_Df/m_Dp 由 build_prop_power 给 100）
HERO_RES_INIT = {'m_MP': 100, 'm_FP': 100,
                 'm_At': HERO_AT, 'm_Df': HERO_DF, 'm_Dp': HERO_DP}


def hero_res_fresh():
    """战斗资源基线。MP/FP 固定，At/Df/Dp 取**当前**全局：

    `world_config` 是在 import 之后才覆盖 HERO_AT/DF/DP 的，若这里直接复制
    import 期算出的 HERO_RES_INIT，改了配置就会出现「客户端属性 39、服务端资源 100」。
    """
    return {'m_MP': HERO_RES_INIT['m_MP'], 'm_FP': HERO_RES_INIT['m_FP'],
            'm_At': HERO_AT, 'm_Df': HERO_DF, 'm_Dp': HERO_DP}
# 招式消耗/属性表：由 sim/gen_magic_cost.py 自动生成
#   · MAGIC_COST 来自 src/data/magicdata.py 的 mana/fp/at/df/dp 表达式（**slv=1 快照**）
#   · MAGIC_EXPR 同一批表达式的**原文**，供逐级求值（magic_cost_at / magic_pow_at）
#   · MAGIC_INFO 来自《梦想世界2013资料全库》招式表（school/kind/genre/pow/抗性…）
# 生成：python tests/sim/gen_magic_cost.py  （依赖 客户端数据/skills_joined.tsv）
# ★ 2026-10-22：不再只吃 1 级快照。客户端菜单的可用性判定本来就是**逐级**算的
#   （warui.GetMagicInfo 现 eval mana/fp/at/df/dp），服务端此前只会念 1 级数字，
#   表现为「招式升到 6 级，消耗一动不动」。逐级口径与 Py2 整除修正见生成器文件头。
try:
    from magic_cost_data import (MAGIC_COST, MAGIC_INFO, MAGIC_EXPR,
                                 magic_cost_at, magic_pow_at, magic_powrate_at,
                                 magic_max_level, py2_expr)
except Exception:                                     # 兜底：保证战斗不崩
    MAGIC_COST = {1101: {'m_MP': 0, 'm_FP': 15, 'm_At': 0, 'm_Df': 4, 'm_Dp': 0}}
    MAGIC_EXPR = {}
    MAGIC_INFO = {1101: {'name': '流水行云', 'school': '剑法', 'kind': '攻击',
                         'genre': '灵巧', 'pow': '50', 'speed': '70', 'mainlv': '10'}}

    def py2_expr(expr):
        return str(expr)

    def magic_cost_at(mid, slv=1, gslv=0, xinfarate=1.0, slvm=None):
        return dict(MAGIC_COST.get(int(mid)) or {})

    def magic_pow_at(mid, slv=1, gslv=0):
        try:
            return int(str((MAGIC_INFO.get(int(mid)) or {}).get('pow', '0')).strip())
        except (TypeError, ValueError):
            return 0

    def magic_powrate_at(mid, slv=1, gslv=0):
        raw = str((MAGIC_INFO.get(int(mid)) or {}).get('eff', '100')).strip()
        try:
            return int(float(raw.rstrip('%')))
        except (TypeError, ValueError):
            return 100

    def magic_max_level(mid):
        return 1


# ═════════════════════ 招式伤害模型（SIM，**不是**原服公式）═════════════════════
#
# 问题：以前 `_magic_damage` 只吃客户端的 `powrate`（效率）一个字段，威力（pow）
#   完全没进战斗。而 magicdata 里 pow 是**逐级表达式**（流水行云 50+(slv-1)*8），
#   是客户端唯一给出的「威力」量纲。
#
# 现模型（sim-pow-eff-anchored-v1）：
#
#     dmg = ⌊ 普攻伤害 × (pow(slv) / pow(1)) × powrate(slv)/100 ⌋
#
#   · pow(slv)     —— 客户端威力表达式逐级求值（真值增长曲线）
#   · powrate(slv) —— 客户端效率表达式逐级求值（百分数）
#   · pow(1)       —— 锚点：把每个招式的威力归一化到**它自己的 1 级值**
#
# 为什么锚在 1 级 pow、而不是搞一个全局 POW_REF：
#   现存 11 条主角放招命中（5 个招式 1101/1301/2101/2401/2501）**全部是 50**，
#   等于同期普攻伤害，5 个不同 pow（50/85/80/70/80）之间零差异。语料对「威力怎么
#   进公式」**零辨识力**。任何把 5 个 pow 映射成 5 个不同伤害的写法都会当场与语料
#   冲突；归一化到 1 级 pow 是唯一同时满足「1 级放招 ≈ 普攻」和「威力要随等级长」
#   的写法：4/5 招式精确复现 50，1101 因效率 94% 落在 47（−3，已知残差）。
#
# ⚠ 强度声明（别把这段读成「复刻了原服」）：
#   · pow 的**绝对**量级没有证据。模型只用它的**相对**增长。
#   · slv>1 的全部行为都是外推 —— 语料里一次都没有非 1 级放招。
#   · 语料本身是 2026-10-15~18 对着**本模拟服**自录的（见
#     `协议资料/报告/客户端网络层解析_20261018.md` §5.2「闭环自证陷阱」），
#     它只能约束「本服曾经打出过什么」，不能证明原服公式。
#   · 群攻不做「目标越多单体越弱」的衰减：没有任何数据支持，硬编就是编造。
MAGIC_DAMAGE_MODEL = 'sim-pow-eff-anchored-v1'
MAGIC_DAMAGE_MAX_MULT = 5.0   # 防御性上限：功率归一 × 效率的乘积不许超 5 倍
# 校准语料：主角放招命中的 hp_chg，结构 {招式id: [每次命中的伤害]}。
# 唯一来源 = `协议资料/报告/录像真值_战斗帧_20261018.csv` 里
# 「op122（WarPerform, a_id=100 主角）→ 其后第一条 op123（WState）的 a_hp_chg」。
# 复核/重生成：`python tests/sim/calibrate_magic_hits.py --check`
#   （打印逐条对照 + 残差，并给出可粘贴的表；--check 时与这张表比对，不一致就 rc=1）
# 全部为 50 = 同期普攻伤害。
MAGIC_CALIBRATION_HITS = {
    1101: [50],
    1301: [50],
    2101: [50, 50, 50],
    2401: [50, 50],
    2501: [50, 50, 50, 50],
}
# 允许的单点残差（点）。1101 的 94% 效率是唯一偏差来源，见模型说明。
MAGIC_CALIBRATION_TOLERANCE = 3


def magic_is_group(pid):
    """招式是否群攻（资料库 kind/genre 任一为「群攻」）。

    kind 与 genre 是两列不同口径：1103 的 kind=攻击、genre=群攻（剑法群攻），
    2101/2501 的 kind 直接写群攻 —— 两列都要看，只看一列会漏。
    """
    info = MAGIC_INFO.get(int(pid)) or {}
    return info.get('kind') == '群攻' or info.get('genre') == '群攻'


def magic_target_limit(pid, slv=1):
    """群攻招式的**目标数**：`slv//6+2`（1~5 级 2 个、6~11 级 3 个…）。

    出处不是猜的：magicdata 里群攻招式的 mana/fp 表达式自带
    `(slv/6+2-1)*10` 这一项 —— 客户端按「每多一个目标 +10 消耗」算钱，
    所以目标数与客户端算消耗用的是同一个式子。非群攻恒为 1。
    """
    if not magic_is_group(pid):
        return 1
    return 2 + max(1, int(slv)) // 6


# ── 世界配置覆盖（可视化编辑器 editor_server.py 写入 world_config.json）──
# 位置约束：必须在**所有可被覆盖的常量定义之后**、任何运行逻辑之前。
# 语义：地图 / NPC / 出口 / 连通 / 战斗调参 以该文件为准；文件不存在或校验不过则
#       整体跳过、保持内置默认值（服务器永远可启动）。
# 逃生舱：`--no-world-config` 强制忽略该文件（回归测试需要确定性时用）。
# ★ 2026-10-19：加载结果必须**无条件**打印（不是只在成功时打印）。
#   旧实现 `if _ok: print(...)` 把「文件在但校验失败」吞成静默——现象是改配置毫无效果、
#   日志里却什么都没有。现在成功/失败/带异常三类都留一行，并记进 WORLD_CONFIG_STATE
#   供启动横幅与 GM 面板复查（MODDING_PLAN 待解清单里的「配置静默失效」）。
WORLD_CONFIG_STATE = {
        'version': 0,               # 第几次加载（每次 +1）
        'updated': '',               # 配置文件里的 updated 字段
    'applied': False,
    'reason': 'skipped',        # applied / missing / invalid / failed / disabled / error
    'message': '',
}
try:
    if '--no-world-config' in sys.argv:
        WORLD_CONFIG_STATE.update(reason='disabled',
                                  message='已按 --no-world-config 忽略 world_config.json')
        print('[world_config] %s' % WORLD_CONFIG_STATE['message'])
    else:
        import world_config as _world_config
        _ok, _msg = _world_config.apply_to(sys.modules[__name__])
        WORLD_CONFIG_STATE.update(
            applied=bool(_ok),
            reason=('applied' if _ok else
                    ('version_stale' if '版本落后' in _msg else
                     ('version_newer' if '版本比程序新' in _msg else
                      ('missing' if '无 world_config.json' in _msg else
                       ('invalid' if '读不出' in _msg else 'failed'))))),
            message=_msg,
            version=getattr(sys.modules[__name__], 'WORLD_CONFIG_VERSION', 0),
            updated=getattr(sys.modules[__name__], 'WORLD_CONFIG_UPDATED', ''))
        print('[world_config] %s%s' % ('✓ ' if _ok else '✗ ', _msg))
except Exception as _e:      # noqa: BLE001 —— 配置层任何异常都不应阻断服务器启动
    WORLD_CONFIG_STATE.update(reason='error', message='配置层异常：%r' % _e)
    print('[world_config] ✗ 配置层异常，已跳过：%r' % _e)

# ★ 剧情 quest-spawn 必须在这里（配置应用**之后**）再并一次：
#   `world_config.apply_to()` 会用 json 里的 60 张地图**整体替换** MAPS，
#   把上面 import 期并进去的剧情 NPC 一起冲掉（表现：主线 4 的护林长老在香叶城
#   点不动）。合并函数对「已存在」是跳过的，所以两次调用都是幂等的。
_MERGED_QUEST_NPCS = _merge_quest_spawns()

# ★ 宠物战斗计算常量（reverse 标定，0..255）——必须在伤害快照**之前**定义，
#   否则 --no-world-config 路径（编辑器还原默认）会在 PET_AT 尚未定义时
#   就执行 PET_DAMAGE = _default_dmg(PET_AT, ...) 抛 NameError。
#   这些值仅用于 _dmg() 伤害公式；面板显示用 *_DISPLAY 另行计算（见下方）。
PET_AT_COMBAT, PET_DF_COMBAT, PET_DP_COMBAT = 37, 15, 4
PET_SPEED_COMBAT = 49
PET_MAGDEF_COMBAT = 36

# 兼容旧代码：PET_AT 等仍指向战斗计算值
PET_AT = PET_AT_COMBAT
PET_DF = PET_DF_COMBAT
PET_DP = PET_DP_COMBAT
PET_SPEED = PET_SPEED_COMBAT
PET_MAGDEF = PET_MAGDEF_COMBAT
PET_DEFENSE = PET_DF_COMBAT

# ★ 三个「伤害快照常量」必须在配置应用**之后**算：早算会被 world_config 覆盖成
#   旧参数点，出现「快照 50、实算 19」的自相矛盾（它们只供旧断言/文档对照）。
# ── GM 面板（--web）启动状态 ────────────────────────────────────────────
# 为什么单独记：面板起不来时旧实现只打一行日志就返回 False，进程照常跑——
# 用户以为面板开了、浏览器打不开、回头看日志又刷过去了。
# 现在把结果记进模块级状态，启动横幅会带出来，GM 面板 /api/features 也能查。
#
# reason 取值：
#   'disabled'  没加 --web，不启动
#   'starting'  正在启动（还没结果）
#   'ready'     启动成功
#   'failed'    启动失败（端口探测/绑定失败，或 httpd 构造异常）
#   'error'     启动过程抛异常（非 OSError）
GM_WEB_STATE = {
    'enabled': False,
    'reason': 'disabled',
    'port': 0,              # 实际监听端口（0 = 没起来）
    'want': 0,              # 请求的端口
    'bumped': False,        # 是否因冲突顺延过
    'message': '',
    # 探测过的端口：[{port, state}]，state ∈ free/busy/overflow。
    # 排查用：光看「顺延到 8081」不知道 8080 是「被占」还是「探测坏了」。
    'probed': [],
}

ATTACK_DMG = _default_dmg(HERO_AT, ENEMY_DF, HERO_DP, ENEMY_LEVEL)
ENEMY_ATTACK_DMG = _default_dmg(ENEMY_AT, HERO_DF, ENEMY_DP, HERO_LEVEL)
PET_DAMAGE = _default_dmg(PET_AT, ENEMY_DF, PET_DP, ENEMY_LEVEL)


# ── 启动自述：把「当前到底在用什么参数」打出来 ──────────────────────────
# 背景（MODDING_PLAN 待解清单两条隐患）：
#   ① 两个模型都能打出 50/48/20，光看伤害数字完全分不出来；默认 reverse 的
#      公式形状有反编译出处，但 A/D/P/Lt 映射仍是欠定模拟参数（§7.5.1）——
#      所以横幅必须显式声明「当前是哪套、参数点是什么、实算多少」；
#      切到 linear（来源不可考）时还要追加否定性标注，拦住「已复刻原服」误读。
#   ② 配置静默失效：文件在、校验不过时旧实现什么都不打印，改配置毫无反应。
# 这里把「模型 + 参数点 + 实际算出来的伤害 + 配置来源」压成一段固定格式，
# 任何人起服第一眼就能判断「我现在跑的到底是哪一套」。
# ★ 这两句会直接出现在启动横幅里，是**唯一**保证「每个起服的人都会看到」的位置。
#   所以措辞要能自解释：不看代码也必须明白 reverse 的公式形状有出处、
#   linear 是来源不可考的便利选项（切过去时横幅会显式否定）。
DMG_MODEL_CAVEAT = {
    'linear': '**非真值**：来源不可考（外部实验表，量级超本作 TBYTE），'
              '仅因加减法直观而保留为可选项；默认模型是 reverse',
    'reverse': '反编译还原（mxmain.dll 基础物伤函数，有出处；47 条录像可精确复现）',
}


def dmg_banner():
    """返回伤害模型自述的多行字符串（供 run() 打日志 / GM 面板查看）。

    刻意**从模块全局现取**而不是捕获 import 期快照：world_config 是 import 之后
    才覆盖的，捕获早值会打出「横幅说 linear、实算是别的」这种骗人的日志。
    """
    caveat = DMG_MODEL_CAVEAT.get(DMG_MODEL, '未知模型')
    lines = ['伤害模型：%s（%s）' % (DMG_MODEL, caveat)]
    if DMG_MODEL == 'linear':
        # 单独再提一行：这句话的作用是**拦住误读**——看到 50/48/20 就以为复刻了原服，
        # 是这个项目最容易被外人下错的结论。宁可横幅长一点。
        # ★ 2026-10-20：linear 已降级为「可选」，措辞从「便利默认」改为「便利选项」。
        lines.append('  ⚠ 当前用的是便利选项 linear（**非默认**），来源不可考，'
                     '**不代表已复刻原服公式**；默认模型是 reverse'
                     '（world_config: battle.dmg_model）')
    if DMG_MODEL == 'reverse':
        lines.append('  参数点：hero A/D/P/Lt=%s/%s/%s/%s  pet=%s/%s/%s/%s  enemy=%s/%s/%s/%s'
                     % (HERO_AT, HERO_DF, HERO_DP, HERO_LEVEL,
                        PET_AT, PET_DF, PET_DP, PET_LEVEL,
                        ENEMY_AT, ENEMY_DF, ENEMY_DP, ENEMY_LEVEL))
        lines.append('  公式常数：gain=%s floor=%s p_div=%s mode=%s rounding=%s'
                     % (DMG_GAIN, DMG_FLOOR, DMG_P_DIV, DMG_MODE, DMG_ROUNDING))
    else:
        lines.append('  参数点（加减法，只用攻防）：hero_at=%s pet_at=%s enemy_at=%s '
                     'hero_df=%s enemy_df=%s'
                     % (HERO_AT, PET_AT, ENEMY_AT, HERO_DF, ENEMY_DF))
    # ★ 必须**现算**，不能读 ATTACK_DMG 等快照常量：那三个是 import 期算的，
    #   一旦 DMG_MODEL / 参数点被 world_config 或热改覆盖，快照就是陈旧值——
    #   横幅会打出「模型说 reverse、伤害却是 linear 的 50」，正是要消除的那种骗人日志。
    now_atk = _default_dmg(HERO_AT, ENEMY_DF, HERO_DP, ENEMY_LEVEL)
    now_pet = _default_dmg(PET_AT, ENEMY_DF, PET_DP, ENEMY_LEVEL)
    now_ene = _default_dmg(ENEMY_AT, HERO_DF, ENEMY_DP, HERO_LEVEL)
    lines.append('  实算伤害：主角普攻 %d / 宠物 %d / 敌方打我 %d（浮动 %s~%s）'
                 % (now_atk, now_pet, now_ene, DMG_RNG_LO, DMG_RNG_HI))
    st = WORLD_CONFIG_STATE
    lines.append('  参数来源：%s' % (st.get('message') or st.get('reason')))
    if not st.get('applied'):
        _r = st.get('reason')
        if _r in ('version_stale', 'version_newer'):
            # ★ 版本不符要**单独醒目告警**（2026-10-20）：
            #   这种情况文件是好的、JSON 也能读，只是结构对不上。
            #   用户最容易误判成「配置生效了但没效果」，所以措辞要说清「已跳过」。
            # message 里已经含「配置版本落后，已跳过」，这里不再重复前缀
            lines.append('  ⚠⚠ %s' % (st.get('message') or _r))
            lines.append('      当前跑的是**内置默认值**，改配置文件不会生效。')
            lines.append('      修复：用 editor_server.py 重新生成，或删掉该文件回到默认。')
        else:
            lines.append('  ⚠ 未应用 world_config.json（reason=%s）——当前是**内置默认值**，'
                         '改配置文件不会生效' % _r)
    # ★ GM 面板状态（2026-10-20）：面板起不来时旧实现只打一行日志就返回 False，
    #   用户以为开了、浏览器打不开、回头日志又刷过去了。这里带进横幅，
    #   启动一眼就能看到「面板到底开没开、在哪个端口、为什么没开」。
    gw = GM_WEB_STATE
    if gw.get('reason') == 'ready':
        lines.append('  GM 面板：已启动 http://127.0.0.1:%d/%s'
                     % (gw.get('port') or 0,
                        '（端口冲突已顺延）' if gw.get('bumped') else ''))
    elif gw.get('reason') in ('failed', 'error'):
        lines.append('  ✗ GM 面板启动失败：%s' % (gw.get('message') or gw.get('reason')))
        lines.append('    （游戏内核不受影响，只是没有 Web 调试面板）')
    elif gw.get('reason') == 'disabled':
        lines.append('  GM 面板：未启用（加 --web 开启）')
    return chr(10).join(lines)

# ---- S2C opcode（NET_FUNCTION_NAME key）----
OP_HELLO = 1
OP_ACCOUNT_INFO = 2
OP_ROLE_INFO = 3
OP_PLAYER_ADD = 6
OP_ACCOUNT_MANAGE = 7
OP_PROP_INIT = 16
OP_PROP_CHANGE = 17
OP_PROP_RESIST = 18
OP_PROP_POWER = 19
OP_BANDWORD = 29
OP_SKILL_LEVEL = 65
OP_TASK_ADD = 72
OP_TASK_REMOVE = 73
OP_BLOCK = 77
OP_MAP_TRACK = 86
OP_MAP_SCENE_M = 96
OP_MAP_GOTO_M = 97
OP_MAP_ADD_NPC = 99
OP_MAP_SUMMON = 105          # netmap.S2CMapSummon：地图上的「召唤跟随」（宠物随行）
OP_WAR_START = 112
OP_WAR_END = 113
OP_WAR_TURN = 114
OP_WAR_ADD = 115
OP_WAR_DEL = 116
OP_WAR_ATTACK_NORMAL = 117
OP_WAR_ATTACK_NORMAL_END = 119
OP_WAR_PERFORM = 122
OP_WAR_WSTATE = 123
OP_WAR_HP_PERCENT = 125
OP_WAR_PROTECT = 121
OP_WAR_SUMMON = 129          # S2CWarSummon：把宠物标记为「可指挥的召唤单位」→ 客户端弹宠物指令菜单
OP_NPC_CHAT_EXT = 152
OP_NPC_LEARN_LIST = 149      # S2CNpcLearnList：教招式清单（M9）

# ---- S2C opcode：打开 UI / 帮派（net.py 路由表）----
# 89: ('netopenui', 'S2COpenUI')  —— C2S_OPENUI(88) 的应答通道
# 84: ('netgroup',  'S2CGroup')   —— C2S_GROUP(81) 的应答通道
OP_OPENUI = 89
OP_GROUP = 84
OP_PRODUCE = 87      # netliving.S2CProduce：C2S_PRODUCE(78) 的应答通道
OP_TIME = 192        # dlgcalendar.S2CTime：C2GS_TIME(61) 的应答通道
OP_MXGW_TIME = 15    # core/mxgw.S2CTime：C2S_TIME(15) 的应答通道（★与 op192 是两条通道，勿混用）

# ---- S2C opcode：物品（net.py 路由 32-38，模块 netitem）----
OP_ITEM_KEY = 32        # netitem.S2CItemKey：id(4) key(2)
OP_ITEM_ADD = 33        # netitem.S2CItemAdd：type(1) id(4) shape(4) flag(2) qty(1) pos(1)
OP_ITEM_DEL = 34        # netitem.S2CItemDel：type(1) id(4)
OP_ITEM_QUANTITY = 35   # netitem.S2CItemQuantity：id(4) qty(1) flash(1)
OP_ITEM_POS = 36        # netitem.S2CItemPos：type(1) id(4) pos(1)
OP_ITEM_ASK_BIND = 37   # netitem.S2CAskBind：id(4)
OP_ITEM_STRING = 38     # netitem.S2CItemString：id(4) infoext(变长)

# ---- S2C opcode：宠物（net.py 路由 48/49/50/51）----
OP_SUMMON_INFO = 48   # netpet.S2CSummonInfo → pet.UpdateInfo（属性面板）
OP_SUMMON_SKILL = 49  # netpet.S2CSummonSkill → 技能格
OP_SUMMON_FIGHT = 50  # netpet.S2CSetFight（出战标记）
OP_PROP_VIEW = 51     # netpet.S2CPropView → 资质 + 9 抗性
OP_SUMMON_KEY = 53    # netpet.S2CSummonKey → pet.SetKey（变异/宝宝/稀有条标）
OP_SUMMON_EQUIP = 54  # netpet.S2CSummEquip → pet.UpdateEquip（召唤兽装备）

# ---- C2S opcode ----
C2S_PING = 8
C2S_GET_TASK = 48
C2S_GIVEUP_TASK = 49      # nettask.C2SGiveUpTask：flag(1)+taskid(2)，放弃进行中任务（真机实测会发）
C2S_MAP_MOVE = 54
C2S_MAP_SYN_DIREMOTE = 55 # 方向/表情同步：idir(1)+iemote(1)（转向/表情时发，单机无对手方）
C2S_DISTRICT = 80         # 地区信息（本服无逻辑）
C2S_DIY_MAGIC = 19        # 自定义招式（本服无逻辑）
C2S_PLAYER_PHOTO = 20     # 玩家照片上传（本服无逻辑）
C2S_PLAYER_IB = 23        # 个人资料（本服无逻辑）
C2S_HERO_SALE = 52        # 摆摊售卖（29 字段，本服无逻辑）
C2S_PLAYER_CHAT = 149     # 玩家聊天（单机服无对手方，忽略）
C2S_PLAYER_WIZARD = 151   # netchat.C2SPlayerWizard：巫师指令串（鼠标导航 'goto scene,x,y'）
C2S_NPC_LOOK = 96
C2S_NPC_ANSWER = 98
C2S_NPC_TASK = 100
C2S_NPC_BUY = 99        # net.C2S_NPC_BUY：body = index(2B) + amount(2B)
C2S_NPC_GETDETAILS = 101
C2S_NPC_SELL = 106       # net.C2S_NPC_SELL：flag(1) + [itemid(4)] 或 [pos(1) amount(2)]
C2S_PROP_POINT = 16      # net.C2S_PROP_POINT：body = 5×1B（体/力/魔/魅/敏 增量）
C2S_PROP_LEVELUP = 17    # net.C2S_PROP_LEVELUP：空包
C2S_NPC_LEARN_SKILL = 103   # net.C2S_NPC_LEARN_SKILL：body = skid(2B)
C2S_NPC_GETCOST = 104    # netnpc.C2SGetMakeCost：flag(1) + ids(N×4B)
C2S_NPC_MAKE = 102       # netnpc.C2SMake*/C2SMake3：mode(1)[+n/type] + ids(N×4B)
C2S_PLAYER_DRUG = 73      # net.C2S_PLAYER_DRUG：炼药；body[0]=子指令(0开界面/1参悟/2炼制/3删)
S2C_NPC_BUYLIST = 147   # netnpc.S2CNpcBuyList（mode2：NPC 购买列表）
S2C_NPC_SELLLIST = 148  # netnpc.S2CNpcSellList（mode1：NPC 收购列表）
S2C_NPC_DETAILS = 151   # netnpc.S2CNpcDetails：pos(1)+desc(变长)，应答 C2S_NPC_GETDETAILS(101)
S2C_NPC_MAKECOST = 155  # netnpc.S2CNpcMakeCost：iTili(2)+iMoney(2)+sklv(2)，应答 C2S_NPC_GETCOST(104)
S2C_MAKE_COMPLETE = 160  # netnpc.S2CMakeCompleteNotify：shape(4)+id(4)+amount(1)+desc(变长)
C2S_WAR_ATTACK = 160
C2S_WAR_PERFORM = 161
C2S_WAR_SPECIAL = 162
C2S_WAR_DEFEND = 163
C2S_WAR_ESCAPE = 164
C2S_WAR_ITEM = 165
C2S_WAR_SUMMON = 166
C2S_WAR_PROTECT = 167
# 召唤兽族指令 168-174（含宠物攻击/招式/防御/道具/保护/逃跑、捕捉 173）
C2S_WAR_CATCH = 173
C2S_WAR_SUMM_CLAN = range(168, 175)
C2S_WAR_SUMM_ATTACK = 168
C2S_WAR_SUMM_PERFORM = 169
C2S_WAR_SUMM_GUARD = 170
C2S_WAR_SUMM_ITEM = 171
C2S_WAR_SUMM_PROTECT = 172
C2S_WAR_SUMM_ESCAPE = 174
C2S_WAR_SUMM_ACTIONS = {
    C2S_WAR_SUMM_ATTACK, C2S_WAR_SUMM_PERFORM, C2S_WAR_SUMM_GUARD,
    C2S_WAR_SUMM_ITEM, C2S_WAR_SUMM_PROTECT, C2S_WAR_SUMM_ESCAPE,
}
C2S_WAR_TEAM = 175
C2S_WAR_LOOK = 176
C2S_WAR_SUMMONCHILD = 177
# 尚未完整模拟的战斗指令；168-174 在宠物等待窗口内单独分派。
C2S_WAR_OTHER = set([C2S_WAR_SPECIAL, C2S_WAR_ITEM, C2S_WAR_SUMMON, C2S_WAR_PROTECT,
                     C2S_WAR_CATCH, C2S_WAR_TEAM, C2S_WAR_LOOK,
                     C2S_WAR_SUMMONCHILD]) | set(C2S_WAR_SUMM_CLAN)
# 会消耗本回合行动权的战斗指令（用于指令窗口判定：收到即关闭倒计时）
BATTLE_CMD_OPS = ({C2S_WAR_ATTACK, C2S_WAR_PERFORM, C2S_WAR_DEFEND, C2S_WAR_ESCAPE}
                  | C2S_WAR_OTHER)

# 客户端会发送、但单机服没有对应业务状态/对手方的指令。
# 显式接收并忽略，避免战斗态下误落到通用回合兜底；这不代表功能已实现。
C2S_SINGLEPLAYER_IGNORED = {
    4: '登录排队：本地服不排队',
    9: '本地密码：账号认证不由游戏服处理',
    10: '举报结果：无举报系统',
    11: '多益通登录：本地服无该认证服务',
    12: '手机绑定：无账号绑定服务',
    18: '聊天属性切换：客户端本地界面状态',
    19: '自创招式：无招式编辑/保存服务',
    20: '玩家照片：无玩家资料服务',
    21: '系统开关：设置由客户端本地管理',
    22: '查看他人：单机无其他玩家',
    23: '玩家账单：无交易账单服务',
    50: '好友：单机无其他玩家',
    51: '角色关闭：角色状态由连接生命周期保存',
    52: '摆摊/角色出售：无交易服务',
    53: '出售记录：无交易记录',
    56: '摆摊：无玩家商店服务',
    57: '添加好友：单机无其他玩家',
    58: '选美投票：无活动服务',
    59: '角色交易：无账号交易服务',
    64: '玩家交易：单机无交易对手',
    65: '玩家商店：无其他玩家摊位',
    66: '屏蔽列表：无玩家社交服务',
    67: '称谓变更：无称谓业务',
    68: '快捷键回写：快捷键由客户端本地管理',
    69: '宠物洗练：无洗练业务',
    70: '房屋：无房屋系统',
    71: '仓库：无仓库系统',
    72: '组队：单机无其他玩家',
    73: '炼药：无配方/炼药业务',
    74: '帮派股票：无帮派运营系统',
    75: '股票操作：无股票系统',
    76: '烹饪：无烹饪业务',
    77: '小游戏关闭：无小游戏服务',
    79: '工厂：无工厂系统',
    80: '街区：无街区服务',
    83: '居民：无居民系统',
    84: '报纸：无报纸服务',
    85: '论坛：无论坛服务',
    86: '长文本聊天：单机无聊天服务',
    87: '客服：无客服服务',
    89: '商城：无商城/支付服务',
    90: '知道问答：无问答服务',
    91: '子女互动：无对应业务状态',
    92: '百科：无百科服务',
    93: '博客：无博客服务',
    94: '拍卖寄售：无交易服务',
    97: 'NPC 指令菜单：未实现对应 NPC 业务',
    105: '向 NPC 交付物品：未实现交付任务业务',
    107: '强行 PK：单机无玩家目标',
    108: 'NPC 右键菜单：未实现对应菜单业务',
    112: '交易中心：无交易中心服务',
    113: 'NPC 对话物品选择：未实现对应交互业务',
    115: 'NPC 对话输入：未实现对应交互业务',
    116: '点卡：无充值服务',
    125: '变造/召唤：无对应玩法服务',
    127: 'NPC 对话数量选择：未实现对应交互业务',
    135: '坐骑：无坐骑业务',
    146: '邮件：无邮件服务',
    147: '子女信息：无子女业务状态',
    148: '私聊：单机无其他玩家',
    150: '玩家公告：无公告服务',
    151: '玩家精灵：无在线引导服务',
    152: '版本上报：无版本运营服务',
    153: '玩家指令：无 GM 指令服务',
    154: '快递/邮寄：无邮件服务',
    155: '找队：单机无其他玩家',
    157: '成就：无成就业务状态',
    158: '成就徽章：无成就业务状态',
    181: '天机交易中心：无交易中心服务',
    182: '工厂操作：无工厂系统',
    55: '方向/表情同步：单机无其他玩家',
    149: '玩家聊天：单机无其他玩家',
}
C2S_SINGLEPLAYER_IGNORED_OPS = set(C2S_SINGLEPLAYER_IGNORED)


# ---- C2S：打开 UI / 帮派（net.py 常量表）----
C2S_OPENUI = 88      # net.C2S_OPENUI：sub(1B)+参数；应答走 S2C op89
C2S_GROUP = 81       # net.C2S_GROUP：帮派/家族指令；应答走 S2C op84
# OPENUI sub（nethero.C2SAddNpc / C2SJingXiu 实测）
OPENUI_SUB_ADDNPC = 109    # C2SAddNpc(minimap 2B) → 应答 sub182 S2CAddMinimapNpc
OPENUI_SUB_JINGXIU = 129   # C2SJingXiu(iopen 1B)  → 应答 sub129 S2CShowBaoziTitle
OPENUI_SUB_PRIVATE = 113   # C2SOpenPrivate()：私密资料面板（dlgsysopt），应答走 op89 或不应答
OPENUI_RESP_ADDNPC = 182   # netopenui.g_Sub2Func[182] = S2CAddMinimapNpc
# GROUP sub（netgroup.C2SOpenGroup / C2SGetFamilyMemberList）
GROUP_SUB_OPEN = 80        # C2SOpenGroup()          → 应答 sub80 S2COpenFamily
GROUP_SUB_MEMBERLIST = 81  # C2SGetFamilyMemberList() → 应答 sub81 S2CFamilyMemberList

# ---- C2S：时间 / 黑名单 / 生产（2026-10-16 客户端实测补齐）----
C2S_BACLLIST = 14          # C2SBlacklist(type,ver)：黑名单同步（本服无逻辑，仅记录）
C2S_TIME = 15              # 服务器时间查询
C2GS_TIME = 61             # 服务器时间查询（GS 变体）
C2S_PRODUCE = 78           # 生产/采集：客户端定时下发（含战斗中）
PRODUCE_SUB_MINERALS = 5   # netliving.C2SMinerals()：矿点查询 → 应答 op87 sub5

# ---- C2S：宠物（net.py 常量表）----
C2S_SUMM_FREE = 128        # 放生
C2S_SUMM_VIEW = 129        # 查看（打开宠物面板且 m_UpdateProp=False 时发）→ 应答 op51
C2S_SUMM_RENAME = 130      # 改名
C2S_SUMM_SET_FIGHT = 131   # 出战 → 应答 op50
C2S_SUMM_PUT = 132         # 存入
C2S_SUMM_TAKE = 133        # 取出
C2S_SUMM_ADDPOINT = 134    # 加点

# ---- C2S：通用通道（netcommon.py）----
C2S_COMMON = 192           # 通用指令：sub=1 点击统计上报 / 2 Lycos / 3 私聊 / 4 置私 / 5 寻路 / 6 取消状态
C2S_COMMON_SUB_SENDLOG = 1

# ---- C2S/S2C：系统设置（dialog/dlgsysopt.py，2026-10-22）----
#
# 这个对话框一次性用三条上行：设置保存(21) / 手机安全(12) / 私密资料(88 sub=113)。
# 全部是「客户端存本地 + 顺手告诉服务器」，服务端不回帧也不会卡 UI
# （dlgsysopt.OnMessage 保存后直接 PROMPT_MSG('保存成功')），所以本服只做
# 「收下、存盘、回读时给出合理值」，不造业务数据。
C2S_SYSSWITCH = 21         # net.C2GS_PLAYER_SYSSWITCH
SYSSWITCH_SUB_SWITCH = 1   # C2SSaveSetting(g_SystemSetting) → switch(4B 位图)
SYSSWITCH_SUB_DATA1 = 2    # C2SSaveSystemData(1, s)：快捷键表
SYSSWITCH_SUB_DATA2 = 3    # C2SSaveSystemData(2, s)
SYSSWITCH_SUB_DATA3 = 4    # C2SSaveSystemData(3, s)
SYSSWITCH_SUB_JB = 5       # C2SCommitJB()：举报/提交，无字段
C2S_DUOYITONG = 11         # net.C2S_DuoYiTong：多益通/私密资料（dlgsysopt 私密页提交）
DUOYITONG_SUB_PRIVATE = 2  # C2SPrivateInfo(info)：'idcard=…||qq=…||mobile=…||name=…'
C2S_MOBILE = 12            # net.C2S_Mobile
MOBILE_SUB_INFO = 1        # C2SRequestMobileInfo()        → 应答 S2C 58 sub=1
MOBILE_SUB_SETTING = 2     # C2SMobileSetting(收/仅好友/时段×4/好友度/通讯费)
MOBILE_SUB_ABOUT_VERIFY = 3  # C2SAboutMobileVerify()
MOBILE_SUB_EXCHANGE = 5    # C2SExchangeBalance()
MOBILE_SUB_GETCODE = 6     # C2SGetMobileVerifyCode(phone 11B)
MOBILE_SUB_VERIFY = 7      # C2SVerifyMoile(phone 11B, code 变长)
S2C_MOBILE = 58            # nethero.S2CMobile：sub(1) + 分派（g_Sub2Call）

# ---- 模拟宠物（造型 4005 = 梦想企鹅：petdata 攻击类型「普通」/携带等级 10，
#      与客户端录像《本地离线》中的宠物一致）----
# ✅ 真值：宠物战斗属性由 petfightdata.py 公式动态计算
#   4005 梦想企鹅 → FactorData idx 51 (野猪, 物理攻击型, lv=1)
PET_ID = 1001
PET_NAME = '梦想企鹅'
PET_SHAPE = 4005
PET_FACTOR_IDX = 51          # FactorData/CorrectData 索引
# ── 宠物目录（2026-10-22 扩展）──
# 名称/流派/攻击类型/携带等级 全部来自 `客户端源码/src/data/petdata.py` 的 DATA（真值）；
# 战斗属性暂**共用**同一套 FactorData（51）——按造型映射 factor 需要
# `petfightdata` 的「流派×攻击类型」推导，本服还没做，属已知模拟项（标 SIM）。
PET_CATALOG = {
    4005: {'name': '梦想企鹅', 'liupai': '鬼', 'attack_type': '普通', 'carry_grade': 10},
    2060: {'name': '小山猪', 'liupai': '妖', 'attack_type': '力敌', 'carry_grade': 5},
    2081: {'name': '青蛙怪', 'liupai': '妖', 'attack_type': '普通', 'carry_grade': 5},
    2110: {'name': '飞贼', 'liupai': '鬼', 'attack_type': '灵巧', 'carry_grade': 15},
}
PET_CATALOG_DEFAULT = PET_SHAPE
# 新角色默认宠物造型（CLI `--pet` 会改这里；存档里的 pet_shape 优先）
PET_SHAPE_DEFAULT = PET_CATALOG_DEFAULT


def pet_def(shape):
    """造型 → 宠物定义（未知造型回落到默认企鹅，绝不返回 None）。"""
    d = PET_CATALOG.get(int(shape or 0))
    if d:
        return d
    return PET_CATALOG[PET_CATALOG_DEFAULT]
PET_WAR_ID = 250             # 宠物作为己方战斗单位时的 war_id（1 字节）
# 队友作为己方战斗单位的 war_id 起始值（1 字节；与主角 100 / 宠物 250 / 敌方 1..9 错开）
TEAM_WAR_ID = 200
TEAM_WAR_SLOTS = 4
TEAM_ALLY_SHAPE = 1
PET_LEVEL = 1

# ───────────────────────────── 宠物真值属性动态计算 ─────────────────────────────
# 资质等级默认按种族基础等级（FactorData['lv']），可扩展为存档存储
def _pet_default_attr_lv():
    """返回默认资质等级字典 {3:hp_lv, 4:at_lv, 5:df_lv, 6:mg_lv, 7:md_lv, 8:sp_lv}"""
    factor = FACTOR_DATA.get(PET_FACTOR_IDX, FACTOR_DATA[51])
    base_lv = factor.get('lv', 1)
    # 6 项资质均从基础等级起步
    return {3: base_lv, 4: base_lv, 5: base_lv, 6: base_lv, 7: base_lv, 8: base_lv}

_PET_ATTR_LV = _pet_default_attr_lv()
_PET_STATS = calc_pet_stats(PET_FACTOR_IDX, PET_LEVEL, _PET_ATTR_LV, tire=0) if _HAS_TRUE_FORMULAS else {}

# ───────────────────────────── 宠物属性：分离「战斗计算值」与「面板显示值」 ─────────────────────────────
# 伤害公式反编译版在 0..255 量级下标定（reverse 参数点）。真值公式给出面板显示值（0..65535）。
# 两者用途不同，不可混用：
#   - *_COMBAT：供 _dmg() 等伤害计算使用，保持 reverse 标定值（兼容录像 50/48/20）
#   - *_DISPLAY：发给客户端显示（pet_base_dict 的 damage/defense/speed 字段），用真值公式
# ★ 战斗计算值已在 world_config.apply_to 之后、伤害快照之前提前定义（见上方），
#   以支持 --no-world-config 路径（编辑器还原默认）。此处仅保留面板显示值与 HP/MP/FP。

# 面板显示值（真值公式，✅ 来自客户端 petfightdata.py；GM petlevel 重算也只动这四项）
PET_AT_DISPLAY = _PET_STATS.get('at', PET_AT_COMBAT)
PET_DF_DISPLAY = _PET_STATS.get('df', PET_DF_COMBAT)
PET_SPEED_DISPLAY = _PET_STATS.get('sp', PET_SPEED_COMBAT)
PET_MAGDEF_DISPLAY = _PET_STATS.get('md', PET_MAGDEF_COMBAT)

# HP/MP/FP 是**录像半真值**（配置 pet_hp/pet_mp/pet_fp 可覆盖；apply_to 在本文件头部
# 已把配置值 setattr 进模块）。⚠ 不能从公式表联动：mg 公式是「法攻」、sp 公式是「速度」，
# 把它们当 MP/_FP 语义就错了（曾误把 PET_MP 写成 289/FP 写成 414，2026-10-21 修正）。
# 只在配置没生效（apply_to 被跳过、属性还不存在）时兜底录像默认值。
PET_HP = getattr(sys.modules[__name__], 'PET_HP', 350)
PET_MP = getattr(sys.modules[__name__], 'PET_MP', 380)
PET_FP = getattr(sys.modules[__name__], 'PET_FP', 30)

# 五维属性（面板显示用，影响招式可用性判定）—— 录像值
PET_STR, PET_CON, PET_MAG, PET_CHA, PET_DEX, PET_POINT = 10, 10, 10, 10, 10, 0


def recompute_pet_display(level=None, factor_idx=None):
    """✅ 真值重算：按 (FactorData idx, 等级) 重算宠物真值属性（at/df/speed/magdef 四项 DISPLAY）。

    GM 调试工具入口（gm_set_pet_level / GM 面板「宠物等级重算」）。
    ★ petfightdata 公式自变量是**资质等级**（attr_lv），不是宠物等级——原版里
      宠物每升一级资质跟着长。这里取「资质等级 = 宠物等级」的联动投影：
      改等级时六项资质等级一齐抬到该值（GM 调试便利，非原服加点明細）。
    约束：
      · 只动 *_DISPLAY 四项、_PET_ATTR_LV 与 _PET_STATS；
      · PET_HP/MP/FP 归录像/配置所有、*_COMBAT 保持 reverse 标定 ——
        改等级**不破坏**录像 50/48/20 的伤害复现；
      · 真值属性表（_PET_STATS 全 6 项）通过 gm_battle_dict 暴露给面板。
    :param level: 新的宠物等级（None=不变；1..120）
    :param factor_idx: FactorData 索引（None=不变；须在 FACTOR_DATA 内）
    :return: 真值属性表 stats（{'hp','at','df','mg','md','sp','tire_rate','tire_max'}）
    """
    global PET_LEVEL, PET_FACTOR_IDX, _PET_ATTR_LV
    if level is not None:
        PET_LEVEL = max(1, min(120, int(level)))
        _PET_ATTR_LV = {i: PET_LEVEL for i in range(3, 9)}   # 资质等级跟随宠物等级
    if factor_idx is not None and factor_idx in FACTOR_DATA:
        PET_FACTOR_IDX = int(factor_idx)
    stats = (calc_pet_stats(PET_FACTOR_IDX, PET_LEVEL, _PET_ATTR_LV, tire=0)
             if _HAS_TRUE_FORMULAS else {})
    if stats:
        g = globals()
        g['PET_AT_DISPLAY'] = stats.get('at', PET_AT_COMBAT)
        g['PET_DF_DISPLAY'] = stats.get('df', PET_DF_COMBAT)
        g['PET_SPEED_DISPLAY'] = stats.get('sp', PET_SPEED_COMBAT)
        g['PET_MAGDEF_DISPLAY'] = stats.get('md', PET_MAGDEF_COMBAT)
        g['_PET_STATS'] = stats
    return stats


# import 期也过一遍：PET_LEVEL 可能已被 world_config（battle.pet_level）覆盖
recompute_pet_display()

PET_EXP, PET_EXPNEXT = 0, 0
PET_LIFE = 8700
# ★ 8700 是录像里的**当前寿命**（m_Life），不是寿命上限。
#   客户端 petzz[4005] 的 lift=15000 是种族寿命基线；两者不是同一字段，别拿 15000 覆盖 8700。

# 资质（ADV_PROP 前 5 项）：攻击/防御/体力/法力/速度 —— 录像半真值（客户端面板显示值）
# ⚠ petzz 只用于客户端资质评级（颜色），不参与战斗公式，不能用它反推战斗属性。
# ★ 真值计算的 ZZ (calc_pet_zz) 与录像面板值不同，录像值为半真值基准
PET_ABILITY = (1797, 1792, 1880, 1828, 1909)
# ✅ 真值 ZZ 评分（供参考/扩展）：_PET_ZZ_TRUE = calc_pet_zz(PET_FACTOR_IDX)

# 抗性：力敌/灵巧/群攻/普通 + 仙/鬼/妖/智/念（有符号）
PET_RESIST = (5, 3, 0, 0, 0, 0, 0, 0, 0)

# 技能：>10000 者视为「绑定技能」（pet.UpdateSkill 会 -10000 并记 bind_idx）
# ✅ 真值：宠物技能取自 petfightdata.py SkillData（含 type/desc）
#   录像 v_23 第 4 格 tooltip「乱舞冲击（主动）群体物理攻击」= petskill 415 (type=1)
#   录像 v_22 第 6 格 tooltip「恐怖（被动）…10% 几率使对方下回合防御」= petskill 505 (type=0)
#   其余按图标语义对号（红焰剑=必杀 / 绿臂=强力 / 蓝奔人=迅捷 / 群像=连击）
PET_SKILLS = [112, 319, 317, 415, 102, 505]
PET_HF_FROM = 6

# ───────────────────── 宠物招式效果（按 petskill.DATA 的 type 分流）─────────────────────
# ★ 2026-10-22：`_pet_perform` 以前对**任何**已配置技能都套用宠物普攻伤害 ——
#   等于「乱舞冲击（群体物理）」和「必杀（被动）」打出来一模一样。现在按客户端数据分流：
#
#   type=0 被动 —— 客户端**不会**把它放进宠物招式菜单
#                  （warui.CSummonMagicDialog 只列 `type == 1 or type == 2`），
#                  所以它被当成主动招式发上来时按「非法指令」处理（回落普攻）。
#   type=1 主动攻击 —— 415 乱舞冲击（desc「群体物理攻击」，真值）。
#   type=2 主动辅助 —— 本服的宠物没有这类技能；按「未实现」显式记日志回落普攻，
#                      不假装有效果（210/309/310 是感知/隐身系，需状态位，无证据）。
PET_SKILL_PASSIVE, PET_SKILL_ATTACK, PET_SKILL_AUX = 0, 1, 2

# 主动招式效果表。只登记**有客户端证据**的效果；没登记 = 未实现（不是「等于普攻」）。
#   pow_rate ：每目标伤害倍率。415 在 petskill 里只有 desc、**没有**威力/效率字段，
#              1.0 是 SIM 值 —— 唯一能守住的口径是「不低于普攻、不凭空加倍」。
#   targets  ：群攻目标数。SIM=2：与主角 1 级群攻同档（slv//6+2 = 2），
#              宠物招式没有等级表达式，无法同源推导。
PET_SKILL_EFFECTS = {
    415: {'name': '乱舞冲击', 'type': PET_SKILL_ATTACK, 'effect': 'attack_group',
          'targets': 2, 'pow_rate': 1.0,
          'desc': '群体物理攻击', 'sim': True,
          'fp_need': '15+LV/3+3*5',     # petskill fpneed 里的消耗部分（Py2 整除）
          'mp_need': None},
}

# 被动效果表：有量化数值的才实现；没数值的显式禁用并写明缺什么。
# 数值出处全部是 petskill.DATA 的 desc（真值）。
PET_PASSIVE_EFFECTS = {
    112: {'name': '必杀', 'effect': 'crit_up', 'crit_add': 0.08, 'enabled': True,
          'evidence': 'petskill 112 desc「必杀几率提高8%」'},
    102: {'name': '连击', 'effect': 'combo', 'chance': 0.40, 'damage_rate': 0.75,
          'enabled': True,
          'evidence': 'petskill 102 desc「40%几率追加一次普通攻击，每次伤害结果降为75%」'},
    317: {'name': '终级迅捷', 'effect': 'speed_up', 'rate': 0.15, 'enabled': False,
          'evidence': 'petskill 317 desc「速度提高15%」',
          'why_disabled': 'PET_SPEED=49 是从录像反解的**表现速度**，宠物若带迅捷，'
                          '这 15% 已经隐含在 49 里；再乘一次是重复计算。'
                          '要用需先采「同学会/去技能」对照场'},
    319: {'name': '终级强力', 'effect': 'atk_up', 'enabled': False,
          'evidence': 'petskill 319 desc「攻击力提高大量」',
          'why_disabled': 'desc 只写「大量」，没有数值，无法标定'},
    505: {'name': '恐怖', 'effect': 'force_defend', 'chance': 0.10, 'enabled': False,
          'evidence': 'petskill 505 desc「10%几率使对方下回合被迫执行防御」',
          'why_disabled': '本服没有「敌方被迫防御」的状态位与表现帧，减伤比例也无数据'},
}


def pet_skill_ids():
    """**默认宠物模板**的技能 id 规范形式（>10000 = 绑定技能，客户端会 -10000）。

    ⚠ 这是新建宠物的模板（PET_SKILLS）；每只宠物真正的技能表在实例的 `skills` 里，
    查某一只要用 `pet_skill_ids_of(p)`。
    """
    return [int(s) - 10000 if int(s) >= 10000 else int(s) for s in PET_SKILLS]


def pet_active_skills():
    """**默认模板**里宠物招式菜单真能点的技能（type 1/2）——与客户端过滤口径一致。"""
    return [sid for sid in pet_skill_ids()
            if (PET_SKILL_EFFECTS.get(sid) or {}).get('type') in
            (PET_SKILL_ATTACK, PET_SKILL_AUX)]
# 宠物「变异」标记：S2CSummonKey(op53) 的 birthkey 字节写 MARK_ISCOLOR(2)
#   wrapinterface.RefreshPetKey → newkey = (m_Key & 65280) >> 8 → 命中 MARK_ISCOLOR 画「变异」@(78,126)
PET_BIRTHKEY = 2
PET_KEY = 0


# ════════════════════ 宠物实例：等级/经验/资质/属性/技能绑到个体 ════════════════════
#
# 背景：在这之前「宠物」是一组**模块级常量**（PET_LEVEL / PET_HP / PET_HP 当前值挂在
#   server.pet_hp、造型挂在 server.pet_shape、技能是固定的 PET_SKILLS），只有一个假想宠物，
#   换造型只是换个 shape —— 等级、经验、资质、技能全都没法分家，也谈不上存档。
#
# 现在：每只宠物一个 dict（`self.pets[pid]`），上面的字段全部挂在个体上；
#   模块级 PET_* 常量降级为「参考宠物（录像标定那只 1 级企鹅）的模板 + 缩放的基准」。
#
# 三层数值的分工（沿用既有的 0..65535 面板层 / 0..255 战斗层约定）：
#   · 真值层 pet_true_stats()  —— petfightdata 公式 + 加点，喂面板（m_HP/m_Damage…）
#   · 战斗层 pet_combat_stats()—— 录像标定值 × 真值**相对倍率**，喂 _dmg()
#   · 资源层 hp/mp/fp          —— 当前值，按实例存，进战斗按实例带入
#
# ★ P3-02：三层的**合成逻辑**已收口到 `pet_progression`（唯一入口，纯函数），
#   这里保留同名别名，既有代码与测试的引用不变。口径（真值/SIM/缺口）见该模块。
PET_SLOT_MAX = ppg.SLOT_MAX      # 携带上限（资料：受人物等级限制；上限值本身是 SIM）
PET_LEVEL_MAX = ppg.LEVEL_MAX
PET_SKILL_MAX = pss.SKILL_MAX    # 技能上限（录像面板可见 6 格；上限取 8 是 SIM）
PET_POINT_PER_LEVEL = ppg.POINT_PER_LEVEL   # 每升 1 级给 5 点自由点（★ SIM）
PET_EXP_SHARE = ppg.EXP_SHARE    # 出战宠物拿战斗经验的比例（★ SIM）
PET_KEY_BOUND = 64          # pet.m_Key 的「已绑定」位（dlgpet 参战确认框读 m_Key & 64）
# ★ P3-02：学技能时是否按客户端真值表（petskill.DATA 404 条）校验 id。
#   True = 表外 id 拒收。理由：学到表外的 id，客户端菜单会显示 `未知<id>`、
#   点了没反应，属于「服务端说有、玩家用不了」的假承诺。
#   做实验可在 pet_learn_skill(..., strict=False) 单次放宽，不要改这个开关。
PET_SKILL_STRICT = True

# 资质下标 → 名称（与 petfightdata.PET_ATTR_IDX 同源：3..8 = hp/at/df/mg/md/sp）
PET_ATTR_IDXS = ppg.ATTR_IDXS
PET_ATTR_NAME = ppg.ATTR_NAME
# 加点字节顺序 **Con 在前**：dlgpet.py 的 _id2attr = {8:0, 23:1, 22:2, 20:3, 19:4}
#   与 PET_POINTS 的 (m_Con, m_Str, m_Mag, m_Cha, m_Dex) 对齐 —— 先 Con 后 Str，
#   按「Str 先」解会把玩家加在体质上的点记成力量（面板立刻对不上）。
PET_POINT_ORDER = ppg.POINT_ORDER
# 五维 → 面板数值的换算表（★ SIM 明细见 pet_progression.POINT_EFFECT）
PET_POINT_EFFECT = ppg.POINT_EFFECT
# 出厂五维（录像面板值）：这 10 点**不算**加点加成，只有超出部分才算。
# 否则「默认宠物 = 录像基准 ×1.0」立刻破（面板会凭空多 +100 血 / +20 攻）。
PET_POINT_BASE = ppg.POINT_BASE

# 造型 → FactorData 资质表下标（★ SIM）
#   客户端 petdata 只给 name/liupai/attack_type/carry_grade，**不提供** FactorData 下标；
#   原服这条映射在服务端、不可得。这里按「名字/流派/攻击类型」对到最接近的种族行：
#     4005 梦想企鹅 → 51 野猪（at=2.4 物理系；录像 48 伤害就是它标定出来的）
#     2060 小山猪   → 51 野猪（同为力敌物理）
#     2081 青蛙怪   → 79 蛤蟆精（同名族，法系 mg=2.4）
#     2110 飞贼     → 63 狐狸仙子（灵巧型 sp=1.7）
#   未登记造型 → 51。**这是模拟映射，不是反解结果。**
PET_FACTOR_BY_SHAPE = {4005: 51, 2060: 51, 2081: 79, 2110: 63}


def pet_factor_idx(shape):
    """造型 → FactorData 下标（未登记回落 51，永不返回 None）。"""
    try:
        return int(PET_FACTOR_BY_SHAPE.get(int(shape), PET_FACTOR_IDX))
    except (TypeError, ValueError):
        return int(PET_FACTOR_IDX)


def pet_factor_idx_of(p):
    """实例的 FactorData 下标：GM 覆盖值优先（p['factor_idx']），否则按造型查表。

    GM 面板的「宠物等级重算」带一个 FactorData 下拉（调试用），那条路径写的就是
    `p['factor_idx']`；正常宠物这里是 0 = 未覆盖。
    """
    try:
        fi = int((p or {}).get('factor_idx') or 0)
    except (TypeError, ValueError):
        fi = 0
    if fi and fi in FACTOR_DATA:
        return fi
    return pet_factor_idx((p or {}).get('shape'))


def _pet_ref_true():
    """参考宠物的真值属性：= **默认新宠**（level 1 / 资质 1 / 五维 10 / 加点 0 / factor 51）。

    为什么拿它当分母：录像里的 350 气血、48 伤害、49 速度全是这只宠物的读数，
    所有缩放都必须让它在 ×1.0 上，否则录像复现立刻破。
    ★ 必须走 `pet_true_stats()`（含五维加点加成）而不是裸 `calc_pet_stats()`：
      默认宠物自带五维 10/10/10/10/10（录像面板值），那部分加成也在分母里，
      否则「同一只宠物」的分子会凭空多出 +100 血/+20 攻，×1.0 立刻不成立。
    """
    if not _HAS_TRUE_FORMULAS:
        return {}
    ref = {'shape': PET_SHAPE_DEFAULT, 'level': 1,
           'attr_lv': {i: 1 for i in PET_ATTR_IDXS},
           'str': PET_STR, 'con': PET_CON, 'mag': PET_MAG,
           'cha': PET_CHA, 'dex': PET_DEX, 'tire': 0}
    return pet_true_stats(ref)


_PET_REF_TRUE = {}      # 真正的值在 pet_true_stats 定义之后算（它依赖五维加成）


def pet_exp_need(factor_idx, attr_lv=None, level=1):
    """升一级所需经验 —— 客户端 `petfightdata.GetPetData` 的 exp_need 公式（真值形状）。

        exp_need[v] = int((lv0**3*2 + lv0**2*(-8) + (lv0-1)*20 + 20)
                          * FactorData[idx][v] + CorrectData[idx][v])     # lv0 = 资质等级+1

    ⚠ 客户端这份公式是**按 6 项资质各算一份**（data['exp_need'][v]），而面板只显示
      一个「升级所需经验」（dlgpet 的 txt_exp 提示串）。这里取 **hp 那一项（v=3）**
      当升级门槛：它是 6 项里量级最小、且语义就是「气血资质的成长成本」。
      「挑哪一项当门槛」是 SIM 解释；公式本身是客户端真值。
    """
    lv0 = int((attr_lv or {}).get(3, max(1, int(level)))) + 1
    idx = int(factor_idx)
    factor = (FACTOR_DATA.get(idx) or FACTOR_DATA.get(PET_FACTOR_IDX) or {})
    correct = (CORRECT_DATA.get(idx) or CORRECT_DATA.get(PET_FACTOR_IDX) or {})
    base = lv0 ** 3 * 2 + lv0 ** 2 * -8 + (lv0 - 1) * 20 + 20
    try:
        return max(1, int(base * float(factor.get('hp', 1.0))
                          + float(correct.get('hp', 0))))
    except (TypeError, ValueError):
        return max(1, int(base))


def pet_true_stats(p):
    """实例的**真值层**属性（面板显示用）：petfightdata 公式 + 五维加点加成。

    ★ P3-02：合成逻辑在 `pet_progression.panel_stats`（唯一入口）。
      这里只负责「取公式结果」（calc_pet_stats 是真值原件，不复制公式），
      加点换算（★ SIM，见 `PET_POINT_EFFECT`）交给服务层 —— 这样「加点贡献
      多少」可以单独取出来给 GM 看，不必再写一份减法。
    """
    if not _HAS_TRUE_FORMULAS or not p:
        return {}
    stats = calc_pet_stats(pet_factor_idx_of(p),
                           max(1, int(p.get('level', 1))),
                           p.get('attr_lv') or {},
                           tire=int(p.get('tire', 0) or 0))
    return ppg.panel_stats(stats, p)


# import 期算一次参考宠物（= 默认新宠，×1.0 锚点）。必须放在 pet_true_stats 之后：
# 它内部要调 pet_true_stats 把五维基线算进去。
_PET_REF_TRUE = _pet_ref_true()

# ⚠ 原来的 `_pet_ratio(p, key)` 已删除：P3-02 把倍率计算收进
#   `pet_progression.ratio(stats, ref, key)` / `combat_ratios` / `resource_ratios`。
#   留着它就是「同一个倍率有两处算法」，正是这次收口要消掉的东西。


def pet_combat_stats(p):
    """实例的**战斗层**数值 = 录像标定值 × 真值相对倍率。

    为什么必须过一层倍率：reverse 物伤模型是在 (PET_AT=37, PET_DF=15, PET_DP=4,
    速度 49) 这组**录像标定值**上拟合的，而真值公式给的是 0..65535 面板档
    （同一只 1 级企鹅 at≈578）。直接把面板值喂进伤害公式会大两个数量级。
    用相对倍率过桥同时保住两件事：
      · 默认企鹅（level 1 / 资质 1 / 加点 0）= ×1.0 → 录像 48 伤害原样复现；
      · 换宠 / 升级 / 加点 → 倍率变化 → 战斗数值真的跟着变。
    ⚠ 破防（dp）没有面板来源，保持录像标定值不变（见 pet_progression.combat_stats）。
    """
    if not p:
        return {'at': PET_AT_COMBAT, 'df': PET_DF_COMBAT, 'dp': PET_DP_COMBAT,
                'speed': PET_SPEED_COMBAT, 'magdef': PET_MAGDEF_COMBAT}
    return ppg.combat_stats(pet_combat_base(), _pet_combat_ratios(p))


def pet_combat_base():
    """战斗层的**录像标定值**（喂给 pet_progression.combat_stats 的底数）。"""
    return {'at': PET_AT_COMBAT, 'df': PET_DF_COMBAT, 'dp': PET_DP_COMBAT,
            'speed': PET_SPEED_COMBAT, 'magdef': PET_MAGDEF_COMBAT}


def _pet_combat_ratios(p):
    return ppg.combat_ratios((p or {}).get('stats') or {}, _PET_REF_TRUE)


def pet_resource_base():
    """资源层的录像基准值（PET_HP/PET_MP/PET_FP，可被 world_config 覆盖）。"""
    return {'hp': PET_HP, 'mp': PET_MP, 'fp': PET_FP}


def pet_resource_max(p):
    """实例的资源上限 {hp, mp, fp}（气血/魔法按真值倍率，力道恒定为录像值）。"""
    if not p:
        return {'hp': max(1, int(PET_HP)), 'mp': max(0, int(PET_MP)),
                'fp': max(0, int(PET_FP))}
    return ppg.resource_max(pet_resource_base(),
                            ppg.resource_ratios(p.get('stats') or {},
                                                _PET_REF_TRUE))


def pet_ability(p):
    """面板「资质」五项（Ability_Attack/Defense/Con/Magic/Speed）。

    录像给的 (1797,1792,1880,1828,1909) 是**默认宠物**的读数；其他宠物/等级按真值
    倍率同比放大 —— ⚠ SIM：这五项在原服是资质评级（不随等级线性变化），这里只保证
    「更强的宠物资质评级更高、默认宠物与录像逐位一致」。
    """
    if not p:
        return PET_ABILITY
    return ppg.ability(PET_ABILITY, p.get('stats') or {}, _PET_REF_TRUE)


def pet_normalize(p):
    """重算实例的派生值并钳制当前资源（等级/资质/加点变动后必须调一次）。

    派生：stats（真值）/ hpmax·mpmax·fpmax / expnext / hp·mp·fp 钳到上限。
    不在这里做的：技能表（由 pet_learn_skill / pet_skill_service 维护）、
    绑定位（由参战流程置位）。

    ★ 幂等：同一个 p 连调 N 次，`stats` / `*max` / `expnext` 逐字段相同
      （P3-02 回归里连调 100 次断言）。这条是「重复结算不叠加」的基础 ——
      派生值只算不存，算几次都一样。
    """
    if not p:
        return p
    p['level'] = max(1, min(PET_LEVEL_MAX, int(p.get('level', 1) or 1)))
    # 资质等级跟随宠物等级（沿用 recompute_pet_display 的既有 SIM 约定），
    # **只增不减**：GM/存档里更高的资质不会被压回去。键统一成 int（存档 JSON
    # 会把键变成 '3'，读档虽已转回，这里再收一次口，避免出现双份键）。
    p['attr_lv'] = ppg.normalize_attr_lv(p['level'], p.get('attr_lv'), PET_ATTR_IDXS)
    attr = p['attr_lv']
    p['stats'] = pet_true_stats(p)
    res = pet_resource_max(p)
    p['hpmax'], p['mpmax'], p['fpmax'] = res['hp'], res['mp'], res['fp']
    p['expnext'] = pet_exp_need(pet_factor_idx_of(p), attr, p['level'])
    for k in ('hp', 'mp', 'fp'):
        cur = p.get(k)
        if cur is None:
            cur = p[k + 'max']
        p[k] = max(0, min(p[k + 'max'], int(cur)))
    return p


def pet_new(pid, shape=None, level=1, name=None, skills=None):
    """建一只宠物实例（默认企鹅 / 1 级 / 全套录像技能）。"""
    shape = int(shape if shape is not None else PET_SHAPE_DEFAULT)
    d = pet_def(shape)
    p = {
        'pid': int(pid), 'name': name or d['name'], 'shape': shape,
        'level': max(1, int(level)), 'exp': 0, 'expnext': 0,
        'attr_lv': {i: max(1, int(level)) for i in PET_ATTR_IDXS},
        'point': 0,
        'str': int(PET_STR), 'con': int(PET_CON), 'mag': int(PET_MAG),
        'cha': int(PET_CHA), 'dex': int(PET_DEX),
        'skills': list(skills if skills is not None else PET_SKILLS),
        'hf_from': int(PET_HF_FROM),
        'factor_idx': 0,          # 0 = 按造型查表；GM 可覆盖（调试用）
        'key': int(PET_KEY), 'birthkey': int(PET_BIRTHKEY),
        'bound': False, 'lookon': False,
        # 召唤兽装备：{pos: {'shape': int, 'desc': str}}，pos ∈ {1 项圈, 2 铠甲, 3 护符}
        # （op54 下行；规则见 PET_EQUIP_ITEMS 上方的注释）
        'equips': {},
        'hp': None, 'mp': None, 'fp': None,
        'life': int(PET_LIFE), 'mo_ling_guo': 0, 'tire': 0,
    }
    pet_normalize(p)
    p['hp'], p['mp'], p['fp'] = p['hpmax'], p['mpmax'], p['fpmax']
    return p


def pet_add_points(p, deltas):
    """加点。`deltas` 是 5 个非负整数，顺序 = PET_POINT_ORDER（Con 在前）。

    返回 (ok, reason)。失败一律**不动任何字段**（客户端会拿 48 重画面板）。
    """
    if not p:
        return False, 'no_pet'
    try:
        deltas = [int(x) for x in deltas[:5]]
    except (TypeError, ValueError):
        return False, 'bad_value'
    if len(deltas) < 5:
        return False, 'bad_length'
    if any(d < 0 for d in deltas):
        return False, 'negative'
    total = sum(deltas)
    if total <= 0:
        return False, 'empty'
    if total > int(p.get('point', 0)):
        return False, 'not_enough'
    for name, d in zip(PET_POINT_ORDER, deltas):
        if d:
            p[name] = int(p.get(name, 0)) + d
    p['point'] = int(p.get('point', 0)) - total
    pet_normalize(p)
    return True, 'ok'


def pet_gain_exp(p, amount):
    """给宠物加经验并按需自动升级。返回 [(旧等级, 新等级), ...]。"""
    if not p or amount <= 0:
        return []
    p['exp'] = int(p.get('exp', 0)) + int(amount)
    ups = []
    guard = 0
    while p['exp'] >= int(p.get('expnext', 1) or 1) and p['level'] < PET_LEVEL_MAX:
        guard += 1
        if guard > PET_LEVEL_MAX:                 # 防御：expnext 异常时不要死循环
            break
        old = p['level']
        p['exp'] -= int(p['expnext'])
        p['level'] = old + 1
        p['point'] = int(p.get('point', 0)) + PET_POINT_PER_LEVEL
        pet_normalize(p)                          # 资质跟随等级 + 属性重算
        ups.append((old, p['level']))
    return ups


def pet_learn_skill(p, sid, strict=None):
    """学一个宠物技能。返回 (ok, reason)。

    ⚠ 客户端**没有**「宠物学技能」的上行协议（netpet 只有放生/查看/改名/出战/存取/加点），
      宠物技能表只能由服务端下发（op49）。所以本函数是本服的权威入口：GM 指令与
      以后的兽诀道具都走它，客户端不会有请求。

    ★ P3-02：判重/上限/白名单全部收口到 `pet_skill_service.learn`。
      `strict` 默认走 `PET_SKILL_STRICT`（True）——学到客户端技能表（404 条真值）
      之外的 id 会被拒（`unknown_skill`），否则客户端菜单显示 `未知<id>` 且点了没反应。
      GM 做实验可显式传 False。
    """
    if not p:
        return False, pss.R_NO_PET
    if strict is None:
        strict = PET_SKILL_STRICT
    skills = p.setdefault('skills', [])
    ok, why, _norm = pss.learn(skills, sid, limit=PET_SKILL_MAX, strict=strict)
    return ok, why


def pet_forget_skill(p, sid):
    """遗忘一个宠物技能。返回 (ok, reason)。

    资料里的「要诀有一定几率冲掉原有技能」——「冲掉」这一步的机制就是它。
    谁会冲掉、几率多少**不可得**（`pet_skill_service.REPLACE_RATE_GAP`），
    所以本函数不会自己触发，只作为服务端权威入口（GM / 以后的兽诀道具）。
    """
    if not p:
        return False, pss.R_NO_PET
    skills = p.get('skills')
    if not isinstance(skills, list):
        return False, pss.R_ABSENT
    ok, why, _norm = pss.forget(skills, sid)
    if ok:
        # `hf_from` 是「从第几格起算护符技能」的下标（S2CSummonSkill 的 x 字段，
        # 客户端算 hfskill = n - x）。删技能后 n 变小，x 若还停在 6 就会 > n，
        # 客户端拿到负数格数 → 面板错位。这里维持 0 <= hf_from <= len(skills)。
        try:
            hf = int(p.get('hf_from') or 0)
        except (TypeError, ValueError):
            hf = 0
        p['hf_from'] = max(0, min(hf, len(skills)))
    return ok, why


def pet_skill_ids_of(p):
    """实例技能表的规范 id（>10000 视为绑定技能，客户端会 -10000）。"""
    return pss.ids_of((p or {}).get('skills'))


class _GmSink(object):
    """把 `M4Server._gm_send` 包成 `sendall` 接口。

    `_pet_frames()` 只认「有 sendall 的连接」，而 GM 面板的推送走 `_gm_send`
    （自带取连接 + 帧批收口）。包一层就能让两条路径复用同一套组帧代码，
    不必为 GM 再抄一遍 48/49/53。
    """

    __slots__ = ('srv',)

    def __init__(self, srv):
        self.srv = srv

    def sendall(self, data):
        return self.srv._gm_send(data)


# ---- 物品（C2S 32-38 ↔ S2C 32-38，netitem）----
C2S_ITEM_GET = 32          # C2SItemGet(id)                 → 补发 S2CItemString
C2S_ITEM_DRAG = 33         # C2SItemDrag(id, source, dest)  → 应答 S2CItemPos
C2S_ITEM_USE = 34          # C2SItemUse(id, 0)              → 扣数量 / 删除 / 生效
C2S_ITEM_STRING = 35       # C2SItemString(id, flag)        → 应答 S2CItemString（悬停触发）
C2S_ITEM_FULL_USE = 36     # C2SItemFullUse(id, full_type)
C2S_ANSWER_BIND = 37       # C2SAnswerBind(id)
C2S_ITEM_LOCK = 38         # C2SItemLock（netplayer.py）

# ---- 详情面板（C2S 144/145/156 ↔ S2C 48/162/190，nethero）----
# ★ 2026-10-19：这三个是 A 档「影响单人可玩」缺口——背包里悬停/右键看物品详情时
#   客户端会发，原服务端不接 → 详情面板拿不到数据。应答帧的线序早已对账通过
#   （op48 S2CSummonInfo / op162 S2CItemInfo / op190 S2CItemTCInfo，见 _extract_wire）。
C2S_GETINFO_SUMMON = 144   # C2SGetSummonInfo(tid,oid)      → 宠物详情 → 应答 op48
C2S_GETINFO_ITEM = 145     # C2SGetItemInfo(tid,oid)        → 物品详情 → 应答 op162
C2S_GETITEMINFOEXT = 156   # C2SGETITEMINFOEXT(itype,id,id1)→ 附加信息 → 应答 op190
OP_ITEM_INFO = 162         # S2CItemInfo：itype(1) key(2) id(4) ext(变长)
OP_ITEM_TCINFO = 190       # S2CItemTCInfo：同构（交易中心/附加信息面板）
S2C_NOTIFY_EXT = 164       # nethero.S2CNotifyExt：itype(1)+shape(4)+msg(变长)，屏幕中部飘窗

# ── 组队（netteam.py）：op72 上行 / op82 下行 ─────────────────────────────
# C2S_PLAYER_TEAM 的帧体 = sub(1B) + 参数；S2C_PLAYER_TEAM 同样是 sub(1B) + body，
# 由 netteam.S2CPlayerTeam 再按 sub 分给 12 个处理器。子命令常量见 team.py。
C2S_PLAYER_TEAM = 72        # net.C2S_PLAYER_TEAM（netteam.py 的 17 个 C2S_* 共用）
OP_PLAYER_TEAM = teammod.OP_S2C_PLAYER_TEAM   # 82
# 单人服里「陪同修炼」的队友（让队伍面板/阵型/成员列表有真实内容）
TEAM_DUMMY_MEMBERS = (
    ('阿豆', 2, 1),         # 名字, 等级, 门派
    ('小青', 3, 2),
    ('石头', 1, 3),
)

# ── C2S：NPC 交互族（netnpc.py，逐个按客户端 PacketAddI 顺序对齐）──────────
# 这几个是单人流程里**真的会点到**的：右键 NPC、给 NPC 东西、NPC 对话中选物品、
# 对话输入框。原先全部落进「未识别」，服务端不应答也不记录。
C2S_NPC_COMMAND = 97        # world.C2SGiveNpc(npcid, gtype)：1(1B) + npcid(4B) + gtype(1B)
                            #   = 点「给予」按钮进入选物态（gtype 2=NPC / 0=玩家）
C2S_NPC_GIVE = 105          # netnpc.C2SNpcGive(itype,targetid,cash,objs)：
                            #   targetid(4) + itype(1) + cash(4) + [ob(4)+amount(1)]*n
                            #   （GIVETYPE_PET=1 时末尾只有 ob(4)，无 amount）
C2S_NPC_RBCLICK = 108       # netnpc.C2SNpcRBClick(id)：id(4B)，右键 NPC 菜单
C2S_NPC_TRADECENTER = 112   # netnpc.C2SNpcTC*：sub(1B) + id(4B)（交易中心买卖）
C2S_NPC_CHOOSE_ITEM = 113   # netnpc.C2SChooseItem(itemid)：itemid(4B)
C2S_NPC_INPUT = 115         # netnpc.C2SNpcInput(dlg,msg)：msg(变长字符串)
C2S_NPC_CHOOSE_ITEM_NUM = 127  # netnpc.C2SChooseItemWithNum(itemid,num)：itemid(4)+num(1)

# NPC_TRADECENTER 的 sub（netnpc.C2SNpcTC* 系列）
NPC_TC_SUB_SELL = 1         # C2SNpcTCSell(id)  → 上架求售
NPC_TC_SUB_BUY = 2          # C2SNpcTCBuy(id)   → 买单
NPC_TC_SUB_CANCEL = 3       # 撤单
NPC_TC_SUB_LIST = 4         # 查询摊位列表

# ── 分派顺序契约：`_dispatch()` 里哪些 opcode 必须排在「战斗保底」之前 ────────
# 为什么单独列一张表：`_dispatch` 是一串顺序敏感的 if/elif，**插错位置不报错**，
# 只表现为「帧序错了」——最典型的是客户端在战斗中也发 OPENUI/COMMON/用道具，
# 这些一旦落到末尾的 `elif self.in_war:` 保底，就会凭空多回一帧 WarTurn，
# 把回合推给敌方。这张表把「谁必须在保底之前」写成数据，
# `tests/_test_dispatch.py` 会逐个在战斗态下跑一遍并断言「没有回 WarTurn」。
# 新增 opcode 时：判断它属于哪一段 → 插进对应段 → 若它属于本表，同步补一条。
DISPATCH_PRE_WAR_OPS = (
    (C2S_MAP_MOVE, '移动（含遇怪触发，战斗态下不应发生但也不该进保底）'),
    (C2S_PROP_LEVELUP, '养成：升级'),
    (C2S_PROP_POINT, '养成：加点'),
    (C2S_NPC_LEARN_SKILL, '养成：学招式'),
    (C2S_NPC_LOOK, 'NPC 点击'),
    (C2S_NPC_ANSWER, 'NPC 对话选项'),
    (C2S_NPC_BUY, 'NPC 购买'),
    (C2S_NPC_SELL, 'NPC 收购'),
    (C2S_NPC_TASK, 'NPC 任务交互（对话任务链/任务应答）'),
    (C2S_NPC_GETDETAILS, 'NPC 物品详情（商店/收购面板悬停）'),
    (C2S_NPC_GETCOST, 'NPC 制作/合成费用查询（→ op155）'),
    (C2S_NPC_MAKE, 'NPC 制作/合成（→ op160 完成通知）'),
    (C2S_PLAYER_DRUG, '炼药（op73，未实现但须识别，不得落战斗保底）'),
    (C2S_GET_TASK, '任务查询'),
    (C2S_OPENUI, '打开 UI（战斗中点按钮同样会发）'),
    (C2S_GROUP, '帮派面板'),
    (C2S_COMMON, '通用通道（战斗中上报点击统计）'),
    (C2S_SYSSWITCH, '系统设置保存（dialog/dlgsysopt）'),
    (C2S_MOBILE, '手机安全设置（dialog/dlgsysopt）'),
    (C2S_DUOYITONG, '私密资料提交（dialog/dlgsysopt 私密页）'),
    (C2S_SUMM_VIEW, '宠物资质面板'),
    (C2S_SUMM_SET_FIGHT, '宠物出战'),
    (C2S_SUMM_FREE, '宠物：放生（本服忽略，但不得落战斗保底）'),
    (C2S_SUMM_RENAME, '宠物：改名（同上）'),
    (C2S_SUMM_PUT, '宠物：存入（同上）'),
    (C2S_SUMM_TAKE, '宠物：取出（同上）'),
    (C2S_SUMM_ADDPOINT, '宠物：加点（同上）'),
    (C2S_ITEM_STRING, '道具附加信息'),
    (C2S_ITEM_GET, '道具详情'),
    (C2S_ITEM_DRAG, '拖动道具'),
    (C2S_ITEM_USE, '使用道具（战斗内吃药）'),
    (C2S_ITEM_FULL_USE, '整包使用道具（战斗内同样可触发）'),
    (C2S_ANSWER_BIND, '道具绑定询问'),
    (C2S_ITEM_LOCK, '道具锁定'),
    (C2S_GETINFO_ITEM, '物品详情（→ S2CItemInfo 162）'),
    (C2S_GETITEMINFOEXT, '物品附加信息（→ S2CItemTCInfo 190）'),
    (C2S_GETINFO_SUMMON, '宠物详情（→ S2CSummonInfo 48）'),
    (C2S_TIME, '时间同步'),
    (C2GS_TIME, '时间同步（C2GS）'),
    (C2S_BACLLIST, '黑名单'),
    (C2S_PRODUCE, '生产'),
    (C2S_GIVEUP_TASK, '放弃任务（进行中→TaskRemove 73）'),
    (C2S_HERO_SALE, '摆摊售卖（无逻辑）'),
    (C2S_MAP_SYN_DIREMOTE, '方向/表情同步（无对手方，真机高频）'),
    (C2S_DISTRICT, '地区信息（无逻辑）'),
    (C2S_DIY_MAGIC, 'DIY 招式（无逻辑）'),
    (C2S_PLAYER_PHOTO, '玩家照片（无逻辑）'),
    (C2S_PLAYER_IB, '个人资料（无逻辑）'),
    (C2S_PLAYER_CHAT, '玩家聊天（无对手方）'),
    (C2S_PLAYER_WIZARD, '鼠标导航（小地图图面 goto scene,x,y）'),
    # 组队（netteam.py）：战斗中同样可能打开/查看队伍面板，不得落保底
    (C2S_PLAYER_TEAM, '组队（建队/离队/阵型/名册 → op82）'),
    # NPC 交互族（netnpc.py）：战斗中也可能点到，必须排在保底之前
    (C2S_NPC_COMMAND, 'NPC：给予入口（选物态）'),
    (C2S_NPC_GIVE, 'NPC：交付物品/宠物/金钱'),
    (C2S_NPC_RBCLICK, 'NPC：右键菜单'),
    (C2S_NPC_TRADECENTER, 'NPC：交易中心买卖'),
    (C2S_NPC_CHOOSE_ITEM, 'NPC 对话：选物品'),
    (C2S_NPC_CHOOSE_ITEM_NUM, 'NPC 对话：选物品数量'),
    (C2S_NPC_INPUT, 'NPC 对话：输入框提交'),
)

# 客户端 ItemAdd/ItemPos 内部会 itype-1 ⇒ 线上 type 为 1-based：
#   1 = ITEM_NORMAL(主背包，含 0-9 装备位/20-49 背包/100-109 战斗道具栏)
#   2 = ITEM_TASK(任务物品)  3 = ITEM_GOODS(材料)  4 = ITEM_BAGS(子背包)
ITEM_WTYPE_NORMAL = 1
ITEM_POS_BAG_START = 20    # item.ITEM_START：背包首格
ITEM_BAG_CAPACITY = 20     # build_prop_init m_BagMax
ITEM_POS_BAG_END = ITEM_POS_BAG_START + ITEM_BAG_CAPACITY
ITEM_POS_FIGHT_START = 100  # item.ITEM_FIGHT_START：战斗道具栏（warui 道具菜单读这一段）

# 模拟背包：(iid, shape, key, qty, pos, infoext)
# 造型取自 data/itemdata.DATA（4972 条）：22100 小麦种子 / 12 鹿茸 / 10411 幻灵珠 /
# 25045 三级鉴定符 / 72132 变身宝珠 / 70942 成长指南
MOCK_ITEMS = [
    (2001, 22100, 0, 5,  ITEM_POS_BAG_START + 0, ''),
    (2002, 12,    0, 10, ITEM_POS_BAG_START + 1, ''),
    (2003, 10411, 0, 3,  ITEM_POS_BAG_START + 2, ''),
    (2004, 25045, 0, 2,  ITEM_POS_BAG_START + 3, ''),
    (2005, 72132, 0, 1,  ITEM_POS_BAG_START + 4, ''),
    (2006, 70942, 0, 1,  ITEM_POS_BAG_START + 5, ''),
    # 战斗道具栏（warui 的「道具」菜单读 ITEM_FIGHT_START..+10）
    (2007, 12,    0, 5,  ITEM_POS_FIGHT_START + 0, ''),
    (2008, 10411, 0, 3,  ITEM_POS_FIGHT_START + 1, ''),
    # 2026-10-22 扩充：药品（itemdata 真名）+ 帮派物资（真名）
    (2009, 21101, 0, 3,  ITEM_POS_BAG_START + 6, ''),
    (2010, 22312, 0, 5,  ITEM_POS_BAG_START + 7, ''),
]
# 使用后生效的物品（shape → 恢复 HP 值）；本服仅实现回血类
# 21101 小还丹 / 21103 铁血归元丹 / 21106 九转还魂丹 的**数值**是模拟值（资料只说
# 「按品质恢复气血」，没有具体量），名字与用途来自 itemdata.py 真值。
ITEM_HEAL = {12: 160, 10411: 300, 70942: 0,
             21101: 200, 21103: 350, 21106: 600}

# ───────────────────────── 召唤兽装备（真值：资料库 §22302-22360，官方攻略 docx）─────────
# 三大类各 4 档（等级 20/40/60/80）。客户端落位来自 dlgpetitem.DrawPetEquip 的
#   pos2obj = {1:'equip1', 2:'equip2', 3:'equip3'} —— pos 1/2/3 即 项圈/铠甲/护符。
# 规则（docx 原文，逐条可引）：
#   · 「当召唤兽等级达到20级时，即可使用所有召唤兽装备」→ PET_EQUIP_LEVEL_MIN=20；
#   · 「一旦装备上，就不能取下，只能用新装备替换旧装备，同时旧装备也会消失」；
#   · 佩带入口：召唤兽界面→选宠→「物品」→右键装备 → netitem.C2SItemSummonUse
#     （= C2S_ITEM_USE(34) 的 9 字节形态：item(4)+summon(4)+flag(1)）。
#     **没有**独立的装备 opcode（net.py C2S 表全量核对过）。
# ⚠ shape id **不可得**：客户端 data/itemdata.py 的 ITEMTYPE 无召唤兽装备类别，
#   docx 装备表只有 名称/等级/效果 三列。故本表初始为空——机制、协议、规则、
#   持久化全部就绪；等权威 shape 源出现时按 {shape: (pos, name, level)} 补数据
#   即可驱动 C2S 路径（回归用 gm_pet_equip / 临时注入，不依赖本表）。
# ⚠ 数值真值同样不可得：docx 只给定性效果（项圈「提升攻击、灵力或防御」、
#   铠甲「提升防御」、护符「附加1～2种召唤兽技能」），没有任何数值。
#   **因此本实现不把装备接入战斗公式**——猜数值比不做更糟（硬性约束：
#   不得猜测装备属性真值，须以权威数据源为准）。
PET_EQUIP_POS = {'项圈': 1, '铠甲': 2, '护符': 3}
PET_EQUIP_POS_NAME = {1: '项圈', 2: '铠甲', 3: '护符'}
PET_EQUIP_LEVEL_MIN = 20
# {shape: (pos, name, level)} —— pos ∈ {1,2,3}；shape 权威源待补（见上方⚠）
PET_EQUIP_ITEMS = {}

# ── NPC 商店（村长处可购买）：沿用 MOCK_ITEMS 的真实造型，附模拟价格 ──
COIN_INIT = 50000
# (shape, 名称, 单价, 单次限购数)
SHOP_ITEMS = [
    (22100, '小麦种子', 100, 1),
    (12,    '鹿茸',     200, 1),
    (10411, '幻灵珠',   500, 1),
    (25045, '三级鉴定符', 800, 1),
    (72132, '变身宝珠', 1500, 1),
    (70942, '成长锦囊', 1200, 1),
    # ── 2026-10-22 扩充（名字/用途来自 itemdata.py；价格是模拟值）──
    (21101, '小还丹',     300, 5),   # 特效药：恢复气血
    (21103, '铁血归元丹', 600, 3),
    (21106, '九转还魂丹', 1500, 1),
    (21212, '枫柳皮',     120, 10),  # 药材
    (22101, '白菜种子',   100, 5),
    (22102, '亚麻种子',   120, 5),
    (22212, '铜矿石',     260, 3),
    (22214, '铁矿石',     300, 3),
    (22306, '亚麻布',     420, 3),   # 帮派物资
    (22312, '茶叶',       200, 5),
]

# ── NPC 收购价（模拟参数，非真值）──────────────────────────────────────
# 真值不可得：客户端 `src/data/itemdata.py` 只有 (名字, 描述)，**没有任何价格字段**，
# 原版收购价在服务端。本服规则：上架商品按售价的 1/4 收，未上架统一 1 金，
# 目的是让「卖」这条链路可走通，而不是复刻经济系统。
SELL_RATIO_NUM, SELL_RATIO_DEN = 1, 4
SELL_PRICE_MIN = 1
SELL_PRICE_FALLBACK = 1
SHOP_PRICE = {shape: price for (shape, _n, price, _q) in SHOP_ITEMS}
ITEM_NAMES = {shape: name for (shape, name, _p, _q) in SHOP_ITEMS}


def item_name_of(shape):
    """商品名（仅本服上架表内的；未知造型退回 shape，避免弹出空名）。"""
    return ITEM_NAMES.get(shape) or ('物品%d' % shape)


def sell_price_of(shape):
    """某造型的收购单价（见上方说明：模拟参数）。"""
    base = SHOP_PRICE.get(shape)
    if base is None:
        return SELL_PRICE_FALLBACK
    return max(SELL_PRICE_MIN, base * SELL_RATIO_NUM // SELL_RATIO_DEN)


def make_s2c(opcode, data=b'', compress=None):
    """S2C 帧：[1B N][payload]，N 含 payload。

    明文模式 payload = [opcode 1B][data]，N = len(payload)。
    加密模式（`_CRYPTO_MODE=True`，原版 DLL）下 payload 先经 LZSS 压缩
    （`lzss.compress([opcode][data])`），N = 压缩后长度 —— 客户端收包路径
    `FUN_00430fe0` 在 +0x72=1 时会先 `CLZWDecompress` 再取 opcode。

    `compress` 显式覆盖：握手 Hello 帧在客户端 SetSeed 前读取，必须
    `compress=False`（否则 _CRYPTO_MODE=True 时会被压缩）。
    """
    do_compress = _CRYPTO_MODE if compress is None else bool(compress)
    payload = bytes([opcode]) + data
    if do_compress:
        payload = lzss.compress(payload)
    if len(payload) >= 0xFF:
        return b'\xFF' + struct.pack('<H', len(payload)) + payload
    return bytes([len(payload)]) + payload


def var_gbk(s):
    """变长字符串（UnpackString(0)）：GBK 字节 + \\0 结尾（C 字符串，无长度前缀）。
    ★实测修正（2026-10-15 客户端截图）：此前误加 [1B len] 前缀——长度字节被渲染成
    名字前的「口」（如「口野猪」），且无 \\0 时客户端越界读吞掉后续帧，战斗卡「请等待」。"""
    return s.encode('gbk', 'replace') + b'\x00'


def _mask_private_info(txt):
    """私密资料脱敏：`idcard=…||qq=…||mobile=…` 里的值只留首尾各 1 位。

    只在**日志**里用；存档存原文（客户端下次打开面板要回填，服务端不展示）。
    """
    out = []
    for part in (txt or '').split('||'):
        if '=' not in part:
            out.append(part)
            continue
        k, _, v = part.partition('=')
        if len(v) <= 2:
            out.append('%s=%s' % (k, '*' * len(v)))
        else:
            out.append('%s=%s%s%s' % (k, v[0], '*' * (len(v) - 2), v[-1]))
    return '||'.join(out)


def recv_exact(sock, n, timeout):
    buf = b''
    sock.settimeout(timeout)
    while len(buf) < n:
        ch = sock.recv(n - len(buf))
        if not ch:
            raise ConnectionError('连接断开')
        buf += ch
    return buf


def recv_c2s(conn, timeout=30):
    """C2S 帧：[opcode 1B][1B 长度 N][body N]，N==0xFF 扩展 0xFF+2B u16。"""
    op = recv_exact(conn, 1, timeout)[0]
    ln = recv_exact(conn, 1, timeout)[0]
    if ln == 0xFF:
        ln = struct.unpack('<H', recv_exact(conn, 2, timeout))[0]
    body = recv_exact(conn, ln, timeout)
    return op, body


def hello_response(server_seed):
    """Hello 25B 回应种子混淆（C0=0x100000000）。"""
    obf = ((server_seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


# ───────────────────────────────────────── 进世界（复用 m2）─────────────────
def build_login_account_info(count=0, vid=1):
    now = int(time.time()) & 0xFFFFFFFF
    return make_s2c(OP_ACCOUNT_INFO, struct.pack('<B', count) + struct.pack('<I', now) + struct.pack('<B', vid))


def build_prop_init(hero_id, name, shape=DEFAULT_HERO_SHAPE):
    """S2CPropInit（op16）：id 4B + 67 项属性（220B），最小合法默认值。

    ★ `shape` = 人物造型码（须属 `ROLE_SHAPES`；建角选定后由 `self.hero_shape` 传入）。
      旧实现写死 1 —— 那不是合法人物造型，客户端会按 `hero/0001` 取贴图，主角渲染异常。
    """
    p = bytearray()
    p += struct.pack('<B', 0)                                   # 0  m_Icon
    p += struct.pack('<H', shape)                               # 1  m_Shape（人物造型码）
    p += b'\x00'                                                # 2  m_Desc (n=0)
    # m_Sex：1=男 / 2=女（客户端 createrole 以 `idx/100 == 1` 区分男/女图标框）
    p += struct.pack('<B', 1 if shape < 200 else 2)             # 3  m_Sex
    p += name.encode('gbk', 'replace')[:16].ljust(20, b'\x00')  # 4  m_Name
    p += struct.pack('<B', 1)                                   # 5  m_Grade
    p += struct.pack('<II', 0, 0)                               # 6  m_Cash (TLONG 8B)
    p += struct.pack('<I', 0)                                   # 7  m_Saving
    p += struct.pack('<I', 0)                                   # 8  m_Exp
    p += struct.pack('<I', 1000)                                # 9  m_NextExp
    p += struct.pack('<I', 1000)                                # 10 m_NeedExp
    p += struct.pack('<B', 1)                                   # 11 m_Class
    p += struct.pack('<B', 1)                                   # 12 m_Country
    p += b'\x00' * 20                                           # 13 sOrg
    p += struct.pack('<B', 0)                                   # 14 m_EmoteForTitle
    p += b'\x00' * 22                                           # 15 m_Title
    p += struct.pack('<HHHHHHHHHHHH',                           # 16-27 12×TWORD
                     400, 400, 100, 100, 100, 100, 80, 80, 100, 100, 100, 100)
    p += struct.pack('<HHHHHHHHHH', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)  # 28-37 10×TWORD
    p += struct.pack('<I', 0)                                   # 38 m_Switch
    p += struct.pack('<B', 0)                                   # 39 m_GridPoint
    p += struct.pack('<I', 0)                                   # 40 m_SkillExp
    p += struct.pack('<I', 0)                                   # 41 m_PFPoint
    p += struct.pack('<H', 0)                                   # 42 m_StoryPoint
    p += struct.pack('<I', 1000)                                # 43 m_Gold
    p += struct.pack('<I', 0)                                   # 44 m_UI
    p += struct.pack('<I', 0)                                   # 45 m_DoubleExp
    p += struct.pack('<I', 0)                                   # 46 m_BuChangExp
    p += struct.pack('<I', 0)                                   # 47 m_OrgOffer
    p += struct.pack('<I', 0)                                   # 48 m_OrgOffer_Max
    p += struct.pack('<I', 0)                                   # 49 m_OrgCoin
    p += struct.pack('<I', 0)                                   # 50 m_CookPoint
    p += struct.pack('<I', 0)                                   # 51 m_FightCash
    p += struct.pack('<I', 0)                                   # 52 m_ShenWuBi
    p += struct.pack('<B', 6)                                   # 53 m_SummonMax
    p += struct.pack('<B', 20)                                  # 54 m_BagMax
    p += struct.pack('<B', 0)                                   # 55 m_VIP
    p += struct.pack('<B', 0)                                   # 56 m_FlyFlag
    p += struct.pack('<I', 0)                                   # 57 m_Child
    p += struct.pack('<B', 0)                                   # 58 m_GradeLimit
    p += struct.pack('<B', 0)                                   # 59 m_Force
    p += struct.pack('<B', 0)                                   # 60 m_TitleColor
    p += struct.pack('<H', shape)                               # 61 m_RealShape
    p += struct.pack('<B', 0)                                   # 62 m_XiulianTotalNeed
    p += struct.pack('<I', 0)                                   # 63 m_PhotoEffect
    p += struct.pack('<I', 0)                                   # 64 m_IsGuestLogin
    p += struct.pack('<H', 0)                                   # 65 m_Birthday
    p += struct.pack('<H', 0)                                   # 66 m_AutoUpgrade
    assert len(p) == 220, len(p)
    return make_s2c(OP_PROP_INIT, struct.pack('<I', hero_id) + bytes(p))


def build_scene_m(mapid=None, name=None, minimap=None, x=None, y=None):
    """S2CMapSceneM（op96）：scene(2) mapid(2) minimapid(2) x(2) y(2) + 场景名(变长)。

    客户端 `netmap.S2CMapSceneM` → `map.Load(scene, mapid, minimapid, args, scenename)`
    并 `world.ClearAllObject()`，故**发本帧后必须补 AddNpc/GotoM**（见 M4Server._send_scene）。
    """
    if mapid is None:
        mapid = MAP_ID
    d = MAPS.get(mapid, MAPS[START_MAP_ID])
    return make_s2c(OP_MAP_SCENE_M,
                    struct.pack('<HHHHH', SCENE_ID, mapid,
                                d['minimap'] if minimap is None else minimap,
                                d['spawn'][0] if x is None else x,
                                d['spawn'][1] if y is None else y)
                    + var_gbk(d['name'] if name is None else name))


def build_goto_m(x=None, y=None):
    x = SPAWN_X if x is None else x
    y = SPAWN_Y if y is None else y
    return make_s2c(OP_MAP_GOTO_M, struct.pack('<HH', x, y))


def build_map_track(obj_id, x, y, dx=0, dy=0, move_type=0):
    """S2CMapTrack（op86）：id(4) x(2) y(2) dx(1有符号) dy(1有符号) move_type(1)。

    ★ 2026-10-16 真机修复（角色回弹）：客户端 `netmap.S2CMapTrack` 语义是
      「从 (x,y) 走到 (x+dx, y+dy)」——dx/dy 为**有符号移动增量**（UnpackSignedChar）。
      旧实现恒发 dx=dy=0 → 角色每次被令走回原地 → 回弹。须回「起点 + 增量」。
    """
    def _sb(v):
        v = max(-128, min(127, int(v)))
        return struct.pack('<b', v)
    return make_s2c(OP_MAP_TRACK,
                    struct.pack('<IHH', obj_id, x, y) + _sb(dx) + _sb(dy)
                    + struct.pack('<B', move_type))


def build_add_npc(npc_id, shape, x, y, name):
    """S2CMapAddNpc（op99）：id(4) shape(2) x(2) y(2) block(1) desc(空) direct(1)
    npctype(2) country(1) addonId(2) title_len(1) name(变长)。"""
    p = bytearray()
    p += struct.pack('<I', npc_id)
    p += struct.pack('<H', shape)
    p += struct.pack('<H', x)
    p += struct.pack('<H', y)
    p += struct.pack('<B', 0)     # block
    p += b'\x00'                  # desc 空
    p += struct.pack('<B', 1)     # direct（方向 1）
    p += struct.pack('<H', 0)     # npctype
    p += struct.pack('<B', 0)     # country
    p += struct.pack('<H', 0)     # addonId
    p += struct.pack('<B', 0)     # title_len
    p += var_gbk(name)
    return make_s2c(OP_MAP_ADD_NPC, bytes(p))


def build_map_summon(owner_id, entries):
    """S2CMapSummon（op105）：把宠物作为「召唤跟随」放到地图上。

    客户端 `netmap.S2CMapSummon` 的解析顺序（★逐字段对齐，勿改序）：
        owner_id(4) flag(1) n(1)
        每只：style(1) shape(2) desc(prop.UnpackDesc) namelen(1) name(N) titlelen(1) title?

    · owner_id = 主角在**地图上**的对象 id（= self.hero_id，与 build_map_track 同源）。
      客户端 `world.g_Objects.get(owner_id)` 取主角作为 owner 与初始落点。
    · flag=1 → 「放出/重建」：客户端先删旧召唤队伍再重建（重复发送即可刷新）。
    · style=0 → 不点半透明、不可点击（客户端置 m_SummonFlag=True，纯跟随表现）；
      style&1 → alpha160（半透明）；style&2 且 owner 是自己 → 可变点击（宠物本体）。
    · desc 用空 desc（先 1B n=0，UnpackDesc 走默认值）。
    · name/title 是「1B 长度前缀 + 裸 GBK 字节」（客户端 UnpackString(s)），
      与 op99 AddNpc 的 var_gbk（\\0 结尾、无长度前缀）不同，勿混用。

    ★ 移动由客户端自带：`netmap.S2CMapTrack` 收到主角移动帧时，会顺着
      `obj.m_SummTeam` 把召唤队伍一起 WalkTo（dist=i*40，队尾拖行），
      服务端**无需**为宠物单独发移动帧。
    """
    p = bytearray()
    p += struct.pack('<I', owner_id)
    p += struct.pack('<B', 1)              # flag：放出/重建
    p += struct.pack('<B', len(entries))
    for (style, shape, name, title) in entries:
        nb = name.encode('gbk', 'replace')
        tb = title.encode('gbk', 'replace') if title else b''
        p += struct.pack('<B', style)
        p += struct.pack('<H', shape)
        p += b'\x00'                       # desc：n=0（空 desc）
        p += struct.pack('<B', len(nb)) + nb
        p += struct.pack('<B', len(tb))
        if tb:
            p += tb
    return make_s2c(OP_MAP_SUMMON, bytes(p))


# ───────────────────────────────────────── NPC 对话 ──────────────────────
def build_npc_chat_ext(npc_name, msg, ran_key=0, npctype=0):
    """S2CNpcChatExt（op152，tag16）：ranKey(2) npctype(4) name(20定长) msg(变长)。"""
    p = bytearray()
    p += struct.pack('<H', ran_key)
    p += struct.pack('<I', npctype)
    p += npc_name.encode('gbk', 'replace')[:20].ljust(20, b'\x00')
    p += var_gbk(msg)
    return make_s2c(OP_NPC_CHAT_EXT, bytes(p))


def build_npc_learn_list(entries, mode=0, icost=0):
    """S2CNpcLearnList（op149）：教招式清单，客户端直接弹「学技能」面板。

    格式（`netnpc.S2CNpcLearnList`）：
        mode(1B) + icost(2B) + 循环[ skid(2B) npcLimit(1B) validLearn(1B) freeLevel(1B) ]
        + skid=0(2B) 结束
    三字段语义（以客户端 `dialog/dlglearnskill.CreateSkillInfo` 的用法为准）：
        npcLimit    门派/身份限制，0 = 不限
        validLearn  传给 `_SkillScore(..., user_limit=validLearn, ...)`；
                    255 = 走「标准」费用表（`LearnCostStd`），非 255 走 Hard 档
        freeLevel   该等级及以下免费（`if level <= FreeLevel: iCash = 0`）
    注意**价格与经验由客户端自己算**（它本地有费用表），服务端只给「能学什么、
    免费到几级」。因此 `icost` 对 1101-1109 段无意义，填 0。
    """
    p = bytearray()
    p += struct.pack('<B', mode & 0xFF)
    p += struct.pack('<H', icost & 0xFFFF)
    for (skid, npc_limit, valid_learn, free_level) in entries:
        p += struct.pack('<H', skid)
        p += struct.pack('<B', npc_limit & 0xFF)
        p += struct.pack('<B', valid_learn & 0xFF)
        p += struct.pack('<B', free_level & 0xFF)
    p += struct.pack('<H', 0)                  # 结束哨兵
    return make_s2c(OP_NPC_LEARN_LIST, bytes(p))


def build_npc_buylist(ticketid, items):
    """S2CNpcBuyList（op147）mode2：NPC 购买列表。

    格式（netnpc.S2CNpcBuyList mode==2）：
        mode(1B)=2 + ticketid(4B) + ext(1B)
        + 循环[ shape(4B) + num(2B) + price(4B) ] 直到 shape==0
    客户端 dlgtrade.Show(bl, ticketid, mode, ext) 打开商店界面。
    items: [(shape, num, price), ...]
    """
    p = bytearray()
    p += bytes([2])
    p += struct.pack('<I', ticketid)
    p += bytes([0])  # ext
    for (shape, num, price) in items:
        p += struct.pack('<I', shape)
        p += struct.pack('<H', num)
        p += struct.pack('<I', price)
    p += struct.pack('<I', 0)  # 终止符 shape==0
    return make_s2c(S2C_NPC_BUYLIST, bytes(p))


def build_npc_sell_list(items, mode=1):
    """S2CNpcSellList（op148）：NPC 收购列表，客户端弹「卖东西」面板。

    格式（`netnpc.S2CNpcSellList`）：
        mode(1B) + [mode==2: ticketid(4B)] + [mode==3: country(1B)]
        + 循环[ shape(4B) + amount(2B) + price(4B) ] 直到 shape==0
    只有 mode 1/2/3 会 `dlgsellnpc.Show(...)`；mode 0 客户端什么都不做。
    ★ 列表第一列是 **shape（造型）而非实例 id** —— 客户端拿它 `itemdata.GetData(shape)`
      取名字，并去背包里找 `m_Shape` 相同的实例做匹配。
    items: [(shape, amount, price), ...]
    """
    p = bytearray([mode & 0xFF])
    for (shape, amount, price) in items:
        p += struct.pack('<I', shape)
        p += struct.pack('<H', amount)
        p += struct.pack('<I', price)
    p += struct.pack('<I', 0)  # 终止符 shape==0
    return make_s2c(S2C_NPC_SELLLIST, bytes(p))


def build_npc_details(pos, desc):
    """S2CNpcDetails（op151）：pos(1) + desc(变长 C 串)。

    应答 C2S_NPC_GETDETAILS(101)：商店/收购面板悬停某格时，客户端拿 desc 喂
    `dlgtrade.UpdateDetails(pos, desc)` 画提示框（netnpc.S2CNpcDetails：
    `pos=UnpackInt(1); desc=UnpackString(0)`）。pos = 面板内格位（0 起）。
    """
    return make_s2c(S2C_NPC_DETAILS,
                    struct.pack('<B', pos & 0xFF) + var_gbk(desc))


# 制作/合成费用的**兜底**值（仅当 craftdata 没给出 cost 时用）。
# ★ 注意 cost 的口径：它扣的是**活力**（m_Huoli），不是体力（m_Tili）——
#   客户端每张生产面板都跟 m_Huoli 比（dlgcraft.py:80 / dlgcook.py:135 /
#   dlgdrug.py:193 / dlgfurnish.py:83）。教训：早期把 cost 当体力扣，客户端面板
#   显示的活力永远不动，玩家误以为「没消耗」。
# ★ 口径（2026-10-21 复核）：
#   · craftdata 的 48 条产物**每条都自带 cost(16–58) 和 lv(5–120)**，所以实际扣的是真值；
#     下面两个 10/1 只是生成表缺字段时的兜底，本仓库不会触发。
#   · craftdata **没有金钱费用**字段，MAKE_COST_MONEY=100 是纯模拟。
MAKE_COST_HUOLI = 10      # 兜底：craftdata 缺 cost 时才用（48 条均不缺）
MAKE_COST_MONEY = 100     # 纯模拟：craftdata 无金钱费用，100 金/件为本服约定
MAKE_COST_SKLV = 1        # 兜底：craftdata 缺 lv 时才用（48 条均不缺）


def build_npc_make_cost(tili, money, sklv):
    """S2CNpcMakeCost（op155）：iTili(2) + iMoney(2) + sklv(2)。

    应答 C2S_NPC_GETCOST(104)。客户端 `netnpc.S2CNpcMakeCost` 读三个 u16 后调
    `dlgmakenew.UpdateCost` / `dlgcompose.UpdateCost`，只用于在制作/合成面板显示
    「需要多少体力 / 金钱 / 技能等级」。三个值都是 u16 无符号。
    """
    return make_s2c(S2C_NPC_MAKECOST,
                    struct.pack('<HHH', tili & 0xFFFF, money & 0xFFFF,
                                sklv & 0xFFFF))


# ── 制作配方（★ 模拟参数：无真值）──────────────────────────────────────
# 客户端 `src/data/craftdata.py` 只有「产物目录」（产物 id → 名称/等级/费用/描述），
# **没有「材料 → 产物」的对应表**；原版配方在服务端，客户端拿不到。
# 这里手写最小闭环，只覆盖本服会发的几种材料；未登记的组合一律拒绝，不猜产物。
# 键 = ((材料 shape, 件数), ...) 升序；值 = 产物 shape。
ITEM_SHAPE_WHEAT = 22100       # 小麦种子（测试与基础配方共用引用，避免散落魔法数）
MAKE_RECIPES = {
    ((ITEM_SHAPE_WHEAT, 2),): 12,        # 小麦种子 ×2 → 鹿茸
    ((12, 2),): 10411,        # 鹿茸 ×2     → 幻灵珠
    ((10411, 2),): 25045,     # 幻灵珠 ×2   → 三级鉴定符
}

# 客户端 `craftdata` 的 48 个产物（25201-25248，lv 5-120）+ 生成的材料配方表。
# 产物目录是**真值**（craftdata × itemdata），材料映射仍是**模拟参数**（见生成器头注释）。
# 生成：python gen_make_recipes.py；缺表时只保留上面 3 条基础配方，不阻断起服。
try:
    from make_recipes import CRAFT_DATA, RECIPES as _CRAFT_RECIPES, MATERIAL_SHOP
except ImportError:
    CRAFT_DATA, _CRAFT_RECIPES, MATERIAL_SHOP = {}, {}, ()
MAKE_RECIPES.update(_CRAFT_RECIPES)
# 材料必须买得到才谈得上制作：把配方用到的真实材料接进商店尾部
# （追加在末尾，不打乱既有商品下标——客户端购买包带的是列表下标）
SHOP_ITEMS.extend((shape, name, price, 1) for shape, name, price in MATERIAL_SHOP)
# 名称/价格索引是从 SHOP_ITEMS 早绑定生成的，追加后必须同步，否则日志里是「物品10155」
ITEM_NAMES.update({shape: name for shape, name, _p in MATERIAL_SHOP})
SHOP_PRICE.update({shape: price for shape, _p, price in MATERIAL_SHOP})


def build_make_complete(shape, iid, amount, desc):
    """S2CMakeCompleteNotify（op160）：shape(4) + id(4) + amount(1) + desc(变长 C 串)。

    应答 C2S_NPC_MAKE(102)。客户端 `netnpc.S2CMakeCompleteNotify` 读完先调
    `g_MakeCompleteCallback(shape, amount, desc)`（→ `dlgmakenew.MakeOver`：刷新面板 +
    「制造了 N 个 X」），再 `dlgsale.RemoveItem(id)` 把条目移出制作面板。
    ★ shape 是**产物造型**；id 是产物入包后的实例 id（本服传 `_grant_item` 的返回值）。
    """
    return make_s2c(S2C_MAKE_COMPLETE,
                    struct.pack('<IIB', shape & 0xFFFFFFFF, iid & 0xFFFFFFFF,
                                amount & 0xFF) + var_gbk(desc))


# 村长对话脚本（ParseWord1 行语法：P=页头 Q/L/M/R=选项 其它=正文）
NPC_CHAT_WELCOME = 'P\n你好，我是村长，欢迎来到南遥国。\nQ我想接个任务。\nQ我想买点东西。\nQ我想卖点东西。\nQ没事，我随便看看。'
NPC_CHAT_TASK_OK = 'P\n最近野外的毛草团闹得厉害，去帮我教训一下它们吧！\nQ好，交给我！'
NPC_CHAT_BYE = 'P\n有需要随时来找我。'

# ── 任务闭环（M6）──
# ★ 2026-10-16：把「只下 S2CTaskAdd(72)」的占位任务升级为可完成闭环：
#   接任务(选项1) → 战斗击杀计数 → 达成 → 回村长交任务 → S2CTaskRemove(73) + 奖励。
# ★ 任务数值全部是**模拟**：客户端 taskdata 只有 title/type/desc，没有奖励字段；
#   《资料全库》也只写“任务给经验/金钱”，没有具体表。tasktypedata.Type2Level[1]=30
#   更是说明“任务类型 1”在原服有 30 级门槛——本服这个 1 级新手任务只是占位闭环，
#   并不冒充原服任务。改这些值不会破坏客户端，只改本服经济节奏。
TASK_NEWBIE_ID = 1
TASK_NEWBIE_NAME = '新手任务'
TASK_NEWBIE_NEED = 3                  # 模拟：需击败的毛草团数量
TASK_STATE_NONE, TASK_STATE_DOING, TASK_STATE_DONE = 0, 1, 2
TASK_REWARD_COINS = 500               # 模拟：资料库无具体金额
TASK_REWARD_EXP = 200                 # 模拟：资料库无具体经验
TASK_REWARD_ITEMS = ((10411, 1, '幻灵珠'),)   # 模拟：物品本身是真值，数量/选择为本服约定
EXP_INIT, NEXT_EXP_INIT = 0, 1000
# hero.ITEM_ALL 索引（src/hero.py:217）：m_Exp(8)/m_NextExp(9)/m_Gold(43) 均 TDWORD 4B
PROP_IDX_EXP, PROP_IDX_NEXTEXP, PROP_IDX_GOLD = 8, 9, 43
PROP_IDX_STORY_POINT = 42     # m_StoryPoint（TWORD，剧情点；ITEM_ALL 附录 A）

# ── 角色养成：升级 / 加点 / 学招式（M9，2026-10-18）─────────────────────
# 三条上行此前都在「未实现」名单里（客户端有 UI 入口但点了没反应）：
#   C2S_PROP_LEVELUP(17)  空包            → 经验够就升一级，发点数
#   C2S_PROP_POINT(16)    5×1B 增量       → 五维加点，扣可用点数
#   C2S_NPC_LEARN_SKILL(103) 2B 招式 id   → 校验金钱后授予/升级招式
# 回推一律用 S2CPropChange(17) 改 ITEM_ALL 对应项——客户端 `nethero.S2CPropChange`
# 就是 `ITEM_ALL[type]` → setattr，任意下标都能热更，不需要专用帧。
PROP_IDX_GRADE = 5              # m_Grade  TBYTE 1B
PROP_IDX_POINT = 37             # m_Point  TWORD 2B（可分配点数）
# ★ 5 字节顺序取自客户端 `dialog/dlgstate.ATTR_POINTS`（面板从上到下）：
#   m_Con_All, m_Str_All, m_Mag_All, m_Cha_All, m_Dex_All —— 不是「力量在前」。
ATTR_ORDER = ((36, 'm_Con_All'), (32, 'm_Str_All'), (33, 'm_Mag_All'),
              (34, 'm_Cha_All'), (35, 'm_Dex_All'))
# ★ 2026-10-23（P3-01）：五维初始值与力量换算的**唯一定义**搬到 progression_service，
#   这里只做别名，避免「两处各写一个 10，改一处漏一处」的经典漂移。
ATTR_BASE = prog.ATTR_BASE      # 五维初始值（PropInit 里原来是 0，加完点才有区别）
ATTR_MAX = 255                 # 客户端加点上限：dlgstate 判 `m_Points[idx] < 255`
POINT_INIT = 5                  # 新角色初始可分配点数（让面板一开局就能用）
POINT_PER_LEVEL = 5             # 每升一级发放
GRADE_MAX = 120                 # 等级上限（★ 与 world_config.json progression.grade_max 同步）
# ★ 2026-10-22：30 → 120。主线 7–22 的真值等级门槛是 35/40/…/120（一览表），
#   卡在 30 会让剧情 7 以后永远接不到。120 既是 world_config 的 guardrail 上限，
#   也是本库最高真值门槛（剧情 22 ≥120）。原服资料：飞升前 140 / 飞升后 150，
#   本服未实现飞升，不取 140。
#   注意：本行是 **import 期的值**；apply_to() 在 1722 行、早于本行执行，
#   故配置里的 grade_max 只在 reload_world_config() 之后才覆盖到这里 —— 两者必须一致。
EXP_PER_LEVEL_STEP = NEXT_EXP_INIT   # 升下一级所需 = 1000 × 当前等级
BATTLE_EXP_PER_ENEMY = 60       # 每击败一只怪的经验（★ 模拟参数，无真值来源）
STR_DMG_DIVISOR = prog.STR_PER_ATK   # 力量每比初始高 4 点，普攻 +1（★ 本服约定，非真值）

# ── 战斗掉落（M8）───────────────────────────────────────────────────────
# 掉落池是真值：drop_data.DROP_BY_SCENE（《梦想世界2013资料全库》场景表
# 「场景等级 / 怪物名称 / 掉落物品」× 客户端 src/data/itemdata.py 解析出的真实 item id）。
# 下面两个是**模拟参数**：资料表只写「哪个场景掉什么」，没有给概率与件数。
DROP_CHANCE = 0.6                     # 每场胜利掷一次骰的概率
DROP_MAX_PER_BATTLE = 1               # 每场最多掉落件数


# ───────────────────────────────────────── 任务 ──────────────────────────
def build_task_add(tid, name, desc, tasktype=1):
    """S2CTaskAdd（op72）最小合法帧：tid(2) icon(1) name(26定长) length(1)+desc
    detail_len(2)=0 cnt(1)=0 cnt(1)=0 timelimit(4) tasktype(4) flag(1) t(1)=0 Tipss(变长空)。"""
    d = desc.encode('gbk', 'replace')
    p = bytearray()
    p += struct.pack('<H', tid)
    p += struct.pack('<B', 0)                                   # icon
    p += name.encode('gbk', 'replace')[:26].ljust(26, b'\x00')  # name 定长 26
    p += struct.pack('<B', len(d))                              # length
    p += d                                                      # desc 定长
    p += struct.pack('<H', 0)                                   # detail_len
    p += struct.pack('<B', 0)                                   # 目标 NPC 数
    p += struct.pack('<B', 0)                                   # oid 数
    p += struct.pack('<I', 0)                                   # timelimit
    p += struct.pack('<I', tasktype)                            # tasktype
    p += struct.pack('<B', 0)                                   # 追踪 flag
    p += struct.pack('<B', 0)                                   # t（无 ex_tips）
    p += var_gbk('')                                            # Tipss 变长空
    return make_s2c(OP_TASK_ADD, bytes(p))


def build_task_remove(tid):
    """S2CTaskRemove（op73）：tid(2)。客户端 nettask.S2CTaskRemove → RemoveTask + 完成提示。"""
    return make_s2c(OP_TASK_REMOVE, struct.pack('<H', tid))


# ───────────────────────────── 打开 UI（C2S_OPENUI 88 → S2C op89）─────────
def build_minimap_npc(minimap, entries):
    """S2C op89 sub182 → netopenui.S2CAddMinimapNpc：
    minimap(2) + n(1) + [x(2) y(2) slen(1) name(slen) elen(1) ext(elen)] × n。

    应答 nethero.C2SAddNpc(minimap)：把小地图 NPC 标记推给客户端
    （dlgmap.ShowAddNpc）。entries 为空 = 本地图无标记。
    """
    p = bytearray()
    p += struct.pack('<H', minimap)
    p += struct.pack('<B', len(entries))
    for (x, y, name, ext) in entries:
        nb = name.encode('gbk', 'replace')
        eb = ext.encode('gbk', 'replace')
        p += struct.pack('<HH', x, y)
        p += struct.pack('<B', len(nb)) + nb
        p += struct.pack('<B', len(eb)) + eb
    return make_s2c(OP_OPENUI, bytes([OPENUI_RESP_ADDNPC]) + bytes(p))


def build_baozi_title():
    """S2C op89 sub129 → netopenui.S2CShowBaoziTitle：无附加数据（打开包子称号面板）。"""
    return make_s2c(OP_OPENUI, bytes([OPENUI_SUB_JINGXIU]))


# ───────────────────────────── 帮派面板（C2S_GROUP 81 → S2C op84）─────────
def build_family_open(name, owner):
    """S2C op84 sub80 → netgroup.S2COpenFamily：name(16定长) + owner(4)。

    dlgfamily.ShowMemberList(name, owner) 建面板，且 __init__ 末尾自动
    DoRefresh() → 客户端会紧接着发 C2S_GROUP(81) 求成员列表。
    """
    p = bytearray([GROUP_SUB_OPEN])
    p += name.encode('gbk', 'replace')[:16].ljust(16, b'\x00')
    p += struct.pack('<I', owner)
    return make_s2c(OP_GROUP, bytes(p))


def build_family_members(members):
    """S2C op84 sub81 → netgroup.S2CFamilyMemberList：
    [mid(4) name(16定长) grade(1) cls(1) online(1)] × n + mid=0(4B) 终止。"""
    p = bytearray([GROUP_SUB_MEMBERLIST])
    for (mid, name, grade, cls, online) in members:
        p += struct.pack('<I', mid)
        p += name.encode('gbk', 'replace')[:16].ljust(16, b'\x00')
        p += struct.pack('<BBB', grade, cls, online)
    p += struct.pack('<I', 0)
    return make_s2c(OP_GROUP, bytes(p))


# ───────────────────────────── 服务器时间 / 矿点（C2S 15/61/78 → S2C）──────
def build_server_time(opcode=OP_TIME, mxgw_ticks=False):
    """S2C 服务器时间。

    · op192 → dialog/dlgcalendar.S2CTime：**Unix 秒**（`time.localtime(g_Time+…)` 直接用）。
    · op15  → core/mxgw.S2CTime：**10000Hz 滴答**（与客户端 `C_sys.GetTime` 同源）。
      ★ 2026-10-16 真机修复（网络错误 007）：mxgw 时钟漂移检查
        `cdt=(GetTime2-GetTime1)/100 > sdt=(t2-t1)+500` 时 `Disconnect(007)`。
        客户端 GetTime 为 10000Hz 计数（dlgcalendar.py:303 `(GetTime-g_StartTick)/10000`
        佐证）：20s 间隔 → cdt≈2000。旧回 Unix 秒 → sdt≈520 → 2e3>520 **每次第二查询必断**。
        回同源 10000Hz 滴答后 cdt=Δ/100 ≪ sdt=Δ+500 恒过（官方协议设计即如此，
        客户端对 32 位回绕有 `t<0 → +0xFFFFFFFF` 处理）。
      ★ 2026-10-20 残留校验排查加固：mxgw 分支的时钟源从 `time.time()`（墙钟）
        换成 `time.monotonic()`（单调）。墙钟会被 NTP 步进/手工改表/VM 快照回滚
        打跳：间隔内回跳 >(cdt-500)/10000 秒（默认 600 单位间隔约 6s）就会
        `cdt > sdt` 误报，客户端弹「网络错误(007)」后断线——**服务端自己造出来的故障**。
        mxgw 只比较**增量**（首响应当基线、第二响应算差），绝对值无意义，
        换单调源后增量恒等于真实经过时间，墙钟怎么跳都不误报。
        op192（日历显示）仍然用墙钟——它要显示真实日期，语义不同，勿混用。
    """
    if mxgw_ticks:
        return make_s2c(opcode, struct.pack('<I', int(time.monotonic() * 10000) & 0xFFFFFFFF))
    return make_s2c(opcode, struct.pack('<I', int(time.time()) & 0xFFFFFFFF))


def build_minerals(iscene, entries=()):
    """S2C op87 sub5 → netliving.S2CMinerals：iOpen(1) iscene(4) ilen(1) + [x(2) y(2) type(1)]×n。

    `dlgmap.RefreshMinerals(scene, lst)` 仅在 `g_Minimap.m_SceneID == scene` 时生效，
    其余情况安全忽略；空列表 = 本地图无矿点。
    """
    p = bytearray([PRODUCE_SUB_MINERALS])
    p += struct.pack('<B', 1)                    # iOpen
    p += struct.pack('<I', iscene)
    p += struct.pack('<B', len(entries))
    for (x, y, mtype) in entries:
        p += struct.pack('<HHB', x, y, mtype)
    return make_s2c(OP_PRODUCE, bytes(p))


# ───────────────────────────── 宠物（C2S 129 → S2C 48/49/51）──────────────
def build_pet_info(pid, name, shape, base):
    """S2C op48 → netpet.S2CSummonInfo → pet.UpdateInfo →
       CPet.UpdateProp 读 BASE_PROP[4:]：

       pid(4) name(20定长) shape(2) desc(变长, 空=0x00) +
         m_Grade(1) m_HP(4) m_MP(4) m_FP(4) m_HPMax(4) m_MPMax(4) m_FPMax(4)
         m_Damage(4) m_Defense(4) m_Speed(4) m_Magdef(4)
         m_Str(2) m_Con(2) m_Mag(2) m_Cha(2) m_Dex(2) m_Point(2)
         m_Exp(4) m_ExpNext(4) m_ExpNeed(4) m_Life(4) m_MoLingGuo(1)

    shape=65535 表示删除该宠物（UpdateInfo 的移除分支）。
    """
    p = bytearray()
    p += struct.pack('<I', pid)
    p += name.encode('gbk', 'replace')[:20].ljust(20, b'\x00')
    p += struct.pack('<H', shape)
    p += b'\x00'                                  # desc: UnpackDesc n=0
    p += struct.pack('<B', base['grade'])
    # ★ 2026-10-18 真机修正：气血/魔法/力道 = 当前值 + 上限值两组，早前两处都写当前值，
    #   残血进战斗时面板会显示「170/170」（应为 170/350）。上限取 hpmax/mpmax/fpmax。
    p += struct.pack('<IIIIII', base['hp'], base['mp'], base['fp'],
                     base.get('hpmax', base['hp']),
                     base.get('mpmax', base['mp']),
                     base.get('fpmax', base['fp']))
    p += struct.pack('<IIII', base['damage'], base['defense'],
                     base['speed'], base['magdef'])
    p += struct.pack('<HHHHHH', base['str'], base['con'], base['mag'],
                     base['cha'], base['dex'], base['point'])
    p += struct.pack('<IIII', base['exp'], base['expnext'], 0, base['life'])
    p += struct.pack('<B', 0)                     # m_MoLingGuo
    return make_s2c(OP_SUMMON_INFO, bytes(p))


def build_pet_prop_view(pid, extra_zz, ability, resist):
    """S2C op51 → netpet.S2CPropView → CPet.UpdateAdvProp 读 ADV_PROP：

       pid(4) extra_zz(2) +
         Ability_Attack/Defense/Con/Magic/Speed (TWORD×5)
         Update_Flag(4) Update_Max(4) PromoteZZ(1) PromoteMax(2)
         Resist_Lidi/Lingqiao/Qungong/Putong/Xian/Gui/Yao/Zhi/Nian (有符号 TBYTE×9)

    `m_UpdateProp=True` 由客户端置位，之后不再重复请求（dlgpet 的 m_UpdateProp 守卫）。
    """
    p = bytearray()
    p += struct.pack('<I', pid)
    p += struct.pack('<H', extra_zz)
    p += struct.pack('<HHHHH', *ability)
    p += struct.pack('<II', 0, 0)                 # Update_Flag / Update_Max
    p += struct.pack('<B', 0)                     # PromoteZZ
    p += struct.pack('<H', 0)                     # PromoteMax
    p += struct.pack('<bbbbbbbbb', *resist)       # 9 抗性（有符号）
    return make_s2c(OP_PROP_VIEW, bytes(p))


def build_pet_skill(pid, skills, hf_from=None):
    """S2C op49 → netpet.S2CSummonSkill → pet.UpdateSkill/UpdateHfSkill：

       pid(4) n(1) x(1) + skill_id(2)×n      （hfskill = n - x）

    · skill_id > 10000 → 视为「绑定技能」（客户端 -10000 并记录 bind_idx）
    · idx >= hfskill 的技能在面板上标记为护符技能
    """
    if hf_from is None:
        hf_from = len(skills)
    p = bytearray()
    p += struct.pack('<I', pid)
    p += struct.pack('<B', len(skills))
    p += struct.pack('<B', hf_from)
    for sk in skills:
        p += struct.pack('<H', sk)
    return make_s2c(OP_SUMMON_SKILL, bytes(p))


def build_pet_fight(pid):
    """S2C op50 → netpet.S2CSetFight：pid(4)。"""
    return make_s2c(OP_SUMMON_FIGHT, struct.pack('<I', pid))


def build_pet_key(pid, key=0, birthkey=0):
    """S2C op53 → netpet.S2CSummonKey：pid(4) key(2) birthkey(1)。

    ★ `netpet.S2CSummonKey`：`key = birthkey << 8 | key` 后调 `pet.SetKey(pid, key, birthkey)`；
      `wrapinterface` 的宠物头像角标取 `newkey = (m_Key & 65280) >> 8`（即 birthkey 字节），
      按 pet.MARK_* 依次判定：仙兽(8)/神兽(4)/稀有(16)/珍英(32)/**变异(2)**/宝宝(1)。
    故 birthkey=2 即在宠物头像右下角 (78,126) 画出「变异」——录像 v_24 的「梦想企鹅（变异）」。
    """
    return make_s2c(OP_SUMMON_KEY,
                    struct.pack('<IHB', pid, key & 0xFFFF, birthkey & 0xFF))


def build_pet_equip(pid, equips):
    """S2C op54 → netpet.S2CSummEquip → pet.UpdateEquip：

       pid(4) + 循环: pos(1) shape(4) desc_len(1) desc(desc_len) —— pos<=0 结束

    线序逐字来自客户端反编译（netpet.py 的 S2CSummEquip）：pos 读到 <=0 就
    break；desc 是 GBK 串（UnpackString(length)）。客户端
    dlgpetitem.DrawPetEquip 只认 pos ∈ (1,2,3)（项圈/铠甲/护符），越界条目
    会被跳过——所以构建端就地把越界 pos 滤掉，不发客户端必然丢弃的数据。
    `equips` 接受三种形态：
      · 存储形态 {pos: {'shape': int, 'desc': str}}（pet_new/_pets_snapshot/
        _load_pets/_pet_equip 用的就是它， GM 与 C2S 两条路径直接透传）；
      · 便捷形态 {pos: (shape, desc)}；
      · [(pos, shape, desc), ...]。
    """
    if isinstance(equips, dict):
        items = []
        for pos, v in sorted(equips.items()):
            if isinstance(v, dict):
                items.append((int(pos), int(v.get('shape') or 0),
                              v.get('desc') or ''))
            else:
                items.append((int(pos), int(v[0]),
                              v[1] if len(v) > 1 else ''))
    else:
        items = [(int(pos), int(shape), desc or '')
                 for (pos, shape, desc) in equips]
    p = bytearray()
    p += struct.pack('<I', int(pid))
    for pos, shape, desc in items:
        if not (1 <= pos <= 3):
            continue
        raw = desc.encode('gbk', 'replace')[:255]
        p += struct.pack('<B', pos)
        p += struct.pack('<I', shape)
        p += struct.pack('<B', len(raw))
        p += raw
    p += struct.pack('<B', 0)                  # pos=0：客户端据此 break
    return make_s2c(OP_SUMMON_EQUIP, bytes(p))


PET_NAME_MAX_BYTES = 20      # SummonInfo 的名字字段是 20 字节定长（客户端 UnpackString(20)）


def gbk_fit(text, max_bytes):
    """把字符串截到 GBK 编码不超过 max_bytes 字节（不切半个汉字）。

    `name.encode('gbk')[:20]` 会把一个汉字的第二个字节切掉，客户端拿到的是
    半个字的乱码尾字节。这里按整字回退。
    """
    raw = (text or '').encode('gbk', 'replace')
    if len(raw) <= max_bytes:
        return text or ''
    raw = raw[:max_bytes]
    while raw:
        try:
            return raw.decode('gbk')
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ''


def pet_base_dict():
    return {'grade': PET_LEVEL,
            'hp': PET_HP, 'hpmax': PET_HP,
            'mp': PET_MP, 'mpmax': PET_MP,
            'fp': PET_FP, 'fpmax': PET_FP,
            'damage': PET_AT_DISPLAY, 'defense': PET_DF_DISPLAY, 'speed': PET_SPEED_DISPLAY,
            'magdef': PET_MAGDEF_DISPLAY, 'str': PET_STR, 'con': PET_CON, 'mag': PET_MAG,
            'cha': PET_CHA, 'dex': PET_DEX, 'point': PET_POINT,
            'exp': PET_EXP, 'expnext': PET_EXPNEXT, 'life': PET_LIFE}


# ───────────────────────────── 物品（C2S 32-38 ↔ S2C 32-38）──────────────
def build_item_add(wtype, iid, shape, special_flag, quantity, pos):
    """S2C op33 → netitem.S2CItemAdd：

       type(1) id(4) shape(4) special_flag(2) quantity(1) pos(1)

    客户端 ItemAdd 内部 `itype -= 1` 并按 pos 存进 g_Items[itype][pos]
    （0-9 装备位 / 20+ 背包 / 100+ 战斗道具栏）；special_flag 即 m_Key。
    """
    return make_s2c(OP_ITEM_ADD,
                    struct.pack('<BIIHBB', wtype, iid, shape, special_flag,
                                quantity, pos))


def build_item_del(wtype, iid):
    """S2C op34 → netitem.S2CItemDel：type(1) id(4)。"""
    return make_s2c(OP_ITEM_DEL, struct.pack('<BI', wtype, iid))


def build_item_quantity(iid, quantity, flash=0):
    """S2C op35 → netitem.S2CItemQuantity：id(4) quantity(1) flash(1)。"""
    return make_s2c(OP_ITEM_QUANTITY, struct.pack('<IBB', iid, quantity, flash))


def build_item_pos(wtype, iid, pos):
    """S2C op36 → netitem.S2CItemPos：type(1) id(4) pos(1)（拖动后回位）。"""
    return make_s2c(OP_ITEM_POS, struct.pack('<BIB', wtype, iid, pos))


def build_item_string(iid, infoext):
    """S2C op38 → netitem.S2CItemString：id(4) infoext(变长, NUL 结尾)。

    客户端悬停道具即发 C2SItemString(id,0)，并把 m_InfoExt 作为提示附加信息。
    """
    return make_s2c(OP_ITEM_STRING, struct.pack('<I', iid) + var_gbk(infoext))


def build_item_info(iid, key=0, ext='', itype=ITEM_WTYPE_NORMAL):
    """S2C op162 → nethero.S2CItemInfo：itype(1) key(2) id(4) ext(变长, NUL 结尾)。

    应答 C2S_GETINFO_ITEM(145)（背包里看物品详情）：
    `dlggetinfo.UpdateServerItemInfo(itype, id, key, ext)`。
    ★ itype 与 op38 的 id 语义不同：这里是「物品类别」，本服背包件一律 ITEM_WTYPE_NORMAL。
    """
    return make_s2c(OP_ITEM_INFO,
                    struct.pack('<BHI', itype & 0xFF, key & 0xFFFF, iid) + var_gbk(ext))


def build_item_tc_info(iid, key=0, ext='', itype=ITEM_WTYPE_NORMAL):
    """S2C op190 → nethero.S2CItemTCInfo：与 op162 同构（交易中心/附加信息面板）。

    应答 C2S_GETITEMINFOEXT(156)（`dlggetinfo.UpdateTradeCenterItemInfo(itype, id, key, ext)`）。
    """
    return make_s2c(OP_ITEM_TCINFO,
                    struct.pack('<BHI', itype & 0xFF, key & 0xFFFF, iid) + var_gbk(ext))


def build_item_key(iid, key):
    """S2C op32 → netitem.S2CItemKey：id(4) key(2)。"""
    return make_s2c(OP_ITEM_KEY, struct.pack('<IH', iid, key))


def build_notify_ext(itype, shape, msg):
    """S2CNotifyExt（op164）：itype(1) + shape(4) + msg(变长 C 串)。

    客户端 `nethero.S2CNotifyExt` 按 itype 取图标，再交给 `usermacro.movehint`
    在屏幕中部滑入一条提示（约 3 秒后自动消失）。itype 取值：
        0 物品 / 1 技能 / 2 人物头像 / 3 角色造型 / 4 宠物 / 5 动画 / 10 徽章
    ★ itype == 6 时 shape 是「造型四元组」4×4B（人物纸娃娃），不是单个 u32；
      本服不发送该形态，故调用方必须自己保证传 4 元组。
    """
    if itype == 6:
        p1, p2, p3, p4 = shape
        body = struct.pack('<BIIII', 6, p1, p2, p3, p4)
    else:
        body = struct.pack('<BI', itype & 0xFF, int(shape) & 0xFFFFFFFF)
    return make_s2c(S2C_NOTIFY_EXT, body + var_gbk(msg))


# ───────────────────────────────────────── 战斗 ──────────────────────────
def build_war_start(scene_field, alineup, elineup, wtype, x, y):
    """S2CWarStart（op112，tag16）：scene(2)→客户端 +1001 还原；alineup(1) elineup(1) type(1) x(2) y(2)。

    ★ 字段定义在 `frames.SPEC`（唯一来源），本函数只做参数搬运。
    """
    return make_s2c(OP_WAR_START, bf.pack_s2c(
        'S2CWarStart', scene=scene_field, alineup=alineup, elineup=elineup,
        wtype=wtype, x=x, y=y))


def build_war_add(wid, shape, allied, name, desc=b'\x00', owner_id=0):
    """S2CWarAdd（op115，tag18）：id(1) shape(2) desc(变长) bitfield(1)
    owner_id(1) cls(1) grade(1) name(变长)。

    ★ bitfield 位序为 MSB-first —— 由 mxmain.dll 内 CNetClient.UnpackBit 核心
      (VA 0x41a4d0) 反汇编证实：
          value = (byte & (0x80>>off | 0x80>>(off+1) | ...)) >> (8 - width - off)
      即 off=0 → 最高位 bit7。字段布局：
          allied = bit7 (0x80)、type = bit6、child = bit5、reserved = bit4..0
      历史上按 LSB(bit0=0x01) 发送导致全单位 allied=0（"玩家在对面"的根因）。

    ★ owner_id：客户端 `core/war.Add` 用 `obj.m_OwnerID = cmd.owner_id`，
      再 `obj.m_Place = lineup.GetEmptyPlace(allied, id, owner_id)`。
      服务端下发的 place 字段被客户端忽略；站位完全由 owner_id 决定：
      owner_id=0 → 取空闲位（宠物会落在主角后面的空位）；
      owner_id=主角 war_id → `owner.m_Place + 5`（落到主角前排，朝向敌方一侧）。
    """
    return make_s2c(OP_WAR_ADD, bf.pack_s2c(
        'S2CWarAdd', id=wid, shape=shape, desc=desc,
        allied=1 if allied else 0, owner_id=owner_id, cls=0, grade=1, name=name))


def build_war_turn(lastcmd):
    """S2CWarTurn（op114）：lastcmd(1)。"""
    return make_s2c(OP_WAR_TURN, bf.pack_s2c('S2CWarTurn', lastcmd=lastcmd))


def build_war_attack_normal(att_id, vic_id):
    """S2CWarAttackNormal（op117）：att_id(1) vic_id(1)。"""
    return make_s2c(OP_WAR_ATTACK_NORMAL,
                    bf.pack_s2c('S2CWarAttackNormal', att_id=att_id, vic_id=vic_id))


def build_war_protect(protector_id, protege_id):
    """S2CWarProtect（op121）：protector_id(1) protege_id(1)。"""
    return make_s2c(OP_WAR_PROTECT,
                    bf.pack_s2c('S2CWarProtect', protector_id=protector_id,
                                protege_id=protege_id))


def build_war_wstate(wid, hurt, life, ward, hp_chg):
    """S2CWarWState（op123，tag18）：id(1) bitfield(1) hp_chg(4 signed)。

    ★ 位域语义由客户端源码定死（`src/netwar.py:S2CWarWState`）：
        cmd.hurt = UnpackBit(0, 4)
        cmd.life = UnpackBit(4, 2)
        cmd.ward = UnpackBit(6, 2)
        cmd.hp_chg = UnpackInt(4)
    但 bit 编号为 MSB-first（mxmain.dll UnpackBit 核心 0x41a4d0）：
        off=0 → bit7。故实际布局为：
        hurt = bit7..4（高 4 位）、life = bit3..2、ward = bit1..0（低 2 位）。
        bitfield = (hurt<<4) | (life<<2) | ward
    """
    return make_s2c(OP_WAR_WSTATE, bf.pack_s2c(
        'S2CWarWState', id=wid, hurt=hurt, life=life, ward=ward, hp_chg=hp_chg))


def build_war_hp_percent(wid, per):
    """S2CWarHPPercent（op125，tag18）：id(1) per(1)。per=血量百分比（0-100）。

    客户端 `war.HPPercent` 会执行 `obj.m_HPFull.SetRate(per * 10)` —— 即血条**只由本帧刷新**。
    不发本帧时，受伤后血条不会变（只有飘字）。最小闭环可不发，但补上更接近真实。
    """
    return make_s2c(OP_WAR_HP_PERCENT,
                    bf.pack_s2c('S2CWarHPPercent', id=wid, per=per))


def build_war_attack_normal_end(wid):
    """S2CWarAttackNormalEnd（op119）：id(1)。"""
    return make_s2c(OP_WAR_ATTACK_NORMAL_END,
                    bf.pack_s2c('S2CWarAttackNormalEnd', id=wid))


def build_war_perform(wid, pid, vic_ids):
    """S2CWarPerform（op122，tag18）：id(1) pid(2) vic_id(1)×N。

    ★ 客户端的 Perform 命令**只由本帧创建**（`netwar.S2CWarPerform` → `war.CCmd(war.Perform)`）：
      客户端 `warui.DoAct('magic', ...)` 只负责发 C2SWarPerform(161)，本地不建命令。
      若服务端收到 161 后回的是 AttackNormal 族，客户端的招式演出与命令推进都缺失，
      表现为「放完招式后卡请等待」（客户端录像实证，见 reports/M4_客户端录像分析.md）。
    """
    return make_s2c(OP_WAR_PERFORM, bf.pack_s2c(
        'S2CWarPerform', id=wid, pid=pid, vic_ids=vic_ids))


def build_war_summon(wid, op=0):
    """S2CWarSummon（op129，tag18）：id(1) op(1)。

    ★ 客户端 `netwar.S2CWarSummon` → `war.CCmd(war.Summon)`（该命令**只由本帧创建**），
      执行时 `war.g_SummonObj = cmd.id`。此后主角每次在战斗菜单里选完动作，
      `warui.ShowSummMenu()` 才会**弹出宠物自己的指令菜单（CSummonMenu：攻击/招式/
      防御/道具/保护/逃跑）**；否则退化成 ShowWait → 玩家无从指挥宠物 → 服务端只能
      干等 30s 超时默认普攻（真机日志「⚠ 宠物指令无效，默认普攻」的根因）。
    ★ op 字段不参与客户端逻辑（`war.Summon` 只用 `cmd.id`），置 0 即可。
    ★ 必须在宠物 WarAdd **之后**下发：`war.Summon` 执行时宠物对象须已在 world.g_Objects，
      否则 `ShowSummMenu` 的 `has_key(g_SummonObj)` 判定为假、仍不弹菜单。
    """
    return make_s2c(OP_WAR_SUMMON, bf.pack_s2c('S2CWarSummon', id=wid, op=op))


def build_war_del(wid):
    """S2CWarDel（op116）：id(1)。"""
    return make_s2c(OP_WAR_DEL, bf.pack_s2c('S2CWarDel', id=wid))


def build_mobile_info(check=0, receive=1, enable_friends=1, limit=(0, 0, 0, 0),
                      degree=0, money=0, balance=0):
    """S2CMobile（op58）sub=1 —— 「手机安全」面板的整包状态。

    读序（`src/nethero.py:S2CMobileInfo`，sub 由 `S2CMobile` 先吃掉）：
        sub(1)=1 check(1) receive(1) enable_friends(1) limit×4(1B)
        friends_degree(4) money(1) balance(2)      → 共 15 字节
    全部字段只喂 `dlgsysopt.InitMobile()` 显示，服务端不依赖它们做判定。
    """
    lim = list(limit or [])[:4]
    while len(lim) < 4:
        lim.append(0)
    body = struct.pack('<BBBB', 1, check & 0xFF, receive & 0xFF, enable_friends & 0xFF)
    body += bytes(bytearray(max(0, min(255, int(x))) for x in lim))
    body += struct.pack('<IBH', degree & 0xFFFFFFFF, money & 0xFF, balance & 0xFFFF)
    return make_s2c(S2C_MOBILE, body)


def build_prop_change_hp(hp):
    """S2CPropChange（op17）改 m_HP：type(1) + value(按 ITEM_ALL[type] 的类型)。

    `src/nethero.py:S2CPropChange`：type=UnpackInt(1) → prop=ITEM_ALL[type] → value=prop.Unpack()
    `ITEM_ALL[16]` = m_HP（TWORD，2B 无符号）。主角真实血量走本帧，**不是** WarWState。
    """
    return make_s2c(OP_PROP_CHANGE, struct.pack('<B', PROP_IDX_HP) + struct.pack('<H', max(0, hp)))


def build_prop_change(idx, value, width=2):
    """S2CPropChange（op17）通用版：type(1) + value（按 ITEM_ALL[type] 的字宽）。

    用于战斗资源扣减：m_MP(18,TWORD) / m_FP(20,TWORD) / m_At(72,TBYTE) /
    m_Df(73) / m_Dp(74)。客户端 `nethero.S2CPropChange` 会 setattr 到 g_Prop，
    从而刷新血条/资源条与招式可用性判定。
    """
    v = max(0, int(value))
    if width == 1:
        return make_s2c(OP_PROP_CHANGE, struct.pack('<BB', idx, v & 0xFF))
    if width == 4:
        # TDWORD 项：m_Exp(8) / m_NextExp(9) / m_Gold(43)（任务奖励同步用）
        return make_s2c(OP_PROP_CHANGE, struct.pack('<BI', idx, v & 0xFFFFFFFF))
    return make_s2c(OP_PROP_CHANGE, struct.pack('<BH', idx, v & 0xFFFF))


def build_war_end():
    """S2CWarEnd（op113）：无字段。"""
    return make_s2c(OP_WAR_END, bf.pack_s2c('S2CWarEnd'))


# ────────────────────────────── 属性补帧（招式可用性）────────────────────
# ★ 根因（2026-10-15，客户端源码定死）：
#   `hero.CreateProp` 只解 `ITEM_ALL[:-8]`（67 项），末 8 项（5 抗性 + m_At/m_Df/m_Dp）
#   仅被设为 `Default()` = 0。而 `hero.GetMagicData` 的可用性判定包含：
#       c1 = int(magic['at'] * xinfarate) > g_Prop.m_At   →  at>0 的招式直接 invalid
#       c2 = int(magic['df'] * xinfarate) > g_Prop.m_Df   →  df>0 的招式直接 invalid
#       c1 = int(magic['dp'] * xinfarate) > g_Prop.m_Dp   →  dp>0 的招式直接 invalid
#   绝大多数招式 df/at/dp 非 0 ⇒ 不发 PropResist/PropPower 就会看到「无法使用这个招式」。
#   这正是 M4 文档 §6.2 记的 ⏳ 项，且录像 19.0-20.5s 的提示即由此产生。
def build_prop_resist():
    """S2CPropResist（op18）：5 项抗性，各 1B 有符号（TBYTE signed）。

    字段序 = `hero.ITEM_ALL[-8:-3]`：m_Resist_Xian/Gui/Yao/Zhi/Nian。
    `hero.UpdateResist` 用 `UnpackSigned(1)` 读（CHeroSignedPropItem.SetSigned(True)）。
    """
    return make_s2c(OP_PROP_RESIST, struct.pack('<bbbbb', 0, 0, 0, 0, 0))


def build_prop_power():
    """S2CPropPower（op19）：m_At/m_Df/m_Dp 各 1B（TBYTE，无符号）。

    字段序 = `hero.ITEM_ALL[-3:]`。这三个值**同时**是：
      · 物伤公式的 A/D/P 输入（`_dmg()` 直接用同一组全局）
      · 招式可用性判据（`warui` 要求三者 ≥4，且 ≥ 招式自身 at/df/dp）
    所以不能再像以前那样一律给 100 —— 给大了伤害会失真（1.3×100 = 130）。
    运行期读全局，`world_config` 覆盖 HERO_AT/DF/DP 后这里自动跟随。
    """
    return make_s2c(OP_PROP_POWER, struct.pack('<BBB', HERO_AT, HERO_DF, HERO_DP))


def build_skill_level_life(skid, level, limit, truelv, point=0, point_max=0):
    """S2CSkillLevel（op65）sktype=1：生活/生产技能（客户端 `hero.UpdateSkill` → `g_Skills[ST_SKILL]`）。

    线序（`src/nethero.py:S2CSkillLevel` sktype==1 分支）：
        sktype(1)=1 + skid(2) + sklevel(1) + sklimit(1) + truelv(1) + point(4) + point_max(4)
    ★ `hero.UpdateSkill` 首句 `sktype -= 1` → 线序 1 落到 ST_SKILL(0)，即「生活技能」槽；
      `sklevel <= 0` 且无熟练度时客户端会**删除**该条目，所以至少要发 1。
    ★ skid 取自 `skilldata`（真值）：1117 = 工艺技术。
    """
    p = struct.pack('<B', 1)                     # sktype
    p += struct.pack('<H', skid)                 # skid
    p += struct.pack('<B', level & 0xFF)         # sklevel（显示等级）
    p += struct.pack('<B', limit & 0xFF)         # sklimit（等级上限）
    p += struct.pack('<B', truelv & 0xFF)        # truelv（真实等级）
    p += struct.pack('<I', point & 0xFFFFFFFF)   # 熟练度
    p += struct.pack('<I', point_max & 0xFFFFFFFF)
    return make_s2c(OP_SKILL_LEVEL, p)


def build_skill_level_magic(skid, level):
    """S2CSkillLevel（op65）sktype=2 分支：授予/更新一个「招式」到 `g_Skills[ST_MAGIC]`。

    `src/nethero.py:S2CSkillLevel`：
        sktype = UnpackInt(1)          # 2 = 学招式
        skid   = UnpackInt(2)
        sklevel= UnpackInt(1)
        [sktype==2 额外] bid=UnpackInt(2) point=UnpackInt(2) cash=UnpackInt(4) iHighLv=UnpackInt(1)
    注意 sktype=10 是「心法经验」专用分支（不读 skid/sklevel），别用 10。
    """
    p = struct.pack('<B', 2) + struct.pack('<H', skid) + struct.pack('<B', level)
    p += struct.pack('<H', 0)        # bid（技能格）
    p += struct.pack('<H', 0)        # point（修炼点）
    p += struct.pack('<I', 0)        # cash
    p += struct.pack('<B', 0)        # iHighLv
    return make_s2c(OP_SKILL_LEVEL, p)


def build_skill_level_juqing(skid, level):
    """S2CSkillLevel（op65）sktype=3 分支：授予/升级一个**剧情技能**。

    这是剧情点（m_StoryPoint）在本版客户端里**唯一**的消费入口：
    `dialog/dlglearnskill.py` 对 `2001 <= skid < 2030` 的条目把花费列换成
    `hero.g_Prop.m_StoryPoint`（够不够也拿它比），学会后由本帧写进
    `hero.g_Skills[2]`（`nethero.S2CSkillLevel` 的 `elif sktype == 3` 分支）。

    `nethero.S2CSkillLevel` 的 sktype=3 只读 skid/sklevel/bid(2)，没有额外字段。
    客户端侧已知小瑕疵：`dlglearnskill` 的等级列读的是 `g_Skills[ST_SKILL]`
    （索引 0），而剧情技能落在 `g_Skills[2]`，所以对话框里等级列显示 0 ——
    服务端状态才是权威，不该为迁就显示把 sktype 改成 1（那会写错槽位）。
    """
    p = (struct.pack('<B', 3) + struct.pack('<H', skid)
         + struct.pack('<B', level) + struct.pack('<H', 0))     # bid = 技能格 0
    return make_s2c(OP_SKILL_LEVEL, p)


def build_skill_equip(skid, level=1):
    """S2CSkillLevel（op65）sktype=7 分支：把招式**装备**进战斗「招式」菜单。

    ★ 关键：战斗菜单读的是 `hero.g_Skills[ST_MAGICREADY]`（索引 6），
      而 `hero.UpdateSkill` 开头执行 `sktype -= 1` —— 所以要让索引落在 6，
      **必须传 sktype=7**（`src/nethero.py:S2CSkillLevel` 的 `elif sktype == 7` 分支
      → `hero.UpdateSkill(7, skid, sklevel)` → 写入 `g_Skills[6]`）。

    对照（易错）：
      · `S2CBlock`(77) → `S2CMagicInfo` 只调 `block.UpdateBlockInfo`（技能面板 UI），
        **不会**装备招式，战斗菜单里看不到 —— 故不能用它来装备。
      · `sktype=2` 只把招式学进 `g_Skills[ST_MAGIC]`（索引 1），同样不进战斗菜单。
    本帧体与 sktype=2 分支不同：只读 skid(2) + sklevel(1)，无额外字段。
    """
    return make_s2c(OP_SKILL_LEVEL, struct.pack('<B', 7) + struct.pack('<H', skid)
                    + struct.pack('<B', level))


# ══════════════════════════════════════════════════════════ 帧批合并 ══
# 一次上行指令往往要回一大串帧：进世界 45 帧、切图整包（SceneM + AddNpc×N +
# GotoM）、战斗演出链（WarStart + WarAdd×N + WarTurn）…… 每帧一次 sendall，
# 就是每帧一次系统调用 + 一次（可能分片成多包的）TCP 写。单机服在本地环回上
# 看不出差别，但 **Nagle 算法 + 小包** 的组合会让客户端收包节奏变得零碎，
# 抓包器/调试台的帧到达时间也被切成锯齿状。
#
# 帧是自定界的（[1B N] 或 [FF][2B N]，见 make_s2c），所以「把 N 帧拼成一次写」
# 与「分 N 次写」在字节流上**完全等价** —— 客户端按长度前缀切分，看不出区别。
# 这是本优化成立的前提，也是它零风险的原因。
#
# ★ 三条硬约束（违反任意一条都会变成线上事故）：
#   1. 绝不跨「等待」合批：任何阻塞读之前必须 flush，否则帧会被扣在缓冲里，
#      客户端等不到 → 卡「请等待」/卡登录。
#   2. 合批层必须在抓包器**下面**：TappedConn 仍是逐帧回调，调试台的
#      「丢弃下一帧」按帧生效（合批后一次 sendall = N 帧会把丢 1 帧变成丢 N 帧）。
#   3. 不走分派路径的推送（GM 面板/HTTP 线程）显式 flush：它们不受
#      「分派结束即 flush」约束，不 flush 就会一直压在缓冲里。
FRAME_BATCH = True            # 总开关（False = 逐帧直发，回归对照用）
FRAME_BATCH_MAX_BYTES = 32768  # 缓冲到此立即 flush：避免一次超长链把延迟顶上去


class FrameBatcher(object):
    """把连发的多帧攒成一次 `sendall`，在保证帧序与不跨等待的前提下减少写次数。

    只包装**下行**（sendall）；上行 recv 原样转发，并在读之前先 flush（约束 1）。
    """

    def __init__(self, conn, max_bytes=FRAME_BATCH_MAX_BYTES):
        self._conn = conn
        self._max = int(max_bytes)
        self._buf = bytearray()
        # 统计：合批到底省了多少次写
        self.frames = 0
        self.writes = 0
        self.bytes = 0
        self.auto_flushes = 0     # 因达到 max_bytes 触发的 flush（非显式）

    # ── 内核用到的接口 ──
    def sendall(self, data):
        if not data:
            return None
        self._buf += data
        self.frames += 1
        if len(self._buf) >= self._max:
            self.auto_flushes += 1
            self.flush()
        return None

    def recv(self, n):
        # ★ 约束 1：读之前必须把攒的帧放出去，绝不跨等待合批。
        #   n<=0 表示调用方只想要「没有也立即返回」的探测读（测试/心跳），
        #   底层 socket 对这种 n 行为未定义 —— 仍先 flush，再交给底层决定。
        self.flush()
        return self._conn.recv(n)

    def flush(self):
        if not self._buf:
            return 0
        data = bytes(self._buf)
        self._buf = bytearray()
        self._conn.sendall(data)
        self.writes += 1
        self.bytes += len(data)
        return len(data)

    # ── 透传（内核还用到 settimeout / close / fileno 等）──
    def settimeout(self, t):
        c = getattr(self._conn, 'settimeout', None)
        return c(t) if c else None

    def close(self):
        try:
            self.flush()
        except Exception:                      # noqa: BLE001
            pass
        c = getattr(self._conn, 'close', None)
        return c() if c else None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    # ── 观测 ──
    def batch_stats(self):
        """给调试台看：帧数 / 写次数 / 合并率。pending>0 说明有帧被压着没发。"""
        return {'enabled': True,
                'frames': self.frames,
                'writes': self.writes,
                'bytes': self.bytes,
                'pending': len(self._buf),
                'auto_flushes': self.auto_flushes,
                'saved': (self.frames - self.writes) if self.frames else 0,
                'ratio': (round(float(self.frames) / self.writes, 2)
                          if self.writes else 0.0)}


# ───────────────────────────────────────── 服务 ──────────────────────────
class _SaveDone(object):
    """`flush_saves()` 的完成哨兵：写线程见到它就置位事件。

    为什么不用 threading.Event 直接塞队列：队列里混着两种元素
    （(data, dirty) 二元组和哨兵），类型必须能区分。哨兵对象比
    「塞个 None 再特判」更直白，也不会和「停止哨兵 None」撞车。
    """

    def __init__(self):
        self.event = threading.Event()


# 经济资源 → (PropChange 属性下标, 字段宽度)。P3-05 收口后由 _econ_publish 统一发帧，
# 免得每个业务函数各写一遍 build_prop_change（写错宽度就是「客户端数值不刷新」）。
_ECON_PROP = {
    eled.KIND_COIN: (PROP_IDX_GOLD, 4),
    eled.KIND_HUOLI: (PROP_IDX_HUOLI, 2),
    eled.KIND_TILI: (PROP_IDX_TILI, 2),
    eled.KIND_ITEM: (None, 0),          # 物品走 ItemAdd/ItemQuantity，不是 PropChange
}

# 经济资源 → 总线事件名。沿用既有 'econ.coins'，调试台页签不用改。
_ECON_EVENT = {
    eled.KIND_COIN: 'econ.coins',
    eled.KIND_HUOLI: 'econ.huoli',
    eled.KIND_TILI: 'econ.tili',
}


class AccountInUseError(RuntimeError):
    """同账号已有活跃会话；这是受控拒绝，不是服务端异常。"""


class M4Server:
    def __init__(self, host='127.0.0.1', port=3134, seed=SEED_DEFAULT,
                 encounter_every=5, logdir=LOGDIR, enemies=ENEMY_COUNT_DEFAULT,
                 turn_timeout=TURN_TIMEOUT, savedir=None, encrypt=False):
        self.host, self.port, self.seed = host, port, seed
        # ★ P3-03：战斗随机源（固定种子 + 流分离）。
        #   以前战斗里 12 处直接用模块级 `random.*`，同一场战斗重放结果不同，
        #   「刚才那次为什么赢」无法回答。现在统一走这个实例，同种子可逐位复现。
        #   `battle_rng.enabled = False` 可退化回模块级 random（仅供兼容旧脚本）。
        self.battle_rng = brng.BattleRng(self.seed)
        self.encrypt = bool(encrypt)        # 原版 DLL（加密+压缩）通道；见 handle()
        self.encounter_every = encounter_every
        # 敌方招式（录像复刻）：概率用招式替代普攻；实例属性便于测试注入
        self.enemy_skill_chance = ENEMY_SKILL_CHANCE
        self.enemy_skills = list(ENEMY_SKILLS)
        # 敌方命中会心（红字）概率。两种用法，别混：
        #   None      → 每次开战按 ENEMY_HURT_BS_BY_SHAPE / ENEMY_HURT_BS_CHANCE 现算
        #   浮点数    → 钉死本值（测试注入用，0 = 全普通橙字、1 = 必红）
        # 实际参与掷骰的是 `_hurt_bs_chance`（开战时解析出来的本场值）。此前只用一个
        # 属性既当配置又当结果，`_start_war` 会把测试注入的值覆盖掉 —— 靠造型表恰好等于
        # 注入值才没暴露，表一空就会变成随机失败。
        self.enemy_hurt_bs_chance = None
        self._hurt_bs_chance = ENEMY_HURT_BS_CHANCE
        self.enemies = max(1, int(enemies))
        self.turn_timeout = max(5, int(turn_timeout))
        # 单机架构：存档目录可注入（默认仍是按 __file__ 推算的 服务端源码/save）
        self.savedir = savedir or SAVE_DIR
        # 多角色：默认仍是单角色存档 role.json（向后兼容既有存档）；
        # 登录账号确定后由 _bind_account() 切到 role_<账号>.json。
        self.account = ''
        # 多会话宿主注入的账号所有权绑定器；None 表示独立/单会话运行。
        # 返回 False 时说明同账号已有活跃会话，本连接必须在读档前被拒绝。
        self._account_binder = None
        self.savepath = os.path.join(self.savedir, 'role.json')
        self._logdir = logdir or LOGDIR
        self._session_tag = ''        # 并发日志文件名后缀（账号/连接序号）
        # 存档写者标识：随存档落盘，用于识别「同一账号被另一个会话覆盖」。
        # 每次 new_session() 都不同（进程内递增），因此两个会话天然可分。
        self._save_tag = 'sess-%d-%d' % (os.getpid(), id(self) & 0xFFFFFF)
        self._save_conflict = False   # 是否发生过「本会话覆盖他人进度」
        # 最近一次落盘的**确定结果**（True/False/None=还没写过）。异步模式下
        # `_persist()` 只回报「已排队」，不可逆交付（宠物入栏）要等写完才知道
        # 成没成功 —— flush_saves 返回时这个值必然已更新（哨兵在写完后才置位）。
        self._save_last_ok = None
        # 最近一次 `_add_pet` 的失败原因（None=成功/未调用）：'bad_shape' /
        # 'full' / 'save_blocked' / 'persist_failed'。GM 面板与测试据此如实上报，
        # 不把「没入栏」伪装成别的。
        self._add_pet_last_error = None
        # ── 存档：增量 + 异步（2026-10-22）──
        # 增量：_save_snapshot 记上次落盘的快照，_persist 只把**变化的键**交给写线程；
        #   全同则整个跳过（_save_noop 计数，能看到省下多少无效 IO）。
        # 异步：_save_queue 里只保留最新快照，写线程合并连续请求后一次落盘
        #   （_save_coalesced 计数）。同步路径因此只剩「快照比对」这点 CPU。
        # ★ 关闭异步 = 逐次同步落盘（回归/离线工具用；默认开）。
        self._save_async = SAVE_ASYNC
        self._save_snapshot = {}
        # ★ 最后一次**成功写盘**的快照（2026-10-23 P1）。三个用途：
        #   ① 写失败时把 _save_snapshot 退回它，否则失败态的基线留在那里，
        #      调用方回滚内存后再做同一操作会被判成「无变化」→ 永远写不出去；
        #   ② noop 分支据此判定「磁盘确定就是这份状态」，只有**同一对象**
        #      （或更早的同一份）才敢对交付方回 True —— 异步队列里还压着
        #      一份更晚的快照时二者不同一，必须回 None 让调用方 flush 后取
        #      _save_last_ok 的真实结果；
        #   ③ 调试台/测试据此区分「内存基线」与「磁盘基线」。
        self._save_disk_snapshot = None
        # 本会话最后一次实际观察到的磁盘写者。它与当前 `_save_tag` 分开：
        # 进程重启/顺序重连载入旧档时，旧 tag 是合法基线；只有磁盘 tag 在
        # 本会话观察之后再次变化，才说明有别的活跃写者抢先落盘。
        self._save_expected_tag = None
        self._save_dirty = []
        self._save_pending = 0
        self._save_lock = threading.Lock()
        self._save_writes = 0
        self._save_coalesced = 0
        self._save_noop = 0
        self._save_queue = None
        self._save_thread = None
        # 最近一次由本会话成功写入后的文件指纹。增量落盘遇到「内存无变化」时
        # 仍用它做一次廉价 stat，避免另一会话改过文件后被 noop 静默吞掉。
        self._save_file_stamp = None
        # 单机架构：监听就绪事件（serve_background 等待真正 bind/listen 完成）

        self._ready = threading.Event()
        # 单机架构：同一时刻只允许一个客户端连接（单角色单会话）
        # ★ 多会话模式下由 MultiServer 管理并发，本标志仅 serve_background 用
        self._busy = False
        self._stop = False          # 优雅停止标志（见 stop()）
        self._listener = None
        self._atexit_armed = False
        # ★ 入场序号表（2026-10-20）：uid → spawn_index，_start_war 时固定。
        #   行动序的同速决胜用它，而不是字典插入顺序或 id 数值。
        self._spawn_index = {}
        self.hero_id = 1
        self.hero_name = ''
        self.hero_shape = DEFAULT_HERO_SHAPE   # 人物造型（建角选定；见 ROLE_SHAPES）
        self.have_role = False
        self.role_listed = False
        self.px, self.py = SPAWN_X, SPAWN_Y
        self.mapid = START_MAP_ID          # 当前地图（多地图/传送）
        self.cur_npc = None                # 最近点击的 NPC id（决定 NPC_ANSWER 路由）
        self.cur_npc_menu = None           # 当前 NPC 对话的目的地列表（随身 NPC 只列其路线目标）
        # 组队态（op72/82）：只有一支队伍，队长恒为本地玩家
        self.team = teammod.TeamState(0, '')
        self._team_war_ids = {}            # war_id → 成员 dict（本场入场的队友）
        # NPC 交互族的临时态（op97 给予入口 / op113/127 选物 / op115 输入）
        self.give_npc = None
        self.give_gtype = 0
        self.npc_chosen = None
        self.npc_input_text = None
        self.move_count = 0
        # 落脚点豁免（见 _check_exit）：刚传送到的坐标，在走出半径前不做出口判定，
        # 避免「spawn 恰在出口标记上」的图（如 1035 落雪冰原）一落地就被弹去邻图。
        self._arrive_xy = None
        self.in_war = False
        # ── 养成状态（M9，持久化）──
        # grade/points/attrs 三样都要进存档：否则「加完点下线再上」等于没加。
        # magics 只存「学过的招式 → 等级」，登录时按它逐条下发（见 _enter_world）。
        self.grade = 1
        self.points = POINT_INIT
        self.attrs = {idx: ATTR_BASE for idx, _n in ATTR_ORDER}
        self.magics = {}
        # 多单位战斗状态：{war_id: hp}；我方恒为 HERO_WAR_ID
        self.enemy_hp = {}
        # 出手顺序：{war_id: speed}（按速度降序结算回合）
        self.enemy_speed = {}
        self.enemy_speed_base = ENEMY_SPEED_BASE   # 本场实际基值（按造型取值，见 ENEMY_SPEED_BY_SHAPE）
        # 回合指令窗口：交出操作权后计时，超时默认普攻
        self.await_cmd = False
        self.cmd_deadline = 0.0
        # 回合窗口闸门（见 _accept_turn）：_cmd_consumed 标记本窗口是否已被消费，
        # _battle_round 是同一套窗口的回合计数（每开一次窗口 +1，仅日志/GM 用）。
        self._cmd_consumed = False
        self._battle_round = 0
        # Client sends the hero action first, then opens the summon menu and
        # sends a separate pet action. Keep the rest of the round until then.
        self._pending_pet_suffix = None
        self._round_defending = set()
        self._round_protectors = {}
        # ── 状态效果（P3-03）──
        #   每个参战单位一个 EffectSet：主角 / 宠物 / 每只敌方。
        #   ★ 必须是**按单位分表**，不是全局一张：全局表会让「敌方身上的燃烧」
        #     与「主角身上的燃烧」共用计时，tick 一次两边一起掉。
        #   ★ 默认全空 → `mods()` 返回中性值（乘 1.0），所以接入后
        #     录像复现值（主角 50 / 宠物 48 / 敌 20）逐位不变。
        self._effects = {}                 # war_id → combat_effects.EffectSet
        self._effects_of_hero = ceff.EffectSet(HERO_WAR_ID)
        self._effects_of_pet = ceff.EffectSet(PET_WAR_ID)
        # 敌方 AI 策略（'random' = 复刻既有行为；'focus' = 集火残血）
        self.enemy_ai_policy = eai.P_RANDOM
        # 最近一次敌方决策（GM 面板/日志用来解释「它为什么打这只」）
        self._last_enemy_decision = None
        # ★ 本场「胜负结算」次数。正常战斗**必须是 1**（胜/负/逃各算一次）。
        #   存成计数量而不是 bool，是因为「结算了两次」和「没结算」都是缺陷，
        #   bool 只能看出后者。测试与 GM 面板都断言 `<= 1`。
        self._war_settle_count = 0
        self._war_settle_kinds = []        # 按发生顺序记 'win'/'lose'/'escape'/'gm'
        # ── GM 战斗调试（2026-10-21 真·战斗系统配套）──
        #   逃跑强制：None=按公式判定 | 'success'/'fail'=必成/必败 | 0..1=按该概率
        #   _escape_last：最近一次逃跑判定（rate/forced/roll/success），面板可视化
        #   _cp_init：控制点**初始值**（每次开战重置为它）；_control_points：当前值
        self._escape_force = None
        self._escape_last = None
        self._cp_init = dict(CONTROL_POINTS.get('npc_battle_init',
                                                {'atk': 30, 'def': 30, 'dis': 30}))
        self._control_points = dict(self._cp_init)
        # 背包状态：{iid: {shape, key, qty, pos, infoext}}
        self._sell_cache = []          # 最近一次下发的收购清单（客户端按下标卖）
        self.items = {}
        self._reset_items()
        # 战斗资源（施法消耗）：{m_MP, m_FP, m_At, m_Df, m_Dp}
        self.hero_res = hero_res_fresh()
        # 宠物战斗资源（主动招式消耗）：{m_FP, m_MP}；每场战斗由 _reset_pet_res 回满
        self.pet_res = {'m_FP': int(PET_FP), 'm_MP': int(PET_MP)}
        # 宠物被动（必杀会心/连击）：置 True 整层关闭，逐帧断言的老测试要确定性
        self.pet_passive_mute = False
        # 门派基础技能等级（gslv）：本服没有养成入口，留空 = 全部 0（与客户端未学分支一致）
        self.base_skills = {}
        self.hero_hp = HERO_HP
        # 生活技能（工艺技术）+ 活力/体力：制作链的两项前置状态（持久化）
        # ★ 活力（m_Huoli, 下标24）才是工艺/烹饪/炼药的消耗口；体力归战斗，见 PROP_IDX_HUOLI 注释
        self.huoli = HERO_HUOLI_INIT
        self.tili = HERO_TILI_INIT
        self.life_lv = LIFE_LV_INIT
        self.life_exp = LIFE_EXP_INIT
        # 活力独立恢复机制的状态（基于真实在线时长，与战斗无关）
        self._huoli_ts = time.time()
        self.huoli_regen_acc = 0.0
        # ── 宠物实例表（2026-10-22）：等级/经验/资质/属性/技能都挂在个体上 ──
        #   pets = {pid: 实例 dict}；pet_hp 是**出战宠物**当前气血的运行镜像
        #   （战斗代码只碰这一个数，切宠/存盘时与实例互相回写，见 _sync_pet_runtime）。
        self.pets = {}
        self.pet_seq = PET_ID
        self.active_pet_id = 0
        self.pet_hp = 0
        self.pet_shape = PET_SHAPE_DEFAULT
        self._pet_alive = True   # 战斗中是否存活（被击倒后本场不再行动）
        self._init_pets()
        # 金钱（NPC 商店购买扣减）与物品 id 分配计数器
        self.coins = COIN_INIT
        self._iid_seq = 9000
        self._econ_reset()      # P3-05：新角色的流水从零起算
        # ── 主线剧情 1–3（2026-10-22）──
        #   tasks 里的主线条目形如 {state, progress, step, qid}；进度 = 第几步
        #   story_point = m_StoryPoint（ITEM_ALL[42]，TWORD）——资料真值给的剧情点
        #   quests_done = 已完成剧情 id（持久化，防重复领奖）
        #   _quest_battle = 剧情战斗上下文；_quest_prompt = 当前对话待确认的动作
        self.story_point = 0
        self.quests_done = set()
        # 剧情技能：{skid: level}，剧情点唯一的消费入口（见 build_skill_level_juqing）
        self.juqing_skills = {}
        self._quest_battle = None
        self._quest_prompt = None
        # ── 系统设置对话框（dialog/dlgsysopt，2026-10-22）──
        #   sys_switch = C2GS_PLAYER_SYSSWITCH sub=1 的 4 字节位图（每 bit 一个开关，
        #     顺序就是客户端 g_SystemSetting 的下标；含义见 dlgsysopt.g_SystemSetting）
        #   sys_data   = sub=2/3/4 的**字符串**（快捷键表 / 第二张表 / 图片数据），
        #     客户端 SaveSystemData 只在关卡切换时整串下发，服务端原样存即可
        #   mobile     = C2S_Mobile(12) 那套「手机安全」设置的本地镜像（S2C 58 sub=1 回读）
        #   private_info = op11 sub=2 提交的私密资料串（'idcard=…||qq=…'，原文存盘）
        self.sys_switch = 0
        self.sys_data = {}
        self.mobile = {'check': 0, 'receive': 1, 'enable_friends': 1,
                       'limit': [0, 0, 0, 0], 'degree': 0, 'money': 0, 'balance': 0}
        self.private_info = ''
        # GM 面板：当前已连接的游戏客户端（用于即时下发协议帧），及线程锁
        #
        # ★ self._lock 是**会话状态总锁**（2026-10-20 升级语义）。
        #   旧版它只保护 _conn 的取用，但 GM HTTP 线程 / 调试台心跳线程与内核
        #   战斗线程会同时读写 hero_hp / coins / items / 战斗态，并**同时向同一个
        #   socket sendall**——实测两个线程并发写同一连接，帧序交错 8 次。
        #   sendall 自身线程安全，但**协议帧序**不是：GM 的 WarStart 可能插在
        #   内核战斗帧中间，客户端按 opcode 顺序解析就错位。
        #   现在 _lock 覆盖：角色数值、背包、战斗状态机、以及一切对外发文。
        # ★ 可重入（RLock）：gm_* → _start_war/_after_hero_action 等同一条
        #   调用链上会再次取锁，普通 Lock 会自锁死。
        self._conn = None
        self._lock = threading.RLock()
        self._web = False
        self._web_port = 8080
        # ── 持久化：载入存档（跨进程重启留存角色/背包/宠物/资源）──
        self._loaded = load_role(self.savepath)
        if isinstance(self._loaded, dict):
            self._save_expected_tag = self._loaded.get('_save_tag')
        self._save_blocked = self._loaded is None and os.path.exists(self.savepath)
        if self._loaded:
            sv = self._loaded
            self.px = sv.get('px', self.px)
            self.py = sv.get('py', self.py)
            self.move_count = sv.get('move_count', self.move_count)
            self.hero_hp = sv.get('hero_hp', self.hero_hp)
            # 迁移可能把 hero_res 置 None（表示「按当前全局参数重算」，见 _migrate_1_to_2）；
            # dict 才直接用，否则回落到 hero_res_fresh()，避免拿旧参数点复活。
            _hr = sv.get('hero_res')
            self.hero_res = _hr if isinstance(_hr, dict) else hero_res_fresh()
            # 旧存档没有这两项 → 用默认值，不影响已有角色
            # 活力/体力：旧存档只有 tili 时，活力按上限给满（老角色不会一进游戏没活力可用）
            self.huoli = int(sv.get('huoli', HERO_HUOLI_INIT
                                    if 'huoli' not in sv else self.huoli))
            self.tili = int(sv.get('tili', self.tili))
            self.life_lv = int(sv.get('life_lv', self.life_lv))
            self.life_exp = int(sv.get('life_exp', self.life_exp))
            if isinstance(sv.get('items'), dict):
                self.items = sv['items']
            self._load_pets(sv)
            self.hero_shape = int(sv.get('hero_shape', self.hero_shape))
            self.coins = sv.get('coins', self.coins)
            self._iid_seq = sv.get('iid_seq', self._iid_seq)
            self.mapid = sv.get('mapid', self.mapid)
            # 养成状态：旧存档没有这些键 → 用默认值（不影响已有角色）
            self.grade = int(sv.get('grade', self.grade))
            self.points = int(sv.get('points', self.points))
            if isinstance(sv.get('attrs'), dict):
                for idx, _n in ATTR_ORDER:
                    self.attrs[idx] = int(sv['attrs'].get(str(idx), ATTR_BASE))
            if isinstance(sv.get('magics'), dict):
                self.magics = {int(k): int(v) for k, v in sv['magics'].items()}
            self.log('   ↺ 已载入存档（位置 %d,%d，HP %d，背包 %d 件，宠物HP %d）'
                     % (self.px, self.py, self.hero_hp, len(self.items), self.pet_hp))
        # ── 任务闭环（M6）/ 经验：{tid: {state, progress}} + m_Exp/m_NextExp ──
        self.tasks = {}
        self.exp = EXP_INIT
        self.next_exp = NEXT_EXP_INIT
        if self._loaded:
            self.tasks = self._loaded.get('tasks') or {}
            self.exp = self._loaded.get('exp', self.exp)
            self.next_exp = self._loaded.get('next_exp', self.next_exp)        # 登录时无条件授予的低阶招式并入「已学」表：它是基线，不是存档内容
            # 主线进度：剧情点 / 已完成列表（旧档没有这两个键 → 0 / 空集，不影响）
            self.story_point = int(self._loaded.get('story_point', 0) or 0)
            self.quests_done = {int(x) for x in
                                (self._loaded.get('quests_done') or [])}
            # 剧情技能（旧档无此键 → 空表；键是 JSON 字符串，见 normalize_tasks 同款坑）
            _jq = self._loaded.get('juqing_skills') or {}
            if isinstance(_jq, dict):
                self.juqing_skills = {int(k): max(0, int(v)) for k, v in _jq.items()
                                      if int(k) in jqmod.JUQING_SKILLS}
            # 系统设置（dlgsysopt）：旧档无这三键 → 保持默认，不报错
            self.sys_switch = int(self._loaded.get('sys_switch', 0) or 0)
            if isinstance(self._loaded.get('sys_data'), dict):
                self.sys_data = {int(k): str(v)
                                 for k, v in self._loaded['sys_data'].items()}
            if isinstance(self._loaded.get('mobile'), dict):
                self.mobile.update({k: v for k, v in self._loaded['mobile'].items()
                                    if k in self.mobile})
            self.private_info = str(self._loaded.get('private_info') or '')
        for _mid in TEACH_MAGIC_IDS:
            self.magics.setdefault(_mid, TEACH_MAGIC_LEVEL)
        # 组队态恢复：队长身份在建角/选角时才确定，这里只恢复文案与开关，
        # 陪同队友随后由 _team_state() 按当前身份重建
        if self._loaded and isinstance(self._loaded.get('team'), dict):
            tv = self._loaded['team']
            if tv.get('active'):
                self.team.active = True
                self.team.title = tv.get('title') or self.team.title
                self.team.min_lv = int(tv.get('min_lv', 0))
                self.team.max_lv = int(tv.get('max_lv', teammod.TEAM_MAX_MEMBERS))
                self.team.attr_class = int(tv.get('attr_class', 0))
                self.team.message = tv.get('message') or ''
                self.team.lineup = int(tv.get('lineup', 0))
                self.team.target = int(tv.get('target', 0))
        # 队伍是「活的」就算 role.json 里没存：因为队长身份以建角/选角为准，
        # 这时候只需要 active+文案，成员将由 _team_state() 在建角后补。
        if self.team.active and not self.team.members:
            self._team_state()
        os.makedirs(logdir, exist_ok=True)
        self.logpath = os.path.join(logdir, 'm4_srv_%s.log' % time.strftime('%Y%m%d_%H%M%S'))
        self._logf = open(self.logpath, 'a', encoding='utf-8')
        if self._save_blocked:
            self.log('   ⚠ 现有存档无法读取，已禁用自动保存以保护原文件：%s'
                     % self.savepath)
        # 帧批合并器：由 run() 在接入时装上；直调 handle（测试/内存对）时为 None。
        # 在 __init__ 里先占位，避免属性不存在导致 _flush_out 抛 AttributeError。
        self._batcher = None
        # 连接关闭后保留最后一段统计；否则 run() 的 finally 清空 _batcher 与
        # 调试台/测试读取之间存在竞态，断线瞬间统计会随机退回 disabled。
        self._last_batch_stats = {'enabled': False}

    def log(self, s):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
        # 多会话并发时给每行加会话标识，否则两个客户端的日志会混在一起看不出谁是谁
        tag = getattr(self, '_session_tag', '')
        if tag:
            line = '[%s] %s' % (tag, line)
        # ★ 解释器关闭阶段禁止再碰 stdout（2026-10-20）。
        #   根因（实测抓到）：daemon 线程在 shutdown 时仍在 print，与主线程
        #   争 stdout 的 BufferedWriter 锁，Python 直接：
        #     Fatal Python error: _enter_buffered_busy: could not acquire lock
        #     for <_io.BufferedWriter name='<stdout>'> at interpreter shutdown
        #   进程以 0xC0000409 退出——**测试其实已经 PASS**，崩在收尾。
        #   用模块级 _SHUTTING_DOWN 标志：atexit 里置位，此后只写文件不写控制台。
        if not _SHUTTING_DOWN:
            try:
                print(line, flush=True)
            except (UnicodeEncodeError, ValueError, OSError):
                # UnicodeEncodeError：Windows GBK 控制台编不了 ✓/✗
                # ValueError/OSError：流已关闭（关闭阶段兜底）
                try:
                    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
                    print(line.encode(enc, 'replace').decode(enc, 'replace'),
                          flush=True)
                except (ValueError, OSError, AttributeError):
                    pass
        if getattr(self, '_logf', None) is not None:
            try:
                self._logf.write(line + '\n')
                self._logf.flush()
            except (ValueError, OSError):
                pass

    def reload_world_config(self):
        """热加载 world_config.json：编辑器保存后调用，无需重启。

        2026-10-20：旧流程是「编辑器只写文件，改完要重启服务端才生效」。
        这里改为「编辑器保存 -> 通知内核 -> 内核重载 -> 返回结果给编辑器」。

        安全保证：
          - 校验不过整体跳过（与 import 期同口径），保留旧配置不崩；
          - 重载在会话锁内执行（与 GM 操作互斥，不与战斗线程抢发文）；
          - 只覆盖常量，不重建运行时对象（items/hero_hp 等角色状态不动）；
          - 重载后重算伤害快照常量（ATTACK_DMG 等），避免快照与配置不一致。

        返回 {ok, message, version, applied_fields}。
        """
        with self._lock:
            import world_config as _wc
            _ok, _msg = _wc.apply_to(sys.modules[__name__])
            if _ok:
                # 重算伤害快照常量：它们引用 HERO_AT 等，配置改了必须重算。
                global ATTACK_DMG, ENEMY_ATTACK_DMG, PET_DAMAGE
                ATTACK_DMG = _default_dmg(HERO_AT, ENEMY_DF, HERO_DP, ENEMY_LEVEL)
                ENEMY_ATTACK_DMG = _default_dmg(ENEMY_AT, HERO_DF, ENEMY_DP, HERO_LEVEL)
                PET_DAMAGE = _default_dmg(PET_AT, ENEMY_DF, PET_DP, ENEMY_LEVEL)
                _ver = getattr(sys.modules[__name__], 'WORLD_CONFIG_VERSION', 0)
                _upd = getattr(sys.modules[__name__], 'WORLD_CONFIG_UPDATED', '')
                WORLD_CONFIG_STATE.update(
                    applied=True, reason='applied', message=_msg,
                    version=_ver, updated=_upd)
                self.log('   [reload] %s (v%d)' % (_msg, _ver))
                return {'ok': True, 'message': _msg,
                        'version': _ver, 'updated': _upd}
            else:
                WORLD_CONFIG_STATE.update(
                    applied=False, reason='failed', message=_msg)
                self.log('   [reload] FAIL %s' % _msg)
                return {'ok': False, 'message': _msg,
                        'version': WORLD_CONFIG_STATE.get('version', 0)}

    def _log_dmg_banner(self):
        """把伤害模型 + world_config 加载结果打进本会话日志（每会话一次）。

        放在 run() 而不是 __init__：多会话模式下 __init__ 每连一个客户端跑一次，
        横幅会重复刷屏；而 run() 只在真正起监听时调用一次。
        """
        for line in dmg_banner().split(chr(10)):
            self.log(line)

    def _send_role_list(self, conn):
        conn.sendall(build_login_account_info(1))
        conn.sendall(make_s2c(OP_ROLE_INFO,
                              struct.pack('<I', self.hero_id) + struct.pack('<I', 0)
                              + struct.pack('<B', 1)
                              + struct.pack('<H', self.hero_shape)  # Shape：人物造型码
                              + struct.pack('<H', 0)                # Desc
                              + struct.pack('<B', 1) + struct.pack('<B', 0) + struct.pack('<B', 1)
                              + struct.pack('<i', 0)
                              + self.hero_name.encode('gbk', 'replace')[:16].ljust(16, b'\x00')))
        self.role_listed = True
        self.log('   已回 AccountInfo(1)+RoleInfo')

    def _enter_world(self, conn):
        """进世界序列：PropInit → PropResist/Power → 技能 → SceneM → AddNpc → GotoM。"""
        conn.sendall(build_prop_init(self.hero_id, self.hero_name, self.hero_shape))
        # ★ PropInit 只含 ITEM_ALL[:-8]；末 8 项（5 抗性 + m_At/m_Df/m_Dp）必须由
        #   S2CPropResist(18)/S2CPropPower(19) 单独下发，否则客户端 `hero.CreateProp`
        #   把它们留成 `Default()`=0 → 任何 `df>0` 的招式都被判 invalid →「无法使用这个招式」。
        conn.sendall(build_prop_resist())
        conn.sendall(build_prop_power())
        # 养成状态补齐（等级/经验/点数/五维/金钱）——必须在 PropInit 之后：
        # 那帧的初值是写死的（1 级、0 经验、1000 金），不覆盖就等于每次重登回档。
        self._send_progression(conn)
        # 活力 + 体力 + 工艺技术（M13）：制作链的前置状态，必须在 PropInit 之后覆盖
        # 活力是工艺/烹饪/炼药的消耗口（见 PROP_IDX_HUOLI 注释），必须显式下发，
        # 否则客户端的 m_Huoli 停在 PropInit 的初值，制作面板永远显示满活力
        conn.sendall(build_prop_change(PROP_IDX_HUOLI, self.huoli, 2))
        conn.sendall(build_prop_change(PROP_IDX_HUOLI_MAX, HERO_HUOLI_MAX, 2))
        conn.sendall(build_prop_change(PROP_IDX_TILI, self.tili, 2))
        conn.sendall(build_prop_change(PROP_IDX_TILI_MAX, HERO_TILI_MAX, 2))
        conn.sendall(build_skill_level_life(
            LIFE_SKILL_CRAFT, self.life_lv, LIFE_LV_MAX, self.life_lv,
            self.life_exp, life_exp_need(self.life_lv)))
        # 重置战斗资源基线（PropInit/PropPower 已把 m_MP/m_FP/m_At/m_Df/m_Dp 送到初值）
        self._reset_hero_res()
        # 授予并**装备**招式，使战斗「招式」菜单有真实多选项
        # （否则 g_Skills[ST_MAGICREADY] 为空 → 菜单只剩「攻击」）
        # ★ M9：以 self.magics 为准（= 基线 8 式 + 玩家向师傅学的），
        #   否则学过的招式下线即丢。
        for _mid, _lv in sorted(self.magics.items()):
            conn.sendall(build_skill_level_magic(_mid, _lv))
            conn.sendall(build_skill_equip(_mid, _lv))
        # 剧情技能：已学的逐条补发 S2CSkillLevel(sktype=3)。
        # 不补的后果：重登后剧情技能从 g_Skills 里消失，「花了剧情点学的等级」看不见了。
        for _skid, _lv in sorted(self.juqing_skills.items()):
            conn.sendall(build_skill_level_juqing(_skid, _lv))
        self._send_scene(conn, self.px, self.py)
        # 任务栏恢复：主线 1–6 都是「接了就跨图跑」的长链，重登不推就等于丢失进度。
        self._send_all_tasks(conn)
        # 携宠：**逐只**下发 SummonInfo(48) 建宠 + SummonSkill(49) 技能格 + SummonKey(53)
        # 标记，最后 S2CSetFight(50) 指出谁在出战。
        # ★ 客户端 g_Pets 只由 48 建出来：少发一只，重登后那只就「不见了」。
        # 资质（op51）由客户端打开面板时以 C2S_SUMM_VIEW(129) 主动请求。
        self._send_all_pets(conn)
        # 背包：逐件 S2CItemAdd（无「整体背包」帧，服务端按件推送）
        for iid, info in sorted(self.items.items()):
            conn.sendall(build_item_add(ITEM_WTYPE_NORMAL, iid, info['shape'],
                                        info['key'], info['qty'], info['pos']))
        self.log('   进世界：PropInit + Resist/Power + 养成同步 + 招式%d + 场景 %s(%d) + GotoM'
                 % (len(self.magics), self._map_def()['name'], self.mapid))
        self.log('   + 背包 ItemAdd×%d（背包 pos=%d.. 战斗栏 pos=%d..）'
                 % (len(self.items), ITEM_POS_BAG_START, ITEM_POS_FIGHT_START))

    def _encounter_def(self):
        """当前地图的遇怪定义；不在场景表内（城镇/室内）返回 None。

        ★ 2026-10-22：场景表来自《梦想世界2013资料全库》暗雷/明雷场景表
          （`encounter_data.MAP_ENCOUNTERS`，43 个野外/迷宫场景，124 条怪物，
          名字全部解析到客户端 monsterdata 的真实 shape）。
          城镇/室内（17 张图）**不在表内 → 不遇怪**，这才是原版行为；
          之前「任何地图走到第 N 步都刷野狼」是模拟服的占位实现。
        """
        if not ENCOUNTER_PER_MAP:
            return None
        return ENCOUNTER_BY_MAP.get(int(self.mapid))

    def _encounter_allowed_here(self):
        """本图是否允许踩雷。

        per_map=False → 旧行为：任何地图都能遇怪（回归测试/录像复刻用）。
        per_map=True  → 只有场景表内的野外/迷宫能遇怪；城镇/室内不遇怪。
        """
        if not ENCOUNTER_PER_MAP:
            return True
        return self._encounter_def() is not None

    def _start_war(self, conn):
        """遇怪：WarStart + WarAdd(我方+已激活宠物) + WarAdd(敌方×N) + WarTurn。

        ★ 开战不可重入：客户端每收到一次 WarStart 会把 `g_WarTimes` +1，而 WarEnd
          只在 `g_WarTimes > 0` 时才处理。已经在战斗中再开一场，会让计数只增不减，
          后续 WarEnd 全被丢掉，客户端卡死在「请等待」。调用方（遇怪/GM/剧情）
          本来都有判断，这里再兜一层，避免新调用点漏判后污染整场状态。
        """
        if self.in_war:
            self.log('   ⚠ 已在战斗中，忽略重复开战（WarStart 不可重入）')
            return False
        self.in_war = True
        self._pending_pet_suffix = None
        self._cmd_consumed = False
        self._battle_round = 0
        # ★ P3-03：本场随机序列从「主种子 + 场次」重新起算。
        #   每场独立 → 「第 3 场的第 1 次掉落」不会被第 1 场的历史挤位，
        #   所以「同种子重跑整局」得到的战斗过程逐场相同。
        self.battle_rng.begin_battle()
        # 状态效果同样每场重来（上一场的燃烧不能带进这一场）
        self._fx_clear_all()
        # 结算计数清零：只在开战时清，战后保留下来供 GM/测试查看「这场结算了几次」
        self._war_settle_count = 0
        self._war_settle_kinds = []
        # 上一场的队友入场记录必须清掉：否则战斗数据残留会让「本场上场单位」
        # 越攒越多（玩家/宠物会重复出现）。成员本身还在队里，只是不上场。
        self._team_war_ids = {}
        # 本模拟服的简化：每场战斗开始回满血，并同步给客户端 `g_Prop.m_HP`，
        # 避免「战斗内 hero_hp 已重置、而 g_Prop.m_HP 仍是上场残血」的不一致。
        self.hero_hp = HERO_HP
        # 出战判定：实例表里有这只、且当前气血 > 0 才入场（倒地宠物不会跟着打）
        self._pet_alive = (self.active_pet_id in self.pets
                           and int(self.pet_hp) > 0)
        if self.active_pet_id in self.pets and not self._pet_alive:
            self.log('   ⚠ 出战宠物 id=%d 气血为 0，本场不参战' % self.active_pet_id)
        conn.sendall(build_prop_change_hp(self.hero_hp))
        # 战斗资源同样回满（与回血同口径），并把当前值同步给客户端
        self._reset_hero_res()
        for (name, idx, width) in COST_PROPS:
            conn.sendall(build_prop_change(idx, self.hero_res[name], width))
        # 宠物资源池（主动招式的 FP/MP 消耗）每战回满。
        # ★ 这里**不发** S2CSummonInfo：客户端手上的宠物 FP 也是满的
        #   （上一场结束的 `_resync_scene` 已按 pet_base_dict 重发过），
        #   在 WarStart 前面塞一帧 48 只会打乱「遇怪帧序」这条被多处逐帧断言的链路。
        self._reset_pet_res()
        # 逃跑失败标记（本回合逃跑失败 → 敌方获得免费攻击回合）
        self._escape_failed = False
        # 控制点系统（真值：资料库第 9128-9134 行）
        #   对NPC战斗：初始各30点；对玩家战斗：初始各0点
        #   攻击类招式消耗攻击点，回复类消耗防御点，封印类消耗干扰点
        #   装备特效影响回复速度（暂简化为每回合各回复1点，封顶30）
        # ★ 初始值取 GM 可调的 _cp_init（gm_set_cp 改的就是它），不是每次硬编码 30。
        self._is_npc_battle = True
        init_cp = (getattr(self, '_cp_init', None)
                   or CONTROL_POINTS.get('npc_battle_init',
                                         {'atk': 30, 'def': 30, 'dis': 30}))
        self._control_points = {'atk': int(init_cp.get('atk', 30)),
                                'def': int(init_cp.get('def', 30)),
                                'dis': int(init_cp.get('dis', 30))}
        # ★ 敌方元数据（2026-10-20）：每只敌方的「入场序号 / 速度 / 造型」等
        #   一次性写进 enemy_meta，**不再靠 enemy_hp 的字典插入序或 sorted() 推算**。
        #
        #   为什么单独建表：
        #     · spawn_index 是「这只怪是本场第几只入场的」，是**事实**，不是
        #       从 dict 顺序里临时算出来的派生值。存下来才不会因为别人重排
        #       enemy_hp（存档还原、调试脚本、字典推导改写）而变。
        #     · 速度也挂在这里：它由 spawn_index 决定（每只递减一个 STEP），
        #       同源同表，不会出现「速度按一个顺序算、序号按另一个顺序排」。
        #
        #   字段：spawn_index（本场入场序，0 起）、speed、shape、name
        #
        # ★ 2026-10-22：按当前地图的场景表选怪（城镇/室内不遇怪，见 _encounter_def）。
        #   怪物名与 shape 是真值；HP 仍取 ENEMY_HP（模拟代表值，原服怪 HP 不可得）。
        _enc = self._encounter_def()
        _e_name, _e_shape, _e_hp = ENEMY_NAME, ENEMY_SHAPE, ENEMY_HP
        self._enc_scene = None
        _qb = getattr(self, '_quest_battle', None)
        if _qb:
            # 剧情战斗优先：怪物名/造型由 quest_data 给出（造型为 SIM，已标注）
            _e_name, _e_shape = _qb['name'], int(_qb['shape'])
            self._enc_scene = '剧情'
        elif _enc and _enc.get('monsters'):
            # 遇怪选怪走 encounter 流：同种子同场次 → 同一只怪
            _e_name, _e_shape = self.battle_rng.choice(brng.S_ENCOUNTER,
                                                       _enc['monsters'])
            _e_shape = int(_e_shape)
            self._enc_scene = _enc.get('scene')
        self.enemy_meta = {}
        for _i in range(self.enemies):
            _wid = ENEMY_WAR_ID + _i
            self.enemy_meta[_wid] = {
                'spawn_index': _i,          # ← 分配点：i 就是入场序，不做排序
                'speed': None,              # ← 下面按 spawn_index 算完填入
                'shape': _e_shape,
                'name': _e_name,
            }
        self.enemy_hp = {wid: _e_hp for wid in self.enemy_meta}
        # 行动序的同速决胜表：主角 0、宠物 1、敌方 = 2 + spawn_index
        self._spawn_index = {HERO_WAR_ID: 0}
        if self._pet_alive:
            self._spawn_index[PET_WAR_ID] = 1
        for _wid, _meta in self.enemy_meta.items():
            self._spawn_index[_wid] = 2 + _meta['spawn_index']
        # 出手顺序：每个敌方一个速度（按造型取基值，再逐个递减），与主角速度交错
        # 下限钳制：录像只覆盖到 6 敌（最大阵容），再多没有真值可依；按 -5 递减到第 9 只
        # 会降到 0 甚至负数（野狼基值 40），速度 ≤0 无意义，故钳到 1（仍排在最后）。
        self.enemy_speed_base = ENEMY_SPEED_BY_SHAPE.get(_e_shape, ENEMY_SPEED_BASE)
        # 技能同口径：按造型取技能库，未登记造型回落 ENEMY_SKILLS
        self.enemy_skills = list(ENEMY_SKILLS_BY_SHAPE.get(_e_shape, ENEMY_SKILLS))
        # 会心：显式注入优先，否则按造型表取，表空则全局概率（表为什么空见常量注释）
        if self.enemy_hurt_bs_chance is None:
            self._hurt_bs_chance = ENEMY_HURT_BS_BY_SHAPE.get(_e_shape,
                                                             ENEMY_HURT_BS_CHANCE)
        else:
            self._hurt_bs_chance = self.enemy_hurt_bs_chance
        # 速度按 spawn_index 逐只递减（第 0 只最快），写回 enemy_meta 与 enemy_speed。
        # ★ 不再用 zip(sorted(enemy_hp), speeds) 配对——那是「靠排序把两列对齐」，
        #   一旦某处重排就错位。现在每只的 spawn_index 是自己的字段，直接算。
        _raw_speeds = []
        for _wid, _meta in self.enemy_meta.items():
            _sp = self.enemy_speed_base - _meta['spawn_index'] * ENEMY_SPEED_STEP
            _raw_speeds.append(_sp)
            _meta['speed'] = max(ENEMY_SPEED_MIN, _sp)
        if min(_raw_speeds) <= 0:
            self.log('   ⚠ 敌方 %d 只时最低速度 %d ≤0，已钳到 %d（录像最大阵容 6，超出部分无真值）'
                     % (len(_raw_speeds), min(_raw_speeds), ENEMY_SPEED_MIN))
        self.enemy_speed = {wid: meta['speed']
                            for wid, meta in self.enemy_meta.items()}
        # 战斗场景 = 当前地图（客户端读时 +1001，故传 mapid-1001）
        conn.sendall(build_war_start(self.mapid - 1001, 1, 1, 0, self.px, self.py))
        conn.sendall(build_war_add(HERO_WAR_ID, self.hero_shape, True, self.hero_name or '你'))
        if self._pet_alive:
            # owner_id=主角 → 客户端把宠物排在主角前排（详见 build_war_add 注释）
            _pname, _pshape, _pdef = self._pet_info()
            conn.sendall(build_war_add(PET_WAR_ID, _pshape, True, _pname,
                                       owner_id=HERO_WAR_ID))
            # 标记为「可指挥的召唤单位」→ 主角选完动作后弹宠物指令菜单（op129）
            conn.sendall(build_war_summon(PET_WAR_ID))
        # 队友作为己方单位入场（只做表现，不参与行动序——见 _speed_order 注释）
        self._team_war_ids = {}
        st = self._team_state()
        if st.active:
            for i, m in enumerate(st.members[1:TEAM_WAR_SLOTS + 1]):
                wid = TEAM_WAR_ID + i
                self._team_war_ids[wid] = m
                conn.sendall(build_war_add(wid, TEAM_ALLY_SHAPE, True,
                                           m.get('name', '队友')))
        # WarAdd 顺序 = 入场序（客户端按此顺序摆在战场格位上）
        for wid in self._enemy_ids():
            _meta = self.enemy_meta.get(wid, {})
            conn.sendall(build_war_add(wid,
                                       _meta.get('shape', ENEMY_SHAPE), False,
                                       _meta.get('name', ENEMY_NAME)))
        conn.sendall(build_war_turn(0))
        self._arm_cmd_timer()
        self.log('   遇怪！WarStart + WarAdd(主角%d%s) + WarAdd(敌方×%d ids=%s) + WarTurn%s'
                 % (HERO_WAR_ID,
                    ' + 宠物%d(WarAdd+召唤129)' % PET_WAR_ID if self._pet_alive else '',
                    self.enemies, self._enemy_ids(),
                    '  [场景 %s / %s×%d]' % (self._enc_scene or '-', _e_name, self.enemies)
                    if self._enc_scene else ''))
        self.log('   速度序：%s' % self._speed_order_desc())
        self._emit('war.start', '进入战斗：%s×%d%s'
                   % (_e_name, self.enemies,
                      '（剧情）' if _qb else (' @%s' % self._enc_scene if self._enc_scene else '')),
                   enemy=_e_name, shape=int(_e_shape), n=self.enemies,
                   scene=self._enc_scene, mapid=self.mapid, quest=bool(_qb))
        return True

    def _enemy_ids(self):
        """本场敌方 id，按**入场序**（spawn_index）排列。

        ★ 2026-10-20：不再用 sorted(enemy_hp)。排序依赖 id 数值与入场序恰好一致
          这个巧合——目前 ENEMY_WAR_ID+i 确实是升序分配的，但那是**分配方式**
          的副作用，不是契约。改成读 enemy_meta 里的 spawn_index 才是显式的。
        """
        if not getattr(self, 'enemy_meta', None):
            return sorted(self.enemy_hp)
        return [wid for wid, _m in sorted(self.enemy_meta.items(),
                                          key=lambda kv: kv[1]['spawn_index'])
                if wid in self.enemy_hp]

    def _resolve_target(self, want):
        """把客户端指定的目标 id 映射到一个存活敌方；无效则退回首个存活敌方。

        want=None 表示客户端未指定（如招式帧只带施法者 id），此时静默取首个存活敌方。
        """
        if want is not None and want in self.enemy_hp and self.enemy_hp[want] > 0:
            return want
        alive = [w for w in self._enemy_ids() if self.enemy_hp[w] > 0]
        if not alive:
            return None
        if want is not None:
            self.log('   ⚠ 目标 id=%s 非本场存活敌方 → 改为 %d' % (want, alive[0]))
        return alive[0]

    def _damage_frames(self, conn, wid, dmg, hurt=HURT_NONE):
        """对**敌方** wid 造成 dmg 伤害的表现帧序列（含击杀）。

        ★ 三个位域/符号约定均由客户端源码定死（详见 docs/M4_协议规格.md §3.1）：
            ward = WARD_HIT(1) —— 否则 BeginHit() 会走 Evade() 分支演成「闪避」；
            hp_chg = +dmg     —— 正数=扣血（负数会被 CreateModifyHP 当成回血）；
            life  = LIFE_DEAD(2) —— 只有 ==2 客户端才调 obj.Die() 播死亡动画。

        本函数**不发** `HPPercent`(125)：客户端 `war.HPPercent` 直接调
        `obj.m_HPFull.SetRate(...)`，而 `m_HPFull` 仅在 `warrior.Create` 的
        `if allied or war.g_WarScene == 65535` 分支创建 —— 敌方单位没有血条对象，
        对敌方发本帧会抛 AttributeError（被 `war.Process` 的 try/except 吞掉后 systrace）。
        敌方血条本就不渲染；主角掉血另见 `_enemy_turn`。

        返回 True 表示该单位已死亡。

        ★ 已死单位（hp<=0 但还没被 `_after_hero_action` 清出去）**不再结算**：
          群攻里同一发伤害可能落到已被前一发打死的怪身上，再扣一次会削成负数，
          并且多发一条 life=DEAD 的 WState（客户端会重播死亡动画）。
        """
        if wid not in self.enemy_hp:
            return False
        if self.enemy_hp.get(wid, 0) <= 0:
            return False
        self.enemy_hp[wid] -= dmg
        dead = self.enemy_hp[wid] <= 0
        life = LIFE_DEAD if dead else LIFE_NORMAL
        conn.sendall(build_war_wstate(wid, hurt, life, WARD_HIT, dmg * HP_CHG_DAMAGE_SIGN))
        return dead

    def _arm_cmd_timer(self):
        """交出操作权：启动本回合指令倒计时（对齐客户端 warui.ShowTimer(30)）。

        同时重开本回合的指令窗口：清零「已消费」标记、回合数 +1。
        （窗口闸门见 _accept_turn：只有本窗口未被消费过的指令才被放行一次。）
        """
        self.await_cmd = True
        self._cmd_consumed = False
        self._battle_round = getattr(self, '_battle_round', 0) + 1
        self.cmd_deadline = time.time() + self.turn_timeout

    def _accept_turn(self):
        """回合窗口闸门：仅在「战斗中 + 等待指令 + 本回合窗口未消费」时放行一次。

        同时提供**行动合法性**与**重复指令防护**两层保障：
          · 合法性 —— in_war 且 await_cmd 为真才能行动；窗口已关闭 / 战斗已结束 /
            处于结算途中的指令一律忽略，绝不误推进回合或误扣资源；
          · 防护 —— 同一回合窗口内的第二次指令（重发 / 客户端双发 / 超时默认动作
            与玩家指令撞车）被拒，避免一次操作被结算两次。
        放行后标记本窗口已消费；窗口由 _arm_cmd_timer 在每回合开局重开（清零）。
        """
        if not getattr(self, 'in_war', False) or not getattr(self, 'await_cmd', False):
            return False
        if getattr(self, '_cmd_consumed', False):
            return False
        self._cmd_consumed = True
        return True

    def _mark_settled(self, kind):
        """登记一次「胜负结算」，返回是否**首次**。

        ★ 为什么需要显式计数：`_after_hero_action` 已有 `if not self.in_war`
          幂等守卫，但守卫只能挡住「已经收尾之后的重复调用」；它挡不住
          「同一批死亡单位在收尾**过程中**被二次结算」——那正是经验翻倍、
          掉落重复入包的经典成因。计数可以事后断言「恰好一次」，
          而不是靠「守卫看起来没问题」。

        `kind`：'win' / 'lose' / 'escape' / 'gm'。重复结算会打 warn 日志
        （带调用栈前几层），便于定位是哪条路径在重入。
        """
        self._war_settle_count += 1
        self._war_settle_kinds.append(kind)
        if self._war_settle_count == 1:
            return True
        import traceback
        stack = [f.strip() for f in traceback.format_stack()[-4:-1]]
        self.log('   ⚠ 战斗结算重复：第 %d 次（kind=%s，前次=%s）'
                 % (self._war_settle_count, kind,
                    '/'.join(self._war_settle_kinds[:-1])))
        for ln in stack:
            self.log('      %s' % ln)
        return False

    # ───────────────────── 状态效果（P3-03，机制见 combat_effects）─────────────────────
    def _fx(self, wid):
        """取某参战单位的状态集（懒建）。主角/宠物固定，敌方按 war id 建。"""
        wid = int(wid)
        if wid == HERO_WAR_ID:
            return self._effects_of_hero
        if wid == PET_WAR_ID:
            return self._effects_of_pet
        es = self._effects.get(wid)
        if es is None:
            es = ceff.EffectSet(wid)
            self._effects[wid] = es
        return es

    def _fx_clear_all(self):
        """清空所有单位的状态（战斗收尾）。"""
        self._effects.clear()
        self._effects_of_hero.clear()
        self._effects_of_pet.clear()

    def _fx_mods(self, wid):
        """某单位的聚合修正（无状态时全中性，乘上去等于没乘）。"""
        return self._fx(wid).mods()

    def _ally_hp_ratio(self):
        """存活友方的当前气血比例 {war_id: 0..1}，供敌方 `focus` 策略挑选目标。

        ★ 敌方血条同理不给（客户端只能刷友方 HPPercent），这里只算友方。
        """
        out = {HERO_WAR_ID: (float(self.hero_hp) / HERO_HP if HERO_HP else 0.0)}
        if self._pet_alive:
            mx = self._pet_hpmax() or 1
            out[PET_WAR_ID] = float(self.pet_hp) / mx
        return out

    def _fx_tick_all(self):
        """回合结束：所有单位状态 -1 回合，返回 {war_id: [到期实例]}。"""
        out = {}
        for wid in [HERO_WAR_ID, PET_WAR_ID] + sorted(self._effects):
            es = self._fx(wid)
            if not es:
                continue
            exp = es.tick()
            if exp:
                out[wid] = exp
        return out

    def _fx_taken(self, wid, dmg):
        """按受击方状态修正伤害（`taken_mul`）。无状态时**原值返回**。

        ★ 无状态时返回的是同一个整数对象、同一个值 —— 所以接入后
          「主角 50 / 宠物 48 / 敌 20」逐位不变。有状态才走 `scaling` 取整。
        """
        m = self._fx_mods(wid)
        if abs(float(m['taken_mul']) - 1.0) < 1e-9:
            return dmg
        return ceff.scaling(dmg, m['taken_mul'], minimum=1)

    def _fx_force_defend(self, wid):
        """该单位是否被状态强制防御（如 #27 恐怖「强制防御1回合」）。"""
        return bool(self._fx_mods(wid)['force_defend'])

    def _fx_end_of_round(self, conn):
        """回合末结算：DOT 扣血 + 状态计时。**无状态时不发任何帧**。

        为什么必须无状态零帧：这条钩子挂在每回合收口处，而「录像复现帧序列」
        被多处逐帧断言。只要默认（没有任何状态）时一帧不发，接入就等于没接入。

        返回 {'dot': {wid: dmg}, 'expired': {wid: [eid]}, 'dead': [wid]}。
        敌方 DOT 只扣血、**不发** WState/HPPercent —— 客户端对敌对单位没有
        HPPercent 通道（会画错），敌方血条只在 WarDel 时才更新。这是已知缺口。
        """
        info = {'dot': {}, 'expired': {}, 'dead': []}
        if not self.in_war:
            return info
        # ① 持续伤害
        for wid in [HERO_WAR_ID, PET_WAR_ID] + sorted(self._effects):
            es = self._fx(wid)
            if not es:
                continue
            if wid == HERO_WAR_ID:
                hp_max, hp_cur = HERO_HP, self.hero_hp
            elif wid == PET_WAR_ID:
                hp_max, hp_cur = self._pet_hpmax(), self.pet_hp
            else:
                hp_max = hp_cur = self.enemy_hp.get(wid, 0)
            if hp_cur <= 0:
                continue
            dmg, has = es.dot_damage(hp_max, hp_cur)
            if not has:
                continue
            info['dot'][wid] = dmg
            if wid == HERO_WAR_ID:
                self.hero_hp = max(0, self.hero_hp - dmg)
                conn.sendall(build_war_wstate(HERO_WAR_ID, HURT_NONE,
                                              LIFE_NORMAL, WARD_HIT, dmg))
                conn.sendall(build_war_hp_percent(
                    HERO_WAR_ID, int(self.hero_hp * 100.0 / HERO_HP)))
                conn.sendall(build_prop_change_hp(self.hero_hp))
                if self.hero_hp <= 0:
                    info['dead'].append(HERO_WAR_ID)
            elif wid == PET_WAR_ID:
                self.pet_hp = max(0, self.pet_hp - dmg)
                conn.sendall(build_war_wstate(PET_WAR_ID, HURT_NONE,
                                              LIFE_NORMAL, WARD_HIT, dmg))
                conn.sendall(build_war_hp_percent(
                    PET_WAR_ID, int(self.pet_hp * 100.0 / self._pet_hpmax())))
                if self.pet_hp <= 0:
                    self._pet_alive = False
                    self._sync_pet_runtime()
                    conn.sendall(build_war_wstate(PET_WAR_ID, HURT_NONE,
                                                  LIFE_DEAD, WARD_HIT, 0))
                    conn.sendall(build_war_del(PET_WAR_ID))
                    info['dead'].append(PET_WAR_ID)
            else:
                self.enemy_hp[wid] = max(0, self.enemy_hp.get(wid, 0) - dmg)
                if self.enemy_hp[wid] <= 0:
                    info['dead'].append(wid)
            self.log('   状态 DOT → %d 承受 %d（%s）'
                     % (wid, dmg, es.summary()))
        # ② 计时
        for wid, exp in self._fx_tick_all().items():
            info['expired'][wid] = [e['eid'] for e in exp]
            if exp:
                self.log('   状态到期 → %d：%s'
                         % (wid, '、'.join('#%d' % e['eid'] for e in exp)))
        # ③ DOT 打死的敌方要走正常清场（WarDel → WarEnd）
        dead_enemy = [w for w in info['dead'] if w not in (HERO_WAR_ID, PET_WAR_ID)]
        if dead_enemy:
            self._after_hero_action(conn, dead_ids=dead_enemy)
        return info

    def gm_buff_add(self, wid, eid, turns=None, stacks=1):
        """GM：给单位施加状态。返回 (ok, reason, 一行摘要)。"""
        es = self._fx(wid)
        ok, reason = es.add(eid, turns=turns, stacks=stacks, source='gm')
        self.log('   GM 施加状态 #%d 给 %d → %s%s'
                 % (int(eid), int(wid), '成功' if ok else '失败',
                    '' if ok else '（%s）' % reason))
        return ok, reason, es.summary()

    def gm_buff_del(self, wid, eid=None):
        """GM：移除某单位的一个状态；eid=None 表示清空该单位全部状态。"""
        es = self._fx(wid)
        if eid is None:
            n = len(es)
            es.clear()
            self.log('   GM 清空 %d 的状态（%d 条）' % (int(wid), n))
            return n > 0, 'cleared', es.summary()
        ok = es.remove(eid)
        self.log('   GM 移除 %d 的状态 #%d → %s'
                 % (int(wid), int(eid), '成功' if ok else '本就没有'))
        return ok, 'ok' if ok else ceff.R_ABSENT, es.summary()

    def gm_buff_list(self, wid=None):
        """GM：列出单位状态（wid=None 列出所有有状态的单位）。"""
        out = {}
        wids = ([int(wid)] if wid is not None
                else [HERO_WAR_ID, PET_WAR_ID] + sorted(self._effects))
        for w in wids:
            es = self._fx(w)
            out[w] = {
                'summary': es.summary(),
                'ids': es.ids(),
                'mods': {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in es.mods().items()
                         if (isinstance(v, float) and abs(v - 1.0) > 1e-9)
                         or (not isinstance(v, float) and v)},
            }
        return out

    def _reset_war_state(self):
        """战斗状态机的**唯一收尾入口**：只清运行态，不发帧、不落盘。

        任何「战斗结束」路径（胜利 / 战败 / 逃跑成功 / GM 强制结束）都必须经过
        这里，否则会留下半套状态，而且后果都在下一场才显现：
          · `_escape_failed` 残留 → 下一场第一回合白挨一轮敌方攻击；
          · `_round_defending` / `_round_protectors` 残留 → 下一场开局自带防御/保护；
          · `_cmd_consumed` 残留 → 下一场第一次行动被误判为「重复指令」直接吞掉；
          · `_pending_pet_suffix` 残留 → 宠物窗口跨场挂起，指令串台。

        `enemy_meta` 故意保留：GM 战斗面板战后仍要显示上一场的单位造型与速度；
        它的清理只发生在下一次 `_start_war`（整表重建）。
        """
        self.in_war = False
        self.await_cmd = False
        self._cmd_consumed = False
        self._pending_pet_suffix = None
        self._team_war_ids = {}
        self._escape_failed = False
        self._round_defending.clear()
        self._round_protectors.clear()
        # 状态效果必须跟战斗一起清：否则「霸体/燃烧」会跨场带到下一场，
        # 而且玩家在 UI 上看不到它们（状态栏是按本场单位画的）。
        self._fx_clear_all()
        self._last_enemy_decision = None
        self._battle_round = 0
        self.cmd_deadline = 0.0
        self.enemy_hp = {}

    # ───────────────────────────── 物品 ─────────────────────────────
    def _reset_items(self):
        """把背包重置为 MOCK_ITEMS 初始状态。"""
        self.items = {}
        for (iid, shape, key, qty, pos, infoext) in MOCK_ITEMS:
            self.items[iid] = {'shape': shape, 'key': key, 'qty': qty,
                               'pos': pos, 'infoext': infoext}

    def _flush_out(self):
        """把帧批缓冲里攒的帧立即写出（无合批器时是空操作）。

        ★ 约束 3：GM 面板/HTTP 线程的推送**不走分派路径**，享受不到
          「分派结束自动 flush」，必须自己收口 —— 否则玩家点了 GM 按钮，
          帧压在缓冲里等下一条上行指令才发出去，表现为「点了没反应」。
        """
        b = getattr(self, '_batcher', None)
        if b is None:
            return 0
        try:
            return b.flush()
        except Exception:                                # noqa: BLE001
            return 0

    def frame_batch_stats(self):
        """返回当前连接或最近一次已关闭连接的帧合批统计。"""
        b = getattr(self, '_batcher', None)
        if b is not None:
            return b.batch_stats()
        return dict(getattr(self, '_last_batch_stats', {'enabled': False}))

    def _emit(self, kind, msg=None, **fields):
        """发一条业务事件（调试台「任务链/经济」页签的数据源）。

        ★ 为什么还要再包一层 try：总线本身已经吞掉订阅者异常，但**埋点参数**
          可能取到意外类型（例如 item name 是 bytes、qty 是 None）。事件是纯观测
          设施，它出任何问题都不能影响玩法 —— 这里吞掉，日志里留不下就算了，
          绝不能让客户端掉线。
        """
        try:
            return bus.emit(kind, msg=msg, **fields)
        except Exception:                                # noqa: BLE001
            return None

    def _emit_econ(self, kind, delta, after, reason):
        """金额类事件的统一出口：经济页签的流水就靠 delta/after/reason 三元组。"""
        self._emit(kind, delta=int(delta), after=after, reason=reason)

    # ───────────── 经济变动唯一入口（P3-05，2026-10-23）─────────────
    # 为什么要有这一层：`self.coins` 原来是散在 8 个业务函数里直接 `+= / -=` 的，
    # 结果「钱是怎么变成现在这个数」答不出来，也没有任何机制拦住「新写的代码
    # 再次绕过它」。现在所有玩法/GM 变动都走 _econ_spend / _econ_gain / _econ_set，
    # 由 economy_service 提供护栏（不许为负 / 多资源原子 / 幂等）并写进账本。
    #
    # 直接给 `self.coins` 赋值**只允许**出现在三处「建立新基线」的地方：
    #   __init__ / _reset_character / _apply_save
    # 这条纪律由 tests/_test_p3_05_economy.py 的 AST 结构断言强制（不是靠自觉）。
    def _econ(self):
        """懒建经济服务。第一次真正需要时才建，账本基线随之落在那一刻。"""
        sv = getattr(self, '_econ_svc', None)
        if sv is None:
            sv = esvc.EconomyService(self, eled.EconomyLedger(), log=self.log)
            self._econ_svc = sv
        return sv

    def _econ_reset(self):
        """丢掉账本 —— 只在新角色 / 读档建立新基线时调（流水不跨档继承）。"""
        self._econ_svc = None

    def _econ_spend(self, conn, amount, reason, kind=eled.KIND_COIN, idem=None):
        """扣一种资源。返回 Result；失败时**一分不动**，由调用方决定怎么回话。"""
        res = self._econ().charge(amount, reason, idem=idem, kind=kind)
        if res.ok:
            self._econ_publish(conn, res, reason)
        return res

    def _econ_gain(self, conn, amount, reason, kind=eled.KIND_COIN, idem=None):
        """发一种资源。返回 Result。"""
        res = self._econ().grant(amount, reason, idem=idem, kind=kind)
        if res.ok:
            self._econ_publish(conn, res, reason)
        return res

    def _econ_spend_multi(self, conn, parts, reason, idem=None):
        """一次扣多种资源，**全成功或全不动**（制作：活力 + 金钱）。"""
        res = self._econ().charge_multi(parts, reason, idem=idem)
        if res.ok:
            self._econ_publish(conn, res, reason)
        return res

    def _econ_set(self, value, reason, kind=eled.KIND_COIN, idem=None, conn=None):
        """把资源设成绝对值（GM 用）。同样留流水。"""
        res = self._econ().set_absolute(value, reason, idem=idem, kind=kind)
        if res.ok:
            self._econ_publish(conn, res, reason)
        return res

    def _econ_publish(self, conn, res, reason):
        """把一次成功的变动推给客户端（PropChange）并发总线事件。

        多资源时 `res.parts` 逐项来；单项时直接用 res 本身。
        `delta == 0` 的项（GM 设成同一个值）不发帧也不发事件，免得刷屏。
        """
        rows = res.parts or [res]
        for r in rows:
            if not r.delta:
                continue
            idx, width = _ECON_PROP.get(r.kind, (None, 0))
            if conn is not None and idx is not None and r.after is not None:
                conn.sendall(build_prop_change(idx, r.after, width))
            self._emit_econ(_ECON_EVENT.get(r.kind, 'econ.' + r.kind),
                            r.delta, r.after, reason)

    def _persist(self):
        """把可变角色状态写入存档（跨进程重启留存）。"""
        if self._save_blocked:
            self.log('   ⚠ 存档未写入：现有存档无法读取，请先备份并修复或移走原文件')
            return
        data = {
            'px': self.px, 'py': self.py,
            # 角色身份：登录回读时 `_apply_save` 靠它恢复（2026-10-22 修）
            'hero_id': self.hero_id,
            'hero_name': self.hero_name,
            'move_count': self.move_count,
            'hero_hp': self.hero_hp,
            'hero_res': self.hero_res,
            'items': self.items,
            # 宠物：实例表（等级/经验/资质/五维/技能/资源全在个体里）+ 出战 id。
            # pet_hp / pet_shape 仍写一份，一是给旧版本读档兜底，二是方便直接看存档。
            'pets': self._pets_snapshot(),
            'pet_hp': self.pet_hp,
            'active_pet_id': self.active_pet_id,
            'pet_shape': self.pet_shape,
            'hero_shape': self.hero_shape,
            'coins': self.coins,
            'iid_seq': self._iid_seq,
            'mapid': self.mapid,
            'tasks': self.tasks,
            # 主线剧情：剧情点 / 已完成剧情（2026-10-22）
            'story_point': self.story_point,
            'quests_done': sorted(self.quests_done),
        # 剧情技能（剧情点花到哪儿去了，必须看得见）
        'juqing_skills': {str(k): v for k, v in sorted(self.juqing_skills.items())},
            # 系统设置（dialog/dlgsysopt，2026-10-22）：开关位图 / 快捷键串 / 手机安全镜像
            'sys_switch': self.sys_switch,
            'sys_data': dict(self.sys_data),
            'mobile': dict(self.mobile),
            'private_info': self.private_info,
            'exp': self.exp,
            'next_exp': self.next_exp,
            # 养成：等级 / 可分配点数 / 五维 / 已学招式（JSON 键统一字符串）
            'grade': self.grade,
            'points': self.points,
            'attrs': {str(k): v for k, v in self.attrs.items()},
            'magics': {str(k): v for k, v in self.magics.items()},
            # 生活技能 / 体力（制作链前置状态）
            'tili': self.tili,
            'huoli': self.huoli,
            'life_lv': self.life_lv,
            'life_exp': self.life_exp,
            # 组队态（op72/82）：队长恒为本人，只存开关/招募文案/阵型/目标；
            # 陪同队友是纯生成数据，重建即可，不必存档
            'team': {
                'active': self.team.active,
                'title': self.team.title,
                'min_lv': self.team.min_lv,
                'max_lv': self.team.max_lv,
                'attr_class': self.team.attr_class,
                'message': self.team.message,
                'lineup': self.team.lineup,
                'target': self.team.target,
            },
        }
        # ★ 同账号多会话的「丢更新」检测（2026-10-19）：
        #   文件锁保证「不会写坏」，但保证不了「另一个会话的进度不被覆盖」——
        #   两个会话各持一份内存状态，后落档的把先落档的整个盖掉。
        #   这里在落档前比对「文件里上次是谁写的」，发现自己不是最后一个写者时
        #   打一条显式告警（不阻止写入：单人游戏不该因为并发就丢档）。
        #   判据用 _save_tag：它在 _bind_account 时按会话生成，随存档落盘。
        # ★ 2026-10-22 增量/异步：这个「读一次文件只为拿 _save_tag」的动作
        #   （一次完整 JSON 解析）改到**写线程**里做——同步路径上只剩
        #   「比对内存快照」的脏键判断，落盘本身也不再阻塞游戏循环。
        data['_save_tag'] = self._save_tag
        # ★ 落盘必须带 schema 版本：否则下次读档会被当成「版本 0 老档」，
        #   白跑一遍迁移（迁移不保证幂等，重复跑可能丢字段）。
        data['_schema'] = SCHEMA_VERSION
        # 增量：与上次落盘的快照逐键比对，只把变化的键留给写线程。
        # 没变化就整个跳过——「走一步存一次」里大量落点位其实什么都没变
        #   （战斗结束后的连续 persist、GM 只读动作后的 persist 常常全同）。
        # ★ 2026-10-23：快照必须**深拷贝**（原先是 `dict(data)` 浅拷）。
        #   data 里的 `items` / `tasks` / `hero_res` 是运行时的**活引用**
        #   （业务代码全是原地增删：`self.items[iid] = …` / `del self.items[iid]` /
        #   `self.hero_res[name] = …` / `self.tasks[tid] = …`）。浅拷后
        #   「上次快照」与「本次 data」指向同一个对象，`last[k] != v` 恒为
        #   False——于是只改背包/任务/战斗资源时 dirty 为空、命中 noop 分支
        #   跳过落盘，**磁盘存档静默停留在旧状态**（实测：原地加一件物品后
        #   磁盘 items 键数不变；原地改 tasks 后磁盘 tasks 为空）。
        #   深拷贝顺带解决第二件事：队列/写线程手里是一份 detached 快照，
        #   json.dump 不再与游戏线程的原地修改赛跑（dict changed size
        #   during iteration 会让写线程整份丢档）。
        snap = copy.deepcopy(data)
        with self._save_lock:
            last = self._save_snapshot
            disk = self._save_disk_snapshot      # 锁内取，避免与写线程的更新错位
            dirty = [k for k, v in snap.items()
                     if k not in last or last[k] != v]
            self._save_dirty = dirty
            self._save_pending = len(dirty)
        if last and not dirty:
            try:
                st = os.stat(self.savepath)
                disk_stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                disk_stamp = None
            if self._save_file_stamp is None or disk_stamp == self._save_file_stamp:
                self._save_noop += 1
                # ★ 无变化且磁盘未被外部改写 → 不排写任务。但**返回值不能一概
                #   当成「未知」**：只有「这份快照确实已在磁盘上」（内存基线与
                #   磁盘基线同一）才能对交付方确认 True。异步队列里还压着一份
                #   更晚的快照时（`disk is not last`），那份写入之后可能失败 ——
                #   此时必须回 None（未知），让调用方 flush 后读 `_save_last_ok`
                #   的真实结果，否则就是「误报成功」。
                if disk is last:
                    return True
                return None
            # 内存没变，但文件指纹变了：另一会话可能刚写过。必须进入 _save_now，
            # 让既有的 _save_tag 检测发出告警并明确采用本会话快照。
            dirty = ['_save_tag']
        with self._save_lock:
            # 只有真的要写才推进内存基线：noop 时保持旧对象，`is` 判定才能
            # 继续认出「磁盘里就是它」。
            self._save_snapshot = snap
        if not self._save_async:
            return self._save_now(snap, dirty)
        # `M4Server` 也被协议测试、数据迁移工具和离线脚本直接实例化；这些入口
        # 不会进入 run()，因此没有后台写线程。这里不能向尚未创建的队列 put，
        # 也不能临时起线程后让调用方在不知情的情况下读到旧档。无写线程时保持
        # 原来的同步语义；真正接入客户端后 run() 会先 _ensure_save_writer()，
        # 游戏循环仍走异步路径。
        if self._save_queue is None or self._save_thread is None \
                or not self._save_thread.is_alive():
            return self._save_now(snap, dirty)
        self._save_queue.put((snap, list(dirty)))
        return None

    def _save_now(self, data, dirty=None):
        """真正落盘（同步）。在写线程、wait=True 的同步路径、或异步关闭时调用。

        ★ `_save_last_ok` 记录**最近一次落盘的确定结果**（2026-10-23 P1）：
          异步模式下 `_persist()` 只返回「已排队」，调用方无法知道最终写成没有。
          不可逆交付（如宠物入栏）需要「持久化成功」这个确定信号，靠它拿：
          写线程 `_write_and_ack` 在 `_save_now` 返回后才置位 flush 哨兵，
          所以 flush 返回时 `_save_last_ok` 必然已写好（GIL 保证可见性）。
        """
        if self._save_blocked:
            self.log('   ⚠ 存档未写入：现有存档无法读取，请先备份并修复或移走原文件')
            self._save_last_ok = False
            return False
        # 跨写者检测放在这里（阻塞 IO 已不在游戏线程上）
        prev_writer = None
        try:
            _prev = load_role(self.savepath)
            if isinstance(_prev, dict):
                prev_writer = _prev.get('_save_tag')
        except Exception:                        # noqa: BLE001
            prev_writer = None
        if prev_writer and prev_writer != self._save_expected_tag:
            self.log('   ⚠ 存档被另一会话写过（%s → 本会话 %s）：拒绝覆盖，保留磁盘进度'
                     % (prev_writer, self._save_tag))
            self._save_conflict = True
            self._save_last_ok = False
            self._save_baseline_rollback()
            return False
        ok = save_role(data, self.savepath)
        self._save_last_ok = bool(ok)
        if ok:
            # 成功：这份快照就是磁盘上的内容，记下来当「磁盘基线」
            self._save_disk_snapshot = data
            self._save_expected_tag = self._save_tag
            try:
                st = os.stat(self.savepath)
                self._save_file_stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._save_file_stamp = None
            self._save_writes += 1
            self.log('   💾 已存档（位置 %d,%d，HP %d，背包 %d 件%s）'
                     % (self.px, self.py, self.hero_hp, len(self.items),
                        '，变更 %d 键' % len(dirty) if dirty else ''))
        else:
            # ★ 写失败（2026-10-23 P1）：内存基线退回**最后一次成功写**的
            #   快照。否则失败态的基线留在 _save_snapshot 上，调用方回滚内存
            #   后再做同一操作，脏键 diff 为空 → 判成「无变化」跳过落盘 →
            #   磁盘永远补不上（实测：落盘失败后重试装备，永远回滚失败）。
            self._save_baseline_rollback()
            self.log('   ⚠ 存档失败（忽略）')
        return ok

    def _save_baseline_rollback(self):
        """写失败时把内存基线退回磁盘基线（`_save_snapshot → _save_disk_snapshot`）。

        为什么必须退：`_persist` 在**发起写之前**就推进了 `_save_snapshot`。
        写失败后若基线停在这份「从未落到磁盘」的状态上，调用方回滚内存、
        再做同一操作时脏键 diff 恒为空 → noop → 磁盘永远补不上这次变更。
        """
        with self._save_lock:
            # `_persist` 的差异计算要求内存基线始终是映射。全新角色第一次
            # 落盘就失败/冲突时还没有磁盘快照（None），应退回空基线，确保
            # 下一次重试会重新生成完整 dirty 集，而不是在 `k not in last`
            # 处异常。
            self._save_snapshot = (self._save_disk_snapshot
                                   if self._save_disk_snapshot is not None else {})

    def _save_writer(self):
        """后台写线程：把连续到达的落盘请求**合并**成一次 IO。

        为什么能合并：`_persist` 在一条业务链上会被调多次（移动/战斗/任务各一次），
        而这些调用之间游戏状态还在变。合并后只写最后一份快照 —— 中间态本来
        就没有「必须落盘」的意义（崩溃时要的那份就是最新那份）。
        队列里只留最新快照，所以队列长度有界、内存不涨。

        ★ flush 语义（`flush_saves` 的完成事件）必须**写完之后**才置位：
          第一版在合并循环里一见到哨兵就 set，结果「flush 返回 True」时
          那份最新快照还在手里没落盘 —— 测试表现为「flush 后文件还是旧值」。
          哨兵改成攒到 `pending_done`，等本轮 `_save_now` 返回再逐个置位。
        """
        while True:
            item = self._save_queue.get()
            if item is None:                    # 停止哨兵
                return
            if isinstance(item, _SaveDone):
                item.event.set()                # 空闲时收到：无事可做，直接应答
                continue
            data, dirty = item
            pending_done = []                   # 本轮要应答的 flush 哨兵
            dropped = 0
            while True:                         # 合并：取空后续，只保留最新快照
                try:
                    nxt = self._save_queue.get_nowait()
                except Exception:               # noqa: BLE001
                    break
                if nxt is None:
                    self._save_queue.put(None)  # 停止哨兵：先落完这份再退
                    self._write_and_ack(data, dirty, pending_done)
                    return
                if isinstance(nxt, _SaveDone):
                    pending_done.append(nxt)
                    continue
                data, dirty = nxt
                dropped += 1
            if dropped:
                self._save_coalesced += dropped
            self._write_and_ack(data, dirty, pending_done)

    def _write_and_ack(self, data, dirty, pending_done):
        """落一份盘，然后才应答等它的 flush 哨兵（顺序即语义）。"""
        try:
            self._save_now(data, dirty)
        except Exception as e:                   # noqa: BLE001
            self._save_last_ok = False           # 交付方据此回滚，绝不误报成功
            self._save_baseline_rollback()       # 基线同样退回磁盘基线
            self.log('   ⚠ 后台落盘异常（忽略）：%r' % e)
        finally:
            for d in pending_done:
                d.event.set()

    def flush_saves(self, timeout=5.0):
        """把待落盘的快照立即写完（停机/断线/测试用）。

        ★ 返回 True 的语义是「**最新那份快照已经落盘**」——哨兵在写线程
          写完当前快照后才被置位（见 _write_and_ack）。所以调用方可以放心
          在这之后读文件。
        """
        if not self._save_async:
            return False
        # 尚未接入客户端的离线实例没有写线程，也就没有待刷新的异步任务。
        # 视作已经完成，避免 stop()/测试收口向 None 队列写哨兵。
        if self._save_queue is None or self._save_thread is None \
                or not self._save_thread.is_alive():
            return True
        done = _SaveDone()
        self._save_queue.put(done)
        return done.event.wait(timeout)

    def save_stats(self):
        """落盘统计（调试台/回归用）：能看到「合并省下多少次 IO、跳过多少次无变化」。"""
        with self._save_lock:
            return {
                'async': self._save_async,
                'pending': (self._save_queue.qsize()
                            if self._save_queue is not None else 0),
                'dirty_keys': len(self._save_dirty or []),
                'writes': self._save_writes,
                'coalesced': self._save_coalesced,
                'noop': self._save_noop,
                'saved_io': self._save_coalesced + self._save_noop,
            }

    # ── 多角色：账号确定后切到该角色的存档 ──────────────────────────────
    def _reset_character(self):
        """把本会话恢复成「全新角色」基线。

        为什么需要：`__init__` 会先载入 `role.json` 并把它的 px/py/背包等套到
        self 上。若随后绑定的账号没有自己的存档（新账号），那些值必须被清掉，
        否则新角色会顶着上一个角色的位置/背包出生。
        这里的每一项都与 `__init__` 的默认值同源（同一个常量）。
        """
        self.px, self.py = SPAWN_X, SPAWN_Y
        self.move_count = 0
        self.mapid = START_MAP_ID
        self._arrive_xy = None
        self.hero_hp = HERO_HP
        self.hero_res = hero_res_fresh()
        self.pet_res = {'m_FP': int(PET_FP), 'm_MP': int(PET_MP)}
        self.base_skills = {}
        self.huoli = HERO_HUOLI_INIT
        self.tili = HERO_TILI_INIT
        self.life_lv = LIFE_LV_INIT
        self.life_exp = LIFE_EXP_INIT
        self._huoli_ts = time.time()
        self.huoli_regen_acc = 0.0
        self.items = {}
        self._reset_items()
        self._sell_cache = []
        # 新角色：宠物栏重置为一只默认企鹅（实例表整体重建，不残留上一角色的宠）
        self.pet_shape = PET_SHAPE_DEFAULT
        self._pet_alive = True
        self._init_pets()
        self.coins = COIN_INIT
        self._iid_seq = 9000
        self._econ_reset()      # P3-05：重开角色，旧号的流水不能并进来
        self.grade = 1
        self.points = POINT_INIT
        self.attrs = {idx: ATTR_BASE for idx, _n in ATTR_ORDER}
        self.magics = {}
        for _mid in TEACH_MAGIC_IDS:
            self.magics.setdefault(_mid, TEACH_MAGIC_LEVEL)
        self.tasks = {}
        self.exp = EXP_INIT
        self.next_exp = NEXT_EXP_INIT
        # 系统设置：新角色回到默认（开关全关、快捷键串空、手机安全默认开）
        self.sys_switch = 0
        self.sys_data = {}
        self.mobile = {'check': 0, 'receive': 1, 'enable_friends': 1,
                       'limit': [0, 0, 0, 0], 'degree': 0, 'money': 0, 'balance': 0}
        self.private_info = ''
        # 队伍：新角色从「未组队」开始（不用上一个角色的队伍）
        self.team = teammod.TeamState(self.hero_id or 1, self.hero_name or '')
        # 换角色：战斗运行态必须整份清掉（旧角色可能停在「等宠物指令」窗口）
        self._reset_war_state()
        self.cur_npc = None
        self.cur_npc_menu = None
        self.give_npc = None
        self.give_gtype = 0
        self.npc_chosen = None
        self.npc_input_text = None

    def _has_char_saves(self):
        """存档目录里是否已有「按账号分」的角色文件（role_*.json）。"""
        try:
            return any(fn.startswith('role_') and fn.endswith('.json')
                       for fn in os.listdir(self.savedir))
        except OSError:
            return False

    def _bind_account(self, account):
        """登录账号确定后绑定角色存档。

        单角色时代只有一个 role.json；现在按账号分文件（role_<账号>.json），
        这样同一台机器上不同账号各自有独立角色，互不覆盖。

        规则（顺序很重要，保证既有存档不丢）：
          1. 已有 role_<账号>.json  → 载入它（换号/换角色）
          2. 没有该账号存档、但存在旧的 role.json 且本次是第一个会话
             → 沿用 role.json（老玩家升级后继续玩原角色，不被清空）
          3. 都没有 → 全新角色，落盘时写 role_<账号>.json
        """
        acc = (account or '').strip()
        binder = getattr(self, '_account_binder', None)
        if acc and binder is not None and not binder(self.account, acc):
            self.log('   ⚠ 拒绝重复登录：账号 %s 已在另一会话中' % acc)
            raise AccountInUseError('账号 %s 已在另一会话中' % acc)
        self.account = acc
        if not acc:
            return
        self._session_tag = acc[:12]
        # 账号也进写者标识：不同账号本就写不同文件，标识里带账号更利于排查
        self._save_tag = '%s-%d-%d' % (acc[:16], os.getpid(), id(self) & 0xFFFFFF)
        path = char_savepath(self.savedir, acc)
        self.savepath = path
        sv = load_role(path)
        if sv is None and not os.path.exists(path):
            # ② 老存档兜底：仅当目录里**还没有任何**按账号分的存档时才认领，
            #    否则第二个账号会把第一个账号的角色克隆走（同角色出现在两个号上）。
            legacy = os.path.join(self.savedir, 'role.json')
            has_char_files = self._has_char_saves()
            if (not has_char_files and self._loaded
                    and os.path.exists(legacy)):
                sv = self._loaded
                self.log('   （沿用单角色旧存档 %s → %s）'
                         % ('role.json', os.path.basename(path)))
        if sv is None:
            self.savepath = path
            self._loaded = None
            self._save_expected_tag = None
            self._save_blocked = False
            # 新角色：清掉 __init__ 从旧 role.json 套过来的位置/背包等，
            # 否则新账号会顶着上一个角色的进度出生（详见 _reset_character）
            self._reset_character()
            self.log('   （新角色存档：%s）' % os.path.basename(path))
            return
        self._apply_save(sv)
        self.log('   （已载入角色存档 %s）' % os.path.basename(path))

    def _apply_save(self, sv):
        """把一份存档字典套到本会话状态上（登录/换号共用）。"""
        self._loaded = sv
        self._save_expected_tag = sv.get('_save_tag') if isinstance(sv, dict) else None
        self._save_blocked = False
        # ★ 2026-10-22 修：回读角色身份。
        #   旧实现只存 px/items 等，**不存 hero_name/hero_id**，也不置 have_role——
        #   于是「老角色重新登录」走到 op1 时 have_role 仍为 False，服务端回
        #   AccountInfo(0)（无角色），客户端永远停在「正在登录中」。
        #   名字兜底用账号名（旧档没有名字字段）；id 兜底 1。
        self.hero_name = sv.get('hero_name') or self.hero_name or self.account
        self.hero_id = int(sv.get('hero_id') or self.hero_id or 1)
        self.have_role = True
        self.px = sv.get('px', self.px)
        self.py = sv.get('py', self.py)
        self.move_count = sv.get('move_count', self.move_count)
        self.hero_hp = sv.get('hero_hp', self.hero_hp)
        # 迁移可能把 hero_res 置 None（表示「按当前全局参数重算」，见 _migrate_1_to_2）；
        # dict 才直接用，否则回落到 hero_res_fresh()，避免拿旧参数点复活。
        _hr = sv.get('hero_res')
        self.hero_res = _hr if isinstance(_hr, dict) else hero_res_fresh()
        # 活力/体力分列：旧存档没有 huoli 键 → 给满，老角色照样能开工
        self.huoli = int(sv.get('huoli', HERO_HUOLI_INIT))
        self.tili = int(sv.get('tili', self.tili))
        self.life_lv = int(sv.get('life_lv', self.life_lv))
        self.life_exp = int(sv.get('life_exp', self.life_exp))
        if isinstance(sv.get('items'), dict):
            self.items = sv['items']
        self._load_pets(sv)
        self.hero_shape = int(sv.get('hero_shape', self.hero_shape))
        self.coins = sv.get('coins', self.coins)
        self._econ_reset()      # P3-05：读档 = 新基线，流水不跨档继承
        self._iid_seq = sv.get('iid_seq', self._iid_seq)
        self.mapid = sv.get('mapid', self.mapid)
        self.grade = int(sv.get('grade', self.grade))
        self.points = int(sv.get('points', self.points))
        if isinstance(sv.get('attrs'), dict):
            for idx, _n in ATTR_ORDER:
                self.attrs[idx] = int(sv['attrs'].get(str(idx), ATTR_BASE))
        if isinstance(sv.get('magics'), dict):
            self.magics = {int(k): int(v) for k, v in sv['magics'].items()}
        # 系统设置（dialog/dlgsysopt）：★ 这段在 __init__ 的加载块里还有一份**同款**，
        #   两处都要加（该函数的读写分散在两处是既有设计，改动时别只改一处）。
        self.sys_switch = int(sv.get('sys_switch', 0) or 0)
        if isinstance(sv.get('sys_data'), dict):
            self.sys_data = {int(k): str(v) for k, v in sv['sys_data'].items()}
        if isinstance(sv.get('mobile'), dict):
            self.mobile.update({k: v for k, v in sv['mobile'].items()
                                if k in self.mobile})
        self.private_info = str(sv.get('private_info') or '')
        for _mid in TEACH_MAGIC_IDS:
            self.magics.setdefault(_mid, TEACH_MAGIC_LEVEL)
        self.tasks = sv.get('tasks') or {}
        self.exp = sv.get('exp', self.exp)
        self.next_exp = sv.get('next_exp', self.next_exp)
        tv = sv.get('team')
        if isinstance(tv, dict):
            self.team.active = bool(tv.get('active'))
            self.team.title = tv.get('title') or self.team.title
            self.team.min_lv = int(tv.get('min_lv', 0))
            self.team.max_lv = int(tv.get('max_lv', teammod.TEAM_MAX_MEMBERS))
            self.team.attr_class = int(tv.get('attr_class', 0))
            self.team.message = tv.get('message') or ''
            self.team.lineup = int(tv.get('lineup', 0))
            self.team.target = int(tv.get('target', 0))
            if self.team.active and not self.team.members:
                self.team.members = [dict(self.team._self_template)]

    # ── 养成：升级 / 加点 / 学招式（M9）────────────────────────────────
    def _send_progression(self, conn):
        """登录后把养成状态补齐：等级 / 经验 / 下一级 / 点数 / 五维 / 金钱。

        为什么不在 `build_prop_init` 里直接写：那个函数是纯静态的（初值写死 1 级、
        1000 金），而这三个值都必须来自存档。用 PropChange 逐项覆盖，
        顺手修掉「重登后金钱显示回 1000」这个一直存在的偏差（客户端只会 setattr）。
        """
        conn.sendall(build_prop_change(PROP_IDX_GRADE, self.grade, 1))
        conn.sendall(build_prop_change(PROP_IDX_EXP, self.exp, 4))
        conn.sendall(build_prop_change(PROP_IDX_NEXTEXP, self.next_exp, 4))
        conn.sendall(build_prop_change(PROP_IDX_POINT, self.points, 2))
        for idx, _name in ATTR_ORDER:
            conn.sendall(build_prop_change(idx, self.attrs[idx], 2))
        conn.sendall(build_prop_change(PROP_IDX_GOLD, self.coins, 4))
        # 剧情点（m_StoryPoint = ITEM_ALL[42]，TWORD）
        if self.story_point:
            conn.sendall(build_prop_change(PROP_IDX_STORY_POINT, self.story_point, 2))

    def _hero_level_up(self, conn):
        """C2S_PROP_LEVELUP(17)：空包，经验够就升一级。

        客户端 `dialog/dlgstate` 的「升级」按钮无条件发本包，够不够由服务端判。
        回推四帧：等级 / 经验 / 下一级所需 / 可用点数 —— 客户端全部走
        `S2CPropChange` 的 `ITEM_ALL[type]` + setattr，无需专用帧。
        """
        if self.grade >= GRADE_MAX:
            self.log('   ⚠ 升级被拒：已达上限 %d 级' % GRADE_MAX)
            return False
        if self.exp < self.next_exp:
            self.log('   ⚠ 升级被拒：经验不足（%d/%d）' % (self.exp, self.next_exp))
            return False
        self.exp -= self.next_exp
        self.grade += 1
        self.points += POINT_PER_LEVEL
        self.next_exp = EXP_PER_LEVEL_STEP * self.grade
        conn.sendall(build_prop_change(PROP_IDX_GRADE, self.grade, 1))
        conn.sendall(build_prop_change(PROP_IDX_EXP, self.exp, 4))
        conn.sendall(build_prop_change(PROP_IDX_NEXTEXP, self.next_exp, 4))
        conn.sendall(build_prop_change(PROP_IDX_POINT, self.points, 2))
        self.log('   升级 → Lv%d（经验 %d/%d，可分配点数 %d）'
                 % (self.grade, self.exp, self.next_exp, self.points))
        self._emit('econ.levelup', '升级 → Lv%d' % self.grade,
                   grade=self.grade, points=self.points, next_exp=self.next_exp)
        self._persist()
        return True

    def _hero_add_points(self, conn, body):
        """C2S_PROP_POINT(16)：5 字节增量，顺序 = 体/力/魔/魅/敏（见 ATTR_ORDER）。

        ★ 是**增量**不是绝对值：客户端面板把玩家点出来的数先加在本地 `m_Points`
          上，确认时才把 5 个增量一次发来（`dlgstate` msgid 4）。
        点数不足时整包拒绝（不做部分生效），避免客户端本地显示与服务端错位。
        """
        deltas = list(body[:5])
        total = sum(deltas)
        if total <= 0:
            return False
        if total > self.points:
            self.log('   ⚠ 加点被拒：请求 %d 点 > 可用 %d' % (total, self.points))
            return False
        applied = 0
        for (idx, name), d in zip(ATTR_ORDER, deltas):
            if d <= 0:
                continue
            new = min(ATTR_MAX, self.attrs[idx] + d)
            gained = new - self.attrs[idx]
            if gained <= 0:
                continue
            self.attrs[idx] = new
            applied += gained
            conn.sendall(build_prop_change(idx, new, 2))
        self.points -= applied
        conn.sendall(build_prop_change(PROP_IDX_POINT, self.points, 2))
        self.log('   加点：体/力/魔/魅/敏 += %s → %s，剩余点数 %d'
                 % (deltas, [self.attrs[i] for i, _n in ATTR_ORDER], self.points))
        self._persist()
        return applied > 0

    @staticmethod
    def _learn_cost(level):
        """学招式费用：与客户端 `dialog/dlglearnskill.LearnCostStd` 同式。

        服务端必须自己算一遍——客户端面板显示的价格是它本地算的，服务端若按别的
        口径扣钱，玩家会看到「显示 51 扣 200」。等级 ≤60 时 GradeExpRatio=1。
        """
        x = level ** 3 * 3.5 - level ** 2 * 20 + (level - 1) * 40 + 30
        return max(1, int(x * 0.1 + 50))

    def _hero_learn_juqing(self, conn, skid):
        """学/升一个**剧情技能**：付剧情点（m_StoryPoint），回 S2CSkillLevel(sktype=3)。

        规则（全部来自客户端，不是本服定的）：
          · 每级消耗 = `juqingdata[sid]['dist']`（2–4 点，skilldata/juqingdata 真值）
          · 最高等级 = `juqingdata[sid]['max']`（3–8 级）
          · 判定与回执 = `dialog/dlglearnskill.py`（2001<=sid<2030 用 m_StoryPoint 比）
        不足/满级都**整包拒绝**（不部分生效），并把当前值回给客户端资源条，
        否则面板会显示「点了、没反应、点数也没变」的分叉状态。
        """
        info = jqmod.JUQING_SKILLS.get(int(skid)) or {}
        cur = int(self.juqing_skills.get(int(skid), 0) or 0)
        if cur >= int(info.get('max', 5)):
            self.log('   ⚠ 学剧情技能被拒：%s 已到 %d 级'
                     % (info.get('name', skid), cur))
            self._emit('quest.juqing_reject', '剧情技能 %s 已满级'
                       % info.get('name', skid), skid=int(skid), level=cur,
                       reason='max')
            return False
        cost = int(info.get('dist', 2))
        if self.story_point < cost:
            self.log('   ⚠ 学剧情技能被拒：%s %d 级需 %d 点，现有 %d'
                     % (info.get('name', skid), cur + 1, cost, self.story_point))
            conn.sendall(build_prop_change(PROP_IDX_STORY_POINT, self.story_point, 2))
            self._emit('quest.juqing_reject', '剧情点不足（%d/%d）'
                       % (self.story_point, cost), skid=int(skid), level=cur,
                       reason='point', have=self.story_point, need=cost)
            return False
        level = cur + 1
        self.story_point -= cost
        self.juqing_skills[int(skid)] = level
        conn.sendall(build_prop_change(PROP_IDX_STORY_POINT, self.story_point, 2))
        conn.sendall(build_skill_level_juqing(int(skid), level))
        self.log('   学剧情技能 %s(%d) → %d 级（剧情点 -%d → %d）'
                 % (info.get('name', '?'), skid, level, cost, self.story_point))
        self._emit('quest.juqing', '剧情技能 %s → %d 级' % (info.get('name', skid), level),
                   skid=int(skid), level=level, cost=cost, after=self.story_point)
        self._persist()
        return True

    def _send_learn_list(self, conn):
        """下发 S2CNpcLearnList(149)：告诉客户端「这师傅能教哪些招式」。

        课表 = 人物招式（LEARN_MAGIC_IDS，花钱）+ **剧情技能**（花剧情点）。
        客户端 `dlglearnskill.CreateSkillInfo` 对 `2001<=sn<2030` 自动把花费列
        换成 m_StoryPoint，所以两边共用同一张列表就能同时列出两种学习入口。
        只列还没学到顶的；已满级的仍下发（freeLevel=npcLimit=0），由客户端显示
        当前等级，玩家再点会收到「已满级」提示，不会误以为列表是空的。
        """
        entries = []
        for skid in LEARN_MAGIC_IDS:
            entries.append((skid, 0, 255, 0))
        for skid in jqmod.JUQING_SKILL_IDS:
            _lv = int(self.juqing_skills.get(skid, 0) or 0)
            _max = int(jqmod.JUQING_SKILLS[skid].get('max', 5))
            entries.append((skid, 0, _max if _lv < _max else 0, 0))
        conn.sendall(build_npc_learn_list(entries))
        self.log('   → 教招式列表 %s + 剧情技能 %s（已学 %s）'
                 % (list(LEARN_MAGIC_IDS), list(jqmod.JUQING_SKILL_IDS)[:4] + ['…'],
                    sorted(self.juqing_skills)))

    def _hero_learn_magic(self, conn, skid):
        """C2S_NPC_LEARN_SKILL(103)：学/升级一个招式**或剧情技能**。

        客户端收到 `S2CSkillLevel(65)` 的 sktype=2 才认为学会（写 `g_Skills[ST_MAGIC]`），
        收到 sktype=7 才把它放进入战斗「招式」菜单（`g_Skills[ST_MAGICREADY]`）——
        两帧都要发，少发第二帧就是「学会了但打不出来」。

        ★ 2026-10-22：同一个 opcode 还承载**剧情技能**（skid 2001–2029）。
          `dialog/dlglearnskill.py` 对这个区间的条目把花费列换成 m_StoryPoint，
          所以学它是扣剧情点、不是扣金钱；学会的回执是 sktype=3。
          玩家从师傅列表里点剧情技能就会发上来——不分支处理的话，会被
          「不在本师傅的课表里」一竿子打回去，剧情点永远花不出去。
        """
        if skid in jqmod.JUQING_SKILLS:
            return self._hero_learn_juqing(conn, skid)
        if skid not in LEARN_MAGIC_IDS:
            self.log('   ⚠ 学招式被拒：%d 不在本师傅的课表里' % skid)
            return False
        cur = self.magics.get(skid, 0)
        if cur >= LEARN_MAGIC_MAX_LEVEL:
            self.log('   ⚠ 学招式被拒：%d 已到 %d 级' % (skid, cur))
            return False
        level = cur + 1
        cost = self._learn_cost(level)
        if self.coins < cost:
            self.log('   ⚠ 学招式被拒：%d 级需 %d 金，现有 %d' % (level, cost, self.coins))
            return False
        # P3-05 收口：扣费走统一入口。service 会再查一次余额（双保险无副作用），
        # 顺带补上此前**漏发**的 econ 事件 —— 学招式这条路径原先在流水里是隐形的。
        paid = self._econ_spend(conn, cost, '学招式 %d → %d 级' % (skid, level))
        if not paid.ok:
            self.log('   ⚠ 学招式被拒：%s' % paid.message)
            return False
        self.magics[skid] = level
        conn.sendall(build_skill_level_magic(skid, level))
        conn.sendall(build_skill_equip(skid, level))
        self.log('   学招式 %d → %d 级（花费 %d，余 %d 金）'
                 % (skid, level, cost, self.coins))
        self._persist()
        return True

    def _dmg(self, atk, df, dp, lv):
        """按物伤公式算一次伤害；模型/参数/浮动全部可被 world_config 覆盖。"""
        base = dmgm.physical_damage(atk, df, dp, lv, gain=DMG_GAIN,
                                    floor_ratio=DMG_FLOOR, p_div=DMG_P_DIV,
                                    mode=DMG_MODE, rounding=DMG_ROUNDING,
                                    model=DMG_MODEL)
        return int(base * dmgm.rng_factor(DMG_RNG_LO, DMG_RNG_HI))

    def _hero_attack_damage(self):
        """主角普攻伤害：⌊1.3×(A−Deff)⌋ + 力量加成。

        默认参数（A=39、敌方 D=0）下 ⌊1.3×39⌋ = 50，与 47 条录像一致。
        ★ 力量加成必须加在**公式之外**：加成是「每 4 点力量 +1 点伤害」的约定值，
          若加进 A 会被 1.3 放大成 +1.3，且与录像校核的口径（未加点 = 50）不一致。

        ★ 2026-10-23（P3-01）：取数改走 `progression_service.final_stats`——
          全服只有这一个地方算最终属性，装备/成长/加点的贡献都在那里合成，
          这里是**消费**而不是再算一遍。默认参数下 A=39、力量加成为 0 ⇒ 仍是 50。
        """
        st = self.progress_stats()
        return self._dmg(st['atk'], ENEMY_DF, st['dp'], ENEMY_LEVEL) + st['str_bonus']

    # ── P3-01：最终属性（派生，不落盘）─────────────────────────────────
    def equipped_shapes(self):
        """当前穿在身上的装备 shape（背包 pos ∈ 1..9）。

        装备的**唯一真实来源就是背包位置**——没有单独的「装备表」，
        所以不存在「背包删了装备但属性还在」的God对象，也不必为装备加存档字段。
        """
        return [int(it['shape']) for it in self.items.values()
                if 1 <= int(it.get('pos', -1)) <= 9]

    def progress_stats(self, explain=False):
        """→ {atk, df, dp, hp_max, mp_max, speed, str_bonus[, contrib]}

        base 传**实时**模块常量（而不是 progression_service.BASE），
        这样 world_config 改过 hero_at/df/dp/hp 之后这里跟着变。
        """
        return prog.final_stats(
            base={'atk': HERO_AT, 'df': HERO_DF, 'dp': HERO_DP,
                  'hp_max': HERO_HP,
                  'mp_max': HERO_RES_INIT.get('m_MP', 100), 'speed': HERO_SPEED},
            grade=self.grade, attrs=self.attrs,
            equipment=self.equipped_shapes(), explain=explain)

    def _award_battle_exp(self, conn):
        """战斗胜利发经验：升级闭环的「入口」，没有这段玩家永远攒不到经验。

        ★ 每只怪 60 点是**模拟参数**——录像里只有伤害/血量/掉落，没有经验值；
          原版经验表在服务端，客户端拿不到（见 MODDING_PLAN 待解清单第 2 条）。
        """
        gain = BATTLE_EXP_PER_ENEMY * max(1, self.enemies)
        self.exp += gain
        conn.sendall(build_prop_change(PROP_IDX_EXP, self.exp, 4))
        self._emit_econ('econ.exp', gain, self.exp, '战斗胜利（%d 只 ×%d）'
                        % (max(1, self.enemies), BATTLE_EXP_PER_ENEMY))
        # 体力恢复（★ 模拟规则）：胜利后回 BATTLE_TILI_REGEN 点，上限封顶
        before_tili = self.tili
        self.tili = min(HERO_TILI_MAX, self.tili + BATTLE_TILI_REGEN)
        if self.tili != before_tili:
            conn.sendall(build_prop_change(PROP_IDX_TILI, self.tili, 2))
            self.log('   体力恢复 %d → %d/%d'
                     % (before_tili, self.tili, HERO_TILI_MAX))
        if self.exp >= self.next_exp:
            self.log('   战斗经验 +%d → %d/%d（已可升级，属性面板点「升级」）'
                     % (gain, self.exp, self.next_exp))
        else:
            self.log('   战斗经验 +%d → %d/%d' % (gain, self.exp, self.next_exp))
        # 宠物经验：只有**出战且存活**的宠物才拿（倒地不算，见 _pet_award_exp）
        # ★ 必须在 WarEnd + 场景重同步之后发（帧顺序同主角经验），否则 48 会被
        #   客户端的 ClearAllObject/重建流程冲掉。
        self._pet_award_exp(conn, gain)
        self._persist()

    def _item_consume(self, conn, iid):
        """使用本服支持的回血道具并同步主角/战斗血条；不支持的道具不扣除。

        对齐客户端：`item.ItemQuantity` 只改数量与角标，`item.ItemDel` 按 type 删格。
        """
        it = self.items.get(iid)
        if it is None:
            self.log('   ⚠ 使用道具 id=%d 不存在（可能已用尽）' % iid)
            return False
        heal = ITEM_HEAL.get(it['shape'], 0)
        if heal <= 0:
            self.log('   ⚠ 暂不支持使用道具 id=%d shape=%d，未扣除' %
                     (iid, it['shape']))
            return False
        if self.hero_hp >= HERO_HP:
            self.log('   ⚠ 主角 HP 已满，道具 id=%d 未扣除' % iid)
            return False
        it['qty'] -= 1
        self.hero_hp = min(HERO_HP, self.hero_hp + heal)
        conn.sendall(build_prop_change_hp(self.hero_hp))
        if self.in_war:
            conn.sendall(build_war_hp_percent(
                HERO_WAR_ID, int(self.hero_hp * 100.0 / HERO_HP)))
        self._send_item_consumed(conn, iid, it)
        self.log('   使用道具 id=%d shape=%d → 剩余 %d（回血 %d → HP %d/%d）'
                 % (iid, it['shape'], it.get('qty', 0), heal, self.hero_hp, HERO_HP))
        self._persist()
        return True

    def _item_use_target(self, conn, iid, target_id=0):
        """使用单个道具；`target_id` 命中宠物 id / 宠物战斗 id 时给宠物用。

        ★ 宠物道具的唯一上行入口是 `C2S_ITEM_USE(34)` 的 **9 字节**形态
          （`netitem.C2SItemSummonUse`：item_id(4) + summon_id(4) + flag(1)），
          见 dlgpetitem.py 的调用点 —— 没有独立的「宠物用药/装备」opcode。
        ★ 同一条上行按物品类型分流：召唤兽装备 → `_pet_equip`（佩带/替换），
          回血类 → 下面的宠物回血逻辑，其余 → 主角消耗或拒用。
        """
        it = self.items.get(iid)
        if it is None:
            self.log('   ⚠ 使用道具 id=%d 不存在（可能已用尽）' % iid)
            return False
        # 目标解析：战斗 id(250) → 出战宠物；否则按实例 id 查栏内
        target = None
        if int(target_id or 0) == PET_WAR_ID:
            target = self._pet()                      # 战斗里用战斗 id 指宠物
        elif int(target_id or 0) in self.pets:
            target = self.pets[int(target_id)]        # 角色外直接给宠物 id
        # 召唤兽装备分支（docx：召唤兽界面「物品」右键装备走的就是这条上行）
        if target is not None and int(it.get('shape') or 0) in PET_EQUIP_ITEMS:
            return self._pet_equip(conn, iid, target['pid'])
        heal = ITEM_HEAL.get(it['shape'], 0)
        if heal <= 0:
            self.log('   ⚠ 暂不支持使用道具 id=%d shape=%d，未扣除' %
                     (iid, it['shape']))
            return False
        if target is None:
            return self._item_consume(conn, iid)
        hpmax = int(target.get('hpmax') or PET_HP) or PET_HP
        # ★ 气血必须按**目标宠物自己的**当前值算：背包里对「非出战」宠物用药时，
        #   self.pet_hp 是出战那只的镜像，拿它当基准会把两只的血混成一笔。
        is_active = (target['pid'] == self.active_pet_id)
        cur_hp = int(self.pet_hp) if is_active else int(target.get('hp') or 0)
        if cur_hp >= hpmax:
            self.log('   ⚠ 宠物 id=%d 气血已满，道具 id=%d 未扣除'
                     % (target['pid'], iid))
            return False
        if cur_hp <= 0 and self.in_war:
            self.log('   ⚠ 宠物已倒地，本场战斗不能复活（道具未扣除）')
            return False
        new_hp = min(hpmax, cur_hp + heal)
        target['hp'] = new_hp
        if is_active:
            self.pet_hp = new_hp
        it['qty'] -= 1
        if self.in_war and self._pet_alive and is_active:
            conn.sendall(build_war_hp_percent(
                PET_WAR_ID, int(new_hp * 100.0 / hpmax)))
        else:
            # 角色外（含宠物倒地后复活）：重发 48 才会更新面板上的气血
            self._pet_frames(conn, target)
        self._send_item_consumed(conn, iid, it)
        self.log('   宠物使用回血道具 id=%d → id=%d HP %d/%d'
                 % (iid, target['pid'], new_hp, hpmax))
        self._persist()
        return True

    def _send_item_consumed(self, conn, iid, item):
        if item['qty'] > 0:
            conn.sendall(build_item_quantity(iid, item['qty']))
        else:
            del self.items[iid]
            conn.sendall(build_item_del(ITEM_WTYPE_NORMAL, iid))

    def _handle_item_use(self, conn, body):
        """兼容单件、定向宠物、道具包裹和多 id 批量使用格式。"""
        if len(body) == 9:
            iid, target_id = struct.unpack('<II', body[:8])
            self.log('   >>> 对宠物使用道具 id=%d target=%d flag=%d'
                     % (iid, target_id, body[8]))
            self._item_use_target(conn, iid, target_id)
            return
        if len(body) == 4:
            iid = struct.unpack('<I', body)[0]
            self.log('   >>> 使用道具 id=%d' % iid)
            self._item_use_target(conn, iid, 0)
            return
        if len(body) == 8:
            first, second = struct.unpack('<II', body)
            if second in self.items:
                ids = (first, second)
            else:
                self.log('   >>> 定向使用道具 id=%d target=%d' % (first, second))
                self._item_use_target(conn, first, second)
                return
        elif len(body) > 8 and len(body) % 4 == 0:
            ids = struct.unpack('<%dI' % (len(body) // 4), body)
        else:
            self.log('   ⚠ 忽略格式错误的道具使用包（%dB）' % len(body))
            return
        for iid in ids:
            self.log('   >>> 批量使用道具 id=%d' % iid)
            self._item_use_target(conn, iid, 0)

    # ───────────────────────── NPC 商店（购买）─────────────────────────
    def _alloc_iid(self):
        """分配一个不冲突的物品 id（MOCK_ITEMS 用 2001..，自 9001 起）。"""
        self._iid_seq += 1
        while self._iid_seq in self.items:
            self._iid_seq += 1
        return self._iid_seq

    def _send_shop_list(self, conn):
        """向客户端推送村长商店的购买列表（op147 mode2）。"""
        items = [(shape, qty, price) for (shape, _n, price, qty) in SHOP_ITEMS]
        conn.sendall(build_npc_buylist(0, items))
        self.log('   下发商店列表 op147 ×%d（金钱 %d）' % (len(items), self.coins))

    # ═════════════════ 宠物实例：查找 / 同步 / 下发帧 ═════════════════
    def _init_pets(self):
        """建默认宠物（旧行为：一只梦想企鹅，id=PET_ID，1 级，全套录像技能）。"""
        self.pets = {}
        self.pet_seq = PET_ID
        p = pet_new(PET_ID, self.pet_shape, level=PET_LEVEL)
        self.pets[p['pid']] = p
        self.active_pet_id = p['pid']
        self.pet_hp = p['hp']
        self.pet_shape = p['shape']
        return p

    def _load_pets(self, sv):
        """从存档恢复宠物实例表（含**旧档迁移**）。

        旧档只有 `pet_hp` / `pet_shape` / `active_pet_id` 三个散字段（那时只有一只假想
        宠物），迁移成「一只默认宠物 + 把当时的气血搬过去」，不丢进度。
        新档是 `pets: [实例字典...]`：逐字段白名单覆盖，坏字段一律回落到 pet_new 的默认，
        保证「存档坏了也不至于起不来」。

        ★ 实例唯一性（2026-10-23 P1）：存档里出现**重复 pid**（手改档/合并档/旧版本
          bug）时，后来的条目会静默覆盖先前的——那正是「重复创建宠物实例」。
          这里显式判重：保留首条、忽略后续重复并记日志。造型同样校验：
          `pet_new` 对未知造型只做**显示**回落，实例里仍存原值，脏 shape 会一路
          写回存档；读档这一路就地把非法造型换成目录默认值。
        """
        raw = sv.get('pets')
        self.pets = {}
        if isinstance(raw, list) and raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    pid = int(item.get('pid') or 0)
                except (TypeError, ValueError):
                    continue
                if pid <= 0:
                    continue
                if pid in self.pets:
                    self.log('   ⚠ 存档宠物 id=%d 重复：保留首条，忽略后续条目'
                             '（实例唯一性）' % pid)
                    continue
                shape = item.get('shape')
                try:
                    shape = int(shape)
                except (TypeError, ValueError):
                    shape = PET_CATALOG_DEFAULT
                if shape not in PET_CATALOG:
                    self.log('   ⚠ 存档宠物 id=%d 造型 %r 不在目录，回落 %s'
                             % (pid, shape, PET_CATALOG[PET_CATALOG_DEFAULT]['name']))
                    shape = PET_CATALOG_DEFAULT
                p = pet_new(pid, shape, item.get('level', 1),
                            item.get('name'))
                for key in ('exp', 'expnext', 'point', 'str', 'con', 'mag', 'cha',
                            'dex', 'key', 'birthkey', 'life', 'mo_ling_guo', 'tire',
                            'factor_idx'):
                    if key in item:
                        try:
                            p[key] = int(item[key])
                        except (TypeError, ValueError):
                            pass
                for flag in ('bound', 'lookon'):
                    if flag in item:
                        p[flag] = bool(item[flag])
                # 召唤兽装备：{pos: {shape, desc}}；pos 越界/坏字段一律丢弃
                # （客户端 DrawPetEquip 只认 1/2/3，脏 pos 发下去也被跳过）
                if isinstance(item.get('equips'), dict):
                    eq = {}
                    for k, v in item['equips'].items():
                        if not isinstance(v, dict):
                            continue          # 手改档可能是 None/字符串/数字
                        try:
                            pos = int(k)
                            shape = int(v.get('shape') or 0)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= pos <= 3 and shape > 0:
                            eq[pos] = {'shape': shape,
                                       'desc': str(v.get('desc') or '')}
                    p['equips'] = eq
                if isinstance(item.get('attr_lv'), dict):
                    for k, v in item['attr_lv'].items():
                        try:
                            p['attr_lv'][int(k)] = int(v)
                        except (TypeError, ValueError):
                            pass
                if isinstance(item.get('skills'), list):
                    p['skills'] = [int(s) for s in item['skills']
                                   if isinstance(s, (int, float))]
                    p['hf_from'] = int(item.get('hf_from', len(p['skills'])))
                pet_normalize(p)                       # 资质/属性/上限按等级重算
                for key in ('hp', 'mp', 'fp'):         # 当前值最后套（先有上限才能钳）
                    if item.get(key) is not None:
                        try:
                            p[key] = max(0, min(p[key + 'max'], int(item[key])))
                        except (TypeError, ValueError):
                            pass
                self.pets[pid] = p
        if not self.pets:
            # 旧档迁移：一只默认宠物，气血沿用存档里的 pet_hp
            shape = sv.get('pet_shape', self.pet_shape)
            try:
                shape = int(shape)
            except (TypeError, ValueError):
                shape = PET_SHAPE_DEFAULT
            if shape not in PET_CATALOG:
                shape = PET_CATALOG_DEFAULT
            p = pet_new(PET_ID, shape, level=PET_LEVEL)
            hp = sv.get('pet_hp')
            if hp is not None:
                try:
                    p['hp'] = max(0, min(p['hpmax'], int(hp)))
                except (TypeError, ValueError):
                    pass
            self.pets[PET_ID] = p
        self.pet_seq = max(self.pets)
        # 出战宠物：存档里那只必须真的在栏内；否则视为「休息」
        try:
            act = int(sv.get('active_pet_id') or 0)
        except (TypeError, ValueError):
            act = 0
        act = act if act in self.pets else 0
        self._pet_switch_runtime(0, act)
        return self.pets

    def _pets_snapshot(self):
        """可 JSON 化的宠物实例列表（存盘用）。

        ★ 先 `_sync_pet_runtime()`：出战宠物的当前气血只活在 self.pet_hp 上，
          不回写就会出现「存盘时血量还是上一场开打前的」。
        ★ `stats`（真值派生值）不入档：它由等级/资质/五维**算**出来，存了反而会
          在公式调整后变成脏数据；读档时 pet_normalize 重算。
        """
        self._sync_pet_runtime()
        out = []
        for pid in sorted(self.pets):
            p = self.pets[pid]
            out.append({
                'pid': p['pid'], 'name': p.get('name'), 'shape': p.get('shape'),
                'level': p.get('level'), 'exp': p.get('exp'),
                'expnext': p.get('expnext'),
                'attr_lv': {str(k): int(v) for k, v in (p.get('attr_lv') or {}).items()},
                'point': p.get('point'),
                'str': p.get('str'), 'con': p.get('con'), 'mag': p.get('mag'),
                'cha': p.get('cha'), 'dex': p.get('dex'),
                'skills': list(p.get('skills') or []),
                'hf_from': p.get('hf_from'),
                'factor_idx': int(p.get('factor_idx') or 0),
                'key': p.get('key'), 'birthkey': p.get('birthkey'),
                'bound': bool(p.get('bound')), 'lookon': bool(p.get('lookon')),
                'equips': {str(pos): {'shape': int(e.get('shape') or 0),
                                      'desc': str(e.get('desc') or '')}
                           for pos, e in sorted((p.get('equips') or {}).items())
                           if 1 <= int(pos) <= 3},
                'hp': p.get('hp'), 'mp': p.get('mp'), 'fp': p.get('fp'),
                'life': p.get('life'), 'mo_ling_guo': p.get('mo_ling_guo'),
                'tire': p.get('tire'),
            })
        return out

    def _sync_pet_runtime(self):
        """把运行态的 pet_hp 写回出战实例（切宠/存盘/下线前必须调）。

        为什么要有这一层：战斗代码只碰 `self.pet_hp`（一个数），而存档/面板要的是
        「每只宠物自己的能力值」。这里做**单向回写**，避免出现两份都能被写的真相。
        """
        cur = self.pets.get(self.active_pet_id)
        if cur is not None:
            cur['hp'] = max(0, min(int(cur.get('hpmax') or PET_HP), int(self.pet_hp)))
        return cur

    def _pet(self, pid=None):
        """取宠物实例；pid=None/0 → 出战宠物（没有则 None）。"""
        if pid is None or int(pid or 0) == 0:
            return self.pets.get(self.active_pet_id)
        return self.pets.get(int(pid))

    def _pet_hpmax(self):
        """出战宠物的气血上限（没有出战宠物时回落录像值，保证除零安全）。"""
        cur = self._pet()
        return int((cur or {}).get('hpmax') or PET_HP) or PET_HP

    def _pet_level(self):
        return int((self._pet() or {}).get('level') or PET_LEVEL)

    def _pet_combat(self, key):
        """出战宠物的战斗层数值（at/df/dp/speed/magdef），见 pet_combat_stats。"""
        return pet_combat_stats(self._pet()).get(key, 0)

    def _pet_info(self, pid=None):
        """(名字, 造型, 目录项)；pid 缺省 = 出战宠物；目录外造型回落默认企鹅。"""
        p = self._pet(pid)
        if not p:
            d = pet_def(self.pet_shape)
            shape = (self.pet_shape if self.pet_shape in PET_CATALOG
                     else PET_CATALOG_DEFAULT)
            return d['name'], int(shape), d
        shape = p['shape'] if p['shape'] in PET_CATALOG else PET_CATALOG_DEFAULT
        d = pet_def(shape)
        return p.get('name') or d['name'], int(shape), d

    def _pet_base(self, p):
        """实例 → build_pet_info 的字段字典（BASE_PROP 4: 之后的那一串）。

        字段顺序固定（见 build_pet_info 的注释）；`exp`/`expnext` 决定面板上的
        「宠物当前拥有的可用经验 / 升级需要消耗的经验」两行。
        """
        if p is None:
            return pet_base_dict()
        stats = p.get('stats') or {}
        return {
            'grade': int(p.get('level') or 1),
            'hp': max(0, min(int(p.get('hpmax') or PET_HP), int(p.get('hp') or 0))),
            'hpmax': int(p.get('hpmax') or PET_HP),
            'mp': max(0, min(int(p.get('mpmax') or PET_MP), int(p.get('mp') or 0))),
            'mpmax': int(p.get('mpmax') or PET_MP),
            'fp': max(0, min(int(p.get('fpmax') or PET_FP), int(p.get('fp') or 0))),
            'fpmax': int(p.get('fpmax') or PET_FP),
            # 面板四项用真值层（0..65535 档），与战斗层的 reverse 标定值分开
            'damage': int(stats.get('at') or PET_AT_DISPLAY),
            'defense': int(stats.get('df') or PET_DF_DISPLAY),
            'speed': int(stats.get('sp') or PET_SPEED_DISPLAY),
            'magdef': int(stats.get('md') or PET_MAGDEF_DISPLAY),
            'str': int(p.get('str') or 0), 'con': int(p.get('con') or 0),
            'mag': int(p.get('mag') or 0), 'cha': int(p.get('cha') or 0),
            'dex': int(p.get('dex') or 0), 'point': int(p.get('point') or 0),
            'exp': int(p.get('exp') or 0), 'expnext': int(p.get('expnext') or 0),
            'life': int(p.get('life') or PET_LIFE),
        }

    def _pet_frames(self, conn, p):
        """下发一只宠物的完整身份三帧：SummonInfo(48) + SummonSkill(49) + SummonKey(53)。"""
        if p is None:
            return
        _pn, _ps, _pd = self._pet_info(p['pid'])
        conn.sendall(build_pet_info(p['pid'], p.get('name') or _pn, _ps,
                                    self._pet_base(p)))
        conn.sendall(build_pet_skill(p['pid'], list(p.get('skills') or []),
                                     int(p.get('hf_from') if p.get('hf_from') is not None
                                         else len(p.get('skills') or []))))
        conn.sendall(build_pet_key(p['pid'], int(p.get('key') or 0),
                                   int(p.get('birthkey') or 0)))

    def _send_all_pets(self, conn):
        """登录/重同步：把**所有**宠物逐只下发，再出战状态一帧（S2CSetFight 50）。

        客户端 `pet.g_Pets` 只由 48 建出来 —— 少发一只，面板列表里就少一只，
        重登后「宠物不见了」就是这么来的。有装备的宠物补发 54
        （`dlgpetitem.DrawPetEquip` 读的是 54 建出来的 `m_Equips`）。
        """
        for pid in sorted(self.pets):
            p = self.pets[pid]
            self._pet_frames(conn, p)
            if p.get('equips'):
                conn.sendall(build_pet_equip(pid, p['equips']))
        conn.sendall(build_pet_fight(self.active_pet_id or 0))
        self.log('   + 宠物 SummonInfo/SummonSkill/SummonKey ×%d（出战 id=%s）'
                 % (len(self.pets), self.active_pet_id or '无'))

    def _add_pet_check(self, shape, level):
        """入栏前置校验（**纯函数，不动任何状态**）。返回 (ok, why, shape, level)。

        为什么单独拆一层：GM 面板要拿「为什么没进」的准原因，测试要逐条断言，
        而 `_add_pet` 的交付路径（落盘/发帧）不该被反复走。造型必须在宠物目录里——
        `pet_new` 对未知造型只做显示回落，实例里仍存原值，那会把脏 shape 写进存档。
        """
        try:
            shape = int(shape)
        except (TypeError, ValueError):
            return False, 'bad_shape', None, None
        if shape not in PET_CATALOG:
            return False, 'bad_shape', None, None
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 1
        level = max(1, min(PET_LEVEL_MAX, level))
        if len(self.pets) >= PET_SLOT_MAX:
            return False, 'full', shape, level
        return True, None, shape, level

    def _add_pet(self, conn, shape, level=1, name=None):
        """新宠物入栏（GM / 后续的捕捉&商店都走这里）。返回实例或 None。

        ★ 交付原子性（2026-10-23 P1）：入栏是**不可逆交付**——客户端一收到
          48/49/53 就会把宠物列进面板。旧实现「先发帧再 _persist()」，落盘失败时
          客户端以为有了、磁盘上没有，重登宠物消失 = **误报成功**。现在的顺序：
            ① 纯校验（造型/等级/栏位/pid 唯一性，不动任何状态）
            ② 入表 + 落盘，**拿到确定结果**（同步看返回值；异步 flush 后看
               `_save_last_ok` —— 哨兵在写线程写完才置位，返回值必然已更新）
            ③ 确认成功才发帧返回；失败回滚（出表 + 还原 pet_seq）并如实返回 None，
               失败原因记在 `_add_pet_last_error`，**一帧都不发**
        """
        ok, why, shape, level = self._add_pet_check(shape, level)
        if not ok:
            self._add_pet_last_error = why
            if why == 'bad_shape':
                self.log('   ⚠ 加宠被拒：造型不在宠物目录（%s）'
                         % sorted(PET_CATALOG))
            else:
                self.log('   ⚠ 宠物栏已满（%d/%d），未入栏'
                         % (len(self.pets), PET_SLOT_MAX))
            return None
        # pid 唯一性：单调计数器 + 显式判重双保险（杜绝覆盖已存在的实例）
        pid = self.pet_seq + 1
        while pid in self.pets:
            pid += 1
        p = pet_new(pid, shape, level=level, name=name)
        self.pets[pid] = p                      # 先入表，_pets_snapshot 才带得上
        ok = self._persist()
        if self._save_blocked:
            ok = False                         # 存档被禁：同步路径也会返回 None
        elif ok is None:                       # 异步：等写线程给确定结果
            self.flush_saves(5.0)
            ok = bool(self._save_last_ok)
        if ok is not True:
            # 落盘未确认：磁盘上从来没有这只宠物（save_role 原子替换），
            # 内存里这半个实例必须撤掉，否则「内存有、磁盘无」就是脏数据。
            del self.pets[pid]
            self.pet_seq = pid - 1
            self._add_pet_last_error = ('save_blocked' if self._save_blocked
                                        else 'persist_failed')
            self.log('   ⚠ 加宠失败已回滚：%s 未入栏（落盘未确认，%s）'
                     % (p['name'], self._add_pet_last_error))
            return None
        self.pet_seq = pid
        self._add_pet_last_error = None
        if conn is not None:
            self._pet_frames(conn, p)           # 确认后才交付
        self.log('   + 新宠物 id=%d %s（%d 级，factor=%d，HP %d，已落盘确认）'
                 % (p['pid'], p['name'], p['level'], pet_factor_idx_of(p),
                    p['hpmax']))
        return p

    def _set_pet_shape(self, shape):
        """改**出战宠物**的造型（目录内才生效）。返回是否真的换了。

        旧语义是「唯一模拟宠物换皮」，现在等价于改实例的 shape 并重算属性/技能
        （造型决定 FactorData 资质表 → 面板与战斗数值都会跟着变）。
        """
        try:
            shape = int(shape)
        except (TypeError, ValueError):
            return False
        p = self._pet()
        if p is None or shape not in PET_CATALOG:
            return False
        changed = shape != p.get('shape')
        if changed:
            p['shape'] = shape
            p['name'] = pet_def(shape)['name']
            pet_normalize(p)
            self.pet_shape = shape
        return changed

    def _pet_switch_runtime(self, old_pid, new_pid):
        """切换出战宠物：先把运行态气血写回旧宠，再把新宠的气血读出来。"""
        old = self.pets.get(int(old_pid or 0))
        if old is not None:
            old['hp'] = max(0, min(int(old.get('hpmax') or PET_HP),
                                   int(self.pet_hp)))
        new = self.pets.get(int(new_pid or 0))
        self.active_pet_id = int(new_pid or 0)
        self.pet_hp = int(new['hp']) if new is not None else 0
        if new is not None:
            self.pet_shape = new['shape']
        self._pet_alive = new is not None and int(self.pet_hp) > 0

    def _set_active_pet(self, conn, pid):
        """出战切换（C2SSetFight 131）。

        ★ 协议里**没有**独立的「收回」指令：客户端点「参战/休憩」发的是同一个
          `C2SSetFight(pid)`（dlgpet 的 msgid==1 分支两种情况都发 pid），
          收回只能靠服务端回 `S2CSetFight(50)` 带 pid=0 —— 客户端 `pet.SetFight`
          查不到该 id 就把 g_ActivatePet 置 None。
          所以这里的语义是**开关**：请求的正是当前出战宠物 → 收回。
        """
        pid = int(pid or 0)
        if pid == 0 or pid == self.active_pet_id:
            if self.active_pet_id:
                self._pet_switch_runtime(self.active_pet_id, 0)
                conn.sendall(build_pet_fight(0))
                self.log('   >>> 宠物休息 id=%d → 回 S2CSetFight(0)' % pid)
            else:
                conn.sendall(build_pet_fight(0))
            self._persist()
            return True
        p = self.pets.get(pid)
        if p is None:
            self.log('   ⚠ 未知宠物 id=%d，保留当前出战状态' % pid)
            conn.sendall(build_pet_fight(self.active_pet_id or 0))
            return False
        if p.get('lookon'):
            self.log('   ⚠ 宠物 id=%d 处于寄存/引藏状态，不能参战' % pid)
            conn.sendall(build_pet_fight(self.active_pet_id or 0))
            return False
        self._pet_switch_runtime(self.active_pet_id, pid)
        conn.sendall(build_pet_fight(pid))
        # 参战即绑定（dlgpet 的确认框文案：选择参战后不能再交易/出售 -> m_Key|=64）。
        # 客户端只认 op53 改 m_Key，所以绑定后必须补一帧。
        if not (int(p.get('key') or 0) & PET_KEY_BOUND):
            p['key'] = int(p.get('key') or 0) | PET_KEY_BOUND
            p['bound'] = True
            conn.sendall(build_pet_key(pid, int(p['key']), int(p.get('birthkey') or 0)))
            self.log('   宠物 id=%d 已绑定（m_Key|=64）' % pid)
        if int(self.pet_hp) <= 0:
            self.log('   ⚠ 宠物 id=%d 气血为 0，参战也不会入场（先用药品/还魂丹）' % pid)
        self.log('   >>> 宠物出战 id=%d（HP %d/%d，%d 级）'
                 % (pid, self.pet_hp, self._pet_hpmax(), p['level']))
        self._persist()
        return True

    def _pet_rename(self, conn, pid, name):
        """改名（C2SSummonRename 130）：pid(4) + 名字(gbk，body 剩余)。

        应答用 48 把新名字推回去（客户端 `pet.UpdateInfo` 对已存在宠物走
        `UpdateProp` 原地改名并刷新列表行）。
        """
        p = self.pets.get(int(pid or 0))
        if p is None:
            self.log('   ⚠ 宠物改名被拒：宠物 id=%s 不在栏内' % pid)
            return False
        raw = (name or '').strip().replace('\x00', '')
        if not raw:
            self.log('   ⚠ 宠物改名被拒：空名字（客户端 C2SSummonRename 应带名字字节）')
            return False
        p['name'] = gbk_fit(raw, PET_NAME_MAX_BYTES)
        self._pet_frames(conn, p)
        self.log('   宠物改名 id=%d → %r' % (p['pid'], p['name']))
        self._persist()
        return True

    def _pet_free(self, conn, pid):
        """放生（C2SSummonFree 128）。删实例 + 下发 shape=65535 的 48。"""
        pid = int(pid or 0)
        p = self.pets.get(pid)
        if p is None:
            self.log('   ⚠ 放生失败：宠物 id=%d 不在栏内' % pid)
            return False
        # 客户端删列表用的是「当前选中/请求的那只」，所以删除帧的 pid 必须带真 id
        name = p.get('name') or ''
        was_active = (pid == self.active_pet_id)
        del self.pets[pid]
        conn.sendall(build_pet_info(pid, name, 65535, pet_base_dict()))
        if was_active:
            self._pet_switch_runtime(pid, 0)
            conn.sendall(build_pet_fight(0))
        self.log('   放生宠物 id=%d %s（剩 %d 只）' % (pid, name, len(self.pets)))
        self._persist()
        return True

    def _pet_store(self, conn, pid, stored):
        """寄存/取出（132 存入 / 133 取出）。

        客户端的「观 看 / 引 藏」只是本地 m_Lookon 开关，**没有**独立的 S2C 载体
        （48 里没有这个字段），所以状态由服务端记账；这里回一帧 48 让面板刷新。
        寄存中的宠物不能参战（`_set_active_pet` 会拒），也不能被放生。
        """
        pid = int(pid or 0)
        p = self.pets.get(pid)
        if p is None:
            self.log('   ⚠ 寄存/取出失败：宠物 id=%d 不在栏内' % pid)
            return False
        p['lookon'] = bool(stored)
        if stored and pid == self.active_pet_id:
            self._pet_switch_runtime(pid, 0)
            conn.sendall(build_pet_fight(0))
        self._pet_frames(conn, p)
        self.log('   宠物 id=%d → %s' % (pid, '寄存（引藏）' if stored else '取出（观看）'))
        self._persist()
        return True

    def _pet_add_point(self, conn, pid, deltas):
        """加点（C2SSummonAddPoints 134）：pid(4) + 5 字节（Con/Str/Mag/Cha/Dex 增量）。"""
        p = self.pets.get(int(pid or 0))
        if p is None:
            self.log('   ⚠ 加点失败：宠物 id=%s 不在栏内' % pid)
            return False
        ok, why = pet_add_points(p, deltas)
        if not ok:
            self.log('   ⚠ 加点被拒（%s）：请求 %s，可用 %s'
                     % (why, list(deltas), p.get('point')))
            # 拒绝也要回一帧 48：客户端已按本地预分配改了面板，不回就是「点数花了没生效」
            self._pet_frames(conn, p)
            return False
        self._pet_frames(conn, p)
        self.log('   加点 id=%d：%s → 五维 Con/Str/Mag/Cha/Dex=%s，剩 %d 点'
                 % (p['pid'], list(deltas),
                    [p['con'], p['str'], p['mag'], p['cha'], p['dex']],
                    p['point']))
        self._persist()
        return True

    def _pet_equip(self, conn, iid, pid):
        """给宠物佩带一件召唤兽装备（C2S_ITEM_USE 9 字节路径的装备分支）。

        协议入口（无独立装备 opcode，客户端反编译 + net.py 全量核对）：
          dlgpetitem 右键装备 → `netitem.C2SItemSummonUse(item, summon, flag)`
          → `C2S_ITEM_USE(34)`，9 字节 item(4)+summon(4)+flag(1)。
        规则（官方攻略 docx，资料库 §22302）：
          · 召唤兽等级 ≥ PET_EQUIP_LEVEL_MIN(20) 才能使用；
          · 同类只能替换、不能卸下，旧装备消失（本方法即「替换」语义）；
          · pos ∈ {1 项圈, 2 铠甲, 3 护符}（dlgpetitem.DrawPetEquip 的 pos2obj）。
        ★ 与 _add_pet 同款的原子交付：先校验（不动状态）→ 改内存 → 落盘确认
          → 才发 54 + 扣道具帧；落盘失败回滚（装备还原、道具不扣）且零帧。
        """
        it = self.items.get(int(iid or 0))
        if it is None:
            self.log('   ⚠ 装备失败：道具 id=%s 不存在' % iid)
            return False
        eq_def = PET_EQUIP_ITEMS.get(int(it.get('shape') or 0))
        if eq_def is None:
            # 不是召唤兽装备：交回调用方走消耗品路径（不扣不装备）
            return None
        pos, name, _lv = eq_def
        p = self.pets.get(int(pid or 0))
        if p is None:
            self.log('   ⚠ 装备失败：宠物 id=%s 不在栏内' % pid)
            return False
        if int(p.get('level') or 0) < PET_EQUIP_LEVEL_MIN:
            self.log('   ⚠ 装备被拒：宠物 id=%d %d 级 < %d 级（%s 用不了）'
                     % (p['pid'], p.get('level'), PET_EQUIP_LEVEL_MIN, name))
            # 拒绝也回 48：客户端右键后面板可能已本地预亮，不回就是「状态分叉」
            self._pet_frames(conn, p)
            return False
        old = (p.get('equips') or {}).get(pos)
        new_eq = {'shape': int(it['shape']), 'desc': name}
        p.setdefault('equips', {})[pos] = new_eq
        it['qty'] = int(it.get('qty', 1)) - 1
        ok = self._persist()
        if self._save_blocked:
            ok = False
        elif ok is None:
            self.flush_saves(5.0)
            ok = bool(self._save_last_ok)
        if ok is not True:
            # 回滚：装备还原、道具数量还原（旧装备「消失」只发生在确认成功后）
            if old is None:
                p['equips'].pop(pos, None)
            else:
                p['equips'][pos] = old
            it['qty'] = int(it.get('qty', 1)) + 1
            self.log('   ⚠ 装备失败已回滚：%s 未佩带（落盘未确认）' % name)
            return False
        # 确认后才交付：54（装备位）+ 道具扣除 + 48（面板）
        conn.sendall(build_pet_equip(p['pid'], p.get('equips') or {}))
        self._send_item_consumed(conn, iid, it)
        self._pet_frames(conn, p)
        self.log('   ✦ 宠物 id=%d 佩带 %s（pos=%d%s）'
                 % (p['pid'], name, pos,
                    '，替换旧装备' if old else ''))
        return True

    def _pet_award_exp(self, conn, amount):
        """战斗胜利给出战宠物经验 + 自动升级（升级后重发 48/49/53）。

        ⚠ SIM：原服「出战宠物拿多少经验」不可得，这里按 `PET_EXP_SHARE`（1.0，
          与主角同额）发放 —— 数值口径在 PET_EXP_SHARE 注释里标明。
        """
        p = self._pet()
        if p is None or amount <= 0:
            return 0
        if int(self.pet_hp) <= 0:
            self.log('   宠物已倒地，本场不获得经验')
            return 0
        gain = int(amount * PET_EXP_SHARE)
        ups = pet_gain_exp(p, gain)
        for (old, new) in ups:
            self.log('   ★ 宠物升级 %d → %d 级（HP 上限 %d，攻击 %s，剩 %d 点）'
                     % (old, new, p['hpmax'], (p.get('stats') or {}).get('at'),
                        p.get('point')))
        if conn is not None:
            self._pet_frames(conn, p)
        self._persist()
        return gain

    def _handle_npc_buy(self, conn, index, amount):
        """处理 C2S_NPC_BUY(99)：index(2B)=商品序号，amount(2B)=数量。"""
        if not (0 <= index < len(SHOP_ITEMS)):
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n没有这个商品。'))
            return
        shape, name, price, _q = SHOP_ITEMS[index]
        amount = int(amount)
        if amount < 1:
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n购买数量必须大于零。'))
            return
        free_positions = [pos for pos in range(ITEM_POS_BAG_START, ITEM_POS_BAG_END)
                          if not any(item.get('pos') == pos
                                     for item in self.items.values())]
        if amount > len(free_positions):
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n背包空间不足（剩余 %d 格）。' % len(free_positions)))
            return
        cost = price * amount
        if self.coins < cost:
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n金钱不足（需 %d，现有 %d）。' % (cost, self.coins)))
            return
        # 扣钱并放入背包空格（逐件 S2CItemAdd，客户端刷新背包）
        # P3-05 收口：扣费失败就**一件都不给**（上面的余额预检查已挡掉绝大多数，
        # 这里挡的是账本拒记这种「钱没扣成」的异常路径）。
        paid = self._econ_spend(conn, cost, 'NPC 购买 %s ×%d' % (name, amount))
        if not paid.ok:
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n交易未能完成：%s' % paid.message))
            self.log('   ⚠ NPC 购买失败：%s' % paid.message)
            return
        for pos in free_positions[:amount]:
            iid = self._alloc_iid()
            self.items[iid] = {'shape': shape, 'key': 0, 'qty': 1,
                               'pos': pos, 'infoext': name}
            conn.sendall(build_item_add(ITEM_WTYPE_NORMAL, iid, shape, 0, 1, pos))
        conn.sendall(build_npc_chat_ext(
            NPC_NAME, 'P\n购买成功：%s ×%d，花费 %d 金钱，剩余 %d。'
            % (name, amount, cost, self.coins)))
        self.log('   NPC 购买：%s ×%d，花费 %d（余 %d）' % (name, amount, cost, self.coins))
        self._persist()

    # ── NPC 收购（M10）──────────────────────────────────────────────────
    def _sell_entries(self):
        """背包按**造型**汇总成收购清单：[(shape, qty, unit_price), ...]，造型升序。"""
        agg = {}
        for it in self.items.values():
            shape = it['shape']
            agg[shape] = agg.get(shape, 0) + int(it.get('qty', 1))
        return [(shape, qty, sell_price_of(shape)) for shape, qty in sorted(agg.items())]

    def _send_sell_list(self, conn):
        """下发 S2CNpcSellList(148)：客户端据此弹出「卖东西」面板。

        ★ 同时把清单缓存到 `_sell_cache`：客户端卖东西时发的是**列表下标**，
          服务端只能用自己发出去的那一份来还原 shape —— 不缓存就解析不了。
        """
        self._sell_cache = self._sell_entries()
        conn.sendall(build_npc_sell_list(self._sell_cache))
        self.log('   → 收购列表 %d 种：%s'
                 % (len(self._sell_cache),
                    ', '.join('%s×%d@%d' % (item_name_of(s), q, p)
                              for s, q, p in self._sell_cache) or '（空背包）'))

    def _take_from_iid(self, conn, iid, amount):
        """从**指定实例**扣 amount，返回实际扣掉的件数（不跨实例）。"""
        it = self.items.get(iid)
        if not it:
            return 0
        take = min(int(it.get('qty', 1)), amount)
        if take <= 0:
            return 0
        it['qty'] -= take
        self._send_item_consumed(conn, iid, it)
        self._emit('item.take', '扣除 %s ×%d' % (item_name_of(it['shape']), take),
                   shape=int(it.get('shape', 0)), name=item_name_of(it['shape']),
                   qty=take, iid=int(iid))
        return take

    def _remove_items_of_shape(self, conn, shape, amount):
        """按造型扣背包，返回实际扣掉的件数；逐实例发 ItemQuantity / ItemDel。"""
        ids = sorted([k for k, v in self.items.items() if v['shape'] == shape],
                     key=lambda k: self.items[k].get('pos', 999))
        gone = 0
        for iid in ids:
            if gone >= amount:
                break
            it = self.items.get(iid)
            if not it:
                continue
            take = min(int(it.get('qty', 1)), amount - gone)
            it['qty'] -= take
            gone += take
            self._send_item_consumed(conn, iid, it)
        return gone

    def _handle_npc_sell(self, conn, body):
        """C2S_NPC_SELL(106)：两种形态（以客户端 `netnpc.C2SSellNpc` 为准）。

        flag=1 → itemid(4B)：卖**该实例整摞**。客户端这条路径不带数量
                  （`dlgsellnpc` 右键/选中物品直接卖，amount 传 0 不上线）。
        flag=0 → pos(1B) + amount(2B)：**pos 是收购列表下标，不是背包格号**
                  （「卖全部同类」按 `enumerate(m_Goods)` 的下标发）。
        两条都按 shape 结算——所以背包里同造型的多个实例会被依次扣掉。
        """
        if not body:
            return False
        flag = body[0]
        sold_iid = 0                       # flag=1 时锁定到具体实例；flag=0 按造型扣
        if flag == 1 and len(body) >= 5:
            iid = struct.unpack('<I', body[1:5])[0]
            it = self.items.get(iid)
            if it is None:
                conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n这件东西不在背包里。'))
                self.log('   ⚠ 卖出被拒：实例 %d 不在背包' % iid)
                return False
            shape = it['shape']
            amount = int(it.get('qty', 1))
            sold_iid = iid
        elif flag == 0 and len(body) >= 4:
            idx = body[1]
            amount = struct.unpack('<H', body[2:4])[0]
            if not (0 <= idx < len(self._sell_cache)):
                conn.sendall(build_npc_chat_ext(
                    NPC_NAME, 'P\n没有这件收购品，请重新打开收购列表。'))
                self.log('   ⚠ 卖出被拒：收购下标 %d 越界（清单 %d 项）'
                         % (idx, len(self._sell_cache)))
                return False
            shape = self._sell_cache[idx][0]
        else:
            self.log('   ⚠ 卖出包无法识别：flag=%d len=%d' % (flag, len(body)))
            return False

        if amount <= 0:
            return False
        price = sell_price_of(shape)
        # ★ flag=1 必须扣**玩家点的那一个实例**：按造型从低位格子扣会卖掉别的摞
        #   （背包里同造型多摞时，玩家点的是第 2 摞、实际扣的是第 1 摞）。
        if sold_iid:
            gone = self._take_from_iid(conn, sold_iid, amount)
        else:
            gone = self._remove_items_of_shape(conn, shape, amount)
        if gone <= 0:
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n你身上没有这件东西。'))
            return False
        gain = price * gone
        self._econ_gain(conn, gain, 'NPC 卖出 %s ×%d' % (item_name_of(shape), gone))
        # ★ 只原位改件数，**绝不重排/删条目**：客户端是按下标卖的，清单一旦重排，
        #   玩家点「鹿茸」就会卖成下一项。卖空的条目保留（qty=0），再点会被拒绝。
        for i, (s0, _q0, p0) in enumerate(self._sell_cache):
            if s0 == shape:
                total = sum(int(v.get('qty', 1)) for v in self.items.values()
                            if v['shape'] == shape)
                self._sell_cache[i] = (s0, total, p0)
                break
        name = item_name_of(shape)
        conn.sendall(build_npc_chat_ext(
            NPC_NAME, 'P\n%s 我收下了：%d 件，给你 %d 金钱，现在有 %d。'
            % (name, gone, gain, self.coins)))
        self.log('   NPC 收购：%s ×%d @%d = %d（余 %d 金）'
                 % (name, gone, price, gain, self.coins))
        self._persist()
        return True

    # ── NPC 制作（M12）：C2S 102 → 扣材料 + 入包 + op160 ──────────────
    def _regen_huoli(self, now=None, advance=0):
        """活力独立恢复：**基于真实在线时长**，与战斗无关（2026-10-22 改造）。

        旧注释「每场战斗回满」是错的——本服历史上从未在战斗里重置活力，它只会越做越少。
        这里改成：每过 `1/HUOLI_REGEN_PER_SEC` 秒自然回 1 点，封顶 HERO_HUOLI_MAX。
        调用点：主循环每次收到上行包时、进世界时、升级时、制作/查费前——让活力是
        「时间函数」而不是「战斗函数」。

        返回本次实际回的活力点数（便于 GM 面板与测试断言）；`advance` 仅供测试：
        直接把计时基准往前推 `advance` 秒后再结算。
        """
        if now is None:
            now = time.time()
        if advance:                       # 仅供测试：把计时基准往前推 advance 秒
            now = self._huoli_ts + float(advance)
        dt = now - self._huoli_ts
        if dt <= 0:
            return 0
        self.huoli_regen_acc += dt * HUOLI_REGEN_PER_SEC
        whole = int(self.huoli_regen_acc)
        got = 0
        if whole >= 1 and self.huoli < HERO_HUOLI_MAX:
            room = HERO_HUOLI_MAX - self.huoli
            got = min(whole, room)
            self.huoli = min(HERO_HUOLI_MAX, self.huoli + got)
            self.huoli_regen_acc -= got
            if self.huoli_regen_acc < 0:
                self.huoli_regen_acc = 0.0
        else:
            # 已满：把累积的小数位清空，避免「满着也一直攒」后瞬间爆加
            self.huoli_regen_acc = 0.0
        self._huoli_ts = now
        if got:
            self._emit('huoli.regen', '活力恢复 %d（→%d）' % (got, self.huoli),
                       gained=got, huoli=self.huoli)
        return got

    def _recipe_of(self, ids):
        """背包实例 id 列表 → (产物 shape 或 None, 造型件数 key)。

        只按背包现有造型折算，**不校验数量**（费用查询 104 用）；制作 102 会另做数量校验。
        """
        shapes = {}
        for iid in ids:
            it = self.items.get(iid)
            if it is None:
                continue
            shapes[it['shape']] = shapes.get(it['shape'], 0) + 1
        key = tuple(sorted(shapes.items()))
        return MAKE_RECIPES.get(key), key

    def _handle_npc_make(self, conn, body):
        """C2S_NPC_MAKE(102)：制作/合成。成功 = 扣材料 → 产物入包 → S2CMakeCompleteNotify(160)。

        body 形态（客户端 `netnpc.py`，五种发送函数）：
          · `C2SMake3`    ：mode=12        + n(1)    + ids(n×4B)
          · `C2SMake11`   ：mode=11/27     + itype(1)+ ids(N×4B)    （extend=16 时 27）
          · 其余（Make/MakeGem/SuperMake）：mode(1) + ids(N×4B)
        ids 是**背包实例 id**，不是造型；配方按「造型→件数」匹配（见 MAKE_RECIPES）。

        失败一律回一条 NPC 对话（客户端制作面板会把提示画在对话框里），**不动背包**。
        """
        if not body:
            return False
        mode = body[0]
        base = mode - 16 if mode >= 16 else mode     # extend=16 的形态归一到基码
        if base == 12:
            n = min(body[1], 8) if len(body) >= 2 else 0
            raw = body[2:2 + 4 * n]
        elif base == 11:
            raw = body[2:]                            # 跳过 itype
        else:
            raw = body[1:]
        raw = raw[:len(raw) - len(raw) % 4]           # 只认整 4B，尾部残字节丢弃
        ids = [struct.unpack('<I', raw[i:i + 4])[0]
               for i in range(0, len(raw), 4)][:8]
        if not ids:
            return False

        # ★ 活力先按在线时长回一点再判定（活力是时间函数，不挂战斗上）
        self._regen_huoli()

        # 实例 → 件数 → 造型件数（同一实例被选两次 = 要两件）
        need = {}
        for iid in ids:
            need[iid] = need.get(iid, 0) + 1
        shapes = {}
        for iid, n in need.items():
            it = self.items.get(iid)
            if it is None:
                conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n这件材料不在背包里。'))
                self.log('   ⚠ 制作被拒：实例 %d 不在背包' % iid)
                self._emit('craft.reject', '制作被拒：材料不在背包', reason='no_item',
                           iid=int(iid))
                return False
            if int(it.get('qty', 1)) < n:
                conn.sendall(build_npc_chat_ext(
                    NPC_NAME, 'P\n%s 数量不够（要 %d 件）。' % (item_name_of(it['shape']), n)))
                self.log('   ⚠ 制作被拒：实例 %d 要 %d 件，只有 %d'
                         % (iid, n, int(it.get('qty', 1))))
                self._emit('craft.reject', '制作被拒：材料数量不足', reason='qty',
                           need=n, have=int(it.get('qty', 1)))
                return False
            shapes[it['shape']] = shapes.get(it['shape'], 0) + n

        key = tuple(sorted(shapes.items()))
        product = MAKE_RECIPES.get(key)
        if product is None:
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n这个配方本服还没收录。'))
            self.log('   ⚠ 制作被拒：配方未收录 %r' % (key,))
            self._emit('craft.reject', '制作被拒：配方未收录', reason='no_recipe')
            return False

        # 两道前置门槛：工艺等级（craftdata.lv）+ 活力（craftdata.cost）
        # ★ cost 是**活力**不是体力：客户端 dlgcraft.py:80 `info['cost'] > m_Huoli`
        info = CRAFT_DATA.get(product) or {}
        need_lv = int(info.get('lv') or 1)
        cost = int(info.get('cost') or (MAKE_COST_HUOLI * max(1, len(ids))))
        if self.life_lv < need_lv:
            # 工艺**升级已落地**（2026-10-22，见 FEATURE_STATUS['craft_levelup']）：
            # 每次成功制作 +1 熟练度，满则升级、溢出累计。所以这里就是普通门禁——
            # 「你的工艺等级还不够」，不再扯「升级没实现」了。
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n你的工艺技术只有 %d 级，这个要 %d 级。'
                % (self.life_lv, need_lv)))
            self.log('   ⚠ 制作被拒：工艺 %d < 需要 %d（多做低阶货攒熟练度即可升级）'
                     % (self.life_lv, need_lv))
            self._emit('craft.reject', '制作被拒：工艺等级不足',
                       reason='life_lv', have=self.life_lv, need=need_lv)
            return False
        if self.huoli < cost:
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n活力不够（要 %d 点，你现在 %d 点）。' % (cost, self.huoli)))
            self.log('   ⚠ 制作被拒：活力 %d < 需要 %d' % (self.huoli, cost))
            self._emit('craft.reject', '制作被拒：活力不足', reason='huoli',
                       have=self.huoli, need=cost)
            return False
        # 金钱门槛：op104 预览报的就是 MAKE_COST_MONEY×件数，实扣必须同额，
        # 否则又是「面板显示 100 金、实际一分不扣」的假承诺（2026-10-20 修复）。
        money = MAKE_COST_MONEY * max(1, len(ids))
        if self.coins < money:
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n金钱不够（要 %d 金，你现在 %d 金）。' % (money, self.coins)))
            self.log('   ⚠ 制作被拒：金钱 %d < 需要 %d' % (self.coins, money))
            self._emit('craft.reject', '制作被拒：金钱不足（差 %d 金）' % (money - self.coins),
                       reason='coins', have=self.coins, need=money)
            return False

        # 背包满则先拒（扣完材料会腾出的格子要算进去，否则「最后一摞正好用完」会被误拒）
        free = sum(1 for p in range(ITEM_POS_BAG_START, ITEM_POS_BAG_END)
                   if not any(it.get('pos') == p for it in self.items.values()))
        freed = sum(1 for iid, n in need.items()
                    if int(self.items[iid].get('qty', 1)) == n)
        if free + freed < 1:
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n背包满了，先腾个位置。'))
            self.log('   ⚠ 制作被拒：背包已满且材料不会腾格')
            self._emit('craft.reject', '制作被拒：背包已满', reason='bag_full')
            return False

        # 扣材料（按实例聚合，避免同一摞发多帧 ItemQuantity）
        for iid, n in need.items():
            self._take_from_iid(conn, iid, n)

        # ★ P3-05：活力与金钱**一次扣**（全成功或全不动）。
        #   原先两行 `max(0, …)` 各自为政 —— 钱扣成了、活力没扣也没人会发现。
        #
        #   ⚠ 顺序上的已知取舍（2026-10-23）：扣费**排在扣材料之后**，因为
        #     op102 的帧序是已验证契约（`_test_dispatch` 锁死了
        #     ItemQuantity → PropChange×2 → SkillLevel → ItemAdd → …）。
        #     曾试过把扣费提到扣材料之前以求「费用失败则材料未动」，代价就是
        #     改这条帧序 —— 按「不改变已验证帧序」的约束，改回原序。
        #     残余风险：扣费失败时材料已扣。但上方已做活力/金钱/背包三重预检，
        #     走到这里扣费只会因账本拒记而失败，实战概率≈0（且会明确告警）。
        reason = '制作扣费（%d 金/件 × %d 件）' % (MAKE_COST_MONEY, max(1, len(ids)))
        paid = self._econ_spend_multi(
            conn, [(eled.KIND_HUOLI, cost), (eled.KIND_COIN, money)], reason)
        if not paid.ok:
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n制作未能完成：%s' % paid.message))
            self.log('   ⚠ 制作失败：%s（材料已扣，需人工核对）' % paid.message)
            self._emit('craft.reject', '制作失败：%s' % paid.message,
                       reason='econ', code=paid.code)
            return False

        # 熟练度（每次成功 +1 点）
        # ── 工艺熟练度 → 技能等级（2026-10-22 实现，craft_levelup）──────────
        #   +1 熟练度；满本级阈值则升级、溢出累计到下一级；封顶 LIFE_LV_MAX。
        #   阈值曲线是 SIM（原服曲线不在本仓库），但「做→攒→升」这条成长循环是真跑通的。
        self.life_exp += 1
        up = 0
        need = life_exp_need(self.life_lv)
        while self.life_exp >= need and self.life_lv < LIFE_LV_MAX:
            self.life_exp -= need
            self.life_lv += 1
            up += 1
            need = life_exp_need(self.life_lv)
        if up:
            self._emit('craft.levelup', '工艺技术升至 %d 级' % self.life_lv,
                       level=self.life_lv, from_level=self.life_lv - up)
            self.log('   ★ 工艺升级：%d → %d 级（熟练度溢出 %d）'
                     % (self.life_lv - up, self.life_lv, self.life_exp))
        conn.sendall(build_skill_level_life(
            LIFE_SKILL_CRAFT, self.life_lv, LIFE_LV_MAX, self.life_lv,
            self.life_exp, life_exp_need(self.life_lv)))

        # craftdata 产物（252xx）不在商店表里，名字要从生成表取
        pname = (CRAFT_DATA.get(product) or {}).get('name') or item_name_of(product)
        self._emit('craft.ok', '制作成功：%s' % pname,
                   product=product, name=pname, money=money, huoli=cost)
        new_iid = self._grant_item(conn, product, pname)
        if new_iid is None:                           # 双保险：预检查已挡，理论上到不了
            conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n产物没能放进背包。'))
            return False
        conn.sendall(build_make_complete(product, new_iid, 1, '制造成功：%s ×1' % pname))
        self.log('   制作成功：%s → %s（bag id %d）'
                 % ('+'.join('%s×%d' % (item_name_of(s), c) for s, c in key),
                    pname, new_iid))
        self._persist()
        return True

    # ───────────────────────── 任务闭环（M6）─────────────────────────
    def _task_state(self, tid=TASK_NEWBIE_ID):
        t = self.tasks.get(tid)
        return t['state'] if t else TASK_STATE_NONE

    def _task_desc(self, tid=TASK_NEWBIE_ID):
        """任务栏描述（带进度）。同 tid 重发 TaskAdd 时客户端会先 RemoveTask 再重建。"""
        t = self.tasks.get(tid)
        if not t:
            return '击败毛草团'
        # 主线剧情：描述 = 当前步骤的目标（数据在 quest_data.py）
        qid = t.get('qid')
        if qid in questmod.QUESTS:
            st = questmod.step_of(qid, int(t.get('step', 0)))
            return st['desc'] if st else '回任务 NPC 复命'
        return '击败毛草团（%d/%d）' % (min(t['progress'], TASK_NEWBIE_NEED), TASK_NEWBIE_NEED)

    def _npc_give_prompt(self, conn, npcid, gtype):
        """op97 C2SGiveNpc：记下「要交给谁」，随后的 op105 才知道目标 NPC。"""
        self.give_npc = npcid
        self.give_gtype = gtype
        self.log('   （进入给予态：目标 NPC=%d gtype=%d）' % (npcid, gtype))

    def _handle_npc_give(self, conn, body):
        """op105 C2SNpcGive(itype,targetid,cash,objs)：把物品/宠物/金钱交给 NPC。

        线序（netnpc.C2SNpcGive）：targetid(4) + itype(1) + cash(4)
          itype=0 (GIVETYPE_ITEM)：[ob(4) + amount(1)] × n
          itype=1 (GIVETYPE_PET) ：[ob(4)]            × n
        本服单机无交任务业务，但**必须真的扣掉背包里的东西并回执**，
        否则客户端看着物品消失又出现。扣完更新背包并落盘。
        """
        targetid = struct.unpack('<I', body[0:4])[0]
        itype = body[4]
        cash = struct.unpack('<I', body[5:9])[0]
        pairs = []                     # (ob, amount)
        rest = body[9:]
        if itype == 0:                 # 物品：ob(4) + amount(1)
            i = 0
            while i + 5 <= len(rest):
                ob = struct.unpack('<I', rest[i:i + 4])[0]
                amt = rest[i + 4]
                pairs.append((ob, amt or 1))
                i += 5
        elif itype == 1:               # 宠物：仅 ob(4)
            i = 0
            while i + 4 <= len(rest):
                pairs.append((struct.unpack('<I', rest[i:i + 4])[0], 1))
                i += 4
        self.log('   >>> 交付 NPC=%d itype=%d cash=%d objs=%s'
                 % (targetid, itype, cash, pairs))

        name = self._npc_name(targetid) if targetid else (
            self._npc_name(self.cur_npc) if self.cur_npc is not None else NPC_NAME)

        if targetid not in self._visible_npc_ids():
            self.log('   ⚠ 目标 NPC %d 不在当前地图，忽略交付' % targetid)
            return False

        # 金钱：只有真付得起才扣（P3-05 收口；失败就不扣，且要如实上报付了多少）
        paid_cash = 0
        if cash > 0:
            res = self._econ_spend(conn, cash, '交付 NPC %d' % targetid)
            if res.ok:
                paid_cash = cash
            else:
                self.log('   ⚠ 金钱不足（有 %d 需 %d），只交付物品' % (self.coins, cash))

        # 物品：逐实例扣数量
        given = 0
        for (ob, amt) in pairs:
            it = self.items.get(ob)
            if it is None:
                self.log('   ⚠ 物品 %d 不在背包，跳过' % ob)
                continue
            take = min(int(it.get('qty', 1)), amt)
            it['qty'] -= take
            given += take
            self._send_item_consumed(conn, ob, it)

        self._persist()
        conn.sendall(build_npc_chat_ext(name, 'P\n谢谢，我收下了。\nQ不客气。'))
        # ★ 原先这里打的是请求里的 `cash`，钱不够时也会显示「已交付 N 金钱」——
        #   日志在撒谎。改成实际扣到的金额（2026-10-23，P3-05 收口时发现）。
        self.log('   >>> 交付完成：%d 件物品，%d 金钱' % (given, paid_cash))
        return True

    def _npc_item_chosen(self, conn, iid, num):
        """op113/op127：NPC 对话框里选中一个背包物品（选物/选数量）。

        单机没有「给 NPC 的任务物品」业务，但要让客户端拿到可理解的反馈，
        而不是静默——静默会让对话框卡在选择态。
        """
        it = self.items.get(iid)
        if it is None:
            self.log('   （选中的物品 %d 不在背包，忽略）' % iid)
            return False
        nm = item_name_of(it['shape'])
        self.log('   （选中 %s ×%d）' % (nm, num))
        name = (self._npc_name(self.cur_npc) if self.cur_npc is not None else NPC_NAME)
        # 记录选择结果，方便后续对话逻辑读取
        self.npc_chosen = (iid, num)
        conn.sendall(build_npc_chat_ext(
            name, 'P\n你选了%s ×%d。\nQ好的。' % (nm, num)))
        return True

    def _npc_input(self, conn, msg):
        """op115：NPC 对话的输入框提交（如存钱数额、改名）。"""
        txt = (msg or '').strip()
        self.log('   （对话输入 %r）' % txt)
        name = (self._npc_name(self.cur_npc) if self.cur_npc is not None else NPC_NAME)
        if not txt:
            conn.sendall(build_npc_chat_ext(name, 'P\n你什么都没输入。\nQ再想想。'))
            return False
        self.npc_input_text = txt
        conn.sendall(build_npc_chat_ext(
            name, 'P\n收到：%s\nQ知道了。' % txt[:40]))
        return True

    # ─────────────────────── 组队（op72 → op82） ───────────────────────
    def _team_state(self):
        """队伍对象随角色身份（id/名字）同步——建角/选角之后才能对上人。"""
        st = self.team
        new_id = self.hero_id or 1
        new_name = self.hero_name or '你'
        if st.leader_id != new_id or not st._self_template.get('name'):
            was_active = st.active
            members = st.members
            st.leader_id = new_id
            st._self_template['id'] = new_id
            st._self_template['name'] = new_name
            st._self_template['grade'] = self.grade
            if was_active and members:
                members[0].update({'id': new_id, 'name': new_name,
                                   'grade': self.grade})
        else:
            st._self_template['grade'] = self.grade
            if st.members:
                st.members[0]['grade'] = self.grade
        # 存档还原出来的队伍只有队长自己：补齐陪同队友，让面板有内容可操作
        # ★ 传 st 进去，别让 _team_enroll_dummies 再回调 _team_state（会无限递归）
        if st.active and len(st.members) == 1:
            self._team_enroll_dummies(st)
        return st

    def _team_send_status(self, conn):
        st = self._team_state()
        conn.sendall(teammod.build_team_status(st.status_of(self.hero_id)))

    def _team_send_list(self, conn):
        """S2CTeamList：成员名册 + 阵型。这是客户端队伍面板的主数据源。"""
        st = self._team_state()
        conn.sendall(teammod.build_team_list(
            st.list_for_wire(),
            lineup=st.lineup,
            canuse=1 if st.is_leader(self.hero_id) else 0))

    def _team_send_info(self, conn):
        st = self._team_state()
        conn.sendall(teammod.build_team_info(
            st.title, self.hero_name or '你',
            st.min_lv, st.max_lv, st.attr_class, st.message))

    def _team_send_teamer_info(self, conn):
        st = self._team_state()
        conn.sendall(teammod.build_teamer_info([
            {'id': m['id'], 'name': m['name'], 'grade': m.get('grade', 1),
             'target': st.target, 'class': m.get('class', 1)}
            for m in st.members]))

    def _team_send_gps(self, conn):
        """S2CGPS：把每个成员的位置告诉客户端（用于队友追踪线）。"""
        st = self._team_state()
        for i, m in enumerate(st.members):
            conn.sendall(teammod.build_team_gps(
                m['id'], self.mapid, self.px + i, self.py, m['name']))

    def _team_refresh(self, conn):
        """建队/变更后的整体同步：状态 → 名册 → 队伍信息 → 成员定位。"""
        self._team_send_status(conn)
        self._team_send_list(conn)
        self._team_send_info(conn)
        self._team_send_gps(conn)

    def _handle_player_team(self, conn, body):
        """op72 C2S_PLAYER_TEAM：sub(1) + 参数（netteam.py 的 17 个子命令）。"""
        if not body:
            self.log('   （组队指令缺 sub，忽略）')
            return False
        sub = body[0]
        st = self._team_state()
        me = self.hero_id

        if sub == teammod.T_MAKETEAM and len(body) >= 5:
            # C2SMakeTeam(id)：id=0 是自己建队；非 0 是邀请别人建队（单人无对手方）
            target = struct.unpack('<I', body[1:5])[0]
            self.log('   >>> 建队 target=%d' % target)
            if target != 0:
                conn.sendall(teammod.build_team_status(st.status_of(me)))
                self.log('   （目标是其他玩家，单机无对手方，忽略邀请）')
                return False
            if st.create():
                self._team_enroll_dummies()
                self.log('   >>> 建队成功（队长=%s id=%d，陪同 %d 人）'
                         % (self.hero_name or '你', me, len(st.members) - 1))
            else:
                self.log('   （已在队伍中，建队幂等）')
            self._team_refresh(conn)

        elif sub == teammod.T_ESCAPE:
            # C2SEscape()：离队 / 解散（队长）——队伍面板的「离队」按钮
            self.log('   >>> 离队/解散')
            if st.active:
                st.dismiss()
                conn.sendall(teammod.build_team_status(teammod.TEAM_NONE))
                conn.sendall(teammod.build_team_list([]))
                self.log('   （队伍已解散）')
            else:
                conn.sendall(teammod.build_team_status(teammod.TEAM_NONE))
            self._persist()

        elif sub == teammod.T_CANCELRECRUIT:
            self.log('   （取消招募：单机无招募池，忽略）')

        elif sub == teammod.T_REQMEMBERINFO:
            # C2SRequestMemberInfo()：面板要名册
            self.log('   >>> 队名册请求')
            self._team_send_list(conn)
            self._team_send_teamer_info(conn)

        elif sub == teammod.T_SETLINEUP and len(body) >= 2:
            # C2SSetLineup(idx)：阵型（0..N），只有队长可改
            idx = body[1]
            if not st.is_leader(me):
                self.log('   （非队长，改阵型被忽略）')
            else:
                st.lineup = idx
                self._team_send_list(conn)
                self.log('   >>> 阵型设为 %d → S2CTeamList' % idx)

        elif sub == teammod.T_KICKOUT and len(body) >= 5:
            pid = struct.unpack('<I', body[1:5])[0]
            if not st.is_leader(me):
                self.log('   （非队长，踢人被忽略）')
            elif st.remove(pid):
                self._team_send_list(conn)
                self.log('   >>> 踢出成员 id=%d → S2CTeamList' % pid)
            else:
                self.log('   （成员 id=%d 不存在或不可踢）' % pid)

        elif sub == teammod.T_GETTEAMINFO and len(body) >= 5:
            self.log('   >>> 查询队伍信息 tid=%d'
                     % struct.unpack('<I', body[1:5])[0])
            self._team_send_info(conn)
            self._team_send_list(conn)

        elif sub == teammod.T_FREEMODE:
            # C2SFreeMode()：非队长点「暂离」回来
            self.log('   >>> 归队（FreeMode）')
            st.reposition(me, 0)
            self._team_send_status(conn)
            self._team_send_list(conn)

        elif sub in (teammod.T_ACCEPTJOIN, teammod.T_RESPREQ):
            self.log('   （加入/邀请应答：单机无其他玩家发起，忽略）')

        elif sub == teammod.T_RECRUIT and len(body) >= 24:
            title = body[1:21].rstrip(b'\x00').decode('gbk', 'replace')
            imin, imax, iclass = body[21], body[22], body[23]
            msg = body[24:].rstrip(b'\x00').decode('gbk', 'replace')
            st.title = title or st.title
            st.min_lv, st.max_lv, st.attr_class = imin, imax, iclass
            st.message = msg or st.message
            self.log('   >>> 招募设置 title=%r lv=%d-%d class=%d msg=%r'
                     % (title, imin, imax, iclass, msg))
            self._team_send_info(conn)
            self._team_send_list(conn)
            self._persist()

        elif sub == teammod.T_CHANGE_TARGET and len(body) >= 2:
            st.target = body[1]
            self.log('   >>> 队伍目标 = %d' % st.target)
            conn.sendall(teammod.build_team_target(st.target))

        elif sub == teammod.T_CHANGELEADER and len(body) >= 5:
            self.log('   （换队长：单机队长固定为本人，忽略）')
        elif sub in (teammod.T_QUEST_TEAMINFO, teammod.T_QUEST_TEAMERINFO):
            self.log('   （队伍查询 sub=%d）：回当前队伍' % sub)
            self._team_send_info(conn)
            self._team_send_teamer_info(conn)
        elif sub in (teammod.T_INVITE_RETURN, teammod.T_INVITE_TRANSFER):
            self.log('   （邀请归队/转让 sub=%d：单机无其他玩家，忽略）' % sub)
        else:
            self.log('   （组队 sub=%d 未实现，忽略）' % sub)
        return True

    def _team_enroll_dummies(self, st=None):
        """建队后挂上「陪同修炼」的队友，让面板有真实内容可操作。

        接受调用方传入的 st，避免回调 _team_state()（那是本函数的调用者，会递归）。
        """
        if st is None:
            st = self.team
        if not st.active:
            return
        nxt = max(st.member_ids() + [self.hero_id or 1]) + 1
        for (name, grade, cls) in TEAM_DUMMY_MEMBERS:
            if len(st.members) >= teammod.TEAM_MAX_MEMBERS:
                break
            st.add_simulated(name, grade=grade, cls=cls, pid=nxt)
            nxt += 1

    # ─────────────────────── 组队结束 ───────────────────────

    def _send_task_add(self, conn, tid=TASK_NEWBIE_ID):
        t = self.tasks.get(tid) or {}
        qid = t.get('qid')
        if qid in questmod.QUESTS:
            name = questmod.quest(qid)['name']
        else:
            name = TASK_NEWBIE_NAME
        conn.sendall(build_task_add(tid, name, self._task_desc(tid), 1))

    def _send_all_tasks(self, conn):
        """登录后把**所有进行中**的任务重发给客户端（新手任务 + 主线）。

        ★ 客户端只在收到 S2CTaskAdd 时才建任务栏；`C2S_GET_TASK(48)` 在正常玩法里
          **从不发**（全量检索：只有调试脚本 gwtest.py 调 `nettask.C2SGetTask`）。
          所以不在登录时推一遍，重登后任务栏就是空的 —— 玩家看不到自己做到第几步，
          剧情 NPC 也还是照常能点，看起来像「任务丢了」。
          这里与 `C2S_GET_TASK` 走同一个 `_send_task_add`，两条路径不会不同步。
        """
        sent = []
        for tid in sorted(self.tasks):
            if not self._task_state(tid):
                continue
            self._send_task_add(conn, tid)
            sent.append(self._task_desc(tid))
        if sent:
            self.log('   + 任务栏重发 S2CTaskAdd×%d（%s）'
                     % (len(sent), '；'.join(sent)))
        return sent

    def _npc_welcome_msg(self):
        """NPC 首页随任务状态变化；选项序号恒为 1=任务 / 2=商店 / 3=收购 / 4=离开。"""
        st = self._task_state()
        if st == TASK_STATE_DONE:
            return ('P\n好样的，毛草团已经教训完了，来领赏吧。\nQ交任务。\nQ我想买点东西。'
                    '\nQ我想卖点东西。\nQ没事，我随便看看。')
        if st == TASK_STATE_DOING:
            p = self.tasks[TASK_NEWBIE_ID]['progress']
            return ('P\n毛草团教训得怎么样了？（%d/%d）\nQ我还在打。\nQ我想买点东西。'
                    '\nQ我想卖点东西。\nQ没事，我随便看看。'
                    % (p, TASK_NEWBIE_NEED))
        return NPC_CHAT_WELCOME

    def _npc_task_choice(self, conn):
        """NPC 选项 1：按任务状态分派 接任务 / 进度提示 / 交任务。"""
        st = self._task_state()
        if st == TASK_STATE_DONE:
            self._turn_in_task(conn)
        elif st == TASK_STATE_DOING:
            p = self.tasks[TASK_NEWBIE_ID]['progress']
            conn.sendall(build_npc_chat_ext(
                NPC_NAME, 'P\n还差 %d 只毛草团（%d/%d），再加把劲。\nQ知道了。'
                % (TASK_NEWBIE_NEED - p, p, TASK_NEWBIE_NEED)))
        else:
            self.tasks[TASK_NEWBIE_ID] = {'state': TASK_STATE_DOING, 'progress': 0}
            self._send_task_add(conn)
            self.log('   >>> 接任务 → S2CTaskAdd(%s 0/%d)' % (TASK_NEWBIE_NAME, TASK_NEWBIE_NEED))
            conn.sendall(build_npc_chat_ext(NPC_NAME, NPC_CHAT_TASK_OK))
            self._persist()

    def _turn_in_task(self, conn):
        """交任务：S2CTaskRemove(73) + 奖励（金钱/经验/道具），并把金钱与经验同步到客户端。"""
        self.tasks.pop(TASK_NEWBIE_ID, None)
        conn.sendall(build_task_remove(TASK_NEWBIE_ID))
        # ★ 一个任务只能交一次：这里先 pop 任务再发奖，若上游有人重复调用，
        #   玩家就会「一个任务拿两份钱」。用幂等键钉住（P3-05）。
        #   C2S 帧没有请求序号，做不到网络级重放防护（见报告 §限制），
        #   但业务级「同一任务只能结算一次」是可以做到的。
        self._econ_gain(conn, TASK_REWARD_COINS, '新手任务奖励',
                        idem='turnin:%d' % TASK_NEWBIE_ID)
        self.exp += TASK_REWARD_EXP
        conn.sendall(build_prop_change(PROP_IDX_EXP, self.exp, 4))
        conn.sendall(build_prop_change(PROP_IDX_NEXTEXP, self.next_exp, 4))
        self._emit_econ('econ.exp', TASK_REWARD_EXP, self.exp, '新手任务奖励')
        got = []
        for (shape, qty, name) in TASK_REWARD_ITEMS:
            for _ in range(qty):
                iid = self._alloc_iid()
                self.items[iid] = {'shape': shape, 'key': 0, 'qty': 1, 'pos': 0, 'infoext': name}
                conn.sendall(build_item_add(ITEM_WTYPE_NORMAL, iid, shape, 0, 1, 0))
            got.append('%s×%d' % (name, qty))
        conn.sendall(build_npc_chat_ext(
            NPC_NAME, 'P\n任务完成！奖励：金钱 %d、经验 %d、%s。\nQ多谢村长。'
            % (TASK_REWARD_COINS, TASK_REWARD_EXP, '、'.join(got))))
        self.log('   >>> 交任务 → S2CTaskRemove + 奖励（金钱+%d 经验+%d %s）'
                 % (TASK_REWARD_COINS, TASK_REWARD_EXP, '、'.join(got)))
        self._persist()

    # ─────────────────── 主线剧情 1–3（2026-10-22）───────────────────
    # 数据在 quest_data.py（真值：剧情名/触发 NPC/坐标/等级门槛/剧情点/流程；
    # 模拟：台词措辞、战斗怪物造型、金钱与经验数值）。
    # 状态机：tasks[qid] = {state, progress, step, qid}，step = 当前第几步。
    def _quest_active(self):
        """当前进行中的主线任务 (qid, task)；没有则 (None, None)。"""
        for _tid, t in self.tasks.items():
            if t.get('qid') in questmod.QUESTS and t.get('state') == TASK_STATE_DOING:
                return int(t['qid']), t
        return None, None

    def _quest_step(self, qid):
        t = self.tasks.get(int(qid))
        if not t:
            return None
        return questmod.step_of(qid, int(t.get('step', 0)))

    def _quest_item_targets(self, st):
        if st.get('any_shapes'):
            return set(int(x) for x in st['any_shapes'])
        ent = questmod.ITEM.get(st.get('item'))
        return {ent[0]} if ent else set()

    def _quest_item_count(self, st):
        tgt = self._quest_item_targets(st)
        return sum(int(v.get('qty', 0)) for v in self.items.values()
                   if int(v.get('shape', 0)) in tgt)

    def _quest_take_items(self, conn, st):
        """按数量扣任务物品（跨多格累加），逐格回 ItemQuantity/ItemDel。"""
        need = int(st.get('qty', 1))
        tgt = self._quest_item_targets(st)
        for iid in sorted(self.items):
            if need <= 0:
                break
            it = self.items.get(iid)
            if not it or int(it.get('shape', 0)) not in tgt:
                continue
            take = min(need, int(it.get('qty', 0)))
            if take <= 0:
                continue
            it['qty'] = int(it['qty']) - take
            need -= take
            self._send_item_consumed(conn, iid, it)
        return need <= 0

    def _npc_quest_dialog(self, conn, npcid):
        """NPC 点击：主线对话。返回 True 表示已接管（不再走通用问候）。"""
        name = self._npc_name(npcid)
        qid, _t = self._quest_active()
        if qid:
            st = self._quest_step(qid)
            if st and st['map'] == self.mapid and st['npc'] == name:
                return self._quest_prompt_step(conn, qid, st, name)
            if st:
                scene = MAPS.get(st['map'], {}).get('name', '?')
                self._quest_prompt = None
                conn.sendall(build_npc_chat_ext(
                    name, 'P\n先去%s找%s。\nQ知道了。' % (scene, st['npc'])))
                return True

        cands = questmod.start_quests_at(self.mapid, name)
        if not cands:
            return False
        # 一个 NPC 可能挂着多条剧情（金大有管 7/12、龙蛇老人管 10/15）：按编号取
        # **第一条还能接的**，而不是「第一条」——否则前一条做完后这个 NPC 就哑了。
        picked = None
        reject = None                      # (qid, reason, have, need) 用于日志/事件
        active_qid, _at = self._quest_active()
        for qid in cands:
            if qid in self.quests_done:
                continue
            q = questmod.quest(qid)
            missing = [p for p in questmod.prev_of(qid) if p not in self.quests_done]
            if missing:
                reject = (qid, 'prev', sorted(self.quests_done), missing)
                continue
            if self.grade < q['level']:
                reject = (qid, 'grade', self.grade, q['level'])
                continue
            picked = qid
            break
        if picked is None:
            if reject:
                qid, reason, have, need = reject
                q = questmod.quest(qid)
                if reason == 'prev':
                    txt = ('P\n「%s」之前还有旧事没了结。\nQ我先把前面的做完。'
                           % q['name'])
                    self.log('   （主线 %d「%s」前置未完成：缺 %s）'
                             % (qid, q['name'], need))
                else:
                    txt = ('P\n这事至少要 %d 级才扛得住，你现在还不够。\nQ我再练练。'
                           % q['level'])
                    self.log('   （主线 %d「%s」等级不足：%d/%d）'
                             % (qid, q['name'], have, need))
                conn.sendall(build_npc_chat_ext(name, txt))
                self._emit('quest.reject', '接取被拒：%s' % reason,
                           reason=reason, qid=int(qid), name=q['name'],
                           have=have, need=need)
                return True
            conn.sendall(build_npc_chat_ext(
                name, 'P\n这件事已经了结了，多谢你。\nQ客气。'))
            return True
        q = questmod.quest(picked)
        # 引擎约束（不是资料条款）：任务栏/步骤提示/剧情战斗上下文都按「只有一条
        # 进行中的主线」设计（`_quest_active()` 只返回第一条）。允许并行接取会让
        # 「当前第几步」失去唯一解，所以这里挡住第二条。
        if active_qid:
            aq = questmod.quest(active_qid)
            conn.sendall(build_npc_chat_ext(
                name, 'P\n你手上的「%s」还没了结，先办完它。\nQ我这就去。' % aq['name']))
            self.log('   （拒绝接第二条主线：%d「%s」进行中，第 %s 步）'
                     % (active_qid, aq['name'], (self.tasks.get(active_qid) or {}).get('step', 0)))
            self._emit('quest.reject', '接取被拒：已有进行中主线 %d' % active_qid,
                       reason='busy', qid=int(picked), name=q['name'],
                       have=int(active_qid))
            return True
        self._quest_prompt = {'qid': picked, 'action': 'accept'}
        conn.sendall(build_npc_chat_ext(name, q['intro']))
        self.log('   >>> 主线 %d「%s」可接取（Lv%d≥%d，剧情点 %d）'
                 % (picked, q['name'], self.grade, q['level'], q['point']))
        return True

    def _quest_prompt_step(self, conn, qid, st, name):
        """当前步骤 NPC：按 kind 决定可执行动作（talk/fight/item/finish）。"""
        kind = st['kind']
        if kind == 'item':
            have = self._quest_item_count(st)
            need = int(st.get('qty', 1))
            if have < need:
                self._quest_prompt = None
                conn.sendall(build_npc_chat_ext(
                    name, 'P\n需要 %s×%d，你只带了 %d。\nQ我再去准备。'
                    % (st.get('item', '物品'), need, have)))
                self._emit('quest.reject',
                           '交物品被拒：%s %d/%d' % (st.get('item', '物品'), have, need),
                           reason='item', qid=int(qid), item=st.get('item', '物品'),
                           have=have, need=need, step=int(self.tasks.get(int(qid), {}).get('step', 0)))
                return True
            action = 'item'
        elif kind == 'fight':
            action = 'fight'
        elif kind == 'finish':
            action = 'finish'
        else:
            action = 'talk'
        self._quest_prompt = {'qid': qid, 'action': action}
        conn.sendall(build_npc_chat_ext(name, st['text']))
        return True

    def _quest_answer(self, conn, choice):
        """NPC 选项应答：执行 `_quest_prompt` 记录的动作。返回 True=已处理。"""
        p = self._quest_prompt
        if not p:
            return False
        self._quest_prompt = None
        if choice != 1:
            nm = self._npc_name(self.cur_npc) if self.cur_npc is not None else 'NPC'
            conn.sendall(build_npc_chat_ext(nm, 'P\n想好了再来找我。'))
            return True
        qid = int(p['qid'])
        action = p['action']
        if action == 'accept':
            self._quest_accept(conn, qid)
        elif action == 'talk':
            # 对话步的奖励（资料里「剧情点1点」也可能挂在交物品/对话步上，
            # 例如剧情 6 第 3 步「给美人鱼草编花环 → 剧情点 1」）。
            # 战斗步由 `_quest_battle_won` 发、收尾步由 `_quest_complete` 发，
            # 两条路径互斥，不会重复。
            self._quest_grant(conn, (self._quest_step(qid) or {}).get('reward'))
            self._quest_advance(conn, qid)
        elif action == 'item':
            st = self._quest_step(qid)
            if not st or not self._quest_take_items(conn, st):
                conn.sendall(build_npc_chat_ext(
                    self._npc_name(self.cur_npc) if self.cur_npc is not None else 'NPC',
                    'P\n东西不够，再来一次吧。'))
                return True
            self._quest_grant(conn, (st or {}).get('reward'))
            self._quest_advance(conn, qid)
        elif action == 'fight':
            self._quest_fight(conn, qid)
        elif action == 'finish':
            self._quest_complete(conn, qid)
        return True

    def _quest_accept(self, conn, qid):
        q = questmod.quest(qid)
        st = q['steps'][0]
        _before = self._visible_npc_ids()
        self.tasks[qid] = {'state': TASK_STATE_DOING, 'progress': 0,
                           'step': 0, 'qid': qid}
        self._refresh_spawns_if_changed(conn, _before)
        conn.sendall(build_task_add(qid, q['name'], st['desc'], 1))
        conn.sendall(build_npc_chat_ext(q['start_npc'], q['accept']))
        self.log('   >>> 接主线 %d「%s」→ S2CTaskAdd（%s）'
                 % (qid, q['name'], st['desc']))
        self._emit('quest.accept', '接取主线 %d「%s」（Lv%d）' % (qid, q['name'], q['level']),
                   qid=int(qid), name=q['name'], step=0, steps=len(q['steps']),
                   npc=q['start_npc'], mapid=q['start_map'], target=st['desc'])
        self._persist()

    def _quest_advance(self, conn, qid, quiet=False):
        t = self.tasks.get(int(qid))
        if not t:
            return
        q = questmod.quest(qid)
        _before = self._visible_npc_ids()
        t['step'] = int(t.get('step', 0)) + 1
        nxt = questmod.step_of(qid, t['step'])
        if not nxt:
            self._quest_complete(conn, qid)
            return
        self._refresh_spawns_if_changed(conn, _before)
        conn.sendall(build_task_add(qid, q['name'], nxt['desc'], 1))
        if not quiet:
            nm = self._npc_name(self.cur_npc) if self.cur_npc is not None else q['start_npc']
            conn.sendall(build_npc_chat_ext(nm, 'P\n%s\nQ这就去。' % nxt['desc']))
        self.log('   >>> 主线 %d 进度 → 第 %d 步：%s' % (qid, t['step'], nxt['desc']))
        self._emit('quest.step', '主线 %d「%s」→ 第 %d/%d 步' % (qid, q['name'], t['step'], len(q['steps'])),
                   qid=int(qid), name=q['name'], step=t['step'], steps=len(q['steps']),
                   kind_step=nxt['kind'], npc=nxt['npc'], mapid=nxt['map'],
                   target=nxt['desc'])
        self._persist()

    def _quest_fight(self, conn, qid):
        t = self.tasks.get(int(qid))
        st = self._quest_step(qid)
        if not t or not st:
            return
        self._quest_battle = {'qid': int(qid), 'step': int(t.get('step', 0)),
                              'name': st['npc'], 'shape': int(st['shape'])}
        conn.sendall(build_npc_chat_ext(
            st['npc'], 'P\n%s 挡在面前！\nQ动手！' % st['npc']))
        self.log('   >>> 剧情战斗：%s（qid=%d step=%d shape=%d%s）'
                 % (st['npc'], qid, t.get('step'), st['shape'],
                    ' SIM造型' if st.get('shape_sim') else ''))
        self._start_war(conn)

    def _quest_complete(self, conn, qid):
        q = questmod.quest(qid)
        t = self.tasks.get(int(qid))
        st = self._quest_step(qid) if t else None
        reward = (st or {}).get('reward') or q['steps'][-1].get('reward') or {}
        _before = self._visible_npc_ids()
        self._quest_grant(conn, reward)
        self.tasks.pop(int(qid), None)
        self._refresh_spawns_if_changed(conn, _before)
        conn.sendall(build_task_remove(int(qid)))
        self.quests_done.add(int(qid))
        conn.sendall(build_npc_chat_ext(
            q['start_npc'], 'P\n「%s」已完成。\nQ多谢。' % q['name']))
        self.log('   ★ 主线完成 %d「%s」· 累计剧情点 %d'
                 % (qid, q['name'], self.story_point))
        self._emit('quest.complete', '主线 %d「%s」完成' % (qid, q['name']),
                   qid=int(qid), name=q['name'], story_point=self.story_point)
        self._persist()

    def _quest_grant(self, conn, reward):
        """发任务奖励：金钱/经验（SIM）、物品（真 id）、剧情点（真值）、领悟招式。"""
        if not reward:
            return
        coins = int(reward.get('coins') or 0)
        if coins:
            self._econ_gain(conn, coins, '剧情奖励')
        exp = int(reward.get('exp') or 0)
        if exp:
            self.exp += exp
            conn.sendall(build_prop_change(PROP_IDX_EXP, self.exp, 4))
            conn.sendall(build_prop_change(PROP_IDX_NEXTEXP, self.next_exp, 4))
            self._emit_econ('econ.exp', exp, self.exp, '剧情奖励')
        point = int(reward.get('point') or 0)
        if point:
            self.story_point += point
            conn.sendall(build_prop_change(PROP_IDX_STORY_POINT, self.story_point, 2))
            self._emit('quest.point', '剧情点 +%d → %d' % (point, self.story_point),
                       delta=point, after=self.story_point)
        got = []
        for it in reward.get('items') or []:
            shape = it.get('shape')
            if shape:
                self._grant_item(conn, int(shape), it['name'], int(it.get('qty', 1)))
                got.append('%s×%d' % (it['name'], int(it.get('qty', 1))))
            else:
                self.log('   ⚠ 奖励「%s」在本版 itemdata 查无此物，记空（%s）'
                         % (it.get('name'), it.get('note', '')))
        if reward.get('learn_magic'):
            for pid in LEARN_MAGIC_IDS:
                if pid not in self.magics:
                    self.magics[pid] = 1
                    conn.sendall(build_skill_level_magic(pid, 1))
                    conn.sendall(build_skill_equip(pid, 1))
                    got.append('招式%d' % pid)
                    break
        if got or coins or exp or point:
            self.log('   奖励 → 金 %d / 经验 %d / 剧情点 %d / %s'
                     % (coins, exp, point, '、'.join(got) or '无物品'))
            self._emit('quest.reward',
                       '奖励：金 %d / 经验 %d / 剧情点 %d / %s'
                       % (coins, exp, point, '、'.join(got) or '无物品'),
                       coins=coins, exp=exp, point=point, items=list(got))

    def _quest_battle_won(self, conn):
        """剧情战斗胜利：发该步奖励并推进到下一步。"""
        qb = self._quest_battle
        if not qb:
            return
        self._quest_battle = None
        qid = int(qb['qid'])
        t = self.tasks.get(qid)
        if not t or int(t.get('step', 0)) != int(qb['step']):
            return
        st = self._quest_step(qid)
        self._quest_grant(conn, (st or {}).get('reward'))
        self.log('   ★ 剧情战斗胜利：%s' % qb.get('name'))
        self._quest_advance(conn, qid, quiet=True)

    def _on_enemy_dead(self, conn):
        """敌方单位被击杀 → 推进进行中任务的击杀计数，并刷新任务栏。

        ★ 单只入口，保留给「逐个结算」的调用方；批量请用 `_on_enemies_dead()`，
          那里只发一次 TaskAdd、只落一次盘。
        """
        return self._on_enemies_dead(conn, 1)

    def _on_enemies_dead(self, conn, n=1):
        """一次击杀 n 只敌方 → 推进任务计数（**一次** TaskAdd + **一次** 落盘）。

        ★ 为什么改成批量：`_after_hero_action` 一次全灭 3 只时会逐只调单只入口，
          于是同一个任务栏被连发 3 遍、存档被连写 3 次。落盘是异步的，连发 3 次
          会让「最新快照」被同一个值反复覆盖（白写），而 TaskAdd 连发 3 帧则让
          客户端任务栏闪 3 次。语义（每只 +1 进度）完全不变，只是收口成一次。
        """
        n = max(0, int(n))
        if n <= 0:
            return
        t = self.tasks.get(TASK_NEWBIE_ID)
        if not t or t['state'] != TASK_STATE_DOING:
            return
        before = t['progress']
        t['progress'] = min(TASK_NEWBIE_NEED, t['progress'] + n)
        if t['progress'] >= TASK_NEWBIE_NEED:
            t['state'] = TASK_STATE_DONE
            self.log('   ★ 任务达成 %s（%d/%d）→ 回村长处交任务'
                     % (TASK_NEWBIE_NAME, t['progress'], TASK_NEWBIE_NEED))
        else:
            self.log('   任务进度 %s %d→%d/%d'
                     % (TASK_NEWBIE_NAME, before, t['progress'], TASK_NEWBIE_NEED))
        self._send_task_add(conn)
        self._persist()

    # ───────────────────────── 战斗资源（施法消耗）─────────────────────────
    def _reset_hero_res(self):
        """战斗开场恢复资源（与 _start_war 回满血同口径）。"""
        self.hero_res = hero_res_fresh()

    def _magic_level(self, pid):
        """招式的**当前等级**（存档 m_Level），钳到 [1, magicdata 的 max_lv]。

        这是「逐级消耗/威力/效率」的唯一取值口：以前所有结算都按 1 级算，
        玩家把招式练到 6 级后消耗与伤害毫无变化。
        """
        try:
            lv = int(self.magics.get(int(pid), 1) or 1)
        except (TypeError, ValueError):
            lv = 1
        return max(1, min(int(magic_max_level(pid) or 1), lv))

    def _magic_base_skill(self, pid):
        """对应门派基础技能 id（客户端 `SKILLDEPEND[magicdata.type]`）。"""
        return int((MAGIC_EXPR.get(int(pid)) or {}).get('base_skill') or 0)

    def _magic_base_skill_level(self, pid):
        """门派基础技能等级 gslv（未学 = 0）。

        本服没有「门派基础技能」养成（C2S 103 只学招式本身），所以恒为 0 ——
        客户端在同样情况下也是走 `except: gslv = 0` 那条分支，口径一致。
        将来接基础技能只需填 `self.base_skills[技能id]`，这里自动生效。
        """
        base = self._magic_base_skill(pid)
        if not base:
            return 0
        try:
            return max(0, int((getattr(self, 'base_skills', None) or {}).get(base, 0)))
        except (TypeError, ValueError):
            return 0

    def _magic_cost(self, pid):
        """本场**本级**的施法消耗 {m_MP,m_FP,m_At,m_Df,m_Dp}（逐级求值）。

        数据来自 magicdata 的 mana/fp/at/df/dp 表达式（Py2 整除语义，见
        `magic_cost_data.py` 文件头）。注意 at/df/dp 在 magicdata 里是常量：
        只有法力/力道随等级增长，攻击/防御/干扰点按招式固定。
        """
        try:
            return magic_cost_at(int(pid), slv=self._magic_level(pid),
                                 gslv=self._magic_base_skill_level(pid))
        except Exception:                              # noqa: BLE001
            return dict(MAGIC_COST.get(int(pid)) or {})

    def _spend_magic_cost(self, conn, pid):
        """按**本级**消耗扣 m_MP/m_FP/m_At/m_Df/m_Dp，并逐项回 S2CPropChange(17)。

        客户端只在菜单做「可用性」判定（`hero.GetMagicData` 比较 `magic['at']/['df']/['dp']`
        与 `g_Prop.m_At/m_Df/m_Dp`），**实际扣减由服务端执行**——不扣就会出现
        「点数永不减少、招式可无限连发」。
        """
        cost = self._magic_cost(pid)
        if not cost:
            self.log('   （招式 %d 无消耗表项，跳过扣减）' % pid)
            return
        parts = []
        for (name, idx, width) in COST_PROPS:
            delta = max(0, int(cost.get(name, 0) or 0))
            if not delta:
                continue
            self.hero_res[name] = max(0, int(self.hero_res.get(name, 0)) - delta)
            conn.sendall(build_prop_change(idx, self.hero_res[name], width))
            parts.append('%s -%d→%d' % (name, delta, self.hero_res[name]))
        self.log('   招式 %d（%d 级）消耗结算：%s'
                 % (pid, self._magic_level(pid),
                    '，'.join(parts) if parts else '无'))

    def _magic_shortage(self, pid):
        """返回首个不足的战斗资源 `(name, have, need)`；充足则返回 None。

        ★ 2026-10-22 修正：零消耗招式（cost 为 `{}`）此前被 `if not cost` 误判为
        「unknown 不足」而拒绝施放。现在区分：cost 为 None（真无数据）才报 unknown，
        空 dict（已知但免费）视为资源充足。
        """
        cost = self._magic_cost(pid)
        if cost is None:
            return ('unknown', 0, 1)
        if not cost:
            return None
        for name, _idx, _width in COST_PROPS:
            need = max(0, int(cost.get(name, 0) or 0))
            have = max(0, int(self.hero_res.get(name, 0) or 0))
            if have < need:
                return (name, have, need)
        return None

    def _magic_damage(self, pid):
        """招式单目标伤害 —— **SIM 模型**（sim-pow-eff-anchored-v1）。

            dmg = ⌊ 普攻伤害 × (pow(slv)/pow(1)) × powrate(slv)/100 ⌋

        威力(pow) 与效率(powrate) 都取自客户端 magicdata 的表达式逐级求值；
        `pow(1)` 是把威力归一化到**该招式自己的 1 级值**的锚点。

        为什么锚在 1 级 pow、以及这套口径的证据强度（语料 11 次放招命中全是 50、
        对威力零辨识力；语料是本服自录的）全部写在 `MAGIC_DAMAGE_MODEL` 上方的
        大段注释里。**这份实现不外推、也不声称是原服公式。**
        """
        pid = int(pid)
        slv = self._magic_level(pid)
        gslv = self._magic_base_skill_level(pid)
        pow_now = int(magic_pow_at(pid, slv, gslv))
        pow_lv1 = int(magic_pow_at(pid, 1, gslv))
        eff = int(magic_powrate_at(pid, slv, gslv)) or 100
        base = self._hero_attack_damage()
        # 威力归一：1 级 = 1.0；pow 缺失/为 0 时退回 1.0（不靠 pow 制造伤害）
        pow_rate = (float(pow_now) / pow_lv1) if pow_lv1 > 0 else 1.0
        mult = pow_rate * (eff / 100.0)
        mult = max(0.05, min(MAGIC_DAMAGE_MAX_MULT, mult))
        return max(1, int(base * mult))

    def _magic_targets(self, pid, preferred):
        """按招式类别返回目标列表（群攻按 `magic_target_limit` 取 N 个）。

        目标数 = `slv//6+2`（客户端算消耗用的是同一个式子，见 magic_target_limit）。
        非群攻始终只取客户端选择的首目标；指定目标非法/已死时由 `_resolve_target`
        回落到首个存活敌方，并把**回落后的 id** 写进 Perform 帧 —— 客户端按帧里的
        vic_id 播受击动画，所以「服务端打谁」和「画面上谁挨打」永远同一只。
        """
        pid = int(pid)
        first = self._resolve_target(preferred)
        if first is None:
            return []
        limit = magic_target_limit(pid, self._magic_level(pid))
        if limit <= 1:
            return [first]
        alive = [wid for wid in self._enemy_ids() if self.enemy_hp.get(wid, 0) > 0]
        ordered = [first] + [wid for wid in alive if wid != first]
        return ordered[:limit]

    def _hero_war_item(self, conn, target, iid):
        """C2SWarItem（id(1)+item(4)）：战斗中用道具 → 生效后按速度序推进回合。

        ★ 行动合法性：道具不存在 / 本服不可用 / 目标非法 / 目标已满血 —— 属于
          **无效指令**，不消耗本回合，只回 WarTurn 重开指令窗口。旧实现无条件
          推进回合，等于「点错一次道具白送敌方一轮」。
        """
        if not self._accept_turn():
            self.log('   ⚠ 战斗用道具指令越窗/重复 → 忽略')
            return
        if not self._war_item_usable(iid, target):
            self.log('   ⚠ 战斗道具 id=%s 对目标 %s 不可用 → 拒绝并交还操作权'
                     % (iid, target))
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            return
        def act():
            # 目标 = 宠物战斗位时给宠物用（背包里对非出战宠物用药走 op34，不走这里）
            if int(target or 0) == PET_WAR_ID:
                self._item_use_target(conn, iid, PET_WAR_ID)
            else:
                self._item_consume(conn, iid)
            return False
        self._resolve_hero_round(conn, act)

    def _war_item_usable(self, iid, target):
        """战斗道具是否可对 target 使用（只认本服有回血效果的道具）。

        返回 False 的动作入口会回 WarTurn 重开窗口，**不推进回合**。
        """
        it = self.items.get(int(iid or 0))
        if it is None:
            return False
        if ITEM_HEAL.get(it.get('shape'), 0) <= 0:
            return False
        target = int(target or 0)
        if target == PET_WAR_ID:
            return bool(self._pet_alive) and int(self.pet_hp) < self._pet_hpmax()
        if target in (HERO_WAR_ID, 0):
            return int(self.hero_hp) < HERO_HP
        return False

    def _hero_war_summon(self, conn, sub, pid):
        """C2SWarSummon(166)：sub=0 召唤宠物 pid 入场 / sub=1 召回当前宠物。

        客户端两处调用（warui 的宠物菜单）：
          · 选一只**不在场**的宠物 → `DoAct('summon', True, 0, pid)`（op=0 + pid4）
          · 对**已在场**的那只再点一次 → `DoAct('summon', True, 1, 0)`（op=1，仅 1 字节）
        所以「召回」不是收起面板，而是真的把场上那只撤下去（WAR_DEL），
        与 `_set_active_pet` 的「休憩」是两件事：这里只影响本场，不改出战设置。
        """
        if not self._accept_turn():
            self.log('   ⚠ 召唤指令越窗/重复 → 忽略')
            return
        reason = self._summon_reject_reason(sub, pid)
        if reason is not None:
            # 非法召唤（sub 越界 / 宠物不在栏 / 倒地 / 寄存）不白占一个回合
            self.log('   ⚠ 召唤被拒绝：%s → 交还操作权' % reason)
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            return
        def act():
            self._war_summon(conn, sub, pid)
            return False
        self._resolve_hero_round(conn, act)

    def _summon_reject_reason(self, sub, pid):
        """召唤/召回指令的合法性预检；合法返回 None，否则返回拒绝原因。"""
        sub = int(sub or 0)
        if sub not in (0, 1):
            return 'sub=%d 非法（只认 0=召唤 / 1=召回）' % sub
        if sub == 1:
            return None if self._pet_alive else '场上没有宠物可召回'
        p = self.pets.get(int(pid or 0))
        if p is None:
            return '宠物 id=%s 不在栏内' % pid
        if p.get('lookon'):
            return '宠物 id=%d 处于寄存/引藏状态' % p['pid']
        if int(p.get('hp') or 0) <= 0:
            return '宠物 id=%d 气血为 0' % p['pid']
        return None

    def _war_summon(self, conn, sub, pid):
        """真正执行召唤/召回（在主角行动位上发帧）。返回是否改变了场上单位。"""
        sub = int(sub or 0)
        if sub == 1:
            if not self._pet_alive:
                self.log('   ⚠ 召回被忽略：场上没有宠物')
                return False
            self._pet_alive = False
            self._sync_pet_runtime()          # 撤回时把当前气血写回实例
            conn.sendall(build_war_del(PET_WAR_ID))
            self.log('   宠物召回 → WarDel(%d)（气血 %d/%d 保留，下回合可再召唤）'
                     % (PET_WAR_ID, self.pet_hp, self._pet_hpmax()))
            return True
        p = self.pets.get(int(pid or 0))
        if p is None:
            self.log('   ⚠ 召唤被忽略：宠物 id=%s 不在栏内' % pid)
            return False
        if p.get('lookon'):
            self.log('   ⚠ 召唤被忽略：宠物 id=%d 处于寄存/引藏状态' % p['pid'])
            return False
        if int(p.get('hp') or 0) <= 0:
            self.log('   ⚠ 召唤被忽略：宠物 id=%d 气血为 0' % p['pid'])
            return False
        # 控制点：客户端在召唤前会判 `m_At/m_Df/m_Dp < 4` 并弹「控制点不足，
        # 无法召唤宠物，是否确定召唤？」——阈值 4 是客户端真值，**扣多少**无证据
        # （本服按阈值 4 扣，且允许不足时照常召唤、只扣到 0，见 SUMMON_CP_COST 注释）。
        paid = {}
        for k in ('atk', 'def', 'dis'):
            have = int((self._control_points or {}).get(k, 0))
            cost = min(SUMMON_CP_COST, max(0, have))
            if cost:
                self._control_points[k] = have - cost
                paid[k] = cost
        if paid and max(paid.values()) < SUMMON_CP_COST:
            self.log('   ⚠ 控制点不足（%s），按客户端「确定召唤」照常召唤，只扣到 0'
                     % paid)
        # 换宠：把场上那只先撤下去（客户端 WarDel 后格子会空出来）
        if self._pet_alive and self.active_pet_id != p['pid']:
            self._sync_pet_runtime()
            conn.sendall(build_war_del(PET_WAR_ID))
            self.log('   换宠：旧宠物 id=%s 先退场（WarDel）' % self.active_pet_id)
        self._pet_switch_runtime(self.active_pet_id, p['pid'])
        self._pet_alive = True
        # 行动序的同速决胜表：宠物固定占 1 号位（与 _start_war 同口径）
        self._spawn_index.setdefault(PET_WAR_ID, 1)
        # 招式资源池跟着换：新上场的宠物用**它自己**的 FP/MP（不是上一只剩下的）
        self.pet_res = {
            'm_FP': int(p.get('fp') if p.get('fp') is not None
                        else p.get('fpmax', PET_FP)),
            'm_MP': int(p.get('mp') if p.get('mp') is not None
                        else p.get('mpmax', PET_MP)),
        }
        # WarAdd 必须在 WarSummon(129) **之前**：war.Summon 执行时宠物对象要已在
        # world.g_Objects，否则 ShowSummMenu 的 has_key 判定为假，菜单弹不出来。
        _pn, _ps, _pd = self._pet_info(p['pid'])
        conn.sendall(build_war_add(PET_WAR_ID, _ps, True, _pn, owner_id=HERO_WAR_ID))
        conn.sendall(build_war_summon(PET_WAR_ID))
        self.log('   宠物召唤 → WarAdd(%d)+Summon(129)：id=%d %s（%d 级，HP %d/%d，'
                 '控制点 -%s）'
                 % (PET_WAR_ID, p['pid'], _pn, p['level'], self.pet_hp,
                    self._pet_hpmax(), paid or '无'))
        return True

    def _hero_war_aux(self, conn, op):
        """其余战斗指令（特技/召唤/捕捉/子女）：本服无专属演出，按速度序推进回合。

        必须走 `_resolve_hero_round` 而非保底 `WarTurn`：否则敌方不会行动、回合也不会推进。
        """
        if not self._accept_turn():
            self.log('   ⚠ 战斗指令 op=%d 越窗/重复 → 忽略' % op)
            return
        self.log('   战斗指令 op=%d：无专属演出，按速度序推进回合' % op)
        self._resolve_hero_round(conn, lambda: False)

    def _hero_war_protect(self, conn, target):
        """记录主角的保护目标；命中发生时再排入客户端 Protect 演出帧。"""
        if not self._accept_turn():
            self.log('   ⚠ 保护指令越窗/重复 → 忽略')
            return
        if target not in (HERO_WAR_ID, PET_WAR_ID) or target == HERO_WAR_ID or (
                target == PET_WAR_ID and not self._pet_alive):
            # 非法目标（自己 / 非友方 / 已倒地的宠物）不推进回合，交还操作权重选
            self.log('   ⚠ 无效保护目标 id=%d → 交还操作权' % target)
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            return

        def act():
            self._round_protectors[target] = HERO_WAR_ID
            self.log('   主角保护目标 %d（本回合首次受击时拦截）' % target)
            return False

        self._resolve_hero_round(conn, act)

    def _round_done(self):
        self._round_defending.clear()
        self._round_protectors.clear()

    def _set_round_protect(self, conn, protector, target):
        if target not in (HERO_WAR_ID, PET_WAR_ID):
            self.log('   ⚠ 无效保护目标 id=%d' % target)
            return False
        if target == protector:
            self.log('   ⚠ 单位不能保护自身')
            return False
        if target == PET_WAR_ID and not self._pet_alive:
            self.log('   ⚠ 无法保护已倒地宠物')
            return False
        if protector == PET_WAR_ID and not self._pet_alive:
            return False
        self._round_protectors[target] = protector
        self.log('   单位 %d 保护目标 %d（本回合首次受击时拦截）'
                 % (protector, target))
        return True

    def _speed_order(self):
        """本回合行动序：存活单位按**显式三元键**降序。

        客户端（2013 版）不显示头顶序号，出手顺序 = 服务端下发行动帧的先后。

        ★ 2026-10-20 改为显式键 `(-speed, side_rank, spawn_index)`：

          旧实现只写 `units.sort(key=lambda u: -u[1])`，同速次序**完全依赖**
          Python 稳定排序 + `enemy_hp` 的字典插入顺序。那是个隐式契约：
            · 换 Python 实现（非稳定排序）就乱；
            · 任何地方重排 `enemy_hp`（如存档还原、字典推导改写）就乱；
            · 读代码的人看不出「同速时谁先」，得反推 units 怎么构建的。
          现在把决胜规则写进 key 本身，排序算法是否稳定都不影响结果。

        键的三段：
          speed       越大越先手 → 取负号
          side_rank   同速时我方优先：主角 0 < 宠物 1 < 敌方 2
          spawn_index 同速同阵营时按**入场序号**（_start_war 时固定），
                      而不是按 id 数值或字典顺序

        ⚠ 不能用 uid 作决胜键：主角 id 是 100，最慢敌速钳到 1 时若按 uid 决胜，
          同速主角会排到敌方之后（这正是 2026-10-19 修过的 bug）。
        """
        units = [(HERO_WAR_ID, HERO_SPEED, SIDE_HERO)]
        if self._pet_alive:
            # 宠物速度取**本次出战宠物**的战斗层数值（等级/加点/造型都会改它）
            units.append((PET_WAR_ID, self._pet_combat('speed'), SIDE_PET))
        for wid, hp in self.enemy_hp.items():
            if hp > 0:
                units.append((wid, self.enemy_speed.get(wid, self.enemy_speed_base),
                              SIDE_ENEMY))
        # spawn_index 在 _start_war 时按入场顺序固定；此处只查表，不重新推算。
        keyed = []
        for uid, sp, side in units:
            keyed.append(((-sp, side, self._spawn_index.get(uid, uid)), uid))
        keyed.sort()
        return [uid for _k, uid in keyed]

    def _speed_order_desc(self):
        parts = []
        for uid in self._speed_order():
            if uid == HERO_WAR_ID:
                parts.append('主角(%d)' % HERO_SPEED)
            elif uid == PET_WAR_ID:
                parts.append('宠物(%d)' % self._pet_combat('speed'))
            else:
                parts.append('敌%d(%d)' % (uid, self.enemy_speed.get(uid, 0)))
        return ' → '.join(parts)

    def _first_alive_enemy(self):
        for wid in self._enemy_ids():
            if self.enemy_hp.get(wid, 0) > 0:
                return wid
        return None

    # ───────────────────── 宠物招式：资源 / 目标 / 伤害 / 被动 ─────────────────────
    def _reset_pet_res(self):
        """战斗开场的宠物资源池（与主角 `_reset_hero_res` 同口径：每战回满）。

        ★ 上限取**出战实例**的 fpmax/mpmax（换宠/升级会变）；
        ★ SIM：原版宠物 FP 是否每战回满无证据。这里选择「与主角一致」，
          至少保证「一场战斗内的消耗会真的被扣、且看得见」。
        """
        p = self._pet()
        self.pet_res = {'m_FP': int((p or {}).get('fpmax', PET_FP)),
                        'm_MP': int((p or {}).get('mpmax', PET_MP))}
        return self.pet_res

    def _pet_skill_need(self, pid):
        """招式的宠物资源消耗 `(fp_need, mp_need)`；表达式来自 petskill.DATA。

        客户端 `pet.PetMagicInvalid` 的写法是 `FP-(15+LV/3+3*5)`：左边是**当前值**，
        结果为负即「不够」（菜单把该招式置灰）。所以消耗就是括号里那一段。
        `LV/3` 是 Py2 整除，故用 `py2_expr` 求值 —— 与客户端同一个口径。
        """
        spec = PET_SKILL_EFFECTS.get(int(pid)) or {}
        out = []
        for key in ('fp_need', 'mp_need'):
            expr = spec.get(key)
            if not expr:
                out.append(0)
                continue
            try:
                out.append(max(0, int(eval(py2_expr(expr), {'__builtins__': {}},
                                           {'LV': int(self._pet_level())}))))
            except Exception:                          # noqa: BLE001
                out.append(0)
        return tuple(out)

    def _pet_skill_shortage(self, pid):
        """宠物资源不足 → (name, have, need)；充足 → None。"""
        fp_need, mp_need = self._pet_skill_need(pid)
        have_fp = max(0, int((getattr(self, 'pet_res', {}) or {}).get('m_FP', 0)))
        have_mp = max(0, int((getattr(self, 'pet_res', {}) or {}).get('m_MP', 0)))
        if have_fp < fp_need:
            return ('m_FP', have_fp, fp_need)
        if have_mp < mp_need:
            return ('m_MP', have_mp, mp_need)
        return None

    def _pet_spend_skill(self, conn, pid):
        """扣宠物 FP/MP，并把新值经 S2CSummonInfo(48) 同步给客户端。

        为什么战斗内敢重发 48：`pet.UpdateInfo` 对**已存在**的宠物只调
        `CPet.UpdateProp` 原地改属性（不重建对象、不碰战场 warrior），所以安全。
        不发就会出现「服务端 FP=0 拒绝施放、客户端菜单还是亮的」——
        玩家点了技能却看到普攻动画，是可见的不一致。
        """
        fp_need, mp_need = self._pet_skill_need(pid)
        if not (fp_need or mp_need):
            return
        self.pet_res['m_FP'] = max(0, int(self.pet_res.get('m_FP', 0)) - fp_need)
        self.pet_res['m_MP'] = max(0, int(self.pet_res.get('m_MP', 0)) - mp_need)
        p = self._pet()
        if p is not None:
            # 资源池落到实例上（换宠/重登后仍是耗掉的状态；下一场 _reset_pet_res 再回满）
            p['fp'] = int(self.pet_res['m_FP'])
            p['mp'] = int(self.pet_res['m_MP'])
        if not (self.in_war and self._pet_alive) or p is None:
            return
        base = self._pet_base(p)
        base['hp'] = max(0, min(int(p.get('hpmax') or PET_HP), int(self.pet_hp)))
        base['fp'] = int(self.pet_res['m_FP'])
        base['mp'] = int(self.pet_res['m_MP'])
        _pname, _pshape, _pdef = self._pet_info()
        conn.sendall(build_pet_info(p['pid'], _pname, _pshape, base))
        self.log('   宠物招式 %d 消耗：FP -%d→%d / MP -%d→%d（S2CSummonInfo 同步）'
                 % (pid, fp_need, self.pet_res['m_FP'], mp_need, self.pet_res['m_MP']))

    def _pet_passives(self):
        """当前宠物**生效中**的被动参数字典（只装有数值证据且 enabled 的效果）。

        ★ 被动按**出战宠物自己的技能表**算：换一只没学「连击」的宠物就不该连击。
        `self.pet_passive_mute = True` 可整层关掉 —— 与 `enemy_skill_chance = 0`
        同性质：逐帧断言的老测试需要确定性，别让 40% 的连击把帧序搅乱。
        """
        out = {'crit_add': 0.0, 'combo_chance': 0.0, 'combo_rate': 0.75, 'combo_max': 1}
        if getattr(self, 'pet_passive_mute', False):
            return out
        for sid in pet_skill_ids_of(self._pet()):
            spec = PET_PASSIVE_EFFECTS.get(sid) or {}
            if not spec.get('enabled'):
                continue
            eff = spec.get('effect')
            if eff == 'crit_up':
                out['crit_add'] += float(spec.get('crit_add', 0) or 0)
            elif eff == 'combo':
                out['combo_chance'] = float(spec.get('chance', 0) or 0)
                out['combo_rate'] = float(spec.get('damage_rate', 0.75) or 0.75)
        return out

    def _pet_skill_damage(self, pid):
        """宠物招式单目标伤害（**SIM**）：宠物普攻伤害 × 招式倍率。

        倍率来自 `PET_SKILL_EFFECTS`；415 没有威力/效率字段，故 1.0（不凭空加倍）。
        与主角走的是两套体系：主角有 pow/powrate 表达式可依，宠物没有。
        ★ 攻击/破防/等级全部取**出战宠物实例**（换宠/升级/加点会改它）。
        """
        spec = PET_SKILL_EFFECTS.get(int(pid)) or {}
        rate = float(spec.get('pow_rate', 1.0) or 1.0)
        base = self._dmg(self._pet_combat('at'), ENEMY_DF,
                         self._pet_combat('dp'), self._pet_level())
        return max(1, int(base * max(0.05, min(3.0, rate))))

    def _pet_skill_targets(self, pid, preferred):
        """宠物群攻目标：客户端选的首目标 + 按入场序的其余存活敌方。

        目标数取自 `PET_SKILL_EFFECTS[pid]['targets']`（SIM=2，理由见该表注释）。
        指定目标已死/非法时由 `_resolve_target` 回落，回落后的 id 会写进 122 帧。
        """
        spec = PET_SKILL_EFFECTS.get(int(pid)) or {}
        limit = max(1, int(spec.get('targets', 1) or 1))
        first = self._resolve_target(preferred)
        if first is None:
            return []
        if limit <= 1:
            return [first]
        alive = [wid for wid in self._enemy_ids() if self.enemy_hp.get(wid, 0) > 0]
        return ([first] + [wid for wid in alive if wid != first])[:limit]

    def _pet_attack(self, conn, target=None):
        """宠物普攻（含被动：必杀会心 / 连击追加）。返回 True=战斗已结束。

        连击的口径（petskill 102 desc 真值）：40% 几率追加**一次**普通攻击，
        且「每次伤害结果都降为 75%」—— 注意是**每次**，所以首击也跟着降，
        不是只有追加那一击降。
        """
        wid = self._resolve_target(target)
        if wid is None:
            # 无存活敌方 → 走正常收尾（WarDel/WarEnd）。只回一个 WarTurn 会把客户端
            # 卡在「请等待」，而战斗其实已经该结束了。
            return self._after_hero_action(conn, dead_ids=[])
        pas = self._pet_passives()
        combo = (pas['combo_chance'] > 0
                 and self.battle_rng.chance(brng.S_COMBO, pas['combo_chance']))
        rate = pas['combo_rate'] if combo else 1.0
        # 攻/破防/等级取**出战宠物实例**：换一只/升一级，普攻伤害真的会变
        base = self._dmg(self._pet_combat('at'), ENEMY_DF,
                         self._pet_combat('dp'), self._pet_level())
        dmg = max(1, int(base * rate))
        hurt = (HURT_BS if (pas['crit_add'] > 0
                            and self.battle_rng.chance(brng.S_CRIT,
                                                       pas['crit_add']))
                else HURT_NONE)
        conn.sendall(build_war_attack_normal(PET_WAR_ID, wid))
        dead = self._damage_frames(conn, wid, dmg, hurt)
        conn.sendall(build_war_attack_normal_end(PET_WAR_ID))
        self.log('   宠物 %d → 敌 %d（%d 伤害%s%s）'
                 % (PET_WAR_ID, wid, dmg, '，会心' if hurt else '',
                    '，连击第 1 击（×%.2f）' % rate if combo else ''))
        if dead:
            return self._after_hero_action(conn, dead_ids=[wid])
        if combo and self.enemy_hp.get(wid, 0) > 0:
            # 追加一击：同一条 AttackNormal→WState→End 演出，客户端会连播两次挥击
            conn.sendall(build_war_attack_normal(PET_WAR_ID, wid))
            dead2 = self._damage_frames(conn, wid, dmg, HURT_NONE)
            conn.sendall(build_war_attack_normal_end(PET_WAR_ID))
            self.log('   宠物 连击追加 → 敌 %d（%d 伤害）' % (wid, dmg))
            if dead2:
                return self._after_hero_action(conn, dead_ids=[wid])
        return False

    def _pet_perform(self, conn, pid, target):
        """宠物使用招式：按 petskill 的 **type** 分流，不再一律套用普攻伤害。

        判定顺序（任何一步失败都**回落普攻**并写明原因，绝不静默地打出一个
        「看起来像招式、其实是普攻」的数字）：
          ① 是否在本宠物技能表内      —— 不在 = 客户端发错了表
          ② 是否有登记效果            —— 没登记 = 未实现，不假装有
          ③ type 是否 1/2（主动）     —— 被动不进程菜单，发上来就是非法指令
          ④ 宠物 FP/MP 是否够         —— 客户端的 PetMagicInvalid 同口径
          ⑤ 目标解析（非法/已死回落首个存活敌方）
        """
        pid = int(pid)
        if pid not in pet_skill_ids_of(self._pet()):
            self.log('   ⚠ 宠物技能 id=%d 不在当前宠物技能表，不执行未知效果 → 回落普攻'
                     % pid)
            return self._pet_attack(conn, target)
        spec = PET_SKILL_EFFECTS.get(pid)
        if not spec:
            base = PET_PASSIVE_EFFECTS.get(pid) or {}
            self.log('   ⚠ 宠物技能 %d（%s，type=%d）未登记战斗效果 → 回落普攻'
                     % (pid, base.get('name', '?'), base.get('type', -1)))
            return self._pet_attack(conn, target)
        if spec.get('type') not in (PET_SKILL_ATTACK, PET_SKILL_AUX):
            self.log('   ⚠ 宠物技能 %d 是被动（不进程菜单），非法指令 → 回落普攻' % pid)
            return self._pet_attack(conn, target)
        if spec.get('type') == PET_SKILL_AUX:
            self.log('   ⚠ 宠物辅助招式 %d（%s）本服无实现（无证据）→ 回落普攻'
                     % (pid, spec.get('name', '?')))
            return self._pet_attack(conn, target)
        shortage = self._pet_skill_shortage(pid)
        if shortage is not None:
            name, have, need = shortage
            self.log('   ⚠ 宠物招式 %d 资源不足：%s %d/%d → 回落普攻'
                     % (pid, name, have, need))
            return self._pet_attack(conn, target)
        targets = self._pet_skill_targets(pid, target)
        if not targets:
            self.log('   ⚠ 宠物招式 %d 无存活目标' % pid)
            return True
        dmg = self._pet_skill_damage(pid)
        conn.sendall(build_war_perform(PET_WAR_ID, pid, targets))
        dead_ids = []
        for wid in targets:
            if self._damage_frames(conn, wid, dmg):
                dead_ids.append(wid)
        self._pet_spend_skill(conn, pid)
        self.log('   宠物招式 %s(%d) → 目标 %s（每目标 %d 伤害，%s）'
                 % (spec.get('name', '?'), pid, targets, dmg,
                    '群体' if len(targets) > 1 else '单体'))
        return self._after_hero_action(conn, dead_ids=dead_ids)

    def _finish_pet_turn(self, conn, op=None, body=b''):
        """完成挂起回合中的宠物指令，再结算宠物之后的慢速单位。

        ★ 字段一律按 `frames.SPEC` 解（唯一来源），**不在这里手写 body 偏移**：
          168 = id(1)+target(1)；**169 = target(1)+perform(2)**（宠物 id 不上线，
          形参声明了但客户端不发）；171 = id(1)+target(1)+item(4)。
          早期版本按「id 打头」解 169，会把 target 当 id、把技能号读成 target<<8 的残片。
        """
        # ★ 越窗/重复防护：真实宠物指令（op 非 None，即客户端主动发来的宠物行动）
        #   必须在本回合窗口内且未被消费过才放行；超时默认动作 / 主角指令抢先于宠物
        #   菜单到达时的「默认普攻结算」走 op=None 分支，不在此消费窗口（由随后到达的
        #   主角指令自行 _accept_turn 接管）。
        if op is not None and not self._accept_turn():
            self.log('   ⚠ 宠物指令越窗/重复 → 忽略')
            return False
        suffix = self._pending_pet_suffix
        if suffix is None:
            return False
        self._pending_pet_suffix = None
        self.await_cmd = False

        cmd = bf.unpack_c2s(op, body) if op is not None else None
        handled = False
        if self._pet_alive and cmd:
            if cmd.get('id') not in (None, PET_WAR_ID):
                self.log('   ⚠ 宠物指令 id=%s 不是本场宠物 %d，忽略'
                         % (cmd.get('id'), PET_WAR_ID))
                cmd = None
        if self._pet_alive and cmd:
            if op == C2S_WAR_SUMM_ATTACK:
                self._pet_attack(conn, cmd['target'])
                handled = True
            elif op == C2S_WAR_SUMM_PERFORM:
                self._pet_perform(conn, cmd['perform'], cmd['target'])
                handled = True
            elif op == C2S_WAR_SUMM_GUARD:
                self._round_defending.add(PET_WAR_ID)
                self.log('   宠物防御（本回合后续受击伤害减半）')
                handled = True
            elif op == C2S_WAR_SUMM_ITEM:
                target, iid = cmd['target'], cmd['item']
                if target in (HERO_WAR_ID, 0):
                    self._item_consume(conn, iid)
                elif target == PET_WAR_ID:
                    self._item_use_target(conn, iid, target)
                else:
                    self.log('   ⚠ 宠物道具目标 %d 不是存活友方，忽略效果' % target)
                self.log('   宠物战斗用道具 id=%d → 目标 %d' % (iid, target))
                handled = True
            elif op == C2S_WAR_SUMM_PROTECT:
                handled = self._set_round_protect(conn, PET_WAR_ID, cmd['target'])
            elif op == C2S_WAR_SUMM_ESCAPE:
                self.log('   宠物逃跑指令（本服暂不模拟独立逃跑效果）')
                handled = True

        if not handled and self.in_war and self._pet_alive:
            self.log('   ⚠ 宠物指令无效，默认普攻')
            self._pet_attack(conn)
        if not self.in_war:
            return True

        for uid in suffix:
            if not self.in_war:
                return True
            if uid == PET_WAR_ID:
                continue
            if self.enemy_hp.get(uid, 0) > 0 and not self._enemy_attack(conn, uid):
                return True
        # ★ P3-03：宠物路径同样要在回合末结算状态（无状态时零帧）
        if self.in_war:
            self._fx_end_of_round(conn)
            if not self.in_war:
                return True
        conn.sendall(build_war_turn(0))
        self._arm_cmd_timer()
        self.log('   宠物指令结算完成 → 慢位 %s → WarTurn' % suffix)
        self._round_done()
        return True

    def _hero_attack(self, conn, target):
        """玩家普攻指定敌方。target=None（超时默认动作）→ 首个存活敌方。

        语义对齐客户端 `warui.DefaultAttack()`（自动战斗/超时路径）：普攻一个敌人。
        """
        if not self._accept_turn():
            self.log('   ⚠ 普攻指令越窗/重复 → 忽略')
            return
        wid = self._resolve_target(target)
        if wid is None:
            # 与 _hero_perform/_pet_attack 同口径：无存活敌方 = 战斗该收尾了。
            self.log('   ⚠ 普攻无存活目标 → 按敌方全灭收尾')
            self._after_hero_action(conn, dead_ids=[])
            return

        def act():
            dmg = self._hero_attack_damage()
            conn.sendall(build_war_attack_normal(HERO_WAR_ID, wid))
            dead = self._damage_frames(conn, wid, dmg)
            conn.sendall(build_war_attack_normal_end(HERO_WAR_ID))
            self.log('   普攻 %d → %d（%d 伤害）' % (HERO_WAR_ID, wid, dmg))
            return self._after_hero_action(conn, dead_ids=[wid] if dead else [])

        self._resolve_hero_round(conn, act)

    def _hero_perform(self, conn, pid, target):
        """玩家施放已学招式：校验（已学/资源/目标）→ 演出 → 结算 → 扣本级消耗。

        三种拒绝都走同一条「回 WarTurn(0) 交还操作权」的路径，而且**一分资源都不扣、
        一滴血都不掉** —— 客户端会重新弹菜单，玩家能立刻改选别的动作。
        """
        if not self._accept_turn():
            self.log('   ⚠ 招式指令越窗/重复 → 忽略')
            return
        pid = int(pid)
        if pid not in self.magics or (pid not in MAGIC_EXPR and pid not in MAGIC_INFO):
            self.log('   ⚠ 招式 pid=%d 未学习或无属性数据，拒绝施放' % pid)
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            return
        shortage = self._magic_shortage(pid)
        if shortage is not None:
            name, have, need = shortage
            self.log('   ⚠ 招式 pid=%d（%d 级）资源不足：%s %d/%d，拒绝施放'
                     % (pid, self._magic_level(pid), name, have, need))
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            return
        targets = self._magic_targets(pid, target)
        if not targets:
            # 没有存活敌方却还在战斗态 = 该收尾了；以前这里只回 WarTurn，
            # 客户端会停在「请等待」而战斗永远不结束。
            self.log('   ⚠ 招式 pid=%d 无存活目标 → 按敌方全灭收尾' % pid)
            self._after_hero_action(conn, dead_ids=[])
            return

        def act():
            slv = self._magic_level(pid)
            gslv = self._magic_base_skill_level(pid)
            damage = self._magic_damage(pid)
            conn.sendall(build_war_perform(HERO_WAR_ID, pid, targets))
            dead_ids = []
            for wid in targets:
                if self._damage_frames(conn, wid, damage):
                    dead_ids.append(wid)
            info = MAGIC_INFO.get(pid) or {}
            pow_now = magic_pow_at(pid, slv, gslv)
            pow_lv1 = max(1, magic_pow_at(pid, 1, gslv))
            self.log('   招式 %s(%d) %d 级 → 目标 %s（每目标 %d 伤害｜威力 %d（×%.2f）｜效率 %d%%）'
                     % (info.get('name', '未知'), pid, slv, targets, damage,
                        pow_now, float(pow_now) / pow_lv1,
                        magic_powrate_at(pid, slv, gslv)))
            # 施法消耗：扣 m_MP/m_FP/m_At/m_Df/m_Dp 并回 PropChange（客户端据此刷新资源条）
            self._spend_magic_cost(conn, pid)
            return self._after_hero_action(conn, dead_ids=dead_ids)

        self._resolve_hero_round(conn, act)

    def _grant_item(self, conn, shape, name, qty=1):
        """放一件物品进背包空格并下发 S2CItemAdd(33)，返回本地 bag id。

        与 NPC 购买/任务奖励同一套入包契约（见 `_handle_npc_buy`）：
        位置取背包区间内第一个空位，bag id 由 `_alloc_iid()` 分配。
        背包满则返回 None（不覆盖已有物品）。
        """
        free = [p for p in range(ITEM_POS_BAG_START, ITEM_POS_BAG_END)
                if not any(it.get('pos') == p for it in self.items.values())]
        if not free:
            self.log('   [物品] 背包已满，%s 未入包' % name)
            return None
        iid = self._alloc_iid()
        self.items[iid] = {'shape': int(shape), 'key': 0, 'qty': max(1, int(qty)),
                           'pos': free[0], 'infoext': name}
        conn.sendall(build_item_add(ITEM_WTYPE_NORMAL, iid, int(shape), 0,
                                    self.items[iid]['qty'], free[0]))
        # 入包飘窗（op164 itype=0 = 物品图标）：与 ItemAdd 成对下发，
        # 客户端会把「获得 X ×N」滑入屏幕中部。放在 ItemAdd 之后，
        # 保证提示里的图标能在背包里找到对应造型。
        conn.sendall(build_notify_ext(
            0, int(shape), '获得 %s ×%d' % (name, self.items[iid]['qty'])))
        self._emit('item.grant', '获得 %s ×%d' % (name, self.items[iid]['qty']),
                   shape=int(shape), name=name, qty=int(self.items[iid]['qty']),
                   iid=iid)
        return iid

    def _roll_drops(self, conn, scene=None):
        """战斗胜利后按当前场景的真实掉落池掷骰。

        场景来自 `self._map_def()['name']`，池来自 drop_data（见文件头 M8 注释）。
        城镇（永泽城 / 谷雨镇 / 清溪城）与资料表未收录的场景没有池 → 不掉落，
        这与资料表「只有野外暗雷/明雷场景才有掉落」的口径一致。
        """
        scene = scene or self._map_def()['name']
        pool = scene_pool(scene) if scene_pool else None
        if not pool:
            self.log('   掉落：%s 无野生掉落池（城镇/未收录），不掉落' % scene)
            return []
        if not self.battle_rng.chance(brng.S_DROP, DROP_CHANCE):
            self.log('   掉落：%s 本场未触发（DROP_CHANCE=%.2f）' % (scene, DROP_CHANCE))
            return []
        got = []
        for _ in range(DROP_MAX_PER_BATTLE):
            token, _how, items = self.battle_rng.choice(brng.S_DROP_PICK,
                                                        pool['pools'])
            shape, name = self.battle_rng.choice(brng.S_DROP_PICK, items)
            iid = self._grant_item(conn, shape, name)
            if iid is None:
                break
            got.append(iid)
            self.log('   掉落：%s → %s（池条目「%s」，item id %d，bag id %d）'
                     % (scene, name, token, shape, iid))
        if got:
            self._persist()
        return got

    def _after_hero_action(self, conn, dead_ids=()):
        """攻击/招式后的收尾：清掉已死单位；全灭则结束。返回 True=战斗已结束。

        ★ 幂等：战斗已结束（GM 连点胜利 / 同一批死亡单位被二次结算）时直接返回，
          不再发第二遍 WarEnd、经验与掉落 —— 旧实现只靠调用方自觉判 in_war，
          重复调用会把「战后结算」执行两次（经验翻倍、掉落重复入包）。

        ★ P3-03 补强两处：
          ① 死亡单位**先全部 WarDel 再统一推进任务计数**。旧实现在循环里逐只调
             `_on_enemy_dead()`，一次全灭 3 只就发 3 遍 TaskAdd + 落盘 3 次；
             进度语义不变（每只 +1），但落盘从 N 次收敛到 1 次。
          ② 胜利分支用 `_mark_settled('win')` 显式计数，重复结算会打 warn 栈。
        """
        if not self.in_war:
            return True
        # WarDel 仍按调用方给的顺序逐只发（帧序与旧实现逐帧相同），
        # 但击杀计数只算**真正从场上清掉**的单位：dead_ids 里若有重复或已死的 id，
        # 旧实现会照样 `_on_enemy_dead()` 推一格进度，等于凭空多加任务进度。
        cleared = 0
        for wid in dead_ids:
            conn.sendall(build_war_del(wid))
            if self.enemy_hp.pop(wid, None) is not None:
                cleared += 1
        if cleared:
            self._on_enemies_dead(conn, cleared)
        if not self.enemy_hp:
            self._mark_settled('win')
            conn.sendall(build_war_end())
            self._reset_war_state()
            self.log('   敌方全灭 → WarEnd（战斗结束）')
            self._resync_scene(conn)
            # 剧情战斗：先结算任务步骤（TaskAdd），再走常规战斗经验/掉落
            self._quest_battle_won(conn)
            # 经验与掉落同一时机：都必须在 WarEnd + 场景重同步之后，
            # 否则 PropChange/ItemAdd 会被客户端场景重建冲掉。
            self._award_battle_exp(conn)
            # 掉落必须在 WarEnd + 场景恢复之后：客户端此时才处理完 ClearAllObject/重建对象，
            # S2CItemAdd 落在其后不会被场景刷新冲掉。
            self._roll_drops(conn)
            self._emit('war.end', '战斗胜利（%d 只已清）' % len(dead_ids or []),
                       result='win', killed=len(dead_ids or []))
            return True
        return False

    def _enemy_attack(self, conn, wid):
        """单个敌方攻击一名友方（主角或宠物）的演出帧序列。返回 False=战斗已结束（主角倒地）。

        ★ 回合契约（客户端源码）：
          · `netwar.S2CWarTurn` 会把 `war.g_Turn += 1`；`war.AttackNormal`/`Perform` 等
            开头 `if g_Turn == 0: return` —— 所以**没有先发 WarTurn 就不会有演出**。
          · 演出链固定为 `AttackNormal(117) → WState(123) → AttackNormalEnd(119)`，
            `AttackNormalEnd` 会把攻守双方 `GoBack` 归位并推进命令队列。
          · 友方掉血用 **S2CWarWState(123)+HPPercent(125)**：友方单位（allied）由
            `warrior.Create` 建出 `m_HPFull`，HPPercent 可安全刷新血条（敌方则不行）。
          目标在「存活友方」中随机：让宠物也能被击（被击倒则本场 WarDel 退场）。

        ★ P3-03：选目标 / 放不放招 / 放哪招 / 会心 四步决策抽到 `enemy_ai.decide()`，
          本函数只负责「按决策发帧」。这样「它为什么打宠物」有一句可读的解释
          （`enemy_ai.explain`），也才能单独测决策而不碰协议。
          默认策略 `'random'` 的抽取种类与顺序与原实现一致（I1）。
        """
        # 目标选择：主角 +（存活宠物）
        targets = [HERO_WAR_ID]
        if self._pet_alive:
            targets.append(PET_WAR_ID)
        tgt = eai.pick_target(self.battle_rng, targets,
                              policy=self.enemy_ai_policy,
                              hp_ratio=self._ally_hp_ratio())
        if tgt is None:                     # 无可打目标（理论上不会：主角恒在）
            return True
        protector = self._round_protectors.pop(tgt, None)
        has_protect = protector in targets
        actual_tgt = protector if has_protect else tgt
        dec = eai.decide_action(self.battle_rng, self.enemy_skill_chance,
                                self.enemy_skills, self._hurt_bs_chance,
                                protected=has_protect)
        dec['target'] = actual_tgt
        dec['intended'] = tgt
        self._last_enemy_decision = dict(dec)
        use_skill = dec['use_skill']
        pid = dec['skill_id']
        if use_skill and pid is not None:
            # ★ 敌方招式**只做演出**（弹字+特效），伤害仍走下面的单目标 WState：
            #   录像里敌方 415（乱舞冲击）的 Perform 帧 vic 列表恒为 1 个 id
            #   （250 或 100），没有多目标帧。宠物侧 415 才是群体结算 ——
            #   两者可共用同一个 petskill id，但证据强度不同，别把这里改成群攻。
            conn.sendall(build_war_perform(wid, pid, [tgt]))
            self.log('   敌方 %d 放招式 pid=%d → 目标 %d（录像复刻）' % (wid, pid, tgt))
        else:
            conn.sendall(build_war_attack_normal(wid, tgt))
        if has_protect:
            # Protect 必须排在 AttackNormal 后面：客户端在处理攻击命令时
            # 从后续队列中查找保护者和双方的受击状态。
            conn.sendall(build_war_protect(actual_tgt, tgt))
            conn.sendall(build_war_wstate(tgt, HURT_NONE, LIFE_NORMAL,
                                          WARD_DEFEND, 0))
            self.log('   保护触发：%d 替 %d 承受攻击' % (actual_tgt, tgt))
        # 会心（红字）与普通（橙字）：已在 decide_action 里掷过（`dec['crit']`），
        # 这里**只取结果**，不要再掷 —— 旧实现在这里握着模块级 random，
        # 现在若重掷就会变成「一回合判定两次」。
        hurt = HURT_BS if dec['crit'] else HURT_NONE
        if actual_tgt == HERO_WAR_ID:
            damage = self._dmg(ENEMY_AT, HERO_DF, ENEMY_DP, HERO_LEVEL)
            ward = WARD_HIT
            # ★ P3-03：状态可令防御「强制生效」（#27 恐怖「强制防御1回合」）
            if (HERO_WAR_ID in self._round_defending
                    or self._fx_force_defend(HERO_WAR_ID)):
                damage = max(1, (damage + DEFEND_DAMAGE_DIVISOR - 1)
                             // DEFEND_DAMAGE_DIVISOR)
                ward = WARD_DEFEND
            # 受击方状态修正（无敌状态时原值返回，录像复现值不变）
            damage = self._fx_taken(HERO_WAR_ID, damage)
            self.hero_hp = max(0, self.hero_hp - damage)
            conn.sendall(build_war_wstate(HERO_WAR_ID, hurt,
                                          LIFE_NORMAL, ward, damage))
            per = int(self.hero_hp * 100.0 / HERO_HP)
            conn.sendall(build_war_hp_percent(HERO_WAR_ID, per))
            conn.sendall(build_war_attack_normal_end(wid))
            conn.sendall(build_prop_change_hp(self.hero_hp))
            self.log('   敌方 %d 行动 → 主角承受 %d，HP %d/%d'
                     % (wid, damage, self.hero_hp, HERO_HP))
            if self.hero_hp <= 0:
                self.log('   主角倒地 → WarEnd（战败）')
                self._mark_settled('lose')
                for w in self._enemy_ids():
                    conn.sendall(build_war_del(w))
                conn.sendall(build_war_end())
                self._reset_war_state()
                self._quest_battle = None      # 战败：剧情战斗作废，可重打
                self._resync_scene(conn)
                self._emit('war.end', '战败（主角倒地）', result='lose')
                return False
            return True
        else:
            # 宠物的防御/等级取**出战实例**（换宠/升级/加点会改减伤）
            damage = self._dmg(ENEMY_AT, self._pet_combat('df'), ENEMY_DP,
                               self._pet_level())
            ward = WARD_HIT
            if (PET_WAR_ID in self._round_defending
                    or self._fx_force_defend(PET_WAR_ID)):
                damage = max(1, (damage + DEFEND_DAMAGE_DIVISOR - 1)
                             // DEFEND_DAMAGE_DIVISOR)
                ward = WARD_DEFEND
            damage = self._fx_taken(PET_WAR_ID, damage)
            self.pet_hp = max(0, self.pet_hp - damage)
            conn.sendall(build_war_wstate(PET_WAR_ID, hurt,
                                          LIFE_NORMAL, ward, damage))
            per = int(self.pet_hp * 100.0 / self._pet_hpmax())
            conn.sendall(build_war_hp_percent(PET_WAR_ID, per))
            conn.sendall(build_war_attack_normal_end(wid))
            self.log('   敌方 %d 行动 → 宠物承受 %d，HP %d/%d'
                     % (wid, damage, self.pet_hp, self._pet_hpmax()))
            if self.pet_hp <= 0:
                self._pet_alive = False
                # 倒地立刻写回实例：否则切宠/存盘时「这只已经 0 血」会丢，
                # 重登后又是一条满血的龙 —— 这就是要消灭的状态分叉。
                self._sync_pet_runtime()
                conn.sendall(build_war_wstate(PET_WAR_ID, HURT_NONE,
                                              LIFE_DEAD, WARD_HIT, 0))
                conn.sendall(build_war_del(PET_WAR_ID))
                self.log('   宠物倒地 → WarDel（本场退场，气血记 0）')
            return True

    def _resolve_hero_round(self, conn, hero_act):
        """按速度序结算一整回合：快位单位先动 → 主角行动 → 慢位单位后动 → 交还操作权。

        客户端先提交主角指令，再打开宠物菜单提交宠物指令；因此宠物速度位之前的单位
        先结算，宠物之后的单位等待宠物命令后结算。敌方随机瞄主角/宠物，使宠物可被击倒。
        """
        self._round_done()
        
        # ✅ 真值：逃跑失败 → 敌方获得免费攻击回合（资料库第 31023/31030 行）
        # ★ 惩罚回合就是**本回合的全部**：跑完敌方一次就收口并交还操作权。
        #   旧实现跑完惩罚后继续往下走正常速度序，敌方会再动手一次（快位 + 慢位），
        #   等于一次逃跑失败被打了 2~3 轮；这里提前 return 把它钉死为「只多打一轮」。
        if getattr(self, '_escape_failed', False):
            self._escape_failed = False
            self.log('   >>> 逃跑失败惩罚：敌方获得白打一回合')
            for uid in self._speed_order():
                if not self.in_war:
                    return
                if uid != HERO_WAR_ID and uid != PET_WAR_ID:
                    if self.enemy_hp.get(uid, 0) > 0:
                        self._enemy_attack(conn, uid)
            if not self.in_war:
                return
            conn.sendall(build_war_turn(0))
            self._arm_cmd_timer()
            self._round_done()
            return
        
        order = self._speed_order()
        hi = order.index(HERO_WAR_ID) if HERO_WAR_ID in order else len(order)
        prefix = order[:hi]      # 比主角快的：敌方 + 可能比主角快的宠物
        suffix = order[hi + 1:]  # 比主角慢的

        def _unit_act(uid):
            """单个单位行动；返回 True=战斗已结束需立即收尾。"""
            if uid == PET_WAR_ID:
                if not self._pet_alive:
                    return False
                return self._pet_attack(conn)
            else:  # 敌方
                if not self.in_war or self.enemy_hp.get(uid, 0) <= 0:
                    return False
                return (not self._enemy_attack(conn, uid))

        for uid in prefix:
            if not self.in_war:
                return
            if _unit_act(uid):
                return
        ended = hero_act()
        if ended or not self.in_war:
            return
        pet_index = suffix.index(PET_WAR_ID) if self._pet_alive and PET_WAR_ID in suffix else -1
        before_pet = suffix[:pet_index] if pet_index >= 0 else suffix
        after_pet = suffix[pet_index + 1:] if pet_index >= 0 else []
        if before_pet:
            conn.sendall(build_war_turn(1))
        for uid in before_pet:
            if not self.in_war:
                return
            if _unit_act(uid):
                return
        if pet_index >= 0 and self._pet_alive:
            self._pending_pet_suffix = after_pet
            self._arm_cmd_timer()
            self.log('   等待宠物指令 op168-174（超时默认宠物普攻）')
            return
        for uid in after_pet:
            if not self.in_war:
                return
            if _unit_act(uid):
                return
        # ★ P3-03：回合末状态结算（DOT + 计时）。**无状态时零帧零副作用**，
        #   所以接在这里不会改变任何既有帧序；有状态时才追加 WState/HPPercent。
        if self.in_war:
            self._fx_end_of_round(conn)
            if not self.in_war:          # DOT 把敌方全打死了 → 已收尾
                return
        conn.sendall(build_war_turn(0))       # 交还操作权（lastcmd=0 → ShowHeroMenu）
        self._arm_cmd_timer()
        self.log('   速度序结算：快位 %s → 主角 → 慢位 %s → WarTurn' % (prefix, suffix))
        self._round_done()

    def _hero_defend_action(self, conn):
        """防御姿态由客户端本地播放；服务端将后续本回合受击伤害减半。"""
        if not self._accept_turn():
            self.log('   ⚠ 防御指令越窗/重复 → 忽略')
            return
        self.log('   >>> 防御')
        def act():
            self._round_defending.add(HERO_WAR_ID)
            return False
        self._resolve_hero_round(conn, act)

    def _timeout_default_action(self, conn):
        """指令窗口超时的默认动作（对齐客户端 `warui.DefaultAttack` 超时路径）。

        ★ 这里**不预先** `_accept_turn()`：真正的动作入口（`_hero_attack` /
          `_finish_pet_turn`）自己会过闸门。旧实现先在超时分支消费一次窗口，
          再调 `_hero_attack` —— 后者看到 `_cmd_consumed=True` 直接判「重复指令」
          忽略，默认普攻被吞掉：客户端停在「请等待」，战斗永远不会推进。
        """
        if not self.in_war or not self.await_cmd:
            return False
        if self._pending_pet_suffix is not None:
            return self._finish_pet_turn(conn)
        self._hero_attack(conn, None)
        return True

    def _fallback_turn(self, conn, op):
        """战斗中对本服无逻辑的指令：至少回一个 WarTurn，避免客户端永久停在「请等待」。

        客户端「请等待」只在两种情况下消失：收到 WarTurn（→ ShowHeroMenu → ShowTimer）
        或 WarEnd（→ warui.Hide）。服务端一旦不回应，客户端就会一直等待。
        """
        if not self._accept_turn():
            self.log('   ⚠ 未做逻辑的战斗指令 op=%d 越窗/重复 → 忽略' % op)
            return
        self.log('   ⚠ 未做逻辑的战斗指令 op=%d → 保底回 WarTurn' % op)
        conn.sendall(build_war_turn(0))
        self._arm_cmd_timer()

    # ───────────────────────── 多地图 / 传送（M7）─────────────────────────
    def _map_def(self, mapid=None):
        return MAPS.get(self.mapid if mapid is None else mapid, MAPS[START_MAP_ID])

    def _npc_name(self, npcid):
        """按当前图 NPC 表取名字（对话框的 name 字段用真实名）。"""
        for (nid, _s, _x, _y, nname) in self._map_def()['npcs']:
            if nid == npcid:
                return nname
        return NPC_NAME

    def _quest_target_npc_name(self):
        """当前主线步骤要交互的 NPC 名（不在本图则 None）。"""
        qid, t = self._quest_active()
        if not qid or not t:
            return None
        st = questmod.step_of(qid, int(t.get('step', 0)))
        if st and st['map'] == self.mapid:
            return st['npc']
        return None

    def _visible_npcs(self):
        """当前地图**可见** NPC 列表。

        资料原文：「部分 NPC 必须在接了任务的情况下才能看到」。
        quest_data 里以 `quest-spawn` 标注的 NPC（剧情 1–6 共 35 个）只在**当前步骤**需要时出现：
          · 例如「捣乱的野兽」只有主线 1 进行到第 2 步（去清风村 40,7）才可见；
          · 任务未接/已完成/放弃 → 不出现；
          · 世界 NPC（村长/老农夫/钱梦庵/主教…来自 npcposdata）永远可见。
        场景下发、小地图 NPC 列表、点击校验三处必须同源，否则会出现
        「画面上没有、小地图能点」或反过来的分叉。
        """
        target = self._quest_target_npc_name()
        out = []
        for npc in self._map_def()['npcs']:
            if npc[0] in questmod.QUEST_SPAWN_IDS and npc[4] != target:
                continue
            out.append(npc)
        return out

    def _visible_npc_ids(self):
        return [npc[0] for npc in self._visible_npcs()]

    def _refresh_spawns_if_changed(self, conn, before_ids):
        """任务推进导致可见集变化 → 重发场景（客户端 SceneM 会 ClearAllObject）。"""
        now = self._visible_npc_ids()
        if list(before_ids) != now:
            self._send_scene(conn, self.px, self.py)
            self.log('   ↻ 任务态 NPC 可见集变化 %s → %s，已重发场景'
                     % (sorted(before_ids), sorted(now)))

    def _send_scene(self, conn, x=None, y=None):
        """下发当前地图整包：SceneM（含场景名/小地图）+ 该图全部 NPC + GotoM。

        ★ 客户端 `S2CMapSceneM` 会 `world.ClearAllObject()`，故每次切图/重同步都必须
          重发该图的 NPC 列表，否则地图上没有对象（既有 _resync_scene 契约）。
        """
        d = self._map_def()
        x = d['spawn'][0] if x is None else x
        y = d['spawn'][1] if y is None else y
        conn.sendall(build_scene_m(self.mapid, d['name'], d['minimap'], x, y))
        _npcs = self._visible_npcs()
        for (nid, shape, nx, ny, nname) in _npcs:
            conn.sendall(build_add_npc(nid, shape, nx, ny, nname))
        # 随行宠物：出战中且活着才画（换宠/跌倒后场景里不该还跟着一只）
        pet_on_map = (self.active_pet_id in self.pets and int(self.pet_hp) > 0)
        if pet_on_map:
            # 随行宠物：客户端 SceneM 会 world.ClearAllObject 并**同坐标**重建主角，
            # 故在其后补发（放 GotoM 之前，保持「整包以 GotoM 收尾」契约，见 _test_map）。
            _pname, _pshape, _pdef = self._pet_info()
            conn.sendall(build_map_summon(
                self.hero_id, [(0, _pshape, _pname, '')]))
        conn.sendall(build_goto_m(x, y))
        self.log('   → 场景 %s(%d)：SceneM + AddNpc×%d + GotoM(%d,%d)%s'
                 % (d['name'], self.mapid, len(_npcs), x, y,
                    ' + 随行宠物' if pet_on_map else ''))

    def _goto_map(self, conn, mapid, x=None, y=None):
        """传送切图：改 self.mapid/坐标 → 整包重发场景 → 落盘。"""
        if mapid not in MAPS:
            self.log('   ⚠ 未知地图 %d，忽略传送' % mapid)
            return
        # 缺客户端 `map/<id>.map` 的图：只警告一次，**不拦**。
        #   2026-10-22 曾短暂拦过，结果打断了太息林地「灵风塔守护人」随身 NPC 的去路
        #   （它的目的地 1073 正好在缺文件名单里）——服务端不该替客户端判断资源在不在，
        #   尤其室内图很可能由 map.fls 兜底。黑屏与否留给真机确认。
        if not map_file_exists(mapid):
            _k = '_warned_map_file'
            if not hasattr(self, _k):
                setattr(self, _k, set())
            if mapid not in getattr(self, _k):
                getattr(self, _k).add(mapid)
                self.log('   ⚠ 地图 %d(%s) 缺客户端 map/%d.map，若切过去黑屏即缺图'
                         % (mapid, MAPS[mapid]['name'], mapid))
        self.mapid = mapid
        d = self._map_def()
        self.px = d['spawn'][0] if x is None else x
        self.py = d['spawn'][1] if y is None else y
        self.move_count = 0
        self._arrive_xy = (self.px, self.py)   # 落脚点豁免（见 _check_exit）
        self._send_scene(conn, self.px, self.py)
        self.log('   ★ 传送 → %s(%d) @(%d,%d)' % (d['name'], mapid, self.px, self.py))
        self._emit('world.goto', '切图 → %s(%d) @(%d,%d)'
                   % (d['name'], mapid, self.px, self.py),
                   mapid=int(mapid), name=d['name'], x=self.px, y=self.py)
        self._persist()

    def _wizard_goto(self, conn, scene, x, y):
        """鼠标导航（小地图图面点击 → 客户端 `netchat.C2SPlayerWizard` 的 'goto scene,x,y'）。

        客户端 `dlgmap.CFindDialog.AutoGoto` 在 `g_AutoGoto`（聊天 `$autogoto` 打开）模式下，
        把小地图上点到的那一点换算成服务端坐标发过来。服务端没有地图碰撞数据，做不到
        「绕墙寻路走过去」，所以：
          · 同图 → 直接到位（S2CMapGotoM 落点，并设落脚点豁免，避免落点贴出口被立刻弹走）；
          · 跨图 → 走 `_goto_map`（整包切图，落点 = 指定坐标）。
        """
        if scene not in MAPS:
            self.log('   ⚠ 导航目标图 %d 未知，忽略' % scene)
            return
        if scene != self.mapid:
            self._goto_map(conn, scene, x, y)
            return
        self.px, self.py = x, y
        self.move_count = 0
        self._arrive_xy = (x, y)
        conn.sendall(build_goto_m(x, y))
        self.log('   ★ 导航到位 %s(%d) @(%d,%d)' % (self._map_def()['name'], scene, x, y))
        self._persist()

    def _check_exit(self):
        """当前坐标落在出口标记半径内 → 返回目标 mapid（无可传送出口时 None）。

        ★ 落脚点豁免：全图落表后，不少图的 spawn 坐标**本身就是** minimap 出口标记
          （如 1035 落雪冰原 spawn=出口(230,6)）。若不豁免，玩家一落地、迈第一步就被
          立刻弹去邻图，等于走不进这张图。故自 _goto_map 落地起，直到**走出**落脚点
          半径为止，暂不做出口判定。
        """
        if self._arrive_xy is not None:
            ax, ay = self._arrive_xy
            if abs(self.px - ax) <= EXIT_RADIUS and abs(self.py - ay) <= EXIT_RADIUS:
                return None
            self._arrive_xy = None
        for (name, ex, ey) in MAP_EXITS.get(self.mapid, []):
            if abs(self.px - ex) <= EXIT_RADIUS and abs(self.py - ey) <= EXIT_RADIUS:
                dest = NAME2MAP.get(name)
                if dest and dest != self.mapid:
                    self.log('   ⌂ 踩到出口「%s」(%d,%d) → 目标图 %d' % (name, ex, ey, dest))
                    return dest
        return None

    def _travel_msg(self, dests=None):
        """传送对话页：选项 = 本图邻接场景（MAP_LINKS）或该随身 NPC 的路线目的地。

        ★ 客户端 `autofindpath` 走到随身 NPC 后，靠 `dlgnpc.g_DlgNpc.m_NpcName == 该 NPC 名`
          判定「已到达」；故对话框的 name 必须用 NPC 真名（见 op96 分派），且选项行首为 'Q'。
        """
        if dests is None:
            dests = MAP_LINKS.get(self.mapid, [])
        lines = ['P\n客官要往哪里去？（当前：%s）' % self._map_def()['name']]
        for mid in dests:
            if mid in MAPS:
                lines.append('Q前往%s。' % MAPS[mid]['name'])
        lines.append('Q不出去了，谢谢。')
        return '\n'.join(lines)

    def _teleport_msg(self):
        """传送使对话页（= 全邻接菜单）；保留原名供既有调用/测试使用。"""
        return self._travel_msg(None)

    def _teleport_choice(self, conn, choice, menu=None):
        dests = MAP_LINKS.get(self.mapid, []) if menu is None else menu
        if 1 <= choice <= len(dests):
            self._goto_map(conn, dests[choice - 1])
        else:
            conn.sendall(build_npc_chat_ext(TELEPORT_NPC_NAME, 'P\n那就不打扰客官了。'))

    def _resync_scene(self, conn):
        """战斗结束后重同步场景。

        客户端 `war.WarEnd` 处理里有 `world.ClearAllObject()`：它会把地图上的所有对象清空，
        但**不会**自行向服务端重新请求。若服务端不回场景数据，地图将为空、
        且战斗单位/等待动画可能残留（录像中「回到场景仍显示请等待 + 战斗单位」即此类）。
        """
        # 战斗结束：先把本场耗掉的气血/资源写回实例，再重发这只宠物的身份帧
        # （48 带新的 hp/exp/等级，49 技能格，53 绑定/变异角标）
        self._sync_pet_runtime()
        self._pet_frames(conn, self._pet())
        self._send_scene(conn, self.px, self.py)
        self.log('   ↻ 战斗结束场景重同步（%s）' % self._map_def()['name'])
        self._persist()

    # ───────────────────────── 逃跑机制（真值：资料库第 30998/31023/31030 行）─────────────────────────
    def _escape_nominal_rate(self):
        """逃跑**名义**成功率（针对本模拟服 NPC 战：基础 50% + NPC 加成 30%）。

        ⚠️ 半真值：数值是对资料库定性描述（§30998/31023/31030）的模拟代表数，
        不是原服反推。gm_battle_dict / _try_escape 共用，保证面板显示与实判一致。
        """
        rate = ESCAPE_MECHANICS.get('base_success_rate', 0.5)
        # 本模拟服只与 NPC 战斗，NPC 加成恒适用
        rate += ESCAPE_MECHANICS.get('npc_bonus', 0.3)
        return max(0.0, min(1.0, rate))

    def _try_escape(self, conn):
        """尝试逃跑，返回是否成功。

        真值规则（资料库 §30998/31023/31030）：
          - 逃跑并非百分百成功；失败则被对方白打一回合（_escape_failed 标记）
          - 与普通 NPC 战斗成功率大幅增加；队长技能可压对方成功率；自身技能可提高
            （后两项简化为 ±10%/级，暂未实现队长/技能系统，预留 0 权重）
        ★ GM 调试：self._escape_force 可强制（'success'/'fail'/0..1），由
          gm_set_escape_force 设置；除强制值外与公式判定路径完全同源。
        """
        nominal = self._escape_nominal_rate()
        force = getattr(self, '_escape_force', None)
        if force == 'success':
            rate, success, forced = 1.0, True, 'success'
        elif force == 'fail':
            rate, success, forced = 0.0, False, 'fail'
        elif isinstance(force, (int, float)) and not isinstance(force, bool):
            rate = max(0.0, min(1.0, float(force)))
            success = self.battle_rng.chance(brng.S_ESCAPE, rate)
            forced = '%d%%' % int(rate * 100)
        else:
            rate, forced = nominal, None     # 队长压制/自身技能未实现，权重 0
            success = self.battle_rng.chance(brng.S_ESCAPE, rate)
        self._escape_last = {'time': time.strftime('%H:%M:%S'),
                             'rate': round(rate, 3), 'forced': forced,
                             'success': bool(success)}
        self.log('   逃跑判定：名义%.0f%%（强制=%s，实施率%.0f%%）→ %s'
                 % (nominal * 100, forced if forced else '否', rate * 100,
                    '成功' if success else '失败'))
        return success

    # ───────────────────────── GM 调试面板（Web 控制）─────────────────────────
    def _gm_send(self, data):
        """向已连接的游戏客户端即时下发协议帧（未连接则忽略）。

        ★ sendall 也在锁内（2026-10-20）：只锁「取 _conn」不够——
          那会让**发文动作本身**落在锁外，与内核线程的 sendall 交错。
          帧序交错不会抛异常，只会让客户端按错误顺序解析 opcode。
        ★ 不自己取锁、由调用方持锁：调用方（gm_* / debugger）已持有 _lock，
          本函数假定「调用方已进入临界区」，直接发。这样既不会嵌套等待，
          也保证「取 conn → 发帧」是同一个临界区。
        """
        c = self._conn
        if c is None:
            return False
        try:
            c.sendall(data)
            # 帧批合并：GM 单帧推送不走分派路径，这里立刻收口
            self._flush_out()
            return True
        except (OSError, ConnectionError):
            return False

    def gm_state_dict(self):
        # ★ 整份快照在锁内一次取完：拆成多次取会读到「半更新」组合
        #   （例如 items 已加、coins 还没扣），GM 面板会显示不可能的中间态。
        with self._lock:
            return {
                'connected': self._conn is not None,
                'hero_hp': self.hero_hp, 'hero_hp_max': HERO_HP,
                'hero_res': dict(self.hero_res),
                'coins': self.coins,
                'exp': self.exp, 'next_exp': self.next_exp,
                'px': self.px, 'py': self.py,
                'mapid': self.mapid,
                'mapname': self._map_def()['name'],
                # 孤岛：从出生图 BFS 不可达，只能靠 GM 切图（见 MAPS 上方的注释）
                'map_island': self.mapid in ISLAND_MAP_IDS,
                'pet_hp': self.pet_hp, 'pet_hp_max': self._pet_hpmax(),
                'pet_alive': self._pet_alive,
                'active_pet_id': self.active_pet_id,
                # 宠物栏概要（2026-10-22）：面板上直接看得到「有几只/谁出战/几级」
                'pet_count': len(self.pets),
                'pets': [{'pid': p['pid'], 'name': p.get('name'),
                          'shape': p.get('shape'), 'level': p.get('level'),
                          'exp': p.get('exp'), 'expnext': p.get('expnext'),
                          'point': p.get('point'), 'hp': p.get('hp'),
                          'hpmax': p.get('hpmax'),
                          'skills': list(p.get('skills') or []),
                          'lookon': bool(p.get('lookon')),
                          'bound': bool(p.get('bound')),
                          'active': p['pid'] == self.active_pet_id}
                         for p in (self.pets[k] for k in sorted(self.pets))],
                'in_war': self.in_war,
                'enemy_count': len(self.enemy_hp),
                # 生活技能（2026-10-20）：life_lv 恒为 LIFE_LV_INIT——工艺升级未实现，
                # 见 FEATURE_STATUS['craft_levelup']。这里如实暴露，面板上能直接看出
                # 「熟练度在涨、等级不动」，不用去翻代码猜。
                'life_lv': self.life_lv, 'life_lv_max': LIFE_LV_MAX,
                'life_exp': self.life_exp, 'life_exp_max': LIFE_EXP_MAX,
                'huoli': self.huoli,
                'encounter_every': self.encounter_every,
                # 任务状态（GM 面板与 _e2e_live_exp.py 判定 op100 正/负路径用）
                'tasks': {tid: {'state': t.get('state'),
                              'progress': t.get('progress', 0)}
                          for tid, t in self.tasks.items()},
                # 主线剧情（2026-10-22）：等级/剧情点/当前任务/已完成
                'grade': self.grade,
                'story_point': self.story_point,
                'quests': {'active': (self._quest_active()[0]),
                           'done': sorted(self.quests_done)},
                # 帧批合并（2026-10-22）：调试台看「帧 N / 写 M 次 / 合并率」，
                #   pending>0 表示有帧压在缓冲里没发（不该出现，出现即 flush 漏了）。
                'batch': self.frame_batch_stats(),
                # 存档落盘（2026-10-22）：异步合并 + 增量跳过
                'save': self.save_stats(),
                'items': [{'iid': iid, 'shape': v.get('shape', 0),
                           'qty': v.get('qty', 0),
                           'name': v.get('infoext', '')}
                          for iid, v in sorted(self.items.items())],
            }

    def gm_set_hp(self, v):
        with self._lock:
            self.hero_hp = max(0, min(HERO_HP, int(v)))
            self._gm_send(build_prop_change_hp(self.hero_hp))
            return self.hero_hp

    def gm_set_res(self, name, v):
        with self._lock:
            found = [c for c in COST_PROPS if c[0] == name]
            if not found:
                return None
            _, idx, width = found[0]
            self.hero_res[name] = max(0, min(100, int(v)))
            self._gm_send(build_prop_change(idx, self.hero_res[name], width))
            return self.hero_res[name]

    def gm_set_coins(self, v):
        with self._lock:
            # P3-05：GM 改钱也走统一入口（留流水 + 发事件）。原先直接赋值，
            # 于是「GM 把钱包改成 99999」在流水里查不到，对账会莫名差一大截。
            res = self._econ_set(max(0, int(v)), '[GM] 设金钱')
            # ★ 原先只发一条聊天、不发 PropChange —— 客户端金钱数字不会变，
            #   GM 面板上看着「设成功了」，游戏里还是旧值（2026-10-23 补）。
            if res.ok and res.delta:
                self._gm_send(build_prop_change(PROP_IDX_GOLD, self.coins, 4))
            self._gm_send(build_npc_chat_ext(NPC_NAME, 'P\n[GM] 金钱已设为 %d' % self.coins))
            if not res.ok:
                self.log('   ⚠ [GM] 设金钱未生效：%s' % res.message)
            return self.coins

    def gm_set_grade(self, level):
        """GM 设等级（主线 1–3 有 10/20/30 级门槛，测试与试玩都需要跳过刷级）。"""
        with self._lock:
            self.grade = max(1, min(GRADE_MAX, int(level)))
            self.next_exp = EXP_PER_LEVEL_STEP * self.grade
            self._gm_send(build_prop_change(PROP_IDX_GRADE, self.grade, 1))
            self._gm_send(build_prop_change(PROP_IDX_NEXTEXP, self.next_exp, 4))
            self._gm_send(build_npc_chat_ext(NPC_NAME, 'P\n[GM] 等级已设为 %d' % self.grade))
            self.log('   [GM] 等级 → %d（下一级 %d）' % (self.grade, self.next_exp))
            self._persist()
            return self.grade

    def gm_add_item(self, shape, qty, name):
        with self._lock:
            iid = self._alloc_iid()
            self.items[iid] = {'shape': int(shape), 'key': 0, 'qty': max(1, int(qty)),
                               'pos': 0, 'infoext': name or 'GM物品'}
            self._gm_send(build_item_add(ITEM_WTYPE_NORMAL, iid, int(shape), 0,
                                         self.items[iid]['qty'], 0))
            return iid

    def gm_del_item(self, iid):
        with self._lock:
            iid = int(iid)
            if iid in self.items:
                del self.items[iid]
                self._gm_send(build_item_del(ITEM_WTYPE_NORMAL, iid))
                return True
            return False

    def gm_set_pos(self, x, y):
        with self._lock:
            self.px, self.py = int(x), int(y)
            self._gm_send(build_goto_m(self.px, self.py))
            return (self.px, self.py)

    def gm_goto_map(self, mapid, x=None, y=None):
        """GM 切图：复用 `_goto_map`（S2CMapSceneM + AddNpc + GotoM 整包 + 落盘）。

        与传送使走的是同一条代码路径，区别只是触发者是面板而不是 NPC 对话。
        没有已连接客户端时只改服务端状态、不下发（下次进世界自然生效）。
        """
        mapid = int(mapid)
        # ★ 切图会发 SceneM + AddNpc×N + GotoM 整包（_goto_map → _send_scene），
        #   并发下发会与内核的 MapTrack / WarAdd 交错 → 客户端场景对象错乱。
        with self._lock:
            c = self._conn
            if c is None:
                self.log('   [GM] 切图 %d：当前无客户端连接，仅改服务端状态' % mapid)
                self.mapid = mapid
                d = self._map_def()
                self.px = d['spawn'][0] if x is None else int(x)
                self.py = d['spawn'][1] if y is None else int(y)
                return {'mapid': self.mapid, 'name': d['name'], 'pushed': False}
            self._goto_map(c, mapid, x, y)
            self._flush_out()   # 帧批合并：GM 整包切图不走分派路径，立即收口
            d = self._map_def()
            pool = scene_pool(d['name']) if scene_pool else None
            self.log('   [GM] 切图 → %s(%d)，掉落池：%s'
                     % (d['name'], mapid, '有' if pool else '无（城镇）'))
            return {'mapid': mapid, 'name': d['name'], 'pushed': True,
                    'has_drop_pool': bool(pool)}

    def gm_set_pet_hp(self, v):
        """GM 改**出战宠物**的气血（同时写回实例，读档/切宠后不会跳回旧值）。"""
        with self._lock:
            hpmax = self._pet_hpmax()
            self.pet_hp = max(0, min(hpmax, int(v)))
            self._sync_pet_runtime()
            if self.in_war and self._pet_alive:
                per = int(self.pet_hp * 100.0 / hpmax)
                self._gm_send(build_war_hp_percent(PET_WAR_ID, per))
                self._gm_send(build_war_wstate(PET_WAR_ID, HURT_NONE, LIFE_NORMAL,
                                               WARD_HIT, 0))
            elif not self.in_war and self._pet() is not None:
                self._pet_frames(_GmSink(self), self._pet())
            return self.pet_hp

    def gm_set_encounter(self, every):
        with self._lock:
            self.encounter_every = max(0, int(every))
            return self.encounter_every

    # ───────────────────── 战斗调试（GM，2026-10-21）─────────────────────
    # ★ 语义：这里全部是「调试器式的旁路开关」，不是新玩法 -
    #   动的是模块常量 / 临时运行态，不碰存档、不改变真实链路（伤害/逃跑/结算
    #   仍走同一条代码路径，只是参数被 GM 覆盖了）。每个 setter 的返回值都
    #   自报家门（覆盖了什么、到下次 reload/重启前有效），面板据实渲染。
    def _gm_pet_dict(self):
        """出战宠物的数值快照（GM 面板 /api/battle 的 pet 段）。

        没有出战宠物时给「空壳 + active=False」，面板据此区分
        「没带宠物」与「宠物数值为 0」—— 旧实现一律报模块常量，两者分不开。
        """
        p = self._pet()
        if p is None:
            return {'id': 0, 'pid': 0, 'active': False, 'name': '', 'shape': 0,
                    'level': 0, 'factor_idx': 0, 'factor_name': '',
                    'combat': pet_combat_stats(None),
                    'display': {'at': PET_AT_DISPLAY, 'df': PET_DF_DISPLAY,
                                'speed': PET_SPEED_DISPLAY,
                                'magdef': PET_MAGDEF_DISPLAY},
                    'hp': 0, 'hpmax': 0, 'mp': 0, 'fp': 0,
                    'true_stats': {}, 'attr_lv': {},
                    'exp': 0, 'expnext': 0, 'point': 0,
                    'skills': [], 'lookon': False, 'bound': False}
        fidx = pet_factor_idx_of(p)
        stats = p.get('stats') or {}
        return {
            'id': p['pid'], 'pid': p['pid'], 'active': True,
            'name': p.get('name'), 'shape': p.get('shape'),
            'level': p.get('level'), 'factor_idx': fidx,
            'factor_name': (FACTOR_DATA.get(fidx, {}) or {}).get('name', '？'),
            # 战斗计算值（reverse 标定 0..255 × 真值相对倍率）
            'combat': pet_combat_stats(p),
            # 面板显示值（真值公式 0..65535 + 加点）
            'display': {'at': stats.get('at'), 'df': stats.get('df'),
                        'speed': stats.get('sp'), 'magdef': stats.get('md')},
            'hp': int(self.pet_hp), 'hpmax': p.get('hpmax'),
            'mp': p.get('mp'), 'fp': p.get('fp'),
            # 真值属性表（petfightdata 公式全 6 项 + 疲劳）
            'true_stats': dict(stats), 'attr_lv': dict(p.get('attr_lv') or {}),
            'exp': p.get('exp'), 'expnext': p.get('expnext'),
            'point': p.get('point'),
            'skills': list(p.get('skills') or []),
            'lookon': bool(p.get('lookon')), 'bound': bool(p.get('bound')),
        }

    def gm_set_story_point(self, n):
        """设定剧情点（GM 面板/控制台）：不改已完成列表，只动余额。

        与 `_hero_learn_juqing` 走同一套状态，所以「设点 → 学技能」这条链
        在面板和客户端走的是同一条代码。
        """
        n = max(0, int(n))
        self.story_point = n
        self._persist()
        return n

    def gm_battle_dict(self):
        """战斗调试快照（GET /api/battle / SSE battle 事件的数据源）。

        一张表看全四件事：
          · 数值分层：主角/宠物/敌方的 *_COMBAT（0..255，伤害公式用）
            与宠物 *_DISPLAY（真值公式 0..65535，面板显示用）——两层不同源，
            这是 2026-10-21 真值战斗系统落地的核心约定；
          · 参数：伤害模型/常数/快照常量（reverse 标定，能复现录像 50/48/20）；
          · 运行态：本战敌方单位（hp/速度/造型）、控制点、逃跑强制与最近判定；
          · 真值属性表：宠物业 6 项公式值（_PET_STATS，资质联动等级）。
        ★ 整份快照在锁内一次取完（与 gm_state_dict 同口径，防「半更新」）。
        """
        with self._lock:
            # 本战敌方元数据（不在战斗中时为空 → 回落模块常量，面板显示「未开战」口径）
            _em = None
            if self.in_war and getattr(self, 'enemy_meta', None):
                _ids = self._enemy_ids()
                _em = self.enemy_meta.get(_ids[0]) if _ids else None
            units = []
            for wid, meta in (getattr(self, 'enemy_meta', {}) or {}).items():
                units.append({
                    'wid': wid, 'hp': self.enemy_hp.get(wid, 0),
                    'speed': meta.get('speed'), 'shape': meta.get('shape'),
                    'name': meta.get('name'), 'spawn_index': meta.get('spawn_index'),
                })
            return {
                'in_war': self.in_war,
                # —— 参数层 ——
                'dmg': {'model': DMG_MODEL, 'gain': DMG_GAIN, 'floor': DMG_FLOOR,
                        'p_div': DMG_P_DIV, 'mode': DMG_MODE,
                        'rounding': DMG_ROUNDING, 'rng_lo': DMG_RNG_LO,
                        'rng_hi': DMG_RNG_HI},
                'snapshots': {'attack_dmg': ATTACK_DMG, 'pet_damage': PET_DAMAGE,
                              'enemy_attack_dmg': ENEMY_ATTACK_DMG},
                # —— 单位层 ——
                'hero': {'at': HERO_AT, 'df': HERO_DF, 'dp': HERO_DP,
                         'level': HERO_LEVEL},
                # 宠物：出战那只的**实例**数值（换宠/升级/加点后跟着变）
                'pet': self._gm_pet_dict(),
                'enemy': {'name': (_em or {}).get('name', ENEMY_NAME),
                          'shape': (_em or {}).get('shape', ENEMY_SHAPE),
                          'scene': getattr(self, '_enc_scene', None),
                          'level': ENEMY_LEVEL, 'hp': ENEMY_HP,
                          'at': ENEMY_AT, 'df': ENEMY_DF, 'dp': ENEMY_DP,
                          'skill_chance': ENEMY_SKILL_CHANCE,
                          'hurt_bs_chance': ENEMY_HURT_BS_CHANCE},
                # —— 招式层（2026-10-22）：逐级消耗 / 威力效率模型 / 宠物招式效果 ——
                #   面板要能一眼看出「现在这一发用的是几级、扣多少、伤害怎么来的」，
                #   否则调数值只能靠翻日志。
                'magic': {
                    'model': MAGIC_DAMAGE_MODEL,
                    'max_mult': MAGIC_DAMAGE_MAX_MULT,
                    'calibration': {
                        'tolerance': MAGIC_CALIBRATION_TOLERANCE,
                        'hits': {str(k): list(v)
                                 for k, v in sorted(MAGIC_CALIBRATION_HITS.items())},
                    },
                    'hero_res': dict(self.hero_res),
                    'skills': [{
                        'id': mid, 'name': (MAGIC_INFO.get(mid) or {}).get('name', ''),
                        'level': self._magic_level(mid),
                        'max_level': int(magic_max_level(mid) or 1),
                        'cost': self._magic_cost(mid),
                        'pow': int(magic_pow_at(mid, self._magic_level(mid),
                                                self._magic_base_skill_level(mid))),
                        'eff': int(magic_powrate_at(mid, self._magic_level(mid),
                                                    self._magic_base_skill_level(mid))),
                        'targets': magic_target_limit(mid, self._magic_level(mid)),
                        'group': magic_is_group(mid),
                        'damage': self._magic_damage(mid) if self.in_war else None,
                    } for mid in sorted(self.magics)],
                },
                'pet_skills': {
                    'ids': list(pet_skill_ids_of(self._pet())),
                    'active': [sid for sid in pet_skill_ids_of(self._pet())
                               if (PET_SKILL_EFFECTS.get(sid) or {}).get('type')
                               in (PET_SKILL_ATTACK, PET_SKILL_AUX)],
                    'res': dict(getattr(self, 'pet_res', {}) or {}),
                    'effects': {str(k): dict(v) for k, v in PET_SKILL_EFFECTS.items()},
                    'passives': {str(k): dict(v) for k, v in PET_PASSIVE_EFFECTS.items()},
                    'live_passives': self._pet_passives(),
                },
                # —— 宠物栏（2026-10-22）：每只的等级/经验/资质/战斗层/资源 ——
                'pets': [{
                    'pid': p['pid'], 'name': p.get('name'), 'shape': p.get('shape'),
                    'level': p.get('level'), 'exp': p.get('exp'),
                    'expnext': p.get('expnext'),
                    'attr_lv': {str(k): v for k, v in (p.get('attr_lv') or {}).items()},
                    'point': p.get('point'),
                    'str': p.get('str'), 'con': p.get('con'), 'mag': p.get('mag'),
                    'cha': p.get('cha'), 'dex': p.get('dex'),
                    'skills': list(p.get('skills') or []),
                    'active': p['pid'] == self.active_pet_id,
                    'lookon': bool(p.get('lookon')), 'bound': bool(p.get('bound')),
                    'hp': (int(self.pet_hp) if p['pid'] == self.active_pet_id
                           else int(p.get('hp') or 0)),
                    'hpmax': p.get('hpmax'),
                    'resource': {'mp': p.get('mp'), 'fp': p.get('fp')},
                    'combat': pet_combat_stats(p),
                    'true_stats': dict(p.get('stats') or {}),
                } for p in (self.pets[k] for k in sorted(self.pets))],
                # —— 运行态 ——
                'units': units,
                'control_points': {
                    'init': dict(getattr(self, '_cp_init', {}) or {}),
                    'current': dict(getattr(self, '_control_points', {}) or {}),
                },
                'escape': {
                    'nominal_rate': self._escape_nominal_rate(),
                    'force': getattr(self, '_escape_force', None),
                    'last': dict(getattr(self, '_escape_last', {}) or {}),
                    'failed': bool(getattr(self, '_escape_failed', False)),
                },
            }

    def gm_set_cp(self, atk=None, dfn=None, dis=None):
        """GM 设控制点：同时改「初始值」（下次开战重置为它）与「当前值」。

        只传想改的键（None 不动）。范围 0..255（资料库半真值初始 30/30/30）。
        """
        with self._lock:
            cp = dict(getattr(self, '_cp_init', None)
                      or CONTROL_POINTS.get('npc_battle_init',
                                            {'atk': 30, 'def': 30, 'dis': 30}))
            for k, v in (('atk', atk), ('def', dfn), ('dis', dis)):
                if v is not None:
                    cp[k] = max(0, min(255, int(v)))
            self._cp_init = cp
            cur = getattr(self, '_control_points', None)
            if cur is not None:
                for k in cp:                         # 只把改过的键同步到当前值
                    if (atk is not None and k == 'atk'
                            or dfn is not None and k == 'def'
                            or dis is not None and k == 'dis'):
                        cur[k] = cp[k]
            self.log('   [GM] 控制点：init=%s current=%s' % (self._cp_init,
                                                            self._control_points))
            return {'init': dict(self._cp_init),
                    'current': dict(self._control_points)}

    def gm_set_escape_force(self, mode):
        """GM 强制逃跑判定：'success' / 'fail' / 'auto'（解除） / 0..1 或 0..100 数值。

        只影响下一次 C2SWarEscape 的判定（惰性使用，不自动清除）；
        与公式判定共用同一条 _try_escape 路径，追迹日志照记。
        """
        with self._lock:
            if mode is None or (isinstance(mode, str)
                                and mode.strip().lower() in ('auto', 'off', '公式', '')):
                self._escape_force = None
                return {'mode': 'auto', 'nominal_rate': self._escape_nominal_rate()}
            if isinstance(mode, str):
                s = mode.strip().lower()
                if s in ('success', '成功', '必定成功'):
                    self._escape_force = 'success'
                elif s in ('fail', 'faild', '失败', '必定失败'):
                    self._escape_force = 'fail'
                else:
                    # 数字字符串：允许 0..100（百分比）或 0..1（概率）
                    f = float(s)
                    self._escape_force = f
            else:
                self._escape_force = mode
            f = self._escape_force
            if isinstance(f, (int, float)) and not isinstance(f, bool) and f > 1:
                f = max(0.0, min(100.0, f)) / 100.0
                self._escape_force = f
            out = {'mode': ('success' if self._escape_force == 'success'
                            else 'fail' if self._escape_force == 'fail'
                            else 'prob=%.0f%%' % (float(self._escape_force) * 100
                                                  if self._escape_force is not None else 0))}
            out['nominal_rate'] = self._escape_nominal_rate()
            self.log('   [GM] 逃跑强制 → %s（名义 %.0f%%）'
                     % (out['mode'], out['nominal_rate'] * 100))
            return out

    def gm_set_pet_level(self, level, factor_idx=None):
        """GM 改**出战宠物**的等级（写实例 + 重算资质/属性 + 重发身份帧）。

        与旧的 `recompute_pet_display()` 的区别：那只改的是**模块常量**（全服共用一份
        假想宠物的面板值），现在改的是个体，切宠/读档不会串。
        入参 factor_idx 已无意义（资质表按造型查 PET_FACTOR_BY_SHAPE），只为兼容保留。
        """
        with self._lock:
            p = self._pet()
            if p is None:
                self.log('   [GM] 设宠物等级失败：当前没有出战宠物')
                return {'level': 0, 'factor_idx': 0, 'factor_name': '',
                        'stats': {}, 'display': {}}
            p['level'] = max(1, min(PET_LEVEL_MAX, int(level)))
            if factor_idx is not None:
                # GM 调试用：临时换 FactorData 资质表（0/None = 恢复按造型查表）
                try:
                    fi = int(factor_idx)
                except (TypeError, ValueError):
                    fi = 0
                p['factor_idx'] = fi if fi in FACTOR_DATA else 0
            pet_normalize(p)
            if int(self.pet_hp) > 0:
                self.pet_hp = min(int(p['hpmax']), max(int(self.pet_hp), 1))
            self._sync_pet_runtime()
            fidx = pet_factor_idx_of(p)
            stats = dict(p.get('stats') or {})
            r = {'pid': p['pid'], 'level': p['level'], 'factor_idx': fidx,
                 'factor_name': (FACTOR_DATA.get(fidx, {}) or {}).get('name', '？'),
                 'stats': stats,
                 'combat': pet_combat_stats(p),
                 'display': {'at': stats.get('at'), 'df': stats.get('df'),
                             'speed': stats.get('sp'), 'magdef': stats.get('md')}}
            self.log('   [GM] 宠物 id=%d 等级 → %d（FactorData %s/%s），'
                     '真值 at=%s df=%s sp=%s｜战斗层 %s'
                     % (p['pid'], p['level'], fidx, r['factor_name'],
                        stats.get('at'), stats.get('df'), stats.get('sp'),
                        r['combat']))
            self._pet_frames(_GmSink(self), p)
            return r

    def gm_pet_learn_skill(self, sid, pid=None):
        """GM 让宠物学一个技能（客户端没有这条上行协议，见 pet_learn_skill 注释）。"""
        with self._lock:
            p = self._pet(pid)
            if p is None:
                self.log('   [GM] 学技能失败：没有目标宠物（pid=%s）' % pid)
                return {'ok': False, 'reason': 'no_pet'}
            ok, why = pet_learn_skill(p, sid)
            if ok:
                self._pet_frames(_GmSink(self), p)
                self.log('   [GM] 宠物 id=%d 学会技能 %s（共 %d 个）'
                         % (p['pid'], sid, len(p.get('skills') or [])))
            else:
                self.log('   [GM] 宠物 id=%d 学技能 %s 被拒（%s）'
                         % (p['pid'], sid, why))
            return {'ok': ok, 'reason': why, 'pid': p['pid'],
                    'skills': list(p.get('skills') or [])}

    def gm_pet_forget_skill(self, sid, pid=None):
        """GM 让宠物遗忘一个技能（「要诀冲掉原有技能」里「冲掉」那一步的机制）。

        客户端没有这条上行协议，只能在服务端动；成功后重发 48/49/53，
        面板立刻少一格（不然就是「服务端删了、客户端还显示」）。
        """
        with self._lock:
            p = self._pet(pid)
            if p is None:
                self.log('   [GM] 遗忘技能失败：没有目标宠物（pid=%s）' % pid)
                return {'ok': False, 'reason': 'no_pet'}
            ok, why = pet_forget_skill(p, sid)
            if ok:
                self._pet_frames(_GmSink(self), p)
                self.log('   [GM] 宠物 id=%d 遗忘技能 %s（剩 %d 个）'
                         % (p['pid'], sid, len(p.get('skills') or [])))
            else:
                self.log('   [GM] 宠物 id=%d 遗忘技能 %s 被拒（%s）'
                         % (p['pid'], sid, why))
            return {'ok': ok, 'reason': why, 'pid': p['pid'],
                    'skills': list(p.get('skills') or [])}

    def gm_pet_stats(self, pid=None):
        """宠物属性**来源分析**：面板层 / 战斗层 / 资源层各是多少、加点贡献多少。

        为什么需要它：宠物面板数字（0..65535）和战斗里真正用的数字（0..255）
        差两个数量级，光看 `stats` 分不清「这只宠物强在哪一层」。加上
        「公式 vs 加点」的差值，配置错乱时一眼能定位是加点换算还是资质表的问题。

        返回的 `lines` 直接就是给人看的（GM 面板 `petstats` 展示），
        `gaps` 是证据缺口清单（装备/资质/要诀），避免有人以为「没坏就是全都算对了」。
        """
        with self._lock:
            p = self._pet(pid)
            if p is None:
                return {'ok': False, 'reason': 'no_pet'}
            fi = pet_factor_idx_of(p)
            base_panel = {}
            if _HAS_TRUE_FORMULAS:
                # 纯公式（不含加点）：给 explain 做差用
                base_panel = calc_pet_stats(fi,
                                            max(1, int(p.get('level', 1))),
                                            p.get('attr_lv') or {},
                                            tire=int(p.get('tire', 0) or 0))
            panel = dict(p.get('stats') or {})
            combat_base = pet_combat_base()
            cr = _pet_combat_ratios(p)
            combat = ppg.combat_stats(combat_base, cr)
            res = pet_resource_max(p)
            lines = ppg.explain_lines(p['pid'], p.get('level', 1), base_panel,
                                      panel, combat_base, combat, res, cr,
                                      p.get('name') or '')
            lines.append(u'  五维：Con %s / Str %s / Mag %s / Cha %s / Dex %s（剩 %s 点）'
                         % (p.get('con'), p.get('str'), p.get('mag'),
                            p.get('cha'), p.get('dex'), p.get('point')))
            for ln in pss.describe(p.get('skills')):
                lines.append(u'  技能 %-6s %s（%s）%s'
                             % (ln['sid'], ln['name'], ln['type_name'],
                                u'[绑定]' if ln['bound'] else ''))
            if not p.get('equips'):
                lines.append(u'  召唤兽装备：无 —— %s' % ppg.EQUIP_GAP)
            return {'ok': True, 'pid': p['pid'], 'name': p.get('name'),
                    'level': p.get('level'), 'factor_idx': fi,
                    'factor_name': (FACTOR_DATA.get(fi, {}) or {}).get('name', '？'),
                    'panel_base': base_panel, 'panel': panel,
                    'point_bonus': ppg.point_bonus(p),
                    'combat_base': combat_base, 'combat': combat, 'ratios': cr,
                    'resource': res,
                    'attrs': {k: p.get(k) for k in PET_POINT_ORDER},
                    'point': p.get('point'),
                    'skills': pss.describe(p.get('skills')),
                    'equips': dict(p.get('equips') or {}),
                    'lines': lines, 'gaps': ppg.provenance()}

    def gm_add_pet(self, shape, level=1, name=None):
        """GM 往宠物栏加一只（顺带验证多宠并存 / 切宠不串数据）。

        失败时 `reason` 是 `_add_pet` 记下的真实原因（bad_shape/full/
        save_blocked/persist_failed），不把「没入栏」笼统说成 full。
        """
        with self._lock:
            p = self._add_pet(_GmSink(self), shape, level=level, name=name)
            if p is None:
                return {'ok': False, 'reason': self._add_pet_last_error or 'full'}
            return {'ok': True, 'pid': p['pid'], 'shape': p['shape'],
                    'level': p['level'], 'name': p['name'],
                    'hpmax': p['hpmax'], 'combat': pet_combat_stats(p)}

    def gm_pet_equip(self, pid, pos, shape, desc=None):
        """GM 给宠物摆一件装备（调试入口，不走背包、不查等级门）。

        为什么有它：`PET_EQUIP_ITEMS` 的 shape 真值**不可得**（见该表注释），
        C2S 路径暂时没有可用的权威数据驱动；机制（op54 线序/替换语义/持久化/
        原子性）需要一条能直接驱动的路径来回归。desc 由调用方给（展示串，
        不是属性真值——数值效果一律不猜，见 PET_EQUIP_ITEMS 注释）。
        """
        with self._lock:
            try:
                pos = int(pos)
                shape = int(shape)
            except (TypeError, ValueError):
                return {'ok': False, 'reason': 'bad_value'}
            p = self.pets.get(int(pid or 0))
            if p is None:
                return {'ok': False, 'reason': 'no_pet'}
            if not (1 <= pos <= 3):
                return {'ok': False, 'reason': 'bad_pos'}
            if desc is None:
                desc = '召唤兽装备(%d)' % shape
            old = (p.get('equips') or {}).get(pos)
            p.setdefault('equips', {})[pos] = {'shape': shape,
                                               'desc': str(desc)}
            ok = self._persist()
            if self._save_blocked:
                ok = False
            elif ok is None:
                self.flush_saves(5.0)
                ok = bool(self._save_last_ok)
            if ok is not True:
                if old is None:
                    p['equips'].pop(pos, None)
                else:
                    p['equips'][pos] = old
                return {'ok': False, 'reason': 'persist_failed'}
            self._gm_send(build_pet_equip(p['pid'], p['equips']))
            return {'ok': True, 'pid': p['pid'], 'pos': pos, 'shape': shape,
                    'desc': str(desc), 'replaced': bool(old),
                    'equips': {str(k): v for k, v in p['equips'].items()}}

    def gm_pet_gain_exp(self, amount, pid=None):
        """GM 给宠物加经验（触发自动升级 + 重发 48/49/53）。"""
        with self._lock:
            p = self._pet(pid)
            if p is None:
                return {'ok': False, 'reason': 'no_pet'}
            ups = pet_gain_exp(p, int(amount))
            self._pet_frames(_GmSink(self), p)
            self.log('   [GM] 宠物 id=%d 经验 +%d → %d/%d，等级 %d%s'
                     % (p['pid'], int(amount), p['exp'], p['expnext'], p['level'],
                        '（升级 %d 次）' % len(ups) if ups else ''))
            return {'ok': True, 'pid': p['pid'], 'level': p['level'],
                    'exp': p['exp'], 'expnext': p['expnext'],
                    'ups': [list(u) for u in ups]}

    def gm_set_enemy_level(self, level):
        """GM 设敌方等级（recompute_enemy_constants 的会话封装）。

        重算 ENEMY_AT/ENEMY_DF（等级缩放，半真值）+ 伤害快照常量；
        战斗中则把现存敌方 HP 一次性重置到 ENEMY_HP（新等级不重算 HP，见下）。
        ENEMY_HP 仍为「100=模拟代表值」，不随等级联动 —— 它的口径是「能复现
        主角 50 → 宠物 48 → 主角 50 三击序列」，与等级缩放是两套标注。
        """
        with self._lock:
            r = recompute_enemy_constants(level)
            if self.in_war and self.enemy_hp:
                self.enemy_hp = {wid: int(ENEMY_HP) for wid in self.enemy_hp}
                self.log('   [GM] 战斗中敌方 HP 重置为 ENEMY_HP=%d（敌没有 m_HPFull，'
                         '不推 HPPercent，血条由 WState 飘字口径）' % ENEMY_HP)
            self.log('   [GM] 敌方等级 → %d：at=%d df=%d（覆盖配置值至下次 reload）'
                     % (r['level'], r['at'], r['df']))
            r['hp'] = int(ENEMY_HP)
            return r

    def gm_war_start(self):
        """GM 强制开战（等价于走够步数触发的那条 `_start_war`）。"""
        # ★ 整个开战流程都在锁内：_start_war 会连发 WarStart + WarAdd×N +
        #   WarTurn 多帧。若只锁「取 _conn」，这些帧会在锁外与内核战斗线程的
        #   发文交错 —— 客户端按 opcode 顺序解析就错位（实测交错 8 次）。
        with self._lock:
            c = self._conn
            if c is None:
                return {'ok': False, 'error': '当前无客户端连接'}
            if self.in_war:
                return {'ok': False, 'error': '已在战斗中'}
            self._start_war(c)
            self._flush_out()     # 帧批合并：GM 开战链不走分派路径，立即收口
            return {'ok': True, 'enemies': len(self.enemy_hp)}

    def gm_war_win(self):
        """GM 一键胜利：走 `_after_hero_action` 的真实胜利分支（WarDel→WarEnd→场景重同步→掉落）。

        不是假结算：掉落掷骰、背包入包、S2CItemAdd(33) 全走玩家正常打赢的同一条路径。
        """
        # ★ 同 gm_war_start：胜利链会连发 WarDel×N + WarEnd + 场景整包 +
        #   掉落 ItemAdd，中间还夹着 _persist() 落盘 —— 必须整体在锁内。
        with self._lock:
            c = self._conn
            if c is None:
                return {'ok': False, 'error': '当前无客户端连接'}
            if not self.in_war:
                return {'ok': False, 'error': '当前不在战斗中'}
            ids = list(self.enemy_hp)
            self._after_hero_action(c, dead_ids=ids)
            self._flush_out()     # 帧批合并：GM 胜利链不走分派路径，立即收口
            return {'ok': True, 'killed': len(ids)}

    # ───────────────────────── 任务编辑（GM）─────────────────────────
    # 走真实链路：改状态 → 重发 TaskAdd / TaskRemove →（完成时）发奖励。
    # 与玩家正常接/交任务共用同一批构建器与奖励路径（_turn_in_task 的字段）。
    def _gm_send_task_refresh(self, conn):
        """把当前任务状态重发到客户端（先 Remove 再 Add，客户端才会重建）。"""
        t = self.tasks.get(TASK_NEWBIE_ID)
        conn.sendall(build_task_remove(TASK_NEWBIE_ID))
        if t and t.get('state') != TASK_STATE_NONE:
            conn.sendall(build_task_add(TASK_NEWBIE_ID, TASK_NEWBIE_NAME,
                                        self._task_desc(), 1))

    def gm_task_set_progress(self, n):
        """设当前任务进度（0..NEED）。走真实链路：改进度 → 重发任务栏。"""
        with self._lock:
            n = max(0, min(TASK_NEWBIE_NEED, int(n)))
            t = self.tasks.setdefault(TASK_NEWBIE_ID, {'state': TASK_STATE_DOING,
                                                       'progress': 0})
            if t.get('state') == TASK_STATE_NONE:
                t['state'] = TASK_STATE_DOING
            t['progress'] = n
            if n >= TASK_NEWBIE_NEED:
                t['state'] = TASK_STATE_DONE
            c = self._conn
            if c is not None:
                self._gm_send_task_refresh(c)
                self._flush_out()
            self.log('   [GM] 任务进度 → %d/%d（状态 %s）'
                     % (n, TASK_NEWBIE_NEED, t['state']))
            self._persist()
            return {'progress': n, 'state': t['state']}

    def gm_task_state(self, state):
        """设任务状态：0=无 1=进行中 2=已完成。state=0 时移除任务。"""
        with self._lock:
            state = int(state)
            if state <= TASK_STATE_NONE:
                self.tasks.pop(TASK_NEWBIE_ID, None)
                if self._conn is not None:
                    self._conn.sendall(build_task_remove(TASK_NEWBIE_ID))
                    self._flush_out()
                self.log('   [GM] 任务移除')
                self._persist()
                return {'state': TASK_STATE_NONE}
            t = self.tasks.setdefault(TASK_NEWBIE_ID, {'state': 1, 'progress': 0})
            t['state'] = state
            if state == TASK_STATE_DONE and t['progress'] < TASK_NEWBIE_NEED:
                t['progress'] = TASK_NEWBIE_NEED       # 完成态必须带满进度
            if self._conn is not None:
                self._gm_send_task_refresh(self._conn)
                self._flush_out()
            self.log('   [GM] 任务状态 → %d' % state)
            self._persist()
            return {'state': state, 'progress': t['progress']}

    def gm_task_complete(self):
        """GM 一键完成：走真实交任务链（TaskRemove + 金钱/经验/道具奖励）。

        与 `_turn_in_task` 同一份奖励路径，区别只是不需要玩家走到村长面前。
        """
        with self._lock:
            c = self._conn
            if c is None:
                return {'ok': False, 'error': '当前无客户端连接'}
            self._turn_in_task(c)         # 真实链路：Remove + 奖励 + 落盘
            self._flush_out()
            return {'ok': True, 'reward': {'coins': TASK_REWARD_COINS,
                                            'exp': TASK_REWARD_EXP,
                                            'items': [list(x) for x in TASK_REWARD_ITEMS]}}
    def gm_heal(self):
        with self._lock:
            self.hero_hp = HERO_HP
            self._reset_hero_res()
            self._gm_send(build_prop_change_hp(self.hero_hp))
            for (_n, idx, width) in COST_PROPS:
                self._gm_send(build_prop_change(idx, self.hero_res[_n], width))
            return True

    def gm_refresh(self):
        with self._lock:
            self._gm_send(build_prop_change_hp(self.hero_hp))
            for (_n, idx, width) in COST_PROPS:
                self._gm_send(build_prop_change(idx, self.hero_res[_n], width))
            return True

    def gm_save(self):
        with self._lock:
            self._persist()
            return True

    # ───────────────────────── 控制台命令行解析器 ─────────────────────────
    # 与 GM 面板 _GMHandler.do_POST 的 api/* 路由是**同一套 gm_* 方法**：
    # 面板按钮、控制台输入、HTTP 命令都打到 M4Server 已有的 gm_*(...) 上，
    # 不存在「控制台绕过真实链路」的伪命令。
    _GM_CMD_HELP = """\
可用指令（全部走内核真实路径，与 GM 面板按钮等价）：
  war start               强制开战（等价走够步数触发的 _start_war）
  war win                 ★真实胜利链：gm_war_win() → _after_hero_action()
                          → build_war_del×N + build_war_end + in_war=False
                          → _on_enemy_dead → _resync_scene
                          → _award_battle_exp → _roll_drops
                          （非直接置 in_war=False，验证的是真实链路）
  hp <数值>               设主角 HP
  coins <数值>            设金钱
  grade <数值>            设等级（主线门槛 10/20/30，测试免刷级）
  res <名称> <数值>       设战斗资源（如 res mp 50；名称见面板下拉）
  pos <x> <y>             传送坐标（下发 GotoM）
  map <mapid> [x] [y]     切图（复用 _goto_map 整包）
  pet <数值>              设宠物 HP
  pet equip <pid> <pos> <shape> [名称]   ★召唤兽装备调试入口（不走背包、不查
                          等级门）：pos 1/2/3=项圈/铠甲/护符，真链路=内存+落盘
                          确认后才下发 op54，失败回滚零帧。C2S 路径的权威 shape
                          表不可得，见 PET_EQUIP_ITEMS 注释；HTTP 同功能：
                          POST api/pet/equip
  item add <shape> <qty> [名称]   进背包
  item del <iid>          删物品
  encounter <步数>        遇怪间隔（0 = 关闭）
  task progress <0..3>    设任务进度（走真实链路重发任务栏）
  task state <0|1|2>      设任务状态（0=移除 1=进行 2=完成）
  task complete           ★一键交任务：真实奖励链（金钱/经验/道具）
  battle                  战斗调试快照（参数/数值分层/控制点/逃跑/本战单位）
  cp [atk dfn dis]        设控制点初始值与当前值（缺省时只看）
  escape <success|fail|auto|数值>  逃跑强制：auto=解除，数值=0..100概率
  petlevel <n>            ★宠物等级重算（真值公式 at/df/speed/magdef）
  petstats [pid]          ★宠物属性来源分析（面板层/战斗层/资源层 + 加点贡献
                          + 技能清单 + 证据缺口；缺省 = 出战宠物）
  petskill <sid> [pid]    让宠物学技能（客户端无上行协议；校验客户端 404 条技能表）
  petforget <sid> [pid]   让宠物遗忘技能（「要诀冲掉原有技能」的机制面）
  enemylevel <n>          敌方等级重算（半真值等级缩放，覆盖配置至 reload）
  buff list [wid]         状态效果：查看（wid 缺省 = 所有有状态的单位）
  buff add <wid> <eid> [回合] [层数]   施加状态（eid 见 buff cat）
  buff del <wid> [eid]    移除一个状态 / 清空该单位全部状态
  buff cat [关键字]       查客户端 284 条状态真值表（标出哪些有可量化机制）
                          wid：主角=100，宠物=250，敌方=1..N
  rng [seed <n>|on|off]   ★战斗随机源：看种子/各流抽取次数；改种子后下一场生效
                          （P3-03：同种子同操作 → 逐位相同结果，录像可复现）
  settle                  ★本场胜负结算次数（正常必须 ≤1，>1 即有重入）
  aipolicy [random|focus] 敌方 AI 策略（random=复刻既有行为，focus=集火残血）
  point <数值>            设剧情点（剧情技能的唯一货币，主线奖励给的就是它）
  juqing [技能id]         学/升剧情技能（与客户端 C2S_NPC_LEARN_SKILL 同一条链；
                          缺省 = 只看已学哪些）
  heal / refresh / save   满血 / 重发状态 / 落盘
  help                    本帮助
"""

    def gm_command(self, line):
        """控制台命令行解析器：复用现有 gm_* 真实路径（与 GM 面板 _GMHandler 同源）。

        返回 {'ok', 'cmd', 'args', 'msg', 'result'}（ok=False 时带 'error'）。
        help 子指令额外返回 'help' 字段。
        """
        if line is None or not str(line).strip():
            return {'ok': False, 'cmd': None, 'args': [], 'msg': '',
                    'error': '空指令'}
        parts = str(line).strip().split()
        cmd = parts[0].lower()
        a = parts[1:]

        def _ret(ok, c, args, msg, result=None, error=None):
            d = {'ok': ok, 'cmd': c, 'args': args, 'msg': msg,
                 'result': result or {}}
            if error is not None:
                d['error'] = error
            return d

        def need(n):
            if len(a) < n:
                raise ValueError('需要 %d 个参数，只给了 %d' % (n, len(a)))

        try:
            # ── 二级：war ──
            if cmd == 'war':
                sub = a[0].lower() if a else ''
                if sub == 'start':
                    r = self.gm_war_start()
                    return _ret(r.get('ok', False), 'war start', a,
                                r.get('error')
                                or ('开战，敌方 %d 只' % r.get('enemies', 0)),
                                r)
                if sub in ('win', 'end'):
                    # 真实胜利链：gm_war_win → _after_hero_action（全歼敌方）
                    r = self.gm_war_win()
                    return _ret(r.get('ok', False), 'war win', a,
                                r.get('error')
                                or ('胜利：击杀 %d 只，已走真实胜利链'
                                    % r.get('killed', 0)),
                                r)
                return _ret(False, 'war', a, '',
                            error='未知子指令 %r（可选 start / win）' % sub)

            if cmd == 'hp':
                need(1); v = self.gm_set_hp(int(a[0]))
                return _ret(v is not None, 'hp', a, 'HP → %s' % v, {'hp': v})
            if cmd == 'coins':
                need(1); v = self.gm_set_coins(int(a[0]))
                return _ret(True, 'coins', a, '金钱 → %s' % v, {'coins': v})
            if cmd == 'grade':
                need(1); v = self.gm_set_grade(int(a[0]))
                return _ret(True, 'grade', a, '等级 → %s' % v, {'grade': v})
            if cmd == 'point':
                # 剧情点（m_StoryPoint）：直接设定，用来试「学剧情技能」这条消费链
                need(1); v = self.gm_set_story_point(int(a[0]))
                return _ret(v is not None, 'point', a, '剧情点 → %s' % v,
                            {'story_point': v})
            if cmd == 'juqing':
                # 学/升剧情技能（与客户端 C2S_NPC_LEARN_SKILL 同一条真实路径）
                if not a:
                    return _ret(True, 'juqing', a,
                                '剧情点 %d，已学剧情技能 %s'
                                % (self.story_point,
                                   {k: jqmod.JUQING_SKILLS.get(k, {}).get('name', k)
                                    for k in sorted(self.juqing_skills)}),
                                {'skills': dict(self.juqing_skills),
                                 'story_point': self.story_point})
                sid = int(a[0])
                _sink = _GmSink(self)
                ok = self._hero_learn_juqing(_sink, sid)
                return _ret(ok, 'juqing', a,
                            '学剧情技能 %s（%d）%s'
                            % (jqmod.JUQING_SKILLS.get(sid, {}).get('name', '?'), sid,
                               '' if ok else '失败：点不够或已满级'),
                            {'skills': dict(self.juqing_skills),
                             'story_point': self.story_point})
            if cmd == 'res':
                need(2); v = self.gm_set_res(a[0], int(a[1]))
                return _ret(v is not None, 'res', a,
                            '资源 %s → %s' % (a[0], v), {'res': v})
            if cmd == 'pos':
                need(2); v = self.gm_set_pos(int(a[0]), int(a[1]))
                return _ret(True, 'pos', a, '坐标 → %s' % (v,), {'pos': v})
            if cmd == 'map':
                need(1)
                x = int(a[1]) if len(a) > 1 else None
                y = int(a[2]) if len(a) > 2 else None
                r = self.gm_goto_map(int(a[0]), x, y)
                return _ret(True, 'map', a,
                            '切图 → %s(%s)'
                            % (r.get('name'), r.get('mapid')), r)
            if cmd == 'pet':
                if a and a[0].lower() == 'equip':
                    # pet equip <pid> <pos> <shape> [名称] —— 与 HTTP api/pet/equip
                    # 同源打 gm_pet_equip（面板/控制台/HTTP 三入口一条真实路径）
                    need(4)
                    desc = ' '.join(a[4:]) or None
                    r = self.gm_pet_equip(int(a[1]), int(a[2]), int(a[3]), desc)
                    if r.get('ok'):
                        msg = ('宠物 id=%s pos=%s(%s) 已佩带 shape=%s%s'
                               % (r['pid'], r['pos'],
                                  PET_EQUIP_POS_NAME.get(r['pos'], '?'),
                                  r['shape'],
                                  '，替换旧装备' if r.get('replaced') else ''))
                    else:
                        msg = '装备失败：%s' % (r.get('reason') or '未知')
                    return _ret(r.get('ok', False), 'pet equip', a, msg, r)
                need(1); v = self.gm_set_pet_hp(int(a[0]))
                return _ret(v is not None, 'pet', a, '宠物 HP → %s' % v,
                            {'pet_hp': v})
            if cmd == 'item':
                sub = a[0].lower() if a else ''
                if sub == 'add':
                    need(3); name = ' '.join(a[3:]) or ''
                    iid = self.gm_add_item(int(a[1]), int(a[2]), name)
                    return _ret(iid is not None, 'item add', a,
                                '新增物品 iid=%s shape=%s' % (iid, a[1]),
                                {'iid': iid})
                if sub == 'del':
                    need(2); ok = self.gm_del_item(int(a[1]))
                    return _ret(ok, 'item del', a,
                                '删除 iid=%s：%s'
                                % (a[1], 'OK' if ok else '不存在'),
                                {'deleted': ok})
                return _ret(False, 'item', a, '',
                            error='未知子指令 %r（add / del）' % sub)
            if cmd == 'task':
                sub = a[0].lower() if a else ''
                if sub == 'progress':
                    need(2); r = self.gm_task_set_progress(int(a[1]))
                    return _ret(True, 'task progress', a,
                                '任务进度 → %s/%s（状态 %s）'
                                % (r['progress'], TASK_NEWBIE_NEED, r['state']), r)
                if sub == 'state':
                    need(2); r = self.gm_task_state(int(a[1]))
                    return _ret(True, 'task state', a, '任务状态 → %s' % r['state'], r)
                if sub in ('complete', 'done'):
                    r = self.gm_task_complete()
                    return _ret(r.get('ok', False), 'task complete', a,
                                r.get('error') or '已走真实奖励链（金钱/经验/道具）', r)
                return _ret(False, 'task', a, '',
                            error='未知子指令 %r（progress/state/complete）' % sub)
            if cmd == 'encounter':
                need(1); v = self.gm_set_encounter(int(a[0]))
                return _ret(True, 'encounter', a, '遇怪间隔 → %s 步' % v,
                            {'every': v})
            # ── 战斗调试（2026-10-21）：快照 / 控制点 / 逃跑强制 / 等级重算 ──
            if cmd == 'battle':
                return _ret(True, 'battle', a, '战斗调试快照（GM 面板同源）',
                            {'battle': self.gm_battle_dict()})
            if cmd == 'cp':
                if a:
                    need(3)
                    r = self.gm_set_cp(int(a[0]), int(a[1]), int(a[2]))
                else:
                    r = self.gm_set_cp()
                return _ret(True, 'cp', a,
                            '控制点 init=%s current=%s' % (r['init'], r['current']), r)
            if cmd == 'escape':
                need(1)
                r = self.gm_set_escape_force(a[0])
                return _ret(True, 'escape', a,
                            '逃跑强制 → %s（名义 %.0f%%）'
                            % (r['mode'], r['nominal_rate'] * 100), r)
            if cmd == 'buff':
                # ── 状态效果（P3-03）──
                #   buff list [wid]        看状态
                #   buff add <wid> <eid> [turns] [stacks]
                #   buff del <wid> [eid]   删一个 / 清空
                #   buff cat [关键字]      查客户端 284 条状态真值表
                sub = a[0].lower() if a else 'list'
                if sub == 'list':
                    wid = int(a[1]) if len(a) > 1 else None
                    r = self.gm_buff_list(wid)
                    msg = '；'.join('%d: %s' % (w, v['summary'])
                                   for w, v in r.items()) or '（无状态）'
                    return _ret(True, 'buff list', a, msg, {'units': r})
                if sub == 'add':
                    need(3)
                    turns = int(a[3]) if len(a) > 3 else None
                    stacks = int(a[4]) if len(a) > 4 else 1
                    ok, reason, summary = self.gm_buff_add(
                        int(a[1]), int(a[2]), turns, stacks)
                    return _ret(ok, 'buff add', a,
                                ('已施加 → %s' % summary) if ok
                                else ('施加失败：%s' % reason),
                                {'reason': reason, 'summary': summary})
                if sub == 'del':
                    need(2)
                    eid = int(a[2]) if len(a) > 2 else None
                    ok, reason, summary = self.gm_buff_del(int(a[1]), eid)
                    return _ret(ok, 'buff del', a,
                                ('已移除 → %s' % summary) if ok
                                else ('移除失败：%s' % reason),
                                {'reason': reason, 'summary': summary})
                if sub == 'cat':
                    kw = a[1] if len(a) > 1 else ''
                    hit = [(i, bfd.get(i)) for i in bfd.ids()
                           if not kw or kw in (bfd.get(i) or '')]
                    msg = '命中 %d 条：%s' % (
                        len(hit),
                        '；'.join('#%d %s%s' % (i, d,
                                                '' if ceff.is_modeled(i)
                                                else '（无机制）')
                                 for i, d in hit[:8])
                        + ('…' if len(hit) > 8 else ''))
                    return _ret(True, 'buff cat', a, msg,
                                {'count': len(hit),
                                 'ids': [i for i, _d in hit],
                                 'modeled': [i for i, _d in hit
                                             if ceff.is_modeled(i)]})
                return _ret(False, 'buff', a, '',
                            error='未知子指令 %r（可选 list/add/del/cat）' % sub)
            if cmd == 'rng':
                # ── 战斗随机源（P3-03）──
                #   rng                看当前种子/场次/各流抽取次数
                #   rng seed <n>       改主种子并重置（下一次开战生效）
                #   rng on|off         开关固定种子流（off = 退回模块级 random）
                if not a:
                    return _ret(True, 'rng', a, self.battle_rng.summary(),
                                {'seed': self.battle_rng._seed,
                                 'battle': self.battle_rng._battle,
                                 'draws': self.battle_rng.draws(),
                                 'enabled': self.battle_rng.enabled})
                if a[0].lower() == 'seed':
                    need(2)
                    self.battle_rng.reset(int(a[1], 0))
                    return _ret(True, 'rng seed', a, self.battle_rng.summary(),
                                {'seed': self.battle_rng._seed})
                if a[0].lower() in ('on', 'off'):
                    self.battle_rng.enabled = (a[0].lower() == 'on')
                    return _ret(True, 'rng %s' % a[0].lower(), a,
                                '固定种子流 → %s'
                                % ('开' if self.battle_rng.enabled else '关'),
                                {'enabled': self.battle_rng.enabled})
                return _ret(False, 'rng', a, '',
                            error='未知子指令 %r（可选 seed/on/off）' % a[0])
            if cmd == 'settle':
                # ── 结算计数（P3-03）：确认「胜负只结算一次」──
                n = self._war_settle_count
                return _ret(True, 'settle', a,
                            '本场结算 %d 次（%s）%s'
                            % (n, '/'.join(self._war_settle_kinds) or '无',
                               '' if n <= 1 else '  ⚠ 重复结算'),
                            {'count': n, 'kinds': list(self._war_settle_kinds)})
            if cmd == 'aipolicy':
                # ── 敌方 AI 策略（P3-03）：random / focus ──
                if not a:
                    return _ret(True, 'aipolicy', a,
                                '当前策略 %s（%s）'
                                % (self.enemy_ai_policy,
                                   eai.POLICY_TITLE.get(self.enemy_ai_policy, '?')),
                                {'policy': self.enemy_ai_policy,
                                 'policies': list(eai.POLICIES)})
                p = a[0].lower()
                if p not in eai.POLICIES:
                    return _ret(False, 'aipolicy', a, '',
                                error='未知策略 %r（可选 %s）'
                                      % (p, ' / '.join(eai.POLICIES)))
                self.enemy_ai_policy = p
                return _ret(True, 'aipolicy', a,
                            '敌方策略 → %s（%s）' % (p, eai.POLICY_TITLE[p]),
                            {'policy': p})
            if cmd == 'petlevel':
                need(1)
                r = self.gm_set_pet_level(int(a[0]))
                st = r.get('stats') or {}
                if st:
                    return _ret(True, 'petlevel', a,
                                '宠物 id=%s 等级 → %s（真值 at=%s df=%s sp=%s｜战斗层 %s）'
                                % (r.get('pid'), r['level'], st.get('at'),
                                   st.get('df'), st.get('sp'), r.get('combat')), r)
                return _ret(True, 'petlevel', a,
                            '宠物等级 → %s（无真值表，battle_formulas 缺失？）' % r['level'], r)
            if cmd == 'petskill':
                # 宠物学技能：客户端没有这条上行协议，只能由 GM/道具触发
                need(1)
                r = self.gm_pet_learn_skill(int(a[0]),
                                            int(a[1]) if len(a) > 1 else None)
                return _ret(bool(r.get('ok')), 'petskill', a,
                            ('宠物 id=%s 学会 %s，现有 %s' % (r.get('pid'), a[0],
                                                             r.get('skills')))
                            if r.get('ok') else
                            ('学技能被拒（%s）' % r.get('reason')), r)
            if cmd == 'petadd':
                need(1)
                r = self.gm_add_pet(int(a[0]), int(a[1]) if len(a) > 1 else 1)
                return _ret(bool(r.get('ok')), 'petadd', a,
                            ('新宠物 id=%s %s（%s 级，HP %s）'
                             % (r.get('pid'), r.get('name'), r.get('level'),
                                r.get('hpmax')))
                            if r.get('ok') else '宠物栏已满', r)
            if cmd == 'petforget':
                # 遗忘技能：客户端没有上行协议；这是「要诀冲掉原有技能」的机制面
                need(1)
                r = self.gm_pet_forget_skill(int(a[0]),
                                             int(a[1]) if len(a) > 1 else None)
                return _ret(bool(r.get('ok')), 'petforget', a,
                            ('宠物 id=%s 遗忘 %s，现有 %s'
                             % (r.get('pid'), a[0], r.get('skills')))
                            if r.get('ok') else
                            ('遗忘被拒（%s）' % r.get('reason')), r)
            if cmd == 'petstats':
                # 属性来源分析（面板层/战斗层/资源层 + 加点贡献 + 缺口清单）
                r = self.gm_pet_stats(int(a[0]) if a else None)
                if not r.get('ok'):
                    return _ret(False, 'petstats', a, '', error=r.get('reason'))
                return _ret(True, 'petstats', a, chr(10).join(r['lines']), r)
            if cmd == 'petexp':
                need(1)
                r = self.gm_pet_gain_exp(int(a[0]))
                return _ret(bool(r.get('ok')), 'petexp', a,
                            ('宠物经验 → %s/%s，等级 %s%s'
                             % (r.get('exp'), r.get('expnext'), r.get('level'),
                                '（升级）' if r.get('ups') else '')), r)
            if cmd == 'enemylevel':
                need(1)
                r = self.gm_set_enemy_level(int(a[0]))
                return _ret(True, 'enemylevel', a,
                            '敌方等级 → %s（at=%s df=%s hp=%s）'
                            % (r['level'], r['at'], r['df'], r['hp']), r)
            if cmd == 'heal':
                self.gm_heal()
                return _ret(True, 'heal', a, '已回满血/资源', {})
            if cmd == 'refresh':
                self.gm_refresh()
                return _ret(True, 'refresh', a, '已重发状态', {})
            if cmd == 'save':
                self.gm_save()
                return _ret(True, 'save', a, '已落盘存档', {})
            if cmd in ('help', '?', 'h'):
                return {'ok': True, 'cmd': 'help', 'args': [], 'msg': '',
                        'help': self._GM_CMD_HELP}
            return _ret(False, cmd, a, '',
                        error='未知指令 %r（输入 help 查看）' % cmd)
        except (ValueError, IndexError) as e:
            return _ret(False, cmd, a, '', error='参数错误：%s' % e)

    def _dashboard_html(self):
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'gm_dashboard.html'), 'r', encoding='utf-8') as f:
                return f.read()
        except OSError:
            return '<h1>GM dashboard html not found</h1>'

    @staticmethod
    def _port_free(port, host='127.0.0.1'):
        """探测端口是否可绑（2026-10-20）。

        用「绑一下再关掉」判断，而不是 connect()——后者只能发现「有人在听」，
        发现不了「被 TIME_WAIT / 已 bind 未 listen 占着」这类更常见的情况。
        SO_REUSEADDR 故意**不设**：设了会把 TIME_WAIT 也算成可用，
        而 Windows 上真正 bind 时仍可能失败，探测就失真了。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, int(port)))
            return True
        except OSError:
            return False
        finally:
            try:
                s.close()
            except OSError:
                pass

    def _pick_web_port(self, want, explicit=False, tries=20):
        """端口冲突时自动顺延，返回 (port, bumped, reason)。

        ★ 2026-10-20：旧实现直接 bind，端口被占就抛 OSError 让进程崩掉——
          而 8080 是最容易被占的端口（另一个调试台、别的开发服务）。
          现在改成：探测到占用就 +1 顺延，并**明确打印**改到了哪个端口。

        语义区分（重要）：
          · explicit=True  —— 用户显式给了 --web-port，仍顺延但打印警告，
                              并说明「如要固定请先释放该端口」。
          · explicit=False —— 用的是默认 8080，顺延属预期行为，只提示一句。

        为什么显式指定也顺延而不是报错：单机工具链（singleplayer 会拉起本进程）
        更怕「起不来」，不怕「换了个端口」——日志里写清楚了，用户能找到。
        """
        want = int(want)
        if want <= 0:
            # 0 = 让 OS 挑，天然无冲突
            self._port_probe_log = []
            return 0, False, 'OS 自动分配'
        # 用 M4Server._port_free 而不是 self._port_free：本方法会被
        # MultiServer 实例借用（见 MultiServer._start_web），那边没有该方法。
        _free = M4Server._port_free
        # ★ 记录探测过程（2026-10-20）：只报结果不够，排查时要能看出
        #   「试过哪些端口、各自什么状态」。失败时把整张表打出来。
        #   格式：[(port, 'free'|'busy'), ...]
        probed = []
        self._port_probe_log = probed
        if _free(want):
            probed.append((want, 'free'))
            return want, False, ''
        probed.append((want, 'busy'))
        for i in range(1, tries + 1):
            cand = want + i
            if cand > 65535:
                probed.append((cand, 'overflow'))
                break
            if _free(cand):
                probed.append((cand, 'free'))
                return cand, True, '端口 %d 已被占用，自动顺延到 %d' % (want, cand)
            probed.append((cand, 'busy'))
        return want, False, '端口 %d 及后续 %d 个端口都不可用' % (want, tries)

    def _format_probe_log(self):
        """把探测过的端口列表格式化成可读多行（排查用）。

        为什么需要：只报「顺延到 8081」看不出为什么不是 8080——
        可能 8080 被占、也可能探测本身有问题。把每个端口的状态列出来，
        用户一眼能判断是「端口被占」还是「探测逻辑坏了」。
        """
        probed = getattr(self, '_port_probe_log', None) or []
        if not probed:
            return []
        busy = [p for p, s in probed if s == 'busy']
        free = [p for p, s in probed if s == 'free']
        out = ['   已检查 %d 个端口：' % len(probed)]
        # 连续区间压缩，避免 20 个端口刷 20 行
        def _rng(nums):
            if not nums:
                return '无'
            segs, i = [], 0
            while i < len(nums):
                j = i
                while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                    j += 1
                segs.append(str(nums[i]) if i == j
                            else '%d-%d' % (nums[i], nums[j]))
                i = j + 1
            return ' '.join(segs)
        out.append('     · 占用：%s' % _rng(sorted(busy)))
        if free:
            out.append('     · 可用：%s' % _rng(sorted(free)))
        return out


    def _start_web(self, port, explicit=False):
        # 单机架构：只绑回环，不监听 0.0.0.0（GM 面板是调试面，不应暴露到局域网）
        want = port
        GM_WEB_STATE.update(enabled=True, reason='starting', want=want,
                            port=0, bumped=False, message='正在启动')
        try:
            port, bumped, reason = self._pick_web_port(port, explicit=explicit)
        except Exception as e:                     # noqa: BLE001
            # 探测阶段抛异常（极罕见，但不该让进程崩）
            self.log('   ✗ GM 面板启动失败（探测端口 %s）：%r' % (want, e))
            GM_WEB_STATE.update(reason='error', port=0,
                                message='探测端口异常：%r' % e)
            self._web_port = 0
            return False
        if bumped:
            if explicit:
                self.log('   ⚠ --web-port %d 已被占用，本次改用 %d'
                         '（要固定请先释放 %d）' % (want, port, want))
            else:
                self.log('   · 默认端口 %d 被占用，GM 面板改用 %d' % (want, port))
        elif reason and want > 0 and port <= 0:
            # reason 非空、但「原本要固定端口却探测不到可用」→ 真失败。
            # ★ want > 0 这个条件是必须的：port=0 表示「交给 OS 分配」，
            #   此时 reason='OS 自动分配' 是**正常**路径，不是失败。
            #   （首版漏了这个判断，web_port=0 被误报成 failed。）
            self.log('   ✗ GM 面板启动失败：%s' % reason)
            for _l in self._format_probe_log():
                self.log(_l)
            GM_WEB_STATE.update(reason='failed', port=0, bumped=False,
                                message=reason,
                                probed=[{'port': _p, 'state': _s} for _p, _s
                                        in (getattr(self, '_port_probe_log', []) or [])])
            self._web_port = 0
            return False
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), _GMHandler)
        except OSError as e:
            # 探测与绑定之间可能被抢（race），这时**不能**让进程崩
            self.log('   ✗ GM 面板启动失败（%s）：%r' % (port, e))
            # 探测说可用、绑定却失败 = race（探测与绑定之间被抢）
            for _l in self._format_probe_log():
                self.log(_l)
            self.log('   注：探测显示可用但绑定失败，通常是探测与绑定之间被抢占')
            GM_WEB_STATE.update(reason='failed', port=0, bumped=bumped,
                                message='绑定 %d 失败：%r' % (port, e),
                                probed=[{'port': _p, 'state': _s} for _p, _s
                                        in (getattr(self, '_port_probe_log', []) or [])])
            self._web_port = 0
            return False
        except Exception as e:                     # noqa: BLE001
            self.log('   ✗ GM 面板启动失败（%s，非 OSError）：%r' % (port, e))
            GM_WEB_STATE.update(reason='error', port=0, bumped=bumped,
                                message='启动异常：%r' % e)
            self._web_port = 0
            return False
        httpd.m4 = self
        # ★ 回填实际端口（2026-10-20）：port=0 时由 OS 分配，
        #   不写回 self._web_port 的话，调用方永远不知道面板在哪。
        #   旧实现只把入参打进日志，传 0 就打印「http://127.0.0.1:0/」。
        self._web_port = httpd.server_address[1]
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        GM_WEB_STATE.update(reason='ready', port=self._web_port,
                            bumped=bool(bumped),
                            probed=[{'port': _p, 'state': _s} for _p, _s
                                    in (getattr(self, '_port_probe_log', []) or [])],
                            message='已启动 http://127.0.0.1:%d/' % self._web_port)
        self.log('   🌐 GM 面板已启动：http://127.0.0.1:%d/' % self._web_port)
        return True

    # ── 后台写线程的启停（2026-10-22 存档异步化）──
    def _ensure_save_writer(self):
        """按需起后台写线程（幂等；异步关闭时不启动）。"""
        if not self._save_async or self._save_thread is not None:
            return
        import queue as _queue
        if self._save_queue is None:
            self._save_queue = _queue.Queue()
        self._save_thread = threading.Thread(target=self._save_writer, daemon=True,
                                             name='m4-save-writer')
        self._save_thread.start()

    def _shutdown_save_writer(self, timeout=5.0):
        """停机前收口：等写完已排队的快照，再停写线程。"""
        if not self._save_async:
            return True
        if self._save_queue is None or self._save_thread is None \
                or not self._save_thread.is_alive():
            self._save_queue = None
            self._save_thread = None
            return True
        self.flush_saves(timeout)
        if self._save_queue is not None:
            self._save_queue.put(None)          # 停止哨兵
        t = self._save_thread
        if t is not None and t.is_alive():
            t.join(timeout)
        self._save_queue = None
        self._save_thread = None
        return True

    def serve_background(self, port=0, web=False, web_port=8080,
                         explicit_web_port=False):
        """在守护线程内启动 accept 循环，返回实际监听端口（单机宿专用入口）。

        port=0 → 由内核挑选空闲端口，客户端配置按返回的真实端口生成，
        彻底消除「3134 被占用 → 客户端连不上」这一类单机故障。
        """
        self.port = port
        self._web = bool(web)
        self._web_port = web_port
        # 记住「端口是用户显式给的还是默认的」：影响冲突时的提示语气，
        # 也影响 _start_web 的行为（两者都顺延，但显式给会打警告）。
        self._web_port_explicit = bool(explicit_web_port)
        self._ready.clear()
        threading.Thread(target=self.run, daemon=True,
                         name='m4-kernel').start()
        if not self._ready.wait(5.0):
            raise RuntimeError('游戏内核监听未就绪（5s 超时）')
        return self.port

    def run(self):
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        # port=0 时回填操作系统实际分配的端口
        self.port = ls.getsockname()[1]
        ls.listen(2)
        self._listener = ls
        # ★ 注册进程退出钩子（2026-10-20）：run() 是死循环，此前只能靠 daemon
        #   线程随进程被强杀。Windows 上这会以 0xC0000409 退出（看着像崩溃，
        #   实为「带 socket 的线程被硬杀」）。atexit 里先 stop() 关掉监听，
        #   阻塞的 accept() 立即抛 OSError 退出循环，线程干净收尾。
        #   用 try/except 包住：解释器关闭阶段模块可能已部分销毁。
        if not getattr(self, '_atexit_armed', False):
            self._atexit_armed = True
            # ★ 必须用 threading._register_atexit，不能用 atexit.register（2026-10-20）。
            #   实测（30 次 × 多轮）：
            #     atexit.register         → 27% 概率 0xC0000409
            #     threading._register_atexit → 0/20
            #   区别在**执行时机**：普通 atexit 在解释器 finalizing 阶段跑，
            #   此时 daemon 线程仍在活动、stdout 已进入关闭流程，任何 print 都可能
            #   触发 _enter_buffered_busy；而 threading._register_atexit 的回调在
            #   **daemon 线程被 join 之前**执行，此时置位 _SHUTTING_DOWN 还能拦住
            #   后续所有 log() 的 stdout 写入。
            #   Python 3.9+ 才有该 API；缺失时退回普通 atexit（老环境仍能跑，
            #   只是崩溃概率回到旧水平）。
            _reg = getattr(threading, '_register_atexit', None)
            try:
                if _reg is not None:
                    _reg(self._safe_stop)
                else:
                    import atexit as _atexit
                    _atexit.register(self._safe_stop)
            except Exception:              # noqa: BLE001
                pass
        self._ready.set()
        self.log('模拟服 v4 %s:%d（seed=0x%08X，每 %d 次移动遇怪，回合超时 %ds，速度序结算）'
                 % (self.host, self.port, self.seed, self.encounter_every,
                    self.turn_timeout))
        self._log_dmg_banner()
        if self._web:
            self._start_web(self._web_port,
                            explicit=getattr(self, '_web_port_explicit', False))
        while not getattr(self, '_stop', False):
            try:
                conn, addr = ls.accept()
            except KeyboardInterrupt:
                break
            except OSError:
                # ★ 监听 socket 被 shutdown() 关闭时会抛 OSError（2026-10-20）。
                #   这不是故障，是 stop() 的正常退出路径。
                break
            # 单机架构：单角色单会话。已有连接在跑时，后续连接直接拒绝，
            # 避免第二个客户端把 self._conn / in_war / enemy_hp 等单例状态覆盖掉。
            if self._busy:
                self.log('── 拒绝并发连接 %s（单机模式：同一时刻只允许一个客户端）'
                         % (addr,))
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self.log('── 接入: %s' % (addr,))
            self._busy = True
            try:
                # ★ 合批层包在**抓包器下面**（约束 2）：tap 装在 M4Server.handle 上，
                #   它包的是这里传进去的 conn。若反过来（合批在外），一次 sendall
                #   就是 N 帧，调试台的「丢弃下一帧」会从丢 1 帧变成丢 N 帧。
                if FRAME_BATCH:
                    conn = FrameBatcher(conn)
                self._batcher = conn if isinstance(conn, FrameBatcher) else None
                self._ensure_save_writer()      # 首次接入时才起写线程（不起空转线程）
                self.handle(conn)
            except Exception as e:  # noqa: BLE001
                self.log('异常: %r' % e)
            finally:
                self._busy = False
                if self._batcher is not None:
                    try:
                        self._batcher.flush()
                    except Exception:                       # noqa: BLE001
                        pass
                    self._last_batch_stats = self._batcher.batch_stats()
                self._batcher = None
                # ★ 断线收口：异步落盘的最后一份快照必须写完再放连接走，
                #   否则「下线前那一下」的进度就丢在队列里了。
                if self._save_async:
                    self.flush_saves(2.0)
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            ls.close()
        except OSError:
            pass

    def _safe_stop(self):
        """atexit 钩子：退出时静默停掉监听（绝不抛异常）。

        ★ 顺序很重要（2026-10-20）：先置 _SHUTTING_DOWN 再 stop()。
          stop() 会关监听 socket，正在 handle() 的线程随之收尾并调 log()——
          若此时还允许写 stdout，就会与解释器关闭争锁，触发
          _enter_buffered_busy 致命错误。先置位等于「静音后再关门」。
        """
        global _SHUTTING_DOWN
        _SHUTTING_DOWN = True
        try:
            self.stop()
        except Exception:                  # noqa: BLE001
            pass

    def stop(self):
        """优雅停止 accept 循环（2026-10-20）。

        ★ 为什么需要：run() 是 `while True` 死循环，此前**没有任何退出路径**——
          测试脚本把 run() 放进 daemon 线程，主线程一结束解释器就直接强杀该线程。
          Windows 上这表现为进程以 0xC0000409（STATUS_STACK_BUFFER_OVERRUN）退出，
          看起来像崩溃，实际是「带 socket 的线程被硬杀」。实测全量回归里偶发 1/10。

          做法：置 _stop 标志 + shutdown 监听 socket（让阻塞的 accept() 立即抛 OSError）。
          不在这里 join 线程——调用方（测试/宿主）自己决定等多久。
        """
        self._stop = True
        # ★ 先关活动连接（2026-10-20）：handle() 线程可能正阻塞在 conn.recv()，
        #   只关监听 socket 唤不醒它（它等的不是 accept）。关掉 conn 会让 recv
        #   立即抛 OSError，线程随即走完 handle 的收尾——包括那些 self.log()。
        #   收尾时的 log 已由 _SHUTTING_DOWN 静音，不会再争 stdout 锁。
        c = getattr(self, '_conn', None)
        if c is not None:
            try:
                c.close()
            except OSError:
                pass
        ls = getattr(self, '_listener', None)
        if ls is not None:
            # ★ Windows 上 shutdown() 对**监听** socket 不一定唤醒阻塞中的 accept()
            #   （实测 stop 后线程仍 alive）。可靠做法是 close()：它会让 accept()
            #   立即抛 OSError(10038 WSAENOTSOCK)，run() 里的 except OSError 接住退出。
            try:
                ls.close()
            except OSError:
                pass
        # ★ 最后收口存档（2026-10-22 异步化）：_stop 置位后 handle() 才会退出，
        #   它退出时自己会 flush 一次；这里再等一次是双保险——进程退出前
        #   绝不允许「最新进度还压在写队列里」。
        self._shutdown_save_writer()

    def handle(self, conn):
        self._conn = conn
        # ★ 不要在这里把 _batcher 置 None：run() 是在**调 handle 之前**装配好批处理器的
        #   （见 run() 的 `conn = FrameBatcher(conn)`）。这里无条件清零会把刚装好的
        #   合批器抹掉，表现为「帧批统计 enabled=False、合并率 0」——
        #   而帧序完全正常，所以只在看统计时才发现，极易漏掉。
        #   只认「当前 conn 自己就是批处理器」时才更新；直调 handle（测试/内存对）时留空。
        if not isinstance(getattr(conn, '_conn', None), FrameBatcher) \
                and not isinstance(conn, FrameBatcher):
            self._batcher = None
        # 加密模式为「进程级全局」（同一份客户端 DLL 要么打过补丁、要么没有）；
        # 此处按 self.encrypt 一次性定死，握手前后不再翻转，避免多会话下
        # 「A 会话正在 make_s2c、B 会话刚复位标志」的竞态。
        set_crypto_mode(self.encrypt)
        time.sleep(0.05)
        # Hello 帧在客户端 SetSeed 之前读取：必须不压缩（compress=False）且不加密
        # （此刻尚未包 CryptoConn，天然明文）。
        conn.sendall(make_s2c(OP_HELLO, struct.pack('<I', self.seed) + struct.pack('<H', SERVER_NUM),
                              compress=False))
        self.log('① S2C Hello 已发')
        try:
            resp = recv_exact(conn, 25, 6)
        except (ConnectionError, socket.timeout):
            self.log('② 握手未完成')
            return
        ok = resp[:3] == hello_response(self.seed)[:3] and resp[3:] == b'\x00' * 22
        self.log('② Hello %s（%s）' % ('PASS ✓' if ok else 'FAIL ✗', resp[:3].hex(' ')))
        if not ok:
            return
        # ③ 握手完成后包上加密（原版 mxmain.dll：+0x72=1）。
        #    客户端在收到 Hello、回完 25B 后调用 SetSeed 开启加密；服务端在
        #    同一时刻用相同 seed 建对称流，并把后续收发包进 CryptoConn：
        #      · sendall → RX 方向密钥流加密（客户端用 RX 流解密）
        #      · recv    → TX 方向密钥流解密（客户端用 TX 流加密）
        #    S2C 帧本身已在 make_s2c 里按 _CRYPTO_MODE 做 LZSS 压缩。
        if self.encrypt:
            if mx_crypto is None or CryptoConn is None:
                self.log('   ⚠ 请求加密但缺 mx_crypto / CryptoConn，回退明文')
                set_crypto_mode(False)
            else:
                try:
                    cipher = mx_crypto.RealCipher(self.seed)
                except KeyError as e:
                    self.log('   ⚠ seed 0x%08X 未收录，加密不可用，回退明文：%r'
                             % (self.seed, e))
                    set_crypto_mode(False)
                else:
                    # ★ 深一层包装（2026-10-21）：本 conn 可能是调试台挂进来的
                    #   TappedConn（鸭子类型，特征 _conn/_tap）。加密要包在
                    #   TappedConn **里面**（socket 级），否则抓包器拿到的是密文：
                    #   帧视图/世界模型/覆盖统计/丢帧/注入在加密模式下全部失明。
                    #   包进内层后，TappedConn 收发的都是明文帧（S2C 为压缩帧，
                    #   由抓包器做 LZSS 往返校验后解压展示）。
                    inner = getattr(conn, '_conn', None)
                    if inner is not None and hasattr(conn, '_tap'):
                        conn._conn = CryptoConn(inner, cipher)
                    else:
                        conn = CryptoConn(conn, cipher)
                    self.log('   🔐 加密已启用（seed=0x%08X）' % self.seed)
        self.role_listed = False
        # 新连接：上一会话可能在「等宠物指令」中途断开，运行态必须整份清掉，
        # 否则残留的 _pending_pet_suffix / _cmd_consumed 会把新会话的第一回合搅乱。
        self._reset_war_state()
        # 位置/计数：仅「无存档的全新角色」才落到出生点；有存档则沿用上次进度
        if not self._loaded:
            self.move_count = 0
            self.px, self.py = SPAWN_X, SPAWN_Y
            self.mapid = START_MAP_ID
        conn.settimeout(60)
        # ★ 循环也响应 _stop（2026-10-20）：
        #   旧实现 while True 且非战斗时 timeout=300——客户端一断开、进程要退出时，
        #   本线程还卡在 recv 里最长 300s，只能被 daemon 强杀。
        #   强杀时若正卡在 print()，会与解释器关闭争 stdout 锁，触发
        #   _enter_buffered_busy 致命错误（进程 0xC0000409，测试其实已 PASS）。
        while not getattr(self, '_stop', False):
            # 活力独立恢复：每次循环（收到上行包 / 超时）都按真实在线时长回一点。
            # 不挂在战斗上——避免「每场战斗回满」被误当成原服规则。
            self._regen_huoli()
            # 战斗指令窗口内按「剩余时间」收包；窗口超时 → 默认普攻
            # （对齐客户端 warui.ShowTimer(30) 倒计时与 DefaultAttack 超时路径）
            if self.in_war and self.await_cmd:
                timeout = max(0.2, self.cmd_deadline - time.time())
            else:
                timeout = 300
            try:
                op, body = recv_c2s(conn, timeout)
            except socket.timeout:
                if (self.in_war and self.await_cmd
                        and time.time() >= self.cmd_deadline - 0.05):
                    self.log('   ⏱ %ds 未下达指令 → 超时默认行动'
                             % self.turn_timeout)
                    self._timeout_default_action(conn)
                continue
            except ConnectionError:
                self.log('   （连接结束）')
                self._persist()
                self._conn = None
                break
            self.log('③ C2S opcode=%d body=%dB: %s' % (op, len(body), body[:40].hex(' ')))

            # ★ 窗口闸口改由战斗指令处理器在 _accept_turn() 内自行负责：只有当
            #   in_war 且 await_cmd 且本窗口未消费时才生效，越窗/重发的指令被处理器
            #   忽略，绝不在此预先翻牌（否则会和第 10239 起的合法性闸门冲突）。
            #   下面只保留「主角指令抢先于宠物菜单到达」的兜底：宠物按默认普攻结算，
            #   随后该主角指令作为下一回合行动由处理器 _accept_turn 接管。
            if (self.in_war and self._pending_pet_suffix is not None
                    and op in BATTLE_CMD_OPS and op not in C2S_WAR_SUMM_ACTIONS):
                self.log('   ⚠ 新战斗指令先于宠物指令到达 → 宠物按默认普攻结算')
                self._finish_pet_turn(conn)

            # ── 心跳 ──
            if op == C2S_PING:
                conn.sendall(make_s2c(250))   # 250 = C2GS_PING 回显
                continue

            # ── 分派 ──
            # 一条上行指令交给 _dispatch()；顺序契约（谁必须排在战斗保底之前）
            # 写在 DISPATCH_PRE_WAR_OPS 里，由 tests/_test_dispatch.py 守着。
            #
            # ★ 整条分派在会话锁内（2026-10-20）：_dispatch 会连发多帧
            #   （进世界 45 帧、切图整包、战斗演出链），而 GM HTTP 线程与调试台
            #   心跳线程也在往同一个 socket 发文。锁是**双向**的——只有 GM 侧
            #   加锁而内核不加，帧序照样交错（实测交错 8 次，与不加锁相同）。
            #   用 RLock 是因为 _dispatch → _hero_attack → _resolve_hero_round
            #   这条链上会再次取锁（超时默认普攻路径），普通 Lock 会自锁死。
            with self._lock:
                self._dispatch(conn, op, body)
            # ★ 帧批合并的收口点：一条上行指令回的所有帧，到此一次性写出。
            #   必须放在**锁内**吗？不 —— 放在锁外更安全：此时分派已结束，
            #   GM 线程抢到锁也不会与本批交错（批里已经没有未发的帧了）。
            self._flush_out()


    def _dispatch(self, conn, op, body):
        """按 opcode 把一条上行指令分给对应处理逻辑（顺序即契约）。

        **插入位置比代码本身更重要**：这串 if/elif 从上到下第一个命中即生效，
        末尾 `elif self.in_war:` 是战斗保底（回一帧 WarTurn 推回合）。
        客户端在战斗中也会发 OPENUI / COMMON / 道具 / 养成类指令，这些必须排在
        保底之前，否则会凭空推进回合。该清单见 `DISPATCH_PRE_WAR_OPS`，
        `tests/_test_dispatch.py` 会逐条在战斗态下验证「没落进保底」。

        返回 True 表示「这条已归某个分支」（正常处理或显式忽略都算）。
        分支体是从原 handle() 里原样搬出来的，只做了两处机械改写：
          · 缩进 -4；
          · 3 处 `continue`（语义是「本帧处理完，收下一帧」）→ `return True`。
        """
        # ★ 整条分派自成临界区（2026-10-20）。
        #   放在这里而不是 handle 里，是为了让**任何**调用方都自动受保护：
        #   handle 主循环、GM 面板 gm_war_start/gm_war_win/gm_goto_map、
        #   以及测试直调 _dispatch。旧版只有 GM 侧加锁、内核侧没有，
        #   实测两个线程并发写同一 socket，帧序交错 8 次（不报错，只是客户端按
        #   错误顺序解析 opcode）。
        #   RLock 可重入：本方法内部多处会再取锁（_persist / _goto_map /
        #   超时默认普攻路径），普通 Lock 会自锁死。
        with self._lock:
            return self._dispatch_locked(conn, op, body)

    def _dispatch_locked(self, conn, op, body):
        """_dispatch 的锁内实现。调用方必须已持有 self._lock。"""
        if op == 1 and len(body) >= 56:
            name = body[4:36].rstrip(b'\x00').decode('gbk', 'replace')
            self.log('   >>> 登录: %s' % name)
            # 多角色：账号到这里才知道，据此切到该角色的存档（见 _bind_account）
            if name and name != self.account:
                self._bind_account(name)
            if self.have_role:
                self._send_role_list(conn)
            else:
                conn.sendall(build_login_account_info(0))
        elif op == 3 and len(body) >= 24:
            nm = body[0:20].rstrip(b'\x00').decode('gbk', 'replace')
            # C2SLoginPlayerAdd：name(20) icon(1) color(2) major(1)（netlogin.py:100）
            icon, color, major = body[20], body[21] | (body[22] << 8), body[23]
            self.hero_shape = ROLE_SHAPE_BY_ICON_MAJOR.get((icon, major),
                                                            DEFAULT_HERO_SHAPE)
            self.log('   >>> 建角: %s（icon=%d color=%d major=%d → 人物造型 %d）'
                     % (nm, icon, color, major, self.hero_shape))
            self.hero_name = nm
            self.have_role = True
            conn.sendall(make_s2c(OP_PLAYER_ADD))
            self._send_role_list(conn)
        elif op == 5 and len(body) >= 1 and body[0] == 16:
            if not self.role_listed and self.have_role:
                self._send_role_list(conn)
            else:
                conn.sendall(make_s2c(OP_ACCOUNT_MANAGE, struct.pack('<BB', 15, 0)))
        elif op == 6 and len(body) >= 5:
            conn.sendall(make_s2c(OP_BANDWORD, struct.pack('<BBI', body[0], 0, 0) + b'\x00'))

        # ── 选角进世界 ──
        elif op == 2 and len(body) >= 4:
            self.hero_id = struct.unpack('<I', body[:4])[0]
            self.log('   >>> 选角进入 id=%d name=%s' % (self.hero_id, self.hero_name))
            self._enter_world(conn)

        # ── 移动（遇怪触发点）──
        elif op == C2S_MAP_MOVE and len(body) >= 5:
            x = struct.unpack('<H', body[0:2])[0]      # 起点（客户端当前服务器坐标）
            y = struct.unpack('<H', body[2:4])[0]
            trace_len = body[4] if len(body) > 4 else 0
            trace = body[5:5 + trace_len]
            # 终点 = 起点 + trace 各方向增量（对齐 nethero.C2SMapMove 的 _offset 表）
            ex, ey = x, y
            for d in trace:
                if 1 <= d <= 8:
                    ox, oy = MOVE_OFFSETS[d - 1]
                    ex += ox
                    ey += oy
            if self.in_war:
                return True
            self.px, self.py = ex, ey
            self.move_count += 1
            # ★ 回弹修复：S2CMapTrack 必须回「起点 + 有符号增量」，
            #   否则客户端把角色拉回原地（见 build_map_track 注释）。
            conn.sendall(build_map_track(self.hero_id, x, y, ex - x, ey - y, 0))
            self.log('   >>> 移动 (%d,%d)→(%d,%d) [第 %d 次]'
                     % (x, y, ex, ey, self.move_count))
            if self.move_count % 10 == 0:   # 每 10 步落盘一次，避免频繁写
                self._persist()
            # 走到地图出口 → 自动传送（坐标取自 minimap_data 真实出口标记）
            dest = self._check_exit()
            if dest is not None:
                self._goto_map(conn, dest)
                return True
            if (self.encounter_every > 0 and self.move_count % self.encounter_every == 0
                    and not self.in_war and self._encounter_allowed_here()):
                # 客户端每次 S2CWarStart 会让 g_WarTimes+1、WarEnd 会 -1，
                # 且 `g_WarTimes == 0` 时**直接忽略** WarEnd。重复发 WarStart 会让
                # 计数累积，导致后续 WarEnd 被丢弃、战斗无法结束（卡「请等待」）。
                self._start_war(conn)

        # ── 养成：加点 / 升级 / 学招式（M9）──
        # 放在 NPC 段之前、战斗保底之前：属性面板与技能面板在战斗中不锁，
        # 落进保底会被当成「未做逻辑的战斗指令」推一帧 WarTurn 出去。
        elif op == C2S_PROP_LEVELUP:
            self._hero_level_up(conn)
        elif op == C2S_PROP_POINT and len(body) >= 5:
            self._hero_add_points(conn, body)
        elif op == C2S_NPC_LEARN_SKILL and len(body) >= 2:
            _skid = struct.unpack('<H', body[:2])[0]
            self.log('   >>> 学招式 skid=%d' % _skid)
            self._hero_learn_magic(conn, _skid)

        # ── NPC 点击 / 对话选项 ──
        elif op == C2S_NPC_LOOK and len(body) >= 4:
            npcid = struct.unpack('<I', body[:4])[0]
            if npcid not in self._visible_npc_ids():
                self.log('   ⚠ 忽略当前地图不存在的 NPC id=%d' % npcid)
                return True
            self.cur_npc = npcid
            self._quest_prompt = None          # 换 NPC 即作废上一次待确认动作
            nname = self._npc_name(npcid)
            self.log('   >>> 点击 NPC id=%d' % npcid)
            travel = TRAVEL_NPCS.get(self.mapid, {})
            if npcid in travel or nname in TRAVEL_NPC_NAMES:
                # 传送使 / 随身 NPC（车夫·船夫·灵风塔守护人…）：给出目的地选项。
                # 随身 NPC 只列它 pathdata 路线里的目标；无路线信息时退化为全邻接菜单。
                dests = travel.get(npcid) or MAP_LINKS.get(self.mapid, [])
                self.cur_npc_menu = dests
                conn.sendall(build_npc_chat_ext(nname, self._travel_msg(dests)))
            elif npcid == TEACHER_NPC_ID:
                # 战斗技能师：选项 1 看课表（149），2 离开
                conn.sendall(build_npc_chat_ext(
                    TEACHER_NPC_NAME,
                    'P\n想学招式就找我，学费按等级算。\nQ看看能学什么。\nQ没什么事。'))
            elif npcid == NPC_ID:
                conn.sendall(build_npc_chat_ext(NPC_NAME, self._npc_welcome_msg()))
            elif self._npc_quest_dialog(conn, npcid):
                pass
            else:
                nname = self._npc_name(npcid)
                conn.sendall(build_npc_chat_ext(nname, 'P\n你好，我是%s。' % nname))
        elif op == C2S_NPC_ANSWER and len(body) >= 4:
            choice = struct.unpack('<I', body[:4])[0]
            self.log('   >>> NPC 选项 = %d' % choice)
            # 主线剧情：上一条对话若登记了待确认动作，优先吃掉这次选项
            if self._quest_answer(conn, choice):
                return True
            if (self.cur_npc is not None
                    and self._npc_name(self.cur_npc) in TRAVEL_NPC_NAMES):
                # 传送使 / 随身 NPC：选项序号 → 目的地（菜单可能是该 NPC 的路线子集）
                self._teleport_choice(conn, choice, self.cur_npc_menu)
            elif self.cur_npc == TEACHER_NPC_ID and choice == 1:
                # 「看看能学什么」→ 下发教招式清单，客户端直接弹面板
                self._send_learn_list(conn)
            elif self.cur_npc == NPC_ID and choice == 2:
                # 「我想买点东西」→ 打开商店
                conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n看看想要点什么？'))
                self._send_shop_list(conn)
            elif self.cur_npc == NPC_ID and choice == 3:
                # 「我想卖点东西」→ 下发收购清单（148），客户端弹卖出面板
                conn.sendall(build_npc_chat_ext(NPC_NAME, 'P\n有什么要卖的？我按市价收。'))
                self._send_sell_list(conn)
            elif self.cur_npc == NPC_ID and choice == 1:
                # 选项 1 = 任务相关：未接→接任务 / 进行中→进度提示 / 已达成→交任务
                self._npc_task_choice(conn)
            else:
                nname = self._npc_name(self.cur_npc) if self.cur_npc is not None else NPC_NAME
                conn.sendall(build_npc_chat_ext(nname, NPC_CHAT_BYE))
        elif op == C2S_NPC_BUY and len(body) >= 4:
            index, amount = struct.unpack('<HH', body[:4])
            self.log('   >>> NPC 购买 index=%d amount=%d' % (index, amount))
            if self.cur_npc == NPC_ID:
                self._handle_npc_buy(conn, index, amount)
            else:
                self.log('   ⚠ 当前 NPC 不提供购买服务，忽略购买请求')
        elif op == C2S_NPC_SELL and len(body) >= 2:
            self.log('   >>> NPC 卖出 flag=%d len=%d' % (body[0], len(body)))
            if self.cur_npc == NPC_ID:
                self._handle_npc_sell(conn, body)
            else:
                self.log('   ⚠ 当前 NPC 不收购物品，忽略卖出请求')

        # ── 鼠标导航（小地图图面点击）→ netchat.C2SPlayerWizard 'goto scene,x,y' ──
        # 必须排在 in_war 保底之前：战斗中同样可能点到（且落保底会被当成敌方回合）。
        elif op == C2S_PLAYER_WIZARD and body:
            txt = body.rstrip(b'\x00').decode('gbk', 'replace')
            i = txt.find('goto ')
            if i < 0:
                self.log('   >>> 巫师指令（忽略）：%r' % txt[:32])
                return True
            arg = txt[i + 5:].split(',')
            try:
                scene, wx, wy = int(arg[0]), int(arg[1]), int(arg[2])
            except (ValueError, IndexError):
                self.log('   ⚠ 巫师 goto 参数非法：%r' % txt[:32])
                return True
            self.log('   >>> 鼠标导航 goto scene=%d x=%d y=%d' % (scene, wx, wy))
            self._wizard_goto(conn, scene, wx, wy)

        # ── NPC 任务交互 / 物品详情（netnpc.C2SNpcTask / C2SNpcGetDetails）──
        elif op == C2S_NPC_TASK and len(body) >= 2:
            # netnpc.C2SNpcTask(taskid(2)[, respond(1)])：NPC 对话里的任务交互
            # （dlgnpc 点任务链 :359 发 msgid-2000；npc.py:287 带 m_Sequence 应答）。
            # 已接取/已达成 → 重发 S2CTaskAdd（客户端同 tid 先 RemoveTask 再重建，
            # 进度随之刷新）；未接取不造数据，避免客户端弹出无中生有的任务。
            taskid = struct.unpack('<H', body[:2])[0]
            respond = body[2] if len(body) >= 3 else 0
            self.log('   >>> NPC 任务交互 tid=%d respond=%d' % (taskid, respond))
            if self._task_state(taskid):
                self._send_task_add(conn, taskid)
                self.log('   >>> 回 S2CTaskAdd(72)（%s）' % self._task_desc(taskid))
            else:
                self.log('   （任务 %d 未接取，不应答）' % taskid)
        elif op == C2S_NPC_GETDETAILS and len(body) >= 4:
            # netnpc.C2SNpcGetDetails(pos(4))：商店/收购面板悬停某格要「物品详情」
            # （dlggetinfo.py:795 / dlgchildinfo.py:497 用面板内格位 idx 调用）。
            # 应答 S2CNpcDetails(151)：pos(1) + desc(变长) → dlgtrade.UpdateDetails。
            pos_ = struct.unpack('<I', body[:4])[0]
            if 0 <= pos_ < len(SHOP_ITEMS):
                shape, _n, price, _q = SHOP_ITEMS[pos_]
                desc = '%s：售价 %d 金钱。' % (item_name_of(shape), price)
            else:
                desc = '（没有这个商品。）'
            conn.sendall(build_npc_details(pos_, desc))
            self.log('   >>> 物品详情 pos=%d → S2CNpcDetails(151) %r' % (pos_, desc))
        elif op == C2S_NPC_GETCOST and len(body) >= 5:
            # netnpc.C2SGetMakeCost(itype, ids)：flag(1) + ids(N×4B)。
            #   客户端按材料件数改写 flag：2 件 → flag=itype，3 件 → 6，4 件 → 8。
            # 应答 S2CNpcMakeCost(155)：制作/合成面板据此显示体力/金钱/技能等级。
            # flag 只用于分流配方类别，本服不区分，仅记录。
            flag = body[0]
            n = min((len(body) - 1) // 4, 8)      # 钳制：畸形包不许放大成越界读
            ids = [struct.unpack('<I', body[1 + 4 * i:5 + 4 * i])[0]
                   for i in range(n)]
            # 能查到配方就按 craftdata 的 cost/lv 报，否则退回模拟公式
            product, _key = self._recipe_of(ids)
            info = (CRAFT_DATA.get(product) or {}) if product else {}
            tili = int(info.get('cost') or (MAKE_COST_HUOLI * max(1, n)))
            money = MAKE_COST_MONEY * max(1, n)
            sklv = int(info.get('lv') or MAKE_COST_SKLV)
            conn.sendall(build_npc_make_cost(tili, money, sklv))
            self.log('   >>> 制作费用 flag=%d ids=%s → S2CNpcMakeCost(155)：'
                     '活力 %d 金钱 %d 技能等级 %d'
                     % (flag, ids, tili, money, sklv))
        elif op == C2S_NPC_MAKE and len(body) >= 5:
            self.log('   >>> 制作请求 mode=%d（%d 个材料 id）'
                     % (body[0], (len(body) - 1) // 4))
            self._handle_npc_make(conn, body)

        # ── 炼药（C2S_PLAYER_DRUG = 73）─────────────────────────────────────
        # ★ 2026-10-22 核实：客户端入口**存在**（`dialog/dlgdrug.py` + `net.py:57`），
        #   子指令 = body[0]：0=打开界面(C2SOpenDrugUI)、1=参悟配方(C2SDrugDiscover)、
        #   2=炼制、3=删配方。但本版客户端**没有配方数据表**（检索不到 drugdata.py，
        #   只有 dlgdrug.py 里的 DRUGDIFF 难度表 + IsMaterial 形状区间 21200-21299）。
        #   → 没有「材料 → 药品」的映射就编不出真配方。按用户要求：不确定材料**不自动
        #   替换物品 ID**，所以这里**只识别、不实现**：记录子指令、回 True（不落进战斗
        #   保底），不发任何会误导客户端的 S2CDrug* 帧。FEATURE_STATUS['alchemy']=not_implemented。
        elif op == C2S_PLAYER_DRUG and len(body) >= 1:
            _sub = body[0] if len(body) >= 1 else 0
            _names = {0: '打开界面', 1: '参悟配方', 2: '炼制', 3: '删配方'}
            self.log('   >>> 炼药请求 sub=%d（%s）—— 本服无配方数据，未实现'
                     % (_sub, _names.get(_sub, '未知')))
            self._emit('alchemy.reject', '炼药未开放（本服无配方数据）',
                       sub=_sub)
            # 不发 S2C：客户端会停在等待，但服务端不会崩、也不会误推进战斗/任务。
            return True

        # ── 任务查询 ──
        elif op == C2S_GET_TASK:
            # 任务栏全量重发：新手任务 + 主线（客户端打开任务面板时请求）
            sent = []
            for _tid in sorted(self.tasks):
                if self._task_state(_tid):
                    self._send_task_add(conn, _tid)
                    sent.append('%d:%s' % (_tid, self._task_desc(_tid)))
            if sent:
                self.log('   >>> 回任务列表 S2CTaskAdd×%d（%s）'
                         % (len(sent), '；'.join(sent)))

        # ── 放弃任务（nettask.C2SGiveUpTask：flag(1)+taskid(2)）──
        # 不处理的后果：客户端任务面板点「放弃」后服务端任务态不变，任务栏不消失、
        # 击杀还在暗涨进度——客户端/服务端状态分叉。正确处理 = 删任务 + 回 TaskRemove(73)。
        elif op == C2S_GIVEUP_TASK and len(body) >= 3:
            taskid = struct.unpack('<H', body[1:3])[0]
            self.log('   >>> 放弃任务 tid=%d' % taskid)
            if self._task_state(taskid):
                _before = self._visible_npc_ids()
                _t = self.tasks.get(taskid) or {}
                _qid = _t.get('qid')
                self.tasks.pop(taskid, None)
                # 放弃主线时清掉剧情战斗/待确认动作，否则下一次遇怪会顶着旧上下文
                if getattr(self, '_quest_battle', None) and \
                        int(self._quest_battle.get('qid', 0)) == taskid:
                    self._quest_battle = None
                self._quest_prompt = None
                self._refresh_spawns_if_changed(conn, _before)
                conn.sendall(build_task_remove(taskid))   # S2CTaskRemove(73) → 客户端移除任务栏
                self.log('   >>> 回 S2CTaskRemove(73)（任务 %d 已放弃）' % taskid)
                if _qid in questmod.QUESTS:
                    _q = questmod.quest(_qid)
                    self._emit('quest.giveup', '放弃主线 %d「%s」（第 %s 步）'
                               % (_qid, _q['name'], _t.get('step', 0)),
                               qid=int(_qid), name=_q['name'], step=int(_t.get('step', 0)))
                self._persist()
            else:
                self.log('   （任务 %d 未接取，忽略放弃请求）' % taskid)

        # ── 打开 UI（op88 C2S_OPENUI，战斗内外均可发生）──
        # 必须在战斗指令分支之前：战斗中点击 UI 类按钮同样发本指令，
        # 若落入 in_war 保底回退会被错误地推成敌方回合。
        elif op == C2S_OPENUI and len(body) >= 1:
            sub = body[0]
            if sub == OPENUI_SUB_ADDNPC and len(body) >= 3:
                # nethero.C2SAddNpc(minimap)：minimap(2B)
                minimap = struct.unpack('<H', body[1:3])[0]
                self.log('   >>> 请求小地图 NPC 标记（minimap=%d）' % minimap)
                d = self._map_def()
                if minimap in (self.mapid, d['minimap']):
                    # ★ 鼠标导航：喂给客户端「小地图面板·本地页」可点条目 = 本图 NPC
                    #   的名字 + **本服真实站位**。客户端点条目即寻路过去并交互
                    #   （dlgmap.OnWalkTo → icon.LeftUp），于是点「车夫」就能走到它并弹菜单。
                    _vis = self._visible_npcs()
                    entries = [(nx, ny, nname, '')
                               for (_n, _s, nx, ny, nname) in _vis]
                    conn.sendall(build_minimap_npc(minimap, entries))
                    self.log('   >>> 回 S2CAddMinimapNpc(182)：%d 条（%s）'
                             % (len(entries), '、'.join(n[4] for n in _vis)))
                else:
                    conn.sendall(build_minimap_npc(minimap, []))
                    self.log('   >>> 回 S2CAddMinimapNpc(182)：空（非本地图）')
            elif sub == OPENUI_SUB_JINGXIU:
                iopen = body[1] if len(body) > 1 else -1
                self.log('   >>> 静修/称号面板请求（iopen=%d）' % iopen)
                conn.sendall(build_baozi_title())
                self.log('   >>> 回 S2CShowBaoziTitle(129)')
            elif sub == OPENUI_SUB_PRIVATE:
                # dialog/dlgsysopt 私密页：面板由客户端自己 ShowPrivate 打开，
                # 服务端只需「收到」；资料随后走 op11 sub=2 提交（见 C2S_DUOYITONG）。
                self.log('   >>> 私密资料面板请求（客户端自开，无需应答）')
            else:
                # 未实现应答的 sub，两种情形都不应答（安全）：
                #   · g_Sub2Func[sub] = None（如 109/69）→ 客户端本就无处理器；
                #   · 已映射但需要业务数据（如 4 = C2SGetDayMessage 每日消息、
                #     108 = 积分商店…）→ 本服不造数据，留空不崩即可。
                self.log('   （OPENUI sub=%d 未实现应答，忽略）' % sub)

        # ── 帮派面板（op81 C2S_GROUP）──
        elif op == C2S_GROUP and len(body) >= 1:
            sub = body[0]
            if sub == GROUP_SUB_OPEN:
                self.log('   >>> 打开帮派面板')
                conn.sendall(build_family_open(FAMILY_NAME, self.hero_id))
                self.log('   >>> 回 S2COpenFamily(80)：%s owner=%d'
                         % (FAMILY_NAME, self.hero_id))
            elif sub == GROUP_SUB_MEMBERLIST:
                self.log('   >>> 帮派成员列表请求（面板打开自动触发）')
                conn.sendall(build_family_members(
                    [(self.hero_id, self.hero_name or '你', 1, 1, 1)]))
                self.log('   >>> 回 S2CFamilyMemberList(81)：1 名成员')
            else:
                # GroupFunc 未映射的 sub：客户端无处理器，不应答
                self.log('   （GROUP sub=%d 客户端无处理器，忽略）' % sub)

        # ── 通用通道（op192 C2S_COMMON）──
        # ★ 必须放在战斗保底分支之前：客户端**在战斗中也会**批量上报点击统计
        #   （netcommon.C2SSendLog：sub=1 + n(1) + [logid(1) cnt(2)]×n），
        #   若落入 in_war 保底会被当成未知战斗指令多回一帧 WarTurn。
        elif op == C2S_COMMON and len(body) >= 1:
            sub = body[0]
            if sub == C2S_COMMON_SUB_SENDLOG and len(body) >= 2:
                n = body[1]
                self.log('   >>> 点击统计上报（%d 条 logid/cnt）' % n)
            else:
                self.log('   （C2S_COMMON sub=%d 本服无逻辑，忽略）' % sub)

        # ── 宠物（op128-134，全部按 netpet.py 的 PacketAddI 顺序解）──
        elif op == C2S_SUMM_VIEW and len(body) >= 4:
            pid = struct.unpack('<I', body[:4])[0]
            p = self._pet(pid)
            self.log('   >>> 查看宠物资质 id=%d' % pid)
            if p is not None:
                # 资质面板 = op51（资质五项 + 培养位 + 9 抗性）；技能格随面板一起补发
                conn.sendall(build_pet_prop_view(p['pid'], 0, pet_ability(p), PET_RESIST))
                conn.sendall(build_pet_skill(p['pid'], list(p.get('skills') or []),
                                             int(p.get('hf_from') or 0)))
                self.log('   >>> 回 S2CPropView(51)：id=%d 资质 %s'
                         % (p['pid'], list(pet_ability(p))))
            else:
                self.log('   （未知宠物 id=%d，不应答）' % pid)
        elif op == C2S_SUMM_SET_FIGHT and len(body) >= 4:
            self._set_active_pet(conn, struct.unpack('<I', body[:4])[0])
        elif op == C2S_SUMM_FREE and len(body) >= 4:
            self._pet_free(conn, struct.unpack('<I', body[:4])[0])
        elif op == C2S_SUMM_RENAME:
            # 只要求 pid 在：名字为空的畸形包也要走这条分支（回一条明确日志），
            # 不能因为「长度不够」掉进战斗保底去推一帧 WarTurn。
            if len(body) < 4:
                self.log('   ⚠ 宠物改名包太短（%dB，至少需要 pid4）' % len(body))
            else:
                pid = struct.unpack('<I', body[:4])[0]
                # PacketAddS(name, len(name))：长度写在帧头，body 里就是裸名字字节
                try:
                    name = body[4:].decode('gbk', 'replace')
                except Exception:                      # noqa: BLE001
                    name = ''
                self._pet_rename(conn, pid, name)
        elif op == C2S_SUMM_PUT and len(body) >= 4:
            self._pet_store(conn, struct.unpack('<I', body[:4])[0], True)
        elif op == C2S_SUMM_TAKE and len(body) >= 4:
            self._pet_store(conn, struct.unpack('<I', body[:4])[0], False)
        elif op == C2S_SUMM_ADDPOINT and len(body) >= 9:
            pid = struct.unpack('<I', body[:4])[0]
            self._pet_add_point(conn, pid, list(body[4:9]))
        elif op == C2S_SUMM_ADDPOINT:
            self.log('   ⚠ 加点包长度不足（%dB，应为 pid4+5 项），忽略' % len(body))

        # ── 物品（op32-38）──
        # 放在战斗指令之前：战斗中用道具同样发这些 opcode，不能落入保底回 WarTurn。
        elif op == C2S_ITEM_STRING and len(body) >= 5:
            iid = struct.unpack('<I', body[:4])[0]
            flag = body[4]
            it = self.items.get(iid)
            self.log('   >>> 请求道具附加信息 id=%d flag=%d' % (iid, flag))
            if it is not None:
                conn.sendall(build_item_string(iid, it['infoext']))
                self.log('   >>> 回 S2CItemString(38)：infoext=%r' % it['infoext'])
            else:
                self.log('   （道具 id=%d 不在背包，不应答）' % iid)
        elif op == C2S_ITEM_GET and len(body) >= 4:
            iid = struct.unpack('<I', body[:4])[0]
            it = self.items.get(iid)
            self.log('   >>> 请求道具详情 id=%d' % iid)
            if it is not None:
                conn.sendall(build_item_string(iid, it['infoext']))
        elif op == C2S_ITEM_DRAG and len(body) >= 6:
            iid = struct.unpack('<I', body[:4])[0]
            source, dest = body[4], body[5]
            it = self.items.get(iid)
            self.log('   >>> 拖动道具 id=%d source=%d dest=%d' % (iid, source, dest))
            valid_dest = (0 <= dest <= 9
                          or ITEM_POS_BAG_START <= dest < ITEM_POS_BAG_END
                          or ITEM_POS_FIGHT_START <= dest < ITEM_POS_FIGHT_START + 10)
            # ★ P3-01：往装备位(1..9)拖时校验「是不是装备 / 槽位对不对 / 等级够不够」。
            #   判定是纯函数（equipment_rules.check_equip），**失败不动任何状态**：
            #   不换位、不落盘，只把当前位置回给客户端，让它把图标弹回去。
            #   以前这里是纯位置搬运，头盔能拖进鞋位、1 级能穿 80 级装备。
            equip_ok, equip_why = True, ''
            if it is not None and 1 <= dest <= 9:
                equip_ok, _reason, equip_why = eqr.check_equip(
                    it['shape'], dest, self.grade)
                if not equip_ok:
                    self.log('   ⚠ 装备被拒(%s)：%s' % (_reason, equip_why))
                    conn.sendall(build_item_pos(ITEM_WTYPE_NORMAL, iid, it['pos']))
            if it is not None and it.get('pos') == source and valid_dest and equip_ok:
                occupied = next((other for other_id, other in self.items.items()
                                 if other_id != iid and other.get('pos') == dest), None)
                if occupied is not None:
                    occupied['pos'] = source
                    other_id = next(other_id for other_id, other in self.items.items()
                                    if other is occupied)
                    conn.sendall(build_item_pos(
                        ITEM_WTYPE_NORMAL, other_id, source))
                it['pos'] = dest
                conn.sendall(build_item_pos(ITEM_WTYPE_NORMAL, iid, dest))
                self.log('   >>> 回 S2CItemPos(36)：id=%d pos=%d' % (iid, dest))
                self._persist()
            else:
                if not equip_ok:
                    pass          # 上面已按具体原因记过日志并回帧，别重复刷
                else:
                    self.log('   ⚠ 拒绝无效拖动（道具=%s 实际位置=%s 目标位置=%d）'
                             % (iid, it.get('pos') if it else None, dest))
        elif op == C2S_ITEM_USE and len(body) >= 4:
            self._handle_item_use(conn, body)
        elif op in (C2S_ITEM_FULL_USE, C2S_ANSWER_BIND, C2S_ITEM_LOCK):
            self.log('   （物品指令 op=%d 本服无逻辑，忽略）' % op)

        # ── 详情面板（op144/145/156）：背包里看物品/宠物详情 → 应答 48/162/190 ──
        # 不接的后果：客户端弹不出详情（单机里悬停背包道具看不到说明）。
        elif op == C2S_GETINFO_ITEM and len(body) >= 8:
            # nethero.C2SGetItemInfo(tid, oid)：tid(4)+oid(4)，取 tid 为道具实例 id
            iid = struct.unpack('<I', body[:4])[0]
            it = self.items.get(iid)
            self.log('   >>> 物品详情 iid=%d' % iid)
            if it is not None:
                conn.sendall(build_item_info(iid, it.get('key', 0), it.get('infoext', '')))
                self.log('   >>> 回 S2CItemInfo(162)：%r'
                         % (it.get('infoext') or it.get('shape')))
            else:
                self.log('   （道具 %d 不在背包，不应答）' % iid)
        elif op == C2S_GETITEMINFOEXT and len(body) >= 9:
            # nethero.C2SGETITEMINFOEXT(id, id1)：itype(1)+id(4)+id1(4)；
            # itype=2 是魂珠（本服无数据）→ 只应答 itype=1 的普通物品
            itype_ = body[0]
            iid = struct.unpack('<I', body[1:5])[0]
            self.log('   >>> 物品附加信息 itype=%d iid=%d' % (itype_, iid))
            it = self.items.get(iid) if itype_ == ITEM_WTYPE_NORMAL else None
            if it is not None:
                conn.sendall(build_item_tc_info(iid, it.get('key', 0),
                                                it.get('infoext', '')))
                self.log('   >>> 回 S2CItemTCInfo(190)')
            else:
                self.log('   （itype=%d / 道具 %d 无附加信息，不应答）' % (itype_, iid))
        elif op == C2S_GETINFO_SUMMON and len(body) >= 8:
            pid = struct.unpack('<I', body[:4])[0]
            self.log('   >>> 宠物详情 pid=%d' % pid)
            p = self._pet(pid)
            if p is not None:
                _pname, _pshape, _pdef = self._pet_info(pid)
                conn.sendall(build_pet_info(p['pid'], _pname, _pshape,
                                            self._pet_base(p)))
                self.log('   >>> 回 S2CSummonInfo(48)（宠物详情重发 id=%d）' % p['pid'])
            else:
                self.log('   （未知宠物 %d，不应答）' % pid)

        # ── 服务器时间：两条通道必须分开（net.py L164 / L317）──
        # · C2S_TIME(15)  ← core/mxgw.GetTime 发出 → 必须回 **op15** mxgw.S2CTime
        # · C2GS_TIME(61) ← dialog/dlgcalendar 发出 → 回 op192 dlgcalendar.S2CTime
        # 混用会让客户端 mxgw.g_Func 永不排空、时钟漂移校验失效 → 挂机后断线。
        elif op == C2S_TIME:
            self.log('   >>> 服务器时间查询 C2S_TIME(15) → 回 mxgw.S2CTime(op15, 10000Hz滴答)')
            conn.sendall(build_server_time(OP_MXGW_TIME, mxgw_ticks=True))
        elif op == C2GS_TIME:
            self.log('   >>> 服务器时间查询 C2GS_TIME(61) → 回 dlgcalendar.S2CTime(op192)')
            conn.sendall(build_server_time(OP_TIME))

        # ── 黑名单同步（op14 C2S_BACLLIST）──
        elif op == C2S_BACLLIST:
            self.log('   （黑名单同步请求，本服无逻辑，忽略）')

        # ── 系统设置对话框（dialog/dlgsysopt，op21 / op12）──
        # ★ 必须排在战斗保底之前：设置面板在战斗外打开，但 21 会被客户端
        #   「改快捷键/缩放」等操作随时触发；落进 in_war 保底会凭空多一帧 WarTurn。
        # ★ 两者都不需要应答：客户端保存后自己 PROMPT_MSG('保存成功')，
        #   不等服务器（见 dlgsysopt.OnMessage msgid=60/62 分支）。
        elif op == C2S_SYSSWITCH and len(body) >= 1:
            sub = body[0]
            if sub == SYSSWITCH_SUB_SWITCH and len(body) >= 5:
                sw = struct.unpack('<I', body[1:5])[0]
                self.sys_switch = sw
                self.log('   >>> 系统设置保存 sub=1 switch=0x%08X（%d 项开启）'
                         % (sw, bin(sw).count('1')))
                self._persist()
            elif sub in (SYSSWITCH_SUB_DATA1, SYSSWITCH_SUB_DATA2, SYSSWITCH_SUB_DATA3):
                # PacketAddS(data, len(data))：裸字节 + 显式长度，**不带 \0**
                raw = bytes(body[1:])
                txt = raw.split(b'\x00', 1)[0].decode('gbk', 'replace')
                self.sys_data[int(sub)] = txt
                self.log('   >>> 系统数据保存 sub=%d（%d 字节）：%s'
                         % (sub, len(raw), txt[:40] + ('…' if len(txt) > 40 else '')))
                self._persist()
            elif sub == SYSSWITCH_SUB_JB:
                self.log('   >>> 提交 JB（举报/反馈），本服无逻辑')
            else:
                self.log('   （SYSSWITCH sub=%d 未实现，忽略）' % sub)

        elif op == C2S_DUOYITONG and len(body) >= 2:
            sub = body[0]
            if sub == DUOYITONG_SUB_PRIVATE:
                txt = bytes(body[1:]).split(b'\x00', 1)[0].decode('gbk', 'replace')
                self.private_info = txt
                self.log('   >>> 私密资料提交（%d 字节）：%s'
                         % (len(txt), _mask_private_info(txt)))
                self._persist()
            else:
                self.log('   （DuoYiTong sub=%d 未实现，忽略）' % sub)

        elif op == C2S_MOBILE and len(body) >= 1:
            sub = body[0]
            if sub == MOBILE_SUB_INFO:
                m = self.mobile
                conn.sendall(build_mobile_info(
                    check=m['check'], receive=m['receive'],
                    enable_friends=m['enable_friends'], limit=m['limit'],
                    degree=m['degree'], money=m['money'], balance=m['balance']))
                self.log('   >>> 手机安全面板请求 → 回 S2CMobile(58) sub=1')
            elif sub == MOBILE_SUB_SETTING and len(body) >= 7:
                # C2SMobileSetting(receive, only_friend, tm[4], friend_degree, price)
                # 实发：sub(1) receive(1) only_friend(1) tm×4(1) degree(4) price(1)
                self.mobile['receive'] = body[1]
                self.mobile['enable_friends'] = 1 if body[2] == 0 else 0
                self.mobile['limit'] = [body[3], body[4], body[5], body[6]]
                if len(body) >= 11:
                    self.mobile['degree'] = struct.unpack('<I', body[7:11])[0]
                if len(body) >= 12:
                    self.mobile['money'] = body[11]
                self.log('   >>> 手机安全设置：接收=%d 仅好友=%d 时段=%s'
                         % (body[1], body[2], self.mobile['limit']))
                self._persist()
            elif sub in (MOBILE_SUB_ABOUT_VERIFY, MOBILE_SUB_EXCHANGE):
                self.log('   （手机验证/余额查询 sub=%d：本服无运营商业务，不应答）' % sub)
            elif sub in (MOBILE_SUB_GETCODE, MOBILE_SUB_VERIFY):
                phone = bytes(body[1:12]).split(b'\x00', 1)[0].decode('gbk', 'replace')
                self.log('   （手机验证码 sub=%d phone=%s：本服不发送短信，不应答）'
                         % (sub, phone))
            else:
                self.log('   （MOBILE sub=%d 未实现，忽略）' % sub)

        # ── 生产/矿点（op78 C2S_PRODUCE）──
        # ★ 必须放在**战斗保底分支之前**：该指令由客户端定时下发（战斗中也会发），
        #   若落入 `elif self.in_war: _fallback_turn` 会被当成未知战斗指令回一帧
        #   多余的 WarTurn（实测 2026-10-16 会话 05:22:48 即此）。
        elif op == C2S_PRODUCE and len(body) >= 1:
            sub = body[0]
            if sub == PRODUCE_SUB_MINERALS:
                self.log('   >>> 矿点查询（小地图矿产层刷新）')
                conn.sendall(build_minerals(MINIMAP_ID, []))
                self.log('   >>> 回 S2CProduce(87) sub5：空矿点列表')
            else:
                self.log('   （PRODUCE sub=%d 未实现，忽略）' % sub)

        # ── 组队（netteam.py）：op72 上行 → op82 下行应答 ──
        # ★ 必须在战斗保底之前：战斗中同样可能打开/查看队伍面板。
        elif op == C2S_PLAYER_TEAM:
            self._handle_player_team(conn, body)

        # ── NPC 交互族（netnpc.py）：给予入口 / 交付 / 右键 / 对话选物 / 输入 ──
        # 全部排在战斗保底之前：这些操作在战斗中点击 NPC 也可能触发，
        # 落进保底会凭空多回一帧 WarTurn。
        elif op == C2S_NPC_COMMAND and len(body) >= 6:
            # world.C2SGiveNpc(npcid, gtype)：1(1) + npcid(4) + gtype(1)
            npcid = struct.unpack('<I', body[1:5])[0]
            gtype = body[5]
            self.log('   >>> 给予入口 npcid=%d gtype=%d' % (npcid, gtype))
            if npcid not in self._visible_npc_ids():
                self.log('   ⚠ 当前地图不存在的 NPC id=%d' % npcid)
            else:
                self._npc_give_prompt(conn, npcid, gtype)
                conn.sendall(build_npc_chat_ext(
                    self._npc_name(npcid),
                    'P\n把东西放到我这儿吧。\nQ（拖拽物品给我）\nQ算了。'))
        elif op == C2S_NPC_GIVE and len(body) >= 9:
            self._handle_npc_give(conn, body)
        elif op == C2S_NPC_RBCLICK and len(body) >= 4:
            npcid = struct.unpack('<I', body[:4])[0]
            self.log('   >>> 右键 NPC id=%d' % npcid)
            if npcid not in self._visible_npc_ids():
                self.log('   ⚠ 当前地图不存在的 NPC id=%d' % npcid)
            else:
                # 右键 = 「我想跟你说话」：等价于左键点击（开对话）
                self.cur_npc = npcid
                conn.sendall(build_npc_chat_ext(
                    self._npc_name(npcid), self._npc_welcome_msg()))
        elif op == C2S_NPC_CHOOSE_ITEM and len(body) >= 4:
            iid = struct.unpack('<I', body[:4])[0]
            self.log('   >>> NPC 对话选物 iid=%d' % iid)
            self._npc_item_chosen(conn, iid, 1)
        elif op == C2S_NPC_CHOOSE_ITEM_NUM and len(body) >= 5:
            iid = struct.unpack('<I', body[:4])[0]
            num = body[4]
            self.log('   >>> NPC 对话选物 iid=%d num=%d' % (iid, num))
            self._npc_item_chosen(conn, iid, num)
        elif op == C2S_NPC_INPUT:
            # netnpc.C2SNpcInput(dlg,msg)：msg(变长，无长度前缀，整帧即字符串)
            msg = body.rstrip(b'\x00').decode('gbk', 'replace')
            self.log('   >>> NPC 对话输入 %r' % msg)
            self._npc_input(conn, msg)
        elif op == C2S_NPC_TRADECENTER and len(body) >= 5:
            sub = body[0]
            tid = struct.unpack('<I', body[1:5])[0]
            self.log('   >>> 交易中心 sub=%d id=%d' % (sub, tid))
            # 单机无交易对手：只回应答把面板关/提示，不造虚假挂单
            conn.sendall(build_npc_chat_ext(
                self._npc_name(self.cur_npc) if self.cur_npc is not None
                else NPC_NAME,
                'P\n交易中心只在多人在线时开放。'))

        # ── 显式忽略：单机无对手方/无业务的上行指令 ──
        # ★ 这张表必须排在战斗保底之前。原先这些 opcode 既没分支也没进表，
        #   战斗中一发就落进 in_war 保底，凭空多推一帧 WarTurn
        #   （真机实测 149/55 在战斗中高频）。现在统一在此接住并记录原因。
        elif op in C2S_SINGLEPLAYER_IGNORED_OPS:
            self.log('   （op=%d %s；单机显式忽略）'
                     % (op, C2S_SINGLEPLAYER_IGNORED[op]))

        # ── 其他客户端指令（单机服无对手方/无业务，显式忽略）──
        # 2026-10-19 真机会话实测客户端会发：19 DIY 招式 / 20 照片 / 23 资料 /
        # 52 摆摊 / 55 方向表情同步 / 80 地区 / 149 聊天。不实现的指令也必须显式
        # 接住——否则战斗中落到 in_war 保底会凭空多推一帧 WarTurn（顺序契约见
        # DISPATCH_PRE_WAR_OPS，tests/_test_dispatch.py 逐条守着）。
        elif op in (C2S_DIY_MAGIC, C2S_PLAYER_PHOTO, C2S_PLAYER_IB,
                    C2S_HERO_SALE, C2S_MAP_SYN_DIREMOTE, C2S_DISTRICT,
                    C2S_PLAYER_CHAT):
            self.log('   （op=%d 单机服无对手方/无逻辑，显式忽略）' % op)

        # ── 战斗指令 ──
        # ★ 字段一律走 `frames.SPEC`（唯一来源）解，不在此手写偏移；
        #   截断帧由 unpack 返回 None，落到各分支原有的兜底/忽略路径。
        elif op == C2S_WAR_ATTACK and self.in_war:
            # C2SWarAttack(id) 的 id 是**目标**（warui.DoAct('attack',…) → C2SWarAttack(tid)）
            cmd = bf.unpack_c2s(op, body) or {}
            self._hero_attack(conn, cmd.get('id'))
        elif op == C2S_WAR_PERFORM and self.in_war and len(body) >= 3:
            # 帧体 id(1) perform(2)；此处的 id 是**施法者**，目标由客户端另选
            # （warui.PhyAttack/Magic → DoAct 里 tid 为攻击者、tid2 为招式 id）。
            # 本服只有一个我方单位，故目标取「当前存活敌方首个」。
            self._hero_perform(conn, bf.unpack_c2s(op, body)['perform'], None)
        elif op == C2S_WAR_DEFEND and self.in_war:
            # 防御同样要推进回合（按速度序结算敌方行动），否则玩家可以无限防御刷状态、
            # 且与「攻击/招式 → 敌方行动」的节奏不一致。
            self._hero_defend_action(conn)
        elif op == C2S_WAR_ESCAPE and self.in_war:
            # ★ 行动合法性闸门：越窗/重复的逃跑指令不生效（不白占一个回合）
            if not self._accept_turn():
                self.log('   ⚠ 逃跑指令越窗/重复 → 忽略')
                return True
            self.log('   >>> 逃跑')
            # ✅ 真值：逃跑并非百分百成功（资料库第 30998/31023/31030 行）
            #   - 基础成功率 + NPC加成 - 队长压制 + 自身技能加成
            #   - 失败惩罚：对方白打一回合
            success = self._try_escape(conn)
            if success:
                # 成功：清场 WarDel → WarEnd（逃跑视同脱战胜利）
                self._mark_settled('escape')
                for wid in self._enemy_ids():
                    conn.sendall(build_war_del(wid))
                conn.sendall(build_war_end())
                self._reset_war_state()
                self._quest_battle = None      # 逃跑成功：剧情战斗作废
                self._resync_scene(conn)
                self._emit('war.end', '逃脱战斗', result='escape')
            else:
                # 失败：记录失败，让 _resolve_hero_round 给敌方一个白打回合后再重开窗口
                self.log('   >>> 逃跑失败，敌方获得白打一回合')
                self._escape_failed = True
                self._pending_pet_suffix = None
                self._resolve_hero_round(conn, lambda: False)
        elif op == C2S_WAR_ITEM and self.in_war and len(body) >= 5:
            # C2SWarItem(id, item)：id(1) + item(4)（netwar.C2SWarItem）
            cmd = bf.unpack_c2s(op, body) or {}
            target, iid = cmd.get('id', -1), cmd.get('item', 0)
            self.log('   >>> 战斗用道具：目标 %d，道具 id=%d' % (target, iid))
            self._hero_war_item(conn, target, iid)
        elif op == C2S_WAR_PROTECT and self.in_war:
            cmd = bf.unpack_c2s(op, body) or {}
            self._hero_war_protect(conn, cmd.get('id', -1))
        elif op in C2S_WAR_SUMM_ACTIONS and self.in_war:
            if self._pending_pet_suffix is not None:
                self._finish_pet_turn(conn, op, body)
            else:
                # ★ 宠物指令只认「主角已交指令、正在等宠物」的那一个窗口。
                #   窗口外到达的是重发/迟到的重复指令，忽略即可；
                #   旧实现走 _fallback_turn，会消费主角的指令窗口并白回一帧 WarTurn，
                #   表现为「重复点宠物技能把主角回合点没了」。
                self.log('   ⚠ 宠物指令 op=%d 不在宠物指令窗口 → 忽略' % op)
        elif op == C2S_WAR_SUMMON and self.in_war:
            # 166 = 战斗中召唤/召回宠物（netwar.C2SWarSummon：op(1) [+ id(4) if op==0]）
            cmd = bf.unpack_c2s(op, body) or {}
            self._hero_war_summon(conn, cmd.get('op', 0), cmd.get('id', 0))
        elif (op in (C2S_WAR_SPECIAL, C2S_WAR_PROTECT, C2S_WAR_CATCH)
                or op == C2S_WAR_SUMMONCHILD or op in C2S_WAR_SUMM_CLAN) and self.in_war:
            # 特技/保护/捕捉/子女：无专属演出，但**必须按速度序推进回合**
            self._hero_war_aux(conn, op)
        elif op in (C2S_WAR_TEAM, C2S_WAR_LOOK) and self.in_war:
            # 175 = 战斗频道喊话（C2SSendSign，纯字符串）、176 = 查看单位：
            # 二者不消耗行动权，不应答、也不推进回合
            self.log('   （战斗频道/查看 op=%d，不消耗行动权，忽略）' % op)

        elif self.in_war:
            # 战斗中的其他未识别指令：同样保底推进，避免客户端卡死
            self._fallback_turn(conn, op)

        else:
            self.log('   （未识别 op=%d）' % op)

        return True

class _GMHandler(BaseHTTPRequestHandler):
    """GM 面板 HTTP 控制：GET / 仪表盘，GET /api/state，POST /api/<action>。

    ★ SSE（2026-10-22）：`GET /api/stream` 把 state / battle / bus 三类事件
      持续推给面板，取代前端 `setInterval(load, 1200)` 的轮询。
      轮询的问题是「不问就没有」：两次询问之间发生的状态变化（战斗回合推进、
      金币扣费、任务推进）面板上是瞎的，而且每 1.2s 两次全量快照请求纯属浪费。
    """

    # SSE 推送节拍：战斗中要跟得紧（回合制一步一变），平时低频即可
    BEAT_IDLE = 2.0
    BEAT_WAR = 0.3

    def _j(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def _m4(self):
        return getattr(self.server, 'm4', None)

    def do_GET(self):
        m4 = self._m4()
        if self.path in ('/', '/index.html'):
            html = m4._dashboard_html() if m4 else '<h1>server not ready</h1>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(html.encode('utf-8')))
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return
        if self.path == '/api/state':
            if not m4:
                return self._j({'error': 'no server'}, 503)
            return self._j(m4.gm_state_dict())
        if self.path == '/api/maps':
            # 全部地图清单（GM 面板切图下拉用）：含孤岛标记与可达性
            m4 = self._m4()
            if not m4:
                return self._j({'error': 'no server'}, 503)
            return self._j({'maps': [
                {'mapid': mid, 'name': MAPS[mid]['name'],
                 'island': mid in ISLAND_MAP_IDS,
                 'current': mid == m4.mapid}
                for mid in sorted(MAPS)]})
        if self.path == '/api/battle':
            # 战斗调试快照（2026-10-21）：参数层/数值分层/控制点/逃跑/本战单位
            if not m4:
                return self._j({'error': 'no server'}, 503)
            return self._j(m4.gm_battle_dict())
        if self.path == '/api/stream':
            return self._stream()
        if self.path == '/api/features':
            # ★ 特性实现状态（2026-10-20）：这是**代码事实**，与有没有会话无关，
            #   所以不放进 gm_state_dict（那是运行时快照，每 2s 推一次）。
            #   客户端 UI 有入口、服务端没做的地方在这里一览，避免「点了没反应
            #   以为 bug」和「字典里有 opcode 就以为支持」。
            # GM 面板自身状态也一并返回：面板起不来时用户连不上 /api/features，
            # 但只要能看到这条，就知道「不是没实现，是面板没起来」。
            # rows 是拼好的成品（key/title/status/desc，已按严重度排好）：
            # GM 面板直接渲染，不用在前端再拼一遍状态文案。
            return self._j({'features': feature_status(),
                            'rows': feature_rows(),
                            'legend': feature_legend(),
                            'gm_web': dict(GM_WEB_STATE)})
        return self._j({'error': 'not found'}, 404)

    # ── SSE：把运行时快照推给面板（取代 setInterval 轮询）──
    def _stream(self):
        """SSE 长连接：持续推 state / battle / bus 三类事件。

        ★ 三条实现要点（少任何一条都会变成线上事故）：
          1. `protocol_version = 'HTTP/1.1'` + 显式 Content-Length/close_connection：
             SSE 是长连接，HTTP/1.0 会让服务器发完就关，前端无限重连。
          2. 循环里每次发送前重新取 m4：面板可能在会话中途才连上。
          3. `wfile.write` 的 BrokenPipe 是**正常**的（用户关页面），必须吞掉
             并退循环，否则线程泄漏。
        """
        m4 = self._m4()
        if not m4:
            return self._j({'error': 'no server'}, 503)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.close_connection = True              # SSE 独占连接，别交回 keep-alive
        bus_seq = 0
        try:
            while not getattr(m4, '_stop', False):
                beat = self.BEAT_WAR if m4.in_war else self.BEAT_IDLE
                time.sleep(beat)
                payloads = []
                try:
                    payloads.append(('state', m4.gm_state_dict()))
                    payloads.append(('battle', m4.gm_battle_dict()))
                except Exception:                 # noqa: BLE001
                    pass
                # 业务事件：只推增量（前端另有 /api/bus 兜底拉历史）
                try:
                    ev = bus.BUS.since(bus_seq)
                    if ev:
                        bus_seq = ev[-1]['seq']
                        payloads.append(('bus', {'events': ev}))
                except Exception:                 # noqa: BLE001
                    pass
                for name, data in payloads:
                    body = ('event: %s\ndata: %s\n\n'
                            % (name, json.dumps(data, ensure_ascii=False)))
                    self.wfile.write(body.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError, ValueError):
            pass                                  # 用户关页面：正常收尾

    def do_POST(self):
        m4 = self._m4()
        if not m4:
            return self._j({'error': 'no server'}, 503)
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n) or b'{}') if n else {}
        except (ValueError, TypeError):
            body = {}
        p = self.path.strip('/')
        try:
            if p == 'api/hp':
                m4.gm_set_hp(body.get('value', 0))
            elif p == 'api/resource':
                m4.gm_set_res(body.get('name'), body.get('value', 0))
            elif p == 'api/coins':
                m4.gm_set_coins(body.get('value', 0))
            elif p == 'api/item/add':
                m4.gm_add_item(body.get('shape', 0), body.get('qty', 1), body.get('name', ''))
            elif p == 'api/item/del':
                m4.gm_del_item(body.get('iid'))
            elif p == 'api/pos':
                m4.gm_set_pos(body.get('x', 0), body.get('y', 0))
            elif p == 'api/map':
                m4.gm_goto_map(body.get('mapid', 0), body.get('x'), body.get('y'))
            elif p == 'api/war/start':
                r = m4.gm_war_start()
                if not r.get('ok'):
                    return self._j(r, 400)
            elif p == 'api/war/win':
                r = m4.gm_war_win()
                if not r.get('ok'):
                    return self._j(r, 400)
            elif p == 'api/pet':
                m4.gm_set_pet_hp(body.get('value', 0))
            elif p == 'api/pet/equip':
                # 召唤兽装备调试入口（shape 真值不可得，见 PET_EQUIP_ITEMS 注释）
                r = m4.gm_pet_equip(body.get('pid', 0), body.get('pos', 0),
                                    body.get('shape', 0), body.get('desc'))
                if not r.get('ok'):
                    return self._j(r, 400)
                return self._j({'ok': True, 'result': r,
                                'state': m4.gm_state_dict()})
            elif p == 'api/task/progress':
                r = m4.gm_task_set_progress(body.get('value', 0))
                if not isinstance(r, dict) or 'error' in r:
                    return self._j(r or {'error': 'failed'}, 400)
            elif p == 'api/task/state':
                r = m4.gm_task_state(body.get('value', 1))
                if not isinstance(r, dict) or 'error' in r:
                    return self._j(r or {'error': 'failed'}, 400)
            elif p == 'api/task/complete':
                r = m4.gm_task_complete()
                if not r.get('ok'):
                    return self._j(r, 400)
            elif p == 'api/encounter':
                m4.gm_set_encounter(body.get('every', 0))
            # ── 战斗调试（2026-10-21）：控制点 / 逃跑强制 / 等级重算 ──
            elif p == 'api/battle/cp':
                m4.gm_set_cp(body.get('atk'), body.get('def'), body.get('dis'))
            elif p == 'api/battle/escape':
                m4.gm_set_escape_force(body.get('mode'))
            elif p == 'api/battle/petlevel':
                r = m4.gm_set_pet_level(body.get('level'),
                                        body.get('factor_idx'))
                if not isinstance(r, dict) or not r.get('level'):
                    return self._j(r or {'error': 'failed'}, 400)
                return self._j({'ok': True, 'result': r,
                                'state': m4.gm_state_dict()})
            elif p == 'api/battle/enemylevel':
                r = m4.gm_set_enemy_level(body.get('level'))
                if not isinstance(r, dict) or not r.get('level'):
                    return self._j(r or {'error': 'failed'}, 400)
                return self._j({'ok': True, 'result': r,
                                'state': m4.gm_state_dict()})
            elif p == 'api/heal':
                m4.gm_heal()
            elif p == 'api/refresh':
                m4.gm_refresh()
            elif p == 'api/save':
                m4.gm_save()
            elif p == 'api/reload':
                r = m4.reload_world_config()
                if not r.get('ok'):
                    return self._j(r, 400)
            elif p == 'api/gm/cmd':
                # 控制台命令行：复用 gm_command 真实路径解析器（与 debugger 同源）
                return self._j(m4.gm_command((body or {}).get('line', '')))
            else:
                return self._j({'error': 'unknown action'}, 404)
        except Exception as e:  # noqa: BLE001
            return self._j({'ok': False, 'error': str(e)}, 400)
        return self._j({'ok': True, 'state': m4.gm_state_dict()})

    def log_message(self, *a):  # 静默 HTTP 访问日志
        pass


class MultiServer(object):
    """多会话宿主：一个监听端口，每个连接一个独立的 `M4Server` 会话。

    为什么要这么做：`M4Server` 的 65 个实例属性里绝大部分是**角色私有**的
    （位置/背包/任务/战斗/队伍…），把它当单例就必然只能单人。这里不改
    `M4Server` 内部一行逻辑，只是让「一个连接 = 一个 M4Server 实例」，
    于是多个客户端可以同时在线，各自玩各自的角色。

    共享的只有：监听 socket、seed/遇怪/回合超时等开服参数、日志目录、GM 面板。
    角色存档按账号分文件（`role_<账号>.json`），互不覆盖。
    """

    def __init__(self, host='127.0.0.1', port=3134, seed=SEED_DEFAULT,
                 encounter_every=5, logdir=LOGDIR, enemies=ENEMY_COUNT_DEFAULT,
                 turn_timeout=TURN_TIMEOUT, savedir=None, max_sessions=16,
                 encrypt=False):
        self.host = host
        self.port = port
        self.seed = seed
        self.encrypt = bool(encrypt)        # 透传给每个会话（原版 DLL 加密通道）
        self.encounter_every = encounter_every
        self.enemies = enemies
        self.turn_timeout = turn_timeout
        self.logdir = logdir
        self.savedir = savedir or SAVE_DIR
        self.max_sessions = max(1, int(max_sessions))
        self.sessions = []                 # 活跃会话（M4Server 实例）
        self._account_owners = {}          # account -> M4Server；同账号只允许一个写会话
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._seq = 0
        self._web = False
        self._web_port = 8080
        self._stop = False

    # ---------------------------------------------------------------- 日志
    def log(self, s):
        line = '[%s] %s' % (time.strftime('%H:%M:%S'), s)
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
            print(line.encode(enc, 'replace').decode(enc, 'replace'), flush=True)

    # ---------------------------------------------------------------- 会话
    def new_session(self):
        """造一个会话对象（还没接连接）。

        每个会话是独立的 M4Server：独立角色状态、独立存档文件、独立日志文件。
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
        srv = M4Server(self.host, self.port, self.seed, self.encounter_every,
                       logdir=self.logdir, enemies=self.enemies,
                       turn_timeout=self.turn_timeout, savedir=self.savedir,
                       encrypt=self.encrypt)
        srv._account_binder = lambda old, new, _srv=srv: \
            self._bind_session_account(_srv, old, new)
        srv._session_tag = 's%d' % seq
        # 每个会话独立日志文件：并发时共用同一个句柄会把行写乱
        try:
            os.makedirs(self.logdir, exist_ok=True)
            srv.logpath = os.path.join(
                self.logdir, 'm4_srv_%s_s%d.log'
                % (time.strftime('%Y%m%d_%H%M%S'), seq))
            srv._logf = open(srv.logpath, 'a', encoding='utf-8')
        except OSError:
            srv._logf = None
        with self._lock:
            self.sessions.append(srv)
        return srv

    def _bind_session_account(self, srv, old_account, new_account):
        """原子登记账号所有权；同账号的第二个活跃会话返回 False。"""
        old_account = (old_account or '').strip()
        new_account = (new_account or '').strip()
        with self._lock:
            owner = self._account_owners.get(new_account)
            if new_account and owner is not None and owner is not srv:
                return False
            if old_account and self._account_owners.get(old_account) is srv:
                self._account_owners.pop(old_account, None)
            if new_account:
                self._account_owners[new_account] = srv
            return True

    def drop_session(self, srv):
        with self._lock:
            account = (getattr(srv, 'account', '') or '').strip()
            if account and self._account_owners.get(account) is srv:
                self._account_owners.pop(account, None)
            if srv in self.sessions:
                self.sessions.remove(srv)
        try:
            if getattr(srv, '_logf', None) is not None:
                srv._logf.close()
                srv._logf = None
        except OSError:
            pass

    def session_count(self):
        with self._lock:
            return len(self.sessions)

    # ---------------------------------------------------------------- 主循环
    def run(self):
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        self.port = ls.getsockname()[1]      # port=0 时回填真实端口
        ls.listen(self.max_sessions)
        self._ready.set()
        self.log('多会话服 v4 %s:%d（seed=0x%08X，遇怪每 %d 次，最多 %d 会话）'
                 % (self.host, self.port, self.seed, self.encounter_every,
                    self.max_sessions))
        for _line in dmg_banner().split(chr(10)):
            self.log(_line)
        if self._web:
            self._start_web(self._web_port,
                            explicit=getattr(self, '_web_port_explicit', False))
        while not self._stop:
            try:
                conn, addr = ls.accept()
            except KeyboardInterrupt:
                break
            except OSError:
                break
            if self.session_count() >= self.max_sessions:
                self.log('── 拒绝 %s（已达上限 %d 会话）' % (addr, self.max_sessions))
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            srv = self.new_session()
            srv._session_tag = 's%d/%s' % (len(self.sessions), addr[0])
            self.log('── 接入 %s（会话数 %d）' % (addr, self.session_count()))
            threading.Thread(target=self._serve_one, args=(srv, conn, addr),
                             daemon=True, name='m4-session').start()
        ls.close()
        self.log('多会话服已停止')

    def _serve_one(self, srv, conn, addr):
        try:
            srv.handle(conn)
        except AccountInUseError as e:
            self.log('会话 %s 已拒绝: %s' % (addr, e))
        except Exception as e:  # noqa: BLE001
            self.log('会话 %s 异常: %r' % (addr, e))
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self.drop_session(srv)
            self.log('── 断开 %s（剩余会话 %d）' % (addr, self.session_count()))

    def _start_web(self, port, explicit=False):
        """GM 面板：绑定到「当前唯一/第一个会话」。

        面板是调试用的，多会话下只反映其中一个角色；要精确控制请用单会话模式。
        """
        def _pick():
            with self._lock:
                return self.sessions[0] if self.sessions else None

        class _Router(_GMHandler):
            def _m4(self):
                return _pick()

        # ★ 端口冲突自动顺延（2026-10-20）：与 M4Server._start_web 同口径。
        #   复用 M4Server 的静态探测函数，避免两套实现漂移。
        want = port
        port, bumped, reason = M4Server._pick_web_port(self, port,
                                                      explicit=explicit)
        if bumped:
            if explicit:
                self.log('   ⚠ --web-port %d 已被占用，本次改用 %d'
                         '（要固定请先释放 %d）' % (want, port, want))
            else:
                self.log('   · 默认端口 %d 被占用，GM 面板改用 %d' % (want, port))
        elif reason:
            self.log('   ⚠ %s' % reason)
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), _Router)
        except OSError as e:
            self.log('   ✗ GM 面板启动失败（%s）：%r' % (port, e))
            self._web_port = 0
            return False
        self._web_port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.log('   🌐 GM 面板已启动：http://127.0.0.1:%d/（跟踪首个会话）'
                 % self._web_port)
        return True

    def serve_background(self, port=0, web=False, web_port=8080,
                         explicit_web_port=False):
        """守护线程内启动 accept 循环，返回实际监听端口。"""
        self.port = port
        self._web = bool(web)
        self._web_port = web_port
        self._web_port_explicit = bool(explicit_web_port)
        self._ready.clear()
        threading.Thread(target=self.run, daemon=True, name='m4-multi').start()
        if not self._ready.wait(5.0):
            raise RuntimeError('多会话内核监听未就绪（5s 超时）')
        return self.port


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3134)
    ap.add_argument('--seed', default=hex(SEED_DEFAULT))
    ap.add_argument('--encrypt', dest='encrypt', action='store_true',
                    default=True,
                    help='启用加密+压缩（默认，配合原版 mxmain.dll）')
    ap.add_argument('--no-encrypt', dest='encrypt', action='store_false',
                    help='禁用加密（仅在仍使用补丁 mxmain_nocrypto.dll 时）')
    ap.add_argument('--encounter-every', type=int, default=ENCOUNTER_EVERY_DEFAULT,
                    help='每 N 次移动触发一次遇怪（默认 %d；可由 world_config.json 的 '
                         'encounter.every 覆盖）' % ENCOUNTER_EVERY_DEFAULT)
    ap.add_argument('--enemies', type=int, default=ENEMY_COUNT_DEFAULT,
                    help='每场战斗的敌方单位数（客户端录像中为 6+，默认 %d）' % ENEMY_COUNT_DEFAULT)
    ap.add_argument('--turn-timeout', type=int, default=TURN_TIMEOUT,
                    help='回合指令超时秒数（对齐客户端 30s 倒计时；超时默认普攻，默认 %d）'
                         % TURN_TIMEOUT)
    ap.add_argument('--fresh', action='store_true',
                    help='启动时删除已有角色存档，从零开始（出生点/满资源/初始背包）')
    ap.add_argument('--no-encounter', action='store_true',
                    help='关闭遇怪（纯探索地图，移动不再触发战斗）')
    ap.add_argument('--no-world-config', action='store_true',
                    help='忽略 world_config.json，强制使用内置默认世界数据（模块导入时即生效）')
    ap.add_argument('--no-enemy-skill', action='store_true',
                    help='关闭敌方招式（敌方仅普攻；协议级战斗测试用，保证行动序列确定性）')
    ap.add_argument('--web', action='store_true',
                    help='启动 Web GM 调试面板（默认端口 8080）')
    ap.add_argument('--web-port', type=int, default=None,
                    help='GM 面板 HTTP 端口（需配合 --web）。不写=默认 8080 且冲突时自动顺延；'
                         '显式指定=冲突时仍顺延但打印警告')
    ap.add_argument('--save-dir', default=None,
                    help='存档目录（默认 %s）' % SAVE_DIR)
    ap.add_argument('--single-session', action='store_true',
                    help='退回单会话模式（同一时刻只允许一个客户端；默认为多会话）')
    ap.add_argument('--max-sessions', type=int, default=16,
                    help='多会话模式下允许的最大并发会话数（默认 16）')
    ap.add_argument('--single', action='store_true',
                    help='单机模式：由 singleplayer.py 托管（内核+客户端同生命周期），'
                         '默认自动选空闲端口并拉起 mx.exe')
    ap.add_argument('--pet', type=int, default=PET_CATALOG_DEFAULT,
                    help='新角色默认宠物造型（可选：%s；默认 %d）。已有存档里的 pet_shape 优先'
                         % ('、'.join('%d=%s' % (k, v['name'])
                                     for k, v in sorted(PET_CATALOG.items())),
                            PET_CATALOG_DEFAULT))
    args = ap.parse_args()
    # 宠物造型是模块级默认值：会话在构造/建角时才读它，所以必须在起服务前改。
    if args.pet in PET_CATALOG:
        globals()['PET_SHAPE_DEFAULT'] = int(args.pet)
    else:
        print('[pet] 造型 %d 不在目录（%s），沿用默认 %d'
              % (args.pet, '/'.join(str(k) for k in sorted(PET_CATALOG)),
                 PET_CATALOG_DEFAULT))
    # ★ --web-port 的「显式 / 默认」区分（2026-10-20）：
    #   default=None 才能知道用户到底写没写；写死了默认值就永远分不清。
    #   这个标志影响冲突时的提示语气，也影响是否提示「要固定请释放端口」。
    _web_port_explicit = args.web_port is not None
    if args.web_port is None:
        args.web_port = 8080
    if args.single:
        import singleplayer
        return singleplayer.main(seed=int(args.seed, 0),
                                encounter_every=(0 if args.no_encounter
                                                 else args.encounter_every),
                                enemies=args.enemies,
                                turn_timeout=args.turn_timeout,
                                fresh=args.fresh,
                                web=args.web, web_port=args.web_port,
                                explicit_web_port=_web_port_explicit,
                                encrypt=args.encrypt,
                                save_dir=args.save_dir)
    if args.fresh:
        savedir = args.save_dir or SAVE_DIR
        # --fresh 清的是「所有角色」，不只是旧的单角色存档
        for fn in os.listdir(savedir) if os.path.isdir(savedir) else []:
            if fn == 'role.json' or (fn.startswith('role_') and fn.endswith('.json')):
                try:
                    os.remove(os.path.join(savedir, fn))
                    print('[fresh] 已删除旧存档 %s' % fn)
                except OSError:
                    pass
    encounter_every = 0 if args.no_encounter else args.encounter_every
    if args.single_session:
        srv = M4Server(args.host, args.port, int(args.seed, 0), encounter_every,
                       enemies=args.enemies, turn_timeout=args.turn_timeout,
                       savedir=args.save_dir, encrypt=args.encrypt)
        if args.no_enemy_skill:
            srv.enemy_skill_chance = 0.0
        if args.web:
            srv._web = True
            srv._web_port = args.web_port
        srv.run()
        return 0
    srv = MultiServer(args.host, args.port, int(args.seed, 0), encounter_every,
                      enemies=args.enemies, turn_timeout=args.turn_timeout,
                      savedir=args.save_dir, max_sessions=args.max_sessions,
                      encrypt=args.encrypt)
    if args.no_enemy_skill:
        _orig_new = srv.new_session

        def _new_no_skill():
            s = _orig_new()
            s.enemy_skill_chance = 0.0
            return s
        srv.new_session = _new_no_skill
    if args.web:
        srv._web = True
        srv._web_port = args.web_port
    srv.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
