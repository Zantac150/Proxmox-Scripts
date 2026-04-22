#!/usr/bin/env bash
# ==============================================================================
# recover-services.sh
# Detect and recover unhealthy Proxmox-related systemd services.
#
# What it does:
#   - Checks active/substate for selected services
#   - Attempts restart when service is unhealthy
#   - Retries restart attempts (configurable)
#   - Supports dry-run mode for safe validation
#
# Usage:
#   chmod +x recover-services.sh
#   sudo ./recover-services.sh [OPTIONS]
#
# Options:
#       --services <LIST>          Comma-separated services to check
#                                  (default: pveproxy,pvedaemon,pvestatd,pve-cluster)
#       --max-retries <N>          Restart attempts per unhealthy service (default: 2)
#       --dry-run                  Print actions only, do not restart
#       --check-only               Detect unhealthy services without restarting
#   -h, --help                     Show this help message
# ==============================================================================

set -euo pipefail

SERVICE_LIST="pveproxy,pvedaemon,pvestatd,pve-cluster"
MAX_RETRIES=2
DRY_RUN=false
CHECK_ONLY=false

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

UNHEALTHY=0
RECOVERED=0
FAILED_RECOVERY=0

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --services)    SERVICE_LIST="$2"; shift 2 ;;
      --max-retries) MAX_RETRIES="$2"; shift 2 ;;
      --dry-run)     DRY_RUN=true; shift ;;
      --check-only)  CHECK_ONLY=true; shift ;;
      -h|--help)     usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done

  [[ "$MAX_RETRIES" =~ ^[1-9][0-9]*$ ]] || error "--max-retries must be a positive integer."
}

service_state() {
  local svc="$1"
  local active sub
  active=$(systemctl is-active "$svc" 2>/dev/null || true)
  sub=$(systemctl show -p SubState --value "$svc" 2>/dev/null || echo "unknown")
  echo "${active}/${sub}"
}

is_healthy() {
  local state="$1"
  [[ "$state" == active/* ]] && [[ "$state" != */failed ]]
}

restart_service() {
  local svc="$1"

  if $DRY_RUN; then
    info "[DRY-RUN] Would restart ${svc}"
    return 0
  fi

  systemctl restart "$svc"
}

process_service() {
  local svc="$1"

  if ! systemctl list-unit-files | awk '{print $1}' | grep -qx "${svc}.service"; then
    warn "${svc} is not installed on this host, skipping."
    return 0
  fi

  local state
  state=$(service_state "$svc")

  if is_healthy "$state"; then
    ok "${svc} healthy (${state})"
    return 0
  fi

  (( UNHEALTHY++ )) || true
  warn "${svc} unhealthy (${state})"

  if $CHECK_ONLY; then
    return 1
  fi

  local attempt
  for (( attempt=1; attempt<=MAX_RETRIES; attempt++ )); do
    info "Restart attempt ${attempt}/${MAX_RETRIES} for ${svc} ..."
    if restart_service "$svc"; then
      sleep 2
      state=$(service_state "$svc")
      if is_healthy "$state"; then
        (( RECOVERED++ )) || true
        ok "${svc} recovered (${state})"
        return 0
      fi
    fi
    warn "${svc} still unhealthy after attempt ${attempt} (${state})"
  done

  (( FAILED_RECOVERY++ )) || true
  fail "${svc} could not be recovered automatically."
  return 1
}

main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Service Recovery"
  echo "=================================================="
  echo -e "${NC}"

  [[ $EUID -eq 0 ]] || error "Must be run as root."
  parse_args "$@"

  IFS=',' read -ra services <<< "$SERVICE_LIST"

  local svc
  local rc=0
  for svc in "${services[@]}"; do
    svc="${svc// /}"
    [[ -n "$svc" ]] || continue
    process_service "$svc" || rc=1
  done

  echo ""
  info "Summary: unhealthy=${UNHEALTHY}, recovered=${RECOVERED}, failed-recovery=${FAILED_RECOVERY}"

  if (( FAILED_RECOVERY > 0 )); then
    error "One or more services failed to recover."
  fi

  if (( UNHEALTHY > 0 )) && $CHECK_ONLY; then
    warn "Unhealthy services detected."
    exit 1
  fi

  if (( UNHEALTHY > 0 )) && $DRY_RUN; then
    warn "Unhealthy services detected (dry-run mode)."
    exit 1
  fi

  (( rc == 0 )) && ok "Service recovery completed successfully."
  exit "$rc"
}

main "$@"
