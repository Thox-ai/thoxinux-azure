"""Offline release boundaries using real temporary Git checkouts and a fake API.

The recorded local kernel proof is used only as a fixture. These tests neither
run GitHub Actions nor publish a release or assert live Azure acceptance.
"""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("thox_create_release", ROOT / "tools/create_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
packer = release.module(ROOT, "build_release.py", "thox_release_test_packer")


class FakeGitHub:
    """In-memory stateful API double; never invokes gh or a network call."""
    def __init__(self, identity):
        self.identity = identity
        self.tag = None
        self.record = None
        self.uploads = {}
        self.mutations = []
        self.private = False

    def get(self, path, missing=False):
        if path != "repos/" + release.REPOSITORY:
            raise AssertionError(path)
        return {"full_name": release.REPOSITORY, "private": self.private}

    def tag_commit(self, tag):
        return self.tag

    def release(self, tag):
        return deepcopy(self.record)

    def manifest(self, tag):
        return json.loads(self.uploads[release.MANIFEST])

    def command(self, *args):
        self.mutations.append(args)
        operation = args[1]
        if operation == "create":
            if self.record is not None:
                raise AssertionError("Attempt to recreate a release")
            target = args[args.index("--target") + 1]
            self.record = {"tag_name": args[2], "target_commitish": target,
                           "prerelease": "--prerelease" in args, "draft": "--draft" in args,
                           "html_url": f"https://github.com/{release.REPOSITORY}/releases/tag/{args[2]}",
                           "body": Path(args[args.index("--notes-file") + 1]).read_text(), "assets": []}
        elif operation == "upload":
            self.assert_no_overwrite(args)
            paths = args[args.index("--repo") + 2:]
            for name in paths:
                path = Path(name)
                content = path.read_bytes()
                if path.name in self.uploads:
                    raise AssertionError("Attempt to overwrite an asset")
                self.uploads[path.name] = content
                self.record["assets"].append({"name": path.name, "state": "uploaded",
                                              "digest": "sha256:" + release.digest(content),
                                              "size": len(content)})
        elif operation == "edit":
            if "--draft=false" not in args or "--prerelease" not in args:
                raise AssertionError(args)
            self.record["draft"] = False
            self.tag = self.record["target_commitish"]
        else:
            raise AssertionError(args)
        return ""

    def assert_no_overwrite(self, args):
        if "--clobber" in args or "--force" in args:
            raise AssertionError("Destructive publication flag")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "source"
        self.root.mkdir()
        for relative in packer.REQUIRED_PATHS:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("public package fixture: " + relative + "\n")
        for relative in ("VERSION", "RELEASE_NOTES.md", "tools/build_release.py", "tools/verify_kernel.py",
                         "payload/pins.json", "payload/run-vm.sh", "payload/guest_service.py"):
            shutil.copyfile(ROOT / relative, self.root / relative)
        self.proof = self.base / "proof"
        self.proof.mkdir()
        for name in release.PROOF_FILES:
            shutil.copyfile(ROOT / "validation/portable-qemu" / name, self.proof / name)
        self.git("init", "-b", "main")
        self.commit("Initial test source")
        self.sha = self.git("rev-parse", "HEAD")
        self.environment = {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": release.REPOSITORY,
            "GITHUB_SHA": self.sha, "GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
            "THOX_PUSH_BEFORE": "0" * 40, "GH_TOKEN": "unit-test-placeholder-not-a-token",
        }
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.identity = release.context(self.root, release.REPOSITORY)
        self.api = FakeGitHub(self.identity)
        self.output = self.base / "products"

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              text=True, capture_output=True).stdout.strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("-c", "user.name=Release test", "-c", "user.email=tests@example.invalid",
                 "commit", "--quiet", "-m", message)

    def prepare(self):
        return release.prepare(self.root, self.proof, self.output)

    def publish(self):
        return release.publish(self.root, self.output, gh=self.api)

    def rewrite_proof(self, name, change):
        path = self.proof / name
        data = json.loads(path.read_text())
        change(data)
        path.write_bytes(release.json_bytes(data))

    def rewrite_bundle(self, product_changes=None, manifest_changes=None):
        """Attacker/transport fixture with self-consistent recomputed checksums."""
        products = {name: (self.output / name).read_bytes() for name in release.ASSETS}
        products.update(product_changes or {})
        manifest = json.loads(products[release.MANIFEST])
        for name in release.DATA_ASSETS:
            manifest["assets"][name] = {"sha256": release.digest(products[name]), "size_bytes": len(products[name])}
        manifest.update(manifest_changes or {})
        products[release.MANIFEST] = release.json_bytes(manifest)
        products[release.CHECKSUMS] = release.checksums({k: v for k, v in products.items() if k != release.CHECKSUMS})
        for name, content in products.items():
            (self.output / name).write_bytes(content)

    def test_prepare_binds_exact_source_and_actual_recorded_proof_bytes(self):
        manifest = self.prepare()
        actual, products = release.validate_bundle(self.root, self.output, self.identity)
        self.assertEqual(manifest, actual)
        self.assertEqual(manifest["source_commit"], self.sha)
        self.assertEqual(set(products), set(release.ASSETS))
        self.assertEqual(products[release.SUMMARY], (self.proof / "summary.json").read_bytes())
        self.assertIs(manifest["azure_deployed"], False)
        self.assertIn("local_qemu_tcg_only", manifest["verification_scope"])

    def test_rejects_false_summary_even_when_other_counts_look_valid(self):
        for key, value in (("result", "failed"), ("azure_deployed", True),
                           ("final_guest_state", "running"), ("boots_passed", 1)):
            with self.subTest(key=key):
                path = self.proof / "summary.json"
                original = path.read_bytes()
                self.rewrite_proof("summary.json", lambda data: data.update({key: value}))
                with self.assertRaisesRegex(ValueError, "local-only acceptance"):
                    self.prepare()
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_missing_kernel_commands_and_changed_serial_are_rejected(self):
        original = (self.proof / "cold-results.json").read_bytes()
        self.rewrite_proof("cold-results.json", lambda data: data["commands"].pop())
        with self.assertRaisesRegex(ValueError, "lifecycle results"):
            self.prepare()
        (self.proof / "cold-results.json").write_bytes(original)
        with (self.proof / "serial.log").open("ab") as stream:
            stream.write(b"changed bytes")
        with self.assertRaisesRegex(ValueError, "serial log"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_different_harness_or_running_final_receipt_cannot_be_issued(self):
        path = self.proof / "summary.json"
        original = path.read_bytes()
        self.rewrite_proof("summary.json", lambda data: data.update(harness_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "different source bytes"):
            self.prepare()
        path.write_bytes(original)
        self.rewrite_proof("cold-final-receipt.json", lambda data: data.update(running=True))
        with self.assertRaisesRegex(ValueError, "clean stopped receipt"):
            self.prepare()

    def test_unexpected_and_symlinked_evidence_is_never_packaged(self):
        unexpected = self.proof / "private.key"
        unexpected.write_text("not a real secret")
        with self.assertRaisesRegex(ValueError, "exact complete evidence inventory"):
            self.prepare()
        unexpected.unlink()
        target = self.proof / "serial.log"
        original = self.base / "outside-log"
        target.rename(original)
        target.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.prepare()

    def test_wrong_event_repo_missing_token_and_sha_cannot_publish(self):
        self.prepare()
        for key, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
                           ("GITHUB_REF", "refs/heads/feature"), ("GITHUB_REPOSITORY", "Elsewhere/repo"),
                           ("GH_TOKEN", ""), ("GITHUB_SHA", "b" * 40)):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.api.mutations, [])

    def test_dirty_checkout_and_changed_bundle_fail_before_remote_mutation(self):
        self.prepare()
        with (self.root / "README.md").open("a") as stream:
            stream.write("changed source")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            self.publish()
        self.git("restore", "README.md")
        with (self.output / release.PROOF_ZIP).open("ab") as stream:
            stream.write(b"changed artifact")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_checksummed_but_wrong_source_zip_is_still_rejected(self):
        self.prepare()
        self.rewrite_bundle({release.SOURCE_ZIP: b"wrong checkout archive"})
        with self.assertRaisesRegex(ValueError, "source ZIP differs"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_recomputed_hashes_do_not_make_incomplete_kernel_proof_acceptable(self):
        self.prepare()
        data = {name: (self.proof / name).read_bytes() for name in release.PROOF_FILES}
        result = json.loads(data["cold-results.json"])
        result["commands"].pop()
        data["cold-results.json"] = release.json_bytes(result)
        self.rewrite_bundle({release.PROOF_ZIP: release.proof_zip(data)})
        with self.assertRaisesRegex(ValueError, "lifecycle results"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_exported_summary_and_manifest_kernel_pins_must_match_enclosed_evidence(self):
        self.prepare()
        original = (self.output / release.SUMMARY).read_bytes()
        changed = json.loads(original)
        changed["ended_at"] = "2026-11-03T00:00:00+00:00"
        self.rewrite_bundle({release.SUMMARY: release.json_bytes(changed)})
        with self.assertRaisesRegex(ValueError, "Exported CI summary"):
            self.publish()
        self.rewrite_bundle({release.SUMMARY: original}, {"kernel_efi_sha256": "f" * 64})
        with self.assertRaisesRegex(ValueError, "kernel identity"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_bundle_is_bound_to_current_run_but_original_attempt_survives_job_rerun(self):
        self.prepare()
        self.rewrite_bundle(manifest_changes={"run_id": "9999",
                                               "run_url": f"https://github.com/{release.REPOSITORY}/actions/runs/9999"})
        with self.assertRaisesRegex(ValueError, "this workflow run"):
            self.publish()
        self.assertEqual(self.api.mutations, [])
        self.rewrite_bundle(manifest_changes={"run_id": self.identity["run_id"], "run_url": self.identity["run_url"]})
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
            self.assertEqual(self.publish()["result"], "published")

    def test_new_release_stages_all_assets_then_publishes_exact_commit(self):
        self.prepare()
        result = self.publish()
        self.assertEqual(result["result"], "published")
        self.assertEqual(self.api.tag, self.sha)
        self.assertEqual(self.api.record["target_commitish"], self.sha)
        self.assertIs(self.api.record["prerelease"], True)
        self.assertIs(self.api.record["draft"], False)
        self.assertEqual(set(self.api.uploads), set(release.ASSETS))
        self.assertEqual([args[1] for args in self.api.mutations], ["create", "upload", "edit"])

    def test_same_commit_rerun_verifies_original_release_without_writes(self):
        self.prepare()
        self.publish()
        self.api.mutations.clear()
        # A subsequent accepted run has fresh timestamps but the same source.
        summary = self.proof / "summary.json"
        data = json.loads(summary.read_text())
        data["ended_at"] = "2026-11-03T00:00:00+00:00"
        summary.write_bytes(release.json_bytes(data))
        shutil.rmtree(self.output)
        with patch.dict(os.environ, {"GITHUB_RUN_ATTEMPT": "2"}):
            self.prepare()
            result = self.publish()
        self.assertEqual(result["result"], "already-published")
        self.assertEqual(self.api.mutations, [])

    def test_conflicting_tag_is_not_moved_and_existing_release_is_not_recreated(self):
        self.prepare()
        self.api.tag = "f" * 40
        with self.assertRaisesRegex(ValueError, "different commit"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_unrelated_draft_and_private_destination_are_not_modified(self):
        self.prepare()
        self.api.record = {"tag_name": self.identity["tag"], "draft": True,
                           "prerelease": True, "target_commitish": self.sha, "body": "other release"}
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.publish()
        self.api.record = None
        self.api.private = True
        with self.assertRaisesRegex(ValueError, "expected public repository"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_partial_same_source_draft_resumes_missing_assets_without_clobber(self):
        self.prepare()
        original_command = self.api.command

        def fail_upload_once(*args):
            if args[1] == "upload":
                only_first = args[:args.index("--repo") + 2] + (args[args.index("--repo") + 2],)
                original_command(*only_first)
                raise RuntimeError("simulated connection loss after first upload")
            return original_command(*args)

        with patch.object(self.api, "command", side_effect=fail_upload_once):
            with self.assertRaisesRegex(RuntimeError, "simulated connection loss"):
                self.publish()
        self.assertIs(self.api.record["draft"], True)
        self.assertEqual(len(self.api.uploads), 1)
        first = deepcopy(self.api.uploads)
        self.api.mutations.clear()
        self.assertEqual(self.publish()["result"], "published")
        self.assertEqual([args[1] for args in self.api.mutations], ["upload", "edit"])
        for name, content in first.items():
            self.assertEqual(self.api.uploads[name], content)

    def test_corrupt_remote_asset_blocks_published_release_noop(self):
        self.prepare()
        self.publish()
        self.api.record["assets"][0]["digest"] = "sha256:" + "f" * 64
        self.api.mutations.clear()
        with self.assertRaisesRegex(ValueError, "differs or is incomplete"):
            self.publish()
        self.assertEqual(self.api.mutations, [])

    def test_version_changes_request_release_but_docs_changes_and_prs_do_not(self):
        self.assertTrue(release.release_requested(self.root))
        before = self.sha
        (self.root / "README.md").write_text("Documentation correction\n")
        self.commit("Docs only")
        with patch.dict(os.environ, {"THOX_PUSH_BEFORE": before, "GITHUB_SHA": self.git("rev-parse", "HEAD")}):
            self.assertFalse(release.release_requested(self.root))
        (self.root / "VERSION").write_text("0.1.0-rc.2\n")
        self.commit("New candidate")
        with patch.dict(os.environ, {"THOX_PUSH_BEFORE": before, "GITHUB_SHA": self.git("rev-parse", "HEAD")}):
            self.assertTrue(release.release_requested(self.root))
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
                self.assertFalse(release.release_requested(self.root))

    def test_api_only_treats_real_http_404_as_absent(self):
        gh = release.GitHub(release.REPOSITORY)
        for status, returncode in (("403 Forbidden", 1), ("500 Server Error", 1)):
            result = subprocess.CompletedProcess([], returncode, "HTTP/2 " + status + "\n\n{}", "request failed")
            with patch.object(release.subprocess, "run", return_value=result):
                with self.assertRaises(RuntimeError):
                    gh.get("repos/example/release", missing=True)
        missing = subprocess.CompletedProcess([], 1, 'HTTP/2 404 Not Found\r\n\r\n{"message":"Not Found"}', "HTTP 404")
        with patch.object(release.subprocess, "run", return_value=missing):
            self.assertIsNone(gh.get("repos/example/release", missing=True))

    def test_api_reconciles_unpublished_drafts_and_rejects_duplicate_tag_records(self):
        gh = release.GitHub(release.REPOSITORY)
        tag = self.identity["tag"]
        draft = {"tag_name": tag, "draft": True, "id": 23}
        with patch.object(gh, "get", return_value=[draft]) as read:
            self.assertEqual(gh.release(tag), draft)
            self.assertIn("/releases?", read.call_args.args[0])
        with patch.object(gh, "get", return_value=[draft, {**draft, "id": 24}]):
            with self.assertRaisesRegex(ValueError, "Multiple releases"):
                gh.release(tag)


if __name__ == "__main__":
    unittest.main()
