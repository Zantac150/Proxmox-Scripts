# Proxmox-Scripts

A collection of Bash scripts and Python modules for automating, managing, and **proactively securing** a [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment/overview) home lab or small environment.

---

## Contents

### Management Scripts

| Script | Description |
|--------|-------------|
| [`post-install/proxmox-post-install.sh`](#post-install) | Post-installation setup: disable enterprise repo, enable community repo, remove nag, enable IOMMU, install tools |
| [`vm-management/create-vm-template.sh`](#vm-template-creator) | Download a cloud-init image and create a reusable VM template |
| [`vm-management/clone-vm.sh`](#vm-clone) | Clone a VM or template into one or more new VMs |
| [`lxc-management/create-lxc.sh`](#lxc-container-creator) | Create an LXC container from a template |
| [`lxc-management/bulk-update.sh`](#bulk-update) | Update all running LXC containers and/or QEMU VMs |
| [`backup/backup-config.sh`](#config-backup) | Back up Proxmox node configuration to local or remote destination |
| [`monitoring/check-node-health.sh`](#node-health-check) | Host observability checks for disk, memory, load, storage, and core services |
| [`monitoring/check-network-health.sh`](#network-health-check) | Validate routing, link state, DNS resolution, and external connectivity |
| [`monitoring/recover-services.sh`](#service-recovery) | Detect unhealthy Proxmox services and restart them automatically |
| [`autonomous-fabric/proxmox-autonomous-fabric.py`](#proxmox-autonomous-fabric-paf) | Intent-based autonomous planning with replay, digital twin simulation, and trust-scored execution plans |

### Proxmox Sentry – AI Security Monitoring

| Component | Description |
|-----------|-------------|
| [`sentry/install-sentry-lxc.sh`](#proxmox-sentry) | One-command installer — creates and configures the Sentry LXC |
| [`sentry/sentry-agent.py`](#proxmox-sentry) | Main orchestration daemon (scheduler, module runner, alert dispatcher) |
| [`sentry/modules/baseline.py`](#sentry-modules) | Metric collection and scikit-learn ML baseline management |
| [`sentry/modules/anomaly_detector.py`](#sentry-modules) | Isolation Forest anomaly detection with static threshold fallback |
| [`sentry/modules/network_monitor.py`](#sentry-modules) | Network connection and traffic anomaly monitoring |
| [`sentry/modules/vuln_scanner.py`](#sentry-modules) | Trivy vulnerability scanner integration for LXC/VM containers |
| [`sentry/modules/config_auditor.py`](#sentry-modules) | Security-score-based LXC/VM configuration auditor |
| [`sentry/modules/recommender.py`](#sentry-modules) | Rule-based remediation recommendation engine |
| [`sentry/alerting/alertmanager.py`](#sentry-alerting) | Pluggable alert dispatcher with deduplication and severity filtering |
| [`sentry/alerting/channels/email_channel.py`](#sentry-alerting) | SMTP email alerts |
| [`sentry/alerting/channels/pushover_channel.py`](#sentry-alerting) | Pushover mobile push notifications |
| [`sentry/alerting/channels/webhook_channel.py`](#sentry-alerting) | Generic webhook (Slack, Discord, Teams, custom JSON) |
| [`sentry/alerting/channels/syslog_channel.py`](#sentry-alerting) | Syslog / remote log-platform integration (Graylog, Splunk, Loki) |

---

## Requirements

- Proxmox VE 7.x or 8.x
- Scripts must be run as **root** on a Proxmox node
- `bash` 4.0+

### Sentry additional requirements (handled by installer)

- Python 3.10+, pip, venv
- [scikit-learn](https://scikit-learn.org/) 1.5+, numpy, pandas, requests, proxmoxer, schedule
- [Trivy](https://aquasecurity.github.io/trivy/) (vulnerability scanner)
- 2 GB RAM / 2 vCPU / 12 GB disk recommended for the Sentry LXC

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

### Node Health Check

**`monitoring/check-node-health.sh`**

Performs host-level observability checks for common Proxmox failure modes:

- Filesystem and inode pressure
- Memory/swap pressure and 1-minute load threshold
- Proxmox storage pool health (`pvesm status`)
- Core Proxmox service health (`pveproxy`, `pvedaemon`, `pvestatd`, `pve-cluster`)
- Optional ZFS pool and cluster quorum checks
- Optional restart of unhealthy core services

```bash
chmod +x monitoring/check-node-health.sh

# Run standard checks:
sudo ./monitoring/check-node-health.sh

# Auto-restart unhealthy core services and use custom thresholds:
sudo ./monitoring/check-node-health.sh \
  --restart-unhealthy \
  --disk-threshold 90 \
  --memory-threshold 92 \
  --load-factor 2.0
```

Options: `--disk-threshold`, `--inode-threshold`, `--memory-threshold`, `--swap-threshold`, `--load-factor`, `--restart-unhealthy`, `--skip-storage`, `--skip-zfs`, `--skip-cluster`

---

### Network Health Check

**`monitoring/check-network-health.sh`**

Validates core networking paths used by Proxmox hosts, LXCs, and VMs:

- Default route and gateway reachability
- Interface and bridge link state
- Resolver reachability and DNS lookup validation
- External target reachability and latency checks

```bash
chmod +x monitoring/check-network-health.sh

# Default checks:
sudo ./monitoring/check-network-health.sh

# Custom target/domain and stricter latency warning:
sudo ./monitoring/check-network-health.sh \
  --target 8.8.8.8 \
  --dns-domain github.com \
  --latency-warn-ms 60
```

Options: `--target`, `--dns-domain`, `--count`, `--latency-warn-ms`, `--skip-external`

---

### Service Recovery

**`monitoring/recover-services.sh`**

Detects unhealthy Proxmox systemd services and performs controlled restart attempts.

```bash
chmod +x monitoring/recover-services.sh

# Recover default core services:
sudo ./monitoring/recover-services.sh

# Check-only mode (no restarts):
sudo ./monitoring/recover-services.sh --check-only

# Custom service list, retries, and dry-run:
sudo ./monitoring/recover-services.sh \
  --services pveproxy,pvedaemon,pvestatd,pve-cluster,corosync \
  --max-retries 3 \
  --dry-run
```

Options: `--services`, `--max-retries`, `--dry-run`, `--check-only`

---

### Proxmox Autonomous Fabric (PAF)

**`autonomous-fabric/proxmox-autonomous-fabric.py`**

PAF is an experimental orchestration engine that turns operator intent into policy-gated execution plans with built-in replay simulation and explainable trust outputs.

Implemented framework capabilities:

- **Intent-based operations** from structured goals or free-text intent
- **Time-travel replay** from stored cluster state snapshots
- **Predictive failure genome** scoring from node telemetry drift
- **Live economic scheduler** based on tariff zone, thermal index, and SLA
- **Blast-radius containment** scoring and containment zones per action
- **Self-authoring runbooks** from generated decision history
- **Cross-layer digital twin** simulation of risk/performance deltas
- **Human trust layer** with why-now, risk, fallback, and confidence narratives

```bash
# Generate an autonomous plan payload from sample files
python3 autonomous-fabric/proxmox-autonomous-fabric.py orchestrate \
  --intent-file autonomous-fabric/examples/intent.sample.json \
  --state-file autonomous-fabric/examples/cluster-state.sample.json \
  --policy-file autonomous-fabric/examples/policy.sample.json \
  --capture-snapshot \
  --history-file autonomous-fabric/examples/history.sample.json \
  --output-json autonomous-fabric/examples/plan-output.sample.json

# Build a markdown runbook from execution history
python3 autonomous-fabric/proxmox-autonomous-fabric.py runbook \
  --history-file autonomous-fabric/examples/history.sample.json
```

Subcommands:
- `orchestrate` — generate trust-scored action plans (optionally snapshot + replay)
- `capture-snapshot` — append point-in-time state to snapshot store
- `runbook` — generate markdown runbook from history events

---

## Proxmox Sentry

**`sentry/`** — AI-powered, Darktrace-inspired security monitoring and predictive alerting for Proxmox VE home labs.

Sentry runs as a dedicated LXC container on your Proxmox node.  It continuously collects metrics, builds ML baselines, scans for vulnerabilities, audits container/VM configurations, monitors the network for anomalies, and alerts you before problems impact your lab.

### Architecture

```
sentry/
├── install-sentry-lxc.sh        ← run this first (Proxmox helper)
├── sentry-agent.py              ← main daemon (systemd service)
├── config/
│   └── sentry.conf.example      ← annotated configuration template
├── modules/
│   ├── baseline.py              ← metric collection + SQLite storage + sklearn model fitting
│   ├── anomaly_detector.py      ← Isolation Forest real-time anomaly detection
│   ├── network_monitor.py       ← suspicious ports, new external hosts, traffic spikes
│   ├── vuln_scanner.py          ← Trivy CVE scanning for LXC rootfs + host OS
│   ├── config_auditor.py        ← security-score LXC/VM config audit via Proxmox API
│   └── recommender.py           ← actionable remediation guidance
└── alerting/
    ├── alertmanager.py          ← dispatcher with dedup, throttle, severity filter
    └── channels/
        ├── email_channel.py     ← SMTP (plain + TLS/STARTTLS)
        ├── pushover_channel.py  ← Pushover mobile push
        ├── webhook_channel.py   ← Slack / Discord / Teams / generic JSON
        └── syslog_channel.py    ← local rsyslog + remote UDP/TCP (Graylog, Splunk, Loki)
```

### Quick Start

```bash
# 1. On the Proxmox host — create and bootstrap the Sentry LXC (ID 900):
chmod +x sentry/install-sentry-lxc.sh
sudo ./sentry/install-sentry-lxc.sh --id 900 --name pve-sentry \
  --ip 192.168.1.200/24 --gw 192.168.1.1

# 2. Edit the configuration inside the container:
pct exec 900 -- nano /opt/sentry/config/sentry.conf

# 3. Start the agent:
pct exec 900 -- systemctl start sentry-agent

# 4. Watch logs:
pct exec 900 -- journalctl -u sentry-agent -f
```

Or run directly from GitHub:
```bash
bash <(curl -s https://raw.githubusercontent.com/Zantac150/Proxmox-Scripts/main/sentry/install-sentry-lxc.sh)
```

Installer options: `--id`, `--name`, `--storage`, `--disk`, `--cores`, `--memory`, `--net-bridge`, `--ip`, `--gw`, `--password`, `--config-file`, `--source-dir`, `--start`

### Sentry Modules

#### `modules/baseline.py`

Collects host and per-container metrics every cycle and stores them in a local SQLite database.  After `baseline_days` (default 7) of history has accumulated, it fits a scikit-learn **StandardScaler → Isolation Forest** pipeline as a multi-variate anomaly detector.  Models are pickled to SQLite so they survive restarts.

Metrics collected:
- Host: CPU load (1/5/15 min), memory usage, per-interface RX/TX bytes, disk I/O counters
- Proxmox node: overall CPU %, memory %, per-guest CPU/memory/network via the PVE API RRD endpoint

#### `modules/anomaly_detector.py`

Scores each incoming metric snapshot through the trained Isolation Forest.  Anomaly score < −0.4 → `critical`; < 0 → `warning`.  Identifies the top-contributing features via the standard scaler to help explain the alert.  Falls back to static thresholds (CPU > 85/95 %, memory > 90/95 %) when ML history is insufficient.

#### `modules/network_monitor.py`

- **Suspicious ports** — alerts immediately if a process opens a port from a known bad-actor list (4444, 1337, 31337, etc.)
- **New external hosts** — every established connection to a first-seen external IP is flagged for review
- **Traffic spikes** — per-interface byte-rate deltas; warns at > 500 MB/s sustained

#### `modules/vuln_scanner.py`

Wraps [Trivy](https://trivy.dev/) to scan:
- Each running LXC container's rootfs at `/var/lib/lxc/<id>/rootfs`
- The Proxmox host OS packages

Scans run on a configurable schedule (`scan_interval_seconds`, default 24 h) to avoid continuous I/O load.  Findings are filtered by `min_severity` (default `HIGH`) and `ignore_unfixed`.

#### `modules/config_auditor.py`

Connects to the Proxmox API and scores each LXC/VM configuration from 0–100.  Issues flagged:

| Check | Deduction | Severity |
|-------|-----------|----------|
| Privileged LXC container | −30 | critical |
| Host device pass-through | −25 | critical |
| Nesting without AppArmor | −10 | warning |
| No firewall rules | −15 | warning |

Guests scoring below `min_score_threshold` (default 70) trigger an alert.

#### `modules/recommender.py`

Maps every finding type to a prioritised, human-readable remediation recommendation that is included in every alert.

### Sentry Alerting

#### `alerting/alertmanager.py`

Central dispatcher with:
- **Deduplication** — identical alerts within `dedup_seconds` (default 1 h) are suppressed
- **Severity filter** — only alerts at or above `min_alert_severity` (default `warning`) are sent
- **Pluggable channels** — add any channel by dropping `<name>_channel.py` in `alerting/channels/` implementing the `AlertChannel` ABC

#### Alert Channels

| Channel | Platform |
|---------|----------|
| `email` | Any SMTP server (plain / STARTTLS / TLS) |
| `pushover` | [Pushover](https://pushover.net/) mobile push |
| `webhook` | Slack, Discord, Microsoft Teams, or any JSON HTTP endpoint |
| `syslog` | Local rsyslog / journald, or remote UDP/TCP syslog (Graylog, Splunk, Loki) |

Enable channels in `sentry.conf`:
```ini
[alerts]
channels = email,pushover,webhook,syslog
```

---

## Contributing

Pull requests and issues are welcome. If you have scripts that help manage a Proxmox environment, feel free to open a PR.

## License

[MIT](LICENSE)
