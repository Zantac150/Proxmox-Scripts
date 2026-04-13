#!/usr/bin/env bash
# ==============================================================================
# backup-config.sh
# Back up Proxmox VE node configuration and optionally VM/CT configs to a
# local directory or a remote destination (rsync over SSH).
#
# What is backed up:
#   /etc/pve/            – All cluster/node config (including VM/CT configs)
#   /etc/network/        – Network interface configuration
#   /etc/hosts           – Hosts file
#   /etc/hostname        – Hostname
#   /root/.ssh/          – Root SSH keys (optional)
#
# Usage:
#   chmod +x backup-config.sh
#   sudo ./backup-config.sh [OPTIONS]
#
# Options:
#   -d, --dest     <PATH>          Local destination directory
#                                  (default: /var/backups/proxmox-config)
#   -r, --remote   <user@host:path> Remote rsync destination (optional)
#   -k, --keep     <N>             Number of local backup sets to keep (default: 7)
#       --no-ssh-keys              Exclude /root/.ssh from the backup
#       --no-compress              Skip tar compression (copy raw files)
#   -h, --help                     Show this help message
#
# Examples:
#   # Local backup, keep 14 days:
#   sudo ./backup-config.sh --dest /mnt/nas/proxmox-backups --keep 14
#
#   # Local + remote rsync:
#   sudo ./backup-config.sh --remote backup@192.168.1.10:/backups/proxmox
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
DEST_DIR="/var/backups/proxmox-config"
REMOTE_DEST=""
KEEP=7
BACKUP_SSH=true
COMPRESS=true

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Argument parsing ───────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -d|--dest)         DEST_DIR="$2"; shift 2 ;;
      -r|--remote)       REMOTE_DEST="$2"; shift 2 ;;
      -k|--keep)         KEEP="$2"; shift 2 ;;
         --no-ssh-keys)  BACKUP_SSH=false; shift ;;
         --no-compress)  COMPRESS=false; shift ;;
      -h|--help)
        grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
        exit 0 ;;
      *) error "Unknown argument: $1" ;;
    esac
  done
}

# ── Build list of source paths ─────────────────────────────────────────────────
backup_sources() {
  local sources=(
    /etc/pve
    /etc/network
    /etc/hosts
    /etc/hostname
    /etc/resolv.conf
    /etc/apt/sources.list
    /etc/apt/sources.list.d
  )
  $BACKUP_SSH && sources+=(/root/.ssh)
  echo "${sources[@]}"
}

# ── Perform backup ─────────────────────────────────────────────────────────────
do_backup() {
  local timestamp
  timestamp=$(date +%Y%m%d_%H%M%S)
  local hostname
  hostname=$(hostname -s)
  local backup_name="${hostname}_${timestamp}"
  local stage_dir="${DEST_DIR}/${backup_name}"

  mkdir -p "$stage_dir"

  info "Staging backup to $stage_dir ..."

  # Copy each source
  local -a sources
  read -ra sources <<< "$(backup_sources)"
  for src in "${sources[@]}"; do
    if [[ -e "$src" ]]; then
      cp -a "$src" "$stage_dir/"
      info "  Copied $src"
    else
      warn "  Not found (skipped): $src"
    fi
  done

  # Save pveversion output for reference
  if command -v pveversion &>/dev/null; then
    pveversion --verbose > "${stage_dir}/pveversion.txt" 2>&1 || true
  fi

  local final_path="$stage_dir"

  if $COMPRESS; then
    local tarball="${DEST_DIR}/${backup_name}.tar.gz"
    info "Compressing to $tarball ..."
    tar -czf "$tarball" -C "$DEST_DIR" "$backup_name"
    rm -rf "$stage_dir"
    final_path="$tarball"
    success "Backup created: $tarball"
  else
    success "Backup created: $stage_dir"
  fi

  echo "$final_path"
}

# ── Sync to remote ─────────────────────────────────────────────────────────────
sync_remote() {
  local src="$1"

  if [[ -z "$REMOTE_DEST" ]]; then
    return
  fi

  command -v rsync &>/dev/null || error "rsync is not installed."

  info "Syncing to remote: $REMOTE_DEST ..."
  rsync -az --progress "$src" "$REMOTE_DEST"
  success "Remote sync complete."
}

# ── Rotate old backups ─────────────────────────────────────────────────────────
rotate_backups() {
  info "Rotating old backups (keeping $KEEP most recent) ..."

  local count=0
  # List backup files/dirs newest-first, delete extras
  while IFS= read -r old; do
    (( count++ ))
    if (( count > KEEP )); then
      info "  Removing old backup: $old"
      rm -rf "$old"
    fi
  done < <(ls -1dt "${DEST_DIR}"/*)

  success "Rotation complete."
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Configuration Backup Script"
  echo "=================================================="
  echo -e "${NC}"

  [[ $EUID -eq 0 ]] || error "Must be run as root."
  parse_args "$@"
  mkdir -p "$DEST_DIR"

  local final_path
  final_path=$(do_backup)

  sync_remote "$final_path"
  rotate_backups

  echo ""
  success "Backup complete: $final_path"
}

main "$@"
