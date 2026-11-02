#!/usr/bin/env python3
"""Boot the released ThoxInux EFI twice and preserve real local QEMU evidence.

Run as an ordinary user with Python 3.11+, qemu-system-x86, OVMF and util-linux.
This does not install a service, modify Azure, or test the production /opt CLI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
EFI_SHA256 = "1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89"
RELEASE = "v0.9.1"
CHECK_NAMES = ["status", "components", "domain list", "help"]
DOWNLOADS = {"GINUX.EFI", "build-receipt.json", "SHA256SUMS"}
BUNDLED = {"GINUX_LICENSE", "GINUX_NOTICE", "run-vm.sh"}
MAX_RESPONSE = 2 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_module(path, name):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def validate_pins(pins):
    if (not isinstance(pins, dict) or pins.get("release") != RELEASE
            or pins.get("efi_sha256") != EFI_SHA256
            or set(pins.get("assets", {})) != DOWNLOADS
            or set(pins.get("bundled_files", {})) != BUNDLED):
        raise RuntimeError("Expected the reviewed v0.9.1 release and exact asset inventory")
    if not re.fullmatch(r"[0-9a-f]{40}", str(pins.get("source_commit", ""))):
        raise RuntimeError("Source pin must be a complete Git commit SHA")


def stage_artifacts(root, stage, pins, release_dir=None):
    """Reuse the provisioning hash/manifest checks without invoking install()."""
    validate_pins(pins)
    bootstrap = load_module(root / "payload/bootstrap.py", "thox_verify_bootstrap")
    artifacts = stage / "artifacts"
    artifacts.mkdir(mode=0o700)
    for name, expected in pins["bundled_files"].items():
        source = root / "payload" / name
        bootstrap.check_file(source, expected)
        if name != "run-vm.sh":
            shutil.copyfile(source, artifacts / name)
    for name, expected in pins["assets"].items():
        if release_dir is None:
            bootstrap.download_asset(artifacts / name, expected)
        else:
            source = release_dir / name
            bootstrap.check_file(source, expected)
            shutil.copyfile(source, artifacts / name)
            bootstrap.check_file(artifacts / name, expected)
    bootstrap.verify_manifest(artifacts, pins)
    return artifacts


def validate_running_receipt(receipt, supervisor_pid, pins):
    checks = receipt.get("checks") if isinstance(receipt, dict) else None
    if (not isinstance(checks, list) or len(checks) != 4
            or any(not isinstance(check, dict) for check in checks)):
        raise RuntimeError("Guest acceptance does not contain exactly four checks")
    if not (receipt.get("schema") == "thoxinux.azure.guest.v1"
            and receipt.get("supervisor_pid") == supervisor_pid
            and receipt.get("accepted") is True and receipt.get("running") is True
            and receipt.get("state") == "running"
            and receipt.get("release") == RELEASE
            and receipt.get("source_commit") == pins["source_commit"]
            and receipt.get("efi_sha256") == EFI_SHA256
            and receipt.get("efi_size_bytes") == 12507648
            and receipt.get("identity_verified") is True
            and receipt.get("observed_kernel_banner") == pins["expected_banner"]
            and [check.get("command") for check in checks] == CHECK_NAMES
            and all(check.get("passed") is True
                    and re.fullmatch(r"[0-9a-f]{64}", str(check.get("response_sha256", "")))
                    for check in checks)):
        raise RuntimeError("Guest acceptance identity, live session or command evidence mismatch")


def verified_serial_slice(receipt, serial):
    offset = receipt.get("serial_session_offset")
    length = receipt.get("serial_session_bytes")
    if (type(offset) is not int or type(length) is not int or offset < 0 or length <= 0
            or offset + length > len(serial)):
        raise RuntimeError("Serial evidence range is invalid")
    section = serial[offset:offset + length]
    if hashlib.sha256(section).hexdigest() != receipt.get("serial_session_sha256"):
        raise RuntimeError("Serial evidence digest does not match the recorded session")
    return section


def controller_command(socket_path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(40)
        client.connect(str(socket_path))
        client.sendall((json.dumps({"command": command}) + "\n").encode())
        response = bytearray()
        while b"\n" not in response:
            block = client.recv(65536)
            if not block:
                raise RuntimeError("Supervisor closed the socket before a complete response")
            response.extend(block)
            if len(response) > MAX_RESPONSE:
                raise RuntimeError("Controller response exceeded the transport bound")
    value = json.loads(bytes(response).split(b"\n", 1)[0])
    if not isinstance(value, dict) or value.get("ok") is not True or not isinstance(value.get("output"), str):
        raise RuntimeError("Controller command failed: " + json.dumps(value)[:4096])
    return value["output"]


def stop_process(process, cleanup_group=False):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=40)
        except subprocess.TimeoutExpired:
            # The supervisor owns a new session containing only its guest.
            # Terminate that session if it cannot complete bounded cleanup.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
    if cleanup_group:
        # A prematurely exited supervisor must not leave QEMU using a state
        # directory that TemporaryDirectory will remove.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def boot(root, stage, output, pins, label, commands, expected_storage):
    argv = [sys.executable, str(root / "payload/guest_service.py"),
            "--pins", str(root / "payload/pins.json"),
            "--launcher", str(root / "payload/run-vm.sh"),
            "--artifacts", str(stage / "artifacts"), "--state", str(stage / "state"),
            "--logs", str(stage / "logs"), "--socket-path", str(stage / "run/control.sock")]
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "QEMU_ACCEL": "tcg", "GINUX_MEMORY": "2G"})
    record = {"label": label, "scope": "local_qemu_tcg_only", "uid": os.geteuid(),
              "release": RELEASE, "efi_sha256": EFI_SHA256, "supervisor_argv": argv,
              "started_at": utc_now(), "commands": [], "result": "failed"}
    receipt_path = stage / "logs/acceptance.json"
    serial_path = stage / "logs/serial.log"
    process = None
    captured = None
    try:
        captured = (output / f"{label}-supervisor.log").open("xb")
        process = subprocess.Popen(argv, cwd=root, env=environment,
                                   stdout=captured, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Supervisor exited before acceptance; inspect its preserved log")
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("supervisor_pid") == process.pid:
                    if receipt.get("state") == "failed":
                        raise RuntimeError("Guest acceptance failed: " + str(receipt.get("error")))
                    if receipt.get("accepted") is True:
                        validate_running_receipt(receipt, process.pid, pins)
                        serial = verified_serial_slice(receipt, serial_path.read_bytes())
                        if expected_storage.encode() not in serial:
                            raise RuntimeError("Expected persistent-storage boot marker was not observed")
                        save_json(output / f"{label}-running-receipt.json", receipt)
                        record["boot_checks"] = receipt["checks"]
                        record["boot_acceptance"] = True
                        break
            time.sleep(0.25)
        else:
            raise TimeoutError("Guest acceptance exceeded the 420-second bound")
        print(f"{label}: released ThoxInux {RELEASE} boot and four controller checks passed", flush=True)
        for command, expected in commands:
            response = controller_command(stage / "run/control.sock", command)
            if expected not in response:
                raise RuntimeError(f"{command}: expected marker missing: {expected}")
            if command == "ps" and not any(line.split() == ["100", "0", "R", "azureprobe:init"] for line in response.splitlines()):
                raise RuntimeError("The expected embedded init process was not listed")
            record["commands"].append({"command": command, "expected": expected,
                                        "passed": True, "response": response})
            print(f"{label}: {command}: passed", flush=True)
        process.send_signal(signal.SIGTERM)
        if process.wait(timeout=40) != 0:
            raise RuntimeError("Supervisor did not exit successfully on clean shutdown")
        final = json.loads(receipt_path.read_text())
        if not (final.get("supervisor_pid") == process.pid and final.get("accepted") is False
                and final.get("running") is False and final.get("clean_shutdown") is True
                and final.get("guest_exit_code") == 0):
            raise RuntimeError("Clean stopped receipt was not observed")
        section = verified_serial_slice(final, serial_path.read_bytes())
        if b"GINUX_KERNEL_OK" not in section:
            raise RuntimeError("Kernel clean-shutdown marker missing from the verified serial session")
        save_json(output / f"{label}-stopped-receipt.json", final)
        record.update(result="passed", clean_shutdown=True)
        print(f"{label}: clean GINUX_KERNEL_OK shutdown passed", flush=True)
    except BaseException as error:
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if process is not None:
            stop_process(process, cleanup_group=record.get("result") != "passed")
        if captured is not None:
            captured.close()
        record["ended_at"] = utc_now()
        save_json(output / f"{label}-results.json", record)
        if serial_path.exists():
            shutil.copyfile(serial_path, output / "serial.log")
        if receipt_path.exists():
            shutil.copyfile(receipt_path, output / f"{label}-final-receipt.json")
    return record


def lifecycle_commands():
    def state(value):
        return "domain status azureprobe", "State:       " + value
    cold = [
        ("domain new azureprobe", "azureprobe: created"),
        ("domain set azureprobe image=embedded://minimal-system-v0 init=/sbin/init memory-mib=128 autostart=false", "settings accepted"),
        ("domain start azureprobe", "azureprobe: running (detached)"), state("running"),
        ("ps", "azureprobe:init"),
        ("domain stop azureprobe", "azureprobe: stopped"), state("stopped"),
        ("domain start azureprobe", "azureprobe: running (detached)"), state("running"),
        ("domain restart azureprobe", "azureprobe: running (detached)"), state("running"),
        ("domain fail azureprobe", "azureprobe: failed (contained)"), state("failed"),
        ("domain start azureprobe", "azureprobe: running (detached)"), state("running"),
        ("domain stop azureprobe", "azureprobe: stopped"), state("stopped"),
    ]
    recovered = [state("configured"), ("domain start azureprobe", "azureprobe: running (detached)"),
                 state("running"), ("domain stop azureprobe", "azureprobe: stopped"), state("stopped")]
    return cold, recovered


def run(root, output, release_dir=None):
    if os.geteuid() == 0:
        raise RuntimeError("Run verification as a regular user, without sudo")
    if output.exists() or output.is_symlink():
        raise RuntimeError("Choose a new output directory; existing evidence will not be overwritten")
    output.mkdir(parents=True, mode=0o700)
    summary = {"result": "failed", "scope": "local_qemu_tcg_only", "release": RELEASE,
               "efi_sha256": EFI_SHA256, "started_at": utc_now(),
               "azure_deployed": False, "production_cli_path_tested": False,
               "systemd_cloud_init_tested": False, "uid": os.geteuid(),
               "profile": "Q35/OVMF, 1 vCPU, 2 GiB, TCG, 48-bit IOMMU",
               "claim_limits": ["Real local kernel/controller and embedded-domain lifecycle only",
                                "Rootfs pending does not establish ready Rocky Linux or systemd userspace",
                                "No native Hyper-V, Azure deployment, production service, graphical desktop or MeshStack acceptance"]}
    try:
        qemu = os.environ.get("QEMU_SYSTEM_X86_64", "qemu-system-x86_64")
        if not shutil.which(qemu) or not shutil.which("flock"):
            raise RuntimeError("Install qemu-system-x86 and util-linux before verification")
        version = subprocess.run([qemu, "--version"], check=True, capture_output=True, text=True, timeout=10)
        summary["qemu"] = version.stdout.splitlines()[0]
        pins = json.loads((root / "payload/pins.json").read_text())
        validate_pins(pins)
        summary["source_commit"] = pins["source_commit"]
        summary["supervisor_sha256"] = digest(root / "payload/guest_service.py")
        summary["launcher_sha256"] = digest(root / "payload/run-vm.sh")
        summary["harness_sha256"] = digest(Path(__file__))
        with tempfile.TemporaryDirectory(prefix="thoxinux-verify-") as temporary:
            stage = Path(temporary)
            stage_artifacts(root, stage, pins, release_dir)
            # Both boots share only this newly created test state. Never use a
            # production guest volume or an output directory as writable state.
            cold, recovered = lifecycle_commands()
            results = [boot(root, stage, output, pins, "cold", cold, "persistent storage initialized"),
                       boot(root, stage, output, pins, "recovered", recovered, "persistent storage recovered")]
        summary.update(result="passed", boots_passed=len(results),
                       lifecycle_commands_passed=sum(len(result["commands"]) for result in results),
                       controller_acceptance_checks_passed=sum(len(result["boot_checks"]) for result in results),
                       clean_shutdowns_passed=sum(result["clean_shutdown"] for result in results),
                       final_guest_state="stopped", log_sha256=digest(output / "serial.log"))
        print(json.dumps(summary, indent=2), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        print(summary["error"], file=sys.stderr, flush=True)
        return 1
    finally:
        summary["ended_at"] = utc_now()
        save_json(output / "summary.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="New directory for complete raw evidence; existing paths are refused")
    parser.add_argument("--release-dir", type=Path,
                        help="Optional local release assets; all hashes and sizes are still verified")
    args = parser.parse_args()
    try:
        return run(ROOT, args.output.resolve(), args.release_dir.resolve() if args.release_dir else None)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ThoxInux verification stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
