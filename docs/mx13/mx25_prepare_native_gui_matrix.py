# -*- coding: utf-8 -*-
"""MX-25 native-GUI per-route verification *planning* baseline.

The 44 automated sendall proofs are NOT native-GUI send/Unpack/handler proofs.
Never copy synthetic/Python receiver results into GUI columns.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "S2C_EMISSION_COVERAGE_ADJUDICATED.json"
OUT = HERE / "MX25_GUI_ROUTE_VERIFICATION_BASELINE_160.csv"
NOT_TESTED = "NOT_TESTED"
FIELDS = [
    "route_id", "opcode", "handler", "automated_sendall_evidence",
    "native_gui_session_send", "native_client_tcp_receive",
    "native_client_unpack", "native_handler_executed",
    "native_gui_effect", "gui_parse_verified", "native_session_id",
    "parser_trace_ref", "server_send_trace_ref", "failure_or_blocker"
]

def main(check):
    original = SOURCE.read_bytes()
    report = json.loads(original.decode("utf-8"))
    assert report["accepted_numerator"] == 44
    assert report["denominator"] == 160
    assert report["gui_parse_status"] == NOT_TESTED
    assert len(report["routes"]) == 160
    assert len({(r["opcode"], r["handler"]) for r in report["routes"]}) == 160
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    for route in report["routes"]:
        row = dict.fromkeys(FIELDS, "")
        row.update(route_id=route["route_id"], opcode=route["opcode"],
                   handler=route["handler"],
                   automated_sendall_evidence=("PASS" if route["accepted"]
                                              else NOT_TESTED),
                   native_gui_session_send=NOT_TESTED,
                   native_client_tcp_receive=NOT_TESTED,
                   native_client_unpack=NOT_TESTED,
                   native_handler_executed=NOT_TESTED,
                   native_gui_effect=NOT_TESTED,
                   gui_parse_verified=NOT_TESTED,
                   failure_or_blocker=("No native same-session capture; automated "
                                       "send is not native parse"))
        writer.writerow(row)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    if check:
        assert OUT.exists() and OUT.read_bytes() == data, "stale GUI baseline"
    else:
        OUT.write_bytes(data)
    print(json.dumps({
        "status": "PASS_MATRIX_GATE_ONLY",
        "routes": 160, "automated_sent": 44,
        "native_parse_status": NOT_TESTED,
        "native_parse_verified": None,
        "origin_sha256": hashlib.sha256(original).hexdigest(),
        "matrix_sha256": hashlib.sha256(data).hexdigest(),
        "mode": "CHECK" if check else "GENERATE"
    }))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    main(ap.parse_args().check)
