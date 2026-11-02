#!/usr/bin/env python3
"""Supervise the pinned ThoxInux QEMU guest and record actual boot evidence.

The service runs as thoxinux. Its Unix socket accepts one noninteractive
controller command per connection; no request is ever passed to a host shell.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

EFI_SHA256 = "1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89"
EFI_SIZE = 12507648
RELEASE = "v0.9.1"
IDENTITY = b"ThoxInux 0.9.0 | ThoxOS Core | THOX.ai LLC"
PROFILE = b"Build profile: ThoxInux unified (standalone UEFI)"
PROMPT = re.compile(rb"(?:\A|[\r\n])ginux> \Z")
PANICS = (b"GINUX_KERNEL_PANIC", b"GINUX_BOOT_PANIC")
MAX_REPLY = 1024 * 1024
MAX_REQUEST = 1024
MAX_COMMAND = 240
CHECKS = (
    ("status", ("controller: running (ring 3)", "integrity: sealed")),
    ("components", ("GINUX.EFI component catalog v0:", "- kernel: active, ring 0",
                    "- controller: active, ring 3", "- boot modules: embedded, sealed")),
    ("domain list", ("NAME", "STATE", "PERSONALITY", "MEM-MIB", "IMAGE")),
    ("help", ("status", "components", "domain", "shutdown")),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def process_start_ticks(pid):
    """Linux start time disambiguates a reused process ID in status receipts."""
    data = Path(f"/proc/{pid}/stat").read_text()
    return int(data.rsplit(")", 1)[1].split()[19])


class GuestError(RuntimeError):
    """A guest, payload or transport verification failure."""


class ControllerRejected(GuestError):
    """The controller returned an error while remaining synchronized."""


class StopRequested(GuestError):
    """Service stop requested while waiting for the guest."""


@dataclass
class Config:
    pins: Path = Path("/opt/thoxinux/pins.json")
    launcher: Path = Path("/opt/thoxinux/run-vm.sh")
    artifacts: Path = Path("/opt/thoxinux/artifacts")
    state: Path = Path("/var/lib/thoxinux")
    logs: Path = Path("/var/log/thoxinux")
    socket_path: Path = Path("/run/thoxinux/control.sock")
    boot_timeout: float = 240.0
    command_timeout: float = 30.0


def regular_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise GuestError(f"Expected a regular nonsymlink file: {path}")
    return path


def verify_payload(config):
    pins = json.loads(regular_file(config.pins).read_text())
    if not isinstance(pins, dict) or pins.get("release") != RELEASE:
        raise GuestError("pins.json must identify released ThoxInux v0.9.1")
    if pins.get("efi_sha256") != EFI_SHA256:
        raise GuestError("EFI pin differs from the reviewed v0.9.1 release")
    if not re.fullmatch(r"[0-9a-f]{40}", str(pins.get("source_commit", ""))):
        raise GuestError("pins.json source_commit must be a full Git SHA")
    regular_file(config.launcher)
    payload = regular_file(config.artifacts / "GINUX.EFI")
    if payload.stat().st_size != EFI_SIZE:
        raise GuestError("EFI size differs from the released payload")
    with payload.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != EFI_SHA256:
        raise GuestError("EFI SHA-256 mismatch; guest was not started")
    return {"release": RELEASE, "source_commit": pins["source_commit"],
            "efi_sha256": actual, "efi_size_bytes": EFI_SIZE}


def atomic_json(path, record):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_identity(output):
    lines = output.replace(b"\r\n", b"\n").split(b"\n")
    for expected in (IDENTITY, PROFILE, b"Ginux controller v0 (ring 3)"):
        if expected not in lines:
            raise GuestError("Expected unified ThoxInux guest identity was not observed")


def validate_command(command):
    if not isinstance(command, str) or not command or len(command) > MAX_COMMAND:
        raise ValueError(f"Command must contain 1 to {MAX_COMMAND} ASCII characters")
    if any(ord(character) < 32 or ord(character) > 126 for character in command):
        raise ValueError("Only a single printable ASCII controller command is allowed")
    words = command.split()
    if not words or words[0] not in {"help", "status", "components", "ls", "ps", "domain", "network", "test"}:
        raise ValueError("Unsupported controller command; stop the service with systemctl for shutdown")
    if "--attach" in words or "--interactive" in words or words[:2] in (["domain", "attach"], ["domain", "exec"]):
        raise ValueError("Interactive domain sessions require a direct serial console")
    return command.strip()


def verify_response(command, output, required=()):
    text = output.decode("utf-8", "replace").replace("\r", "")
    lines = text.splitlines()
    if lines and lines[0].strip() == command:
        lines = lines[1:]
    body = "\n".join(lines)
    # Errors come from the real controller. Domain state 'failed' alone is not
    # a protocol error because 'domain fail' is a legitimate test command.
    error_pattern = r"(?im)^(?:error\b|unknown command\b|ginux: unknown\b)|\b(?:unknown command|unrecognized command|invalid argument|command not found|list cursor failed)\b"
    if re.search(error_pattern, body):
        raise ControllerRejected(body[:4096])
    for expected in required:
        if expected not in body:
            raise GuestError(f"{command}: expected response marker missing: {expected}")
    if not body.strip():
        raise GuestError(f"{command}: controller returned an empty response")
    return body


class GuestSession:
    """One serial stream; requests are executed sequentially by the supervisor."""

    def __init__(self, process, log, stop_requested=lambda: False):
        self.process = process
        self.log = log
        self.stop_requested = stop_requested
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)
        self.pending = bytearray()
        self.digest = hashlib.sha256()
        self.bytes_written = 0
        self.closed = False
        self.panic_tail = b""

    def capture(self, data):
        if data:
            self.log.write(data)
            self.log.flush()
            self.digest.update(data)
            self.bytes_written += len(data)
            self.pending.extend(data)
            if len(self.pending) > MAX_REPLY:
                raise GuestError("Guest response exceeded the 1 MiB transport bound; full bytes remain logged")
            panic_window = self.panic_tail + data
            self.panic_tail = panic_window[-64:]
            if any(marker in panic_window for marker in PANICS):
                raise GuestError("Guest kernel panic observed")

    def pump(self, timeout=0.0):
        for key, _ in self.selector.select(timeout):
            data = os.read(key.fd, 65536)
            if data:
                self.capture(data)
            elif not self.closed:
                self.selector.unregister(key.fileobj)
                self.closed = True

    def wait_prompt(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self.stop_requested():
                raise StopRequested("Service stop requested")
            match = PROMPT.search(self.pending)
            if match:
                response = bytes(self.pending[:match.start()])
                self.pending.clear()
                return response
            if self.process.poll() is not None or self.closed:
                raise GuestError("Guest exited before returning the controller prompt")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GuestError("Timed out waiting for controller prompt")
            self.pump(min(remaining, 0.25))

    def command(self, command, timeout, required=()):
        command = validate_command(command)
        self.pump(0)
        # A prior prompt cannot satisfy a new request. Any unsolicited bytes
        # have already been appended to the complete serial log.
        self.pending.clear()
        self.process.stdin.write(command.encode("ascii") + b"\n")
        self.process.stdin.flush()
        response = self.wait_prompt(timeout)
        return verify_response(command, response, required)

    def finish(self, request_shutdown=True):
        """Return clean shutdown evidence, then reap the child in all cases."""
        clean = False
        if self.process.poll() is None and request_shutdown:
            try:
                self.process.stdin.write(b"shutdown\n")
                self.process.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            deadline = time.monotonic() + 25
            while self.process.poll() is None and time.monotonic() < deadline:
                try:
                    self.pump(0.2)
                except GuestError:
                    break
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        # The child is reaped before the final bounded stream drain.
        self.process.wait()
        for _ in range(64):
            if self.closed:
                break
            try:
                self.pump(0.05)
            except GuestError:
                continue
        clean = self.process.returncode == 0 and b"GINUX_KERNEL_OK" in self.pending
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
        return clean


def read_request(connection):
    connection.settimeout(2)
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(min(256, MAX_REQUEST + 1 - len(data)))
        if not chunk:
            raise ValueError("Request must end with a newline")
        data.extend(chunk)
        if len(data) > MAX_REQUEST:
            raise ValueError("Request exceeds 1024 bytes")
    line, trailing = bytes(data).split(b"\n", 1)
    if trailing.strip():
        raise ValueError("Exactly one request is allowed per connection")
    request = json.loads(line)
    if not isinstance(request, dict) or set(request) != {"command"}:
        raise ValueError("Request must be a JSON object with one command field")
    return validate_command(request["command"])


def create_socket(path):
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != os.geteuid():
            raise GuestError("Refusing to replace a foreign or non-socket control path")
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(0.3)
            probe.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink(missing_ok=True)
        else:
            raise GuestError("Another guest supervisor already owns the control socket")
        finally:
            probe.close()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(4)
        server.setblocking(False)
        return server
    except BaseException:
        server.close()
        raise


def serve(config):
    if os.geteuid() == 0:
        raise GuestError("Run the supervisor as the dedicated nonroot thoxinux user")
    os.umask(0o027)
    for directory in (config.state, config.logs, config.socket_path.parent):
        directory.mkdir(parents=True, exist_ok=True)
    stop = {"requested": False}
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.update(requested=True))
    receipt_path = config.logs / "acceptance.json"
    record = {"schema": "thoxinux.azure.guest.v1", "session_id": uuid.uuid4().hex,
              "supervisor_pid": os.getpid(),
              "supervisor_start_ticks": process_start_ticks(os.getpid()),
              "started_at": utc_now(), "updated_at": utc_now(), "accepted": False,
              "running": False, "state": "starting", "checks": [], "error": None,
              "accelerator": "tcg", "guest_memory_mib": 2048, "guest_vcpus": 1,
              "serial_log": str(config.logs / "serial.log"),
              "claim_scope": "Actual QEMU guest controller; Azure provisioning must be verified separately. No native Hyper-V, Linux-domain, GUI or MeshStack acceptance"}
    atomic_json(receipt_path, record)
    session = None
    server = None
    log = None
    return_code = 1

    def save():
        record["updated_at"] = utc_now()
        if session:
            record["serial_session_bytes"] = session.bytes_written
            record["serial_session_sha256"] = session.digest.hexdigest()
            record["guest_pid"] = session.process.pid
        atomic_json(receipt_path, record)

    try:
        record.update(verify_payload(config))
        # Bind before launch to reject concurrent supervisors without touching
        # either guest's persistent state. Access remains owner-only.
        server = create_socket(config.socket_path)
        serial_path = config.logs / "serial.log"
        descriptor = os.open(serial_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
        log = os.fdopen(descriptor, "ab", buffering=0)
        record["serial_session_offset"] = os.fstat(descriptor).st_size
        environment = os.environ.copy()
        environment.update({"QEMU_ACCEL": "tcg", "GINUX_MEMORY": "2G"})
        process = subprocess.Popen(["/bin/bash", str(config.launcher), "--artifact-dir",
                                    str(config.artifacts), "--state-dir", str(config.state)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=environment, bufsize=0)
        session = GuestSession(process, log, lambda: stop["requested"])
        record["guest_start_ticks"] = process_start_ticks(process.pid)
        session.capture(("\n[THOX supervisor session " + record["session_id"] + " started " + record["started_at"] + "]\n").encode())
        record.update(state="booting", running=True)
        save()
        boot = session.wait_prompt(config.boot_timeout)
        verify_identity(boot)
        record["identity_verified"] = True
        record["observed_kernel_banner"] = IDENTITY.decode()
        record["state"] = "accepting"
        for command, required in CHECKS:
            response = session.command(command, config.command_timeout, required)
            record["checks"].append({"command": command, "passed": True,
                                     "response_sha256": hashlib.sha256(response.encode()).hexdigest()})
            save()
        if process.poll() is not None:
            raise GuestError("Guest exited immediately after acceptance checks")
        record.update(accepted=True, running=True, state="running", accepted_at=utc_now())
        save()
        next_heartbeat = time.monotonic() + 10
        while not stop["requested"]:
            session.pump(0.2)
            if process.poll() is not None or session.closed:
                raise GuestError("Guest process exited while service was running")
            # Continuous unsolicited output is retained without letting the
            # in-memory command buffer grow for the lifetime of the service.
            if session.pending:
                session.pending.clear()
            try:
                connection, _ = server.accept()
            except BlockingIOError:
                connection = None
            if connection:
                with connection:
                    try:
                        _, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if peer_uid != os.geteuid():
                            raise ValueError("Control socket requires the thoxinux service identity")
                        command = read_request(connection)
                        response = session.command(command, config.command_timeout)
                        result = {"ok": True, "command": command, "output": response}
                    except (ValueError, UnicodeError, OSError, ControllerRejected) as exc:
                        result = {"ok": False, "error": str(exc)[:4096]}
                    except GuestError as exc:
                        # A timeout loses request/reply synchronization. Stop
                        # and fail acceptance instead of serving stale replies.
                        try:
                            connection.sendall((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode())
                        except OSError:
                            pass
                        raise
                    try:
                        connection.sendall((json.dumps(result) + "\n").encode())
                    except OSError:
                        pass
            if time.monotonic() >= next_heartbeat:
                save()
                next_heartbeat = time.monotonic() + 10
        return_code = 0
        record["state"] = "stopping"
    except StopRequested:
        return_code = 0
        record["state"] = "stopping"
    except (OSError, ValueError, GuestError, subprocess.SubprocessError) as exc:
        record.update(state="failed", error=f"{type(exc).__name__}: {exc}"[:4096])
        print(record["error"], file=sys.stderr, flush=True)
    finally:
        record.update(accepted=False, running=False)
        try:
            save()
        except OSError as exc:
            # A full or failed log volume must not leave an orphaned guest.
            return_code = 1
            print(f"Could not persist stopping receipt: {exc}", file=sys.stderr)
        if session:
            try:
                record["clean_shutdown"] = session.finish(request_shutdown=True)
                record["guest_exit_code"] = session.process.returncode
            except (OSError, GuestError, subprocess.SubprocessError) as exc:
                return_code = 1
                record.update(state="failed", clean_shutdown=False,
                              error=record.get("error") or f"Guest cleanup failed: {exc}")
        if server:
            server.close()
            try:
                config.socket_path.unlink(missing_ok=True)
            except OSError as exc:
                return_code = 1
                print(f"Could not remove control socket: {exc}", file=sys.stderr)
        if log:
            log.close()
        if return_code == 0:
            record["state"] = "stopped"
        record["ended_at"] = utc_now()
        try:
            save()
        except OSError as exc:
            return_code = 1
            print(f"Could not persist final receipt: {exc}", file=sys.stderr)
    return return_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("pins", Config.pins), ("launcher", Config.launcher),
                          ("artifacts", Config.artifacts), ("state", Config.state),
                          ("logs", Config.logs), ("socket-path", Config.socket_path)):
        parser.add_argument("--" + name, type=Path, default=default)
    parser.add_argument("--boot-timeout", type=float, default=240.0)
    parser.add_argument("--command-timeout", type=float, default=30.0)
    args = parser.parse_args()
    if not 1 <= args.boot_timeout <= 600 or not 1 <= args.command_timeout <= 120:
        parser.error("Timeouts must be bounded: boot 1..600 seconds, command 1..120 seconds")
    try:
        return serve(Config(**vars(args)))
    except (OSError, ValueError, GuestError) as exc:
        print(f"ThoxInux supervisor stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
