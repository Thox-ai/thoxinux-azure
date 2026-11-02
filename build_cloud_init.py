#!/usr/bin/env python3
"""Build deterministic, secret-free Azure cloud-init from reviewed payload files."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAPPING = {
    'run-vm.sh': ('/opt/thoxinux/run-vm.sh', '0755'),
    'guest_service.py': ('/opt/thoxinux/guest_service.py', '0755'),
    'bootstrap.py': ('/opt/thoxinux/bootstrap.py', '0755'),
    'pins.json': ('/opt/thoxinux/pins.json', '0644'),
    'GINUX_LICENSE': ('/opt/thoxinux/artifacts/GINUX_LICENSE', '0644'),
    'GINUX_NOTICE': ('/opt/thoxinux/artifacts/GINUX_NOTICE', '0644'),
    'thox-kernel': ('/usr/local/bin/thox-kernel', '0755'),
    'thoxinux.service': ('/etc/systemd/system/thoxinux.service', '0644'),
}


def generate(root=ROOT):
    files = []
    for name, (destination, permissions) in MAPPING.items():
        source = root / 'payload' / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Missing regular payload: {source.name}')
        data = source.read_bytes()
        encoded = base64.b64encode(gzip.compress(data, mtime=0)).decode('ascii')
        files.append({'path': destination, 'owner': 'root:root',
                      'permissions': permissions, 'encoding': 'gz+b64', 'content': encoded})
    configuration = {
        'package_update': True,
        'packages': ['qemu-system-x86', 'ovmf', 'python3', 'ca-certificates', 'util-linux'],
        'write_files': files,
        'runcmd': [['/usr/bin/python3', '/opt/thoxinux/bootstrap.py']],
        'final_message': 'THOX Azure host cloud-init finished. Guest acceptance is recorded separately.',
    }
    result = ('#cloud-config\n' + json.dumps(configuration, indent=2) + '\n').encode()
    if len(result) >= 65536:
        raise ValueError('Azure customData exceeds the 64 KiB decoded limit')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check committed cloud-init without modifying it')
    args = parser.parse_args()
    target = ROOT / 'cloud-init.yaml'
    data = generate()
    if args.check:
        if not target.is_file() or target.read_bytes() != data:
            raise SystemExit('cloud-init.yaml is stale; run python3 build_cloud_init.py')
    else:
        target.write_bytes(data)
    print(json.dumps({'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                      'mode': 'checked' if args.check else 'generated'}))
