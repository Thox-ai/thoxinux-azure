# Development and verification

The deployment helpers use Python's standard library. Local kernel verification requires Linux x86-64, Python 3.11 or later, QEMU, OVMF and `flock` from util-linux. Azure CLI is required only for authenticated Azure operations.

## Source map

| Path | Role |
| --- | --- |
| `azuredeploy.json` | CLI deployment's Azure resource profile |
| `deploy.py` | Plan creation, ownership checks, validation, what-if, deployment and status |
| `payload/bootstrap.py` | Release verification and production service installation |
| `payload/guest_service.py` | Actual kernel supervisor, serial checks and control socket |
| `payload/thox-kernel` | Current status, log tail and noninteractive controller client |
| `payload/run-vm.sh` | Preserved upstream-derived THOX launcher |
| `payload/pins.json` | Exact release identities and asset hashes |
| `build_cloud_init.py` | Generates `cloud-init.yaml` from payload files |
| `portal/build_portal.py` | Generates the self-contained portal template and acceptance extension |
| `tools/verify_kernel.py` | Runs real local boot, lifecycle and recovery verification |
| `.github/workflows/verify.yml` | CI workflow for static and actual kernel checks |

## Check generated files and unit guards

Run from the repository root:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B build_cloud_init.py --check
python3 -B portal/build_portal.py --check
```

These checks perform no Azure deployment. Tests that simulate Azure responses or child processes are explicitly separated from the real kernel verifier. Keep that distinction when reporting results.

## Reproduce the actual kernel test

On Ubuntu, install the emulator packages:

```bash
sudo apt-get update
sudo apt-get install --yes --no-install-recommends qemu-system-x86 ovmf util-linux
```

Then run as an ordinary user, **without sudo**, and choose a new output directory:

```bash
python3 tools/verify_kernel.py --output validation-run
```

The verifier downloads the pinned public release, checks each asset and the manifest, and uses fresh temporary state for two real boots. The first boot performs the lifecycle checks; the second confirms persisted configuration recovery. Both guests must shut down cleanly. Existing output directories are refused so earlier evidence cannot be overwritten by an implicit rerun.

For already downloaded release assets, supply their directory:

```bash
python3 tools/verify_kernel.py \
  --release-dir /path/to/verified-release-files \
  --output validation-run-offline
```

The same size, hash and manifest checks still apply. Required downloaded files are `GINUX.EFI`, `build-receipt.json` and `SHA256SUMS`; attribution comes from the bundled payload. Review the resulting `summary.json`, per-boot results, receipts and serial log. A failed run must remain failed in its evidence.

## Change the deployment

Edit the source profile or payload, then regenerate in dependency order:

```bash
python3 build_cloud_init.py
python3 portal/build_portal.py
python3 -B -m unittest discover -s tests -v
python3 build_cloud_init.py --check
python3 portal/build_portal.py --check
```

Run the real kernel verifier when changes can affect the guest launcher, supervisor, release identity, firmware or serial protocol. Preserve fresh evidence with the source and harness hashes. Changes to Azure resources or cloud-init additionally require an authenticated Azure validation and deployment before claiming cloud readiness.

Treat kernel upgrades as explicit release changes. Update the reviewed asset manifest and all corresponding fixed identities in bootstrap, supervisor, status helper and verifier together. Regenerate cloud-init and the portal template. Do not relax the size, hash, identity or controller checks to make an unverified release pass.

Keep the current one-vCPU, 2 GiB guest profile until a separate boot test validates any change. Host sizing and guest memory are different settings. The production guest runs as `thoxinux`; package installation runs as root during cloud-init.

## Validate an Azure change

Use a new dedicated resource group and the intended authenticated subscription. The CLI route creates an exact plan, validates the ARM template, inspects what-if and checks ownership before VM creation. Existing plans are immutable; prepare a new plan when source or parameters change. The [CLI guide](CLI_DEPLOYMENT.md) provides the commands.

The portal route embeds cloud-init, resolves the latest regional Ubuntu 24.04 image and records its exact version. It has distinct portal tags and an initial acceptance extension. It does not inherit CLI ownership safeguards. See the [architecture comparison](ARCHITECTURE.md#two-deployment-entry-points) and [portal guide](../portal/README.md).

Save separate evidence for ARM validation, resource provisioning and the guest's actual acceptance. For live health, use the production status helper through Azure Run Command. Its current process and heartbeat checks are stronger than reading an old JSON receipt directly. [Microsoft Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command).

## CI and publication

The verification workflow runs on Ubuntu 24.04, uses action commit pins, checks generated files, boots the real kernel and uploads evidence. Its verification job needs only read access to repository contents. Release publication is a separate operation with explicitly scoped write permission. Neither operation provisions Azure or requires Azure credentials. Only a completed remote run supports a GitHub CI success claim.

The Deploy to Azure button must resolve to the published public raw `portal/azuredeploy.json`. Keep the README URL and repository destination consistent; a local template check cannot establish remote availability. Follow [PUBLISH.md](PUBLISH.md) for the exact repository publication procedure and verify the anonymous raw template after push.

Preserve the Apache-2.0 license and upstream notices when distributing the package. Current evidence and its limits belong in [VALIDATION.md](VALIDATION.md), with the raw receipts alongside it.

## Continuing implementation and acceptance

Start from the integration release's exact commit or the reviewed pull-request head, and record the full base and head SHAs with the change. The upstream kernel remains pinned to `fbad5952f20281ef6ad7eaefc1f54f7db4c0c6e9`; that kernel revision and this integration's own release commit are different identities. Check for existing work before editing and keep changes scoped to the relevant component.

The recorded local baseline has passed two real boots, eight controller checks, 22 lifecycle requests, persistent-state recovery and two clean shutdowns. It leaves the guest stopped. The production Azure/cloud-init/systemd path still needs its own execution evidence. A published source release does not close that gap.

| Continuing work | Responsible role | Required result |
| --- | --- | --- |
| Review a source or documentation change | Contributor and repository reviewer | Exact base/head, changed paths, documentation review, relevant test results and known limits. |
| Validate Azure deployment | Operator with permission in the intended subscription | A new dedicated resource group, ARM and provisioning results, exact host image, service state and current kernel acceptance. |
| Verify a release | Release maintainer and reviewer | Formal prerelease record, resolved tag commit, workflow result, packaged artifacts and matching downloaded checksums. |
| Change a kernel or launcher pin | Kernel maintainer and integration reviewer | Reviewed upstream bytes, updated fixed identities, regenerated payloads and a new real two-boot verification record. |

These roles describe responsibilities, not evidence that a person or external runtime has accepted or completed an assignment. Preserve unresolved failures and the commands needed to reproduce them in the pull request or release record.

Stop the affected operation when the expected source changes, a digest fails, a resource is not owned by the deployment, or acceptance fails. Inspect the actual state before retrying. Keep existing evidence and deployment plans intact. For recovery, use a verified prior source release in a new plan/resource group; never move an issued tag or repurpose an existing plan. Stopping the guest service does not stop Azure compute charges; deallocate the VM when the lab is idle. See the [user guide](USER_GUIDE.md) for lifecycle and storage consequences.

The [repository requirements map](REPOSITORY_STANDARD.md) identifies the public deliverables and evidence boundaries. Private operational pickup records stay outside this public source and its archives.
