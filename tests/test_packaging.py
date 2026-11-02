"""Public-package inventory, integrity and deterministic-byte boundary tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "tools/build_release.py"
SPEC = importlib.util.spec_from_file_location("thox_source_packaging", SOURCE)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        for relative in packaging.REQUIRED_PATHS:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture:" + relative + "\n").encode())

    def test_private_files_keys_binaries_plans_and_git_are_not_packaged(self):
        for relative in ("HANDOFF.md", "private/account.txt", "keys/id_ed25519",
                         "id_rsa", ".env", ".git/config", "azure-plan/parameters.json",
                         "release/GINUX.EFI", "payload/__pycache__/guest_service.pyc"):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PRIVATE-FIXTURE-NOT-A-REAL-SECRET")
        output = self.base / "dist"
        packaging.build(self.source, output)
        with zipfile.ZipFile(output / packaging.ARCHIVE_NAME) as archive:
            expected = {f"thoxinux-azure/{name}" for name in packaging.REQUIRED_PATHS} | {"thoxinux-azure/SHA256SUMS"}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertTrue(all(b"PRIVATE-FIXTURE" not in archive.read(name) for name in archive.namelist()))

    def test_unexpected_public_root_file_and_source_symlink_are_refused(self):
        unexpected = self.source / "payload/account.key"
        unexpected.write_text("private fixture")
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            packaging.build(self.source, self.base / "one")
        self.assertFalse((self.base / "one").exists())
        unexpected.unlink()
        target = self.base / "outside"
        target.write_text("not public")
        path = self.source / "payload/pins.json"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            packaging.build(self.source, self.base / "two")

    def test_zip_bytes_and_manifest_are_reproducible_despite_source_metadata(self):
        first = self.base / "first"
        second = self.base / "second"
        packaging.build(self.source, first)
        for relative in packaging.REQUIRED_PATHS:
            path = self.source / relative
            os.utime(path, (1234567890, 1234567890))
            path.chmod(0o600)
        packaging.build(self.source, second)
        for name in (packaging.ARCHIVE_NAME, packaging.MANIFEST_NAME, packaging.CHECKSUM_NAME):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        with zipfile.ZipFile(first / packaging.ARCHIVE_NAME) as archive:
            self.assertTrue(all(info.date_time == packaging.ZIP_TIME for info in archive.infolist()))

    def test_checksums_and_outer_manifest_bind_actual_archive_bytes(self):
        output = self.base / "dist"
        manifest = packaging.build(self.source, output)
        packed = (output / packaging.ARCHIVE_NAME).read_bytes()
        self.assertEqual(hashlib.sha256(packed).hexdigest(), manifest["sha256"])
        self.assertEqual(len(packed), manifest["size_bytes"])
        self.assertEqual(json.loads((output / packaging.MANIFEST_NAME).read_text()), manifest)
        with zipfile.ZipFile(output / packaging.ARCHIVE_NAME) as archive:
            for line in archive.read("thoxinux-azure/SHA256SUMS").decode().splitlines():
                digest, relative = line.split("  ", 1)
                data = archive.read("thoxinux-azure/" + relative)
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            for relative, item in manifest["files"].items():
                data = archive.read("thoxinux-azure/" + relative)
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertEqual(len(data), item["size_bytes"])

    def test_different_existing_archive_is_preserved(self):
        output = self.base / "dist"
        output.mkdir()
        original = output / packaging.ARCHIVE_NAME
        original.write_bytes(b"earlier artifact")
        with self.assertRaisesRegex(ValueError, "Refusing to replace"):
            packaging.build(self.source, output)
        self.assertEqual(original.read_bytes(), b"earlier artifact")
        self.assertFalse((output / packaging.MANIFEST_NAME).exists())


if __name__ == "__main__":
    unittest.main()
