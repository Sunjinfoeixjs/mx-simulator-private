import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'o10'); os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
port = srv.serve_background(0, web=False); time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)
st = R.step('hero_attack', 'p', 160, bytes([1]), '', 1.0)
B.absorb(st.frames)
er = B.enemy_rows(st)
print('enemy rows:', len(er))
for r in er: print('   ', r['who'], r['target'], r['actual'], r['reverse'])
mine = {100, 250}
chains = O.attack_chains(st.frames)
for c in chains: print('chain att=%s vic=%s chg=%s inmine=%s' % (c['att'], c['vic'], c['chg'], c['att'] in mine))
print('units:', {k: v['allied'] for k,v in B.units.items()})
R.close(); srv.stop()