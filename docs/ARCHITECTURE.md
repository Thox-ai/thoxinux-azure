# Architecture

This profile runs the ThoxInux Rust kernel as a QEMU TCG guest inside an Azure Ubuntu VM. Ubuntu supplies Azure-compatible storage, networking and management. The guest executes its own EFI kernel and controller. The [local evidence](VALIDATION.md) establishes that guest boot path.

## Execution layers

| Layer | Responsibility |
| --- | --- |
| Azure control plane | VM, managed disk, virtual network, public IPv4 and extension lifecycle |
| Ubuntu 24.04 AMD64 Gen2 host | Azure VM agent, package installation, systemd and QEMU |
| `thoxinux.service` | Starts the supervisor as the locked, unprivileged `thoxinux` account |
| `guest_service.py` | Verifies the EFI, observes serial output, executes acceptance checks and serves bounded controller requests |
| QEMU Q35 with OVMF | Emulates the hardware exposed to the actual ThoxInux EFI |
| ThoxInux | Kernel, controller and embedded-domain operations |

TCG emulates the guest CPU and does not require nested hardware virtualization. The tested profile uses one guest vCPU, 2 GiB RAM, a 48-bit IOMMU and the preserved official launcher. The 2 GiB guest ceiling remains in place because the reviewed EFI boot path has a low identity-map constraint. A larger Azure host does not remove that constraint. [Pinned launcher](https://github.com/Thox-ai/thoxinux/blob/fbad5952f20281ef6ad7eaefc1f54f7db4c0c6e9/kernel/ginux/scripts/run-vm.sh).

The Ubuntu host retains Trusted Launch, Secure Boot and vTPM. Separate OVMF firmware boots the unsigned research EFI. This is not native ThoxInux boot on Azure Hyper-V. A native port would need compatible virtual storage, bus and networking support; converting a disk to VHD alone is insufficient. [Microsoft image requirements](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/create-upload-generic).

## Provisioned resources

| Resource | Configuration and purpose |
| --- | --- |
| Resource group | Dedicated deployment scope; new group required for the portal route |
| VM | `Standard_D2s_v5` by default; `Standard_D4s_v5` optional |
| Managed OS disk | 128 GiB `StandardSSD_LRS`; stores Ubuntu, release files, logs and guest volume |
| VNet and subnet | Dedicated address space; subnet sets `defaultOutboundAccess: false` |
| Network security group | Explicit deny rule for all inbound traffic |
| Network interface | Accelerated networking and the dedicated subnet |
| Standard static public IPv4 | Explicit outbound connectivity for packages, releases and Azure management |
| Managed boot diagnostics | Ubuntu host diagnostics |
| Portal acceptance extension | Custom Script 2.1 checks completed initialization and live guest acceptance |

There is no NAT Gateway, Bastion, load balancer or GPU in the template. Azure supports an attached public IPv4 as an explicit outbound method even when default subnet outbound access is disabled. Inbound connectivity remains controlled by the NSG. [Microsoft outbound networking guidance](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access).

## Two deployment entry points

| Concern | Portal template | CLI helper |
| --- | --- | --- |
| Source | `portal/azuredeploy.json` | `azuredeploy.json` plus `deploy.py` |
| Host image | Resolves Ubuntu 24.04 `latest` during deployment; outputs the exact version | Resolves and pins the regional image before plan creation |
| User inputs | Four template inputs plus Azure's subscription/resource-group controls | Account and target options stored in a hashed plan |
| Ownership | `thox-entrypoint=azure-portal`; new group required | Exact `thox-plan` tags and resource inventory checks |
| Change preview | Azure portal validation | ARM validation and inspected what-if before creation |
| Initial acceptance | Custom Script extension gates template completion | Separate `deploy.py status` after provisioning |
| Later management | Portal or explicit Azure resource IDs | Original plan for status and deallocation |

The portal template embeds the same cloud-init payload as the CLI path. It does not inherit the CLI's ownership checks. Azure incremental deployments can update matching existing resources, which is why a new dedicated portal group is required. [ARM deployment behavior](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deployment-modes).

## Release verification and state

`payload/pins.json` identifies the reviewed release and every downloaded asset by size and SHA-256. Bootstrap verifies the release manifest and bundled attribution before enabling the service. The supervisor verifies the expected EFI independently before launch.

The guest stores state in `/var/lib/thoxinux/volume.raw`, a sparse file with a 64 GiB logical size. It occupies space on the Azure managed OS disk as data is written. The launcher preserves that volume and refreshes the guest's firmware-variable file on each boot. This configuration stores lab data in Azure; it is not an on-device deployment.

The supervisor requires the kernel banner and four controller responses before recording acceptance. Its receipt binds serial-session byte ranges to hashes. The status helper additionally checks the exact release identity, process names and start times, the control socket and heartbeat age. Service shutdown clears current acceptance.

The portal gate waits for cloud-init, checks the active service, and invokes that status helper. Cloud-init alone may report VM provisioning before its custom-data work finishes, so the extra gate connects initial deployment success to a completed guest check. [Azure custom-data behavior](https://learn.microsoft.com/en-us/azure/virtual-machines/custom-data).
