#!/usr/bin/env python3
"""Package verified CI evidence, then publish an immutable engineering prerelease.

Prepare needs actual successful QEMU output and a clean checkout of GITHUB_SHA.
Publish is restricted to pushes to main in the named repository. This helper
never moves tags, replaces assets, deletes releases, or calls Azure.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Thox-ai/thoxinux-azure"
MANIFEST = "thoxinux-release-manifest.json"
CHECKSUMS = "SHA256SUMS"
PROOF_ZIP = "thoxinux-ci-kernel-proof.zip"
SUMMARY = "thoxinux-ci-kernel-summary.json"
SOURCE_ZIP = "thoxinux-azure-source.zip"
SOURCE_MANIFEST = "thoxinux-azure-source.manifest.json"
SOURCE_CHECKSUM = "thoxinux-azure-source.zip.sha256"
DATA_ASSETS = (SOURCE_ZIP, SOURCE_MANIFEST, SOURCE_CHECKSUM, PROOF_ZIP, SUMMARY)
ASSETS = DATA_ASSETS + (MANIFEST, CHECKSUMS)
PROOF_FILES = (
    "summary.json", "serial.log", "cold-results.json", "recovered-results.json",
    "cold-running-receipt.json", "recovered-running-receipt.json",
    "cold-stopped-receipt.json", "recovered-stopped-receipt.json",
    "cold-final-receipt.json", "recovered-final-receipt.json",
    "cold-supervisor.log", "recovered-supervisor.log",
)
MAX_ASSET_BYTES = 64 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def module(root, filename, name):
    spec = importlib.util.spec_from_file_location(name, root / "tools" / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def read_regular(path):
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Expected a regular file: {path.name}")
    if path.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError(f"File exceeds the release size bound: {path.name}")
    data = path.read_bytes()
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError(f"File changed beyond the release size bound: {path.name}")
    return data


def checkout_sha(root):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, check=True, timeout=30)
        return result.stdout.strip()
    sha = git("rev-parse", "HEAD")
    if not SHA.fullmatch(sha):
        raise ValueError("The checkout does not have a complete commit SHA")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Release preparation/publication requires a clean checkout")
    return sha


def context(root, repository, publish=False, environment=None):
    env = os.environ if environment is None else environment
    if repository != REPOSITORY or env.get("GITHUB_REPOSITORY") != repository:
        raise ValueError(f"This workflow only issues releases for {REPOSITORY}")
    sha = env.get("GITHUB_SHA", "")
    if env.get("GITHUB_ACTIONS") != "true" or not SHA.fullmatch(sha):
        raise ValueError("Run this helper from the verified GitHub Actions workflow")
    if any(not re.fullmatch(r"[1-9][0-9]*", env.get(key, ""))
           for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")):
        raise ValueError("A complete GitHub workflow run identity is required")
    if publish and (env.get("GITHUB_EVENT_NAME") != "push"
                    or env.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("Publication is permitted only for a push to main")
    if publish and not env.get("GH_TOKEN"):
        raise ValueError("The release job needs its contents:write GH_TOKEN")
    if checkout_sha(root) != sha:
        raise ValueError("Checkout HEAD does not equal the verified GITHUB_SHA")
    version = read_regular(root / "VERSION").decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-rc\.[1-9][0-9]*", version):
        raise ValueError("VERSION must identify an engineering release candidate")
    return {"repository": repository, "source_commit": sha, "version": version,
            "tag": "v" + version, "run_id": env["GITHUB_RUN_ID"],
            "run_attempt": env["GITHUB_RUN_ATTEMPT"],
            "run_url": f"https://github.com/{repository}/actions/runs/{env['GITHUB_RUN_ID']}"}


def release_requested(root, environment=None):
    """Only the initial VERSION or a changed VERSION requests a new release."""
    env = os.environ if environment is None else environment
    if (env.get("GITHUB_EVENT_NAME") != "push" or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_REPOSITORY") != REPOSITORY):
        return False
    identity = context(root, REPOSITORY, environment=env)
    before = env.get("THOX_PUSH_BEFORE", "")
    if before == "0" * 40:
        return True
    if not SHA.fullmatch(before):
        raise ValueError("The release decision needs the complete push-before commit")
    subprocess.run(["git", "-C", str(root), "cat-file", "-e", before + "^{commit}"],
                   check=True, capture_output=True, text=True, timeout=30)
    changed = subprocess.run(["git", "-C", str(root), "diff", "--quiet", before,
                              identity["source_commit"], "--", "VERSION"],
                             capture_output=True, text=True, timeout=30)
    if changed.returncode not in (0, 1):
        raise RuntimeError("Could not determine whether this push changes VERSION")
    return changed.returncode == 1


def validate_proof(root, proof):
    """Check captured evidence against the exact harness, supervisor and pins."""
    if proof.is_symlink() or not proof.is_dir() or set(p.name for p in proof.iterdir()) != set(PROOF_FILES):
        raise ValueError("The CI kernel proof must have the exact complete evidence inventory")
    data = {name: read_regular(proof / name) for name in PROOF_FILES}
    if sum(map(len, data.values())) > MAX_ASSET_BYTES:
        raise ValueError("The complete kernel proof exceeds the release size bound")
    summary = json.loads(data["summary.json"])
    pins = json.loads(read_regular(root / "payload/pins.json"))
    verifier = module(root, "verify_kernel.py", "thox_release_verifier")
    verifier.validate_pins(pins)
    expected = {"result": "passed", "scope": "local_qemu_tcg_only", "boots_passed": 2,
                "controller_acceptance_checks_passed": 8, "lifecycle_commands_passed": 22,
                "clean_shutdowns_passed": 2, "final_guest_state": "stopped",
                "release": pins["release"], "source_commit": pins["source_commit"],
                "efi_sha256": pins["efi_sha256"], "azure_deployed": False,
                "production_cli_path_tested": False, "systemd_cloud_init_tested": False}
    if (not isinstance(summary, dict) or any(summary.get(k) != v for k, v in expected.items())
            or type(summary.get("uid")) is not int or summary["uid"] <= 0):
        raise ValueError("Kernel summary does not establish the required local-only acceptance")
    for key, path in (("harness_sha256", "tools/verify_kernel.py"),
                      ("supervisor_sha256", "payload/guest_service.py"),
                      ("launcher_sha256", "payload/run-vm.sh")):
        if summary.get(key) != digest(read_regular(root / path)):
            raise ValueError(f"Kernel proof was produced by different source bytes: {path}")
    if summary.get("log_sha256") != digest(data["serial.log"]):
        raise ValueError("Kernel serial log does not match the accepted summary")
    sessions = set()
    for label, commands in zip(("cold", "recovered"), verifier.lifecycle_commands()):
        running = json.loads(data[f"{label}-running-receipt.json"])
        verifier.validate_running_receipt(running, running.get("supervisor_pid"), pins)
        verifier.verified_serial_slice(running, data["serial.log"])
        session = running.get("session_id")
        if not session or session in sessions:
            raise ValueError("Cold and recovery evidence must record distinct guest sessions")
        sessions.add(session)
        stopped = json.loads(data[f"{label}-stopped-receipt.json"])
        final = json.loads(data[f"{label}-final-receipt.json"])
        if (stopped != final or stopped.get("session_id") != session
                or stopped.get("accepted") is not False or stopped.get("running") is not False
                or stopped.get("clean_shutdown") is not True or stopped.get("guest_exit_code") != 0):
            raise ValueError("Kernel evidence does not end with a clean stopped receipt")
        if b"GINUX_KERNEL_OK" not in verifier.verified_serial_slice(stopped, data["serial.log"]):
            raise ValueError("Kernel shutdown marker is absent from its bound serial evidence")
        result = json.loads(data[f"{label}-results.json"])
        actual = result.get("commands", [])
        if (result.get("result") != "passed" or result.get("clean_shutdown") is not True
                or result.get("boot_acceptance") is not True or result.get("boot_checks") != running["checks"]
                or len(actual) != len(commands)):
            raise ValueError("Kernel lifecycle results are incomplete")
        for recorded, (command, marker) in zip(actual, commands):
            if (recorded.get("command") != command or recorded.get("expected") != marker
                    or recorded.get("passed") is not True or marker not in recorded.get("response", "")):
                raise ValueError("Kernel lifecycle command evidence is incomplete")
    return data, summary, pins


def proof_zip(data):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, content in sorted(data.items()):
            entry = zipfile.ZipInfo("thoxinux-ci-kernel-proof/" + name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, content)
    return output.getvalue()


def checksums(products):
    return "".join(f"{digest(data)}  {name}\n" for name, data in sorted(products.items())).encode()


def prepare(root, proof, output, repository=REPOSITORY):
    identity = context(root, repository)
    data, summary, pins = validate_proof(root, proof)
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a new release output directory; existing evidence is preserved")
    packer = module(root, "build_release.py", "thox_release_packer")
    with tempfile.TemporaryDirectory(prefix="thox-release-") as temporary:
        stage = Path(temporary)
        packer.build(root, stage)
        products = {name: read_regular(stage / name) for name in DATA_ASSETS[:3]}
    products.update({PROOF_ZIP: proof_zip(data), SUMMARY: data["summary.json"]})
    manifest = {"schema": "thoxinux.azure.ci-release.v1", **identity,
                "maturity": "engineering-preview", "azure_deployed": False,
                "kernel_release": pins["release"], "kernel_efi_sha256": pins["efi_sha256"],
                "verification_scope": summary["scope"],
                "assets": {name: {"sha256": digest(content), "size_bytes": len(content)}
                           for name, content in sorted(products.items())}}
    products[MANIFEST] = json_bytes(manifest)
    products[CHECKSUMS] = checksums(products)
    output.mkdir(parents=True)
    for name, content in products.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    return manifest


def validate_bundle(root, directory, identity):
    if (directory.is_symlink() or not directory.is_dir()
            or set(p.name for p in directory.iterdir()) != set(ASSETS)):
        raise ValueError("Release bundle has an unexpected asset inventory")
    products = {name: read_regular(directory / name) for name in ASSETS}
    manifest = json.loads(products[MANIFEST])
    validate_manifest(manifest, identity)
    if (manifest["run_id"] != identity["run_id"] or manifest["run_url"] != identity["run_url"]
            or int(manifest["run_attempt"]) > int(identity["run_attempt"])):
        raise ValueError("Release input artifact does not belong to this workflow run or an earlier attempt")
    for name in DATA_ASSETS:
        if manifest["assets"][name] != {"sha256": digest(products[name]), "size_bytes": len(products[name])}:
            raise ValueError(f"Release asset integrity mismatch: {name}")
    if products[CHECKSUMS] != checksums({name: data for name, data in products.items() if name != CHECKSUMS}):
        raise ValueError("Release SHA256SUMS do not match the actual asset bytes")
    # Recheck the transported proof independently of its outer manifest. Never
    # extract caller-selected ZIP paths, even after a checksum matches.
    prefix = "thoxinux-ci-kernel-proof/"
    with zipfile.ZipFile(io.BytesIO(products[PROOF_ZIP])) as archive:
        entries = archive.infolist()
        if (len(entries) != len(PROOF_FILES)
                or set(archive.namelist()) != {prefix + name for name in PROOF_FILES}
                or any(entry.file_size > MAX_ASSET_BYTES for entry in entries)
                or sum(entry.file_size for entry in entries) > MAX_ASSET_BYTES):
            raise ValueError("Transported kernel proof has an invalid ZIP inventory or size")
        data = {name: archive.read(prefix + name) for name in PROOF_FILES}
    if data["summary.json"] != products[SUMMARY]:
        raise ValueError("Exported CI summary differs from the enclosed kernel proof")
    with tempfile.TemporaryDirectory(prefix="thox-proof-readback-") as temporary:
        proof = Path(temporary)
        for name, content in data.items():
            (proof / name).write_bytes(content)
        _, _, pins = validate_proof(root, proof)
    if (manifest.get("kernel_release") != pins["release"]
            or manifest.get("kernel_efi_sha256") != pins["efi_sha256"]):
        raise ValueError("Release manifest kernel identity differs from the verified pins")
    packer = module(root, "build_release.py", "thox_publish_packer")
    actual_source, _ = packer.archive_bytes(packer.collect_sources(root))
    if products[SOURCE_ZIP] != actual_source:
        raise ValueError("Release source ZIP differs from the verified checkout")
    return manifest, products


def validate_manifest(manifest, identity):
    if (not isinstance(manifest, dict) or manifest.get("schema") != "thoxinux.azure.ci-release.v1"
            or any(manifest.get(key) != identity[key] for key in ("repository", "source_commit", "version", "tag"))
            or manifest.get("maturity") != "engineering-preview" or manifest.get("azure_deployed") is not False
            or manifest.get("verification_scope") != "local_qemu_tcg_only"
            or set(manifest.get("assets", {})) != set(DATA_ASSETS)):
        raise ValueError("Release manifest does not identify this verified source and scope")
    if (any(not re.fullmatch(r"[1-9][0-9]*", str(manifest.get(key, "")))
            for key in ("run_id", "run_attempt"))
            or manifest.get("run_url") != f"https://github.com/{identity['repository']}/actions/runs/{manifest['run_id']}"):
        raise ValueError("Release manifest has invalid workflow provenance")
    for entry in manifest["assets"].values():
        if (not isinstance(entry, dict) or not SHA256.fullmatch(str(entry.get("sha256", "")))
                or type(entry.get("size_bytes")) is not int or not 0 < entry["size_bytes"] <= MAX_ASSET_BYTES):
            raise ValueError("Invalid release asset manifest entry")


class GitHub:
    """Use authenticated gh without shell interpolation or token output."""
    def __init__(self, repository):
        self.repository = repository

    def command(self, *args):
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"GitHub command failed: {result.stderr.strip()[:2000]}")
        return result.stdout

    def get(self, path, missing=False):
        result = subprocess.run(["gh", "api", "--include", path], capture_output=True, text=True, timeout=60)
        response = result.stdout.replace("\r\n", "\n")
        header, separator, body = response.partition("\n\n")
        status = re.search(r"^HTTP/\S+\s+(\d{3})\b", header)
        if missing and status and status[1] == "404":
            return None
        if result.returncode or not separator or not status or status[1] != "200":
            raise RuntimeError(f"Cannot read GitHub release state: {result.stderr.strip()[:2000]}")
        return json.loads(body)

    def tag_commit(self, tag):
        ref = self.get(f"repos/{self.repository}/git/ref/tags/{tag}", missing=True)
        if ref is None:
            return None
        obj = ref.get("object", {})
        for _ in range(5):
            if not SHA.fullmatch(str(obj.get("sha", ""))):
                raise ValueError("GitHub returned an invalid tag object")
            if obj.get("type") == "commit":
                return obj["sha"]
            if obj.get("type") != "tag":
                break
            obj = self.get(f"repos/{self.repository}/git/tags/{obj['sha']}").get("object", {})
        raise ValueError("Release tag does not resolve to a commit within the allowed depth")

    def release(self, tag):
        # The tag endpoint returns published releases only. Authenticated release
        # listings include drafts for a token with push access, which is needed
        # to resume an upload interrupted before the tag has been created.
        matches = []
        for page in range(1, 11):
            records = self.get(f"repos/{self.repository}/releases?per_page=100&page={page}")
            if not isinstance(records, list):
                raise ValueError("GitHub release listing is not an array")
            matches.extend(item for item in records if item.get("tag_name") == tag)
            if len(records) < 100:
                break
        else:
            raise ValueError("Release history exceeds the bounded reconciliation scan")
        if len(matches) > 1:
            raise ValueError("Multiple releases use this tag; reconcile without modifying them")
        return matches[0] if matches else None

    def manifest(self, tag):
        raw = self.command("release", "download", tag, "--repo", self.repository,
                           "--pattern", MANIFEST, "--output", "-")
        if len(raw) > 1024 * 1024:
            raise ValueError("Remote release manifest exceeds the size bound")
        return json.loads(raw)


def marker(identity):
    return f"<!-- thoxinux-azure-release source={identity['source_commit']} tag={identity['tag']} -->"


def release_state(gh, identity):
    tag_sha = gh.tag_commit(identity["tag"])
    if tag_sha is not None and tag_sha != identity["source_commit"]:
        raise ValueError("Issued tag points at a different commit; update VERSION instead of moving it")
    release = gh.release(identity["tag"])
    if release is not None:
        if (release.get("tag_name") != identity["tag"] or release.get("prerelease") is not True
                or marker(identity) not in release.get("body", "")
                or (release.get("draft") is True and release.get("target_commitish") != identity["source_commit"])
                or (release.get("draft") is not True and tag_sha != identity["source_commit"])):
            raise ValueError("Existing release does not belong to this exact engineering preview")
    return release


def remote_assets(release):
    assets = release.get("assets", [])
    indexed = {item.get("name"): item for item in assets}
    if len(indexed) != len(assets) or not set(indexed).issubset(ASSETS):
        raise ValueError("Existing release has conflicting or unexpected assets")
    return indexed


def check_remote_asset(item, sha256, size):
    if (item.get("state") != "uploaded" or item.get("digest") != "sha256:" + sha256
            or item.get("size") != size):
        raise ValueError(f"Existing release asset differs or is incomplete: {item.get('name')}")


def verify_published(gh, release, identity, verified_manifest):
    assets = remote_assets(release)
    if set(assets) != set(ASSETS):
        raise ValueError("Published release is incomplete; existing published assets are never replaced")
    remote_manifest = gh.manifest(identity["tag"])
    validate_manifest(remote_manifest, identity)
    if (remote_manifest["assets"][SOURCE_ZIP]["sha256"] != verified_manifest["assets"][SOURCE_ZIP]["sha256"]
            or any(remote_manifest.get(key) != verified_manifest[key]
                   for key in ("kernel_release", "kernel_efi_sha256"))):
        raise ValueError("Existing release source package differs from this exact checkout")
    for name, meta in remote_manifest["assets"].items():
        check_remote_asset(assets[name], meta["sha256"], meta["size_bytes"])
    encoded = json_bytes(remote_manifest)
    check_remote_asset(assets[MANIFEST], digest(encoded), len(encoded))
    expected = {name: meta["sha256"] for name, meta in remote_manifest["assets"].items()}
    expected[MANIFEST] = digest(encoded)
    sums = "".join(f"{sha}  {name}\n" for name, sha in sorted(expected.items())).encode()
    check_remote_asset(assets[CHECKSUMS], digest(sums), len(sums))


def publish(root, directory, repository=REPOSITORY, gh=None):
    identity = context(root, repository, publish=True)
    manifest, products = validate_bundle(root, directory, identity)
    if shutil.which("gh") is None and gh is None:
        raise ValueError("Install the authenticated GitHub CLI before publication")
    gh = gh or GitHub(repository)
    repo = gh.get(f"repos/{repository}")
    if repo.get("full_name", "").lower() != repository.lower() or repo.get("private") is not False:
        raise ValueError("The authorized release destination must be the expected public repository")
    release = release_state(gh, identity)
    if release is not None and release.get("draft") is not True:
        # A repeated run can produce different timestamps in fresh kernel proof.
        # Keep and verify the originally issued evidence for this same source.
        verify_published(gh, release, identity, manifest)
        return {"result": "already-published", "url": release["html_url"], "tag": identity["tag"],
                "source_commit": identity["source_commit"]}
    if release is None:
        notes = read_regular(root / "RELEASE_NOTES.md").decode()
        notes += (f"\n## Issuing verification\n\nIntegration commit: `{identity['source_commit']}`.\n\n"
                  f"[Actual CI run]({manifest['run_url']}) (attempt {manifest['run_attempt']}).\n\n" + marker(identity) + "\n")
        with tempfile.TemporaryDirectory(prefix="thox-release-notes-") as temporary:
            path = Path(temporary) / "notes.md"
            path.write_text(notes)
            gh.command("release", "create", identity["tag"], "--repo", repository,
                       "--target", identity["source_commit"], "--title",
                       f"ThoxInux Azure {identity['tag']}: engineering preview",
                       "--draft", "--prerelease", "--notes-file", str(path))
        release = release_state(gh, identity)
        if release is None or release.get("draft") is not True:
            raise ValueError("Draft creation did not yield the expected release; reconcile before retrying")
    assets = remote_assets(release)
    for name, item in assets.items():
        check_remote_asset(item, digest(products[name]), len(products[name]))
    missing = [str(directory / name) for name in ASSETS if name not in assets]
    if missing:
        gh.command("release", "upload", identity["tag"], "--repo", repository, *missing)
    release = release_state(gh, identity)
    if release is None or release.get("draft") is not True:
        raise ValueError("Release state changed during staging; refusing to publish")
    assets = remote_assets(release)
    if set(assets) != set(ASSETS):
        raise ValueError("Draft assets are incomplete; rerun this same release job to resume safely")
    for name, item in assets.items():
        check_remote_asset(item, digest(products[name]), len(products[name]))
    gh.command("release", "edit", identity["tag"], "--repo", repository, "--draft=false", "--prerelease")
    release = release_state(gh, identity)
    if release is None or release.get("draft") is not False:
        raise ValueError("Publication was not confirmed; reconcile remote state before retrying")
    verify_published(gh, release, identity, manifest)
    return {"result": "published", "url": release["html_url"], "tag": identity["tag"],
            "source_commit": identity["source_commit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("requested", help="Emit the push's VERSION-based release decision")
    prepare_args = commands.add_parser("prepare", help="Package the current run's accepted kernel evidence")
    prepare_args.add_argument("--proof-dir", required=True, type=Path)
    prepare_args.add_argument("--output-dir", required=True, type=Path)
    publish_args = commands.add_parser("publish", help="Publish or verify this exact engineering prerelease")
    publish_args.add_argument("--artifact-dir", required=True, type=Path)
    for subparser in (prepare_args, publish_args):
        subparser.add_argument("--repository", default=REPOSITORY)
    args = parser.parse_args()
    try:
        if args.command == "requested":
            print("publish_release=" + str(release_requested(ROOT)).lower())
            return 0
        if args.command == "prepare":
            result = prepare(ROOT, args.proof_dir, args.output_dir, args.repository)
        else:
            result = publish(ROOT, args.artifact_dir, args.repository)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"Release stopped: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
