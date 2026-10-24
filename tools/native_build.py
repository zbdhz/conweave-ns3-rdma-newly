#!/usr/bin/env python3
"""Configure and build this repository natively with its Python 2 Waf."""

import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES = "point-to-point,applications,flow-monitor"


def positive_jobs(value):
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("jobs must be an integer >= 1")
    if count < 1:
        raise argparse.ArgumentTypeError("jobs must be >= 1")
    return count


def executable(value):
    expanded = os.path.expanduser(value)
    found = shutil.which(expanded)
    if not found:
        raise RuntimeError("Executable not found or not executable: " + expanded)
    return str(Path(found).absolute())


def capture(command):
    result = subprocess.run(
        command, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Preflight command failed: {}\n{}".format(
                shlex.join(command), result.stdout[-2000:]
            )
        )
    return result.stdout.strip()


def write_manifest(path, manifest):
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_tail(path, limit):
    # Seek near EOF so a large compiler log is never loaded in full.
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        start = max(0, size - 16384)
        stream.seek(start)
        text = stream.read().decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start and lines:
        lines = lines[1:]
    for line in lines[-limit:]:
        print("  " + line)


def run_step(name, command, env, log_dir, manifest, manifest_path):
    log_path = log_dir / (name + ".log")
    record = {
        "step": name, "command": command, "cwd": str(REPO_ROOT),
        "log": str(log_path), "status": "running", "exit_code": None,
        "elapsed_seconds": None,
    }
    manifest["commands"].append(record)
    write_manifest(manifest_path, manifest)
    print("{} log: {}".format(name, log_path), flush=True)
    started = time.monotonic()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write("Command: " + shlex.join(command) + "\n")
            log.flush()
            result = subprocess.run(
                command, cwd=str(REPO_ROOT), env=env,
                stdout=log, stderr=subprocess.STDOUT, check=False,
            )
        record["exit_code"] = result.returncode
        record["status"] = "completed" if result.returncode == 0 else "failed"
    except KeyboardInterrupt:
        record["exit_code"] = 130
        record["status"] = "interrupted"
        raise
    except OSError as error:
        record["exit_code"] = 1
        record["status"] = "failed"
        record["error"] = str(error)
        raise
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_manifest(manifest_path, manifest)
        print_tail(log_path, 8 if record["exit_code"] == 0 else 14)
    print(
        "{}: exit {}, {:.3f}s".format(
            name, record["exit_code"], record["elapsed_seconds"]
        ), flush=True,
    )
    return record["exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python2", default=str(
            Path.home() / "miniforge3/envs/conweave-build/bin/python"
        ), help="Python 2.7 interpreter used by the bundled Waf",
    )
    parser.add_argument("--jobs", type=positive_jobs, default=8)
    parser.add_argument("--cc", default="/usr/bin/gcc")
    parser.add_argument("--cxx", default="/usr/bin/g++")
    args = parser.parse_args(argv)

    started = time.monotonic()
    now = datetime.datetime.now().astimezone()
    log_dir = REPO_ROOT / ".native" / ("build-" + now.strftime("%Y%m%d-%H%M%S-%f"))
    log_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = log_dir / "manifest.json"
    manifest = {
        "schema_version": 1, "started_at": now.isoformat(),
        "repo_root": str(REPO_ROOT), "status": "preflight",
        "exit_code": None, "elapsed_seconds": None,
        "jobs": args.jobs, "modules": MODULES,
        "versions": {"runner_python": sys.version}, "commands": [],
    }
    write_manifest(manifest_path, manifest)
    print("Build manifest: " + str(manifest_path), flush=True)
    exit_code = 1
    try:
        if not (REPO_ROOT / "waf").is_file() or not (REPO_ROOT / "wscript").is_file():
            raise RuntimeError("Cannot find the repository Waf and wscript")
        python2 = executable(args.python2)
        cc = executable(args.cc)
        cxx = executable(args.cxx)
        python_info = json.loads(capture([
            python2, "-c",
            "import json,sys; print(json.dumps({'version':sys.version,"
            "'major':sys.version_info[0],'minor':sys.version_info[1]}))",
        ]))
        if (python_info["major"], python_info["minor"]) != (2, 7):
            raise RuntimeError("--python2 must point to Python 2.7")
        manifest["versions"].update({
            "waf_python": python_info["version"],
            "cc": capture([cc, "--version"]),
            "cxx": capture([cxx, "--version"]),
        })
        env = os.environ.copy()
        env.update({"CC": cc, "CXX": cxx})
        manifest["build_environment"] = {"CC": cc, "CXX": cxx}
        manifest["python2_executable"] = python2
        manifest["status"] = "running"
        write_manifest(manifest_path, manifest)
        prefix = [python2, str(REPO_ROOT / "waf")]
        configure = prefix + [
            "configure", "--build-profile=optimized", "--disable-python",
            "--disable-examples", "--disable-tests", "--enable-modules=" + MODULES,
        ]
        exit_code = run_step(
            "configure", configure, env, log_dir, manifest, manifest_path
        )
        if exit_code == 0:
            # A full build generates module headers before scratch compilation.
            # A direct --targets build on a fresh tree can miss those headers.
            exit_code = run_step(
                "build", prefix + ["build", "-j" + str(args.jobs)],
                env, log_dir, manifest, manifest_path,
            )
        manifest["status"] = "completed" if exit_code == 0 else "failed"
    except KeyboardInterrupt:
        exit_code = 130
        manifest["status"] = "interrupted"
        print("Build interrupted; logs are retained.", file=sys.stderr)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        exit_code = 1
        manifest["status"] = "failed"
        manifest["error"] = str(error)
        print("Build failed: " + str(error), file=sys.stderr)
    finally:
        manifest["exit_code"] = exit_code
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        manifest["finished_at"] = datetime.datetime.now().astimezone().isoformat()
        write_manifest(manifest_path, manifest)
        print("Manifest retained: " + str(manifest_path), flush=True)
    return exit_code if exit_code >= 0 else 128 - exit_code


if __name__ == "__main__":
    sys.exit(main())
