#!/usr/bin/env python3
"""Prepare and deploy one Azure host for the pinned ThoxInux kernel guest.

Copyright 2026 THOX.ai LLC. SPDX-License-Identifier: Apache-2.0
The local plan is evidence of preparation only, never of Azure or guest success.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
PROJECT = "thoxinux-kernel"
SIZES = ("Standard_D2s_v5", "Standard_D4s_v5")
IMAGE_PREFIX = "Canonical:ubuntu-24_04-lts:server:"
PLAN_FILES = ("azuredeploy.json", "parameters.json", "cloud-init.yaml")
MAX_CUSTOM_DATA_BYTES = 65535


class DeploymentError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(command, *, timeout=3600):
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(f"Could not complete {command[0]}: {exc}") from exc
    if result.returncode:
        # Azure CLI commands here never request credentials or access tokens.
        raise DeploymentError(f"Command failed ({result.returncode}): "
                              f"{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def az(arguments, subscription=None):
    if shutil.which("az") is None:
        raise DeploymentError("Azure CLI is unavailable. Run this package in Azure Cloud Shell (Bash) or an authenticated Azure CLI environment.")
    command = ["az", *arguments]
    if subscription:
        command += ["--subscription", subscription]
    command += ["--only-show-errors", "--output", "json"]
    output = run(command)
    try:
        return json.loads(output) if output.strip() else None
    except json.JSONDecodeError as exc:
        raise DeploymentError("Azure CLI returned a non-JSON response; no success inferred.") from exc


def validate_config(config):
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", config["subscription_id"]):
        raise DeploymentError("Use an explicit Azure subscription GUID.")
    if config["subscription_id"] == "00000000-0000-0000-0000-000000000000":
        raise DeploymentError("The placeholder subscription ID cannot be deployed.")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.()-]{0,88}[a-zA-Z0-9_-]", config["resource_group"]):
        raise DeploymentError("Invalid dedicated resource group name.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", config["vm_name"]):
        raise DeploymentError("VM name must be 3–63 lowercase letters, digits or hyphens and end with a letter or digit.")
    if not re.fullmatch(r"[a-z0-9]{2,30}", config["location"]):
        raise DeploymentError("Use an Azure location identifier such as eastus.")
    if config["vm_size"] not in SIZES:
        raise DeploymentError("Unsupported VM size in this reviewed deployment profile.")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,31}", config["admin_username"]) or config["admin_username"] in {"root", "admin", "administrator", "test", "user"}:
        raise DeploymentError("Use a non-reserved Linux admin username.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", config["image_version"]):
        raise DeploymentError("An exact numeric image version is required; latest is not a reproducible plan.")


def public_key_text(path):
    if path.is_symlink() or not path.is_file():
        raise DeploymentError("SSH public key must be a regular file, not a symlink.")
    try:
        text = path.read_text().strip()
    except UnicodeError as exc:
        raise DeploymentError("SSH public key must be UTF-8 text.") from exc
    if "PRIVATE KEY" in text or "\n" in text or len(text) > 8192:
        raise DeploymentError("Provide one public SSH key, never a private key.")
    fields = text.split()
    if len(fields) < 2 or fields[0] not in {"ssh-ed25519", "ssh-rsa"}:
        raise DeploymentError("Expected an OpenSSH ed25519 or RSA public key.")
    try:
        blob = base64.b64decode(fields[1], validate=True)
        length = struct.unpack(">I", blob[:4])[0]
        if blob[4:4 + length].decode("ascii") != fields[0]:
            raise ValueError("key type mismatch")
        offset = 4 + length
        first_len = struct.unpack(">I", blob[offset:offset + 4])[0]
        offset += 4
        if fields[0] == "ssh-ed25519":
            if first_len != 32 or len(blob) != offset + 32:
                raise ValueError("invalid ed25519 length")
        else:
            offset += first_len
            modulus_len = struct.unpack(">I", blob[offset:offset + 4])[0]
            modulus = blob[offset + 4:]
            if len(modulus) != modulus_len or int.from_bytes(modulus, "big").bit_length() < 2048:
                raise ValueError("RSA public key must have at least 2048 bits")
    except (ValueError, UnicodeDecodeError, struct.error) as exc:
        raise DeploymentError(f"Invalid SSH public key: {exc}") from exc
    return text


def authenticated_subscription(requested=None):
    if requested:
        account = az(["account", "show"], requested)
    else:
        accounts = az(["account", "list", "--all"])
        enabled = {item["id"]: item for item in accounts if item.get("state") == "Enabled"}
        if len(enabled) != 1:
            raise DeploymentError("Specify --subscription: there must be exactly one enabled Azure subscription to select automatically.")
        account = next(iter(enabled.values()))
    if account.get("state") != "Enabled":
        raise DeploymentError("The selected subscription is not enabled.")
    return account["id"]


def resolve_image(subscription, location):
    image = az(["vm", "image", "show", "--location", location, "--urn", IMAGE_PREFIX + "latest"], subscription)
    if image.get("hyperVGeneration") != "V2":
        raise DeploymentError("Resolved Ubuntu image is not verified Generation 2.")
    if image.get("architecture") not in (None, "x64"):
        raise DeploymentError("Resolved Ubuntu image is not x64.")
    version = image.get("name", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise DeploymentError("Azure did not return an exact image version.")
    return version


def ensure_public_key(vm_name, supplied=None):
    if supplied:
        path = Path(supplied).expanduser()
        public_key_text(path)
        return path
    # Called only after live Azure account verification in prepare().
    private = Path.home() / ".ssh" / vm_name
    public = private.with_name(private.name + ".pub")
    if public.exists():
        public_key_text(public)
        return public
    if private.exists() or private.is_symlink() or public.is_symlink():
        raise DeploymentError("A dedicated key path already exists without a usable public key; specify --ssh-public-key. Nothing was overwritten.")
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "thoxinux-kernel-azure", "-f", str(private)], timeout=60)
    public_key_text(public)
    return public


def config_from_args(args, subscription, image_version):
    return {
        "subscription_id": subscription,
        "resource_group": args.resource_group,
        "location": args.location,
        "vm_name": args.name,
        "vm_size": args.size,
        "admin_username": args.admin_username,
        "image_version": image_version,
    }


def create_plan(config, key_path, output, template_path=None, cloud_init_path=None):
    validate_config(config)
    key = public_key_text(Path(key_path))
    template = (template_path or ROOT / "azuredeploy.json").read_bytes()
    cloud_init = (cloud_init_path or ROOT / "cloud-init.yaml").read_bytes()
    if not cloud_init.startswith(b"#cloud-config\n"):
        raise DeploymentError("cloud-init.yaml must start with #cloud-config.")
    if not 0 < len(cloud_init) <= MAX_CUSTOM_DATA_BYTES:
        raise DeploymentError("cloud-init exceeds Azure's 64 KiB decoded customData limit.")
    try:
        json.loads(template)
    except json.JSONDecodeError as exc:
        raise DeploymentError("Invalid ARM template JSON.") from exc
    digest = sha256(canonical({"config": config, "ssh_public_key": key,
                               "template_sha256": sha256(template),
                               "cloud_init_sha256": sha256(cloud_init)}))
    parameters = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in {
            "location": config["location"], "vmName": config["vm_name"],
            "vmSize": config["vm_size"], "adminUsername": config["admin_username"],
            "sshPublicKey": key, "imageVersion": config["image_version"],
            "customData": base64.b64encode(cloud_init).decode(), "planDigest": digest,
        }.items()},
    }
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise DeploymentError("Plan output must be a new or empty directory. Existing plans are never overwritten.")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    (output / "azuredeploy.json").write_bytes(template)
    (output / "cloud-init.yaml").write_bytes(cloud_init)
    write_json(output / "parameters.json", parameters)
    record = {"schema_version": 1, "stage": "prepared_not_deployed", "created_utc": utc_now(),
              "config": config, "plan_digest": digest,
              "files": {name: sha256((output / name).read_bytes()) for name in PLAN_FILES}}
    write_json(output / "plan.json", record)
    return record


def load_plan(path):
    path = Path(path).resolve()
    try:
        record = json.loads((path / "plan.json").read_text())
        if record.get("schema_version") != 1 or set(record["files"]) != set(PLAN_FILES):
            raise DeploymentError("Unsupported plan schema or file inventory.")
        validate_config(record["config"])
        for name in PLAN_FILES:
            file = path / name
            if file.is_symlink() or sha256(file.read_bytes()) != record["files"][name]:
                raise DeploymentError(f"Prepared plan has changed: {name}. Prepare a new plan.")
        parameters = json.loads((path / "parameters.json").read_text())["parameters"]
        config = record["config"]
        expected = {"location": config["location"], "vmName": config["vm_name"],
                    "vmSize": config["vm_size"], "adminUsername": config["admin_username"],
                    "imageVersion": config["image_version"], "planDigest": record["plan_digest"]}
        if any(parameters[name]["value"] != value for name, value in expected.items()):
            raise DeploymentError("Plan parameters do not match the saved configuration.")
        cloud_init = (path / "cloud-init.yaml").read_bytes()
        if base64.b64decode(parameters["customData"]["value"], validate=True) != cloud_init:
            raise DeploymentError("Plan customData does not match the reviewed cloud-init.")
        digest = sha256(canonical({"config": config, "ssh_public_key": parameters["sshPublicKey"]["value"],
                                  "template_sha256": record["files"]["azuredeploy.json"],
                                  "cloud_init_sha256": record["files"]["cloud-init.yaml"]}))
        if digest != record["plan_digest"]:
            raise DeploymentError("Prepared plan digest mismatch.")
        return path, record
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise DeploymentError(f"Invalid prepared plan: {exc}") from exc


def expected_tags(record):
    return {"thox-project": PROJECT, "thox-instance": record["config"]["vm_name"], "thox-plan": record["plan_digest"]}


def owned_tags(tags, record):
    return all((tags or {}).get(key) == value for key, value in expected_tags(record).items())


def ownership_preflight(record, *, must_exist=False):
    """No writes: inventory must succeed and all existing resources must be ours."""
    config = record["config"]
    subscription = config["subscription_id"]
    group = config["resource_group"]
    exists = az(["group", "exists", "--name", group], subscription)
    if exists is not True:
        if exists is not False:
            raise DeploymentError("Could not establish whether the target group exists.")
        if must_exist:
            raise DeploymentError("Dedicated resource group does not exist. deploy creates it, then performs validation and what-if before VM creation.")
        return False
    group_info = az(["group", "show", "--name", group], subscription)
    if not owned_tags(group_info.get("tags"), record):
        raise DeploymentError("Existing resource group does not belong to this exact ThoxInux plan. Choose a new dedicated resource group.")
    resources = az(["resource", "list", "--resource-group", group], subscription)
    if not isinstance(resources, list):
        raise DeploymentError("Resource inventory failed; deployment stopped.")
    vm_id = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Compute/virtualMachines/{config['vm_name']}"
    vm_entries = [item for item in resources if item.get("id", "").lower() == vm_id.lower()]
    vm = None
    if vm_entries:
        if len(vm_entries) != 1 or not owned_tags(vm_entries[0].get("tags"), record):
            raise DeploymentError("Existing VM ownership is not established.")
        vm = az(["vm", "show", "--resource-group", group, "--name", config["vm_name"]], subscription)
        image = vm.get("storageProfile", {}).get("imageReference", {})
        if {k: image.get(k) for k in ("publisher", "offer", "sku", "version")} != {
            "publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": config["image_version"]}:
            raise DeploymentError("Existing VM image differs. The helper will not replace or update an unrelated VM.")
        if vm.get("hardwareProfile", {}).get("vmSize") != config["vm_size"]:
            raise DeploymentError("Existing VM size differs from the exact plan.")
    os_disk_id = (vm or {}).get("storageProfile", {}).get("osDisk", {}).get("managedDisk", {}).get("id", "")
    for resource in resources:
        if owned_tags(resource.get("tags"), record):
            continue
        # Azure-created managed OS disks do not always inherit VM tags. Require
        # both the owned VM's OS disk reference and the disk's live managedBy.
        if os_disk_id and resource.get("id", "").lower() == os_disk_id.lower() and resource.get("type", "").lower() == "microsoft.compute/disks":
            disk = az(["disk", "show", "--ids", os_disk_id], subscription)
            if disk.get("managedBy", "").lower() == vm_id.lower():
                continue
        # Action Run Command installs this Azure agent extension without our
        # tags. Permit only its exact child resource under the verified VM,
        # then check the provider and handler in its live ARM properties.
        extension_id = vm_id + "/extensions/RunCommandLinux"
        if vm is not None and resource.get("id", "").lower() == extension_id.lower() and resource.get("type", "").lower() == "microsoft.compute/virtualmachines/extensions":
            extension = az(["resource", "show", "--ids", extension_id, "--api-version", "2024-07-01"], subscription)
            properties = extension.get("properties", {})
            if properties.get("publisher") == "Microsoft.CPlat.Core" and properties.get("type") == "RunCommandLinux":
                continue
        raise DeploymentError(f"Refusing unowned resource in target group: {resource.get('id', 'unknown')}.")
    return True


def record_stage(path, stage, result):
    write_json(path / f"{stage}-receipt.json", {"stage": stage, "observed_utc": utc_now(), "result": result})


def arm_operation(path, record, command):
    config = record["config"]
    args = ["deployment", "group", command, "--resource-group", config["resource_group"],
            "--name", f"thoxinux-{record['plan_digest'][:12]}",
            "--template-file", str(path / "azuredeploy.json"),
            "--parameters", "@" + str(path / "parameters.json")]
    if command == "create":
        args += ["--mode", "Incremental"]
    elif command == "what-if":
        # CLI otherwise emits a human-formatted diff even with --output json.
        args += ["--no-pretty-print", "--result-format", "FullResourcePayloads"]
    result = az(args, config["subscription_id"])
    record_stage(path, command, result)
    return result


def inspect_what_if(result):
    """Block destructive changes rather than treating preview output as approval."""
    if not isinstance(result, dict) or result.get("status") not in ("Succeeded", None):
        raise DeploymentError("Azure what-if did not succeed.")
    changes = result.get("changes")
    if not isinstance(changes, list):
        raise DeploymentError("Azure what-if returned no inspectable changes.")
    allowed = {"Create", "NoChange", "Ignore"}
    denied = [item for item in changes if item.get("changeType") not in allowed]
    if denied:
        kinds = sorted({str(item.get("changeType")) for item in denied})
        raise DeploymentError("What-if contains changes requiring a new dedicated plan or human review: " + ", ".join(kinds))
    return {"changes": len(changes), "create": sum(c.get("changeType") == "Create" for c in changes), "destructive": False}


STATUS_SCRIPT = r'''python3 - <<'PY'
import json, subprocess, sys
try:
    probe = subprocess.run(['/usr/local/bin/thox-kernel','status'], capture_output=True, text=True, timeout=20)
    evidence = json.loads(probe.stdout)
    probe_exit = probe.returncode
except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    evidence = {'accepted':False,'running':False,'state':'unavailable','error':type(exc).__name__}
    probe_exit = 1
active = subprocess.run(['systemctl','is-active','thoxinux.service'], capture_output=True, text=True, timeout=10).stdout.strip()
cloud = subprocess.run(['cloud-init','status'], capture_output=True, text=True, timeout=10).stdout.strip()[:200]
fields = ('release','state','efi_sha256','accepted_at','updated_at','error','session_id','heartbeat_age_seconds','checks_passed','checks_total')
summary = {k:(v[:240] if isinstance(v,str) else v) for k,v in evidence.items() if k in fields and (v is None or isinstance(v,(str,int,float,bool)))}
accepted = probe_exit == 0 and evidence.get('accepted') is True and evidence.get('running') is True and active == 'active'
summary.update(service=active[:40],cloud_init=cloud,helper_exit=probe_exit,accepted=accepted,running=evidence.get('running') is True)
print('THOX_STATUS_JSON=' + json.dumps(summary,separators=(',',':')))
sys.exit(0 if accepted else 2)
PY'''


def status(path, record):
    ownership_preflight(record, must_exist=True)
    config = record["config"]
    command = az(["vm", "run-command", "invoke", "--resource-group", config["resource_group"],
                  "--name", config["vm_name"], "--command-id", "RunShellScript", "--scripts", STATUS_SCRIPT], config["subscription_id"])
    record_stage(path, "guest-status", command)
    return parse_guest_status(command)


def parse_guest_status(command):
    if not isinstance(command, dict) or not isinstance(command.get("value"), list):
        raise DeploymentError("Run Command returned no readable execution result.")
    messages = "\n".join(item.get("message", "") for item in command["value"] if isinstance(item, dict))
    payloads = [line[len("THOX_STATUS_JSON="):] for line in messages.splitlines() if line.startswith("THOX_STATUS_JSON=")]
    if len(payloads) != 1:
        raise DeploymentError("Run Command completed without one bounded ThoxInux status receipt; guest acceptance is unknown.")
    try:
        summary = json.loads(payloads[0])
    except json.JSONDecodeError as exc:
        raise DeploymentError("Run Command status receipt is invalid or truncated.") from exc
    if not isinstance(summary, dict):
        raise DeploymentError("Run Command status receipt is not an object.")
    accepted = (summary.get("accepted") is True and summary.get("running") is True
                and summary.get("helper_exit") == 0 and summary.get("service") == "active")
    return {"stage": "guest_accepted" if accepted else "guest_not_accepted",
            "run_command_returned": True, "guest_accepted": accepted, "guest": summary}


def prepare(args, local_only=False):
    if local_only:
        if not args.subscription or not args.image_version or not args.ssh_public_key:
            raise DeploymentError("Local plan requires --subscription, --image-version, and --ssh-public-key. prepare resolves these in an authenticated environment.")
        subscription = args.subscription
        version = args.image_version
        key = Path(args.ssh_public_key).expanduser()
    else:
        subscription = authenticated_subscription(args.subscription)
        # Always resolve live and pin a Gen2 image; --image-version is local-plan only.
        if args.image_version:
            raise DeploymentError("Use local plan for an explicit preverified --image-version; prepare resolves the current regional Gen2 version.")
        version = resolve_image(subscription, args.location)
        # Validate the VM name before it can form a local key path.
        validate_config(config_from_args(args, subscription, version))
        key = ensure_public_key(args.name, args.ssh_public_key)
    config = config_from_args(args, subscription, version)
    record = create_plan(config, key, Path(args.out))
    return {"stage": record["stage"], "plan": str(Path(args.out).resolve()), "config": config,
            "plan_digest": record["plan_digest"], "live_azure_deployed": False}


def execute(args):
    if args.command in ("plan", "prepare"):
        return prepare(args, local_only=args.command == "plan")
    path, record = load_plan(args.plan)
    config = record["config"]
    if authenticated_subscription(config["subscription_id"]).lower() != config["subscription_id"].lower():
        raise DeploymentError("Azure returned a different subscription.")
    if args.command == "status":
        return status(path, record)
    exists = ownership_preflight(record, must_exist=args.command != "deploy")
    if args.command == "deallocate":
        result = az(["vm", "deallocate", "--resource-group", config["resource_group"], "--name", config["vm_name"]], config["subscription_id"])
        record_stage(path, "deallocate", result)
        return {"stage": "deallocated", "note": "Managed OS disk and Standard public IPv4 remain allocated and billable. No resources were deleted."}
    if args.command == "deploy":
        if not exists:
            result = az(["group", "create", "--name", config["resource_group"], "--location", config["location"],
                         "--tags", *[f"{k}={v}" for k, v in expected_tags(record).items()]], config["subscription_id"])
            record_stage(path, "resource-group", result)
            ownership_preflight(record, must_exist=True)
        arm_operation(path, record, "validate")
        preview = arm_operation(path, record, "what-if")
        summary = inspect_what_if(preview)
        # Recheck ownership immediately before creation after the read-only previews.
        ownership_preflight(record, must_exist=True)
        result = arm_operation(path, record, "create")
        state = result.get("properties", {}).get("provisioningState") if isinstance(result, dict) else None
        if state != "Succeeded":
            raise DeploymentError("ARM did not confirm successful provisioning. Inspect create-receipt.json before any retry.")
        return {"stage": "azure_resources_provisioned", "provisioning_state": state, "what_if": summary,
                "guest_accepted": False, "note": "cloud-init and the ThoxInux boot test may still be running. Run status; provisioning is not guest acceptance.",
                "vm_name": config["vm_name"], "resource_group": config["resource_group"]}
    result = arm_operation(path, record, args.command)
    if args.command == "what-if":
        return {"stage": "what-if", "summary": inspect_what_if(result)}
    return {"stage": "validated", "azure_resources_provisioned": False}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("plan", "prepare"):
        c = sub.add_parser(command, help="Pure local plan" if command == "plan" else "Resolve Azure identity/image; create a local immutable plan")
        c.add_argument("--subscription")
        c.add_argument("--resource-group", default="rg-thoxinux-kernel")
        c.add_argument("--name", default="thoxinux-kernel-01")
        c.add_argument("--location", default="eastus")
        c.add_argument("--size", choices=SIZES, default=SIZES[0])
        c.add_argument("--admin-username", default="thoxadmin")
        c.add_argument("--ssh-public-key")
        c.add_argument("--image-version")
        c.add_argument("--out", default="azure-plan")
    for command in ("validate", "what-if", "deploy", "status", "deallocate"):
        c = sub.add_parser(command)
        c.add_argument("--plan", required=True)
    return p


def main():
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, indent=2))
        if result.get("stage") == "guest_not_accepted":
            return 2
    except (DeploymentError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
