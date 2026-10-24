# -*- coding: utf-8 -*-
"""MX-13 frozen S2C route inventory (strictly read-only on client source)."""
import ast, hashlib, json, pathlib, re, collections
ROOT=pathlib.Path(__file__).resolve().parents[2]
LIST=ROOT/"协议资料"/"字典"/"protocol_s2c_full.txt"
# External client snapshot is not part of the P0 Git tree; provide MX13_CLIENT_NET to regenerate elsewhere.
import os
CLIENT=pathlib.Path(os.environ.get("MX13_CLIENT_NET",r"C:\Users\Administrator\MX\客户端源码\src\net.py"))
OUT=ROOT/"docs"/"mx13"
OUT.mkdir(parents=True,exist_ok=True)
raw=LIST.read_bytes(); txt=raw.decode("utf-8-sig")
# Hard-fail on source drift: frozen P0 route evidence must never silently change denominator.
EXPECTED_LIST_SHA256="17c57cdb77300bc222d68fbbc9ed154af98763c4e5024b79c51bd7f76807e7e4"
EXPECTED_NET_SHA256="98929e57e440d40950a8b530c3ec006eeb5999b500eb732495e513534699aa31"
assert hashlib.sha256(raw).hexdigest()==EXPECTED_LIST_SHA256,"P0 S2C route list changed"
records=[]
for ln,line in enumerate(txt.splitlines(),1):
    x=line.strip()
    if not x or x.startswith("#"):continue
    cols=x.split()
    if cols[0].isdigit():tag=int(cols[0]);handler=cols[1]; category=cols[2] if len(cols)>2 else None
    else:tag=None;handler=cols[0];category=cols[1] if len(cols)>1 else None
    if "." not in handler:raise ValueError(f"Invalid route line {ln}: {x!r}")
    records.append(dict(id=f"R{len(records)+1:03}",line=ln,tag=tag,handler=handler,category=category))
client_bytes=CLIENT.read_bytes()
assert hashlib.sha256(client_bytes).hexdigest()==EXPECTED_NET_SHA256,"Client opcode dictionary changed: rebaseline with evidence"
source=client_bytes.decode("utf-8-sig")
start=re.search(r"(?m)^NET_FUNCTION_NAME\s*=\s*\{",source)
if not start:raise RuntimeError("NET_FUNCTION_NAME missing")
at=start.end()-1; depth=0; stop=None
for i in range(at,len(source)):
    if source[i]=="{":depth+=1
    if source[i]=="}":
        depth-=1
        if depth==0:stop=i+1;break
mapping=ast.literal_eval(source[at:stop])
byname=collections.defaultdict(list)
for opcode,v in mapping.items():
    if isinstance(opcode,int) and isinstance(v,tuple) and len(v)>=2:
        byname[f"{v[0]}.{v[1]}"].append(dict(opcode=opcode,tag=v[2] if len(v)>2 else None))
issues=[]
for r in records:
    matches=[m for m in byname.get(r["handler"],[]) if m["tag"]==r["tag"]]
    r["opcode"]=matches[0]["opcode"] if len(matches)==1 else None
    if len(matches)!=1:issues.append(dict(id=r["id"],handler=r["handler"],tag=r["tag"],candidate_matches=byname.get(r["handler"],[])))
outside=[dict(opcode=op,handler='.'.join(val[:2])) for op,val in sorted(mapping.items()) if isinstance(op,int) and isinstance(val,tuple) and len(val)>=2 and '.'.join(val[:2]) not in {r['handler'] for r in records}]
meta=dict(baseline="p0-accepted / 15dd282",source_list=str(LIST.relative_to(ROOT)),source_list_sha256=hashlib.sha256(raw).hexdigest(),client_mapping_origin=str(CLIENT),client_mapping_sha256=hashlib.sha256(client_bytes).hexdigest(),client_mapping_is_external_to_p0=True,raw_route_count=len(records),unique_handlers=len(set(r["handler"] for r in records)),unique_mapped_opcodes=len(set(r["opcode"] for r in records if r["opcode"] is not None)),unresolved=issues,client_mapping_outside_frozen_160=outside,routes=records)
(OUT/"route_inventory.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(dict(raw=meta["raw_route_count"],handlers=meta["unique_handlers"],mapped_opcodes=meta["unique_mapped_opcodes"],unresolved=len(issues),issues=issues[:30],client_mapping_entries=len(mapping),client_sha256=meta["client_mapping_sha256"]),ensure_ascii=False))
