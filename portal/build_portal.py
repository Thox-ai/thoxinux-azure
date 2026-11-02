#!/usr/bin/env python3
"""Generate the self-contained Azure portal template from the reviewed CLI profile.

Copyright 2026 THOX.ai LLC. SPDX-License-Identifier: Apache-2.0
This generates files only. It never contacts Azure or provisions cloud resources.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_SCRIPT = "\n".join((
    "set -euo pipefail",
    "timeout 900s cloud-init status --wait --long",
    "systemctl is-active --quiet thoxinux.service",
    "/usr/local/bin/thox-kernel status",
))


def generate(root=ROOT):
    cloud_init = (root / "cloud-init.yaml").read_bytes()
    if not cloud_init.startswith(b"#cloud-config\n") or not 0 < len(cloud_init) < 65536:
        raise ValueError("Expected valid cloud-init under the Azure 64 KiB decoded customData limit")
    template = json.loads((root / "azuredeploy.json").read_text())
    pins = json.loads((root / "payload" / "pins.json").read_text())
    digest = hashlib.sha256(cloud_init).hexdigest()
    parameters = template["parameters"]
    template["metadata"] = {
        "description": "Deploy the real ThoxInux kernel in QEMU TCG on an Azure Ubuntu host. Select Create new resource group. This portal entry point does not run the CLI ownership safeguards.",
        "generatedFrom": "azuredeploy.json and cloud-init.yaml; run python3 portal/build_portal.py",
        "cloudInitSha256": digest,
        "kernelRelease": pins["release"],
        "kernelSha256": pins["efi_sha256"],
        "hostImagePolicy": "Resolve Ubuntu24.04 latest at deployment; record imageReference.exactVersion in deployment outputs.",
    }
    template["parameters"] = {
        "vmName": {
            "type": "string",
            "defaultValue": "[concat('thoxinux-', uniqueString(resourceGroup().id))]",
            "minLength": 3,
            "maxLength": 63,
            "metadata": {"description": "VM name in a NEW dedicated resource group. Use lowercase letters, digits and hyphens. The default adds a stable resource-group suffix to reduce collisions."},
        },
        "location": {
            "type": "string",
            "defaultValue": "[resourceGroup().location]",
            "metadata": {"description": "Azure region for the VM. Defaults to the new resource group's location; the selected Dsv5 size must be available with sufficient subscription quota."},
        },
        "vmSize": copy.deepcopy(parameters["vmSize"]),
        "sshPublicKey": {
            "type": "secureString",
            "minLength": 40,
            "maxLength": 8192,
            "metadata": {"description": "Paste one existing OpenSSH public key (ssh-ed25519 or ssh-rsa). Do not paste a private key. No inbound SSH port is opened; management uses Azure Run Command."},
        },
    }
    template["parameters"]["vmSize"]["metadata"] = {
        "description": "D2s_v5: 2 vCPU / 8 GiB host RAM. D4s_v5: 4 vCPU / 16 GiB. The validated ThoxInux guest stays at 1 vCPU / 2 GiB. Compute, disk and public IPv4 are billable."
    }
    template["variables"]["tags"] = {
        "thox-project": "thoxinux-kernel",
        "thox-instance": "[parameters('vmName')]",
        "thox-entrypoint": "azure-portal",
        "thox-cloudinit-sha256": digest,
        "thox-kernel-release": pins["release"],
    }
    vms = [r for r in template["resources"] if r["type"] == "Microsoft.Compute/virtualMachines"]
    if len(vms) != 1:
        raise ValueError("Expected exactly one VM in the source profile")
    vm = vms[0]
    vm["properties"]["osProfile"]["adminUsername"] = "thoxadmin"
    vm["properties"]["osProfile"]["customData"] = base64.b64encode(cloud_init).decode("ascii")
    keys = vm["properties"]["osProfile"]["linuxConfiguration"]["ssh"]["publicKeys"]
    if len(keys) != 1:
        raise ValueError("Expected exactly one SSH public-key entry")
    keys[0]["path"] = "/home/thoxadmin/.ssh/authorized_keys"
    vm["properties"]["storageProfile"]["imageReference"]["version"] = "latest"
    template["resources"].append({
        "type": "Microsoft.Compute/virtualMachines/extensions",
        "apiVersion": "2024-07-01",
        "name": "[concat(parameters('vmName'), '/thoxinux-guest-acceptance')]",
        "location": "[parameters('location')]",
        "tags": "[variables('tags')]",
        "dependsOn": ["[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]"],
        "properties": {
            "publisher": "Microsoft.Azure.Extensions",
            "type": "CustomScript",
            "typeHandlerVersion": "2.1",
            "autoUpgradeMinorVersion": True,
            "forceUpdateTag": "[deployment().name]",
            "settings": {
                "commandToExecute": "/bin/bash -c " + shlex.quote(ACCEPTANCE_SCRIPT),
            },
        },
    })
    template["outputs"].update({
        "kernelRelease": {"type": "string", "value": pins["release"]},
        "kernelSha256": {"type": "string", "value": pins["efi_sha256"]},
        "cloudInitSha256": {"type": "string", "value": digest},
        "deployedUbuntuImageVersion": {
            "type": "string",
            "value": "[reference(resourceId('Microsoft.Compute/virtualMachines', parameters('vmName')), '2024-07-01').storageProfile.imageReference.exactVersion]",
        },
        "guestAcceptanceCommand": {"type": "string", "value": "sudo /usr/local/bin/thox-kernel status"},
        "guestConsoleLogCommand": {"type": "string", "value": "sudo /usr/local/bin/thox-kernel logs --lines 30"},
        "acceptanceBoundary": {"type": "string", "value": "The thoxinux-guest-acceptance extension waits for cloud-init and requires a running, accepted guest before initial deployment succeeds. This is point-in-time evidence; use guestAcceptanceCommand for current status."},
    })
    return (json.dumps(template, indent=2) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed portal template differs from generated bytes")
    args = parser.parse_args()
    target = ROOT / "portal" / "azuredeploy.json"
    data = generate()
    if args.check:
        if not target.is_file() or target.read_bytes() != data:
            raise SystemExit("portal/azuredeploy.json is stale; run python3 portal/build_portal.py")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(json.dumps({"mode": "checked" if args.check else "generated", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}))


if __name__ == "__main__":
    main()
