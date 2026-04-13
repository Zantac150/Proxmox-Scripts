# Proxmox-Scripts

A collection of Bash scripts for automating and managing a [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment/overview) home lab or small environment. Each script is self-contained, documented, and designed to be run directly on a Proxmox host.

---

## Contents

| Script | Description |
|--------|-------------|
| [`post-install/proxmox-post-install.sh`](#post-install) | Post-installation setup: disable enterprise repo, enable community repo, remove nag, enable IOMMU, install tools |
| [`vm-management/create-vm-template.sh`](#vm-template-creator) | Download a cloud-init image and create a reusable VM template |
| [`vm-management/clone-vm.sh`](#vm-clone) | Clone a VM or template into one or more new VMs |
| [`lxc-management/create-lxc.sh`](#lxc-container-creator) | Create an LXC container from a template |
| [`lxc-management/bulk-update.sh`](#bulk-update) | Update all running LXC containers and/or QEMU VMs |
| [`backup/backup-config.sh`](#config-backup) | Back up Proxmox node configuration to local or remote destination |

---

## Requirements

- Proxmox VE 7.x or 8.x
- Scripts must be run as **root** on a Proxmox node
- `bash` 4.0+

---

## Scripts

### Post-Install

**`post-install/proxmox-post-install.sh`**

Performs the most common post-installation housekeeping tasks on a fresh Proxmox node:

- Disables the **enterprise** (paid subscription) repository
- Enables the **no-subscription** (community) repository
- Removes the subscription **nag dialog** from the web UI
- Enables **IOMMU/passthrough** in GRUB (auto-detects Intel/AMD)
- Installs useful utilities: `vim`, `curl`, `wget`, `htop`, `iftop`, `iotop`, `net-tools`, `nmap`

```bash
# Clone the repo, then run:
chmod +x post-install/proxmox-post-install.sh
sudo ./post-install/proxmox-post-install.sh

# Or run directly from GitHub:
bash <(curl -s https://raw.githubusercontent.com/Zantac150/Proxmox-Scripts/main/post-install/proxmox-post-install.sh)
```

---

### VM Template Creator

**`vm-management/create-vm-template.sh`**

Downloads a cloud-init enabled OS image and registers it as a Proxmox VM template ready for cloning.

Supported OS images (`--os` flag):

| Key | OS |
|-----|----|
| `ubuntu22` | Ubuntu 22.04 LTS (Jammy) |
| `ubuntu24` | Ubuntu 24.04 LTS (Noble) |
| `debian12` | Debian 12 (Bookworm) |
| `rocky9` | Rocky Linux 9 |

```bash
chmod +x vm-management/create-vm-template.sh

# Ubuntu 22.04 template at ID 9000 on local-lvm storage (defaults):
sudo ./vm-management/create-vm-template.sh

# Debian 12 template at ID 9001:
sudo ./vm-management/create-vm-template.sh --id 9001 --os debian12 --storage local-lvm
```

Options: `--id`, `--name`, `--storage`, `--os`, `--cores`, `--memory`

---

### VM Clone

**`vm-management/clone-vm.sh`**

Clones an existing VM or template into one or more full-clone VMs.

```bash
chmod +x vm-management/clone-vm.sh

# Clone template 9000 into VM 101:
sudo ./vm-management/clone-vm.sh --source 9000 --id 101 --name my-vm

# Clone template 9000 into 3 VMs (IDs 200–202), start them immediately:
sudo ./vm-management/clone-vm.sh --source 9000 --id 200 --name web-server --count 3 --start
```

Options: `--source`, `--id`, `--name`, `--count`, `--cores`, `--memory`, `--storage`, `--start`

---

### LXC Container Creator

**`lxc-management/create-lxc.sh`**

Creates a new LXC container, automatically downloading the template if needed.

```bash
chmod +x lxc-management/create-lxc.sh

# Basic container with DHCP:
sudo ./lxc-management/create-lxc.sh --id 200 --name my-container

# Static IP, 2 cores, 1 GB RAM, start immediately:
sudo ./lxc-management/create-lxc.sh \
  --id 201 --name nginx \
  --ip 192.168.1.50/24 --gw 192.168.1.1 \
  --cores 2 --memory 1024 \
  --start
```

Options: `--id`, `--name`, `--template`, `--storage`, `--disk`, `--cores`, `--memory`, `--swap`, `--net-bridge`, `--ip`, `--gw`, `--password`, `--unprivileged`, `--privileged`, `--start`

---

### Bulk Update

**`lxc-management/bulk-update.sh`**

Updates the packages inside all running LXC containers and optionally QEMU VMs (via guest agent). Auto-detects `apt-get`, `dnf`, `yum`, `apk`, `pacman`, and `zypper`.

```bash
chmod +x lxc-management/bulk-update.sh

# Update all running LXC containers:
sudo ./lxc-management/bulk-update.sh --lxc-only

# Update everything (LXC + VMs):
sudo ./lxc-management/bulk-update.sh --all

# Update specific containers only:
sudo ./lxc-management/bulk-update.sh --lxc-only --ids 100,101,105

# Dry run – see what would happen:
sudo ./lxc-management/bulk-update.sh --dry-run
```

Options: `--lxc-only`, `--vm-only`, `--all`, `--include-stopped`, `--ids`, `--dry-run`

---

### Config Backup

**`backup/backup-config.sh`**

Backs up critical Proxmox node configuration files (`/etc/pve`, `/etc/network`, host files, etc.) to a local directory and optionally syncs to a remote host via rsync.

```bash
chmod +x backup/backup-config.sh

# Local backup with 14-day retention:
sudo ./backup/backup-config.sh --keep 14

# Custom local directory:
sudo ./backup/backup-config.sh --dest /mnt/nas/proxmox-backups

# Local + remote rsync:
sudo ./backup/backup-config.sh --remote backup@192.168.1.10:/backups/proxmox
```

Options: `--dest`, `--remote`, `--keep`, `--no-ssh-keys`, `--no-compress`

---

## Contributing

Pull requests and issues are welcome. If you have scripts that help manage a Proxmox environment, feel free to open a PR.

## License

[MIT](LICENSE)
