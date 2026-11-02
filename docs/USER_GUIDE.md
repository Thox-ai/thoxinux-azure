# User guide

The Azure host runs Ubuntu 24.04. The ThoxInux kernel runs continuously inside QEMU, supervised by `thoxinux.service`. Use the Azure portal to manage the VM and its **Run command → RunShellScript** page to inspect the kernel. Start with the [quick start](QUICKSTART.md).

## Obtain an SSH public key

The portal template requires a public key to provision the `thoxadmin` account even though it exposes no inbound SSH port. An existing OpenSSH `.pub` file is suitable if it uses Ed25519 or RSA with at least 2,048 bits. Display only that public file, for example:

```bash
cat "$HOME/.ssh/id_ed25519.pub"
```

If you need a new key, the following Bash block works in Azure Cloud Shell, Linux or macOS. It stops if either target path already exists, asks for the key's passphrase interactively, and prints only the public key:

```bash
(
  umask 077
  thox_key_path="$HOME/.ssh/thoxinux-azure"
  mkdir -p "$HOME/.ssh"
  if [ -e "$thox_key_path" ] || [ -L "$thox_key_path" ] ||
     [ -e "$thox_key_path.pub" ] || [ -L "$thox_key_path.pub" ]; then
    printf '%s\n' 'Key path already exists. Use its public key or choose a different filename.' >&2
    exit 1
  fi
  ssh-keygen -t ed25519 -f "$thox_key_path" -C thoxinux-azure &&
    cat "$thox_key_path.pub"
)
```

Paste the one-line public key into Azure. Retain the private file and its passphrase in your own storage; they do not belong in the repository, template or deployment outputs. [Microsoft's SSH key documentation](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/create-ssh-keys-detailed).

## Read current health

Run this on the VM through **RunShellScript**:

```bash
/usr/local/bin/thox-kernel status
```

| Field | Meaning |
| --- | --- |
| `accepted` | The expected kernel identity and all four controller checks passed, and current liveness checks still hold |
| `running` | The recorded supervisor and QEMU processes match their identities and start times, the control socket exists, and the receipt heartbeat is fresh |
| `state` | The supervisor's recorded phase, such as `booting`, `running`, `failed` or `stopped` |
| `checks_passed` / `checks_total` | Expect `4 / 4` for accepted operation |
| `efi_sha256` | Identity of the exact released kernel binary |
| `updated_at` / `heartbeat_age_seconds` | Receipt time and age; the helper rejects stale evidence |
| `error` | The recorded failure, when one is available |

Both `accepted` and `running` must be `true`. A recorded `state: running` can remain in an old file after a crash, so that field alone is insufficient. The helper exits nonzero when acceptance is unavailable.

For a CLI deployment, `python3 deploy.py status --plan azure-plan` returns a wrapper with `stage: guest_accepted`, `guest_accepted: true`, and the helper fields under `guest`. It also requires `guest.service: active`. Exit code `2` means a status result was obtained but the guest was not accepted; exit code `1` indicates a helper or deployment-operation error.

## Query the controller

Azure RunShellScript normally executes as root. Run guest commands as the locked `thoxinux` service identity, which owns the private control socket:

```bash
runuser -u thoxinux -- /usr/local/bin/thox-kernel command help
runuser -u thoxinux -- /usr/local/bin/thox-kernel command status
runuser -u thoxinux -- /usr/local/bin/thox-kernel command components
runuser -u thoxinux -- /usr/local/bin/thox-kernel command domain list
runuser -u thoxinux -- /usr/local/bin/thox-kernel command ps
```

These are real kernel-controller requests. They are not passed to a host shell. The command interface accepts one bounded, noninteractive request at a time. Attached domain consoles and interactive `domain exec` sessions are excluded from this interface.

The controller can demonstrate embedded-domain lifecycle operations. The recorded tests cover those operations, but domain status still reports `Rootfs: pending`; this package does not establish a ready Rocky Linux/systemd userspace. See [validation scope](VALIDATION.md).

## Diagnose a failed or incomplete start

Run one or more of these commands on the VM:

```bash
cloud-init status --long
journalctl -u thoxinux.service -n 50 --no-pager
/usr/local/bin/thox-kernel logs --lines 30
```

| Symptom | Check |
| --- | --- |
| Azure rejects creation | Read the deployment operation error for quota, region capacity, permissions or subscription policy |
| `thox-kernel` is missing | Inspect cloud-init status and `tail -n 40 /var/log/cloud-init-output.log` |
| Portal acceptance extension fails | Inspect its status, then cloud-init and the service journal; the gate has a 15-minute initialization limit |
| EFI checksum mismatch | Preserve the failure and compare the release with `payload/pins.json`; do not bypass the checksum check |
| Service exits or remains unaccepted | Inspect the guest serial log and the receipt's `error` field |
| Controller command cannot connect | Confirm acceptance, then run the command through `runuser -u thoxinux --` |
| Run Command produces no result | Check the VM agent and outbound connectivity; it requires access to Azure endpoints |

Full evidence stays on the host:

| Path | Contents |
| --- | --- |
| `/var/log/thoxinux/acceptance.json` | Current session, checks, process identities and serial-session hashes |
| `/var/log/thoxinux/serial.log` | Appended guest console output |
| `/opt/thoxinux/install-receipt.json` | Installation provenance; not current acceptance |
| `/var/lib/thoxinux/volume.raw` | Persistent 64 GiB sparse guest volume |

Azure Run Command returns only a bounded output window, so use short log tails. Azure boot diagnostics describe the Ubuntu host; the ThoxInux serial log supplies guest evidence. Sources: [Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command), [boot diagnostics](https://learn.microsoft.com/en-us/azure/virtual-machines/boot-diagnostics).

## Lifecycle and costs

To stop, start or restart only the guest, run the appropriate command on the VM:

```bash
systemctl stop thoxinux.service
systemctl start thoxinux.service
systemctl restart thoxinux.service
```

Stopping requests a clean kernel shutdown and clears running acceptance. Starting performs new boot checks while retaining the existing guest volume. Query current status after a start or restart.

To pause Azure compute charges, use the VM's **Stop** action and confirm **Stopped (deallocated)**. A shutdown inside Ubuntu or stopping the guest service does not establish deallocation. The managed disk and static public IPv4 remain allocated and billable. [Azure power states and billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

For a VM created with the CLI helper, the original plan supports:

```bash
python3 deploy.py deallocate --plan azure-plan
```

Use Azure **Start** to resume the host; the enabled service starts the guest. Portal-created resources have `thox-entrypoint=azure-portal` and must be managed through the portal or explicit Azure resource IDs. They do not have a CLI plan's ownership identity.

Deleting the dedicated resource group removes its resources and stored guest data. Deleting only the VM can leave the detached disk and NIC because this profile preserves them. Review the group's remaining resources when retiring the lab. Actual charges depend on the selected region, subscription and runtime; no live price quote is included.
