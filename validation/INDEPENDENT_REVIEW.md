# Independent integration and evidence review

The Azure reviewer independently checked the deployment architecture, local
runtime integration and actual QEMU receipts before packaging. This was an AI
collaborator review, not a human acceptance or cloud-provider certification.

The review confirmed that the package keeps the Ubuntu Azure host and ThoxInux
guest acceptance separate, preserves the released EFI identity, runs the guest
as an unprivileged process, and uses explicit outbound networking with inbound
access denied.

Material findings corrected before packaging included stale receipt acceptance,
real controller help-response parsing, bounded bootstrap timing, PID reuse,
Azure-created Run Command extension ownership, and machine-readable what-if
output. The bootstrap now uses the same strict status helper as Azure status
and permits 420 seconds for the bounded first boot and command checks.

The final review independently matched the EFI, launcher, supervisor and serial
log hashes. It verified all four running/stopped receipt serial slices against
the log, including offsets, lengths and SHA-256 values. Counts reconcile to 17
cold-start plus five recovery lifecycle commands, eight controller checks, two
boots and two clean zero-exit shutdowns. Both test guests were stopped afterward.

The initial 54-test integration suite passed with no failures or skips, including
the local Unix-socket check. The current full suite result is recorded separately
in unit-tests.json. These test results do not constitute Azure provisioning.

Three observed version labels are deliberately preserved: the published release
tag is v0.9.1, the unchanged kernel banner says ThoxInux 0.9.0, and the released
build receipt retains a 0.2.0-azure-candidate metadata label. All are bound to the
published v0.9.1 EFI SHA-256 in SOURCE.json and the kernel test summary.

The local test first encountered an incomplete temporary QEMU firmware search
path before the kernel could start. The temporary test runtime's firmware path
was repaired without changing the official launcher or kernel. The complete
serial log retains that failed staging attempt before the successful sessions;
the receipt offsets identify the exact successful-session evidence.

Remaining acceptance stages require the authenticated Azure account: ARM
validation, what-if, VM creation, actual cloud-init/systemd installation and the
production-path CLI status check. Domain controller tests do not establish a
ready Rocky Linux root filesystem, systemd userspace or a complete desktop.

## Portal and portable-verifier integration

The self-contained portal template was independently reviewed after adding the
Custom Script acceptance gate. Its exact embedded cloud-init matches the
reviewed generated payload. Eleven portal tests cover the template contract
and execute the actual Bash gate with explicitly labeled command doubles to
verify failure propagation. This is local control-flow evidence, not an Azure
extension execution.

The new portable verifier also completed a fresh actual two-boot kernel run.
Evidence is preserved in portable-qemu/. It passed eight controller checks,
22 lifecycle commands and two clean shutdowns without source changes. Readback
verified all four running/stopped receipt serial slices and confirmed all test
supervisor/QEMU processes had stopped. The new run does not include the initial
firmware-staging failure retained in the historical local-qemu/ log.
