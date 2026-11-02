#!/usr/bin/env python3
"""Install the pinned ThoxInux release on the Ubuntu Azure host.

This script runs as root only during host provisioning. The kernel itself runs
as a locked, unprivileged account under thoxinux.service.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path('/opt/thoxinux')
FILES = {'GINUX.EFI', 'build-receipt.json', 'GINUX_LICENSE', 'GINUX_NOTICE'}
MAX_ASSET = 16 * 1024 * 1024


def file_digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def check_file(path: Path, expected: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f'{path.name}: expected a regular file')
    if path.stat().st_size != expected['size_bytes']:
        raise RuntimeError(f'{path.name}: size does not match pinned release')
    if file_digest(path) != expected['sha256']:
        raise RuntimeError(f'{path.name}: SHA-256 does not match pinned release')


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise RuntimeError('Release redirect must use HTTPS without credentials')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_asset(destination: Path, expected: dict, opener=None) -> None:
    size = expected['size_bytes']
    parsed = urllib.parse.urlsplit(expected['url'])
    if (type(size) is not int or not 0 < size <= MAX_ASSET
            or not re.fullmatch(r'[a-f0-9]{64}', expected['sha256'])
            or parsed.scheme != 'https' or parsed.hostname != 'github.com'
            or parsed.username or parsed.password
            or not parsed.path.startswith('/Thox-ai/thoxinux/releases/download/v0.9.1/')
            or parsed.query or parsed.fragment):
        raise RuntimeError('Invalid pinned release asset')
    if destination.exists() or destination.is_symlink():
        # Never overwrite unrelated or corrupt artifacts during an implicit retry.
        check_file(destination, expected)
        return
    opener = opener or urllib.request.build_opener(HttpsRedirects())
    descriptor, name = tempfile.mkstemp(prefix='.release-', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            request = urllib.request.Request(expected['url'], headers={
                'User-Agent': 'THOX-ThoxInux-Azure-Deployment/1.0'})
            with opener.open(request, timeout=60) as response:
                remaining = size + 1
                while remaining:
                    chunk = response.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
        check_file(temporary, expected)
        temporary.chmod(0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def verify_manifest(artifacts: Path, pins: dict) -> None:
    entries = {}
    for line in (artifacts / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (GINUX\.EFI|build-receipt\.json|GINUX_LICENSE|GINUX_NOTICE)', line)
        if not match or match[2] in entries:
            raise RuntimeError('Unsafe, duplicate, or unexpected checksum manifest entry')
        entries[match[2]] = match[1]
    if set(entries) != FILES:
        raise RuntimeError('Release manifest must cover exactly all four payload files')
    for name, digest in entries.items():
        expected = pins['assets'].get(name, pins['bundled_files'].get(name))
        if expected is None or digest != expected['sha256']:
            raise RuntimeError(f'{name}: manifest disagrees with reviewed pins')
        check_file(artifacts / name, expected)


def install(root: Path = ROOT) -> None:
    if os.geteuid() != 0:
        raise RuntimeError('Host provisioning requires root; guest execution must not use root')
    pins = json.loads((root / 'pins.json').read_text())
    if pins.get('release') != 'v0.9.1':
        raise RuntimeError('This installer is reviewed for release v0.9.1')
    artifacts = root / 'artifacts'
    if artifacts.is_symlink():
        raise RuntimeError('Refusing symlink artifact directory')
    artifacts.mkdir(mode=0o755, exist_ok=True)
    for name, expected in pins['bundled_files'].items():
        check_file(root / name if name == 'run-vm.sh' else artifacts / name, expected)
    for name, expected in pins['assets'].items():
        if name not in {'GINUX.EFI', 'build-receipt.json', 'SHA256SUMS'}:
            raise RuntimeError('Unexpected download filename')
        download_asset(artifacts / name, expected)
    verify_manifest(artifacts, pins)
    for required in ('qemu-system-x86_64', 'flock', 'systemctl', 'useradd'):
        if not shutil.which(required):
            raise RuntimeError(f'Missing host package command: {required}')
    try:
        account = pwd.getpwnam('thoxinux')
        if (account.pw_dir != '/var/lib/thoxinux' or account.pw_shell != '/usr/sbin/nologin'
                or account.pw_uid == 0):
            raise RuntimeError('Existing thoxinux account has unexpected identity or login settings')
    except KeyError:
        subprocess.run(['useradd', '--system', '--create-home', '--home-dir', '/var/lib/thoxinux',
                        '--shell', '/usr/sbin/nologin', '--user-group', 'thoxinux'], check=True)
        account = pwd.getpwnam('thoxinux')
    for folder in (Path('/var/lib/thoxinux'), Path('/var/log/thoxinux')):
        folder.mkdir(mode=0o750, exist_ok=True)
        if folder.is_symlink():
            raise RuntimeError('Refusing symlink state or log directory')
        os.chown(folder, account.pw_uid, account.pw_gid)
        folder.chmod(0o750)
    receipt = {
        'schema_version': 1, 'installed': True, 'release': pins['release'],
        'source_commit': pins['source_commit'], 'efi_sha256': pins['efi_sha256'],
        'installed_at_unix': time.time(), 'guest_acceptance': 'pending',
        'host': 'Ubuntu 24.04 Azure Gen2', 'guest': 'ThoxInux QEMU TCG 1 vCPU 2 GiB',
    }
    (root / 'install-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'thoxinux.service'], check=True)
    # The supervisor permits 240 seconds to boot and four 30-second commands.
    # Leave additional time for service startup without confusing a slow TCG
    # boot with a failed installation.
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        health = subprocess.run(['/usr/local/bin/thox-kernel', 'status'],
                                capture_output=True, text=True, timeout=15)
        if health.returncode == 0:
            state = json.loads(health.stdout)
            if state.get('accepted') is True and state.get('running') is True:
                if state.get('efi_sha256') != pins['efi_sha256']:
                    raise RuntimeError('Guest acceptance EFI differs from installed release')
                active = subprocess.run(['systemctl', 'is-active', '--quiet', 'thoxinux.service'])
                if active.returncode == 0:
                    print(json.dumps({'installed': True, 'accepted': True,
                                      'efi_sha256': pins['efi_sha256']}))
                    return
        time.sleep(2)
    raise RuntimeError('ThoxInux guest did not pass boot acceptance; inspect acceptance.json and serial.log')


if __name__ == '__main__':
    try:
        install()
    except Exception as error:
        raise SystemExit(f'ThoxInux bootstrap failed: {error}')
