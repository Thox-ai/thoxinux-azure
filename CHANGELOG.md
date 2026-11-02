# Changelog

## Unreleased deployment integration

- Added a self-contained Azure portal template and Deploy to Azure button.
- Provisioned configuration covers Ubuntu 24.04, Dsv5, a managed disk, dedicated networking, explicit outbound connectivity and denied inbound access.
- Added a fail-fast Custom Script acceptance extension that waits for cloud-init and a running, accepted kernel.
- Pinned ThoxInux v0.9.1 release bytes and the canonical launcher, preserving upstream rights.
- Added an unprivileged continuous guest service, current-health CLI and bounded controller transport.
- Added CLI deployment with immutable plans, exact image selection, ownership checks and conservative what-if handling.
- Preserved real local QEMU evidence and added a portable two-boot verifier and GitHub Actions workflow.

This entry describes prepared source. It is not a GitHub Release record or proof of live Azure deployment.
