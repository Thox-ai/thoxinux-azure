#!/usr/bin/env python3
"""Create one new public GitHub repository and publish this clean main branch.

Requires an already authenticated GitHub CLI. No login, force push, existing
repository update, Azure deployment or Azure charge is performed by this tool.
Copyright 2026 THOX.ai LLC. SPDX-License-Identifier: Apache-2.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = "Thox-ai/thoxinux-azure"
PORTAL_PREFIX = "https://portal.azure.com/#create/Microsoft.Template/uri/"
DESCRIPTION = "Deploy the real ThoxInux kernel on Azure with a pinned release, portal button, automatic guest acceptance and reproducible validation."


class PublishError(RuntimeError):
    pass


def run(argv, root):
    # Read commands must not opportunistically refresh Git's index or gh tokens.
    # These selectors apply to this subprocess only, never the global config.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
           "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}
    try:
        return subprocess.run(argv, cwd=root, env=env, capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(f"Could not complete {argv[0]} ({type(exc).__name__}). No automatic retry was performed.") from exc


def checked(argv, root, action):
    result = run(argv, root)
    if result.returncode:
        # Do not echo command output that may include a locally configured
        # credential-bearing remote URL or authentication diagnostic.
        raise PublishError(f"{action} failed (exit {result.returncode}). No automatic retry was performed.")
    return result.stdout.strip()


def gh_json(arguments, root, action):
    output = checked(["gh", "api", "--hostname", "github.com", *arguments], root, action)
    try:
        return json.loads(output)
    except ValueError as exc:
        raise PublishError(f"{action} returned invalid JSON.") from exc


def validate_repository(repository):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", repository):
        raise PublishError("Use an explicit GitHub OWNER/NAME; URLs, option-like values and extra path segments are not accepted.")
    if repository.endswith(".git"):
        raise PublishError("Use OWNER/NAME without a .git suffix.")
    return repository


def canonical_remote(repository):
    return f"https://github.com/{repository}.git"


def remote_target(url):
    """Accept credential-free HTTPS or ordinary SSH URLs on github.com only."""
    match = re.fullmatch(r"git@github\.com:([^/?#]+/[^/?#]+?)(?:\.git)?", url)
    if match:
        return match.group(1).lower()
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "github.com" and not parsed.query and not parsed.fragment:
        return parsed.path.lstrip("/").removesuffix(".git").lower()
    if parsed.scheme == "ssh" and parsed.netloc == "git@github.com" and not parsed.query and not parsed.fragment:
        return parsed.path.lstrip("/").removesuffix(".git").lower()
    raise PublishError("A remote URL is not a credential-free github.com URL for the requested repository.")


def validate_readme_button(root, repository):
    readme = root / "README.md"
    if readme.is_symlink() or not readme.is_file():
        raise PublishError("README.md must be a regular tracked file.")
    text = readme.read_text()
    pattern = r"\[!\[Deploy to Azure\]\([^\s)]+\)\]\((https://portal\.azure\.com/#create/Microsoft\.Template/uri/[^\s)]+)\)"
    buttons = re.findall(pattern, text)
    expected = f"https://raw.githubusercontent.com/{repository}/main/portal/azuredeploy.json"
    if not buttons or any(unquote(button[len(PORTAL_PREFIX):]) != expected for button in buttons):
        raise PublishError(f"README Deploy to Azure button must target the public raw {repository}/main/portal/azuredeploy.json template.")
    return buttons[0]


def git_state(root, repository):
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise PublishError("This publisher requires a normal local Git repository with a .git directory. Initialize and commit the prepared source first.")
    top = checked(["git", "rev-parse", "--show-toplevel"], root, "Read Git root")
    if Path(top).resolve() != root.resolve():
        raise PublishError("The publisher is not at the intended repository root.")
    if checked(["git", "rev-parse", "--is-bare-repository"], root, "Inspect Git repository") != "false":
        raise PublishError("A bare repository cannot be published by this helper.")
    branch = checked(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], root, "Read current Git branch")
    if branch != "main":
        raise PublishError("The checked-out branch must be main. The publisher never renames branches.")
    commit = checked(["git", "rev-parse", "--verify", "HEAD"], root, "Read committed source")
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit):
        raise PublishError("Git did not return a valid committed source ID.")
    if checked(["git", "status", "--porcelain=v1", "--untracked-files=all"], root, "Check clean working tree"):
        raise PublishError("Uncommitted or untracked changes exist. Review and commit the complete package before publication.")
    checked(["git", "ls-files", "--error-unmatch", "README.md", "portal/azuredeploy.json"], root, "Verify tracked deployment entry points")
    remotes = checked(["git", "remote"], root, "Read local remotes").splitlines()
    if any(name != "origin" for name in remotes):
        raise PublishError("Unexpected existing remotes are present. Use the prepared isolated repository; no remotes were modified.")
    if "origin" in remotes:
        for options in (["--all"], ["--push", "--all"]):
            urls = checked(["git", "remote", "get-url", *options, "origin"], root, "Read origin target").splitlines()
            if not urls or any(remote_target(url) != repository.lower() for url in urls):
                raise PublishError("Existing origin fetch or push target does not match the requested repository.")
    # Inspect URL rewrite rules without adding a temporary remote. Git only
    # treats file-backed remote entries as configured for `remote get-url`.
    # Refuse any matching rewrite that could move this new origin elsewhere.
    rewrites = run(["git", "config", "--get-regexp", r"^url\..*\.(insteadof|pushinsteadof)$"], root)
    if rewrites.returncode not in (0, 1):
        raise PublishError("Git URL rewrite configuration could not be inspected.")
    source_url = canonical_remote(repository)
    for line in rewrites.stdout.splitlines():
        try:
            key, prefix = line.split(None, 1)
        except ValueError as exc:
            raise PublishError("Git contains an uninspectable URL rewrite rule.") from exc
        match = re.fullmatch(r"url\.(.+)\.(?:insteadof|pushinsteadof)", key, re.IGNORECASE)
        if not match:
            raise PublishError("Git contains an uninspectable URL rewrite rule.")
        if source_url.startswith(prefix):
            target = match.group(1) + source_url[len(prefix):]
            if remote_target(target) != repository.lower():
                raise PublishError("Git URL rewriting redirects the requested publication target.")
    return {"commit": commit, "origin_exists": "origin" in remotes}


def require_remote_absent(root, repository):
    response = run(["gh", "api", "--hostname", "github.com", "--include", f"repos/{repository}"], root)
    statuses = re.findall(r"^HTTP/\S+\s+(\d{3})\b", response.stdout, flags=re.MULTILINE)
    if response.returncode == 0:
        raise PublishError("The GitHub repository already exists. This helper only creates a new repository and never updates an existing one.")
    if not statuses or statuses[-1] != "404":
        raise PublishError("GitHub did not confirm HTTP 404 for the requested repository. Authentication, rate limits or network access may be blocking the check; no creation attempted.")


def preflight(root, repository):
    repository = validate_repository(repository)
    for program in ("git", "gh"):
        if not shutil.which(program):
            raise PublishError(f"{program} is unavailable. Install it and authenticate gh outside this script before publication.")
    state = git_state(root, repository)
    button = validate_readme_button(root, repository)
    checked(["gh", "auth", "status", "--active", "--hostname", "github.com", "--no-refresh"],
            root, "Verify existing GitHub authentication without refresh")
    identity = gh_json(["user"], root, "Read authenticated GitHub identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("login"), str) or not identity["login"]:
        raise PublishError("The authenticated GitHub identity could not be established.")
    require_remote_absent(root, repository)
    template = root / "portal" / "azuredeploy.json"
    if template.is_symlink() or not template.is_file():
        raise PublishError("Portal template must be a regular tracked file.")
    try:
        json.loads(template.read_bytes())
    except ValueError as exc:
        raise PublishError("Portal template is not valid JSON.") from exc
    return {**state, "repository": repository, "actor": identity["login"], "button_url": button,
            "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest()}


def push_command():
    # Use gh's already-authenticated helper for this one operation. Nothing is
    # added to the user's global Git credential configuration.
    return ["git", "-c", "credential.helper=", "-c", "credential.https://github.com.helper=!gh auth git-credential",
            "push", "--set-upstream", "origin", "main:main"]


def verify_public_template(repository, expected_sha256):
    url = f"https://raw.githubusercontent.com/{repository}/main/portal/azuredeploy.json"
    for attempt in range(3):
        try:
            # Deliberately unauthenticated. This proves the Azure template URI
            # is publicly readable without a GitHub credential.
            request = Request(url, headers={"User-Agent": "THOX-Azure-Publication-Check"})
            with urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise PublishError("Public template did not return HTTP 200.")
                data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha256:
                raise PublishError("Public template bytes differ from the committed portal template.")
            return url
        except (HTTPError, URLError, OSError):
            if attempt == 2:
                raise PublishError("main was pushed, but anonymous access to the raw portal template could not be verified. Repository creation is not retried.")
            time.sleep(2)


def publish(root, repository, *, dry_run=False):
    state = preflight(root, repository)
    create = ["gh", "repo", "create", repository, "--public", "--description", DESCRIPTION]
    add_remote = ["git", "remote", "add", "origin", canonical_remote(repository)]
    actions = [create] + ([] if state["origin_exists"] else [add_remote]) + [push_command()]
    if dry_run:
        return {"stage": "dry_run", "repository": repository, "visibility": "public", "branch": "main",
                "commit": state["commit"], "actor": state["actor"], "actions": actions,
                "button_url": state["button_url"], "write_actions_executed": False,
                "azure_resources_created": False, "azure_charges_created": False}
    # Repeat local and remote checks immediately before the first mutation.
    if git_state(root, repository) != {"commit": state["commit"], "origin_exists": state["origin_exists"]}:
        raise PublishError("Local Git state changed after preflight. Nothing was created.")
    validate_readme_button(root, repository)
    require_remote_absent(root, repository)
    try:
        checked(create, root, "Create NEW PUBLIC GitHub repository")
    except PublishError as exc:
        raise PublishError(f"Repository creation request did not complete successfully: {exc} Its outcome may be uncertain. Inspect https://github.com/{repository} before retrying; no push or automatic write retry was attempted.") from exc
    try:
        created = gh_json([f"repos/{repository}"], root, "Verify newly created repository")
        if not isinstance(created, dict) or created.get("full_name", "").lower() != repository.lower() or created.get("private") is not False:
            raise PublishError("The newly created repository identity or public visibility does not match.")
        branches = gh_json([f"repos/{repository}/branches"], root, "Verify new repository is empty")
        if branches != []:
            raise PublishError("The newly created repository unexpectedly has branches. No push was attempted.")
        if not state["origin_exists"]:
            checked(add_remote, root, "Add local origin for newly created repository")
        current = git_state(root, repository)
        if current["commit"] != state["commit"]:
            raise PublishError("Local commit changed after creation. No push was attempted.")
        checked(push_command(), root, "Push main without force")
        remote = gh_json([f"repos/{repository}/git/ref/heads/main"], root, "Verify pushed main commit")
        if not isinstance(remote, dict) or remote.get("object", {}).get("sha") != state["commit"]:
            raise PublishError("GitHub main does not match the reviewed local commit.")
        template_url = verify_public_template(repository, state["template_sha256"])
    except PublishError as exc:
        raise PublishError(f"Repository was created at https://github.com/{repository}, but publication did not complete verification: {exc} Inspect its actual state before any manual follow-up. This helper will refuse an existing repository on rerun.") from exc
    return {"stage": "published_and_public_template_verified", "repository": f"https://github.com/{repository}",
            "visibility": "public", "branch": "main", "commit": state["commit"],
            "template_url": template_url, "template_sha256": state["template_sha256"], "button_url": state["button_url"],
            "azure_resources_created": False, "azure_charges_created": False,
            "azure_live_validation": "not_performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, metavar="OWNER/NAME")
    parser.add_argument("--dry-run", action="store_true", help="Perform read-only checks and print actions; do not create, add a remote or push")
    args = parser.parse_args()
    try:
        print(json.dumps(publish(ROOT, args.repository, dry_run=args.dry_run), indent=2))
    except (PublishError, OSError) as exc:
        print(f"PUBLICATION BLOCKED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
