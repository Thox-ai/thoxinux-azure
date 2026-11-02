"""Unit transport tests only. Fake children do not prove kernel or Azure boot."""
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "payload" / "guest_service.py"
SPEC = importlib.util.spec_from_file_location("thox_guest_service", SOURCE)
guest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guest
SPEC.loader.exec_module(guest)
CLI_LOADER = SourceFileLoader("thox_kernel_cli", str(SOURCE.parent / "thox-kernel"))
CLI_SPEC = importlib.util.spec_from_loader(CLI_LOADER.name, CLI_LOADER)
cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_LOADER.exec_module(cli)


class GuestTests(unittest.TestCase):
    def test_requires_exact_guest_identity_not_launcher_banner(self):
        with self.assertRaises(guest.GuestError):
            guest.verify_identity(b"ThoxOS Ginux Research Edition: QEMU/tcg\r\nGinux controller v0 (ring 3)")
        guest.verify_identity(b"\r\n" + guest.IDENTITY + b"\r\n" + guest.PROFILE + b"\r\nGinux controller v0 (ring 3)\r\n")
        with self.assertRaises(guest.GuestError):
            guest.verify_identity(b"prefix" + guest.IDENTITY + b"\n" + guest.PROFILE + b"\nGinux controller v0 (ring 3)")

    def test_prompt_requires_terminal_line_boundary(self):
        self.assertIsNotNone(guest.PROMPT.search(b"hello\r\nginux> "))
        self.assertIsNone(guest.PROMPT.search(b"echo ginux> "))
        self.assertIsNone(guest.PROMPT.search(b"ginux> status\r\ncontroller: running"))

    def test_command_refuses_multiline_control_and_interactive(self):
        for text in ("status\nshutdown", "status\r", "help\x1b", "domain attach x", "domain start x --attach", "shutdown", "x" * 241):
            with self.subTest(text=text), self.assertRaises(ValueError):
                guest.validate_command(text)
        self.assertEqual(guest.validate_command("domain list"), "domain list")

    def test_unknown_reply_and_missing_expected_content_fail(self):
        for response in (b"status\r\nerror: unknown command status", b"unknown command: nope"):
            with self.assertRaises(guest.ControllerRejected):
                guest.verify_response("status", response)
        with self.assertRaises(guest.GuestError):
            guest.verify_response("status", b"status\r\ncontroller: running (ring 3)", ("integrity: sealed",))
        self.assertIn("running", guest.verify_response("status", b"status\r\ncontroller: running (ring 3)\r\nintegrity: sealed", guest.CHECKS[0][1]))

    def test_real_help_usage_is_not_an_error(self):
        response = b"help\r\nGinux controller shell\r\n\r\nUsage:\r\n  ginux COMMAND\r\nAvailable Commands:\r\n  status\r\n  components\r\n  domain COMMAND\r\n  shutdown"
        self.assertIn("Usage:", guest.verify_response("help", response, guest.CHECKS[3][1]))

    def test_pins_fail_closed_before_payload_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = guest.Config(pins=root / "pins.json", launcher=root / "launcher", artifacts=root)
            config.pins.write_text(json.dumps({"release": "v0.9.1", "source_commit": "a" * 40, "efi_sha256": "0" * 64}))
            with self.assertRaisesRegex(guest.GuestError, "EFI pin"):
                guest.verify_payload(config)

    def test_receipt_atomic_replacement_clears_stale_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            guest.atomic_json(path, {"accepted": True, "running": True})
            guest.atomic_json(path, {"accepted": False, "running": False})
            self.assertEqual(json.loads(path.read_text()), {"accepted": False, "running": False})
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_status_requires_exact_release_identity_and_four_checks(self):
        record = {"schema": "thoxinux.azure.guest.v1", "release": "v0.9.1",
                  "efi_sha256": guest.EFI_SHA256, "efi_size_bytes": guest.EFI_SIZE,
                  "identity_verified": True, "observed_kernel_banner": guest.IDENTITY.decode(),
                  "accepted": True, "running": True, "state": "running",
                  "checks": [{"command": command, "passed": True, "response_sha256": "a" * 64}
                             for command, _ in guest.CHECKS]}
        self.assertTrue(cli.valid_acceptance(record))
        for key, value in (("efi_sha256", "b" * 64), ("release", "v0.9.0"),
                           ("identity_verified", False), ("schema", "other"),
                           ("checks", record["checks"][:3]), ("checks", [record["checks"][0]] * 4)):
            with self.subTest(key=key):
                self.assertFalse(cli.valid_acceptance({**record, key: value}))

    def test_status_rejects_reused_pid(self):
        with mock.patch.object(cli.os, "kill"), mock.patch.object(Path, "read_text", return_value="1 (qemu) S " + "0 " * 18 + "123 0"):
            self.assertFalse(cli.live_process(2, 456, "qemu"))

    def test_socket_owner_permissions_and_existing_listener_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.sock"
            try:
                server = guest.create_socket(path)
            except PermissionError:
                self.skipTest("Execution sandbox denies AF_UNIX bind; deployment runtime check remains required")
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(guest.GuestError, "already owns"):
                    guest.create_socket(path)
            finally:
                server.close()

    def test_request_rejects_extra_fields(self):
        class BytesConnection:
            def __init__(self, payload):
                self.stream = io.BytesIO(payload)
            def settimeout(self, timeout):
                pass
            def recv(self, count):
                return self.stream.read(count)
        with self.assertRaises(ValueError):
            guest.read_request(BytesConnection(b'{"command":"status","shell":true}\n'))
        with self.assertRaises(ValueError):
            guest.read_request(BytesConnection(b'x' * 1025))
        self.assertEqual(guest.read_request(BytesConnection(b'{"command":"status"}\n')), "status")

    def fake_session(self, code):
        process = subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        return guest.GuestSession(process, io.BytesIO())

    def test_fake_child_chunked_prompt_and_verified_command(self):
        code = """
import sys, time
sys.stdout.write('fake test child\\r\\ngin'); sys.stdout.flush()
time.sleep(0.02)
sys.stdout.write('ux> '); sys.stdout.flush()
for line in sys.stdin:
    if line.strip() == 'shutdown':
        print('GINUX_KERNEL_OK'); break
    sys.stdout.write(line.strip() + '\\r\\ncontroller: running (ring 3)\\r\\nintegrity: sealed\\r\\nginux> ')
    sys.stdout.flush()
"""
        session = self.fake_session(code)
        try:
            self.assertIn(b"fake test child", session.wait_prompt(2))
            response = session.command("status", 2, guest.CHECKS[0][1])
            self.assertIn("integrity: sealed", response)
        finally:
            self.assertTrue(session.finish())
        self.assertIn(b"controller: running", session.log.getvalue())

    def test_fake_child_timeout_cannot_be_acceptance(self):
        session = self.fake_session("import time; print('no prompt', flush=True); time.sleep(10)")
        try:
            with self.assertRaisesRegex(guest.GuestError, "Timed out"):
                session.wait_prompt(0.05)
        finally:
            session.finish(request_shutdown=False)

    def test_stale_prompt_cannot_satisfy_new_command(self):
        session = self.fake_session("import sys,time; sys.stdout.write('ginux> '); sys.stdout.flush(); time.sleep(10)")
        try:
            session.wait_prompt(2)
            with self.assertRaisesRegex(guest.GuestError, "Timed out"):
                session.command("status", 0.05)
        finally:
            session.finish(request_shutdown=False)

    def test_fake_child_panic_aborts_wait(self):
        session = self.fake_session("print('GINUX_KERNEL_PANIC', flush=True)")
        try:
            with self.assertRaisesRegex(guest.GuestError, "panic"):
                session.wait_prompt(2)
        finally:
            session.finish(request_shutdown=False)

    def test_split_panic_is_detected_across_idle_buffer_clear(self):
        session = self.fake_session("import time; time.sleep(10)")
        try:
            session.capture(b"GINUX_KERNEL_")
            session.pending.clear()
            with self.assertRaisesRegex(guest.GuestError, "panic"):
                session.capture(b"PANIC")
        finally:
            session.finish(request_shutdown=False)


if __name__ == "__main__":
    unittest.main()
