"""Offline boundary tests. Azure CLI responses are doubles, not live evidence."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("thox_deploy", ROOT / "deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def public_key():
    keytype = b"ssh-ed25519"
    body = struct.pack(">I", len(keytype)) + keytype + struct.pack(">I", 32) + bytes(range(32))
    return "ssh-ed25519 " + base64.b64encode(body).decode() + " offline-test-public-key"


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.key = self.folder / "public.pub"
        self.key.write_text(public_key() + "\n")
        self.cloud = self.folder / "cloud-init.yaml"
        self.cloud.write_text("#cloud-config\npackage_update: true\n")
        self.config = {"subscription_id": "11111111-2222-3333-4444-555555555555", "resource_group": "rg-thoxinux-test",
                       "location": "eastus", "vm_name": "thoxinux-test-01", "vm_size": "Standard_D2s_v5",
                       "admin_username": "thoxadmin", "image_version": "24.04.202610160"}
        self.output = self.folder / "plan"

    def make_plan(self):
        return deploy.create_plan(self.config, self.key, self.output, ROOT / "azuredeploy.json", self.cloud)

    def test_local_plan_does_not_call_azure_and_roundtrips(self):
        with patch.object(deploy, "az", side_effect=AssertionError("network call in local plan")):
            record = self.make_plan()
            _, loaded = deploy.load_plan(self.output)
        self.assertEqual(loaded, record)
        self.assertEqual(record["stage"], "prepared_not_deployed")

    def test_rejects_placeholder_subscription(self):
        self.config["subscription_id"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaisesRegex(deploy.DeploymentError, "placeholder"):
            self.make_plan()
        self.assertFalse(self.output.exists())

    def test_rejects_floating_image(self):
        self.config["image_version"] = "latest"
        with self.assertRaisesRegex(deploy.DeploymentError, "exact numeric"):
            self.make_plan()

    def test_rejects_private_key_material(self):
        self.key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n")
        with self.assertRaisesRegex(deploy.DeploymentError, "never a private key"):
            self.make_plan()

    def test_rejects_invalid_public_key_wire_format(self):
        self.key.write_text("ssh-ed25519 " + base64.b64encode(b"invalid").decode())
        with self.assertRaisesRegex(deploy.DeploymentError, "Invalid SSH"):
            self.make_plan()

    def test_rejects_symlink_public_key(self):
        alias = self.folder / "alias.pub"
        alias.symlink_to(self.key)
        with self.assertRaisesRegex(deploy.DeploymentError, "not a symlink"):
            deploy.public_key_text(alias)

    def test_rejects_oversized_cloud_init(self):
        self.cloud.write_bytes(b"#cloud-config\n" + b"x" * 65535)
        with self.assertRaisesRegex(deploy.DeploymentError, "64 KiB"):
            self.make_plan()

    def test_rejects_tampered_cloud_init(self):
        self.make_plan()
        (self.output / "cloud-init.yaml").write_text("#cloud-config\nruncmd: [unexpected]\n")
        with self.assertRaisesRegex(deploy.DeploymentError, "has changed"):
            deploy.load_plan(self.output)

    def test_rejects_tampered_config_even_when_file_hashes_match(self):
        self.make_plan()
        record = json.loads((self.output / "plan.json").read_text())
        record["config"]["resource_group"] = "other-group"
        deploy.write_json(self.output / "plan.json", record)
        with self.assertRaisesRegex(deploy.DeploymentError, "digest mismatch"):
            deploy.load_plan(self.output)

    def test_rejects_overwrite_existing_plan(self):
        self.make_plan()
        with self.assertRaisesRegex(deploy.DeploymentError, "never overwritten"):
            self.make_plan()

    def test_ambiguous_subscription_does_not_select_default(self):
        accounts = [{"id": "a", "state": "Enabled", "isDefault": True}, {"id": "b", "state": "Enabled"}]
        with patch.object(deploy, "az", return_value=accounts):
            with self.assertRaisesRegex(deploy.DeploymentError, "Specify --subscription"):
                deploy.authenticated_subscription()

    def test_refuses_non_gen2_image(self):
        with patch.object(deploy, "az", return_value={"hyperVGeneration": "V1", "name": "24.04.202610160"}):
            with self.assertRaisesRegex(deploy.DeploymentError, "Generation 2"):
                deploy.resolve_image(self.config["subscription_id"], "eastus")

    def test_prepare_checks_identity_before_generating_key(self):
        args = deploy.parser().parse_args(["prepare"])
        with patch.object(deploy, "authenticated_subscription", side_effect=deploy.DeploymentError("sign-in required")), \
             patch.object(deploy, "ensure_public_key") as keygen:
            with self.assertRaisesRegex(deploy.DeploymentError, "sign-in required"):
                deploy.prepare(args)
        keygen.assert_not_called()

    def test_unsafe_vm_name_rejected_before_key_generation(self):
        args = deploy.parser().parse_args(["prepare", "--name", "../../unsafe-key"])
        with patch.object(deploy, "authenticated_subscription", return_value=self.config["subscription_id"]), \
             patch.object(deploy, "resolve_image", return_value=self.config["image_version"]), \
             patch.object(deploy, "ensure_public_key") as keygen:
            with self.assertRaisesRegex(deploy.DeploymentError, "VM name"):
                deploy.prepare(args)
        keygen.assert_not_called()

    def test_group_with_wrong_tags_blocks_before_inventory_or_write(self):
        record = self.make_plan()
        with patch.object(deploy, "az", side_effect=[True, {"tags": {"purpose": "other"}}]) as azure:
            with self.assertRaisesRegex(deploy.DeploymentError, "does not belong"):
                deploy.ownership_preflight(record)
        self.assertEqual(azure.call_count, 2)

    def test_unowned_resource_blocks_deployment(self):
        record = self.make_plan()
        resource = {"id": "/unexpected", "tags": {}, "type": "Microsoft.Storage/storageAccounts"}
        with patch.object(deploy, "az", side_effect=[True, {"tags": deploy.expected_tags(record)}, [resource]]):
            with self.assertRaisesRegex(deploy.DeploymentError, "unowned resource"):
                deploy.ownership_preflight(record)

    def test_group_inventory_failure_is_not_treated_as_empty(self):
        record = self.make_plan()
        with patch.object(deploy, "az", side_effect=[True, {"tags": deploy.expected_tags(record)}, deploy.DeploymentError("forbidden")]):
            with self.assertRaisesRegex(deploy.DeploymentError, "forbidden"):
                deploy.ownership_preflight(record)

    def test_absent_group_has_no_mutations_during_preflight(self):
        record = self.make_plan()
        with patch.object(deploy, "az", return_value=False) as azure:
            self.assertFalse(deploy.ownership_preflight(record))
        self.assertEqual(azure.call_count, 1)
        self.assertEqual(azure.call_args.args[0][:2], ["group", "exists"])

    def test_owned_vm_with_different_image_is_never_replaced(self):
        record = self.make_plan()
        config = record["config"]
        vm_id = f"/subscriptions/{config['subscription_id']}/resourceGroups/{config['resource_group']}/providers/Microsoft.Compute/virtualMachines/{config['vm_name']}"
        resources = [{"id": vm_id, "tags": deploy.expected_tags(record)}]
        vm = {"storageProfile": {"imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "24.04.202401010"}}}
        with patch.object(deploy, "az", side_effect=[True, {"tags": deploy.expected_tags(record)}, resources, vm]):
            with self.assertRaisesRegex(deploy.DeploymentError, "VM image differs"):
                deploy.ownership_preflight(record)

    def test_untagged_disk_requires_bidirectional_vm_reference(self):
        record = self.make_plan()
        config = record["config"]
        vm_id = f"/subscriptions/{config['subscription_id']}/resourceGroups/{config['resource_group']}/providers/Microsoft.Compute/virtualMachines/{config['vm_name']}"
        disk_id = vm_id.replace("virtualMachines/", "disks/") + "-os"
        resources = [{"id": vm_id, "tags": deploy.expected_tags(record)}, {"id": disk_id, "type": "Microsoft.Compute/disks"}]
        vm = {"hardwareProfile": {"vmSize": config["vm_size"]}, "storageProfile": {
            "imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": config["image_version"]},
            "osDisk": {"managedDisk": {"id": disk_id}}}}
        base = [True, {"tags": deploy.expected_tags(record)}, resources, vm]
        with patch.object(deploy, "az", side_effect=base + [{"managedBy": vm_id}]):
            self.assertTrue(deploy.ownership_preflight(record))
        with patch.object(deploy, "az", side_effect=base + [{"managedBy": "/other/vm"}]):
            with self.assertRaisesRegex(deploy.DeploymentError, "unowned resource"):
                deploy.ownership_preflight(record)

    def test_only_exact_azure_runcommand_extension_is_allowed(self):
        record = self.make_plan()
        config = record["config"]
        vm_id = f"/subscriptions/{config['subscription_id']}/resourceGroups/{config['resource_group']}/providers/Microsoft.Compute/virtualMachines/{config['vm_name']}"
        extension_id = vm_id + "/extensions/RunCommandLinux"
        resources = [{"id": vm_id, "tags": deploy.expected_tags(record)}, {"id": extension_id, "type": "Microsoft.Compute/virtualMachines/extensions"}]
        vm = {"hardwareProfile": {"vmSize": config["vm_size"]}, "storageProfile": {
            "imageReference": {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": config["image_version"]}}}
        base = [True, {"tags": deploy.expected_tags(record)}, resources, vm]
        with patch.object(deploy, "az", side_effect=base + [{"properties": {"publisher": "Microsoft.CPlat.Core", "type": "RunCommandLinux"}}]):
            self.assertTrue(deploy.ownership_preflight(record))
        with patch.object(deploy, "az", side_effect=base + [{"properties": {"publisher": "Other", "type": "RunCommandLinux"}}]):
            with self.assertRaisesRegex(deploy.DeploymentError, "unowned resource"):
                deploy.ownership_preflight(record)

    def test_delete_modify_or_uninspectable_whatif_is_blocked(self):
        for change in ("Delete", "Modify", "Deploy", "Unsupported"):
            with self.subTest(change=change), self.assertRaises(deploy.DeploymentError):
                deploy.inspect_what_if({"status": "Succeeded", "changes": [{"changeType": change}]})
        with self.assertRaisesRegex(deploy.DeploymentError, "no inspectable changes"):
            deploy.inspect_what_if({"status": "Succeeded"})

    def test_create_and_nochange_preview_accepted(self):
        result = deploy.inspect_what_if({"status": "Succeeded", "changes": [{"changeType": "Create"}, {"changeType": "NoChange"}]})
        self.assertEqual(result, {"changes": 2, "create": 1, "destructive": False})

    def test_whatif_requests_machine_readable_output(self):
        record = self.make_plan()
        with patch.object(deploy, "az", return_value={"status": "Succeeded", "changes": []}) as azure:
            deploy.arm_operation(self.output, record, "what-if")
        args = azure.call_args.args[0]
        self.assertIn("--no-pretty-print", args)
        self.assertEqual(azure.call_args.args[1], self.config["subscription_id"])

    def test_failed_validation_prevents_vm_creation(self):
        record = self.make_plan()
        args = deploy.parser().parse_args(["deploy", "--plan", str(self.output)])
        with patch.object(deploy, "authenticated_subscription", return_value=self.config["subscription_id"]), \
             patch.object(deploy, "ownership_preflight", return_value=True), \
             patch.object(deploy, "arm_operation", side_effect=deploy.DeploymentError("validation rejected")) as arm:
            with self.assertRaisesRegex(deploy.DeploymentError, "validation rejected"):
                deploy.execute(args)
        self.assertEqual([call.args[2] for call in arm.call_args_list], ["validate"])

    def test_successful_provisioning_does_not_claim_guest_acceptance(self):
        self.make_plan()
        args = deploy.parser().parse_args(["deploy", "--plan", str(self.output)])
        with patch.object(deploy, "authenticated_subscription", return_value=self.config["subscription_id"]), \
             patch.object(deploy, "ownership_preflight", return_value=True), \
             patch.object(deploy, "arm_operation", side_effect=[{}, {"status": "Succeeded", "changes": [{"changeType": "Create"}]}, {"properties": {"provisioningState": "Succeeded"}}]):
            result = deploy.execute(args)
        self.assertEqual(result["stage"], "azure_resources_provisioned")
        self.assertIs(result["guest_accepted"], False)

    def test_azure_runcommand_success_without_guest_receipt_is_not_acceptance(self):
        with self.assertRaisesRegex(deploy.DeploymentError, "acceptance is unknown"):
            deploy.parse_guest_status({"value": [{"code": "ProvisioningState/succeeded", "message": "Enable succeeded: [stdout]"}]})

    def test_remote_status_requires_active_service_fresh_accepted_helper(self):
        valid = {"accepted": True, "running": True, "helper_exit": 0, "service": "active"}
        def envelope(value):
            return {"value": [{"message": "Enable succeeded:\n[stdout]\nTHOX_STATUS_JSON=" + json.dumps(value) + "\n[stderr]\n"}]}
        self.assertTrue(deploy.parse_guest_status(envelope(valid))["guest_accepted"])
        for key, value in (("accepted", False), ("running", False), ("helper_exit", 1), ("service", "activating")):
            changed = dict(valid, **{key: value})
            with self.subTest(key=key):
                result = deploy.parse_guest_status(envelope(changed))
                self.assertFalse(result["guest_accepted"])
                self.assertEqual(result["stage"], "guest_not_accepted")


class NetworkAndHostTests(unittest.TestCase):
    def test_inbound_closed_host_secure_and_agent_present(self):
        template = json.loads((ROOT / "azuredeploy.json").read_text())
        resources = {r["type"]: r for r in template["resources"]}
        rules = resources["Microsoft.Network/networkSecurityGroups"]["properties"]["securityRules"]
        self.assertTrue(rules)
        self.assertTrue(all(r["properties"]["access"] == "Deny" for r in rules))
        self.assertEqual(rules[0]["properties"]["sourceAddressPrefix"], "*")
        self.assertEqual(rules[0]["properties"]["destinationPortRange"], "*")
        subnet = resources["Microsoft.Network/virtualNetworks"]["properties"]["subnets"][0]["properties"]
        self.assertIs(subnet["defaultOutboundAccess"], False)
        self.assertEqual(resources["Microsoft.Network/publicIPAddresses"]["sku"]["name"], "Standard")
        vm = resources["Microsoft.Compute/virtualMachines"]["properties"]
        self.assertIs(vm["securityProfile"]["uefiSettings"]["secureBootEnabled"], True)
        self.assertIs(vm["securityProfile"]["uefiSettings"]["vTpmEnabled"], True)
        self.assertIs(vm["osProfile"]["linuxConfiguration"]["provisionVMAgent"], True)
        self.assertIs(vm["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"], True)
        self.assertEqual(vm["storageProfile"]["osDisk"]["diskSizeGB"], 128)
        self.assertNotIn("Microsoft.Network/natGateways", resources)
        self.assertNotIn("Microsoft.Network/bastionHosts", resources)


if __name__ == "__main__":
    unittest.main()
