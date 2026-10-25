# -*- coding: utf-8 -*-
"""Final mechanical cross-audit of MX-13 expanded artifacts and original P0 regression."""
import collections,csv,hashlib,json,pathlib,re
P=pathlib.Path(__file__).resolve().parent
read=lambda name:json.loads((P/name).read_text(encoding="utf-8-sig"))
d=read("S2C_EMISSION_COVERAGE_EXPANDED.json")
rows=list(csv.DictReader((P/"S2C_ROUTE_STATUS_EXPANDED.csv").open(encoding="utf-8-sig",newline="")))
events=[]
for s in d["scenarios"]:
    raw=(P/s["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==s["sha256"],s["file"]
    e=[json.loads(x) for x in raw.decode("utf-8").splitlines() if x]
    events.extend(x for x in e if x["status"]=="SENT" and x["boundary"]=="socket.sendall")
assert len(events)==d["successful_physical_frames"]==sum(x["successful_frames"] for x in d["scenarios"])
ids={x["route_id"] for x in events if x.get("route_id")}
assert len(ids)==d["emitted_unique_routes"] and len(rows)==160
assert set(x["route_id"] for x in rows if x["status"]=="PASS")==ids
assert sum(x["status"]=="NOT_TESTED" for x in rows)==d["not_emitted_routes"]
assert all(len(x["logical_payload_sha256"])==64 for x in events)
assert d["applicable_routes"]==160 and not d["excluded_routes"]
crypto=read("mx13_crypto_results.json")
domain=read("emission_domains_results.json")
gui=read("GUI_ROUTE_EVIDENCE_CORROBORATION.json")
gate=read("GUI_ROUTE_PARSE_ACCEPTANCE.json")
assert crypto["status"]=="PASS" and crypto["independently_lzss_verified_frames"]==66
assert domain["status"]=="PASS" and domain["traces"]["craft_result"]["in_inventory"]
assert domain["traces"]["craft_result"]["shape"]==25201
assert gui["route_parse_confirmed"]==0 and gui["native_gui_screenshot_count"]==5
assert gate["status"]=="NOT_TESTED" and gate["verified_routes"]==0
text=(P/"regression_full_expanded_20261024.log").read_text(encoding="utf-8-sig",errors="replace")
stat=dict(re.findall(r"(?m)^\s*(steps_(?:passed|failed|total))\s*:\s*(\d+)\s*$",text))
assert stat=={"steps_passed":"65","steps_failed":"0","steps_total":"65"},stat
out=dict(result="PASS",baseline="p0-accepted/15dd282",static_routes=160,
    physical_successful_frames=len(events),emitted_unique_routes=len(ids),
    automated_percent=round(100*len(ids)/160,4),not_emitted=160-len(ids),
    native_gui_status="NOT_TESTED",native_gui_verified_routes=0,
    historic_gui_correlated_screenshots=5,
    crypto_lzss_verified_frames=66,
    craft_product=domain["traces"]["craft_result"],
    full_p0_regression=stat,
    scenarios=[{"name":x["name"],"sha256":x["sha256"]} for x in d["scenarios"]])
(P/"MX13_EXPANDED_ACCEPTANCE_AUDIT.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(out,ensure_ascii=False))
