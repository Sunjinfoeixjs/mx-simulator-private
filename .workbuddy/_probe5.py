import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'oracle')
os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None
srv._persist()
port = srv.serve_background(0, web=False)
time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)
print('units:');
for w, u in sorted(B.units.items()):
    print('   id=%d name=%-8s allied=%s owner=%s' % (w, u['name'], u['allied'], u.get('owner_id')))

# 主角普攻：C2S 160 target
foe = sorted(w for w, u in B.units.items() if not u['allied'])
hero = sorted(w for w, u in B.units.items() if u['allied'])
print('foe', foe, 'ally', hero)
row = B.attack_step('hero', foe[0], 'hero', 160, bytes([foe[0]]), note='主角普攻(无防御)')
print('damage actual =', row['actual'], ' reverse =', row['reverse'], ' linear =', row['linear'])

# 全灭胜利
srv.gm_war_win(); R.collect(0.8)
txt, np, nt = B.report()
print(); print(txt)
R.close(); srv.stop()