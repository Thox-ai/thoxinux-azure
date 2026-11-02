#!/usr/bin/env bash
# THOX.ai LLC: QEMU launcher for the pinned Ginux research guest.
# No host devices, shared home directories, inbound ports, or GPU passthrough.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
artifact_dir="${GINUX_ARTIFACT_DIR:-$script_dir/../artifacts}"
state_dir="${GINUX_STATE_DIR:-${XDG_STATE_HOME:-${HOME:?HOME is required}/.local/state}/thox/ginux}"
print_command=0
while (($#)); do
    case "$1" in
        --artifact-dir|--state-dir)
            (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
            if [[ "$1" == --artifact-dir ]]; then artifact_dir="$2"; else state_dir="$2"; fi
            shift 2;;
        --print-command) print_command=1; shift;;
        --help)
            echo "Usage: run-vm.sh [--artifact-dir DIR] [--state-dir DIR] [--print-command]"
            echo "QEMU_ACCEL=tcg (default) or kvm; GINUX_MEMORY=2G (default, maximum)."
            exit 0;;
        *) echo "Unknown argument: $1" >&2; exit 2;;
    esac
done
accelerator="${QEMU_ACCEL:-tcg}"
memory="${GINUX_MEMORY:-2G}"
[[ "$accelerator" == tcg || "$accelerator" == kvm ]] || { echo "QEMU_ACCEL must be tcg or kvm" >&2; exit 2; }
[[ "$memory" =~ ^([1-9][0-9]*)([MG])$ ]] || { echo "GINUX_MEMORY must be a positive M/G value, for example 2G" >&2; exit 2; }
memory_amount="${BASH_REMATCH[1]}"
memory_unit="${BASH_REMATCH[2]}"
# The reviewed EFI boot path uses a low identity map. OVMF places this EFI image
# above it at 4G; retain the tested 2G maximum until that upstream limit is fixed.
if ((${#memory_amount} > 4)) ||
    [[ "$memory_unit" == G && "$memory_amount" -gt 2 ]] ||
    [[ "$memory_unit" == M && "$memory_amount" -gt 2048 ]]; then
    echo "This research profile supports at most 2G/2048M guest RAM; larger EFI boots are unvalidated." >&2
    exit 2
fi
for path in "$artifact_dir" "$state_dir"; do
    [[ "$path" != *','* && "$path" != *$'\n'* ]] || { echo "QEMU paths cannot contain commas or newlines" >&2; exit 2; }
done
artifact_dir="$(realpath "$artifact_dir")"
[[ -s "$artifact_dir/GINUX.EFI" && -s "$artifact_dir/SHA256SUMS" ]] || {
    echo "Missing built artifacts. Build or export the EFI first." >&2; exit 1;
}
declare -A checksum_entries=()
checksum_pattern='^([a-f0-9]{64})  (GINUX\.EFI|build-receipt\.json|GINUX_LICENSE|GINUX_NOTICE)$'
while IFS= read -r checksum_line || [[ -n "$checksum_line" ]]; do
    [[ "$checksum_line" =~ $checksum_pattern ]] || {
        echo "Checksum manifest contains an unexpected or unsafe entry." >&2; exit 1;
    }
    checksum_name="${BASH_REMATCH[2]}"
    [[ -z "${checksum_entries[$checksum_name]:-}" ]] || {
        echo "Checksum manifest contains a duplicate entry." >&2; exit 1;
    }
    checksum_entries[$checksum_name]=1
done < "$artifact_dir/SHA256SUMS"
for checksum_name in GINUX.EFI build-receipt.json GINUX_LICENSE GINUX_NOTICE; do
    [[ -n "${checksum_entries[$checksum_name]:-}" && -f "$artifact_dir/$checksum_name" && ! -L "$artifact_dir/$checksum_name" ]] || {
        echo "Checksum manifest must cover each regular payload file, including $checksum_name." >&2; exit 1;
    }
done
(cd "$artifact_dir" && sha256sum --strict -c SHA256SUMS) >&2
if [[ "$accelerator" == kvm && (! -r /dev/kvm || ! -w /dev/kvm) ]]; then
    echo "KVM requested but /dev/kvm is not accessible. Use TCG or configure host KVM access." >&2
    exit 1
fi
qemu="${QEMU_SYSTEM_X86_64:-qemu-system-x86_64}"
command -v "$qemu" >/dev/null || { echo "Install qemu-system-x86" >&2; exit 1; }
ovmf_code="${OVMF_CODE:-}"
ovmf_vars_template="${OVMF_VARS:-}"
if [[ -z "$ovmf_code" && -z "$ovmf_vars_template" ]]; then
    # Select a matching CODE/VARS pair, including Ubuntu 24.04's 4M package.
    for firmware_dir in /usr/share/OVMF /usr/share/edk2/x64; do
        for suffix in '' '_4M'; do
            if [[ -f "$firmware_dir/OVMF_CODE${suffix}.fd" && -f "$firmware_dir/OVMF_VARS${suffix}.fd" ]]; then
                ovmf_code="$firmware_dir/OVMF_CODE${suffix}.fd"
                ovmf_vars_template="$firmware_dir/OVMF_VARS${suffix}.fd"
                break 2
            fi
        done
    done
fi
[[ -f "$ovmf_code" && -f "$ovmf_vars_template" ]] || {
    echo "Install ovmf or set both OVMF_CODE and OVMF_VARS to a matching pair." >&2; exit 1;
}
for path in "$ovmf_code" "$ovmf_vars_template"; do
    [[ "$path" != *','* && "$path" != *$'\n'* ]] || { echo "Invalid firmware path" >&2; exit 2; }
done
cpu_model='max,+smep,+smap,+umip'
[[ "$accelerator" == tcg ]] || cpu_model='host,+smep,+smap,+umip'
state_dir="$(realpath -m "$state_dir")"
for path in "$artifact_dir" "$state_dir"; do
    [[ "$path" != *','* && "$path" != *$'\n'* ]] || { echo "Resolved QEMU paths cannot contain commas or newlines" >&2; exit 2; }
done
args=(
    -machine "q35,accel=$accelerator" -cpu "$cpu_model" -smp 1 -m "$memory"
    -display none -monitor none -serial stdio -no-reboot -net none
    -device 'intel-iommu,intremap=off,aw-bits=48'
    -device 'virtio-blk-pci,drive=ginux-volume,disable-legacy=on,iommu_platform=on,serial=ginux-volume'
    -netdev 'user,id=ginux-net,restrict=off'
    -device 'virtio-net-pci,netdev=ginux-net,disable-legacy=on,iommu_platform=on,mac=52:54:00:12:34:56,addr=0x3'
    -drive "if=none,id=ginux-volume,format=raw,file=$state_dir/volume.raw"
    -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code"
    -drive "if=pflash,format=raw,file=$state_dir/ovmf-vars.fd"
    -drive "if=none,id=ginux-esp,format=raw,file=fat:rw:$state_dir/esp"
    -device 'ide-hd,drive=ginux-esp,bootindex=1'
)
if ((print_command)); then
    printf '%q ' "$qemu" "${args[@]}"; printf '\n'; exit 0
fi
[[ "$EUID" -ne 0 ]] || { echo "Run the guest as a regular user, not root." >&2; exit 1; }
command -v flock >/dev/null || { echo "Install util-linux (flock)" >&2; exit 1; }
mkdir -p "$state_dir/esp/EFI/BOOT"
chmod 700 "$state_dir"
exec 9>"$state_dir/launch.lock"
flock -n 9 || { echo "This guest state is already in use." >&2; exit 1; }
cp "$artifact_dir/GINUX.EFI" "$state_dir/esp/EFI/BOOT/BOOTX64.EFI"
# Match upstream's fresh firmware-variable copy per boot. The persistent Ginux
# volume is separate and never reset or shrunk by this launcher.
cp "$ovmf_vars_template" "$state_dir/ovmf-vars.fd"
if [[ -e "$state_dir/volume.raw" ]]; then
    [[ -f "$state_dir/volume.raw" && ! -L "$state_dir/volume.raw" ]] || {
        echo "Refusing non-regular or symlink volume." >&2; exit 1;
    }
else
    truncate -s 64G "$state_dir/volume.raw"
fi
echo "ThoxOS Ginux Research Edition: QEMU/$accelerator, one vCPU, $memory RAM." >&2
echo "No graphical desktop. Ginux native physical hardware and NVIDIA support are unvalidated." >&2
exec "$qemu" "${args[@]}"
