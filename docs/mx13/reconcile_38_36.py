# -*- coding: utf-8 -*-
"""MX-28: verify static identities and produce cross-branch candidate audit; never auto-merge numerators."""
import csv, hashlib, json, pathlib
HERE=pathlib.Path(__file__).resolve().parent
AA=pathlib.Path(r"C:\Users\Administrator\MX-p67\S2C_EMISSION_COVERAGE.json")
BB=HERE/"S2C_EMISSION_COVERAGE_EXPANDED.json"
a=json.loads(AA.read_text(encoding="utf-8"))
b=json.loads(BB.read_text(encoding="utf-8"))
inventory=json.loads((HERE/"route_inventory.json").read_text(encoding="utf-8"))
assert a["static_routes"]==b["raw_static_routes"]==160
assert a["registry_source_sha256"]==b["client_mapping_sha256"]==inventory["client_mapping_sha256"]
assert b["route_list_sha256"]==inventory["source_list_sha256"]
ma={(int(x["opcode"]),x["handler"]):x for x in a["routes"]}
mb={(int(x["opcode"]),x["handler"]):x for x in b["routes"]}
assert len(ma)==len(mb)==160 and ma.keys()==mb.keys()
sa={k for k,v in ma.items() if v["automated_emission_status"]=="PASS"}
sb={k for k,v in mb.items() if v["status"]=="PASS"}
both=sa&sb
ao=sa-sb
bo=sb-sa
assert (len(sa),len(sb),len(both),len(ao),len(bo),len(sa|sb))==(38,36,30,8,6,44)
rows=[]
for k in sorted(mb):
  ar=ma[k]
  br=mb[k]
  membership="BOTH" if k in both else ("P67_ONLY" if k in ao else ("MX13_ONLY" if k in bo else "NEITHER"))
  rows.append(dict(route_id=br["route_id"],opcode=k[0],handler=k[1],tag=br["tag"],
                   p67_route_id=ar["route_id"],p67_status=ar["automated_emission_status"],
                   mx13_status=br["status"],comparison=membership,
                   candidate_union="YES" if k in sa|sb else "NO",
                   verified_merged="NOT_TESTED",
                   native_gui_parse="NOT_TESTED",
                   mx13_scenarios=br["scenarios"],
                   p67_trigger_tests="|".join(ar.get("trigger_tests",[]))))
with (HERE/"MX13_CROSS_BRANCH_160_RECONCILIATION.csv").open("w",newline="",encoding="utf-8-sig") as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
data=dict(task="MX-28",status="CANDIDATE_ONLY",p0="p0-accepted/15dd282",
          p67_commit="7db4df1",mx13_commit="4d8c236",
          source_sha256={"p67_report":hashlib.sha256(AA.read_bytes()).hexdigest(),
                         "mx13_report":hashlib.sha256(BB.read_bytes()).hexdigest(),
                         "client_registry":inventory["client_mapping_sha256"],
                         "p0_route_list":inventory["source_list_sha256"]},
          counts=dict(p67=38,mx13=36,common=30,p67_only=8,mx13_only=6,candidate_union=44,neither=116),
          p67_all_tests_passed=a["all_tests_passed"],p67_status=a["status"],
          verified_merged_coverage=False,native_gui_parse="NOT_TESTED",
          known_failure="_test_singleplayer timeout waiting for opcode 112; failed test emissions excluded from P67 numerator",
          p67_only=[dict(route_id=mb[k]["route_id"],opcode=k[0],handler=k[1]) for k in sorted(ao)],
          mx13_only=[dict(route_id=mb[k]["route_id"],opcode=k[0],handler=k[1]) for k in sorted(bo)])
(HERE/"MX13_CROSS_BRANCH_44_CANDIDATES.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["# MX-28: 36-vs-38 cross-branch route reconciliation","",
       "Both sources match the external client NET_FUNCTION_NAME dictionary SHA256.",
       "P67 is PARTIAL with a failed singleplayer opcode112 test; MX13 has a clean 65/65 P0 regression.","",
       "| Group | Routes |","|---|---:|","| Common | 30 |","| P67 only | 8 |","| MX13 only | 6 |",
       "| Candidate union | 44 |","| Neither | 116 |","",
       "44 is a candidate union only. It is NOT an accepted coverage numerator or native GUI parse result.","",
       "P67 only (route/opcode):"]
lines+=["- %s / %s"%(mb[k]["route_id"],k[0]) for k in sorted(ao)]
lines+=["","MX13 only (route/opcode):"]
lines+=["- %s / %s"%(mb[k]["route_id"],k[0]) for k in sorted(bo)]
lines+=["","Full 160-row details: MX13_CROSS_BRANCH_160_RECONCILIATION.csv.",
        "Re-check original successful-send event-to-test provenance and P67 failing test exclusion before claiming new coverage."]
(HERE/"MX13_CROSS_BRANCH_44_CANDIDATES.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps(data["counts"]))
