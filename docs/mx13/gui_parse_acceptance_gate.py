# -*- coding: utf-8 -*-
"""Gate for native-GUI S2C route parse attestations. No fabricated events.

Native parser hook and server socket recorder are external to this project.
Without both contemporaneous records status remains NOT_TESTED (exit code 2).
"""
import collections,datetime,hashlib,json,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent
GUI_DIR=HERE/"native_gui_live"
SENT=GUI_DIR/"server_physical_sent.jsonl"
PARSED=GUI_DIR/"native_client_parser.jsonl"
REPORT=HERE/"GUI_ROUTE_PARSE_ACCEPTANCE.json"
routes={r["id"]:r for r in json.loads((HERE/"route_inventory.json").read_text(encoding="utf-8"))["routes"]}
def load(p):
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()]
def save(result,exit_code):
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("status","verified_routes","reason")},ensure_ascii=False))
    return exit_code
def run():
    if not SENT.is_file() or not PARSED.is_file():
        return save(dict(status="NOT_TESTED",verified_routes=0,
            reason="Same-session physical-send and native-GUI decoder return events are both required",
            required_files=[str(SENT),str(PARSED)]),2)
    send=load(SENT);parsed=load(PARSED)
    assert send and parsed,"Missing non-empty actual evidence"
    delivered=collections.Counter()
    for e in send:
        if e.get("status")!="SENT" or e.get("boundary")!="socket.sendall":continue
        if e.get("capture_origin")!="live_native_GUI_server":continue
        if not all(e.get(k) for k in ("session_id","logical_payload_sha256","route_id","opcode")):continue
        if e["route_id"] not in routes or routes[e["route_id"]]["opcode"]!=e["opcode"]:continue
        delivered[(e["session_id"],e["route_id"],e["opcode"],e["logical_payload_sha256"])]+=1
    verified=[];rejected=[]
    for e in parsed:
        if e.get("origin")!="native_GUI_client_parser_hook" or e.get("parse_result")!="SUCCESS":
            rejected.append(dict(reason="untrusted_or_unsuccessful_parser_event"));continue
        if not (e.get("client_pid") and e.get("hook_module")=="mxmain.dll" and e.get("decoder_provenance_sha256")):
            rejected.append(dict(reason="native_hook_provenance_missing"));continue
        if e.get("session_id") is None or e.get("route_id") not in routes:
            rejected.append(dict(reason="route_or_session_missing"));continue
        r=routes[e["route_id"]]
        if e.get("opcode")!=r["opcode"] or e.get("handler")!=r["handler"]:
            rejected.append(dict(reason="opcode_handler_mismatch"));continue
        key=(e["session_id"],e["route_id"],e["opcode"],e.get("logical_payload_sha256"))
        if delivered[key]<=0:
            rejected.append(dict(reason="no_matched_successful_physical_send"));continue
        delivered[key]-=1
        verified.append(dict(route_id=e["route_id"],opcode=e["opcode"],
                             session_id=e["session_id"],handler=e["handler"]))
    result=dict(status="PARTIAL_VERIFIED" if verified else "NOT_TESTED",
        verified_routes=len({x["route_id"] for x in verified}),
        verified_route_ids=sorted({x["route_id"] for x in verified}),
        raw_denominator=160,
        rate_percent=round(100*len({x["route_id"] for x in verified})/160,4) if verified else None,
        matched_events=len(verified),
        rejected_client_events=len(rejected),
        rejected_reasons=dict(collections.Counter(x["reason"] for x in rejected)),
        reason="Only matching same-session server send and authenticated native parser return count")
    return save(result,0 if verified else 2)
if __name__=="__main__":sys.exit(run())
