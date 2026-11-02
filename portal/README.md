# Deploy through the Azure portal

This template provisions the Azure host and installs the pinned ThoxInux kernel automatically. It embeds the same reviewed cloud-init used by the command-line deployment path. You do not need to generate or paste cloud-init, a plan digest, or an image version.

**Select Create new for the resource group.** Use a new, dedicated group for this deployment. The standard Azure template form can update resources with matching names in an existing group and does not run `deploy.py`'s ownership checks. The default VM name includes a stable resource-group suffix to reduce accidental collisions; this is not an ownership check.

## Portal fields

| Field | Selection |
| --- | --- |
| Subscription | Your intended Azure subscription |
| Resource group | **Create new**, for example `rg-thoxinux-kernel-demo` |
| Region | A region with Dsv5 capacity and sufficient quota |
| VM name | Keep the generated name or enter a new lowercase name |
| Location | Defaults to the resource group's region |
| VM size | `Standard_D2s_v5` for one kernel guest; D4s_v5 is optional |
| SSH public key | One existing `ssh-ed25519` or `ssh-rsa` public key, never the private key |

The fixed administrator name is `thoxadmin`. No inbound SSH port is opened. Manage the host through its Azure portal **Run command** page using **RunShellScript**.

The host image is Canonical Ubuntu 24.04 AMD64 Gen2. This portal route selects the latest regional image available when you deploy and records the resulting `imageReference.exactVersion` in the `deployedUbuntuImageVersion` deployment output. The ThoxInux kernel release and EFI SHA-256 remain fixed in the embedded payload. The CLI route remains available when an exact host image must be selected before deployment.

The template also installs a `thoxinux-guest-acceptance` Custom Script extension. It waits up to 15 minutes for cloud-init, requires an active `thoxinux.service`, and runs the authoritative guest status check. A failed initialization or guest check makes the extension and deployment fail. The gate executes only installed commands and downloads no extra scripts. Successful extension completion is evidence for that execution time; use the status helper whenever you need current health.

## Confirm the guest is running

Wait for Azure deployment completion, then open the VM's **Run command → RunShellScript** and run:

```bash
sudo /usr/local/bin/thox-kernel status
```

Require `accepted: true` and `running: true`. If the helper is not installed yet or reports an unaccepted state, inspect initialization and the guest log:

```bash
cloud-init status --long
sudo journalctl -u thoxinux.service -n 50 --no-pager
sudo /usr/local/bin/thox-kernel logs --lines 30
```

The VM's provisioning state and boot diagnostics describe the Ubuntu host. Guest acceptance comes from the actual ThoxInux QEMU serial session and controller checks, which the acceptance extension checks before the template completes. This deployment does not establish native Hyper-V boot, graphical desktop readiness, or platform application acceptance.

The full receipt remains at `/var/log/thoxinux/acceptance.json`, and the serial log remains at `/var/log/thoxinux/serial.log`. Azure Run Command has a bounded output window, so use the concise helper output for status.

## Deployment button construction

The repository's main README links to this template using Microsoft's documented convention:

```text
https://portal.azure.com/#create/Microsoft.Template/uri/<URL-encoded public raw template URL>
```

The raw URL must point to this repository's public `portal/azuredeploy.json`. A private GitHub URL does not provide Azure anonymous access. A valid link and local template tests do not establish that Azure has accepted or deployed the template.

## Rebuild after a payload change

From the repository root:

```bash
python3 build_cloud_init.py
python3 portal/build_portal.py
python3 portal/build_portal.py --check
python3 -m unittest discover -s tests -p test_portal.py -v
```

`portal/azuredeploy.json` is generated source. Change the root deployment profile or payload, then regenerate it. The portal tests require the embedded cloud-init to match `cloud-init.yaml` byte for byte.

## Costs and lifecycle

The VM, 128 GiB managed OS disk and Standard static public IPv4 incur Azure charges. The public IPv4 is used for outbound package and release downloads; the NSG denies all inbound traffic. No NAT Gateway, Bastion, load balancer or GPU is provisioned.

Stopping the VM from the Azure portal deallocates its compute when the final power state is **Stopped (deallocated)**. The disk and static public IPv4 remain allocated and billable. Portal deployments are identified by `thox-entrypoint=azure-portal`; do not invent a CLI plan to manage them. Use the Azure portal or explicit resource IDs in your own authenticated Azure commands.

## Sources

- [Microsoft: Deploy to Azure button](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-to-azure-button)
- [Microsoft: ARM deployment modes and same-name resource updates](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deployment-modes)
- [Microsoft: VM image reference and exactVersion](https://learn.microsoft.com/en-us/rest/api/compute/virtualmachines/get)
- [Microsoft: Linux Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command)
- [Microsoft: Custom Script extension for Linux](https://learn.microsoft.com/en-us/azure/virtual-machines/extensions/custom-script-linux)

Your AI. Your Data. Your Rules.™
