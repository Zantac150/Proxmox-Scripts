#!/usr/bin/env bash
# ==============================================================================
# create-lxc.sh
# Create a Proxmox LXC container from a template.
#
# Usage:
#   chmod +x create-lxc.sh
#   sudo ./create-lxc.sh [OPTIONS]
#
# Options:
#   -i, --id        <CTID>      Container ID                  (required)
#   -n, --name      <NAME>      Hostname                      (required)
#   -t, --template  <TEMPLATE>  Template string or path       (default: ubuntu-22.04-standard)
#       --storage   <STORAGE>   Root FS storage pool          (default: local-lvm)
#       --disk      <GB>        Root disk size in GB          (default: 8)
#       --cores     <N>         CPU cores                     (default: 1)
#       --memory    <MB>        RAM in MiB                    (default: 512)
#       --swap      <MB>        Swap in MiB                   (default: 512)
#       --net-bridge <BR>       Network bridge                (default: vmbr0)
#       --ip        <CIDR>      Static IP in CIDR notation    (default: dhcp)
#       --gw        <GW>        Default gateway               (optional, required for static IP)
#       --password  <PASS>      Root password                 (default: prompted)
#       --unprivileged          Create unprivileged container (default: true)
#       --start                 Start container after creation
#   -h, --help                  Show this help message
#
# Examples:
#   # DHCP, unprivileged, default sizes:
#   sudo ./create-lxc.sh --id 200 --name my-container
#
#   # Static IP, 2 cores, 1 GB RAM, start immediately:
#   sudo ./create-lxc.sh --id 201 --name nginx --ip 192.168.1.50/24 --gw 192.168.1.1 \
#        --cores 2 --memory 1024 --start
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
CTID=""
CT_NAME=""
TEMPLATE="ubuntu-22.04-standard"
STORAGE="local-lvm"
DISK_SIZE=8
CORES=1
MEMORY=512
SWAP=512
NET_BRIDGE="vmbr0"
CT_IP="dhcp"
CT_GW=""
CT_PASSWORD=""
UNPRIVILEGED=1
AUTO_START=false
TEMPLATE_STORAGE="local"

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
      -i|--id)           CTID="$2"; shift 2 ;;
      -n|--name)         CT_NAME="$2"; shift 2 ;;
      -t|--template)     TEMPLATE="$2"; shift 2 ;;
         --storage)      STORAGE="$2"; shift 2 ;;
         --disk)         DISK_SIZE="$2"; shift 2 ;;
         --cores)        CORES="$2"; shift 2 ;;
         --memory)       MEMORY="$2"; shift 2 ;;
         --swap)         SWAP="$2"; shift 2 ;;
         --net-bridge)   NET_BRIDGE="$2"; shift 2 ;;
         --ip)           CT_IP="$2"; shift 2 ;;
         --gw)           CT_GW="$2"; shift 2 ;;
         --password)     CT_PASSWORD="$2"; shift 2 ;;
         --unprivileged) UNPRIVILEGED=1; shift ;;
         --privileged)   UNPRIVILEGED=0; shift ;;
         --start)        AUTO_START=true; shift ;;
      -h|--help)         usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done

  [[ -n "$CTID"    ]] || error "--id is required."
  [[ -n "$CT_NAME" ]] || error "--name is required."

  if [[ "$CT_IP" != "dhcp" && -z "$CT_GW" ]]; then
    error "A --gw (gateway) is required when using a static IP."
  fi
}

# ── Resolve template ───────────────────────────────────────────────────────────
resolve_template() {
  # If the template looks like a full path / tarball, use it directly
  if [[ "$TEMPLATE" == *"/"* || "$TEMPLATE" == *.tar* ]]; then
    echo "$TEMPLATE"
    return
  fi

  # Search pveam cache
  local cached
  cached=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null \
    | awk '{print $1}' \
    | grep -i "$TEMPLATE" \
    | head -1) || true

  if [[ -n "$cached" ]]; then
    echo "$cached"
    return
  fi

  # Try to download from pveam
  info "Template not cached. Searching online..."
  local available
  available=$(pveam available --section system 2>/dev/null \
    | awk '{print $2}' \
    | grep -i "$TEMPLATE" \
    | head -1) || true

  if [[ -z "$available" ]]; then
    error "Template '$TEMPLATE' not found locally or in pveam catalog."
  fi

  info "Downloading template: $available ..."
  pveam download "$TEMPLATE_STORAGE" "$available"
  echo "${TEMPLATE_STORAGE}:vztmpl/${available}"
}

# ── Preflight checks ───────────────────────────────────────────────────────────
preflight() {
  [[ $EUID -eq 0 ]] || error "Must be run as root."
  command -v pct &>/dev/null || error "pct not found – is this a Proxmox host?"

  if pct status "$CTID" &>/dev/null; then
    error "Container ID $CTID already exists. Choose a different --id."
  fi
}

# ── Prompt for password if not provided ───────────────────────────────────────
get_password() {
  if [[ -z "$CT_PASSWORD" ]]; then
    read -rsp "Enter root password for container $CTID: " CT_PASSWORD
    echo ""
    read -rsp "Confirm password: " CT_PASSWORD_CONFIRM
    echo ""
    [[ "$CT_PASSWORD" == "$CT_PASSWORD_CONFIRM" ]] || error "Passwords do not match."
  fi
}

# ── Create container ───────────────────────────────────────────────────────────
create_container() {
  local tmpl
  tmpl=$(resolve_template)

  info "Creating LXC container $CTID ($CT_NAME) from $tmpl ..."

  # Build network config string
  local net_config="name=eth0,bridge=${NET_BRIDGE}"
  if [[ "$CT_IP" == "dhcp" ]]; then
    net_config+=",ip=dhcp"
  else
    net_config+=",ip=${CT_IP},gw=${CT_GW}"
  fi

  pct create "$CTID" "$tmpl" \
    --hostname "$CT_NAME" \
    --storage "$STORAGE" \
    --rootfs "${STORAGE}:${DISK_SIZE}" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --swap "$SWAP" \
    --net0 "$net_config" \
    --unprivileged "$UNPRIVILEGED" \
    --features nesting=1 \
    --password "$CT_PASSWORD" \
    --onboot 1

  success "Container $CTID ($CT_NAME) created."

  if $AUTO_START; then
    info "Starting container $CTID ..."
    pct start "$CTID"
    success "Container $CTID started."
  fi
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – LXC Container Creator"
  echo "=================================================="
  echo -e "${NC}"

  parse_args "$@"
  preflight
  get_password
  create_container
}

main "$@"
