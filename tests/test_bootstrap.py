"""Integrity guard tests. These tests do not create Azure resources or boot a guest."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstrap', ROOT / 'payload/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class Opener:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        return io.BytesIO(self.data)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = b'test payload, not an EFI kernel'
        self.expected = {'size_bytes': len(self.data),
                         'sha256': hashlib.sha256(self.data).hexdigest(),
                         'url': 'https://github.com/Thox-ai/thoxinux/releases/download/v0.9.1/GINUX.EFI'}

    def test_exact_download_and_reuse(self):
        opener = Opener(self.data)
        target = self.root / 'GINUX.EFI'
        bootstrap.download_asset(target, self.expected, opener)
        bootstrap.download_asset(target, self.expected, opener)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(target.read_bytes(), self.data)

    def test_bad_hash_never_installed(self):
        target = self.root / 'GINUX.EFI'
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            bootstrap.download_asset(target, self.expected, Opener(b'x' * len(self.data)))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_download_never_installed(self):
        target = self.root / 'GINUX.EFI'
        with self.assertRaisesRegex(RuntimeError, 'size'):
            bootstrap.download_asset(target, self.expected, Opener(self.data + b'extra'))
        self.assertFalse(target.exists())

    def test_existing_mismatch_preserved(self):
        target = self.root / 'GINUX.EFI'
        target.write_bytes(b'unrelated')
        with self.assertRaises(RuntimeError):
            bootstrap.download_asset(target, self.expected, Opener(self.data))
        self.assertEqual(target.read_bytes(), b'unrelated')

    def test_symlink_refused(self):
        other = self.root / 'elsewhere'
        other.write_bytes(self.data)
        target = self.root / 'GINUX.EFI'
        target.symlink_to(other)
        with self.assertRaisesRegex(RuntimeError, 'regular file'):
            bootstrap.download_asset(target, self.expected, Opener(self.data))

    def test_untrusted_download_origin_refused(self):
        for url in ('http://github.com/Thox-ai/thoxinux/releases/download/v0.9.1/GINUX.EFI',
                    'https://github.com/other/release',
                    'https://github.com/Thox-ai/thoxinux/releases/download/v0.9.1/GINUX.EFI?token=secret'):
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, 'Invalid'):
                bootstrap.download_asset(self.root / 'GINUX.EFI', dict(self.expected, url=url), Opener(self.data))

    def fixture_manifest(self):
        pins = {'assets': {}, 'bundled_files': {}}
        rows = []
        for name in sorted(bootstrap.FILES):
            data = name.encode()
            (self.root / name).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pins['assets'][name] = {'sha256': digest, 'size_bytes': len(data)}
            rows.append(f'{digest}  {name}')
        (self.root / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
        return pins, rows

    def test_complete_manifest(self):
        pins, _ = self.fixture_manifest()
        bootstrap.verify_manifest(self.root, pins)

    def test_manifest_traversal_and_duplicate_rejected(self):
        for suffix in ('../evil', 'GINUX.EFI'):
            pins, rows = self.fixture_manifest()
            rows.append('a' * 64 + '  ' + suffix)
            (self.root / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
            with self.subTest(name=suffix), self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                bootstrap.verify_manifest(self.root, pins)

    def test_manifest_disagreement_rejected(self):
        pins, rows = self.fixture_manifest()
        pins['assets']['GINUX.EFI']['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'disagrees'):
            bootstrap.verify_manifest(self.root, pins)


if __name__ == '__main__':
    unittest.main()
