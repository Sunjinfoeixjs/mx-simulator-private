import re
import sys

SPEC = (
    'm_Icon:1 m_Shape:2 m_Desc:? m_Sex:1 m_Name:f20 m_Grade:1 '
    'm_Cash:8 m_Saving:4 m_Exp:4 m_NextExp:4 m_NeedExp:4 m_Class:1 '
    'm_Country:1 sOrg:f20 m_EmoteForTitle:1 m_Title:f22 m_HP:2 m_HPMax:2 '
    'm_MP:2 m_MPMax:2 m_FP:2 m_FPMax:2 m_SP:2 m_SPMax:2 '
    'm_Huoli:2 m_Huoli_Max:2 m_Tili:2 m_Tili_Max:2 m_Att_All:2 m_Def_All:2 '
    'm_Spe_All:2 m_Magdef_All:2 m_Str_All:2 m_Mag_All:2 m_Cha_All:2 m_Dex_All:2 '
    'm_Con_All:2 m_Point:2 m_Switch:4 m_GridPoint:1 m_SkillExp:4 m_PFPoint:4 '
    'm_StoryPoint:2 m_Gold:4 m_UI:4 m_DoubleExp:4 m_BuChangExp:4 m_OrgOffer:4 '
    'm_OrgOffer_Max:4 m_OrgCoin:4 m_CookPoint:4 m_FightCash:4 m_ShenWuBi:4 '
    'm_SummonMax:1 m_BagMax:1 m_VIP:1 m_FlyFlag:1 m_Child:4 '
    'm_GradeLimit:1 m_Force:1 m_TitleColor:1 m_RealShape:2 m_XiulianTotalNeed:1 '
    'm_PhotoEffect:4 m_IsGuestLogin:4 m_Birthday:2 m_AutoUpgrade:2 '
    'm_Resist_Xian:1s m_Resist_Gui:1s m_Resist_Yao:1s m_Resist_Zhi:1s '
    'm_Resist_Nian:1s m_At:1 m_Df:1 m_Dp:1'
)

tokens = SPEC.split()
print('total tokens:', len(tokens))
idx = {}
for i, t in enumerate(tokens):
    name = t.split(':')[0]
    idx[name] = i
for n in ('m_Huoli', 'm_Huoli_Max', 'm_Tili', 'm_Tili_Max',
          'm_HP', 'm_Grade', 'm_Exp', 'm_NextExp', 'm_Gold', 'm_Point'):
    print('%-16s index=%d' % (n, idx.get(n, -1)))
