import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'oracle')
os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None
srv._persist()
port = srv.serve_background(0, web=False); time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)

# 一步一步打：主角普攻同一个目标，直到出结果（每次都会触发敌方行动）
tgt = 1
for i in range(12):
    if not srv.in_war: print('war ended at round', i); break
    row = B.attack_step('hero', tgt, 'hero', 160, bytes([tgt]), window=0.9)
    ops = [op for op, _b in row['step'].frames]
    # 敌方行动帧也会被吸收，逐个数出来
    for wid, chg in B.absorb(row['step'].frames):
        if wid in (100, 250):
            # 我方被打：补一行对照
            pass
    print('round %d: ops=%s actual_hero_dmg=%s' % (i, ops[:8], row['actual']))

txt, np, nt = B.report()
print(); print(txt)
print(); print(O.format_rows(B.rows[-4:], '最后 4 行'))
R.close(); srv.stop()