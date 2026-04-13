#!/usr/bin/env bash
# ==============================================================================
# clone-vm.sh
# Clone a Proxmox VM or template into one or more new VMs.
#
# Usage:
#   chmod +x clone-vm.sh
#   sudo ./clone-vm.sh [OPTIONS]
#
# Options:
#   -s, --source  <VMID>      Source VM / template ID  (required)
#   -i, --id      <VMID>      Starting ID for new VM(s) (required)
#   -n, --name    <NAME>      Base name for new VM(s)   (required)
#   -c, --count   <N>         Number of clones          (default: 1)
#       --cores   <N>         Override CPU cores        (optional)
#       --memory  <MB>        Override RAM in MiB       (optional)
#       --storage <STORAGE>   Target storage pool       (optional)
#       --start               Start VMs after cloning
#   -h, --help                Show this help message
#
# Example – clone template 9000 into 3 VMs starting at ID 101:
#   sudo ./clone-vm.sh -s 9000 -i 101 -n web-server -c 3 --start
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
SOURCE_ID=""
START_ID=""
BASE_NAME=""
COUNT=1
OVERRIDE_CORES=""
OVERRIDE_MEMORY=""
OVERRIDE_STORAGE=""
AUTO_START=false

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
      -s|--source)  SOURCE_ID="$2"; shift 2 ;;
      -i|--id)      START_ID="$2"; shift 2 ;;
      -n|--name)    BASE_NAME="$2"; shift 2 ;;
      -c|--count)   COUNT="$2"; shift 2 ;;
         --cores)   OVERRIDE_CORES="$2"; shift 2 ;;
         --memory)  OVERRIDE_MEMORY="$2"; shift 2 ;;
         --storage) OVERRIDE_STORAGE="$2"; shift 2 ;;
         --start)   AUTO_START=true; shift ;;
      -h|--help)    usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done

  [[ -n "$SOURCE_ID" ]] || error "--source is required."
  [[ -n "$START_ID"  ]] || error "--id is required."
  [[ -n "$BASE_NAME" ]] || error "--name is required."

  [[ "$COUNT" =~ ^[1-9][0-9]*$ ]] || error "--count must be a positive integer."
}

# ── Preflight checks ───────────────────────────────────────────────────────────
preflight() {
  [[ $EUID -eq 0 ]] || error "Must be run as root."
  command -v qm &>/dev/null || error "qm not found – is this a Proxmox host?"

  qm status "$SOURCE_ID" &>/dev/null \
    || error "Source VM/template $SOURCE_ID not found."

  # Check that target IDs are all free
  local id
  for (( idx=0; idx<COUNT; idx++ )); do
    id=$(( START_ID + idx ))
    if qm status "$id" &>/dev/null; then
      error "VM ID $id already exists. Choose a different --id or adjust --count."
    fi
  done
}

# ── Clone loop ─────────────────────────────────────────────────────────────────
do_clone() {
  local total_ok=0

  for (( idx=0; idx<COUNT; idx++ )); do
    local new_id=$(( START_ID + idx ))
    local new_name
    if [[ $COUNT -eq 1 ]]; then
      new_name="$BASE_NAME"
    else
      new_name="${BASE_NAME}-$(printf '%02d' $(( idx + 1 )))"
    fi

    info "Cloning $SOURCE_ID → $new_id ($new_name) ..."

    # Full clone (linked clones are faster but less portable)
    qm clone "$SOURCE_ID" "$new_id" --name "$new_name" --full 1

    # Apply any overrides
    local set_args=()
    [[ -n "$OVERRIDE_CORES"   ]] && set_args+=(--cores "$OVERRIDE_CORES")
    [[ -n "$OVERRIDE_MEMORY"  ]] && set_args+=(--memory "$OVERRIDE_MEMORY")

    if [[ ${#set_args[@]} -gt 0 ]]; then
      qm set "$new_id" "${set_args[@]}"
    fi

    success "VM $new_id ($new_name) created."

    if $AUTO_START; then
      info "Starting VM $new_id ..."
      qm start "$new_id"
      success "VM $new_id started."
    fi

    (( total_ok++ ))
  done

  echo ""
  success "Done. $total_ok VM(s) cloned from template $SOURCE_ID."
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – VM Clone Script"
  echo "=================================================="
  echo -e "${NC}"

  parse_args "$@"
  preflight
  do_clone
}

main "$@"
