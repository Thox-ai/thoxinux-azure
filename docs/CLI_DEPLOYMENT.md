# CLI deployment with immutable plans

Your AI. Your Data. Your Rules.™

**This is the alternative CLI entry point.** For the deployment button, use the [quick start](QUICKSTART.md). Both paths require an authenticated Azure subscription; local tests are not live deployment evidence.

This package creates one Azure Ubuntu host and runs the real **ThoxInux v0.9.1**
kernel continuously inside QEMU TCG. It uses the existing THOX launcher and the
published EFI, with every release file pinned by SHA-256. Ubuntu supplies Azure's
Hyper-V drivers and VM agent. ThoxInux runs as its own kernel inside the guest.

## Configuration

| Item | Prepared value |
| --- | --- |
| Resource group | `rg-thoxinux-kernel` |
| VM | `thoxinux-kernel-01` |
| Region | `eastus`, configurable before planning |
| Host | Ubuntu 24.04 x64, Generation 2 |
| Host image | `Canonical:ubuntu-24_04-lts:server`, exact regional version resolved during prepare |
| Size | `Standard_D2s_v5`, 2 vCPUs, 8 GiB RAM |
| OS disk | 128 GiB, Standard SSD |
| Guest | Q35/OVMF, TCG, 1 vCPU, 2 GiB RAM, 48-bit IOMMU |
| Guest persistent volume | Sparse 64 GiB file on the host OS disk |
| Boot trust | Trusted Launch, Secure Boot and vTPM on the Ubuntu host; separate unsigned OVMF guest |
| Network | Explicit outbound via a Standard static public IPv4; all inbound traffic denied by NSG |
| Management | Azure Run Command; no public SSH/RDP/application port |
| Service | `thoxinux.service`, unprivileged locked `thoxinux` account |

The guest's 2 GiB ceiling follows the current EFI mapping constraint. Increasing
the Azure host's RAM does not justify increasing guest RAM beyond that ceiling.
`Standard_D4s_v5` is available through `--size` if a larger host is needed.

## Deploy from Azure Cloud Shell

1. Open [Azure Cloud Shell](https://shell.azure.com/) and select **Bash** in the
   intended account. Clone the published repository or upload and extract the source ZIP.
2. From the repository root, prepare the exact plan and deploy it:

```bash
python3 deploy.py prepare --out azure-plan
python3 deploy.py deploy --plan azure-plan
python3 deploy.py status --plan azure-plan
```

`prepare` selects an account automatically only when exactly one enabled
subscription is visible. When several are available, choose the intended ID:

```bash
az account list --query "[?state=='Enabled'].{Name:name,Id:id}" --output table
python3 deploy.py prepare --subscription SUBSCRIPTION_ID --out azure-plan
```

Replace `SUBSCRIPTION_ID` with that account's real subscription GUID. No private
key, password, token, or subscription credential belongs in this package or chat.

`prepare` resolves the regional image, verifies it is Gen2, generates a dedicated
local Ed25519 SSH key if none was supplied, and records a deterministic plan.
The private key stays in the authenticated execution environment. It is never
included in cloud-init, ARM parameters, or the package.

`deploy` creates only its dedicated resource group when absent, validates the
ARM template against Azure, obtains a machine-readable what-if, checks ownership
again, and creates the resources. It stops on quota, policy, authorization or
capacity errors. It refuses modified plans, foreign resources, existing VM
changes, and destructive or unexpected what-if operations. An existing resource
group must belong to this exact plan. Resume its original `azure-plan` directory
or prepare a different dedicated resource group; do not remove ownership guards.

The VM provisioning operation can finish before cloud-init and the guest boot
checks finish. Repeat `status` after bootstrap has had time to complete. A valid
result has `stage: guest_accepted`, `guest_accepted: true`, `running: true`, and
`service: active`. Exit code 2 means the guest has not been accepted. Azure VM
provisioning success by itself is not a ThoxInux boot result.

### Change the target before planning

```bash
python3 deploy.py prepare \
  --subscription SUBSCRIPTION_ID \
  --location eastus \
  --resource-group rg-thoxinux-kernel \
  --name thoxinux-kernel-01 \
  --size Standard_D2s_v5 \
  --out azure-plan
```

Supply `--ssh-public-key /path/to/key.pub` to use an existing public key. Local
`plan` mode is available for offline preparation with an explicit subscription,
already verified exact image version and public key. It does not contact Azure.

## Inspect and operate the real guest

In the Azure portal, open the created VM, choose **Run command**, then
**RunShellScript**. These commands run on the Ubuntu host:

```bash
/usr/local/bin/thox-kernel status
runuser -u thoxinux -- /usr/local/bin/thox-kernel command status
runuser -u thoxinux -- /usr/local/bin/thox-kernel command components
runuser -u thoxinux -- /usr/local/bin/thox-kernel command domain list
```

Controller commands go to the running kernel through its owner-only Unix
socket. They never become host shell commands. The command interface is
noninteractive. The package does not provide a desktop, graphical application
preview or attached interactive domain console.

Useful diagnostics and lifecycle commands:

```bash
cloud-init status --long
/usr/local/bin/thox-kernel logs --lines 30
journalctl -u thoxinux.service -n 30 --no-pager
systemctl restart thoxinux.service
```

The guest's controller acceptance receipt is
`/var/log/thoxinux/acceptance.json`. Its complete serial log is
`/var/log/thoxinux/serial.log`. Installation provenance is recorded separately in
`/opt/thoxinux/install-receipt.json`. `thox-kernel status` rejects stale receipts,
wrong EFI identities, missing checks and reused process IDs. Service stop clears
running acceptance and requests a clean guest shutdown.

Azure Run Command returns a limited output window. The status helper therefore
returns a concise JSON summary and leaves complete evidence on the VM. Azure's
boot diagnostics show the outer Ubuntu host; use the guest serial log for
ThoxInux evidence.

## Cost control

The VM, managed disk and Standard public IPv4 incur Azure charges after
deployment. This package does not create a NAT gateway, Bastion, GPU, load
balancer, additional subscription or recurring automation. Check current pricing, quota and capacity in the target subscription before deploying.

To stop compute billing while keeping the VM's disk:

```bash
python3 deploy.py deallocate --plan azure-plan
```

The disk and public IPv4 remain allocated and billable. No helper deletes
resources. To start it later, use the Azure portal's **Start** action; the
enabled `thoxinux.service` boots the guest and performs fresh acceptance checks.

## Verified release and evidence

Release: [ThoxInux v0.9.1 engineering preview](https://github.com/Thox-ai/thoxinux/releases/tag/v0.9.1)

```text
EFI SHA-256:
1bc1c7af33470edc009fa5756d3fe6063573660e07e157d19397afc680a7bb89

Canonical source revision:
fbad5952f20281ef6ad7eaefc1f54f7db4c0c6e9

Preserved upstream revision:
36503bda2d87465765e1b94e65f9d100f27ed5be
```

v0.9.1 changes build-control bytes without changing kernel code, so the actual
guest still prints `ThoxInux 0.9.0 | ThoxOS Core | THOX.ai LLC`. The exact EFI hash
identifies the v0.9.1 binary. The released build receipt retains some older
edition/version labels but binds the correct EFI hash; it is preserved verbatim.
The unrelated older checked-in EFI receipt is not substituted.

Fresh local tests used the released binary, unchanged official launcher,
packaged supervisor, Ubuntu QEMU 8.2.2, OVMF and an unprivileged process:

- Two actual kernel boots passed, including persistent-state recovery.
- Eight controller acceptance checks passed, four per boot.
- All 22 embedded-domain lifecycle checks passed, including start, stop,
  restart, intentional failure, recovery and persistent configuration.
- Both guests ended with `GINUX_KERNEL_OK` and QEMU exit code zero.
- Both test guests were stopped after evidence capture.

See `validation/` for complete results, serial logs and test receipts.
The repository refers to the canonical public release instead of duplicating its binary.
Azure bootstrap downloads that release from its official URLs and verifies all
files before starting the service. Use `tools/verify_kernel.py` to reproduce local
acceptance with the same byte-identity checks.

These results establish local QEMU kernel/controller boot, domain controller
operations and persistent configuration recovery. Domain status still reports
`Rootfs: pending`; Rocky Linux and systemd userspace readiness are not established.
The results do not establish native Azure Hyper-V kernel support, physical
device support, arbitrary Linux workload compatibility, a complete desktop, or
MeshStack/ThoxWork integration. Azure ARM validation, live provisioning and the
production service/CLI environment still require the signed-in target account.

## Recheck package code

```bash
python3 -B -m unittest discover -s tests -v
python3 build_cloud_init.py --check
python3 portal/build_portal.py --check
```

If payload code changes, run `python3 build_cloud_init.py` and
`python3 portal/build_portal.py`, then prepare a fresh plan. Never edit a plan in place. Unit tests use explicit mocked Azure responses
and fake-child transport fixtures; the separate `validation/local-qemu/` results
come from the real released EFI.

## Sources and attribution

- [Canonical ThoxInux source](https://github.com/Thox-ai/thoxinux)
- [Pinned official launcher](https://github.com/Thox-ai/thoxinux/blob/fbad5952f20281ef6ad7eaefc1f54f7db4c0c6e9/kernel/ginux/scripts/run-vm.sh)
- [Microsoft Dsv5 sizes](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dsv5-series)
- [Canonical Azure Ubuntu image reference](https://ubuntu.com/azure/docs/azure-how-to/instances/find-ubuntu-images/)
- [Azure explicit outbound connectivity](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access)
- [Azure Linux Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command)
- [Azure CLI deployment what-if](https://learn.microsoft.com/en-us/cli/azure/deployment/group?view=azure-cli-latest#az-deployment-group-what-if)
- [cloud-init file payload encoding](https://docs.cloud-init.io/en/latest/reference/modules.html#write-files)

Apache-2.0 license and upstream attribution are preserved in `LICENSE`,
`UPSTREAM-NOTICE.md`, `payload/GINUX_LICENSE` and `payload/GINUX_NOTICE`.
New deployment integration: THOX.ai LLC, 2026. Sadie coordinated the work with
separate kernel, Azure deployment and independent Azure review collaborators.
