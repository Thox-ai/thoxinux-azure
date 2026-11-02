# Build the public source package

Run from the repository root:

```bash
python3 tools/build_release.py
```

The packer writes three files under `dist/`:

- `thoxinux-azure-source.zip`
- `thoxinux-azure-source.manifest.json`
- `thoxinux-azure-source.zip.sha256`

Extract the ZIP and enter its `thoxinux-azure/` directory. The internal
`SHA256SUMS` covers every packaged source and evidence file, excluding itself.
The outer JSON manifest records the archive digest, byte count and complete
file inventory, including the internal checksum file.

```bash
cd dist
sha256sum -c thoxinux-azure-source.zip.sha256
unzip thoxinux-azure-source.zip
cd thoxinux-azure
sha256sum -c SHA256SUMS
```

## Exact public inventory

`tools/build_release.py` contains an explicit path allowlist. It includes the
deployment templates, public documentation, reviewed payload, helper tools,
named tests, verification workflow and known public validation records. Raw
kernel binaries remain available from the separately pinned upstream release.
Git history can be distributed as a separate Git bundle.

The packer never includes `.git/`, internal handoffs, unrelated private
directories, keys, local deployment plans, raw release binaries or Python bytecode.
Unexpected files inside a known public source root cause a failure. Add a new
public source path to the allowlist only after reviewing its content. Symlinked
source files and special files are refused.

Historical and new verification records retain their actual recorded bytes.
Packaging does not turn local QEMU tests into Azure deployment, production
systemd acceptance or a new kernel release. No Git tag or publication is created.

## Determinism and repeated builds

`.gitattributes` disables automatic line-ending conversion so hashes remain valid across Git checkouts, including the raw serial evidence.

ZIP entries use sorted paths, fixed timestamps, fixed Unix permissions and
`ZIP_STORED`. Identical input bytes therefore produce identical archive bytes
without depending on filesystem modification times or compression-library
versions. The source package is limited to 50 MiB of input data.

An identical existing output is verified and retained. A different existing
output is preserved and causes a failure. Use a fresh output directory for a
changed source snapshot:

```bash
python3 tools/build_release.py --output-dir dist/new-snapshot
```

The package manifest describes the bytes actually included. It does not assign
a version or substitute a Git commit identifier for an independently verified
source-history record.
