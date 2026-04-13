#!/usr/bin/env bash
# ==============================================================================
# bulk-update.sh
# Update all running (or all) LXC containers and/or QEMU VMs on a Proxmox host.
#
# The script detects the package manager inside each container/VM and runs the
# appropriate update command (apt / dnf / yum / apk / pacman / zypper).
#
# Usage:
#   chmod +x bulk-update.sh
#   sudo ./bulk-update.sh [OPTIONS]
#
# Options:
#   --lxc-only       Update LXC containers only
#   --vm-only        Update QEMU VMs only (requires qemu-guest-agent)
#   --all            Update both LXC containers and QEMU VMs (default)
#   --include-stopped Also update stopped containers/VMs (starts them, updates,
#                    then stops them again)
#   --ids <LIST>     Comma-separated list of IDs to update (default: all)
#   --dry-run        Show what would be updated without making changes
#   -h, --help       Show this help message
#
# Examples:
#   sudo ./bulk-update.sh
#   sudo ./bulk-update.sh --lxc-only --ids 100,101,105
#   sudo ./bulk-update.sh --all --include-stopped
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
DO_LXC=true
DO_VM=true
INCLUDE_STOPPED=false
FILTER_IDS=""
DRY_RUN=false

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }

# ── Argument parsing ───────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lxc-only)        DO_VM=false; shift ;;
      --vm-only)         DO_LXC=false; shift ;;
      --all)             DO_LXC=true; DO_VM=true; shift ;;
      --include-stopped) INCLUDE_STOPPED=true; shift ;;
      --ids)             FILTER_IDS="$2"; shift 2 ;;
      --dry-run)         DRY_RUN=true; shift ;;
      -h|--help)
        grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
        exit 0 ;;
      *) error "Unknown argument: $1" ;;
    esac
  done
}

# ── Helpers ────────────────────────────────────────────────────────────────────
is_in_filter() {
  local id="$1"
  [[ -z "$FILTER_IDS" ]] && return 0
  IFS=',' read -ra arr <<< "$FILTER_IDS"
  for f in "${arr[@]}"; do
    [[ "$f" == "$id" ]] && return 0
  done
  return 1
}

run_or_dry() {
  if $DRY_RUN; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
  else
    eval "$@"
  fi
}

# ── Detect package manager and build update command ───────────────────────────
update_cmd_for() {
  # $1 = command runner prefix (e.g. "pct exec ID --")
  local runner="$1"

  # Walk through known package managers
  for pm in apt-get dnf yum apk pacman zypper; do
    if $runner command -v "$pm" &>/dev/null 2>&1; then
      case "$pm" in
        apt-get) echo "DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get -y full-upgrade && apt-get -y autoremove" ;;
        dnf)     echo "dnf -y upgrade" ;;
        yum)     echo "yum -y update" ;;
        apk)     echo "apk update && apk upgrade" ;;
        pacman)  echo "pacman -Syu --noconfirm" ;;
        zypper)  echo "zypper -n update" ;;
      esac
      return 0
    fi
  done

  echo ""
  return 1
}

# ── Update a single LXC container ─────────────────────────────────────────────
update_lxc() {
  local ctid="$1"
  local ct_name
  ct_name=$(pct config "$ctid" | grep '^hostname:' | awk '{print $2}' || echo "ct-$ctid")
  local status
  status=$(pct status "$ctid" | awk '{print $2}')

  header "LXC $ctid ($ct_name) – status: $status"

  local was_stopped=false
  if [[ "$status" != "running" ]]; then
    if ! $INCLUDE_STOPPED; then
      warn "Skipping (stopped). Use --include-stopped to update stopped containers."
      return 1
    fi
    info "Starting container $ctid ..."
    run_or_dry "pct start $ctid"
    was_stopped=true
    # Give it a moment to boot
    sleep 3
  fi

  local runner="pct exec $ctid --"
  local cmd
  if ! cmd=$(update_cmd_for "$runner"); then
    warn "Could not detect package manager in container $ctid – skipping."
    if $was_stopped; then
      run_or_dry "pct stop $ctid"
    fi
    return 1
  fi

  info "Running: $cmd"
  run_or_dry "pct exec $ctid -- bash -c \"$cmd\""
  success "Container $ctid updated."

  if $was_stopped; then
    info "Stopping container $ctid (was stopped before update) ..."
    run_or_dry "pct stop $ctid"
  fi
}

# ── Update a single QEMU VM ───────────────────────────────────────────────────
update_vm() {
  local vmid="$1"
  local vm_name
  vm_name=$(qm config "$vmid" | grep '^name:' | awk '{print $2}' || echo "vm-$vmid")
  local status
  status=$(qm status "$vmid" | awk '{print $2}')

  header "VM $vmid ($vm_name) – status: $status"

  if [[ "$status" != "running" ]]; then
    if ! $INCLUDE_STOPPED; then
      warn "Skipping (stopped). Use --include-stopped to update stopped VMs."
      return 1
    fi
    info "Starting VM $vmid ..."
    run_or_dry "qm start $vmid"
    info "Waiting for guest agent..."
    sleep 15
  fi

  # Requires qemu-guest-agent inside the VM
  if ! qm agent "$vmid" ping &>/dev/null 2>&1; then
    warn "Guest agent not responding in VM $vmid – skipping."
    warn "Ensure qemu-guest-agent is installed and running inside the VM."
    return 1
  fi

  local runner="qm guest exec $vmid --"
  local cmd
  if ! cmd=$(update_cmd_for "$runner"); then
    warn "Could not detect package manager in VM $vmid – skipping."
    return 1
  fi

  info "Running: $cmd"
  run_or_dry "qm guest exec $vmid -- bash -c \"$cmd\""
  success "VM $vmid updated."
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Bulk Update Script"
  echo "=================================================="
  echo -e "${NC}"

  [[ $EUID -eq 0 ]] || error "Must be run as root."
  command -v pct &>/dev/null || error "pct not found – is this a Proxmox host?"
  parse_args "$@"

  $DRY_RUN && warn "DRY-RUN mode enabled – no changes will be made."

  local ok=0 skipped=0

  if $DO_LXC; then
    info "Processing LXC containers..."
    while IFS= read -r line; do
      local ctid
      ctid=$(echo "$line" | awk '{print $1}')
      is_in_filter "$ctid" || continue
      update_lxc "$ctid" && (( ok++ )) || (( skipped++ ))
    done < <(pct list | tail -n +2)
  fi

  if $DO_VM; then
    info "Processing QEMU VMs..."
    while IFS= read -r line; do
      local vmid
      vmid=$(echo "$line" | awk '{print $1}')
      is_in_filter "$vmid" || continue
      update_vm "$vmid" && (( ok++ )) || (( skipped++ ))
    done < <(qm list | tail -n +2)
  fi

  echo ""
  success "Bulk update complete. Updated: $ok | Skipped: $skipped"
}

main "$@"
