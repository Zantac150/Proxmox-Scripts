#!/usr/bin/env bash
# ==============================================================================
# install-sentry-lxc.sh
# Create and bootstrap a Proxmox LXC container running the Proxmox Sentry
# AI-powered security monitoring system.
#
# What this script does:
#   1. Creates a Debian 12 LXC container on the Proxmox host
#   2. Installs Python 3, pip, and all required Python dependencies
#      (scikit-learn, proxmoxer, requests, etc.)
#   3. Installs Trivy vulnerability scanner
#   4. Copies/downloads the Sentry module tree into the container
#   5. Writes a sentry.conf from prompted values (or --config-file path)
#   6. Installs and enables a systemd sentry-agent service
#
# Usage:
#   chmod +x install-sentry-lxc.sh
#   sudo ./install-sentry-lxc.sh [OPTIONS]
#
# Options:
#   -i, --id        <CTID>        Container ID              (default: 900)
#   -n, --name      <NAME>        Container hostname        (default: pve-sentry)
#       --storage   <STORAGE>     Root FS storage pool      (default: local-lvm)
#       --disk      <GB>          Root disk size in GB      (default: 12)
#       --cores     <N>           CPU cores                 (default: 2)
#       --memory    <MB>          RAM in MiB                (default: 2048)
#       --net-bridge <BR>         Network bridge            (default: vmbr0)
#       --ip        <CIDR>        Static IP (CIDR)          (default: dhcp)
#       --gw        <GW>          Default gateway           (optional)
#       --password  <PASS>        Root password             (default: prompted)
#       --config-file <PATH>      Pre-written sentry.conf to inject
#       --source-dir  <PATH>      Local sentry source dir   (default: same dir as this script)
#       --start                   Start container after install
#   -h, --help                    Show this help message
#
# Run directly from GitHub:
#   bash <(curl -s https://raw.githubusercontent.com/Zantac150/Proxmox-Scripts/main/sentry/install-sentry-lxc.sh)
# ==============================================================================

set -euo pipefail

# ── Defaults ───────────────────────────────────────────────────────────────────
CTID=900
CT_NAME="pve-sentry"
STORAGE="local-lvm"
DISK_SIZE=12
CORES=2
MEMORY=2048
SWAP=512
NET_BRIDGE="vmbr0"
CT_IP="dhcp"
CT_GW=""
CT_PASSWORD=""
CONFIG_FILE=""
AUTO_START=false
TEMPLATE_STORAGE="local"
TEMPLATE_TAG="debian-12-standard"
SENTRY_INSTALL_DIR="/opt/sentry"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}"

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

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -i|--id)          CTID="$2"; shift 2 ;;
      -n|--name)        CT_NAME="$2"; shift 2 ;;
      --storage)        STORAGE="$2"; shift 2 ;;
      --disk)           DISK_SIZE="$2"; shift 2 ;;
      --cores)          CORES="$2"; shift 2 ;;
      --memory)         MEMORY="$2"; shift 2 ;;
      --net-bridge)     NET_BRIDGE="$2"; shift 2 ;;
      --ip)             CT_IP="$2"; shift 2 ;;
      --gw)             CT_GW="$2"; shift 2 ;;
      --password)       CT_PASSWORD="$2"; shift 2 ;;
      --config-file)    CONFIG_FILE="$2"; shift 2 ;;
      --source-dir)     SOURCE_DIR="$2"; shift 2 ;;
      --start)          AUTO_START=true; shift ;;
      -h|--help)        usage ;;
      *) error "Unknown argument: $1" ;;
    esac
  done
}

require_root()    { [[ $EUID -eq 0 ]] || error "Must be run as root."; }
require_proxmox() { command -v pveversion &>/dev/null || error "Must run on a Proxmox VE host."; }

# ── Template handling ──────────────────────────────────────────────────────────
ensure_template() {
  info "Checking for Debian 12 LXC template..."
  local available
  available=$(pveam list "${TEMPLATE_STORAGE}" 2>/dev/null | awk '{print $1}' | grep "${TEMPLATE_TAG}" | head -n1 || true)

  if [[ -z "$available" ]]; then
    info "Template not found locally; updating template list and downloading..."
    pveam update
    local remote
    remote=$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep "${TEMPLATE_TAG}" | head -n1 || true)
    [[ -n "$remote" ]] || error "Cannot find ${TEMPLATE_TAG} in available templates."
    pveam download "${TEMPLATE_STORAGE}" "${remote}"
    available=$(pveam list "${TEMPLATE_STORAGE}" | awk '{print $1}' | grep "${TEMPLATE_TAG}" | head -n1)
  fi

  TEMPLATE_PATH="${available}"
  success "Template ready: ${TEMPLATE_PATH}"
}

# ── Password prompt ────────────────────────────────────────────────────────────
prompt_password() {
  if [[ -z "$CT_PASSWORD" ]]; then
    read -r -s -p "Enter root password for container ${CTID}: " CT_PASSWORD
    echo
    local pw2
    read -r -s -p "Confirm password: " pw2
    echo
    [[ "$CT_PASSWORD" == "$pw2" ]] || error "Passwords do not match."
  fi
}

# ── Container creation ─────────────────────────────────────────────────────────
create_container() {
  info "Creating LXC container ${CTID} (${CT_NAME})..."

  pct list | awk '{print $1}' | grep -q "^${CTID}$" \
    && error "Container ${CTID} already exists. Use a different --id."

  local net_arg="name=eth0,bridge=${NET_BRIDGE}"
  if [[ "$CT_IP" == "dhcp" ]]; then
    net_arg+=",ip=dhcp"
  else
    net_arg+=",ip=${CT_IP}"
    [[ -n "$CT_GW" ]] && net_arg+=",gw=${CT_GW}"
  fi

  pct create "${CTID}" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE_PATH##*/}" \
    --hostname  "${CT_NAME}"            \
    --storage   "${STORAGE}"            \
    --rootfs    "${STORAGE}:${DISK_SIZE}" \
    --cores     "${CORES}"              \
    --memory    "${MEMORY}"             \
    --swap      "${SWAP}"               \
    --net0      "${net_arg}"            \
    --unprivileged 1                    \
    --features  nesting=1               \
    --password  "${CT_PASSWORD}"        \
    --start     0

  success "Container ${CTID} created."
}

# ── Start and wait ─────────────────────────────────────────────────────────────
start_container() {
  info "Starting container ${CTID}..."
  pct start "${CTID}"
  local attempts=0
  while ! pct exec "${CTID}" -- test -f /etc/os-release 2>/dev/null; do
    (( attempts++ )) || true
    [[ $attempts -lt 20 ]] || error "Container ${CTID} did not become ready in time."
    sleep 2
  done
  success "Container ${CTID} is running."
}

# ── In-container execution helper ─────────────────────────────────────────────
ct() { pct exec "${CTID}" -- bash -c "$*"; }

# ── System package installation ────────────────────────────────────────────────
install_packages() {
  info "Updating container packages and installing dependencies..."
  ct "DEBIAN_FRONTEND=noninteractive apt-get update -qq"
  ct "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq"
  ct "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3 python3-pip python3-venv \
        curl wget ca-certificates gnupg \
        iproute2 iputils-ping net-tools \
        procps lsof sqlite3 jq \
        libatlas-base-dev"
  success "System packages installed."
}

# ── Trivy installation ─────────────────────────────────────────────────────────
install_trivy() {
  info "Installing Trivy vulnerability scanner..."
  ct "curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
        | sh -s -- -b /usr/local/bin"
  ct "trivy --version"
  success "Trivy installed."
}

# ── Python virtual environment + packages ─────────────────────────────────────
install_python_deps() {
  info "Creating Python virtual environment and installing packages..."
  ct "python3 -m venv /opt/sentry-venv"
  ct "/opt/sentry-venv/bin/pip install --upgrade pip wheel --quiet"
  ct "/opt/sentry-venv/bin/pip install \
        scikit-learn==1.5.2 \
        numpy==1.26.4 \
        pandas==2.2.3 \
        requests==2.32.3 \
        proxmoxer==2.0.1 \
        paramiko==3.5.0 \
        schedule==1.2.2 \
        --quiet"
  success "Python dependencies installed."
}

# ── Copy Sentry source into container ─────────────────────────────────────────
deploy_sentry_source() {
  info "Deploying Sentry source files to container ${CTID}..."
  ct "mkdir -p ${SENTRY_INSTALL_DIR}"

  # Push files via pct push (available in PVE 7+)
  local files=(
    sentry-agent.py
    "modules/__init__.py"
    "modules/baseline.py"
    "modules/anomaly_detector.py"
    "modules/network_monitor.py"
    "modules/vuln_scanner.py"
    "modules/config_auditor.py"
    "modules/recommender.py"
    "alerting/__init__.py"
    "alerting/alertmanager.py"
    "alerting/channels/__init__.py"
    "alerting/channels/email_channel.py"
    "alerting/channels/pushover_channel.py"
    "alerting/channels/webhook_channel.py"
    "alerting/channels/syslog_channel.py"
  )

  ct "mkdir -p ${SENTRY_INSTALL_DIR}/modules \
              ${SENTRY_INSTALL_DIR}/alerting/channels \
              ${SENTRY_INSTALL_DIR}/config \
              /var/log/sentry \
              /var/lib/sentry"

  for rel in "${files[@]}"; do
    local src="${SOURCE_DIR}/${rel}"
    local dst="${SENTRY_INSTALL_DIR}/${rel}"
    if [[ -f "$src" ]]; then
      pct push "${CTID}" "${src}" "${dst}"
    else
      warn "Source file not found, skipping: ${src}"
    fi
  done

  success "Sentry source deployed."
}

# ── Configuration file ─────────────────────────────────────────────────────────
deploy_config() {
  info "Deploying sentry configuration..."
  local dst="${SENTRY_INSTALL_DIR}/config/sentry.conf"

  if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
    pct push "${CTID}" "${CONFIG_FILE}" "${dst}"
    success "Custom config deployed from ${CONFIG_FILE}."
  else
    # Deploy the example config as a starting point
    local example="${SOURCE_DIR}/config/sentry.conf.example"
    if [[ -f "$example" ]]; then
      pct push "${CTID}" "${example}" "${dst}"
    else
      # Write a minimal config directly
      ct "cat > ${dst} << 'CONF'
[proxmox]
host     = 127.0.0.1
user     = root@pam
password =
verify_ssl = false

[sentry]
interval_seconds  = 300
baseline_days     = 7
log_file          = /var/log/sentry/sentry.log
db_file           = /var/lib/sentry/sentry.db
anomaly_threshold = 0.15

[alerts]
channels = email,pushover

[email]
enabled  = false
smtp_host = localhost
smtp_port = 25
from      = sentry@proxmox.local
to        = admin@example.com

[pushover]
enabled  = false
api_token =
user_key  =

[webhook]
enabled = false
url     =
CONF"
    fi
    warn "Edit ${dst} inside the container before starting the service."
  fi
}

# ── Systemd service ────────────────────────────────────────────────────────────
install_service() {
  info "Installing sentry-agent systemd service..."
  ct "cat > /etc/systemd/system/sentry-agent.service << 'UNIT'
[Unit]
Description=Proxmox Sentry AI Security Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/opt/sentry-venv/bin/python3 /opt/sentry/sentry-agent.py --config /opt/sentry/config/sentry.conf
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sentry-agent

[Install]
WantedBy=multi-user.target
UNIT"
  ct "systemctl daemon-reload"
  ct "systemctl enable sentry-agent"
  success "sentry-agent service installed and enabled."
}

# ── Final summary ──────────────────────────────────────────────────────────────
print_summary() {
  local container_ip
  container_ip=$(pct exec "${CTID}" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")

  echo -e "${CYAN}"
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║        Proxmox Sentry – Installation Complete            ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo -e "${NC}"
  echo "  Container ID   : ${CTID}"
  echo "  Hostname       : ${CT_NAME}"
  echo "  IP Address     : ${container_ip}"
  echo ""
  echo "  Next steps:"
  echo "    1. Edit config:  pct exec ${CTID} -- nano /opt/sentry/config/sentry.conf"
  echo "    2. Start agent:  pct exec ${CTID} -- systemctl start sentry-agent"
  echo "    3. View logs:    pct exec ${CTID} -- journalctl -u sentry-agent -f"
  echo ""
  warn "Set your Proxmox API credentials and alerting details in sentry.conf before starting."
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox Sentry – LXC Installer"
  echo "=================================================="
  echo -e "${NC}"

  require_root
  require_proxmox
  parse_args "$@"

  prompt_password
  ensure_template
  create_container
  start_container
  install_packages
  install_trivy
  install_python_deps
  deploy_sentry_source
  deploy_config
  install_service

  if $AUTO_START; then
    info "Starting sentry-agent service..."
    ct "systemctl start sentry-agent" || warn "Service start deferred — configure sentry.conf first."
  fi

  print_summary
}

main "$@"
