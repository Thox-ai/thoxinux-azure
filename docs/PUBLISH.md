# Publish source and issue an engineering preview

The destination is the public **Thox-ai/thoxinux-azure** GitHub repository. The repository has been created. Source publication, CI verification, a formal integration release and Azure deployment are separate operations.

The Deploy to Azure button requires anonymous access to `main/portal/azuredeploy.json`. Its presence in an unpublished local README does not make the remote template available.

## Access required

The publishing account or GitHub App must have contents-write access to this repository, with the permissions needed to publish workflow files. A public read and a user's administrative role do not grant an integration write access. If a write returns `Resource not accessible by integration`, verify the app installation and repository selection for the owning organization. Do not substitute another account's credentials or remove protections.

The release job uses GitHub Actions' repository token with `contents: write` only in that job. Verification and pull-request jobs retain read-only permissions. Organization policy may restrict workflow tokens or Actions; report the actual failure instead of disabling its controls.

## Preserve the prepared Git history

The delivery archive includes a Git bundle and a separate source ZIP. To reconstruct the committed repository, run from the directory containing the bundle:

```bash
git clone --branch main thoxinux-azure.bundle thoxinux-azure
cd thoxinux-azure
git remote remove origin
```

The removed origin points only to the local bundle. This does not delete source or modify any remote repository. The source ZIP intentionally omits `.git`; extracting it does not reconstruct the commit history.

## Publish into the existing empty destination

Use the intended authorized GitHub account in a trusted terminal with Git, Python 3.11+ and a current GitHub CLI. Authenticate with GitHub's own flow if needed:

```bash
gh auth login --hostname github.com
gh auth status --active --hostname github.com --no-refresh
gh repo view Thox-ai/thoxinux-azure --json nameWithOwner,isPrivate,defaultBranchRef
```

Confirm the exact repository and public visibility, then inspect its branch inventory:

```bash
gh api repos/Thox-ai/thoxinux-azure/branches
```

The initial destination should return an empty array. If branches now exist, inspect and integrate the actual state; do not overwrite it or force-push this snapshot. From the clean reconstructed repository, add the exact origin and push main:

```bash
git remote add origin https://github.com/Thox-ai/thoxinux-azure.git
git -c credential.helper= \
  -c 'credential.https://github.com.helper=!gh auth git-credential' \
  push --set-upstream origin main:main
```

This push does not use force. The command-specific helper uses the existing GitHub CLI authentication without writing a global Git credential setting. Preserve any unexpected push error and inspect the remote state before retrying.

Set the GitHub About description through an account with repository administration access:

```bash
gh repo edit Thox-ai/thoxinux-azure \
  --description 'Deploy the real ThoxInux kernel on Azure with a pinned release, portal button, automatic guest acceptance and reproducible validation.'
```

A source README or workflow token does not establish that this metadata update succeeded. Read back the description after the operation.

## Verify publication

Compare the actual remote main commit with the local source:

```bash
git rev-parse HEAD
gh api repos/Thox-ai/thoxinux-azure/git/ref/heads/main --jq .object.sha
```

When publishing through GitHub's file API instead of pushing a bundle, commit history can differ. Verify the complete tree and exact file hashes in that case; do not claim identical commits.

Download the public template without authentication and compare its SHA-256 with `portal/azuredeploy.json`. The README button must decode to this exact raw target:

```text
https://raw.githubusercontent.com/Thox-ai/thoxinux-azure/main/portal/azuredeploy.json
```

The workflow page must show a completed successful verification run for the published commit before a CI success claim is made. Live Azure ARM validation is still a later step.

## Formal prerelease gate

The initial deployment integration version is **v0.1.0-rc.1**, independent of the kernel's **v0.9.1** release. `VERSION`, `CHANGELOG.md` and `RELEASE_NOTES.md` identify the intended integration version; they do not by themselves constitute a GitHub Release.

The workflow verifies every main-branch and pull-request change, including documentation. It runs the unit suite, checks generated files, boots the actual released kernel twice and packages the reviewed source plus the fresh CI evidence. A separate release job can publish only after verification succeeds for the exact source commit and the workflow's release-request conditions are met.

The release job uses the exact verification artifact ID and enforces its digest. It creates or verifies the immutable version tag, publishes an engineering prerelease and uploads the source, manifest, checksums and actual CI kernel evidence. It never overwrites an issued tag or an existing different asset. Same-source retries preserve the original evidence. An uncertain publication outcome requires readback before another write.

A new intended release requires an explicit VERSION change and corresponding release notes. Documentation-only changes still run verification but must not silently move the existing version tag. Manual workflow dispatch performs verification only. Rerun a failed release job to resume the same verified commit and artifact; a different commit requires its own reviewed version change.

Before reporting a release complete, verify its actual Release ID and URL, non-draft prerelease state, publication time, tag commit, notes, assets and downloaded hashes. A failed workflow or a draft candidate remains unfinished. No successful Azure deployment follows from a source release or local QEMU result.

## Creating a different new repository

`tools/publish_github.py` is retained for the separate case of creating a **new** public repository. It intentionally refuses a destination that already exists, so do not use it to initialize this already-created repository. For a different destination, first deliberately update and commit the README deployment button and applicable repository references.

The helper requires a clean committed main branch, verifies authentication and remote absence, creates the new public repository, pushes without force and checks anonymous access to the template. Its `--dry-run` performs read-only checks. It creates no Azure resources.

## References

- [GitHub App installation and authorization](https://docs.github.com/en/apps/using-github-apps/installing-a-github-app-from-a-third-party)
- [GitHub CLI authentication](https://cli.github.com/manual/gh_auth_status)
- [GitHub repository metadata](https://cli.github.com/manual/gh_repo_edit)
- [GitHub release creation](https://cli.github.com/manual/gh_release_create)
- [Microsoft Deploy to Azure button](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-to-azure-button)
