# Repository requirements and evidence

This page maps the public requirements for the ThoxInux Azure integration to its implementation, documentation and verification records. It describes this repository's delivery contract. It is not a certification of Azure deployment, operating-system readiness or organization-wide compliance.

## Public deliverables

| Requirement | Source or evidence | Acceptance boundary |
| --- | --- | --- |
| Purpose, audience and maturity | [README](../README.md) | Identify the kernel-development audience and engineering-preview status. Keep capabilities and exclusions consistent with observed results. |
| Repository identity | [GitHub repository](https://github.com/Thox-ai/thoxinux-azure) | The public description, owner, default branch and visibility must match the released integration. README copy alone does not establish GitHub metadata. |
| Accurate badges | [README](../README.md) and linked workflow/release pages | Each badge must identify its subject and link to supporting evidence. Distinguish integration version, upstream kernel version, supported host and actual CI status. |
| User onboarding | [Quick start](QUICKSTART.md), [user guide](USER_GUIDE.md), [portal guide](../portal/README.md) | Cover prerequisites, a new resource group, the public SSH key, normal operation, current health, recovery and costs. |
| Reproducible deployment | [Portal template](../portal/azuredeploy.json), [CLI guide](CLI_DEPLOYMENT.md) | Generated files must match their sources. Preserve the different image-selection and ownership behavior of the two deployment paths. |
| Architecture and boundaries | [Architecture](ARCHITECTURE.md), [security guidance](../SECURITY.md) | Describe the Ubuntu host, unprivileged QEMU guest, denied inbound traffic, management permissions and known kernel limits. |
| Development and continuing work | [Development](DEVELOPMENT.md), [contribution guide](../CONTRIBUTING.md) | Provide setup, source ownership, verification commands, configuration, remaining acceptance and recovery instructions. |
| Real headless evidence | [Validation](VALIDATION.md), [portable run](../validation/portable-qemu/summary.json) | Preserve the actual serial output, receipts, source identities, failures and claim limits. A generated image is not a new test run. |
| Readable preview | [Evidence preview](PREVIEW.md) | Explain the recorded controller workflow and architecture. Identify the source, environment and scope of every capture or evidence rendering. |
| License and attribution | [LICENSE](../LICENSE), [NOTICE](../NOTICE), [upstream attribution](../UPSTREAM-NOTICE.md) | Retain Apache-2.0 and upstream rights. Separate proprietary THOX products are outside this repository's license grant. |
| Formal integration release | [GitHub Releases](https://github.com/Thox-ai/thoxinux-azure/releases) | An issued version requires a published Release with its tag, resolved source commit, maturity, notes, artifacts and checksums verified. A tag or badge alone is insufficient. |
| Public source archive | [Packaging](PACKAGING.md) | Package only the reviewed public inventory. Verify the archive, its manifest and the extracted file checksums. |
| Support and change history | [Contributing](../CONTRIBUTING.md), [security guidance](../SECURITY.md), [changelog](../CHANGELOG.md) | Give usable reporting routes and preserve the distinction between proposed, released and independently verified changes. |

## What the evidence proves

The committed portable run proves **two real QEMU boots, eight controller acceptance checks, 22 lifecycle commands, persistent-state recovery and two clean shutdowns** with the pinned kernel. It ran as an ordinary user and finished with the guest stopped. Its receipts explicitly record that Azure deployment and the production cloud-init/systemd installation were not tested. [Validation and limits](VALIDATION.md) gives the exact identities and raw records.

The following states require separate evidence:

| State | Required observation |
| --- | --- |
| Source published | Read back the exact public commit and the anonymous raw deployment template. |
| CI passed | Inspect a completed workflow run for that commit and retain its actual test artifacts. A workflow file is only an implementation. |
| Integration released | Verify the formal Release, its tag-to-commit relationship, prerelease state and downloaded asset checksums. |
| Azure initialized | Record the actual deployment and acceptance-extension results in the intended subscription, with the exact host image version. |
| Kernel currently healthy | Query the running VM and require `accepted: true`, `running: true`, four successful checks and an active service. An old receipt is not sufficient. |

Use the linked GitHub workflow and release records for their current remote state. Neither a successful source release nor passing local CI establishes Azure acceptance.

## Headless preview requirements

This integration exposes a kernel controller through serial output and Azure Run Command. A useful preview therefore shows genuine controller responses and a readable explanation of the host, guest and acceptance sequence.

Keep these distinctions visible:

- A terminal capture shows the actual running surface or the recorded output being inspected. Its caption identifies which one it is.
- A chart, SVG or HTML rendering of committed receipts is an **evidence rendering**, with links to those receipts. It is not a screenshot of a deployed Azure VM.
- A workflow diagram explains the implementation. It does not prove that the depicted deployment ran.
- HTML and PDF previews must preserve the evidence labels and remain readable with working source links. Do not add a fabricated graphical desktop or Azure portal capture to make this headless product appear more complete.

## Review and release discipline

Review the README and affected documentation with every change, including documentation-only changes. Check commands, links, defaults, generated configuration, preview captions and known limits against the implementation. After committing, verify the committed bytes and retain a review record identifying the full commit, reviewed paths, hashes, reviewer, time and findings. Keep that receipt outside the commit it reviews.

An engineering-preview release remains a prerelease until its stated acceptance gates have evidence. Preserve issued tags and release assets. Corrections use a new commit or version with an explicit change record; they do not rewrite historical results. [Development](DEVELOPMENT.md) explains the verification sequence, and [publication](PUBLISH.md) explains the remote checks.

## Public-package boundary

Customer documentation, reproducible source and non-sensitive validation records belong in this repository. Credentials, private keys, subscription-specific plans, internal operational assignments and private review records do not belong in public source, previews or downloadable archives.

The source pack uses an explicit allowlist. Review every newly included path and inspect the resulting archive, not only the working directory. A successful packaging check establishes the checked inventory; it does not certify external delivery, private access controls or unrun deployment steps.
