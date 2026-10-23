import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O
tmp = os.path.join(os.environ['TEMP'], 'o13'); os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
port = srv.serve_background(0, web=False); time.sleep(0.3)
R = O.StepRunner('127.0.0.1', port, timeout=4.0); R.hello(); R.enter_world()
B = O.BattleOracle(R, m)
srv.gm_war_start(); fr = R.collect(0.8); B.absorb(fr)
row = B.attack_step('hero', None, 'hero', 160, bytes([1]), window=1.0)
row = B.attack_step('hero', None, 'hero', 160, bytes([1]), window=1.0)
st = row['step']
# manual replay of enemy_rows internals
mine = {100, 250}
errs = []
t = next((k for k,(op,_b) in enumerate(st.frames) if op==114), None)
chains = O.attack_chains(st.frames)
b = len(st.frames)
if t is not None:
    for c in chains:
        if c['i'] > t and c['att'] in mine: b = c['i']; break
chains = O.attack_chains(st.frames[:b])
print('bound', b, 'chains', len(chains))
for c in chains:
    print('  att=%s inmine=%s -> skip' % (c['att'], c['att'] in mine) if c['att'] in mine else '  att=%s vic=%s chg=%s -> consider' % (c['att'], c['vic'], c['chg']))
    if c['att'] in mine: continue
    wid = c['vic']; chg = c['chg']
    if chg is None: print('     chg None -> skip'); continue
    u = B.units.get(wid) or {}
    print('     units[%s]=%s allied=%s' % (wid, u.get('name'), u.get('allied')))
    if u.get('allied') is not True: print('     not allied -> skip'); continue
    print('     WOULD EMIT ROW')
R.close(); srv.stop()