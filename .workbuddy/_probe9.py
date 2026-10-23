import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'oracle3'); os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
port = srv.serve_background(0, web=False); time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)
for i in range(10):
    if not R.in_war: break
    row = B.attack_step('hero', None, 'hero', 160, b'', window=0.9, auto_target=True)
    for er in B.enemy_rows(row['step']):
        B.rows.append(er)
        print('r%d 我方受击: target=%s raw=%d base=%d reverse=%d' % (i, er['target'], er['raw_chg'], er['actual'], er['reverse']))
B.end_step('GM 一键胜利')
txt, np, nt = B.report()
print(); print(txt)
R.close(); srv.stop()