# Validation and known limits

**The released kernel has passed real local QEMU boot and controller tests. Azure deployment and remote GitHub Actions execution have not been established by this evidence.**

## Current portable verification

The repository's [portable verifier](../tools/verify_kernel.py) was executed with QEMU 8.2.2, OVMF and ordinary user permissions. It used the unchanged packaged launcher and supervisor, verified the official release files, and created fresh temporary guest state. The second boot reused only that test state to check recovery.

The [portable summary](../validation/portable-qemu/summary.json) records:

| Check | Actual result |
| --- | --- |
| Released EFI boots | 2 passed |
| Controller acceptance | 8 checks passed, 4 per boot |
| Embedded-domain lifecycle requests | 22 passed |
| Clean kernel shutdowns | 2 passed, with `GINUX_KERNEL_OK` and QEMU exit code 0 |
| Persistent state | Storage and local configuration recovered on the second boot |
| Execution identity | Unprivileged UID 65534 |
| Final guest state | Stopped |
| Azure deployed | `false` |
| Production CLI path tested | `false` |
| systemd/cloud-init installation tested | `false` |

The lifecycle requests exercise creation, configuration, start, process listing, stop, restart, intentional contained failure, restart after failure, and recovery of the saved configuration. The [cold results](../validation/portable-qemu/cold-results.json), [recovery results](../validation/portable-qemu/recovered-results.json), [serial log](../validation/portable-qemu/serial.log), and accompanying running/stopped receipts preserve the actual responses. Receipt offsets, byte counts and hashes identify each serial session.

These are kernel/controller and embedded-domain results. Domain status reports **`Rootfs: pending`**. The tests do not establish ready Rocky Linux or systemd userspace, arbitrary Linux application compatibility, graphical desktop readiness, native Hyper-V boot, physical device operation, or MeshStack/ThoxWork integration.

## Preserved earlier evidence

[validation/local-qemu](../validation/local-qemu/) contains an earlier real two-boot run. Its serial log retains an initial emulator launch failure caused by missing firmware ROM files, followed by successful boots after the local emulator runtime was completed. The successful sessions are distinguished by their receipt offsets and hashes. The failed attempt is not counted as a successful boot.

The earlier and portable suites are separate runs with the same kernel. The current acceptance claim uses the portable suite's **2 boots, 8 controller checks, 22 lifecycle requests and 2 clean shutdowns**; it does not multiply these counts by combining historical runs.

## Exact release identity

| Identifier | Preserved value |
| --- | --- |
| Official release tag | `v0.9.1` |
| EFI SHA-256 | `1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89` |
| EFI bytes | `12507648` |
| Canonical source revision | `fbad5952f20281ef6ad7eaefc1f54f7db4c0c6e9` |
| Upstream revision | `36503bda2d87465765e1b94e65f9d100f27ed5be` |
| Observed kernel banner | `ThoxInux 0.9.0 \| ThoxOS Core \| THOX.ai LLC` |
| Released build-receipt version | `0.2.0-azure-candidate` |

The release tag, embedded runtime banner and older build-receipt label are different identifiers. They are preserved as observed. The exact EFI digest binds the tested bytes to the [official v0.9.1 release](https://github.com/Thox-ai/thoxinux/releases/tag/v0.9.1); the package does not rewrite the banner or invent a newer build receipt. See [pins.json](../payload/pins.json) and [UPSTREAM-NOTICE.md](../UPSTREAM-NOTICE.md).

## What the offline tests establish

Unit tests exercise release-integrity rejection, modified-plan detection, resource ownership, deployment-status parsing, serial transport, stale receipt handling and portal payload consistency. Portal gate tests execute the actual Bash sequence against labeled command doubles to confirm that failure stops later commands. These checks do not execute Azure APIs or establish a successful Azure deployment.

The prepared [GitHub Actions workflow](../.github/workflows/verify.yml) runs static tests and the portable kernel verifier. Its presence is not evidence that GitHub Actions has run successfully. A remote CI claim requires an actual workflow run and its artifacts.

## Azure acceptance still required

An authenticated deployment must establish template validation, regional quota and capacity, resource creation, cloud-init installation, production service startup and the current guest status. The portal template includes a Custom Script gate for these initialization checks. Its implementation has been reviewed and locally tested with command doubles; the Azure extension itself has not yet been executed as part of this evidence.

On the deployed VM, require the current helper's `accepted: true` and `running: true`, the expected EFI digest, all four controller checks, and an active service. Preserve the Azure deployment result, exact host image version and guest receipt. Provisioning status alone is insufficient because Azure can report the VM ready before cloud-init finishes. [Microsoft custom-data documentation](https://learn.microsoft.com/en-us/azure/virtual-machines/custom-data).
