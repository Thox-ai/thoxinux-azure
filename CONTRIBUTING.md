# Contributing

Use a branch and a pull request. Explain the problem, the resulting behavior, and the validation actually performed. Keep changes to the kernel or canonical launcher in their source repository; this integration pins reviewed upstream bytes.

Run the unit suite and both generated-file checks from the README. A change to the service, launcher pin, release identity or boot acceptance also requires the real local QEMU verifier. Azure resource changes require a new dedicated test resource group and authenticated ARM validation before claiming live Azure acceptance.

Regenerate `cloud-init.yaml` and then `portal/azuredeploy.json` after payload changes. Do not edit an immutable CLI deployment plan. Do not relax hash checks, guest memory bounds, unprivileged execution, controller transport limits, ownership guards or denied inbound access merely to make a test pass.

Keep documentation and validation limits current. Preserve raw evidence, including failed runs, and label mocks or fixtures. Never place credentials, private keys, subscription identifiers, deployment plans or private operational handoffs in this public repository. Keep upstream copyright and license files unchanged unless the upstream release itself changes.

Contributions are provided under this repository's Apache-2.0 license. This project does not grant rights to separate proprietary THOX technologies.
