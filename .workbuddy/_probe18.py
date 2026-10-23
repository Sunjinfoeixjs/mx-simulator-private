import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'o12'); os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
port = srv.serve_background(0, web=False); time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)
for i in range(2):
    if not R.in_war: break
    row = B.attack_step('hero', None, 'hero', 160, bytes([1]), window=1.0)
    st = row['step']
    B.absorb(st.frames)
    print('r%d nframes=%d' % (i, len(st.frames)))
    turn_at = next((k for k,(op,_b) in enumerate(st.frames) if op==114), None)
    print('   turn_at(114) =', turn_at)
    mine = {100,250}
    chains = O.attack_chains(st.frames)
    for c in chains: print('   chain i=%d att=%s mine=%s' % (c['i'], c['att'], c['att'] in mine))
    print('   units:', {k: v['allied'] for k, v in B.units.items()})
    er = B.enemy_rows(st)
    print('   enemy_rows ->', len(er))
    b = len(st.frames); t = turn_at
    if t is not None:
        for c in chains:
            if c['i'] > t and c['att'] in mine: b = c['i']; break
    print('   computed bound =', b, 'chains in bound:', len(O.attack_chains(st.frames[:b])))
R.close(); srv.stop()