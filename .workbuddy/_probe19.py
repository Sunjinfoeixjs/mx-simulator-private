import sys
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
B = O.BattleOracle.__new__(O.BattleOracle)
print('_expect_att hero =', repr(O.BattleOracle._expect_att('hero','hero')))
print('_expect_att pet =', repr(O.BattleOracle._expect_att('pet','pet')))
mine = {O.BattleOracle._expect_att('hero','hero'), O.BattleOracle._expect_att('pet','pet')}
print('mine =', mine, 'types', {type(x).__name__ for x in mine})
print('1 in mine?', 1 in mine, ' 2 in mine?', 2 in mine)
print('HERO_WAR_ID', m.HERO_WAR_ID, type(m.HERO_WAR_ID).__name__)
print('PET_WAR_ID', m.PET_WAR_ID, type(m.PET_WAR_ID).__name__)