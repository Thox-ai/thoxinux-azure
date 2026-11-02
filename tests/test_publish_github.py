"""Publication guards use real temporary Git repos and explicit fake GitHub replies.

No test creates or pushes a real remote repository.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publish_github", ROOT / "tools" / "publish_github.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
REAL_RUN = publisher.run
REPOSITORY = "Thox-ai/thoxinux-azure"


def response(argv, code=0, output="", error=""):
    return subprocess.CompletedProcess(argv, code, output, error)


class FakeGitHub:
    """Only external GitHub behavior is simulated; local git checks are real."""
    def __init__(self):
        self.calls = []
        self.mutations = []
        self.auth_ok = True
        self.existing = False
        self.lookup_status = 404
        self.private_after_create = False
        self.branches_after_create = []
        self.push_fails = False
        self.created = False
        self.commit = None

    def __call__(self, argv, root):
        self.calls.append(list(argv))
        if argv[0] == "git":
            if "push" in argv:
                self.mutations.append("push")
                self.commit = REAL_RUN(["git", "rev-parse", "HEAD"], root).stdout.strip()
                return response(argv, 1 if self.push_fails else 0)
            if argv[:3] == ["git", "remote", "add"]:
                self.mutations.append("add-origin")
            return REAL_RUN(argv, root)
        if argv[:3] == ["gh", "auth", "status"]:
            return response(argv, 0 if self.auth_ok else 1)
        if argv[:3] == ["gh", "repo", "create"]:
            self.mutations.append("create-public")
            self.created = True
            return response(argv, output=f"https://github.com/{REPOSITORY}\n")
        if argv[:2] != ["gh", "api"]:
            raise AssertionError(f"Unexpected command: {argv}")
        endpoint = argv[-1]
        if endpoint == "user":
            return response(argv, output=json.dumps({"login": "approved-test-owner"}))
        if "--include" in argv:
            if self.existing:
                return response(argv, output="HTTP/2.0 200 OK\n\n{}")
            return response(argv, 1, f"HTTP/2.0 {self.lookup_status} Test\n\n{{}}")
        if endpoint == f"repos/{REPOSITORY}":
            return response(argv, output=json.dumps({"full_name": REPOSITORY, "private": self.private_after_create}))
        if endpoint.endswith("/branches"):
            return response(argv, output=json.dumps(self.branches_after_create))
        if endpoint.endswith("/git/ref/heads/main"):
            return response(argv, output=json.dumps({"object": {"sha": self.commit}}))
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


class PublicationGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "--initial-branch=main")
        (self.repo / "portal").mkdir()
        (self.repo / "portal" / "azuredeploy.json").write_text('{"test_fixture":true}\n')
        raw = f"https://raw.githubusercontent.com/{REPOSITORY}/main/portal/azuredeploy.json"
        button = publisher.PORTAL_PREFIX + quote(raw, safe="")
        self.readme = f"# Test fixture\n\n[![Deploy to Azure](https://aka.ms/deploytoazurebutton)]({button})\n"
        (self.repo / "README.md").write_text(self.readme)
        self.git("add", "README.md", "portal/azuredeploy.json")
        self.git("-c", "user.name=Publication Test", "-c", "user.email=publication@example.test", "commit", "-m", "offline test fixture")
        self.fake = FakeGitHub()
        self.which_patch = patch.object(publisher.shutil, "which", return_value="/available/tool")
        self.which_patch.start()
        self.addCleanup(self.which_patch.stop)
        self.run_patch = patch.object(publisher, "run", side_effect=self.fake)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def git(self, *args):
        result = REAL_RUN(["git", *args], self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def assert_no_mutation(self):
        self.assertEqual(self.fake.mutations, [])

    def test_dry_run_is_read_only_in_real_local_repo(self):
        before = self.git("status", "--porcelain=v1", "--untracked-files=all")
        result = publisher.publish(self.repo, REPOSITORY, dry_run=True)
        self.assertEqual(result["stage"], "dry_run")
        self.assertFalse(result["write_actions_executed"])
        self.assertFalse(result["azure_resources_created"])
        self.assertEqual(self.git("remote"), "")
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), before)
        self.assert_no_mutation()

    def test_missing_cli_blocks_before_mutation(self):
        with patch.object(publisher.shutil, "which", side_effect=lambda tool: None if tool == "gh" else "/git"):
            with self.assertRaisesRegex(publisher.PublishError, "gh is unavailable"):
                publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(self.fake.calls, [])

    def test_missing_git_directory_never_initializes_one(self):
        plain = self.repo / "untracked"
        plain.mkdir()
        with self.assertRaisesRegex(publisher.PublishError, "normal local Git"):
            publisher.publish(plain, REPOSITORY)
        self.assertFalse((plain / ".git").exists())
        self.assert_no_mutation()

    def test_dirty_and_untracked_files_are_blocked(self):
        (self.repo / "README.md").write_text(self.readme + "uncommitted\n")
        with self.assertRaisesRegex(publisher.PublishError, "Uncommitted"):
            publisher.publish(self.repo, REPOSITORY)
        self.git("restore", "README.md")
        (self.repo / "untracked.txt").write_text("not reviewed")
        with self.assertRaisesRegex(publisher.PublishError, "Uncommitted"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_other_branch_or_detached_head_is_blocked(self):
        self.git("switch", "-c", "development")
        with self.assertRaisesRegex(publisher.PublishError, "must be main"):
            publisher.publish(self.repo, REPOSITORY)
        self.git("checkout", "--detach")
        with self.assertRaisesRegex(publisher.PublishError, "current Git branch"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_wrong_readme_button_target_blocks_other_namespace(self):
        with self.assertRaisesRegex(publisher.PublishError, "README Deploy to Azure"):
            publisher.publish(self.repo, "OtherOwner/other-repo")
        self.assert_no_mutation()

    def test_existing_origin_mismatch_is_never_rewritten(self):
        self.git("remote", "add", "origin", "https://github.com/OtherOwner/other.git")
        with self.assertRaisesRegex(publisher.PublishError, "origin fetch or push target"):
            publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(self.git("remote", "get-url", "origin"), "https://github.com/OtherOwner/other.git")
        self.assert_no_mutation()

    def test_pushurl_mismatch_is_blocked_even_when_fetch_matches(self):
        self.git("remote", "add", "origin", publisher.canonical_remote(REPOSITORY))
        self.git("remote", "set-url", "--push", "origin", "https://github.com/OtherOwner/other.git")
        with self.assertRaisesRegex(publisher.PublishError, "origin fetch or push target"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_git_push_url_rewrite_is_detected_before_create(self):
        self.git("config", "url.https://example.invalid/.pushInsteadOf", "https://github.com/")
        with self.assertRaisesRegex(publisher.PublishError, "credential-free github.com"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_existing_github_repository_is_not_reused(self):
        self.fake.existing = True
        with self.assertRaisesRegex(publisher.PublishError, "already exists"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_lookup_failure_is_not_assumed_absent(self):
        self.fake.lookup_status = 403
        with self.assertRaisesRegex(publisher.PublishError, "did not confirm HTTP 404"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_failed_authentication_never_creates_repository(self):
        self.fake.auth_ok = False
        with self.assertRaisesRegex(publisher.PublishError, "authentication without refresh"):
            publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_changed_local_commit_between_checks_blocks_creation(self):
        original = publisher.git_state
        count = 0
        def changing(root, repository):
            nonlocal count
            state = original(root, repository)
            count += 1
            if count == 2:
                state["commit"] = "a" * 40
            return state
        with patch.object(publisher, "git_state", side_effect=changing):
            with self.assertRaisesRegex(publisher.PublishError, "changed after preflight"):
                publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_repository_created_by_another_actor_during_preflight_blocks_write(self):
        original = publisher.require_remote_absent
        count = 0
        def raced(root, repository):
            nonlocal count
            count += 1
            if count == 2:
                self.fake.existing = True
            return original(root, repository)
        with patch.object(publisher, "require_remote_absent", side_effect=raced):
            with self.assertRaisesRegex(publisher.PublishError, "already exists"):
                publisher.publish(self.repo, REPOSITORY)
        self.assert_no_mutation()

    def test_unexpected_new_repository_visibility_blocks_push(self):
        self.fake.private_after_create = True
        with self.assertRaisesRegex(publisher.PublishError, "Repository was created.*public visibility"):
            publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(self.fake.mutations, ["create-public"])

    def test_unexpected_new_repository_branches_block_push(self):
        self.fake.branches_after_create = [{"name": "main"}]
        with self.assertRaisesRegex(publisher.PublishError, "unexpectedly has branches"):
            publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(self.fake.mutations, ["create-public"])

    def test_push_failure_preserves_partial_state_without_retry(self):
        self.fake.push_fails = True
        with self.assertRaisesRegex(publisher.PublishError, "Repository was created.*Push main"):
            publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(self.fake.mutations, ["create-public", "add-origin", "push"])
        self.assertEqual(self.git("remote", "get-url", "origin"), publisher.canonical_remote(REPOSITORY))

    def test_new_public_repository_path_checks_commit_and_anonymous_template(self):
        expected = f"https://raw.githubusercontent.com/{REPOSITORY}/main/portal/azuredeploy.json"
        with patch.object(publisher, "verify_public_template", return_value=expected) as verify:
            result = publisher.publish(self.repo, REPOSITORY)
        self.assertEqual(result["stage"], "published_and_public_template_verified")
        self.assertEqual(result["commit"], self.git("rev-parse", "HEAD"))
        self.assertFalse(result["azure_resources_created"])
        self.assertEqual(self.fake.mutations, ["create-public", "add-origin", "push"])
        verify.assert_called_once_with(REPOSITORY, result["template_sha256"])


class PureInputGuards(unittest.TestCase):
    def test_option_like_url_or_nested_repository_is_rejected(self):
        for target in ("--public", "https://github.com/a/b", "a/b/c", "a/b.git", "a/$(bad)", "a/b\n"):
            with self.subTest(target=target), self.assertRaises(publisher.PublishError):
                publisher.validate_repository(target)

    def test_credential_bearing_or_other_host_remotes_are_rejected(self):
        for url in ("https://token@github.com/a/b.git", "https://github.com.evil.invalid/a/b.git", "file:///tmp/repo"):
            with self.subTest(url=url), self.assertRaises(publisher.PublishError):
                publisher.remote_target(url)
        self.assertEqual(publisher.remote_target("git@github.com:Thox-ai/thoxinux-azure.git"), "thox-ai/thoxinux-azure")


if __name__ == "__main__":
    unittest.main()
