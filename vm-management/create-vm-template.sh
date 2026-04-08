#!/usr/bin/env bash
# ==============================================================================
# create-vm-template.sh
# Download a cloud-init image and convert it into a reusable Proxmox VM template.
#
# Supported images (selectable via --os flag):
#   ubuntu22  – Ubuntu 22.04 LTS (Jammy)
#   ubuntu24  – Ubuntu 24.04 LTS (Noble)
#   debian12  – Debian 12 (Bookworm)
#   rocky9    – Rocky Linux 9
#
# Usage:
#   chmod +x create-vm-template.sh
#   sudo ./create-vm-template.sh [OPTIONS]
#
# Options:
#   -i, --id      <VMID>     VM ID for the template (default: 9000)
#   -n, --name    <NAME>     VM name (default: derived from OS choice)
#   -s, --storage <STORAGE>  Target storage pool (default: local-lvm)
#   -o, --os      <OS>       OS image to use (default: ubuntu22)
#       --cores   <N>        CPU cores (default: 2)
#       --memory  <MB>       RAM in MiB (default: 2048)
#       --ciuser  <USER>     Cloud-init default user (default: OS-specific)
#   -h, --help               Show this help message
#
# Example:
#   sudo ./create-vm-template.sh --id 9001 --os debian12 --storage local-lvm
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
VMID=9000
STORAGE="local-lvm"
CORES=2
MEMORY=2048
OS="ubuntu22"
VM_NAME=""
CIUSER=""
TMPDIR_WORK="/tmp/proxmox-vm-template"

# ── OS image definitions ───────────────────────────────────────────────────────
declare -A OS_URL OS_FILENAME OS_DEFAULT_NAME OS_DEFAULT_USER

OS_URL["ubuntu22"]="https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
OS_FILENAME["ubuntu22"]="jammy-server-cloudimg-amd64.img"
OS_DEFAULT_NAME["ubuntu22"]="ubuntu-22-04-template"
OS_DEFAULT_USER["ubuntu22"]="ubuntu"

OS_URL["ubuntu24"]="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
OS_FILENAME["ubuntu24"]="noble-server-cloudimg-amd64.img"
OS_DEFAULT_NAME["ubuntu24"]="ubuntu-24-04-template"
OS_DEFAULT_USER["ubuntu24"]="ubuntu"

OS_URL["debian12"]="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
OS_FILENAME["debian12"]="debian-12-genericcloud-amd64.qcow2"
OS_DEFAULT_NAME["debian12"]="debian-12-template"
OS_DEFAULT_USER["debian12"]="debian"

OS_URL["rocky9"]="https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2"
OS_FILENAME["rocky9"]="Rocky-9-GenericCloud-Base.latest.x86_64.qcow2"
OS_DEFAULT_NAME["rocky9"]="rocky-9-template"
OS_DEFAULT_USER["rocky9"]="rocky"

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
  exit 0
}

# ── Argument parsing ───────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -i|--id)      VMID="$2"; shift 2 ;;
      -n|--name)    VM_NAME="$2"; shift 2 ;;
      -s|--storage) STORAGE="$2"; shift 2 ;;
      -o|--os)      OS="$2"; shift 2 ;;
         --cores)   CORES="$2"; shift 2 ;;
         --memory)  MEMORY="$2"; shift 2 ;;
         --ciuser)  CIUSER="$2"; shift 2 ;;
      -h|--help)    usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done

  [[ -v "OS_URL[$OS]" ]] || error "Unknown OS: '$OS'. Valid options: ${!OS_URL[*]}"
  [[ -z "$VM_NAME" ]] && VM_NAME="${OS_DEFAULT_NAME[$OS]}"
  [[ -z "$CIUSER"  ]] && CIUSER="${OS_DEFAULT_USER[$OS]}"
}

# ── Preflight checks ───────────────────────────────────────────────────────────
preflight() {
  [[ $EUID -eq 0 ]] || error "Must be run as root."
  command -v qm &>/dev/null || error "qm not found – is this a Proxmox host?"
  command -v qemu-img &>/dev/null || error "qemu-img not found. Install qemu-utils."

  if qm status "$VMID" &>/dev/null; then
    error "VM ID $VMID already exists. Choose a different ID with --id."
  fi

  pvesm status --storage "$STORAGE" &>/dev/null \
    || error "Storage pool '$STORAGE' not found. Check pvesm status."
}

# ── Download image ─────────────────────────────────────────────────────────────
download_image() {
  local url="${OS_URL[$OS]}"
  local filename="${OS_FILENAME[$OS]}"
  local dest="${TMPDIR_WORK}/${filename}"

  mkdir -p "$TMPDIR_WORK"

  if [[ -f "$dest" ]]; then
    info "Image already cached: $dest"
  else
    info "Downloading $OS image from $url ..."
    wget -q --show-progress -O "$dest" "$url"
    success "Download complete."
  fi

  echo "$dest"
}

# ── Create template ────────────────────────────────────────────────────────────
create_template() {
  local image_path="$1"

  info "Creating VM $VMID ($VM_NAME) on storage $STORAGE ..."

  qm create "$VMID" \
    --name "$VM_NAME" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --net0 virtio,bridge=vmbr0 \
    --ostype l26 \
    --agent enabled=1 \
    --serial0 socket \
    --vga serial0

  info "Importing disk image..."
  qm importdisk "$VMID" "$image_path" "$STORAGE" --format qcow2

  # Determine disk name (storage:vm-VMID-disk-0)
  local disk="${STORAGE}:vm-${VMID}-disk-0"

  qm set "$VMID" \
    --scsihw virtio-scsi-pci \
    --scsi0 "${disk}" \
    --ide2 "${STORAGE}:cloudinit" \
    --boot c \
    --bootdisk scsi0 \
    --ipconfig0 ip=dhcp \
    --ciuser "$CIUSER"

  info "Converting VM to template..."
  qm template "$VMID"

  success "Template $VMID ($VM_NAME) created successfully!"
  info "Clone with:  qm clone $VMID <new-id> --name <new-name> --full 1"
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – VM Template Creator"
  echo "=================================================="
  echo -e "${NC}"

  parse_args "$@"
  preflight

  info "OS:      $OS"
  info "VMID:    $VMID"
  info "Name:    $VM_NAME"
  info "Storage: $STORAGE"
  info "Cores:   $CORES"
  info "Memory:  ${MEMORY} MiB"
  echo ""

  local image_path
  image_path=$(download_image)
  create_template "$image_path"
}

main "$@"
