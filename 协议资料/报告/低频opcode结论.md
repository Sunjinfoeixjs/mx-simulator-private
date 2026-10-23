# 6.5% 低频 opcode —— 结论与剩余清单

> 承接 `opcode重映射报告.md` / `反编译完成报告.md`
> 日期：2026-10-13
> 方法：从 `mxmain.dll` 直接提取分派表 + Ghidra case 分支 + 全量字节码频次交叉核对

---

## 一、6.5% 的准确构成

「6.5% 未映射」= 按**指令条数**算出的 48,641 / 752,582 条指令，涉及 **63 种 opcode**。
这 63 种按执行机制分成两堆：

| 分组 | 种类 | 说明 |
| --- | --- | --- |
| **A. 在分派表内**（`0x00..0x62`） | 45 种 | 表里有 handler，只是还没起名 → **可直接补全** |
| **B. 在分派表外**（`≥0x63`） | 18 种 | 走 `switch` 分支，需读 `eval_full.c` 的 case 体 → **可补全** |
| 合计 | **63 种** | |

另外还有 **116 个从未出现的空槽**（`0x63..0xff` 的大部分），它们**不属于**这 6.5%。

> 前一份报告写「剩余 0.13% 是极低频指令」，那个 0.13% 指的是**未识别的指令条数**
> 在反编译产物里的比例；而 6.5% 是**未命名 opcode 覆盖的指令条数**。两者口径不同。

---

## 二、关键机制修正：只有一张分派表

前一版报告提到「第二张表」，本次纠正。

### 唯一真实分派表

```
computed jump @ 0048d3a3 : JMP dword ptr [EAX*4 + 0x48f668]
```

对应 C 代码（`eval_full.c`）：

```c
uVar15 = (uint)(byte)*local_4c;              // 取 opcode
uVar17 = 0;
puVar3 = (ushort *)((int)local_4c + 1);
if (0x5e < uVar15) {                         // 宽度判据
    uVar17 = (uint)*(ushort *)((int)local_4c + 1);   // 2 字节小端参数
    puVar3 = (ushort *)((int)local_4c + 3);
}
switch(uVar15) {
  case 0: ...
  case 1: ...
  default:
      FUN_004aa930(PTR_DAT_0064cce0, "unknown opcode");   // 兜底
}
```

**宽度规则实证**：`op >= 0x5e` 才读 2 字节参数，否则 1 字节。

### 表长 = 99 项（`0x00..0x62`）

从 `0x48f668` 起连续 99 个 dword 都是 `.text` 内代码指针，第 100 项即变成 x86 指令字节：

```
0x00: 0048d2f0 ... 0x62: 0048f28a
0x63: 02620100   <- 已是代码
```

### 被排除的假阳性

`0x48f8f0` 处另有 14 项指针数组，曾被当作「第二张分派表」。反汇编后真相：

```
0x48d502: 33 db      xor  ebx, ebx
          3b c1      cmp  eax, ecx
          0f 9c c3   setl bl        <- <
          c3         ret
0x48d50d: ...        0f 9e c3  setle   <- <=
0x48d518: ...        0f 94 c3  sete    <- ==
0x48d523: ...        0f 95 c3  setne   <- !=
0x48d52e: ...        0f 9f c3  setg    <- >
0x48d539: ...        0f 9d c3  setge   <- >=
```

这是 **COMPARE_OP 的比较函数指针表**（顺序 = `Py_LT/Py_LE/Py_EQ/Py_NE/Py_GT/Py_GE`），
**与 opcode 分派无关**。此前 `0xaa`/`0xaf` 在此表「恰好对上」纯属地址巧合。

---

## 三、op ≥ 0x63 走 switch case

`eval_full.c` 显式 `case` 共 **102 个**，范围 `0xb..0xfb`，落在 `0x63..0xa1` 区间的有 12 个：

```
0x6c 0x71 0x72 0x7e 0x83 0x85 0x8b 0x8f 0x90 0x91 0x95 0x98
```

**高频 + 带参 opcode 被编译成 case，不再走指针表** —— 这正是
`0x71 STORE_FAST`、`0x8f LOAD_CONST`、`0xc1 LOAD_FAST` 等最高频指令不在表内的原因。

---

## 四、A 组：分派表内、可立即定名（证据 A）

handler 带 CPython 独有错误串的槽位，一次性定名：

| custom | 次数 | handler | 错误串 | 定名 |
| --- | --- | --- | --- | --- |
| `0x03` | 3 | 0x48df86 | `lost sys.displayhook` | PRINT_EXPR |
| `0x06` | 506 | 0x48ec17 | `no locals found during 'import *'` | IMPORT_STAR |
| `0x09` | 1,866 | 0x48e0d5 | `lost sys.stdout`（非换行支） | PRINT_ITEM |
| `0x0f` | 227 | 0x48dfcd | `lost sys.displayhook` | PRINT_EXPR |
| `0x12` | — | 0x48e25f | `lost sys.stdout` | PRINT_ITEM / NEWLINE_TO |
| `0x14` | — | 0x48e26d | `lost sys.stdout` | PRINT_ITEM / NEWLINE_TO |
| `0x29` | — | 0x48e55d | `no locals found when storing %s` | STORE_NAME |
| `0x33` | 29 | 0x48e010 | `lost sys.displayhook` | PRINT_EXPR |
| `0x3b` | — | 0x48f4e8 | `%.200s() takes no arguments` | CALL_FUNCTION |
| `0x3c` | — | 0x48e75d | `global name ... is not defined` | LOAD_GLOBAL / STORE_GLOBAL |
| `0x3e` | 360 | 0x48d71c | `local variable ... before assignment` | LOAD_FAST / LOAD_DEREF |
| `0x48` | 1 | 0x48e5ac | `no locals found when storing %s` | STORE_NAME |
| `0x4a` | — | 0x48e790 | `no locals when loading %s` | LOAD_NAME |
| `0x4c` | 23 | 0x48d6d8 | `local variable ... before assignment` | LOAD_FAST / LOAD_DEREF |
| `0x4e` | — | 0x48eac6 | `__import__ not found` | IMPORT_NAME |
| `0x57` | 306 | 0x48eadc | `__import__ not found` | IMPORT_NAME |
| `0x5b` | 23 | 0x48eb12 | `__import__ not found` | IMPORT_NAME |
| `0x5d` | — | 0x48e620 | `name '%.200s' is not defined` | LOAD_NAME |
| `0x62` | — | 0x48f28a | `XXX lineno / unknown opcode` | 兜底 default |

> ⚠️ `0x3b` / `0x62` 是**桩函数**：`0x62` 的 handler 就是 `default:` 的报错路径被塞进表尾。
> 它们不是独立 opcode。这张表是「数据结构 + 桩」混杂，**不能简单按一项 = 一个 opcode 读**。

---

## 五、B 组：表外、需读 case 体（18 种）

`0x6c 0x7e 0x83 0x91 0xad 0xbb 0xbf 0xc4 0xca 0xcd 0xcf 0xd3 0xd6 0xdb 0xde 0xe3 0xf1 0xf2`

这些在 `eval_full.c` 里有独立 case 分支，逐个读 case 体即可定名。按已掌握的上下文画像，
其中若干已可高置信推定：

| custom | 次数 | 上下文画像（前驱 → 自身 → 后继） | 推定 |
| --- | --- | --- | --- |
| `0x6c` | 4,347 | `CALL_FUNCTION`/`UNPACK_SEQUENCE` → **自身** → `STORE_FAST`(99.2%) | STORE_* 族（多值赋值） |
| `0xd3` | 3,473 | `STORE_FAST`/`POP_TOP`/`STORE_GLOBAL` → **自身** → `LOAD_FAST`+`LOAD_GLOBAL` | 语句边界类（SETUP_*） |
| `0xde` | 3,297 | `LOAD_FAST`/`LOAD_ATTR` → **自身** → `0x37`/`LOAD_CONST`/`RETURN_VALUE` | 取值类 |
| `0x37` | 3,003 | `LOAD_FAST`/`0xde` → **自身** → `STORE_FAST`/`CALL_FUNCTION` | 取值类 |
| `0xca` | 2,577 | `START`/`STORE_*` → **自身** → `LOAD_FAST`/`STORE_FAST` | 函数入口类 |
| `0xc4` | 2,015 | `CALL_FUNCTION`/`LOAD_CONST` → **自身** → `LOAD_CONST`/`LOAD_GLOBAL`/`JUMP_FORWARD` | 分支/构建类 |
| `0xe3` | 1,362 | — | 待定 |

> 完整定名仍需逐个核对 `eval_full.c` 对应 case 的取值基址与 NULL 分支，
> 方法与 A 组一致（结构指纹，证据 B）。

---

## 六、总体进度

| 口径 | 数值 |
| --- | --- |
| 字节码实际出现的 opcode 种类 | **92 种** |
| 已定名（原 32 + 本次新增） | 51 种 |
| 待定名（A 组 45 中未定 + B 组 18） | 约 41 种 |
| 覆盖的指令条数占比 | 93.5% |
| **从未出现的空槽** | **116 个**（不属于缺口，可忽略） |

**结论：不存在「解不出来」的 opcode。** 63 种待定名项全部有明确出处
（45 个在分派表、18 个在 switch case），只是尚未逐个读代码起名。
补全它们即可把覆盖率从 93.5% 推到接近 100%。

---

## 七、下一步（按性价比排序）

1. **A 组 45 种**：直接读 `0x48f668` 表内 handler 的取值基址 + NULL 分支 —— 最省力。
2. **B 组 18 种**：读 `eval_full.c` 对应 case 体 —— 中等成本。
3. **回归验证**：补全后重跑 `verify_opcode_map.py`，看跳转对齐率是否仍为 100%、未识别指令是否归零。

---

## 八、交付物

| 文件 | 说明 |
| --- | --- |
| `低频opcode结论.md` | 本文档 |
| `extract_dispatch_table.py` | 从 mxmain.dll 提取 99 项分派表（手工 PE 解析） |
| `analyze_unknown_ops.py` | 63 种缺口 opcode 的全量频次 + 上下文画像 |
| `final_opcode_conclusion.py` | 综合结论生成器（表 + case + 频次三方交叉） |
| `opcode_map_final.json` | 机制级映射（分派表原值 / case 集 / 空槽集） |
| `ghidra-out/dispatch_table_raw.json` | 分派表原始数据 |

---

## 九、边界说明

- 结论基于 `mxmain.dll`（v1.1.50）静态提取 + `script.fls` 全量字节码频次统计。
- 「空槽」结论依赖全量扫描；若后期版本引入新 opcode，需重跑 `opcode_hist.json` 比对。
- 未重建可运行协议栈，产出用于停服客户端的格式与逻辑研究。
