"""Offline portal packaging guards. These do not establish an Azure deployment."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_portal", ROOT / "portal" / "build_portal.py")
portal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal)


class PortalTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / "portal" / "azuredeploy.json").read_text())
        cls.source = json.loads((ROOT / "azuredeploy.json").read_text())
        cls.vm = next(r for r in cls.template["resources"] if r["type"] == "Microsoft.Compute/virtualMachines")

    def test_generated_template_is_current_and_deterministic(self):
        self.assertEqual((ROOT / "portal" / "azuredeploy.json").read_bytes(), portal.generate())
        self.assertEqual(portal.generate(), portal.generate())

    def test_cloud_init_is_exact_not_a_manual_copy_or_unpinned_remote_script(self):
        cloud = (ROOT / "cloud-init.yaml").read_bytes()
        embedded = base64.b64decode(self.vm["properties"]["osProfile"]["customData"], validate=True)
        self.assertEqual(embedded, cloud)
        self.assertLess(len(embedded), 65536)
        self.assertEqual(self.template["metadata"]["cloudInitSha256"], hashlib.sha256(cloud).hexdigest())

    def test_no_cli_generated_inputs_or_private_credentials_required(self):
        parameters = self.template["parameters"]
        self.assertEqual(set(parameters), {"vmName", "location", "vmSize", "sshPublicKey"})
        self.assertEqual(parameters["sshPublicKey"]["type"], "secureString")
        self.assertNotIn("defaultValue", parameters["sshPublicKey"])
        for name, parameter in parameters.items():
            if name != "sshPublicKey":
                self.assertIn("defaultValue", parameter)
        serialized = json.dumps(self.template)
        self.assertNotIn("PRIVATE KEY", serialized)
        for removed in ("customData", "imageVersion", "planDigest", "adminUsername"):
            self.assertNotIn(f"parameters('{removed}')", serialized)

    def test_all_parameter_references_resolve_in_portal_template(self):
        references = re.findall(r"parameters\('([^']+)'\)", json.dumps(self.template))
        self.assertTrue(references)
        self.assertTrue(set(references) <= set(self.template["parameters"]))

    def test_parameter_form_defaults_are_region_aware_and_collision_resistant(self):
        parameters = self.template["parameters"]
        self.assertEqual(parameters["location"]["defaultValue"], "[resourceGroup().location]")
        self.assertIn("uniqueString(resourceGroup().id)", parameters["vmName"]["defaultValue"])
        self.assertIn("NEW", parameters["vmName"]["metadata"]["description"])

    def test_network_and_security_profile_are_preserved(self):
        original = {r["type"]: r for r in self.source["resources"]}
        for resource in self.template["resources"]:
            if resource["type"].startswith("Microsoft.Network/"):
                self.assertEqual(resource, original[resource["type"]])
        props = self.vm["properties"]
        source_props = original["Microsoft.Compute/virtualMachines"]["properties"]
        self.assertEqual(props["securityProfile"], source_props["securityProfile"])
        self.assertEqual(props["hardwareProfile"], source_props["hardwareProfile"])
        self.assertEqual(props["storageProfile"]["osDisk"], source_props["storageProfile"]["osDisk"])
        self.assertTrue(props["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"])

    def test_current_host_image_resolution_is_recorded_and_kernel_remains_pinned(self):
        image = self.vm["properties"]["storageProfile"]["imageReference"]
        self.assertEqual(image, {"publisher": "Canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "latest"})
        self.assertIn("imageReference.exactVersion", self.template["outputs"]["deployedUbuntuImageVersion"]["value"])
        pins = json.loads((ROOT / "payload" / "pins.json").read_text())
        self.assertEqual(self.template["outputs"]["kernelSha256"]["value"], pins["efi_sha256"])
        self.assertEqual(self.template["outputs"]["kernelRelease"]["value"], pins["release"])

    def test_portal_tags_do_not_impersonate_an_immutable_cli_plan(self):
        tags = self.template["variables"]["tags"]
        self.assertEqual(tags["thox-entrypoint"], "azure-portal")
        self.assertNotIn("thox-plan", tags)
        self.assertEqual(tags["thox-cloudinit-sha256"], self.template["metadata"]["cloudInitSha256"])

    def test_portal_acceptance_extension_waits_and_executes_only_local_commands(self):
        extensions = [r for r in self.template["resources"] if r["type"] == "Microsoft.Compute/virtualMachines/extensions"]
        self.assertEqual(len(extensions), 1)
        extension = extensions[0]
        self.assertIn("resourceId('Microsoft.Compute/virtualMachines'", extension["dependsOn"][0])
        props = extension["properties"]
        self.assertEqual(props["publisher"], "Microsoft.Azure.Extensions")
        self.assertEqual(props["type"], "CustomScript")
        self.assertEqual(props["typeHandlerVersion"], "2.1")
        self.assertEqual(set(props["settings"]), {"commandToExecute"})
        self.assertNotIn("protectedSettings", props)
        command = shlex.split(props["settings"]["commandToExecute"])
        self.assertEqual(command[:2], ["/bin/bash", "-c"])
        self.assertEqual(command[2], portal.ACCEPTANCE_SCRIPT)
        self.assertIn("timeout 900s cloud-init status --wait --long", command[2])
        self.assertEqual(extension["tags"], "[variables('tags')]")

    def test_acceptance_gate_propagates_each_failure_and_stops_following_commands(self):
        # Exercise the actual bash command with explicit local test doubles.
        # No Azure resources, systemd service, or kernel guest are involved.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trace = root / "trace.txt"
            scripts = {
                "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
                "cloud-init": '#!/bin/sh\nprintf "cloud-init\\n" >> "$TRACE"\nexit "$CLOUD_EXIT"\n',
                "systemctl": '#!/bin/sh\nprintf "systemctl\\n" >> "$TRACE"\nexit "$SYSTEMD_EXIT"\n',
                "thox-kernel": '#!/bin/sh\nprintf "thox-kernel\\n" >> "$TRACE"\nexit "$GUEST_EXIT"\n',
            }
            for name, content in scripts.items():
                (root / name).write_text(content)
                (root / name).chmod(0o755)
            script = portal.ACCEPTANCE_SCRIPT.replace("/usr/local/bin/thox-kernel", shlex.quote(str(root / "thox-kernel")))
            cases = [
                (10, 0, 0, 10, ["cloud-init"]),
                (0, 11, 0, 11, ["cloud-init", "systemctl"]),
                (0, 0, 12, 12, ["cloud-init", "systemctl", "thox-kernel"]),
                (0, 0, 0, 0, ["cloud-init", "systemctl", "thox-kernel"]),
            ]
            for cloud, systemd, guest, exit_code, expected_trace in cases:
                with self.subTest(cloud=cloud, systemd=systemd, guest=guest):
                    trace.write_text("")
                    env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "TRACE": str(trace),
                           "CLOUD_EXIT": str(cloud), "SYSTEMD_EXIT": str(systemd), "GUEST_EXIT": str(guest)}
                    process = subprocess.run(["/bin/bash", "-c", script], env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(process.returncode, exit_code)
                    self.assertEqual(trace.read_text().splitlines(), expected_trace)

    def test_actual_readme_button_targets_this_public_portal_template(self):
        text = (ROOT / "README.md").read_text()
        pattern = r"\[!\[Deploy to Azure\]\(https://aka\.ms/deploytoazurebutton\)\]\((https://portal\.azure\.com/#create/Microsoft\.Template/uri/[^\s)]+)\)"
        buttons = re.findall(pattern, text)
        self.assertEqual(len(buttons), 1, "README must contain exactly one real Deploy to Azure button")
        raw = "https://raw.githubusercontent.com/Thox-ai/thoxinux-azure/main/portal/azuredeploy.json"
        self.assertEqual(unquote(buttons[0].split("/uri/", 1)[1]), raw)
        self.assertTrue((ROOT / "portal" / "azuredeploy.json").is_file())
        self.assertNotIn("token", buttons[0])


if __name__ == "__main__":
    unittest.main()
