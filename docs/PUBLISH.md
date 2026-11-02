# Publish the prepared GitHub repository

The intended public repository is **`Thox-ai/thoxinux-azure`**. The Deploy to Azure button becomes usable after its raw template is publicly available at that repository's `main/portal/azuredeploy.json` path.

This helper publishes GitHub source only. It creates no Azure resources, does not invoke Azure APIs, and creates no Azure charges. Azure deployment remains a separate action through the portal or CLI.

## Open the prepared Git bundle

The delivery archive contains `thoxinux-azure.bundle` and a separate source ZIP. Use the bundle to preserve the prepared commit and authorship. Extract the delivery archive, then run these commands from the directory containing the bundle:

```bash
git clone --branch main thoxinux-azure.bundle thoxinux-azure
cd thoxinux-azure
git remote remove origin
```

The clone's initial `origin` points to the local bundle file. The last command removes only that freshly created local-bundle reference so the publication helper can add the new GitHub target. It does not delete any files or change any remote repository.

If you already have the prepared clean Git repository, use it directly. The separate source ZIP is for inspection and copying; it intentionally contains no `.git` directory. Extracting that ZIP alone does not meet the helper's committed-repository prerequisite.

## Before publication

Use a machine with Git, Python 3 and a current GitHub CLI. Sign in to `gh` outside the helper using the intended account, with permission to create a public repository in the `Thox-ai` organization. The helper never signs in, refreshes a credential, prints a token, or changes global Git authentication settings.

The prepared source must be a normal local Git repository with a committed, clean `main` branch. The helper checks the actual Git root, tracked README/template, working tree, remotes and README button target before it can create anything. A detached branch, uncommitted or untracked file, mismatched remote, unverified repository lookup, or already-existing GitHub repository stops publication.

The current GitHub CLI must support `gh auth status --no-refresh`; this ensures the dry run does not refresh stored credentials. If the command is unavailable, update GitHub CLI outside this helper.

If this machine is not signed in yet, authenticate once before running the helper:

```bash
gh auth login --hostname github.com
```

Complete GitHub's own sign-in flow. Do not put tokens in a command, source file or chat. If an account is already authenticated, keep that session and proceed.

## Review the concrete actions

From the repository root:

```bash
python3 tools/publish_github.py --repository Thox-ai/thoxinux-azure --dry-run
```

Dry run performs read-only local and GitHub checks. It prints the authenticated account, committed source ID, exact public repository, deployment button, and argument arrays for the proposed actions. It creates no repository, adds no remote, pushes no commit, and writes no receipt.

## Publish

```bash
python3 tools/publish_github.py --repository Thox-ai/thoxinux-azure
```

The helper rechecks the local commit and remote absence, then:

1. Creates a **new public** GitHub repository with no starter commit.
2. Verifies its identity, public visibility and empty branch list.
3. Adds `origin` locally only when absent.
4. Pushes the committed local `main` without force.
5. Verifies GitHub's `main` commit matches the local source.
6. Downloads the public raw portal template **without authentication** and checks its SHA-256 against the committed file.

The final JSON result distinguishes verified GitHub publication from live Azure deployment. A successful publication makes the template URL available to Azure; it does not prove that a subscription has accepted or run the deployment.

## If publication stops partway through

The helper never deletes a repository, changes an existing repository's visibility, force-pushes, or retries a write. It refuses to reuse an existing repository, even if an earlier run created it successfully but failed to push or verify.

If it reports that a repository was created, inspect that exact repository and the local `origin` before taking further action. The error identifies the completed creation and unverified later stage. Preserve its state for a deliberate follow-up; do not change the helper's absence checks to make a retry proceed.

Only use another `--repository OWNER/NAME` after intentionally updating and committing the README Deploy to Azure button to point to that exact repository. This package is prepared for `Thox-ai/thoxinux-azure`; it will not silently publish under a personal account or another namespace.

## References

- [GitHub CLI authentication status](https://cli.github.com/manual/gh_auth_status)
- [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create)
- [GitHub: Add locally hosted code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
- [Microsoft: Deploy to Azure button](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-to-azure-button)
