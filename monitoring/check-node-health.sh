#!/usr/bin/env bash
# ==============================================================================
# check-node-health.sh
# Node-level observability checks for Proxmox VE hosts.
#
# Checks:
#   - Filesystem and inode utilization
#   - Memory/swap usage
#   - CPU load pressure
#   - Proxmox storage pool status
#   - Core Proxmox service health (optional restart)
#   - Optional ZFS pool and cluster quorum checks
#
# Usage:
#   chmod +x check-node-health.sh
#   sudo ./check-node-health.sh [OPTIONS]
#
# Options:
#       --disk-threshold <PCT>      Disk usage warning threshold (default: 85)
#       --inode-threshold <PCT>     Inode usage warning threshold (default: 85)
#       --memory-threshold <PCT>    Memory usage warning threshold (default: 90)
#       --swap-threshold <PCT>      Swap usage warning threshold (default: 50)
#       --load-factor <FLOAT>       Warn if load1 > cores*factor (default: 1.50)
#       --restart-unhealthy         Restart unhealthy core Proxmox services
#       --skip-storage              Skip pvesm storage checks
#       --skip-zfs                  Skip ZFS pool checks
#       --skip-cluster              Skip cluster quorum checks
#   -h, --help                      Show this help message
# ==============================================================================

set -euo pipefail

DISK_THRESHOLD=85
INODE_THRESHOLD=85
MEMORY_THRESHOLD=90
SWAP_THRESHOLD=50
LOAD_FACTOR=1.50
RESTART_UNHEALTHY=false
SKIP_STORAGE=false
SKIP_ZFS=false
SKIP_CLUSTER=false

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

WARNINGS=0
FAILURES=0

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --disk-threshold)      DISK_THRESHOLD="$2"; shift 2 ;;
      --inode-threshold)     INODE_THRESHOLD="$2"; shift 2 ;;
      --memory-threshold)    MEMORY_THRESHOLD="$2"; shift 2 ;;
      --swap-threshold)      SWAP_THRESHOLD="$2"; shift 2 ;;
      --load-factor)         LOAD_FACTOR="$2"; shift 2 ;;
      --restart-unhealthy)   RESTART_UNHEALTHY=true; shift ;;
      --skip-storage)        SKIP_STORAGE=true; shift ;;
      --skip-zfs)            SKIP_ZFS=true; shift ;;
      --skip-cluster)        SKIP_CLUSTER=true; shift ;;
      -h|--help)             usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done
}

validate_number() {
  local value="$1" label="$2"
  [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || error "$label must be numeric."
}

inc_warn() {
  (( WARNINGS++ )) || true
  warn "$*"
}

inc_fail() {
  (( FAILURES++ )) || true
  fail "$*"
}

check_disk_usage() {
  info "Checking disk usage..."
  while IFS= read -r line; do
    local fs use mount
    fs=$(awk '{print $1}' <<< "$line")
    use=$(awk '{print $5}' <<< "$line" | tr -d '%')
    mount=$(awk '{print $6}' <<< "$line")

    if (( use >= DISK_THRESHOLD )); then
      inc_warn "Disk usage ${use}% on ${mount} (${fs})"
    else
      ok "Disk usage ${use}% on ${mount}"
    fi
  done < <(df -P -x tmpfs -x devtmpfs | tail -n +2)
}

check_inode_usage() {
  info "Checking inode usage..."
  while IFS= read -r line; do
    local fs use mount
    fs=$(awk '{print $1}' <<< "$line")
    use=$(awk '{print $5}' <<< "$line" | tr -d '%')
    mount=$(awk '{print $6}' <<< "$line")

    if (( use >= INODE_THRESHOLD )); then
      inc_warn "Inode usage ${use}% on ${mount} (${fs})"
    else
      ok "Inode usage ${use}% on ${mount}"
    fi
  done < <(df -Pi -x tmpfs -x devtmpfs | tail -n +2)
}

check_memory_and_load() {
  info "Checking memory/swap/load..."

  local mem_total mem_used mem_pct swap_total swap_used swap_pct
  mem_total=$(free -m | awk '/^Mem:/ {print $2}')
  mem_used=$(free -m | awk '/^Mem:/ {print $3}')
  mem_pct=$(( mem_total > 0 ? (100 * mem_used / mem_total) : 0 ))

  swap_total=$(free -m | awk '/^Swap:/ {print $2}')
  swap_used=$(free -m | awk '/^Swap:/ {print $3}')
  swap_pct=$(( swap_total > 0 ? (100 * swap_used / swap_total) : 0 ))

  local cores load1 limit
  cores=$(nproc)
  load1=$(awk '{print $1}' /proc/loadavg)
  limit=$(awk -v c="$cores" -v f="$LOAD_FACTOR" 'BEGIN { printf "%.2f", c*f }')

  if (( mem_pct >= MEMORY_THRESHOLD )); then
    inc_warn "Memory usage ${mem_pct}% (${mem_used}/${mem_total} MiB)"
  else
    ok "Memory usage ${mem_pct}% (${mem_used}/${mem_total} MiB)"
  fi

  if (( swap_total > 0 )); then
    if (( swap_pct >= SWAP_THRESHOLD )); then
      inc_warn "Swap usage ${swap_pct}% (${swap_used}/${swap_total} MiB)"
    else
      ok "Swap usage ${swap_pct}% (${swap_used}/${swap_total} MiB)"
    fi
  else
    info "Swap not configured; skipping swap threshold check."
  fi

  if awk -v l="$load1" -v m="$limit" 'BEGIN { exit !(l>m) }'; then
    inc_warn "Load1=${load1} exceeds threshold ${limit} (cores=${cores}, factor=${LOAD_FACTOR})"
  else
    ok "Load1=${load1} within threshold ${limit}"
  fi
}

check_storage_pools() {
  $SKIP_STORAGE && return

  info "Checking Proxmox storage pools..."
  if ! command -v pvesm &>/dev/null; then
    inc_fail "pvesm not found; cannot check storage pools."
    return
  fi

  while IFS= read -r line; do
    local name status total avail used pct
    name=$(awk '{print $1}' <<< "$line")
    status=$(awk '{print $2}' <<< "$line")
    total=$(awk '{print $5}' <<< "$line")
    avail=$(awk '{print $6}' <<< "$line")
    used=$(( total - avail ))
    pct=$(( total > 0 ? (100 * used / total) : 0 ))

    if [[ "$status" != "active" ]]; then
      inc_fail "Storage '${name}' status is '${status}'"
      continue
    fi

    if (( pct >= DISK_THRESHOLD )); then
      inc_warn "Storage '${name}' usage ${pct}%"
    else
      ok "Storage '${name}' healthy (${pct}% used)"
    fi
  done < <(pvesm status | tail -n +2)
}

check_services() {
  info "Checking core Proxmox services..."

  local services=(pveproxy pvedaemon pvestatd pve-cluster)
  systemctl list-unit-files | grep -q '^corosync\.service' && services+=(corosync)
  systemctl list-unit-files | grep -q '^pve-ha-lrm\.service' && services+=(pve-ha-lrm)
  systemctl list-unit-files | grep -q '^pve-ha-crm\.service' && services+=(pve-ha-crm)

  local svc
  for svc in "${services[@]}"; do
    local active substate
    active=$(systemctl is-active "$svc" 2>/dev/null || true)
    substate=$(systemctl show -p SubState --value "$svc" 2>/dev/null || echo "unknown")

    if [[ "$active" == "active" && "$substate" != "failed" ]]; then
      ok "Service ${svc} is healthy (${active}/${substate})"
      continue
    fi

    inc_warn "Service ${svc} unhealthy (${active}/${substate})"

    if $RESTART_UNHEALTHY; then
      info "Restarting ${svc} ..."
      if systemctl restart "$svc" && [[ "$(systemctl is-active "$svc" 2>/dev/null || true)" == "active" ]]; then
        ok "Service ${svc} recovered after restart."
      else
        inc_fail "Service ${svc} failed to recover after restart."
      fi
    fi
  done
}

check_zfs() {
  $SKIP_ZFS && return

  command -v zpool &>/dev/null || return

  info "Checking ZFS pools..."
  local unhealthy=0
  while IFS= read -r pool; do
    local state
    state=$(zpool list -H -o health "$pool" 2>/dev/null || echo "UNKNOWN")
    if [[ "$state" == "ONLINE" ]]; then
      ok "ZFS pool ${pool} is ONLINE"
    else
      inc_fail "ZFS pool ${pool} health is ${state}"
      unhealthy=1
    fi
  done < <(zpool list -H -o name 2>/dev/null || true)

  [[ $unhealthy -eq 1 ]] && warn "Run: zpool status -x for details."
}

check_cluster() {
  $SKIP_CLUSTER && return

  command -v pvecm &>/dev/null || return

  info "Checking cluster quorum..."
  local status
  status=$(pvecm status 2>/dev/null || true)
  if [[ -z "$status" ]]; then
    warn "Unable to read cluster status; skipping quorum check."
    return
  fi

  if grep -q "Quorate:[[:space:]]*Yes" <<< "$status"; then
    ok "Cluster quorum present."
  elif grep -q "No cluster network" <<< "$status"; then
    info "Node is not in a cluster (standalone)."
  else
    inc_fail "Cluster is not quorate."
  fi
}

main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Node Health Check"
  echo "=================================================="
  echo -e "${NC}"

  [[ $EUID -eq 0 ]] || error "Must be run as root."
  command -v pveversion &>/dev/null || error "Must run on a Proxmox VE host."

  parse_args "$@"

  validate_number "$DISK_THRESHOLD" "--disk-threshold"
  validate_number "$INODE_THRESHOLD" "--inode-threshold"
  validate_number "$MEMORY_THRESHOLD" "--memory-threshold"
  validate_number "$SWAP_THRESHOLD" "--swap-threshold"
  validate_number "$LOAD_FACTOR" "--load-factor"

  check_disk_usage
  check_inode_usage
  check_memory_and_load
  check_storage_pools
  check_services
  check_zfs
  check_cluster

  echo ""
  info "Summary: warnings=${WARNINGS}, failures=${FAILURES}"

  if (( FAILURES > 0 )); then
    error "Health check completed with failures."
  fi

  if (( WARNINGS > 0 )); then
    warn "Health check completed with warnings."
    exit 1
  fi

  ok "Health check passed with no warnings or failures."
}

main "$@"
