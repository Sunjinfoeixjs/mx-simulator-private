# B9：`dialog/dlgsysopt` 解锁报告（源码还原 484/484）

> 日期：2026-10-16　补丁：`tools/u6_patch26.py` **[P7]**
> 清单：`data/manifest/uncompyle6_ok_b9.json`（484 模块）

## 1. 结论

`dialog/dlgsysopt.py` 已完整还原并落盘（1840 行 / 78,402 字节），
`src/*.py` 达 **484 / 484（100%）**，lib2to3（Py2 文法）全量解析通过。

## 2. 失败定位（三角定位）

工具：`_dlgsysopt_triage2.py`（xdis 载入 + scanner.ingest + parser.parse，逐 code object）

```
code object 总数 = 103 ；parse 成功 = 102 ；失败 = 1
失败对象：OnMessage (firstlineno=1101, tokens=1569)
异常：Parse error at or near `LOAD_FAST' instruction at offset 0
     → uncompyle6.semantics.pysource.SourceWalkerError: Deparsing stopped due to parse error
```

精确卡点（与既有插桩诊断 `logs/_real_stuck.txt` 一致）：

```
1499_0  COME_FROM     1408
1499    JUMP_ABSOLUTE 3708      ← for 循环体末尾回边
1502_0  COME_FROM     1343      ← 卡住点（idx=624/1569）
1502    POP_TOP
```

## 3. 根因

文法中 `JUMP_BACK`（新版本循环回边）已有「前置 COME_FROM 吸收」规则：

```
cf_jb_cf_pop ::= _come_froms JUMP_BACK come_froms POP_TOP
ifelsestmtl  ::= testexpr c_stmts_opt cf_jb_cf_pop else_suitel
```

而 **2.6 的循环回边是 `JUMP_ABSOLUTE`**（scanner 不转 JUMP_BACK），该变体缺失，
导致「if 分支体以嵌套 if（产生 COME_FROM）+ 循环回边结尾、且该 if 带 else 分支」
无法归约 —— 只有 `ja_cf_pop ::= JUMP_ABSOLUTE come_froms POP_TOP`（无前置 COME_FROM）。

## 4. 补丁 [P7]

```python
cf_ja_cf_pop ::= _come_froms JUMP_ABSOLUTE come_froms POP_TOP
ifelsestmtc  ::= testexpr_then c_stmts_opt cf_ja_cf_pop else_suitec
ifelsestmt   ::= testexpr      c_stmts_opt cf_ja_cf_pop else_suite
```

候选实验（`_exp_ja_cf_pop.py`，逐个候选对全部 103 个 code object 试 parse）：

| 候选 | 结果 |
|---|---|
| baseline（不加规则） | 失败 1/103（OnMessage） |
| A. cf_ja_cf_pop + ifelsestmtl | 失败 1/103 |
| B. 仅 cf_ja_cf_pop | 失败 1/103 |
| C. iflaststmt 带 _come_froms | 失败 1/103 |
| **D. cf_ja_cf_pop + ifelsestmtc + ifelsestmt** | **失败 0/103 ✅** |

## 5. 重建正确性核查

卡点后紧跟 `dlgnpc.ShowClientDlg('请先进行手机验证…')`（`MobileCheck` 锚点，行 1220）。
重建结果结构完整、语义正确（`elif msgid == 55` 分支）：

```python
elif g_MobileArgs['手机验证']:
    if self.GetObject('enable_mobile').IsSelected():
        for obj_name in ('enable_1', 'enable_2', 'enable_3'):
            self.GetObject(obj_name).SetSelect(False)
        for ctrl in self.m_CtrlList.itervalues():
            ctrl.m_DownButton.Enable(False)
    else:
        self.GetObject('enable_1').SetSelect(True)
        self.m_CtrlList['通讯费'].m_DownButton.Enable(True)
else:
    import dlgnpc
    dlgnpc.ShowClientDlg(b'\xc7\xeb…', '', MobileCheck)
    self.GetObject('enable_mobile').m_Selected = True
```

无语句丢失/重复（JUMP_ABSOLUTE 为 for 体末尾回边，被 if-else 归约顺带消费）。

## 6. 质量闸门（新文件）

| 项 | 值 |
|---|---|
| 空文件 / <3 行 | 否 / 否 |
| 颠倒赋值（`None =`/`数字 =`） | **0** |
| 语句粘连（4+ 空格） | **0** |
| 裸 yield 缺陷 F1/F2 | **0 / 0** |
| lib2to3（Py2 文法）解析 | **PASS** |
| 顶层 def/class、方法 def | 24 / 74 |

## 7. 回归验证

`tools/_p7_regress.py`：以 P7 文法全量重新 deparse 484 个 pyc，
与磁盘 `src/*.py` 逐字节比对（CRLF 归一化）。

**发现 P7 的副作用（重要）**：全量回归第 1 处差异为 `core/warrior.py`：

```
DIFF  core/warrior.py   len=1616/1616  first=(326, '                else:', '')
```

`warrior.LoadShadowPet` 被 P7 翻成 `try/except/else`（`else:` + 深一级缩进）。

**判定实验**（`_cmp_warrior_nop7.py`）：

```
带 P7  == 磁盘 ? False
无 P7  == 磁盘 ? True
```

→ 差异由 P7 引入。**反汇编判定**（`_dis_lsp2.py`，xdis token 流）：

```
 58  off=137   SETUP_EXCEPT      → 162
 65  off=158   POP_BLOCK
 66  off=159   JUMP_FORWARD      → 185      ← try 尾部跳转
 67  off=162_0 COME_FROM 137                ← handler 入口
 78  off=180   JUMP_FORWARD      → 185      ← handler 尾部跳转
 81  off=185_0 COME_FROM 180                ← 与 159 汇聚同一目标
 82  off=185_1 COME_FROM 159
```

两条跳转（159、180）**汇聚同一目标 185** —— 字节码只支持「try/except + 后续语句」
平铺形态，**不存在 else 体**。即 P7 让 LALR 冲突表重排后，把该天然歧义解成了不忠实的一支。

**处置：P7 改为按需启用（`u6_patch26.ENABLE_P7`，默认 False）**

- 机制：`_customize_with_kv` 仅在 `ENABLE_P7=True` 时注入 `_CF_JA_RULES`。
- 恢复入口：`tools/_recover_dlgsysopt_p7.py`（脚本内开启 P7，只解编译该文件）。
- 零回归复验（`_verify_zero_regress.py`）：

```
默认 ENABLE_P7 = False
core/warrior.py      默认管线 == 磁盘 ? True    ← 副作用已隔离
dialog/dlgsysopt.py  deparse FAIL SourceWalkerError（符合预期，需 P7）
netopenui.py         默认管线 == 磁盘 ? False   ← 既有陈旧差异（见下）
teamui.py            默认管线 == 磁盘 ? False   ← 既有陈旧差异（见下）
```

**副产品发现**：`netopenui.py` / `teamui.py` 等文件在**默认管线（P1–P6）下重解编译即产出修正版**
（`yield` 正确缩进/换行），磁盘现存的是**修复前落盘的旧产物**（即验收发现的 F1/F2 裸 yield 缺陷）。
→ 该 82 个缺陷文件可用当前默认管线批量刷新修复（`tools/_refresh_stale.py --apply`），
与 P7 无关，可独立执行。

## 8. 复现

```
python38 tools/_recover_dlgsysopt_p7.py          # 恢复 dlgsysopt（仅本文件启用 P7）
python38 tools/_gen_manifest_b9.py               # 刷新 B9 清单（484 模块）
python38 tools/_verify_zero_regress.py           # 零回归复验
```

三次运行 md5 一致：`3b7437cdef58e1384df12e334f770924`（1840 行 / 76,562 字节）。
