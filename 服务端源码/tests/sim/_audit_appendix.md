### 附录 A · `hero.ITEM_ALL` 索引真值表（75 项，客户端 `src/hero.py:217`）

S2CPropChange（op17）的 `type` 字段就是本表下标，服务端下发的每个属性变更都按此定位。

| 下标 | 字段 | 解码类 | 类型/宽度 |
|---:|---|---|---|
| 0 | `m_Icon` | CHeroPropItem | TBYTE |
| 1 | `m_Shape` | CShapeItem | TWORD |
| 2 | `m_Desc` | CDescItem | TDESC |
| 3 | `m_Sex` | CHeroPropItem | TBYTE |
| 4 | `m_Name` | CHeroPropItem | 20 |
| 5 | `m_Grade` | CHeroPropItem | TBYTE |
| 6 | `m_Cash` | CCashItem | TLONG |
| 7 | `m_Saving` | CSavingItem | TDWORD |
| 8 | `m_Exp` | CExpItem | TDWORD |
| 9 | `m_NextExp` | CNextExpItem | TDWORD |
| 10 | `m_NeedExp` | CHeroPropItem | TDWORD |
| 11 | `m_Class` | CHeroPropItem | TBYTE |
| 12 | `m_Country` | CHeroPropItem | TBYTE |
| 13 | `sOrg` | COrgItem | 20 |
| 14 | `m_EmoteForTitle` | CHeroPropItem | TBYTE |
| 15 | `m_Title` | CTitleItem | 22 |
| 16 | `m_HP` | CHeroPropMultiItemText | TWORD |
| 17 | `m_HPMax` | CHeroPropMultiItemText | TWORD |
| 18 | `m_MP` | CHeroPropMultiItemText | TWORD |
| 19 | `m_MPMax` | CHeroPropMultiItemText | TWORD |
| 20 | `m_FP` | CHeroPropMultiItemText | TWORD |
| 21 | `m_FPMax` | CHeroPropMultiItemText | TWORD |
| 22 | `m_SP` | CHeroPropMultiItemText | TWORD |
| 23 | `m_SPMax` | CHeroPropMultiItemText | TWORD |
| 24 | `m_Huoli` | CHeroPropMultiItemText | TWORD |
| 25 | `m_Huoli_Max` | CHeroPropMultiItemText | TWORD |
| 26 | `m_Tili` | CHeroPropMultiItemText | TWORD |
| 27 | `m_Tili_Max` | CHeroPropMultiItemText | TWORD |
| 28 | `m_Att_All` | CHeroPropItem | TWORD |
| 29 | `m_Def_All` | CHeroPropItem | TWORD |
| 30 | `m_Spe_All` | CHeroPropItem | TWORD |
| 31 | `m_Magdef_All` | CHeroPropItem | TWORD |
| 32 | `m_Str_All` | CHeroPropItem | TWORD |
| 33 | `m_Mag_All` | CHeroPropItem | TWORD |
| 34 | `m_Cha_All` | CHeroPropItem | TWORD |
| 35 | `m_Dex_All` | CHeroPropItem | TWORD |
| 36 | `m_Con_All` | CHeroPropItem | TWORD |
| 37 | `m_Point` | CHeroPropItem | TWORD |
| 38 | `m_Switch` | CHeroPropItem | TDWORD |
| 39 | `m_GridPoint` | CHeroPropItem | TBYTE |
| 40 | `m_SkillExp` | CHeroPropItem | TDWORD |
| 41 | `m_PFPoint` | CHeroPropItem | TDWORD |
| 42 | `m_StoryPoint` | CHeroPropItem | TWORD |
| 43 | `m_Gold` | CHeroPropItem | TDWORD |
| 44 | `m_UI` | CHeroPropItem | TDWORD |
| 45 | `m_DoubleExp` | CHeroPropItem | TDWORD |
| 46 | `m_BuChangExp` | CHeroPropItem | TDWORD |
| 47 | `m_OrgOffer` | CHeroPropMultiItemText | TDWORD |
| 48 | `m_OrgOffer_Max` | CHeroPropMultiItemText | TDWORD |
| 49 | `m_OrgCoin` | CHeroPropItem | TDWORD |
| 50 | `m_CookPoint` | CHeroPropItem | TDWORD |
| 51 | `m_FightCash` | CHeroPropItem | TDWORD |
| 52 | `m_ShenWuBi` | CHeroPropItem | TDWORD |
| 53 | `m_SummonMax` | CHeroPropItem | TBYTE |
| 54 | `m_BagMax` | CBagMaxItem | TBYTE |
| 55 | `m_VIP` | CHeroPropItem | TBYTE |
| 56 | `m_FlyFlag` | CHeroPropItem | TBYTE |
| 57 | `m_Child` | CHeroPropItem | TDWORD |
| 58 | `m_GradeLimit` | CHeroPropItem | TBYTE |
| 59 | `m_Force` | CHeroPropItem | TBYTE |
| 60 | `m_TitleColor` | CHeroPropItem | TBYTE |
| 61 | `m_RealShape` | CHeroPropItem | TWORD |
| 62 | `m_XiulianTotalNeed` | CHeroPropItem | TBYTE |
| 63 | `m_PhotoEffect` | CHeroPropItem | TDWORD |
| 64 | `m_IsGuestLogin` | CHeroPropItem | TDWORD |
| 65 | `m_Birthday` | CHeroPropItem | TWORD |
| 66 | `m_AutoUpgrade` | CHeroPropItem | TWORD |
| 67 | `m_Resist_Xian` | CHeroSignedPropItem | TBYTE |
| 68 | `m_Resist_Gui` | CHeroSignedPropItem | TBYTE |
| 69 | `m_Resist_Yao` | CHeroSignedPropItem | TBYTE |
| 70 | `m_Resist_Zhi` | CHeroSignedPropItem | TBYTE |
| 71 | `m_Resist_Nian` | CHeroSignedPropItem | TBYTE |
| 72 | `m_At` | CHeroPropItem | TBYTE |
| 73 | `m_Df` | CHeroPropItem | TBYTE |
| 74 | `m_Dp` | CHeroPropItem | TBYTE |

### 附录 B · 服务端实际下发的 opcode 通道（45 条静态；另有 op15 / op192 两条动态通道经 `build_server_time()` 复用）

| op | 服务端常量 | 客户端模块 | 处理函数 | 判定 |
|---:|---|---|---|---|
| 1 | OP_HELLO | netlogin | S2CHello | OK |
| 2 | OP_ACCOUNT_INFO | netlogin | S2CLoginAccountInfo | OK |
| 3 | OP_ROLE_INFO | netlogin | S2CLoginRoleInfo | OK |
| 6 | OP_PLAYER_ADD | netlogin | S2CLoginPlayerAdd | OK |
| 7 | OP_ACCOUNT_MANAGE | netlogin | S2CAccountManage | OK |
| 16 | OP_PROP_INIT | nethero | S2CPropInit | OK |
| 17 | OP_PROP_CHANGE | nethero | S2CPropChange | OK |
| 18 | OP_PROP_RESIST | nethero | S2CPropResist | OK |
| 19 | OP_PROP_POWER | nethero | S2CPropPower | OK |
| 29 | OP_BANDWORD | banword | S2CBandWord | OK |
| 32 | OP_ITEM_KEY | netitem | S2CItemKey | OK |
| 33 | OP_ITEM_ADD | netitem | S2CItemAdd | OK |
| 34 | OP_ITEM_DEL | netitem | S2CItemDel | OK |
| 35 | OP_ITEM_QUANTITY | netitem | S2CItemQuantity | OK |
| 36 | OP_ITEM_POS | netitem | S2CItemPos | OK |
| 38 | OP_ITEM_STRING | netitem | S2CItemString | OK |
| 48 | OP_SUMMON_INFO | netpet | S2CSummonInfo | OK |
| 49 | OP_SUMMON_SKILL | netpet | S2CSummonSkill | OK |
| 50 | OP_SUMMON_FIGHT | netpet | S2CSetFight | OK |
| 51 | OP_PROP_VIEW | netpet | S2CPropView | OK |
| 53 | OP_SUMMON_KEY | netpet | S2CSummonKey | OK |
| 65 | OP_SKILL_LEVEL | nethero | S2CSkillLevel | OK |
| 72 | OP_TASK_ADD | nettask | S2CTaskAdd | OK |
| 73 | OP_TASK_REMOVE | nettask | S2CTaskRemove | OK |
| 84 | OP_GROUP | netgroup | S2CGroup | OK |
| 86 | OP_MAP_TRACK | netmap | S2CMapTrack | OK |
| 87 | OP_PRODUCE | netliving | S2CProduce | OK |
| 89 | OP_OPENUI | netopenui | S2COpenUI | OK |
| 96 | OP_MAP_SCENE_M | netmap | S2CMapSceneM | OK |
| 97 | OP_MAP_GOTO_M | netmap | S2CMapGotoM | OK |
| 99 | OP_MAP_ADD_NPC | netmap | S2CMapAddNpc | OK |
| 112 | OP_WAR_START | netwar | S2CWarStart | OK |
| 113 | OP_WAR_END | netwar | S2CWarEnd | OK |
| 114 | OP_WAR_TURN | netwar | S2CWarTurn | OK |
| 115 | OP_WAR_ADD | netwar | S2CWarAdd | OK |
| 116 | OP_WAR_DEL | netwar | S2CWarDel | OK |
| 117 | OP_WAR_ATTACK_NORMAL | netwar | S2CWarAttackNormal | OK |
| 119 | OP_WAR_ATTACK_NORMAL_END | netwar | S2CWarAttackNormalEnd | OK |
| 121 | OP_WAR_PROTECT | netwar | S2CWarProtect | OK |
| 122 | OP_WAR_PERFORM | netwar | S2CWarPerform | OK |
| 123 | OP_WAR_WSTATE | netwar | S2CWarWState | OK |
| 125 | OP_WAR_HP_PERCENT | netwar | S2CWarHPPercent | OK |
| 147 | S2C_NPC_BUYLIST | netnpc | S2CNpcBuyList | OK |
| 152 | OP_NPC_CHAT_EXT | netnpc | S2CNpcChatExt | OK |
| 250 | 字面量 | - | - | 白名单（非 S2C 分派） |
