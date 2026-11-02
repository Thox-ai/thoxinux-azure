"""Trust-boundary checks for the real-kernel harness; these do not boot a guest."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / "tools/verify_kernel.py"
SPEC = importlib.util.spec_from_file_location("portable_kernel_verify", PATH)
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)


class KernelProofTests(unittest.TestCase):
    def pins(self):
        return {"release": "v0.9.1", "efi_sha256": verify.EFI_SHA256,
                "source_commit": "f" * 40,
                "expected_banner": "ThoxInux 0.9.0 | ThoxOS Core | THOX.ai LLC",
                "assets": {name: {} for name in verify.DOWNLOADS},
                "bundled_files": {name: {} for name in verify.BUNDLED}}

    def receipt(self):
        pins = self.pins()
        return {"schema": "thoxinux.azure.guest.v1", "supervisor_pid": 123,
                "accepted": True, "running": True, "state": "running",
                "release": "v0.9.1", "source_commit": pins["source_commit"],
                "efi_sha256": verify.EFI_SHA256, "efi_size_bytes": 12507648,
                "identity_verified": True, "observed_kernel_banner": pins["expected_banner"],
                "checks": [{"command": name, "passed": True, "response_sha256": "a" * 64}
                           for name in verify.CHECK_NAMES]}

    def test_rejects_different_efi_or_unexpected_download(self):
        pins = self.pins()
        verify.validate_pins(pins)
        with self.assertRaises(RuntimeError):
            verify.validate_pins({**pins, "efi_sha256": "0" * 64})
        with self.assertRaises(RuntimeError):
            verify.validate_pins({**pins, "assets": {**pins["assets"], "unexpected.sh": {}}})

    def test_rejects_stale_receipt_other_guest_and_duplicate_checks(self):
        record = self.receipt()
        verify.validate_running_receipt(record, 123, self.pins())
        for field, value in (("supervisor_pid", 124), ("efi_sha256", "a" * 64),
                             ("running", False), ("identity_verified", False),
                             ("checks", [record["checks"][0]] * 4), ("checks", None)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                verify.validate_running_receipt({**record, field: value}, 123, self.pins())

    def test_receipt_slice_must_match_complete_bytes_and_digest(self):
        serial = b"earlier-session\nmeasured-session\nlater-session"
        measured = b"measured-session\n"
        receipt = {"serial_session_offset": len(b"earlier-session\n"),
                   "serial_session_bytes": len(measured),
                   "serial_session_sha256": hashlib.sha256(measured).hexdigest()}
        self.assertEqual(verify.verified_serial_slice(receipt, serial), measured)
        for field, value in (("serial_session_offset", -1), ("serial_session_bytes", len(serial) + 1),
                             ("serial_session_sha256", "0" * 64)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                verify.verified_serial_slice({**receipt, field: value}, serial)


if __name__ == "__main__":
    unittest.main()
