#!/usr/bin/env python3
"""Run two small native RDMA smoke cases using only the Python standard library.

This checks completion and observed routing, not congestion-control performance.
Each new output directory retains the exact inputs, combined stdout/stderr,
per-case results, and summary.json. Existing output directories are never reused.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
TOPOLOGY_NAME = "leaf_spine_128_100G_OS2.txt"
TIMEOUT_SECONDS = 120
FLOW_COUNT = 16
FLOW_BYTES = 1_000_000
START_SECONDS = "0.001000000"
CASES = (("ecmp", 0), ("conweave", 9))


class SmokeFailure(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def flow_rows():
    return [(i % 4, 16 + i % 4, 3, FLOW_BYTES, START_SECONDS) for i in range(FLOW_COUNT)]


def write_inputs(case_dir, topology, lb_mode):
    # Relative paths also support an --output-dir containing spaces: the C++
    # configuration parser tokenizes filenames on whitespace.
    shutil.copyfile(str(topology), str(case_dir / TOPOLOGY_NAME))
    flows = [str(FLOW_COUNT)] + [" ".join(map(str, row)) for row in flow_rows()]
    (case_dir / "flows.txt").write_text("\n".join(flows) + "\n", encoding="utf-8")
    config = """TOPOLOGY_FILE {topology}
FLOW_FILE flows.txt
FLOW_INPUT_FILE input_log.txt
CNP_OUTPUT_FILE cnp.txt
FCT_OUTPUT_FILE fct.txt
PFC_OUTPUT_FILE pfc.txt
QLEN_MON_FILE qlen.txt
VOQ_MON_FILE voq.txt
VOQ_MON_DETAIL_FILE voq_per_dst.txt
UPLINK_MON_FILE uplink.txt
CONN_MON_FILE conn.txt
EST_ERROR_MON_FILE est_error.txt
FLOWGEN_START_TIME 0.001
FLOWGEN_STOP_TIME 0.01
QLEN_MON_START 1000000
QLEN_MON_END 10000000
SW_MONITORING_INTERVAL 1000
BUFFER_SIZE 9
CC_MODE 1
LB_MODE {lb_mode}
ENABLE_PFC 1
ENABLE_IRN 0
CONWEAVE_TX_EXPIRY_TIME 300
CONWEAVE_REPLY_TIMEOUT_EXTRA 4
CONWEAVE_PATH_PAUSE_TIME 16
CONWEAVE_EXTRA_VOQ_FLUSH_TIME 16
CONWEAVE_DEFAULT_VOQ_WAITING_TIME 200
ALPHA_RESUME_INTERVAL 1
RATE_DECREASE_INTERVAL 4
CLAMP_TARGET_RATE 0
RP_TIMER 300
FAST_RECOVERY_TIMES 1
EWMA_GAIN 0.00390625
RATE_AI 40Mb/s
RATE_HAI 100Mb/s
MIN_RATE 100Mb/s
DCTCP_RATE_AI 1000Mb/s
ERROR_RATE_PER_LINK 0
L2_CHUNK_SIZE 4000
L2_ACK_INTERVAL 1
L2_BACK_TO_ZERO 0
RATE_BOUND 1
HAS_WIN 0
VAR_WIN 0
FAST_REACT 0
MI_THRESH 0
INT_MULTI 1
GLOBAL_T 1
U_TARGET 0.95
MULTI_RATE 0
SAMPLE_FEEDBACK 0
ENABLE_QCN 1
USE_DYNAMIC_PFC_THRESHOLD 1
PACKET_PAYLOAD_SIZE 1000
LINK_DOWN 0 0 0
KMAX_MAP 1 100000000000 400
KMIN_MAP 1 100000000000 100
PMAX_MAP 1 100000000000 0.2
LOAD 10
RANDOM_SEED 1
""".format(topology=TOPOLOGY_NAME, lb_mode=lb_mode)
    (case_dir / "config.txt").write_text(config, encoding="utf-8")


def expected_completed_qps():
    source_ports = defaultdict(lambda: 10000)
    dest_ports = defaultdict(lambda: 100)
    result = Counter()
    for src, dst, _pg, size, _start in flow_rows():
        result[(src, dst, source_ports[src], dest_ports[dst], size)] += 1
        source_ports[src] += 1
        dest_ports[dst] += 1
    return result


def integer_rows(path, columns, delimiter=None):
    if not path.is_file():
        raise SmokeFailure("Missing output: {}".format(path.name))
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split(delimiter)
        if len(fields) != columns:
            raise SmokeFailure("{}:{}: expected {} fields".format(path.name, line_number, columns))
        try:
            result.append(tuple(int(field) for field in fields))
        except ValueError as exc:
            raise SmokeFailure("{}:{}: invalid integer field".format(path.name, line_number)) from exc
    return result


def validate(case_dir, lb_mode):
    fct = integer_rows(case_dir / "fct.txt", 8)
    if len(fct) != FLOW_COUNT:
        raise SmokeFailure("Completed {} of {} flows".format(len(fct), FLOW_COUNT))
    if Counter(tuple(row[:5]) for row in fct) != expected_completed_qps():
        raise SmokeFailure("Completed QP identities/ports/sizes differ from the fixed inputs")
    if len({tuple(row[:4]) for row in fct}) != FLOW_COUNT:
        raise SmokeFailure("Duplicate completed QP")
    if any(row[6] <= 0 for row in fct):
        raise SmokeFailure("A flow has nonpositive FCT")
    completed_bytes = sum(row[4] for row in fct)
    if completed_bytes != FLOW_COUNT * FLOW_BYTES:
        raise SmokeFailure("Completed payload is not 16,000,000 bytes")

    log = (case_dir / "run.log").read_text(encoding="utf-8", errors="replace")
    completion = re.search(r"finished so far:\s*(\d+)\s*/\s*total:\s*(\d+)", log)
    if not completion or tuple(map(int, completion.groups())) != (FLOW_COUNT, FLOW_COUNT):
        raise SmokeFailure("Log does not confirm completion of all 16 flows")

    # Columns: time_ns, ToR ID, output port, cumulative transmitted wire bytes.
    uplink = integer_rows(case_dir / "uplink.txt", 4, ",")
    if not uplink:
        raise SmokeFailure("No uplink samples")
    counters = {}
    for timestamp, tor, port, nbytes in uplink:
        if timestamp < 0 or tor < 0 or port <= 0 or nbytes < 0:
            raise SmokeFailure("Invalid uplink sample")
        previous = counters.get((tor, port))
        if previous and (timestamp < previous[0] or nbytes < previous[1]):
            raise SmokeFailure("Uplink timestamp or cumulative byte count regressed")
        counters[(tor, port)] = (timestamp, nbytes)
    used = defaultdict(dict)
    for (tor, port), (_timestamp, nbytes) in sorted(counters.items()):
        if nbytes:
            used[tor][port] = nbytes
    # Hosts 0..3 attach to ToR 128 in the unmodified repository topology.
    if not used.get(128):
        raise SmokeFailure("No sampled wire traffic on source ToR 128 uplinks")
    checks = {"flow_accounting": True, "positive_fct": True, "uplink_counters": True}
    if lb_mode == 9:
        if len(used[128]) < 2:
            raise SmokeFailure("ConWeave did not exercise at least two source-ToR uplinks")
        replies = re.search(r"Number of INIT's Reply sent \(RTT_REPLY\):\s*(\d+)", log)
        if not replies or int(replies.group(1)) <= 0:
            raise SmokeFailure("No ConWeave INIT RTT_REPLY observed")
        if re.search(r"WARNING - Tor Sw.*VOQ \(num=[1-9]\d*\)", log):
            raise SmokeFailure("ConWeave reports unflushed VOQs")
        checks["conweave_multiple_uplinks"] = True
        checks["conweave_init_reply"] = True
        checks["no_reported_unflushed_voq"] = True
    return {
        "checks": checks,
        "completed_flows": len(fct),
        "completed_payload_bytes": completed_bytes,
        "used_uplinks_wire_bytes_by_tor": dict(used),
        "fct_ns_min": min(row[6] for row in fct),
        "fct_ns_max": max(row[6] for row in fct),
    }


def run_case(binary, topology, output_dir, case_name, lb_mode, env):
    case_dir = output_dir / case_name
    case_dir.mkdir()
    result = {"case": case_name, "lb_mode": lb_mode, "status": "FAIL", "directory": str(case_dir)}
    began = time.monotonic()
    try:
        write_inputs(case_dir, topology, lb_mode)
        result["input_sha256"] = {
            name: sha256(case_dir / name) for name in (TOPOLOGY_NAME, "flows.txt", "config.txt")
        }
        result["argv"] = [str(binary), "config.txt"]
        result["cwd"] = str(case_dir)
        with (case_dir / "run.log").open("wb") as log:
            process = subprocess.run(
                result["argv"], cwd=str(case_dir), env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS, check=False,
            )
        result["returncode"] = process.returncode
        if process.returncode:
            raise SmokeFailure("Binary exited with code {}".format(process.returncode))
        result.update(validate(case_dir, lb_mode))
        result["status"] = "PASS"
    except subprocess.TimeoutExpired:
        result["error"] = "Binary exceeded the {} second timeout".format(TIMEOUT_SECONDS)
    except (SmokeFailure, OSError, ValueError) as exc:
        result["error"] = "{}: {}".format(type(exc).__name__, exc)
    result["wall_time_seconds"] = round(time.monotonic() - began, 6)
    write_json(case_dir / "result.json", result)
    if result["status"] == "PASS":
        print("PASS {}: 16 flows, 16000000 payload bytes".format(case_name), flush=True)
    else:
        print("FAIL {}: {} (evidence: {})".format(case_name, result["error"], case_dir), file=sys.stderr, flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=REPO / "build/scratch/network-load-balance",
                        help="native compiled executable (default: %(default)s)")
    parser.add_argument("--output-dir", type=Path,
                        help="new evidence directory; must not already exist")
    args = parser.parse_args()
    binary = args.binary.expanduser().resolve()
    output_dir = (args.output_dir or REPO / ".native" / (
        "smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )).expanduser().resolve()
    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(exist_ok=False)
    except OSError as exc:
        parser.error("Cannot create a new output directory {}: {}".format(output_dir, exc))

    topology = REPO / "config" / TOPOLOGY_NAME
    summary = {
        "schema_version": 1, "status": "FAIL", "repository": str(REPO),
        "binary": str(binary), "output_dir": str(output_dir),
        "timeout_seconds_per_case": TIMEOUT_SECONDS,
        "expected_flows_per_case": FLOW_COUNT,
        "expected_payload_bytes_per_case": FLOW_COUNT * FLOW_BYTES,
        "purpose": "Native functional smoke test; no performance benefit is asserted.",
        "output_notes": [
            "run.log combines binary stdout and stderr.",
            "Input-stream logging is disabled upstream; input_log.txt may be empty.",
            "CNP/PFC and VOQ-detail files may be empty without congestion/reordering.",
            "Queue and estimator-error logging is disabled upstream; those files may be absent.",
            "Uplink counters include control/data wire bytes, not just payload.",
        ],
        "cases": [],
    }
    try:
        if not binary.is_file() or not os.access(str(binary), os.X_OK):
            raise SmokeFailure("Executable is missing or not executable: {}".format(binary))
        if not topology.is_file():
            raise SmokeFailure("Missing repository topology: {}".format(topology))
        summary["binary_sha256"] = sha256(binary)
        summary["topology_sha256"] = sha256(topology)
        env = os.environ.copy()
        build_dir = str(REPO / "build")
        env["LD_LIBRARY_PATH"] = build_dir + (
            os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )
        summary["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH"]
        for case_name, lb_mode in CASES:
            result = run_case(binary, topology, output_dir, case_name, lb_mode, env)
            summary["cases"].append(result)
            write_json(output_dir / "summary.json", summary)
        if all(result["status"] == "PASS" for result in summary["cases"]):
            summary["status"] = "PASS"
    except (SmokeFailure, OSError, ValueError) as exc:
        summary["error"] = "{}: {}".format(type(exc).__name__, exc)
        print("FAIL: {}".format(summary["error"]), file=sys.stderr)
    finally:
        write_json(output_dir / "summary.json", summary)
    print("{}: {}".format(summary["status"], output_dir / "summary.json"), flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
