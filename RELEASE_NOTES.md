# ThoxInux Azure v0.1.0-rc.1

This is an **engineering preview** of the Azure deployment package for the
released ThoxInux v0.9.1 kernel. It provisions an Ubuntu 24.04 Generation 2 Azure
host and runs the pinned kernel inside QEMU using software emulation.

## Changes

- A self-contained **Deploy to Azure** template, with a bounded guest acceptance
  extension that fails deployment when the service or kernel acceptance fails.
- A CLI deployment path with an explicit subscription, an exact Ubuntu image
  version, ownership checks, validation, what-if, and live guest status.
- A host with Trusted Launch, Secure Boot and vTPM; no public inbound ports;
  management through Azure Run Command.
- A pinned EFI, supervised kernel lifecycle, serial evidence, controller checks,
  a readable preview, and operations and development documentation.

## Validation

Publication requires the complete unit suite, generated cloud-init and portal
template checks, deterministic source packaging, and a **fresh real QEMU run in
GitHub Actions**. The kernel run covers two boots, eight controller acceptance
checks, twenty-two embedded-domain lifecycle commands, persistent configuration
recovery, and two clean kernel shutdowns. The guest is stopped when the test ends.

The release assets include the source ZIP and its file manifest, the complete CI
kernel evidence ZIP, the CI kernel summary, a release manifest tying those bytes
to the exact integration commit and workflow run, and SHA-256 checksums. The
source ZIP also retains separately labelled historical local evidence. Use the
**CI** evidence assets for the verification run that issued this release.

## Limits

This release does **not** establish a successful Azure deployment, native
Hyper-V boot, production readiness, ready Rocky Linux or systemd userspace inside
the kernel, a graphical desktop, or MeshStack acceptance. The CI run exercises
the real kernel and its controller under local QEMU TCG; it does not exercise
cloud-init or the deployed systemd service in Azure. Azure deployment remains an
operator action requiring a subscription, suitable capacity, and chargeable
resources. Use a new, empty resource group for the portal path.

The upstream v0.9.1 release reports the `ThoxInux 0.9.0` kernel banner. This
package preserves and verifies the exact released binary and its documented
identity; it does not relabel the runtime or rebuild the kernel.

## Upgrade

This is the initial integration release. Deploy it into a new, empty resource
group and retain any existing guest storage and evidence until the new instance
has independently passed its acceptance checks. It does not migrate or upgrade
an existing VM in place. Existing CLI plans and issued release tags remain
immutable; prepare a new plan for a new deployment. No automatic resource or
data deletion is performed.

Repository: <https://github.com/Thox-ai/thoxinux-azure>

Copyright © 2026 THOX.ai LLC. See `LICENSE`, `NOTICE`, and `UPSTREAM-NOTICE.md`
for the integration license and retained upstream rights.
