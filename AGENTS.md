# AGENTS.md — ThoxInux on Azure

Guidance for any agent or contributor working in this repository.

## What this repository is

ThoxInux on Azure deploys an Azure VM that runs the real ThoxInux kernel
(ThoxOS Core) inside an unprivileged QEMU guest, with a supervised service
and an acceptance check that fails the deployment if kernel health fails.
It is an engineering preview. See `README.md` for the product description
and `docs/VALIDATION.md` for exactly what has and has not been proven.

## Working rules

- Verify before claiming. A workflow file is not a CI pass, a local QEMU
  run is not an Azure deployment, and a version file is not a release.
  Claims must name the evidence they rest on.
- Run the full local gate before pushing:
  `python3 -B -m unittest discover -s tests -v`,
  `python3 build_cloud_init.py --check`, and
  `python3 portal/build_portal.py --check`.
  Generated files (`azuredeploy.json`, `cloud-init.yaml`,
  `portal/azuredeploy.json`) must match their sources.
- Never fabricate evidence. No simulated Azure portal screenshots, no
  invented desktop or userspace captures. Previews are renderings of
  recorded runs and must be labelled as such.
- Preserve issued tags and release assets. Corrections ship as a new
  commit or a new version with an explicit change record; history and
  published results are not rewritten.
- Keep the public-package boundary: no credentials, private keys,
  subscription-specific plans, or internal operational material in this
  repository, its previews, or its release archives.
- Review the README and affected documentation with every change,
  including documentation-only changes, and check links, defaults, and
  known limits against the implementation.

## Handoff

Current state, remaining acceptance steps, and recovery instructions for
the next contributor live in `.agents/HANDOFF.md` and
`docs/DEVELOPMENT.md`. Keep the handoff current when the state changes.
