import sys
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import damage_model as dm

# 两个模型在标定点上一致，但在别处是否可分？
REV = dict(hero=(39,15,4,1), pet=(37,15,4,1), foe=(31,0,0,1))
LIN = dict(hero=(100,60,4,1), pet=(96,60,4,1), foe=(100,0,0,1))
print('%-22s %8s %8s %8s' % ('场景', 'reverse', 'linear', '差值'))
for name in ('hero','pet','foe'):
    a,d,p,l = REV[name]
    r = dm.physical_damage(a,d,p,l,model='reverse')
    a2,d2,p2,l2 = LIN[name]
    n = dm.physical_damage(a2,d2,p2,l2,model='linear')
    print('%-22s %8d %8d %8d' % (name+' 标定点', r, n, n-r))
print()
print('=== 关键：同一组入参喂两个模型 ===')
for a,d in ((39,0),(39,15),(39,30),(100,60),(60,40),(39,60)):
    r = dm.physical_damage(a,d,4,1,model='reverse')
    n = dm.physical_damage(a,d,4,1,model='linear')
    print('A=%-4d D=%-4d  reverse=%-4d linear=%-4d  差=%d' % (a,d,r,n,n-r))