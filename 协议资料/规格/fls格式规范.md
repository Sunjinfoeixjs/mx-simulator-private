# .fls 资源归档格式规范（D1 探针结论）

> 2026-10-14 · 基于 4 个小归档（passive/effect/skill/war）只读探针 + mxmain.dll 反编译交叉验证
> 工具：`tools/fls_probe.py`（容器+条目统计）、`tools/_probe_deep.py` / `_probe_xor.py`（条目拆解）
> 权威依据：`tools/fls.py`（容器解析）+ `dll/export/decompiled_all.c`（原生加载函数）

## 1. 容器层（与 script.fls 完全同构）

```
0x00  char[4]  magic "0SLF"（小端读作 0x464C5330）
0x04  uint32   entry_count
0x08  uint32   table_offset（索引表绝对偏移）
0x0C  ...       载荷区（条目数据）
table_offset  entry_count × 16 字节索引条目（加密存储）
```

索引解密（`fls.py: decrypt_entry`，逐条目 i 从 0 计数）：

```
key = i * 0x0E7C83D2                                    (mod 2^32)
f3' = f3 ^ (f2 * i * 0x0F718FE + 0x0D59D16C)
f2' = (f0 * f1 + 0x0DF25E) ^ f2
f1' = (f0 + key) ^ f1
f0' = f0（明文，= 条目名哈希）
```

解密后字段语义（探针实测）：

| 字段 | 语义 | 证据 |
|---|---|---|
| f0 | 条目名哈希（32 位） | 值域远超文件大小（如 0x13E3D9BF > war.fls 8.4MB），非偏移 |
| f1 | 标志/子类型（通常 0） | 仅个别条目非零（war #99=66、#131=41721；effect #11=10773） |
| f2 | 载荷长度 | `f3+f2 ≤ size` 对 4 个归档 100% 成立 |
| f3 | 载荷文件内偏移 | 与 f2 配合读出全部条目 |

原生对应：`FUN_00448af0` / `FUN_00448c00`（打开 `data\<name>` → 读 12 字节头 → 校验 0x464C5330 → 读索引表 → 上述解密循环）。

## 2. 条目分类（按载荷首字节）

### 2.1 Info block（`49 30` / `49 31`）——主体资源数据

- 魔数 `49 30 01 00`（"I0" + 版本 1）；变体 `49 31 01 00`（"I1"）
- 26 字节共同结构头 + 变长内容：
  ```
  49 30 | 01 00 | <count/type: 2B> | <w1> <h1> | ... 9 字节 0x00 ... | <w2> <h2> <h3> | 00
  ```
  passive.fls 全 41 条目头部完全一致（`4930010001002c002c00000000000000...`，26B 前缀）；
  war/effect 的 count 与宽高字段逐条目不同（如 `4930010010006d008500350064002a00`）
- 内容含 0x0b 长度前缀的 ASCII 名字串 + 二进制记录块；尾部高熵数据疑似像素/调色板
- 由原生 `CGetInfo` 类族解析：`GetInfoBlock`(0x4486d0) / `GetString`(0x481a0) / `GetNumber`(0x48280) / `GetInfoString`(0x483e0) / `GetInfoNumber`(0x48530)——即"键值配置块"格式
- **规模**：passive 41 条全 2.5KB 级；war/effect 混合 2.5KB–169KB 大块与小块

### 2.2 IG 指针条目（`49 47 0D 0A` = "IG\r\n"）——归档内索引

skill.fls #60/#94 实测：

```
IG\r\nskill\r\n1621.igs        （21 字节）
IG\r\nskill\r\n1620.igs
```

即 `IG\r\n<归档名>\r\n<逻辑文件名>.igs`。磁盘上无 .igs 文件（`D:\mxsj2013\client\data\` 只有 15 个 .fls），故 .igs 为归档内逻辑名。

### 2.3 小模板条目（XOR 加密，13–212B）——结构层已破 ★

**分布**：passive 0/41；effect 19/30；skill 2/282；war 88/181。小条目在文件中**聚集在尾部**（war 的 88 条全部在 0x5F7153–0x5F8000 区间）。

**加密**：整条目 XOR 一个 4 字节密钥（逐条目不同）。

**密钥恢复**（三种途径，按可靠性排序）：
1. 模板头反推：`key = ENC[0:4] ^ 51 57 52 53`，验证 `ENC[4:8] ^ key == 67 54 53 6f`（war 系模板）
2. 计数器区众数组：载荷中反复出现 `cf 4X af a3` 形态的 4 字节组（X = 0x4e^counter），取众数组即密钥
3. 直接猜首组（效果差，仅 1 次出现时不可靠）

**解密后结构**（war 系，64 字节共享模板 + 变长尾）：

```
+0x00  51 57 52 53 67 54 53 6f     8 字节模板头（"QWRSgTSo"）
+0x08  00 01 00 00 00 00 00 00 ... 10 个 4 字节步长项，第二字节为 (counter ^ 1)：1,0,3,2,5,4,7,6,9,8
+0x30  00 00 10 12 38 3b 11 03 2a 03 2a 03 38 11 12 38   16 字节共同尾
+0x40  <逐条目变长尾>               war #2: 10B；#7/#9: 20B
```

**实测**：war #2(74B)/#7(84B)/#9(84B) 解密后前 64 字节完全一致；war #10/#12(37/41B) 为同模板短变体；effect 系模板变体不同（`00 00 00 00 36 03 01 3c` 前缀形态，effect #6/#9 实测）。

**语义未定**：非 GBK 文本、非 marshal、非 zlib、非标准 LZSS 首字节规则。模板语义待深挖（候选：游戏内部序列化配置；后续可结合 `ResLib.dll`（345KB，资源加载器）Ghidra headless 分析或 `CGetInfo` 函数族）。

### 2.4 与 script.fls 的差异对照

| 层 | script.fls（Python 模块） | 资源 .fls |
|---|---|---|
| 容器 | 0SLF + 加密索引 | 同左（同一套常数） |
| zlib | 全部条目 0x78 头 | 无（4 个小文件实测 0 命中） |
| 9 字节 XOR 头 | 有（`76 15×8`，key=0x15） | 无 |
| PRNG 解混淆 | 有（种子=模块名） | 无（资源条目不经过 Python 层） |
| marshal | 2.6 marshal 代码对象 | 无 |
| 小条目加密 | — | 4 字节 XOR 模板（见 2.3） |

## 3. 原生代码索引（mxmain.dll，供回退查询）

| 功能 | 地址 | 说明 |
|---|---|---|
| 容器打开 + 索引解密 | FUN_00448af0 / FUN_00448c00 | `data\<name>` → 0x464C5330 校验 |
| 自定义 LZSS 解压 | Decompress @0x447610 | 字面量运行（n+0x11 / n-3 / 0x00 扩展）+ 匹配 token（≥0x10，窗口 0x800/0x4000）+ 结束标记 `0x11 00 00`；用于回放文件/JPEG 子块，**小模板条目首字节不适配其初始字面量规则，未确认应用于 .fls 条目** |
| LZSS 压缩 | Compress @0x447c10 | token 格式权威定义处；输入 ≤13 字节特殊处理 |
| Info block 解析 | CGetInfo 族 0x481a0–0x4486d0 | GetString/GetNumber/GetInfoBlock |
| 资源类 | CRes / CResWithThreadLoad / CShare 0x447d10+ | 引用计数资源管理 |

## 4. 各归档条目统计（探针实测）

| 归档 | 条目数 | Info block | IG 指针 | 小模板 | 异常 |
|---|---|---|---|---|---|
| passive.fls | 41 | 41 | 0 | 0 | 无 |
| effect.fls | 30 | 11 | 0 | 19 | #6 起高熵；f1 非零 1 条 |
| skill.fls | 282 | 280 | 2 | 0 | #60/#94 为 IG 指针 |
| war.fls | 181 | 73 | 0 | 88 | #2（74B 高熵，已破）；f1 非零 2 条 |

其余 10 个归档（goods/misc/minimap/sound/item/mapobject/map/magic/interface/char）待 D2 批量解密时按同规范探针+验证。

## 5. 遗留问题（D1 之后跟进）

1. 小模板条目的语义（"QWRSgTSo" 模板头含义、计数器表用途、尾部结构）——候选路径：ResLib.dll Ghidra headless 分析
2. Info block 的键值解析器（GetString/GetNumber 语义 → 文本化导出）——D3 候选
3. f1 非零条目的特殊语义（war #99/#131、effect #11）
4. f0 名字哈希的字典反查（需要容器索引外的名字表，疑在 IG 条目或原生代码中）

## 6. 写入侧（2026-10-22 补齐：`tools/fls_pack.py`）

### 6.1 索引加密的逆

解密公式（§1）里 `nf3` 的掩码取**原始 f2**、`nf2` 的取**原始 f1**，所以逆推要用前一步的**密文**：

```
e0 = p0
e1 = p1 ^ ((p0 + i*0x0E7C83D2) & MASK)
e2 = p2 ^ ((p0 * e1 + 0x0DF25E) & MASK)                  # 用 e1
e3 = p3 ^ ((e2 * i * 0xF718FE + 0x0D59D16C) & MASK)      # 用 e2
```

### 6.2 两种写回布局

| 布局 | 做法 | 判据 |
|---|---|---|
| `preserve` | 载荷区原样拷贝 + 索引重加密 + 表位置不变 | 与磁盘字节**逐字节一致**（无损判据） |
| `compact` | 按索引顺序重排 payload，索引表放末尾 | 尺寸 = `12 + Σpayload + N*16 + 表尾` |

### 6.3 实测（`fls_pack.py --verify`）

passive(41) / effect(30) / skill(282) / war(181) / goods(119) 五个归档
`preserve` 全部**逐字节一致**，`compact` 载荷与 f0/f1 逐条一致。

**要点**：原包载荷区有条目间空隙（war.fls 实测 **41,787 B**），`compact` 会丢弃，
所以「新包大小 = 原大小 + delta」不成立，必须按 6.2 的公式核算。客户端按 `f3/f2` 定位条目，
理论上不依赖空隙（真机未验，见 `报告/fls写回与资源替换_20261022.md` §六）。
