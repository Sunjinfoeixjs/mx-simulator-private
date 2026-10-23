import sys
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import damage_model as dm, m4_server as m
print('m4 常量: HERO_AT=%d HERO_DF=%d HERO_DP=%d' % (m.HERO_AT, m.HERO_DF, m.HERO_DP))
print('         PET_AT=%d PET_DF=%d PET_DP=%d' % (m.PET_AT, m.PET_DF, m.PET_DP))
print('         ENEMY_AT=%d ENEMY_DF=%d ENEMY_DP=%d' % (m.ENEMY_AT, m.ENEMY_DF, m.ENEMY_DP))
print('DMG_MODEL=%r GAIN=%r MODE=%r ROUNDING=%r' % (m.DMG_MODEL, m.DMG_GAIN, m.DMG_MODE, m.DMG_ROUNDING))
print()
CASES = [('hero->foe', m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL),
         ('pet->foe',  m.PET_AT,  m.ENEMY_DF, m.PET_DP,  m.ENEMY_LEVEL),
         ('foe->hero', m.ENEMY_AT,m.HERO_DF,  m.ENEMY_DP,m.HERO_LEVEL),
         ('foe->pet',  m.ENEMY_AT,m.PET_DF,   m.ENEMY_DP,m.PET_LEVEL)]
print('%-11s %-16s %7s %7s %7s' % ('谁打谁','(A,D,P,Lt)','reverse','linear','差'))
for name, a, d, p, l in CASES:
    r = dm.physical_damage(a, d, p, l, model='reverse')
    n = dm.physical_damage(a, d, p, l, model='linear')
    print('%-11s %-16s %7d %7d %7d' % (name, '(%d,%d,%d,%d)'%(a,d,p,l), r, n, n-r))
print()
print('两个模型在**同一组入参**上差得很远 —— 这正是编排器要摊开给人看的。')