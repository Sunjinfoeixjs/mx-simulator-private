# -*- coding: utf-8 -*-
"""MX-28 fail-closed cross-branch S2C sendall evidence adjudication.

Emissions are admitted only from successful physical sendall observations in
PASS tests. FAILED tests are excluded, GUI parsing is never inferred.
Run: python docs/mx13/adjudicate_44_routes.py [--check]
"""
import argparse
import collections
import csv
import hashlib
import io
import json
import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
P67 = ROOT.parent / "MX-p67"
SOURCES = ("emission_socket_actual.jsonl", "emission_domains_actual.jsonl",
           "emission_encrypted_actual.jsonl")

def require(condition, reason):
    if not condition:
        raise AssertionError(reason)

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args],
                                   text=True).strip()

def valid_sha(x):
    return isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) is not None

def rows_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]

def main(check=False):
    p67 = P67
    require(p67.is_dir(), "P67 worktree unavailable")
    require(git(ROOT, "rev-parse", "p0-accepted^{commit}") ==
            git(p67, "rev-parse", "p0-accepted^{commit}"), "P0 commit mismatch")
    require(git(ROOT, "rev-parse", "p0-accepted^{commit}").startswith("15dd282"),
            "Unexpected frozen P0 commit")
    require(git(p67, "rev-parse", "--short", "HEAD") == "7db4df1",
            "P67 evidence branch changed: review required")

    previous = read_json(HERE / "MX13_CROSS_BRANCH_44_CANDIDATES.json")
    report_a_path = HERE / "S2C_EMISSION_COVERAGE_EXPANDED.json"
    report_b_path = p67 / "S2C_EMISSION_COVERAGE.json"
    report_a, report_b = read_json(report_a_path), read_json(report_b_path)
    require(digest(report_a_path) == previous["source_sha256"]["mx13_report"],
            "MX13 frozen report digest mismatch")
    require(digest(report_b_path) == previous["source_sha256"]["p67_report"],
            "P67 frozen report digest mismatch")
    require(report_a["client_mapping_sha256"] ==
            report_b["registry_source_sha256"] ==
            previous["source_sha256"]["client_registry"], "Client mapping mismatch")
    require(report_a["route_list_sha256"] ==
            previous["source_sha256"]["p0_route_list"], "Route list mismatch")
    require(report_a["raw_static_routes"] == report_b["static_routes"] == 160,
            "Denominator changed")
    require(not report_a["excluded_routes"] and report_b["excluded_routes"] == 0,
            "Denominator exclusions changed")
    require(report_b["status"] == "PARTIAL" and not report_b["all_tests_passed"],
            "P67 failing test status has changed; re-audit required")

    mx_routes = {r["opcode"]: r for r in report_a["routes"]}
    p67_routes = {r["opcode"]: r for r in report_b["routes"]}
    require(len(mx_routes) == len(p67_routes) == 160, "Duplicate opcodes")
    require({(o, r["handler"]) for o, r in mx_routes.items()} ==
            {(o, r["handler"]) for o, r in p67_routes.items()},
            "opcode/handler mapping mismatch")

    test_runs = {r["name"]: r for r in report_b["test_runs"]}
    failed = {k for k, r in test_runs.items() if r["status"] != "PASS"}
    require(failed == {"_test_singleplayer"}, "Unexpected P67 failing tests")
    excluded = report_b["excluded_failed_test_emissions"]
    require(len(excluded) == 1 and excluded[0]["name"] == "_test_singleplayer"
            and excluded[0]["excluded_success_events_from_failed_test"] == 67,
            "Failed scenario's 67 successful physical events not explicitly excluded")
    require(report_b["send_attempt_failures"] == 1 and
            not report_b["observer_errors"], "Send failures/observer errors changed")

    p67_event_file = p67 / "S2C_EMISSION_EVENTS.jsonl"
    events_b = rows_jsonl(p67_event_file)
    require(len(events_b) == report_b["all_emission_frames"] == 1474,
            "P67 event count mismatch")
    count_b = collections.Counter()
    by_test = collections.Counter()
    tests_b = collections.defaultdict(set)
    sample_b = collections.defaultdict(list)
    ids = set()
    unknown = collections.Counter()
    for e in events_b:
        t, op = e["test_name"], e["opcode"]
        require(t in test_runs and test_runs[t]["status"] == "PASS",
                "Non-PASS test event included: " + t)
        require(t not in failed, "Failed test event included")
        require(e["send_result"] == "SUCCESS" and
                e["send_boundary"] == "raw socket.sendall", "Non-success P67 send")
        require(e["frame_length"] > 0 and valid_sha(e["wire_sha256"])
                and e["call_path"], "P67 incomplete send metadata")
        eid = (t, e["event_id"])
        require(eid not in ids, "Duplicate event ID in P67 test")
        ids.add(eid)
        by_test[t] += 1
        if op not in p67_routes:
            require(e["route_id"] is None and e["handler"] is None,
                    "Unmapped P67 opcode has a route id")
            unknown[op] += 1
            continue
        r = p67_routes[op]
        require(e["handler"] == r["handler"] and e["route_id"] == r["route_id"],
                "P67 event route/handler mismatch")
        count_b[op] += 1
        tests_b[op].add(t)
        if len(sample_b[op]) < 3:
            sample_b[op].append(dict(test=t, event_id=e["event_id"],
                                     wire_sha256=e["wire_sha256"]))
    require(dict(unknown) == {250: 6}, "Heartbeat/outside-denominator events changed")
    require(sum(count_b.values()) == 1468 and len(by_test) == 9,
            "P67 valid event count changed")
    for t, n in by_test.items():
        require(n == test_runs[t]["success_events"], "P67 test frame count mismatch: " + t)
    # Independently verify original individual test receipts and their JSONL
    # against the aggregate: aggregate report PASS flags alone are insufficient.
    run_dir = p67 / "mx13_runs"
    p67_test_manifest = {}
    for t, declared in test_runs.items():
        if t == "test_s2c_emission.py":
            continue  # standalone fake-socket guard: never counted in emissions
        receipt_path = run_dir / (t + ".result.json")
        events_path = run_dir / (t + ".events.jsonl")
        receipt = read_json(receipt_path)
        original_events = rows_jsonl(events_path)
        require(receipt["name"] == t and receipt["status"] == declared["status"],
                "Individual test receipt status mismatch: " + t)
        require(receipt["success_events"] == declared["success_events"] ==
                len(original_events), "Individual test event count mismatch: " + t)
        if declared["status"] == "PASS":
            aggregated = [e for e in events_b if e["test_name"] == t]
            require(original_events == aggregated,
                    "Original individual JSONL differs from aggregate: " + t)
        else:
            require(t == "_test_singleplayer" and len(original_events) == 67
                    and not any(e["test_name"] == t for e in events_b),
                    "Failed test leaked into aggregate: " + t)
        p67_test_manifest[t] = {
            "status": receipt["status"],
            "success_frames_in_original_test_log": len(original_events),
            "receipt_sha256": digest(receipt_path),
            "original_events_sha256": digest(events_path)}
    require(len(p67_test_manifest) == 10, "Expected ten genuine test receipts")
    for op, r in p67_routes.items():
        require(count_b[op] == r["automated_emission_count"],
                "P67 per-route tally mismatch")
        require(tests_b[op] == set(r["trigger_tests"]),
                "P67 per-route triggering tests mismatch")

    ev_a, count_a = [], collections.Counter()
    samples_a = collections.defaultdict(list)
    for i, filename in enumerate(SOURCES):
        path = HERE / filename
        require(digest(path) == report_a["scenarios"][i]["sha256"],
                "MX13 scenario SHA mismatch: " + filename)
        block = rows_jsonl(path)
        require(len(block) == report_a["scenarios"][i]["successful_frames"],
                "MX13 scenario frame count mismatch")
        require(len({x["route_id"] for x in block}) ==
                report_a["scenarios"][i]["unique_routes"],
                "MX13 scenario distinct-route count mismatch")
        for e in block:
            op = e["opcode"]
            require(e["status"] == "SENT" and e["boundary"] == "socket.sendall",
                    "MX13 unsuccessful send included")
            require(op in mx_routes and e["route_id"] == mx_routes[op]["route_id"]
                    and e["handler"] == mx_routes[op]["handler"],
                    "MX13 event opcode/handler mismatch")
            frame_hash = e.get("frame_sha256") or e.get("recovered_wire_sha256")
            require(valid_sha(frame_hash) and e["frame_bytes"] > 0
                    and e["call_path"], "MX13 incomplete send metadata")
            require(e.get("physical_mode") is None or
                    e["physical_mode"].startswith("xor_"),
                    "Unexpected non-physical encryption mode")
            count_a[op] += 1
            if len(samples_a[op]) < 2:
                samples_a[op].append(frame_hash)
        ev_a.extend(block)
    require(len(ev_a) == report_a["successful_physical_frames"] == 251,
            "MX13 frame count mismatch")
    for op, r in mx_routes.items():
        require(count_a[op] == r["success_frames"], "MX13 per-route count mismatch")
        require((count_a[op] > 0) == (r["status"] == "PASS"),
                "MX13 status mismatch")

    combined = []
    for op, r in sorted(mx_routes.items()):
        x, y = count_a[op], count_b[op]
        status = ("BOTH_PASS" if x and y else "MX13_PASS" if x else
                  "P67_PASS" if y else "NOT_TESTED")
        combined.append(dict(route_id=r["route_id"], opcode=op,
             handler=r["handler"], tag=r.get("tag"), emission_status=status,
             accepted=(status != "NOT_TESTED"), mx13_success_events=x,
             p67_success_events_from_PASS_tests=y,
             p67_test_names=sorted(tests_b[op]),
             p67_event_samples=sample_b[op],
             mx13_frame_sha256_samples=samples_a[op],
             native_gui_parse="NOT_TESTED"))
    cats = collections.Counter(r["emission_status"] for r in combined)
    require(cats == {"BOTH_PASS": 30, "MX13_PASS": 6,
                     "P67_PASS": 8, "NOT_TESTED": 116},
            "Cross-branch grouping changed")
    p67_only = [r for r in combined if r["emission_status"] == "P67_PASS"]
    require({r["opcode"] for r in p67_only} ==
            {int(r["opcode"]) for r in previous["p67_only"]},
            "Unexpected P67-only route set")

    result = dict(schema=1, task="MX-28", metric="verified_successful_socket_sendall_unique_routes",
       status="PASS_EMISSION_ONLY", baseline="p0-accepted/15dd282",
       denominator=160, accepted_numerator=44, accepted_coverage_percent=27.5,
       previously_accepted_numerator=36, newly_admitted_routes=8, not_observed=116,
       groups=dict(cats), gui_parse_status="NOT_TESTED", gui_parse_accepted=None,
       p67_overall_test_status="PARTIAL", p67_failed_test="_test_singleplayer",
       p67_failed_test_events_excluded=67, p67_send_failures_excluded=1,
       p67_outside_route_registry=dict(unknown), p67_pass_test_frames=1474,
       p67_mapped_pass_test_frames=1468, mx13_pass_frames=251,
       p67_original_test_receipts=p67_test_manifest,
       merged_frame_count_not_applicable=True,
       source_sha256=dict(p67_events=digest(p67_event_file),
          p67_report=digest(report_b_path), mx13_report=digest(report_a_path),
          mx13_event_files={n:digest(HERE/n) for n in SOURCES},
          client_registry=report_a["client_mapping_sha256"],
          p0_route_list=report_a["route_list_sha256"]),
       accepted_p67_only=p67_only, routes=combined,
       limits=["Physical successful sendall is not TCP delivery confirmation.",
               "Python test receiver does not prove original GUI client decode.",
               "P67 full suite PARTIAL because _test_singleplayer failed.",
               "No original-DLL XOR/LZSS equivalence proof.",
               "This is a cross-run union of unique opcode/handler pairs, not frame counts."])
    accepted = [r for r in combined if r["accepted"]]
    require(len(accepted) == result["accepted_numerator"] == 44,
            "Numerator mismatch")
    json_out = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    buf = io.StringIO(newline="")
    fields = ["route_id","opcode","handler","emission_status","accepted",
              "mx13_success_events","p67_success_events_from_PASS_tests",
              "p67_test_names","native_gui_parse"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in combined:
        row = {k:r[k] for k in fields}
        row["p67_test_names"] = "|".join(r["p67_test_names"])
        writer.writerow(row)
    csv_out = ("\ufeff" + buf.getvalue()).encode("utf-8")
    md = [
      "# MX-28 | Cross-branch physical-emission evidence adjudication",
      "",
      "**PASS (physical socket.sendall emission metric only): 44/160 = 27.50%.**",
      "The prior 36/160 expands by 8 P67-only routes each evidenced by at least one",
      "real socket.sendall SUCCESS from an independently PASS test.",
      "",
      "| Group | Unique routes |",
      "|---|---:|",
      "| Both branches PASS | 30 |",
      "| MX13-only PASS | 6 |",
      "| P67-only PASS, newly admitted | 8 |",
      "| Not observed | 116 |",
      "",
      "## P67-only admitted routes (failed tests excluded)",
      "| Route | Opcode | Handler | PASS send events | PASS tests |",
      "|---|---:|---|---:|---|"]
    for r in p67_only:
        md.append("| {} | {} | {} | {} | {} |".format(
           r["route_id"],r["opcode"],r["handler"],
           r["p67_success_events_from_PASS_tests"],", ".join(r["p67_test_names"])))
    md.extend(["",
      "## Strict exclusions and limitations",
      "- The P67 `_test_singleplayer` test FAILED; its 67 emitted events are excluded",
      "  from the P67 accepted evidence stream. No accepted event cites that test.",
      "- One unsuccessful send attempt is excluded. Six opcode-250 heartbeat frames",
      "  are outside the frozen 160-route denominator and are not new routes.",
      "- No cross-branch frame totals are added: 251 and 1,474 belong to",
      "  separate runs. Only unique (opcode, handler) routes are unioned.",
      "- P67 full-suite status stays PARTIAL; this is not full-regression PASS.",
      "- Native-client GUI parser return remains NOT_TESTED, with no verified routes.",
      "- No original-client DLL XOR/LZSS equivalence is claimed.",
      "",
      "## Reproduction",
      "`python docs/mx13/adjudicate_44_routes.py --check`",
      "",
      "All source SHA256 values and 160 individual decisions are stored in",
      "`S2C_EMISSION_COVERAGE_ADJUDICATED.json` and",
      "`S2C_ROUTE_ADJUDICATION_160.csv`. Raw login frames are not copied.",
      ""])
    md_out = "\n".join(md).encode("utf-8")
    outputs = {
       HERE/"S2C_EMISSION_COVERAGE_ADJUDICATED.json":json_out,
       HERE/"S2C_ROUTE_ADJUDICATION_160.csv":csv_out,
       HERE/"MX28_EVIDENCE_ADJUDICATION.md":md_out}
    for path, raw in outputs.items():
        if check:
            require(path.exists() and path.read_bytes() == raw,
                    "Generated artifact missing or stale: " + path.name)
        else:
            path.write_bytes(raw)
    print(json.dumps({k:result[k] for k in (
       "status","denominator","accepted_numerator","accepted_coverage_percent",
       "newly_admitted_routes","not_observed","groups","p67_failed_test_events_excluded",
       "gui_parse_status")},ensure_ascii=False))
    print("OUTPUT MODE", "CHECK_PASS" if check else "WRITTEN")
    for path,raw in outputs.items():
        print(path.name,len(raw),hashlib.sha256(raw).hexdigest())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args()
    main(args.check)
