#!/usr/bin/env bash
# ==============================================================================
# proxmox-post-install.sh
# Post-installation setup script for Proxmox VE
#
# What this script does:
#   1. Disables the enterprise (subscription) repository
#   2. Enables the no-subscription (community) repository
#   3. Optionally disables the pve-enterprise and ceph repositories
#   4. Runs a full system update
#   5. Removes the nag/subscription dialog from the web UI
#   6. Enables IOMMU (passthrough) in GRUB if supported
#   7. Installs useful tools (vim, curl, wget, htop, iftop, iotop, net-tools)
#
# Usage:
#   chmod +x proxmox-post-install.sh
#   sudo ./proxmox-post-install.sh
#
# Run directly from GitHub:
#   bash <(curl -s https://raw.githubusercontent.com/Zantac150/Proxmox-Scripts/main/post-install/proxmox-post-install.sh)
# ==============================================================================

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

# ── Helpers ────────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || error "This script must be run as root."
}

require_proxmox() {
  command -v pveversion &>/dev/null || error "This script must be run on a Proxmox VE host."
}

# ── Repository configuration ───────────────────────────────────────────────────
configure_repos() {
  info "Configuring APT repositories..."

  # Disable enterprise repo
  local ent_list="/etc/apt/sources.list.d/pve-enterprise.list"
  if [[ -f "$ent_list" ]]; then
    sed -i 's|^deb|#deb|g' "$ent_list"
    success "Disabled enterprise repository ($ent_list)"
  fi

  # Disable ceph enterprise repo
  local ceph_list="/etc/apt/sources.list.d/ceph.list"
  if [[ -f "$ceph_list" ]]; then
    sed -i 's|^deb|#deb|g' "$ceph_list"
    success "Disabled ceph enterprise repository ($ceph_list)"
  fi

  # Detect PVE major version for the no-subscription repo
  local pve_major
  pve_major=$(pveversion | grep -oP 'pve-manager/\K[0-9]+')
  local codename
  codename=$(grep VERSION_CODENAME /etc/os-release | cut -d= -f2)

  local nosub_list="/etc/apt/sources.list.d/pve-no-subscription.list"
  if [[ ! -f "$nosub_list" ]] || ! grep -q "pve-no-subscription" "$nosub_list"; then
    echo "deb http://download.proxmox.com/debian/pve ${codename} pve-no-subscription" \
      > "$nosub_list"
    success "Enabled no-subscription repository ($nosub_list)"
  else
    info "No-subscription repository already configured."
  fi
}

# ── System update ──────────────────────────────────────────────────────────────
run_update() {
  info "Updating package lists and upgrading system..."
  apt-get update -qq
  apt-get -y full-upgrade
  apt-get -y autoremove
  success "System updated."
}

# ── Remove subscription nag ────────────────────────────────────────────────────
remove_nag() {
  info "Removing subscription nag from web UI..."

  local jsfile
  jsfile=$(find /usr/share/javascript/proxmox-widget-toolkit -name "proxmoxlib.js" 2>/dev/null | head -1)

  if [[ -z "$jsfile" ]]; then
    warn "proxmoxlib.js not found – skipping nag removal."
    return
  fi

  if grep -q "data.status !== 'Active'" "$jsfile"; then
    # Replace the subscription check so it always passes
    sed -i "s/data.status !== 'Active'/false/g" "$jsfile"
    success "Subscription nag removed. Restart pveproxy to apply: systemctl restart pveproxy"
  else
    info "Subscription nag already removed (or pattern not found)."
  fi
}

# ── IOMMU / passthrough ────────────────────────────────────────────────────────
enable_iommu() {
  info "Checking CPU for IOMMU support..."

  local grub_file="/etc/default/grub"
  local iommu_param=""

  if grep -qE "^flags.*vmx" /proc/cpuinfo; then
    iommu_param="intel_iommu=on iommu=pt"
    info "Intel CPU detected."
  elif grep -qE "^flags.*svm" /proc/cpuinfo; then
    iommu_param="amd_iommu=on iommu=pt"
    info "AMD CPU detected."
  else
    warn "Could not detect CPU vendor for IOMMU – skipping."
    return
  fi

  if grep -q "$iommu_param" "$grub_file"; then
    info "IOMMU parameters already present in GRUB config."
    return
  fi

  cp "${grub_file}" "${grub_file}.bak.$(date +%Y%m%d%H%M%S)"
  sed -i "s|GRUB_CMDLINE_LINUX_DEFAULT=\"|GRUB_CMDLINE_LINUX_DEFAULT=\"${iommu_param} |" "$grub_file"
  update-grub
  success "IOMMU enabled. Reboot required for changes to take effect."
}

# ── Install useful tools ───────────────────────────────────────────────────────
install_tools() {
  info "Installing useful utilities..."
  apt-get -y install \
    vim curl wget htop iftop iotop net-tools \
    lsof dnsutils nmap \
    > /dev/null
  success "Utilities installed."
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  echo -e "${CYAN}"
  echo "=================================================="
  echo "  Proxmox VE – Post-Installation Setup Script"
  echo "=================================================="
  echo -e "${NC}"

  require_root
  require_proxmox

  configure_repos
  run_update
  remove_nag
  enable_iommu
  install_tools

  echo ""
  success "Post-installation setup complete!"
  warn "A reboot is recommended if IOMMU was enabled for the first time."
}

main "$@"
