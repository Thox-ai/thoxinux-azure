# Quick start

Deploy one Azure VM that hosts the real ThoxInux kernel in a managed QEMU TCG process. This is a kernel research environment with a controller console. The [validation report](VALIDATION.md) records the actual local boot tests and the remaining Azure validation boundary.

## 1. Choose the Azure account and public key

Use the intended Azure subscription and a **new dedicated resource group**. The account needs permission to deploy the VM, disk, network resources and VM extension. Review the selected region's quota and pricing before creating the resources.

Have one OpenSSH **public** key ready. Azure supports Ed25519 and RSA keys of at least 2,048 bits. Copy the single line from the `.pub` file. Keep the matching private key on your computer or in your own credential storage. The [user guide](USER_GUIDE.md#obtain-an-ssh-public-key) includes a command that creates a dedicated key without overwriting an existing file. [Microsoft's key guide](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/create-ssh-keys-detailed) explains the supported formats.

## 2. Open Deploy to Azure

Use the **Deploy to Azure** button in the [repository README](../README.md). The button requires the published public template URL; a local package or an unpublished repository does not make that URL available. Publication instructions are in [PUBLISH.md](PUBLISH.md).

In Azure's deployment form, select the subscription and choose **Create new** for the resource group. The template adds four inputs:

| Input | What to enter |
| --- | --- |
| VM name | Keep the generated name, or use a new lowercase name with letters, digits and hyphens |
| Location | Keep the resource group's region, provided the VM size is available there |
| VM size | `Standard_D2s_v5` for one guest; `Standard_D4s_v5` provides a larger host |
| SSH public key | Paste the complete `ssh-ed25519` or `ssh-rsa` public-key line |

The default host has **2 vCPUs, 8 GiB RAM and a 128 GiB Standard SSD**. The ThoxInux guest uses **1 vCPU and 2 GiB RAM**. A static public IPv4 provides outbound access; the network security group denies all inbound traffic. SSH is not opened. The administrator name is `thoxadmin`.

Select **Review + create**, review the deployment, then select **Create**. Azure's documented [deployment button](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-to-azure-button) opens this review flow.

## 3. Let installation and the guest check finish

The embedded cloud-init installs the emulator, verifies the pinned release files, and enables `thoxinux.service`. The portal template's `thoxinux-guest-acceptance` extension waits up to 15 minutes for cloud-init, then requires the active service and a successful guest status check. Failure leaves an error in the deployment instead of reporting kernel acceptance.

The gate records readiness at its execution time. Check current health after opening the VM.

## 4. Check and use the kernel

Open the VM in Azure, select **Run command**, then **RunShellScript**. Run:

```bash
/usr/local/bin/thox-kernel status
```

Require `accepted: true`, `running: true`, `checks_passed: 4`, and `checks_total: 4`. This helper verifies the expected EFI identity, controller checks and current process information. A green Ubuntu VM provisioning state alone is insufficient.

To query the actual guest controller, run:

```bash
runuser -u thoxinux -- /usr/local/bin/thox-kernel command status
runuser -u thoxinux -- /usr/local/bin/thox-kernel command components
runuser -u thoxinux -- /usr/local/bin/thox-kernel command domain list
```

If acceptance fails, use the [diagnostics and recovery instructions](USER_GUIDE.md#diagnose-a-failed-or-incomplete-start). Azure Run Command works through the VM agent without opening an inbound management port. [Microsoft Run Command documentation](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command).

## Pause when finished

Use the Azure VM's **Stop** action and confirm **Stopped (deallocated)** to release its compute allocation. The disk and static public IPv4 remain billable. Stopping only `thoxinux.service` leaves the Azure host running. See [lifecycle and costs](USER_GUIDE.md#lifecycle-and-costs).

## CLI alternative

The repository also provides an authenticated Azure CLI route that pins the host image before deployment and checks resource ownership. From the repository root in Azure Cloud Shell or another authenticated Azure CLI environment:

```bash
python3 deploy.py prepare --out azure-plan
python3 deploy.py deploy --plan azure-plan
python3 deploy.py status --plan azure-plan
```

If multiple enabled subscriptions are visible, `prepare` requires an explicit `--subscription` ID. Keep the original plan directory for later status and deallocation. See [CLI deployment](CLI_DEPLOYMENT.md) for account selection and options. A portal-created VM is managed through the portal; it is not adopted by inventing a CLI plan.
