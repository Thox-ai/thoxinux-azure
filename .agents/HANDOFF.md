# Handoff — ThoxInux on Azure

## Current state (verified 2026-11-02)

- Public repository: https://github.com/Thox-ai/thoxinux-azure
- Prepared source commit `adb92f2327cba68fce5cafb9c959cae8910d984b`
  was published to `main` on 2026-11-02 via an authorized GitHub CLI
  account after the connected GitHub App route returned
  `403 Resource not accessible by integration`. The App installation for
  the Thox-ai organization remains an open integration item; it is not a
  blocker for the CLI publication route.
- Repository description and topics are set and read back.
- The anonymous raw portal template
  (`main/portal/azuredeploy.json`, SHA-256
  `df303e62c46290f6a38b0d4ff2b3752db4a085175c1edc94110e269f5f1b7af5`)
  is live and hash-matches the committed file, so the README
  Deploy to Azure button resolves.
- CI: workflow run 41404874219 passed verification on the exact commit
  (113 unit tests, generated-file checks, and a fresh two-boot kernel
  verification in GitHub Actions). The release job's first attempt
  stopped after creating the draft, before asset upload; rerunning the
  failed job resumed the same draft and completed publication, exactly
  as `docs/PUBLISH.md` prescribes.
- Release: v0.1.0-rc.1 engineering prerelease, published
  2026-11-02T07:15:53Z, tag pointing at `adb92f2`, seven assets
  uploaded. The CI kernel summary records result `passed`, eight
  controller checks, 22 lifecycle commands, two clean shutdowns, guest
  stopped, and `azure_deployed: false`.
  https://github.com/Thox-ai/thoxinux-azure/releases/tag/v0.1.0-rc.1
- Upstream kernel pin: ThoxInux v0.9.1, EFI SHA-256
  `1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89`.

## What is proven

- 113 local unit tests pass (`python3 -B -m unittest discover -s tests`),
  re-run independently on the prepared tree before publication.
- The pinned real kernel passed two QEMU boots, eight controller checks,
  22 lifecycle commands, persistence recovery, and two clean shutdowns
  both in the recorded local run under `validation/` and in the fresh
  GitHub Actions run attached to the release.

## What is not proven yet

- No Azure VM has been provisioned from this template. Live Azure ARM
  validation, cloud-init/systemd execution, acceptance-extension
  completion, and current guest status still require the intended Azure
  account. Do not claim Azure acceptance from local or CI QEMU results.
- A complete guest Linux userspace, graphical desktop, native Hyper-V
  boot, and MeshStack readiness are outside the demonstrated scope.
- Standards audit (THOX-REPO-001) still records, honestly: no raster
  product capture (headless serial evidence and labelled renderings are
  the proportionate preview for this product), no independent legal or
  human/content acceptance supplied.

## Next steps, in order

1. Run the first real Azure deployment in a dedicated resource group,
   then record the deployment result, exact host image version,
   cloud-init and service logs, and the guest status output
   (`accepted: true`, `running: true`, `checks_passed: 4`).
2. If the GitHub App route is still wanted for future automation,
   install/configure the app for the Thox-ai organization with
   thoxinux-azure selected; CLI publication does not depend on it.
3. Founder report and SharePoint archive closeout for this release
   follow the THOX repository standard process.

## Recovery

- A failed release job is rerun for the same verified commit and
  artifact; do not move the tag or overwrite assets.
- If the remote gains unexpected branches or commits, inspect and
  integrate that work; never force-push this snapshot over it.
