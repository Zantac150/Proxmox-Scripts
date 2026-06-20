# Proxmox-Scripts Integration Checklist

A step-by-step verification guide for confirming that every script and module
in this repository functions correctly on a live **Proxmox VE 7.x / 8.x** node.

Run all commands as **root** on a Proxmox host unless noted otherwise.

---

## Prerequisites

```bash
# Confirm PVE version
pveversion --verbose

# Confirm bash ≥ 4.0
bash --version | head -1

# Confirm Python ≥ 3.10 (required by Sentry and PAF)
python3 --version

# Confirm git is available (for cloning)
git --version
```

---

## 1. Repository Setup

```bash
# Clone repository to a working directory on the PVE node
git clone https://github.com/Zantac150/Proxmox-Scripts.git /opt/proxmox-scripts
cd /opt/proxmox-scripts
chmod +x $(find . -name '*.sh' -type f)
```

**Expected:** No errors. All `.sh` files become executable.

---

## 2. Post-Install Script

**File:** `post-install/proxmox-post-install.sh`

```bash
# Dry-run equivalent: show what would change
grep -E 'apt|rm|sed|echo|update' post-install/proxmox-post-install.sh | head -30

# Run (only on a fresh/disposable node — modifies apt sources and GRUB)
bash post-install/proxmox-post-install.sh
```

**Verify after run:**
```bash
# Enterprise repo should be commented out
grep -v '^#' /etc/apt/sources.list.d/pve-enterprise.list

# No-subscription repo should be active
grep -v '^#' /etc/apt/sources.list.d/pve-no-subscription.list

# Nag removal: DataCenterSummary subscription key should be altered
grep -i "subscription" /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js | head -3

# IOMMU should be in GRUB config
grep -E "intel_iommu|amd_iommu" /etc/default/grub
```

---

## 3. VM Template Creator

**File:** `vm-management/create-vm-template.sh`

**Requirements:** `qemu-img`, `wget` or `curl`, `pvesm`, `qm`, Proxmox storage with
`images` content type enabled.

```bash
# Check storage availability
pvesm status

# Create Ubuntu 22.04 template at ID 9000 (adjust storage name as needed)
bash vm-management/create-vm-template.sh --id 9000 --os ubuntu22 --storage local-lvm

# Verify template was created
qm list | grep 9000
qm config 9000
```

**Expected:** VM 9000 listed with `template: 1` in its config.

---

## 4. VM Clone

**File:** `vm-management/clone-vm.sh`

**Requirements:** A template VM (e.g., ID 9000 from step 3).

```bash
# Clone template 9000 → VM 200 (single)
bash vm-management/clone-vm.sh --source 9000 --id 200 --name integration-test-vm

# Verify
qm list | grep 200
qm config 200

# Clone with count=2 and auto-start
bash vm-management/clone-vm.sh --source 9000 --id 210 --name ci-vm --count 2 --start

# Verify both VMs exist and are running
qm list | grep -E '21[0-1]'
qm status 210
qm status 211

# Cleanup
qm stop 210; qm stop 211
qm destroy 210; qm destroy 211
qm destroy 200
```

---

## 5. LXC Container Creator

**File:** `lxc-management/create-lxc.sh`

**Requirements:** `pct`, `pveam`, available LXC template storage.

```bash
# Ensure a Debian template is available
pveam update
pveam available --section system | grep debian-12

# Create a basic DHCP container (ID 300)
bash lxc-management/create-lxc.sh --id 300 --name integration-test-lxc

# Verify container exists
pct list | grep 300
pct config 300

# Create with static IP and start immediately (adjust to your network)
bash lxc-management/create-lxc.sh \
  --id 301 --name ci-lxc-static \
  --ip 192.168.1.250/24 --gw 192.168.1.1 \
  --cores 1 --memory 512 --start

# Verify running
pct status 301

# Cleanup
pct stop 300; pct destroy 300
pct stop 301; pct destroy 301
```

---

## 6. Bulk Update

**File:** `lxc-management/bulk-update.sh`

**Requirements:** At least one running LXC container.

```bash
# Dry-run — see what would update without making changes
bash lxc-management/bulk-update.sh --lxc-only --dry-run

# Update all running LXC containers
bash lxc-management/bulk-update.sh --lxc-only

# Update all running LXC containers AND QEMU VMs (qemu-guest-agent required in VMs)
bash lxc-management/bulk-update.sh --all

# Update specific container IDs only
bash lxc-management/bulk-update.sh --lxc-only --ids 100,101
```

**Expected:** Each target container shows update output (apt/dnf/apk/pacman/zypper).

---

## 7. Config Backup

**File:** `backup/backup-config.sh`

```bash
# Local backup with 14-day retention to default directory
bash backup/backup-config.sh --keep 14

# Verify backup was created
ls -lh /var/backups/proxmox-config/

# Inspect archive contents
tar -tzf /var/backups/proxmox-config/proxmox-config-*.tar.gz | head -20

# Test remote rsync (substitute a real SSH destination)
bash backup/backup-config.sh \
  --remote backup-user@192.168.1.50:/backups/proxmox \
  --keep 7
```

**Expected:** Timestamped `.tar.gz` present; rsync exits 0 with remote test.

---

## 8. Node Health Check

**File:** `monitoring/check-node-health.sh`

```bash
# Standard run
bash monitoring/check-node-health.sh

# Strict thresholds with restart-on-fail
bash monitoring/check-node-health.sh \
  --disk-threshold 90 \
  --memory-threshold 90 \
  --load-factor 2.0 \
  --restart-unhealthy

# Verify ZFS pools (if applicable)
bash monitoring/check-node-health.sh 2>&1 | grep -E "ZFS|zpool|DEGRADED|ONLINE"

# Verify cluster quorum (multi-node only)
bash monitoring/check-node-health.sh 2>&1 | grep -i "quorum"

# Confirm pvesm storage checks ran
bash monitoring/check-node-health.sh 2>&1 | grep -i "storage"
```

**Expected:** Exit code 0 on a healthy node; any `FAIL` or `WARN` lines identify real issues.

---

## 9. Network Health Check

**File:** `monitoring/check-network-health.sh`

```bash
# Default checks (uses node's default gateway and resolver)
bash monitoring/check-network-health.sh

# Custom target and stricter latency
bash monitoring/check-network-health.sh \
  --target 8.8.8.8 \
  --dns-domain github.com \
  --latency-warn-ms 60

# Skip external checks (airgapped environments)
bash monitoring/check-network-health.sh --skip-external
```

**Expected:** Gateway reachable, DNS resolves, no `FAIL` lines on a healthy network.

---

## 10. Service Recovery

**File:** `monitoring/recover-services.sh`

```bash
# Check-only (no restarts)
bash monitoring/recover-services.sh --check-only

# Custom service list in dry-run mode
bash monitoring/recover-services.sh \
  --services pveproxy,pvedaemon,pvestatd,pve-cluster,corosync \
  --max-retries 3 \
  --dry-run

# Live run (will restart unhealthy services)
bash monitoring/recover-services.sh

# Verify core services are running after recovery
for svc in pveproxy pvedaemon pvestatd pve-cluster; do
  echo -n "$svc: "
  systemctl is-active "$svc"
done
```

**Expected:** All core Proxmox services report `active`.

---

## 11. Proxmox Sentry

**File:** `sentry/install-sentry-lxc.sh`

**Requirements:** Outbound internet (Debian LXC template download + pip install).

```bash
# Install Sentry LXC at ID 900 (adjust IP/gateway)
bash sentry/install-sentry-lxc.sh \
  --id 900 \
  --name pve-sentry \
  --ip 192.168.1.200/24 \
  --gw 192.168.1.1

# Verify LXC was created and is running
pct status 900

# Verify Python dependencies are installed inside the container
pct exec 900 -- python3 -c "import proxmoxer, sklearn, schedule, pandas; print('deps-ok')"

# Copy and configure sentry.conf
pct exec 900 -- cp /opt/sentry/config/sentry.conf.example /opt/sentry/config/sentry.conf
pct exec 900 -- nano /opt/sentry/config/sentry.conf   # fill in PVE host + credentials

# Start agent
pct exec 900 -- systemctl start sentry-agent
pct exec 900 -- systemctl status sentry-agent

# Watch first monitoring cycle (wait ~30 s)
pct exec 900 -- journalctl -u sentry-agent -n 50 --no-pager

# Verify DB was created and has data
pct exec 900 -- python3 -c "
import sqlite3
conn = sqlite3.connect('/var/lib/sentry/sentry.db')
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print('tables:', tables)
conn.close()
"
```

**Expected:** `sentry-agent` service `active (running)`; logs show
`=== Sentry monitoring cycle starting ===` with no ERROR lines after first pass.

---

### Sentry Module Checks (inside Sentry LXC)

```bash
# All commands run inside the Sentry container:
pct exec 900 -- bash

# --- Anomaly detector ---
python3 -c "
import sys; sys.path.insert(0,'/opt/sentry')
from modules.baseline import BaselineCollector
import configparser
cfg = configparser.ConfigParser()
cfg.read('/opt/sentry/config/sentry.conf')
b = BaselineCollector(cfg, '/tmp/test-sentry.db')
m = b.collect()
print('metrics collected:', len(m))
"

# --- Config auditor ---
python3 -c "
import sys; sys.path.insert(0,'/opt/sentry')
from modules.config_auditor import ConfigAuditor
import configparser
cfg = configparser.ConfigParser()
cfg.read('/opt/sentry/config/sentry.conf')
a = ConfigAuditor(cfg)
issues = a.audit(exclude_ids=set())
print('audit issues found:', len(issues))
"

# --- Vulnerability scanner (requires trivy) ---
which trivy && trivy --version || echo "trivy not in PATH; install with: apt install trivy"
python3 -c "
import sys; sys.path.insert(0,'/opt/sentry')
from modules.vuln_scanner import VulnScanner
import configparser
cfg = configparser.ConfigParser()
cfg.read('/opt/sentry/config/sentry.conf')
v = VulnScanner(cfg)
print('vuln scanner initialised, next_scan_due:', v._next_scan)
"

# --- Alert channels (dry-run) ---
python3 -c "
import sys; sys.path.insert(0,'/opt/sentry')
from alerting.alertmanager import AlertManager
import configparser
cfg = configparser.ConfigParser()
cfg.read('/opt/sentry/config/sentry.conf')
am = AlertManager(cfg)
print('enabled channels:', [type(c).__name__ for c in am.channels])
"

exit  # exit the pct exec shell
```

---

## 12. Proxmox Autonomous Fabric (PAF)

**File:** `autonomous-fabric/proxmox-autonomous-fabric.py`

PAF is a planning engine and does **not** require PVE binaries at runtime.
All of the following run on the PVE host directly from the cloned repo.

```bash
cd /opt/proxmox-scripts

# Build a plan from sample inputs
python3 autonomous-fabric/proxmox-autonomous-fabric.py orchestrate \
  --intent-file autonomous-fabric/examples/intent.sample.json \
  --state-file autonomous-fabric/examples/cluster-state.sample.json \
  --policy-file autonomous-fabric/examples/policy.sample.json \
  --capture-snapshot \
  --history-file /tmp/paf-history.json \
  --output-json /tmp/paf-plan.json

# Inspect the plan
python3 -c "
import json
with open('/tmp/paf-plan.json') as f:
    p = json.load(f)
print(f\"Actions: {len(p['actions'])}\")
for a in p['actions']:
    print(f\"  [{a['phase']}] {a['title']} — confidence={a['confidence']} blast={a['blast_radius_score']}\")
print(f\"Digital twin: risk_delta={p['digital_twin']['predicted_risk_delta_pct']}%\")
"

# Capture a real-time state snapshot (replace with actual pvesh output for production)
python3 autonomous-fabric/proxmox-autonomous-fabric.py capture-snapshot \
  --state-file autonomous-fabric/examples/cluster-state.sample.json \
  --snapshot-store /var/lib/paf-snapshots.json

# Replay from an earlier state
python3 autonomous-fabric/proxmox-autonomous-fabric.py orchestrate \
  --intent-file autonomous-fabric/examples/intent.sample.json \
  --state-file autonomous-fabric/examples/cluster-state.sample.json \
  --policy-file autonomous-fabric/examples/policy.sample.json \
  --snapshot-store /var/lib/paf-snapshots.json \
  --replay-at "$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S+00:00)" \
  --output-json /tmp/paf-replay.json

# Generate markdown runbook from execution history
python3 autonomous-fabric/proxmox-autonomous-fabric.py runbook \
  --history-file /tmp/paf-history.json \
  --output-markdown /tmp/paf-runbook.md

cat /tmp/paf-runbook.md
```

**Expected:** Plan JSON contains one or more actions with trust narratives; runbook
markdown renders at least one event block.

---

## Sandbox Pre-Flight Results (CI environment)

The following checks run automatically in this repository's CI-equivalent sandbox
environment (Ubuntu 24.04, no PVE binaries present) and **must all pass** before
any PR is merged:

| Check | Command | Status |
|-------|---------|--------|
| Shell syntax — all `.sh` files | `find . -name '*.sh' \| xargs -n1 bash -n` | ✅ |
| Python compile — sentry | `python3 -m compileall -q sentry` | ✅ |
| Python compile — autonomous-fabric | `python3 -m compileall -q autonomous-fabric` | ✅ |
| PAF orchestrate subcommand | see PAF section above | ✅ |
| PAF capture-snapshot subcommand | see PAF section above | ✅ |
| PAF replay-at subcommand | see PAF section above | ✅ |
| PAF runbook subcommand | see PAF section above | ✅ |
| Sample JSON validity | `python3 -c "import json; ..."` | ✅ |
| Stdlib-only sentry imports | `recommender`, `alertmanager` | ✅ |

---

## Known PVE-Dependency Limitations

Scripts and modules that call PVE binaries will gracefully skip or exit with
an informative message when those binaries are absent:

| Script / Module | Behaviour when PVE missing |
|-----------------|---------------------------|
| `check-node-health.sh` | `pvesm not found` warning; skips storage section |
| `check-node-health.sh` | `pvecm not found`; skips cluster quorum section |
| `sentry/modules/baseline.py` | Logs `proxmoxer not available; Proxmox API metrics disabled` |
| `sentry/modules/config_auditor.py` | Logs `proxmoxer not available; config audit requires Proxmox API` |
| `sentry/modules/vuln_scanner.py` | Logs `trivy not found` if binary absent; skips scan |
| `lxc-management/bulk-update.sh` | `pct list` returns empty; no containers updated (safe) |

---

## Suggested Verification Order on a Fresh PVE Node

1. **Step 1** — Post-install  
2. **Step 3** — Create VM template (required by step 4)  
3. **Step 4** — Clone VM (smoke test for `qm` path)  
4. **Step 5** — Create LXC (smoke test for `pct` path)  
5. **Steps 6–10** — Bulk update, backup, monitoring (require running containers)  
6. **Step 11** — Install and validate Sentry LXC  
7. **Step 12** — Run PAF on live cluster state snapshot

---

*Generated from repository code analysis — keep this file updated when new scripts
or modules are added.*
