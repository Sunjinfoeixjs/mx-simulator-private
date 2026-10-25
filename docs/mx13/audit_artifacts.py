# -*- coding: utf-8 -*-
"""Audit MX-13 artifacts and P0 test stats without changing them."""
import csv,json,pathlib,re,sys
HERE=pathlib.Path(__file__).resolve().parent
d=json.loads((HERE/"S2C_EMISSION_COVERAGE.json").read_text(encoding="utf-8"))
rows=list(csv.DictReader((HERE/"S2C_ROUTE_STATUS.csv").open(encoding="utf-8-sig",newline="")))
raw_events=[json.loads(x) for x in (HERE/"emission_socket_actual.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
full=(HERE/"regression_full_p0_junction_client.log").read_text(encoding="utf-8-sig",errors="replace")
failed=(HERE/"regression_full_p0_initial_fail.log").read_text(encoding="utf-8-sig",errors="replace")
missing_assets=(HERE/"regression_full_p0_external_client.log").read_text(encoding="utf-8-sig",errors="replace")
assert len(rows)==160
assert sum(x["status"]=="PASS" for x in rows)==26
assert sum(x["status"]=="NOT_TESTED" for x in rows)==134
assert len({x["opcode"] for x in rows})==160
assert len([e for e in raw_events if e["status"]=="SENT"])==98
assert len(d["client_mapping_outside_160"])==8
assert d["confirmed_gui_decoded_routes"]==0 and d["gui_metric_status"]=="NOT_TESTED"
final_stats=dict(re.findall(r"(?m)^\s*(steps_(?:passed|failed|total))\s*:\s*(\d+)\s*$",full))
assert final_stats=={"steps_passed":"65","steps_failed":"0","steps_total":"65"}, final_stats
initial_stats=dict(re.findall(r"(?m)^\s*(steps_(?:passed|failed|total))\s*:\s*(\d+)\s*$",failed))
assert initial_stats=={"steps_passed":"63","steps_failed":"2","steps_total":"65"},initial_stats
asset_stats=dict(re.findall(r"(?m)^\s*(steps_(?:passed|failed|total))\s*:\s*(\d+)\s*$",missing_assets))
assert asset_stats=={"steps_passed":"56","steps_failed":"9","steps_total":"65"},asset_stats
s={
 "result":"PASS","route_denominator":160,"actually_sent_unique_routes":26,"emitted_successful_frames":98,
 "python_client_decoded_routes":25,"gui_metric":"NOT_TESTED",
 "status_counts":{"PASS":26,"FAIL":0,"NOT_TESTED":134},
 "initial_full_p0":{"status":"FAIL","steps":initial_stats,"reason":"Cross-worktree path drift in two doc gates"},
 "no_client_asset_junction":{"status":"FAIL","steps":asset_stats,"reason":"External decompiled client data path absent; 9 dependent tests cannot run"},
 "final_full_p0":{"status":"PASS","steps":final_stats,"exit_code":0,"source":"regression_full_p0_junction_client.log","dependency":"Git-ignored client-source junction with pinned net.py SHA256"},
 "client_dictionary_extra_mappings":8
}
(HERE/"MX13_ACCEPTANCE_AUDIT.json").write_text(json.dumps(s,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(s,ensure_ascii=False))
