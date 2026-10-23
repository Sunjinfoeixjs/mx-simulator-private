# -*- coding: utf-8 -*-
u"""宠物技能表：学习/遗忘/替换的**唯一落库入口**（P3-02）。

协议事实（决定了本模块的定位）
------------------------------
客户端 `netpet` 只有 **放生/查看/改名/出战/存取/加点** 六类上行，
**没有**「宠物学技能」这个 opcode（`net.py` 138 条上行全量核对过）。
宠物技能表只能由服务端经 `S2CSummonSkill(49)` 下发 →
所以本模块是权威入口，调用方只有两处：GM 指令、以及**以后的**兽诀道具。
不要指望客户端会发请求上来。

真值 / 模拟 / 缺口
------------------
  A 真值  技能 id 白名单 = `pet_skill_data.PET_SKILL_INFO`（404 条，
          由 `gen_pet_skill_data.py` 从客户端 `petskill.DATA` 生成）。
          学到表外的 id，客户端菜单会显示 `未知<id>`、点了没反应 ——
          所以默认**拒收**（`unknown_skill`），而不是让它静默进表。
  A 真值  绑定技能：`S2CSummonSkill` 里 ≥10000 的 id 表示「绑定技能」，
          客户端 `pet.UpdateSkill` 会 -10000 并记 bind_idx。归一化必须两边都认。
  C 约定  重复技能拒收（`already`）：同一技能占两格没有意义。
  S 模拟  技能格上限 `SKILL_MAX = 8`（录像面板可见 6 格；上限取 8 无真值）。
  G 缺口  「魔兽要诀 → 学会哪个技能」不可得（见 `SKILL_SRC_GAP`）：
          itemdata 只有单一道具 25405，没有「这本要诀记载哪个技能」的字段。
          资料还写明「有一定几率冲掉原有技能」，但几率也没数值。
          于是 `learn`/`forget`/`replace` **三个机制都实现了**，
          缺的是「谁触发、换成什么」——那属于数据，不编。

为什么失败必须**零副作用**
--------------------------
加点/学技能都是「服务端说不行、客户端已经本地改过面板」的场景。
所以每个入口都先做完全部校验再动列表，失败时连 `len(skills)` 都不变；
调用方负责回一帧 `S2CSummonInfo(48)` 把面板拉回真实状态（见 `_pet_add_point`）。
"""
import pet_skill_data as psd

BIND_OFFSET = 10000      # S2CSummonSkill 的「绑定技能」偏移（客户端会减回去）
SKILL_MAX = 8            # ★ SIM：技能格上限（录像面板可见 6 格）

# 归一化结果里的 reason 常量：调用方直接拿去记日志/回 GM，不要自己猜字符串
R_OK = 'ok'
R_NO_PET = 'no_pet'
R_BAD_ID = 'bad_id'
R_UNKNOWN = 'unknown_skill'
R_ALREADY = 'already'
R_FULL = 'full'
R_ABSENT = 'absent'

# ── 缺口文案（GM 面板/文档共用一份，避免各处措辞漂移）──────────────────────
SKILL_SRC_GAP = (u'魔兽要诀 → 技能 的映射不可得：itemdata 只有单一道具 25405，'
                 u'没有「这本要诀记载哪个技能」的字段')
REPLACE_RATE_GAP = u'「有几率冲掉原有技能」的几率无真值（资料只写了「有一定几率」）'


def normalize(sid):
    """→ (规范 id, 是否绑定技能)。

    规范 id <=0 表示非法（本函数不抛异常，调用方判 `norm <= 0`）。
    ≥10000 视为绑定技能：保留「绑定」这个事实（写回时仍用原 id），
    但**判重/查表一律按规范 id**，否则 415 与 10415 会被当成两个技能。
    """
    try:
        raw = int(sid)
    except (TypeError, ValueError):
        return 0, False
    if raw >= BIND_OFFSET:
        return raw - BIND_OFFSET, True
    return raw, False


def ids_of(skills):
    """技能表 → 规范 id 列表（顺序不变）。脏数据跳过，不抛。"""
    out = []
    for s in (skills or []):
        n, _b = normalize(s)
        if n > 0:
            out.append(n)
    return out


def has(skills, sid):
    """技能表里有没有这个技能（按规范 id 比较，绑定形式也算有）。"""
    n, _b = normalize(sid)
    return n > 0 and n in ids_of(skills)


def check_learn(skills, sid, limit=None, strict=True):
    """只判定，不改任何东西 → (ok, reason, 规范 id)。

    `strict=True` 时按客户端真值表白名单校验 id 是否存在；
    GM 想强塞一个表外技能做实验可传 False（会记进日志，别当默认）。
    """
    limit = SKILL_MAX if limit is None else int(limit)
    norm, _bound = normalize(sid)
    if norm <= 0:
        return False, R_BAD_ID, norm
    if strict and not psd.known(norm):
        return False, R_UNKNOWN, norm
    if norm in ids_of(skills):
        return False, R_ALREADY, norm
    if len(skills or []) >= limit:
        return False, R_FULL, norm
    return True, R_OK, norm


def learn(skills, sid, limit=None, strict=True):
    """学一个技能：成功时**原地** append 并返回 (True, 'ok', norm)。

    保留入参的原始写法（`sid=10415` 存 10415 而不是 415）——绑定技能是
    客户端要看到的事实，归一化只用于判重。
    """
    ok, why, norm = check_learn(skills, sid, limit, strict)
    if not ok:
        return False, why, norm
    if not isinstance(skills, list):
        return False, R_BAD_ID, norm
    skills.append(int(sid))
    return True, R_OK, norm


def forget(skills, sid):
    """遗忘一个技能 → (ok, reason, norm)。不存在则不动列表。"""
    if not isinstance(skills, list):
        return False, R_BAD_ID, 0
    norm, _bound = normalize(sid)
    if norm <= 0:
        return False, R_BAD_ID, norm
    for i, s in enumerate(skills):
        n, _b = normalize(s)
        if n == norm:
            skills.pop(i)
            return True, R_OK, norm
    return False, R_ABSENT, norm


def replace(skills, sid, victim=None, limit=None, strict=True):
    """「冲掉一个旧技能、换成新的」——`replace_skill` 机制（要诀语义）。

    `victim=None` 时按技能表顺序冲掉**第一个**（最老的），这是资料里
    「有一定几率冲掉原有技能」中「冲掉」那一步的可确定部分。
    ⚠ **谁会冲掉、冲掉几率是多少，不可得**（`REPLACE_RATE_GAP`）——
      所以本函数不会自己跑，必须由调用方（以后的兽诀道具）显式调用。

    返回 (ok, reason, norm, 被冲掉的规范 id or None)。
    失败时列表原样不动。
    """
    norm, _b = normalize(sid)
    if norm <= 0:
        return False, R_BAD_ID, norm, None
    if strict and not psd.known(norm):
        return False, R_UNKNOWN, norm, None
    cur = ids_of(skills)
    if norm in cur:
        return False, R_ALREADY, norm, None
    if not isinstance(skills, list) or not skills:
        return False, R_ABSENT, norm, None
    if victim is None:
        out_norm, _bb = normalize(skills[0])
    else:
        out_norm, _bb = normalize(victim)
        if out_norm not in cur:
            return False, R_ABSENT, norm, None
    limit = SKILL_MAX if limit is None else int(limit)
    if len(skills) > limit:            # 已经在超限状态（手改档）：先允许替换收口
        pass
    ok, why = forget(skills, out_norm)[:2]
    if not ok:
        return False, why, norm, None
    ok, why, _n = learn(skills, sid, limit, strict)
    if not ok:                         # 理论上不会发生：换出后再学必然有位
        skills.append(out_norm)        # 回滚
        return False, why, norm, None
    return True, R_OK, norm, out_norm


def describe(skills):
    """技能表 → 面板/日志用的可读清单（表外 id 如实标 `未知<id>`，不编名字）。

    返回 [{'sid','bound','name','type','type_name','desc','known'}]，保持原顺序。
    """
    out = []
    for s in (skills or []):
        norm, bound = normalize(s)
        info = psd.get(norm) if norm > 0 else None
        if info is None:
            out.append({'sid': norm, 'bound': bound,
                        'name': u'未知<%s>' % (s,), 'type': None,
                        'type_name': u'?', 'desc': u'', 'known': False})
            continue
        out.append({'sid': norm, 'bound': bound, 'name': info['name'],
                    'type': info['type'], 'type_name': info['type_name'],
                    'desc': info['desc'], 'known': True})
    return out


def active_ids(skills):
    """能进宠物招式菜单的 id（type 1/2）；被动不进菜单（客户端口径）。"""
    out = []
    for norm, _bound in (normalize(s) for s in (skills or [])):
        info = psd.get(norm) if norm > 0 else None
        if info and info['type'] in (1, 2):
            out.append(norm)
    return out


def provenance():
    """真值/SIM/缺口一览（写进文档与 GM 面板）。"""
    return {
        'whitelist': 'A 真值（petskill.DATA 共 %d 条，生成器对账）'
                     % len(psd.PET_SKILL_INFO),
        'bind_offset': 'A 真值（S2CSummonSkill ≥10000 = 绑定技能）',
        'skill_max': 'SIM（%d 格；录像面板可见 6 格）' % SKILL_MAX,
        'source': 'GAP（%s）' % SKILL_SRC_GAP,
        'replace_rate': 'GAP（%s）' % REPLACE_RATE_GAP,
    }
