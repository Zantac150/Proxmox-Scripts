#!/usr/bin/env bash
# ==============================================================================
# check-network-health.sh
# Network health checks for Proxmox nodes.
#
# Checks:
#   - Default route and gateway reachability
#   - Bridge and link state overview
#   - DNS resolver reachability and lookup test
#   - Optional Internet target latency test
#
# Usage:
#   chmod +x check-network-health.sh
#   sudo ./check-network-health.sh [OPTIONS]
#
# Options:
#       --target <HOST/IP>         External target to ping (default: 1.1.1.1)
#       --dns-domain <DOMAIN>      Domain for DNS test (default: proxmox.com)
#       --count <N>                Ping packet count per test (default: 3)
#       --latency-warn-ms <MS>     Warn if avg latency exceeds this (default: 100)
#       --skip-external            Skip external target reachability check
#   -h, --help                     Show this help message
# ==============================================================================

set -euo pipefail

TARGET_HOST="1.1.1.1"
DNS_DOMAIN="proxmox.com"
PING_COUNT=3
LATENCY_WARN_MS=100
SKIP_EXTERNAL=false

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
      --target)          TARGET_HOST="$2"; shift 2 ;;
      --dns-domain)      DNS_DOMAIN="$2"; shift 2 ;;
      --count)           PING_COUNT="$2"; shift 2 ;;
      --latency-warn-ms) LATENCY_WARN_MS="$2"; shift 2 ;;
      --skip-external)   SKIP_EXTERNAL=true; shift ;;
      -h|--help)         usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done
}

inc_warn() {
  (( WARNINGS++ )) || true
  warn "$*"
}

inc_fail() {
  (( FAILURES++ )) || true
  fail "$*"
}

get_avg_latency() {
  local target="$1"
  ping -n -c "$PING_COUNT" -W 1 "$target" 2>/dev/null \
    | awk -F'/' '/^rtt|^round-trip/ {print $5}'
}

check_default_route() {
  info "Checking default route..."
  local route iface gateway
  route=$(ip route show default | head -n 1 || true)

  [[ -n "$route" ]] || { inc_fail "No default route configured."; return; }

  iface=$(awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' <<< "$route")
  gateway=$(awk '{for(i=1;i<=NF;i++) if($i=="via") print $(i+1)}' <<< "$route")

  if [[ -z "$iface" ]]; then
    inc_fail "Unable to identify default route interface."
  else
    ok "Default route interface: $iface"
  fi

  if [[ -n "$gateway" ]]; then
    local avg
    avg=$(get_avg_latency "$gateway" || true)
    if [[ -z "$avg" ]]; then
      inc_fail "Gateway $gateway is unreachable."
    else
      ok "Gateway $gateway reachable (avg ${avg} ms)"
      if awk -v a="$avg" -v t="$LATENCY_WARN_MS" 'BEGIN { exit !(a>t) }'; then
        inc_warn "Gateway latency ${avg} ms exceeds ${LATENCY_WARN_MS} ms"
      fi
    fi
  else
    warn "Default route has no explicit gateway (possibly point-to-point)."
  fi
}

check_bridge_and_links() {
  info "Checking bridge and link state..."

  while IFS= read -r line; do
    local ifname state
    ifname=$(awk '{print $1}' <<< "$line")
    state=$(awk '{print $2}' <<< "$line")
    if [[ "$state" == "UP" ]]; then
      ok "Interface ${ifname} is UP"
    else
      inc_warn "Interface ${ifname} state is ${state}"
    fi
  done < <(ip -br link | awk '/^(vmbr|bond|eno|ens|eth)/ {print $1, $2}')
}

check_dns() {
  info "Checking DNS resolver reachability..."

  local tested=0
  while IFS= read -r dns; do
    [[ -n "$dns" ]] || continue
    (( tested++ )) || true

    if ping -n -c 1 -W 1 "$dns" &>/dev/null; then
      ok "DNS server $dns reachable"
    else
      inc_warn "DNS server $dns not reachable via ICMP"
    fi
  done < <(awk '/^nameserver/ {print $2}' /etc/resolv.conf | head -n 3)

  (( tested > 0 )) || inc_warn "No nameservers found in /etc/resolv.conf"

  if command -v getent &>/dev/null && getent ahosts "$DNS_DOMAIN" >/dev/null 2>&1; then
    ok "DNS lookup succeeded for ${DNS_DOMAIN}"
  else
    inc_fail "DNS lookup failed for ${DNS_DOMAIN}"
  fi
}

check_external_target() {
  $SKIP_EXTERNAL && return

  info "Checking external reachability (${TARGET_HOST})..."
  local avg
  avg=$(get_avg_latency "$TARGET_HOST" || true)

  if [[ -z "$avg" ]]; then
    inc_fail "External target ${TARGET_HOST} unreachable"
    return
  fi

  ok "External target ${TARGET_HOST} reachable (avg ${avg} ms)"
  if awk -v a="$avg" -v t="$LATENCY_WARN_MS" 'BEGIN { exit !(a>t) }'; then
    inc_warn "External latency ${avg} ms exceeds ${LATENCY_WARN_MS} ms"
  fi
}

main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Network Health Check"
  echo "=================================================="
  echo -e "${NC}"

  [[ $EUID -eq 0 ]] || error "Must be run as root."

  parse_args "$@"
  [[ "$PING_COUNT" =~ ^[1-9][0-9]*$ ]] || error "--count must be a positive integer."
  [[ "$LATENCY_WARN_MS" =~ ^[0-9]+([.][0-9]+)?$ ]] || error "--latency-warn-ms must be numeric."

  check_default_route
  check_bridge_and_links
  check_dns
  check_external_target

  echo ""
  info "Summary: warnings=${WARNINGS}, failures=${FAILURES}"

  if (( FAILURES > 0 )); then
    error "Network checks completed with failures."
  fi

  if (( WARNINGS > 0 )); then
    warn "Network checks completed with warnings."
    exit 1
  fi

  ok "Network checks passed with no warnings or failures."
}

main "$@"
