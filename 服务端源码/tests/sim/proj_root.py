# -*- coding: utf-8 -*-
"""项目根定位：按**标志目录**向上找，而不是数 `dirname` 的层数。

为什么要有这个模块（P5-00 审计发现，2026-10-23）
──────────────────────────────────────────────
内核和几个工具原本各自写着同一行：

    CLAW = os.path.dirname(os.path.dirname(os.path.dirname(
               os.path.abspath(__file__))))

这行只在「文件位于 `MX/<a>/<b>/x.py`」时成立。文件实际在
`MX/服务端源码/tests/sim/x.py`，是 **4 层**深，`dirname×3` 只到
`MX/服务端源码`；再拼一次 `'服务端源码'` 就变成：

    期望  MX/服务端源码/save/role.json
    实际  MX/服务端源码/服务端源码/save/role.json

它**不报错**：`load_role()` 对「文件不存在」返回 None 是正常路径，
于是默认启动时读到空档，历史 `role.json` 被静默忽略 —— 玩家以为进度丢了。

只要还可能挪文件（`sim/` → `tests/sim/` 已经挪过一次），
「数层数」就会再错一次。所以改成**找标志目录**：

    同时含 `服务端源码/` 与 `协议资料/` 的那一层就是项目根

用法
────
    from proj_root import find_project_root, fallback_root

    CLAW = find_project_root(__file__) or fallback_root(__file__, 4)
"""
import os

# 项目根的**双标志**：两个都必须在，避免把某个中间层误认成根。
# 只用一个标志时，`MX/服务端源码/` 自己也可能含同名子目录（旧布局就发生过）。
ROOT_MARKERS = ('服务端源码', '协议资料')

# 向上找的最大层数。当前文件在第 4 层，留余量到 8，够任何合理布局。
MAX_UP = 8


def find_project_root(start):
    """从 start（通常是 `__file__`）向上找同时含两个标志目录的那一层。

    参数
        start : 文件路径或目录路径。传 `__file__` 时从它所在目录开始找。

    返回
        项目根的绝对路径（str）；找不到时返回 None。

    ★ 找不到时**返回 None 而不是猜一个路径**：猜出来的路径能让程序继续跑，
      但存/读档会落到没人预期的位置，属于最坏的一种失败（静默）。
      调用方必须显式决定「找不到怎么办」，让这个决定留在代码里、看得见。
    """
    if not start:
        return None
    p = os.path.abspath(start)
    if not os.path.isdir(p):
        p = os.path.dirname(p)
    cur = p
    for _ in range(MAX_UP):
        if all(os.path.isdir(os.path.join(cur, m)) for m in ROOT_MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:          # 已到盘符根
            break
        cur = parent
    return None


def fallback_root(start, levels):
    """标志目录都不在时的兜底：向 levels 层。

    存在的意义只有一个：**别让起服直接崩**。标志目录缺失说明布局已被改动，
    这时数层数同样不可靠，但至少给出一个确定的路径，并且调用点会打日志。
    """
    p = os.path.abspath(start)
    if not os.path.isdir(p):
        p = os.path.dirname(p)
    for _ in range(max(0, int(levels))):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p
