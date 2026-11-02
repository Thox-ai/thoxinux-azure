# Changelog

## v0.1.0-rc.1 candidate

Formal GitHub publication is gated by the verification workflow. Until a non-draft prerelease is read back, this remains a prepared candidate.

- Added a self-contained Azure portal template and Deploy to Azure button.
- Provisioned configuration covers Ubuntu 24.04, Dsv5, a managed disk, dedicated networking, explicit outbound connectivity and denied inbound access.
- Added a fail-fast Custom Script acceptance extension that waits for cloud-init and a running, accepted kernel.
- Pinned ThoxInux v0.9.1 release bytes and the canonical launcher, preserving upstream rights.
- Added an unprivileged continuous guest service, current-health CLI and bounded controller transport.
- Added CLI deployment with immutable plans, exact image selection, ownership checks and conservative what-if handling.
- Preserved real local QEMU evidence and added a portable two-boot verifier and GitHub Actions workflow.

- Added provider-backed build, last-commit and integration-release badges, with separate kernel, license, host and THOX identity badges.
- Added authentic headless HTML/PDF previews, complete release notes and a public requirements/evidence map.
- Added a separate verified-source prerelease job with immutable tags and assets, exact verification-artifact binding and release checksums.

This entry describes the intended release contents. It is not a GitHub Release record or proof of live Azure deployment.
