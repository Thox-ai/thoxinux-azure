#!/usr/bin/env python3
"""Create a deterministic, explicitly allowlisted public source ZIP.

This packages source and authentic public evidence. It does not create a Git
tag, publish a release, include Git history, or claim a deployment succeeded.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "thoxinux-azure"
ARCHIVE_NAME = "thoxinux-azure-source.zip"
MANIFEST_NAME = "thoxinux-azure-source.manifest.json"
CHECKSUM_NAME = "thoxinux-azure-source.zip.sha256"
MAX_SOURCE_BYTES = 50 * 1024 * 1024
ZIP_TIME = (1980, 1, 1, 0, 0, 0)

REQUIRED_PATHS = (
    ".gitignore", ".gitattributes", "README.md", "LICENSE", "NOTICE", "UPSTREAM-NOTICE.md",
    "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "azuredeploy.json",
    "deploy.py", "build_cloud_init.py", "cloud-init.yaml",
    "docs/QUICKSTART.md", "docs/USER_GUIDE.md", "docs/ARCHITECTURE.md",
    "docs/VALIDATION.md", "docs/DEVELOPMENT.md", "docs/CLI_DEPLOYMENT.md",
    "docs/PUBLISH.md", "docs/PACKAGING.md",
    "portal/README.md", "portal/azuredeploy.json", "portal/build_portal.py",
    "payload/run-vm.sh", "payload/guest_service.py", "payload/bootstrap.py",
    "payload/pins.json", "payload/GINUX_LICENSE", "payload/GINUX_NOTICE",
    "payload/thox-kernel", "payload/thoxinux.service",
    "tools/verify_kernel.py", "tools/publish_github.py", "tools/build_release.py",
    "tests/test_bootstrap.py", "tests/test_deploy.py", "tests/test_guest.py",
    "tests/test_portal.py", "tests/test_publish_github.py",
    "tests/test_verify_kernel.py", "tests/test_packaging.py",
    ".github/workflows/verify.yml",
    "validation/INDEPENDENT_REVIEW.md", "validation/SOURCE.json",
    "validation/STATUS.json", "validation/unit-tests.json", "validation/unit-tests.log",
)

EVIDENCE_NAMES = (
    "summary.json", "serial.log", "cold-results.json", "recovered-results.json",
    "cold-running-receipt.json", "recovered-running-receipt.json",
    "cold-stopped-receipt.json", "recovered-stopped-receipt.json",
    "cold-final-receipt.json", "recovered-final-receipt.json",
    "cold-supervisor.log", "recovered-supervisor.log", "execution-receipt.json",
    "harness-console.log", "readback-validation.json",
)
OPTIONAL_PATHS = ("validation/README.md",) + tuple(
    f"validation/{directory}/{name}"
    for directory in ("local-qemu", "portable-qemu")
    for name in EVIDENCE_NAMES
)
PUBLIC_ROOTS = ("docs", "portal", "payload", "tools", "tests", "validation", ".github/workflows")
EXECUTABLE_PATHS = {
    "deploy.py", "build_cloud_init.py", "portal/build_portal.py",
    "payload/run-vm.sh", "payload/guest_service.py", "payload/bootstrap.py",
    "payload/thox-kernel", "tools/verify_kernel.py", "tools/publish_github.py",
    "tools/build_release.py",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def ignore_generated(path):
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def regular_source(root, relative):
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing symlink source: {relative}")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Missing regular public source file: {relative}")
    return path


def collect_sources(root):
    allowed = set(REQUIRED_PATHS) | set(OPTIONAL_PATHS)
    # Known public roots are strict. Unlisted root-level files, .git, release
    # binaries and separate private directories are never candidates at all.
    for relative in PUBLIC_ROOTS:
        directory = root / relative
        if directory.is_symlink():
            raise ValueError(f"Refusing symlink public directory: {relative}")
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            rel = path.relative_to(root)
            if ignore_generated(rel):
                continue
            if path.is_symlink():
                raise ValueError(f"Refusing symlink under a public root: {rel.as_posix()}")
            if path.is_file() and rel.as_posix() not in allowed:
                raise ValueError(f"Unexpected file under a public root: {rel.as_posix()}")
            if not path.is_file() and not path.is_dir():
                raise ValueError(f"Refusing special file under a public root: {rel.as_posix()}")
    selected = list(REQUIRED_PATHS)
    selected.extend(name for name in OPTIONAL_PATHS if (root / name).exists() or (root / name).is_symlink())
    content = {}
    total = 0
    for relative in sorted(selected):
        path = regular_source(root, relative)
        total += path.stat().st_size
        if total > MAX_SOURCE_BYTES:
            raise ValueError("Public source package exceeds the 50 MiB source-byte bound")
        data = path.read_bytes()
        content[relative] = data
    if sum(len(data) for data in content.values()) > MAX_SOURCE_BYTES:
        raise ValueError("Public source changed while reading and exceeded the source-byte bound")
    return content


def archive_bytes(content):
    checksums = "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(content.items())).encode()
    entries = {**content, "SHA256SUMS": checksums}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, data in sorted(entries.items()):
            item = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{name}", ZIP_TIME)
            item.create_system = 3
            item.compress_type = zipfile.ZIP_STORED
            mode = 0o755 if name in EXECUTABLE_PATHS else 0o644
            item.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(item, data)
    packed = output.getvalue()
    # Verify the actual archive, not only the intended input inventory.
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Generated ZIP failed its CRC verification")
        if sorted(archive.namelist()) != sorted(f"{ARCHIVE_ROOT}/{name}" for name in entries):
            raise ValueError("Generated archive inventory mismatch")
        for name, data in entries.items():
            if archive.read(f"{ARCHIVE_ROOT}/{name}") != data:
                raise ValueError(f"Generated archive bytes differ: {name}")
    return packed, entries


def write_identical_or_new(path, data):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ValueError(f"Refusing to replace a different existing output: {path.name}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".package-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        # Linking is atomic and refuses a racing writer's destination.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build(root, output):
    root = Path(root).resolve()
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Refusing a symlink output directory")
    content = collect_sources(root)
    packed, entries = archive_bytes(content)
    inventory = {name: {"sha256": sha256(data), "size_bytes": len(data),
                        "mode": "0755" if name in EXECUTABLE_PATHS else "0644"}
                 for name, data in sorted(entries.items())}
    manifest = {
        "schema": "thoxinux.azure.source-package.v1",
        "archive": ARCHIVE_NAME, "archive_root": ARCHIVE_ROOT,
        "sha256": sha256(packed), "size_bytes": len(packed),
        "payload_file_count": len(content), "archive_file_count": len(entries),
        "checksum_file": f"{ARCHIVE_ROOT}/SHA256SUMS",
        "compression": "ZIP_STORED", "files": inventory,
        "scope": "Public source and recorded evidence. No Git history, kernel binary, tag, publication or deployment is created.",
    }
    products = {
        ARCHIVE_NAME: packed,
        MANIFEST_NAME: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        CHECKSUM_NAME: f"{sha256(packed)}  {ARCHIVE_NAME}\n".encode(),
    }
    output.mkdir(parents=True, exist_ok=True)
    # Check every existing output before creating any of this invocation's files.
    for name, data in products.items():
        path = output / name
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"Refusing to replace a different existing output: {name}")
    for name, data in products.items():
        write_identical_or_new(output / name, data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        manifest = build(args.source, args.output_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Source packaging stopped: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: manifest[key] for key in
                      ("archive", "sha256", "size_bytes", "payload_file_count", "archive_file_count")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
