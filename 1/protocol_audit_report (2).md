# 协议资料自动审计报告

> 此报告是静态元数据检查，不证明数据包在真实网络环境中正确。

## 1. 资料统计

- C2S/GS2C 定义项：**123**；不区分方向的不同编号：**121**。
- S2C 路由：**160**。
- 源码函数记录：**1504**，其中直接标注 Opcode 的 C2S 函数：**894**。
- 战斗字段函数：**19**；物品/宠物等字段函数：**53**。

## 2. 文件间的一致性

- C2S/GS2C 文本清单：123 条；相对 map 定义：编号不一致 0 条，map 未收录在文本清单的名称 0 条。
- S2C 文本路由：160 条；相对 map 定义：仅文本存在 0 条，仅 JSON 存在 0 条。

## 3. 复用 Opcode（需要保留多变体）

| Opcode | 函数数 | 函数 |
|---:|---:|---|
| 23 | 3 | C2SBuyIB, C2SBuyIB2, C2SOpenIB |
| 34 | 3 | C2SItemUse, C2SItemsUse, C2SItemSummonUse |
| 71 | 16 | C2SSHMove, C2SPushSH, C2SPopSH, C2SSHItemString, C2SSHSetPwd, C2SSHPay, C2SUnlock, C2SSHClearPwd, C2SEnterSH, C2SRentSH, C2SReNameSH, C2SUpGradeStoreHouse, C2SOpenStoreHouse, C2SNormalToInsure, C2SInsureToNormal, C2SArrange |
| 89 | 18 | C2SPutItem, C2SGetItem, C2SModifyPrice, C2SGetShopList, C2SGetShopIntro, C2SLookShop, C2SGetItemInfo, C2SBuyItem, C2SSaving, C2SDraw, C2SSwithItem, C2SLock, C2SUnLock, C2SSetIntro, C2SSetAdFee, C2SChangeType, C2SSaveSign, C2SSendShopIdx |
| 176 | 2 | C2SLookWarrior, C2SWarLook |

> 相同 Opcode 不一定是冲突：可能有子命令、不同参数形态或不同方向。

## 4. 长度及字段异常

- 带有非固定长度字段的函数：**5**。
- 固定字段长度求和与 total_bytes 不一致：**0**（注意：此项不覆盖可变内容）。
- 非标准动态字段描述：**1**。
- 字段函数的 Opcode 映射缺失/冲突：**0**；函数源码索引中找不到同名同编号定义：**0**。

| 函数 | Opcode | 位置 | size | kind | value |
|---|---:|---:|---|---|---|
| C2SArrange | 71 | 1 | param:data | I | N:len |

### 可变字段明细

- `C2SSendSign` (Opcode 175): [(0, 'N:len', 'S', 'param:msg')]
- `C2SSetIntro` (Opcode 89): [(1, 'N:len', 'S', 'param:intro')]
- `C2SChangeType` (Opcode 89): [(2, 'N:len', 'S', 'param:name')]
- `C2SRename` (Opcode 130): [(1, 'N:len', 'S', 'param:name')]
- `C2SArrange` (Opcode 71): [(1, 'param:data', 'I', 'N:len')]

### 必须人工验证的高风险条目

- **Opcode 34**：`C2SItemUse`（8 字节固定字段）、`C2SItemsUse`（4 字节）、`C2SItemSummonUse`（9 字节）；无统一显式子命令，应验证服务端如何区分。
- **Opcode 176**：`C2SLookWarrior(order)` 与 `C2SWarLook(id)` 字段宽度相同但命名语义不同。
- **Opcode 71 子命令 33**：`C2SArrange` 的 `size="param:data", value="N:len"` 不符合其他字段的表示方式；与函数索引中对 `len(data)` 的读取描述比对后再修改。
- **Opcode 175**：`C2SSendSign` 包含可变长度字符串，但 `total_bytes=0` 不是完整包体长度。
- **方向字段**：C2S 与 GS2C 可能共用编号 174、181，不能在不分方向的字典中覆盖。

## 5. 服务器列表静态检查

- 区域分组：6；服务器记录：206。
- 名称/IP/端口完全重复的三元组：18 组（允许分区重复，但导入前需要定义去重规则）。
- 格式或特殊端口警告：6 条。

| 行号 | 原因 | 原始内容 |
|---:|---|---|
| 34 | 非主流端口 1111（需核实，不一定错误） | 战无不胜	111.111.111.111	1111	3		1月26日 |
| 59 | 非主流端口 1111（需核实，不一定错误） | 战无不胜	111.111.111.111	1111	3		1月26日 |
| 73 | 非主流端口 31343（需核实，不一定错误） | 普陀山	61.142.132.70	31343			 |
| 74 | 非主流端口 31343（需核实，不一定错误） | 缥缈峰	61.142.132.70	31343			 |
| 108 | IP 字段不是单独的合法地址（疑似漏分隔符） | 梦里水乡(网通)120.31.146.13	3134	3			 |
| 108 | 非主流端口 3（需核实，不一定错误） | 梦里水乡(网通)120.31.146.13	3134	3			 |

## 6. 建议的修复策略

1. **只读保留原件**：不要自动覆盖任何源文件。
2. **创建规范化消息定义**：以 `direction + opcode + 可选 subcmd/variant` 作为键。
3. **将动态字段显式建模**：例如 `length_expression`、`length_prefix`、`encoding`，未知值保持 `null`。
4. **验证包头/字节序/加密压缩**：不能从当前字段摘录中擅自确定。
5. **以客户端源码+本地模拟服证据修复歧义**：对高风险函数建立一条请求与对应响应的回归用例。
6. **分离战斗行为与数值规则**：Opcode 决定操作和序列化，不足以给出伤害、成长、命中等公式。

