"""
modules/vuln_scanner.py
Proxmox Sentry – Trivy vulnerability scanning integration.

Discovers running LXC containers and QEMU VMs on the Proxmox host,
runs Trivy against their root filesystems / OS packages, and returns
structured vulnerability findings.
"""

import configparser
import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("sentry.vuln_scanner")

# Schema for scan state tracking
SCHEMA = """
CREATE TABLE IF NOT EXISTS vuln_scans (
    vmid        TEXT NOT NULL,
    scan_type   TEXT NOT NULL,
    last_scan   REAL NOT NULL,
    PRIMARY KEY (vmid, scan_type)
);
"""

SEVERITY_ORDER = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class VulnScanner:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg            = cfg
        self.db_file        = cfg.get("sentry", "db_file", fallback="/var/lib/sentry/sentry.db")
        self.enabled        = cfg.getboolean("vuln_scanner", "enabled", fallback=True)
        self.scan_interval  = cfg.getint(
            "vuln_scanner", "scan_interval_seconds", fallback=86400
        )
        self.min_severity   = cfg.get("vuln_scanner", "min_severity", fallback="HIGH").upper()
        self.ignore_unfixed = cfg.getboolean("vuln_scanner", "ignore_unfixed", fallback=True)
        self.trivy_bin      = cfg.get("vuln_scanner", "trivy_bin", fallback="trivy")

        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file, timeout=10)

    # ── Public API ─────────────────────────────────────────────────────────────

    def scan_due(self) -> List[Dict]:
        """Scan containers/VMs that are due for a re-scan; return findings."""
        if not self.enabled:
            return []
        if not self._trivy_available():
            log.warning("Trivy binary not found at '%s'; skipping vuln scan.", self.trivy_bin)
            return []

        findings = []
        for target in self._discover_targets():
            vmid      = target["vmid"]
            scan_type = target["type"]
            if not self._is_due(vmid, scan_type):
                continue
            log.info("Running Trivy scan on %s %s…", scan_type, vmid)
            result = self._run_trivy(target)
            self._mark_scanned(vmid, scan_type)
            findings.extend(self._parse_trivy_output(result, target))

        return findings

    def scan_all(self) -> List[Dict]:
        """Force a scan of all discovered targets regardless of schedule."""
        if not self.enabled or not self._trivy_available():
            return []
        findings = []
        for target in self._discover_targets():
            result = self._run_trivy(target)
            self._mark_scanned(target["vmid"], target["type"])
            findings.extend(self._parse_trivy_output(result, target))
        return findings

    # ── Target discovery ───────────────────────────────────────────────────────

    def _discover_targets(self) -> List[Dict]:
        """Discover running LXC containers using pct."""
        targets = []
        try:
            result = subprocess.run(
                ["pct", "list"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 3:
                    continue
                vmid, status = parts[0], parts[1]
                if status != "running":
                    continue
                targets.append({
                    "vmid":   vmid,
                    "type":   "lxc",
                    "name":   parts[2] if len(parts) > 2 else f"ct-{vmid}",
                })
        except FileNotFoundError:
            log.debug("pct not found; running in standalone/non-Proxmox mode.")
        except Exception as exc:
            log.debug("LXC discovery error: %s", exc)

        # Also scan the host OS itself
        targets.append({"vmid": "host", "type": "host", "name": "proxmox-host"})
        return targets

    # ── Scan scheduling ────────────────────────────────────────────────────────

    def _is_due(self, vmid: str, scan_type: str) -> bool:
        if self.scan_interval <= 0:
            return True
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_scan FROM vuln_scans WHERE vmid=? AND scan_type=?",
                (vmid, scan_type),
            ).fetchone()
        if row is None:
            return True
        return (time.time() - row[0]) >= self.scan_interval

    def _mark_scanned(self, vmid: str, scan_type: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO vuln_scans (vmid, scan_type, last_scan) VALUES (?,?,?)
                   ON CONFLICT(vmid, scan_type) DO UPDATE SET last_scan=excluded.last_scan""",
                (vmid, scan_type, time.time()),
            )

    # ── Trivy execution ────────────────────────────────────────────────────────

    def _trivy_available(self) -> bool:
        try:
            subprocess.run(
                [self.trivy_bin, "--version"],
                capture_output=True, timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _run_trivy(self, target: Dict) -> Optional[Dict]:
        cmd = [self.trivy_bin, "--quiet", "--format", "json"]

        if self.ignore_unfixed:
            cmd += ["--ignore-unfixed"]
        cmd += ["--severity", self.min_severity + ",CRITICAL"]

        if target["type"] == "lxc":
            vmid = target["vmid"]
            # Mount the container rootfs and scan it
            rootfs = f"/var/lib/lxc/{vmid}/rootfs"
            if not os.path.isdir(rootfs):
                rootfs = f"/rpool/data/subvol-{vmid}-disk-0"
            if not os.path.isdir(rootfs):
                log.debug("Cannot locate rootfs for LXC %s; falling back to OS scan.", vmid)
                cmd += ["os", "--scanners", "vuln", rootfs if os.path.isdir(rootfs) else "/"]
            else:
                cmd += ["rootfs", rootfs]
        else:
            # Scan host OS packages
            cmd += ["os", "--scanners", "vuln", "/"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.stdout.strip():
                return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            log.warning("Trivy scan timed out for %s %s", target["type"], target["vmid"])
        except json.JSONDecodeError as exc:
            log.debug("Trivy JSON parse error: %s", exc)
        except Exception as exc:
            log.error("Trivy execution error: %s", exc)
        return None

    # ── Output parsing ─────────────────────────────────────────────────────────

    def _parse_trivy_output(self, data: Optional[Dict], target: Dict) -> List[Dict]:
        if not data:
            return []

        findings = []
        min_ord = SEVERITY_ORDER.get(self.min_severity, 3)

        results = data.get("Results", [])
        for res in results:
            vulns = res.get("Vulnerabilities") or []
            for vuln in vulns:
                sev = vuln.get("Severity", "UNKNOWN").upper()
                if SEVERITY_ORDER.get(sev, 0) < min_ord:
                    continue

                pkg   = vuln.get("PkgName", "unknown")
                vid   = vuln.get("VulnerabilityID", "CVE-UNKNOWN")
                title = vuln.get("Title", f"Vulnerability in {pkg}")
                desc  = vuln.get("Description", "")
                fixed = vuln.get("FixedVersion", "")
                score = vuln.get("CVSS", {}).get("nvd", {}).get("V3Score", None)

                alert_sev = "critical" if sev == "CRITICAL" else "warning"

                finding = {
                    "type":          "vulnerability",
                    "severity_level": sev,
                    "severity":       alert_sev,
                    "vmid":           target["vmid"],
                    "name":           target["name"],
                    "cve":            vid,
                    "package":        pkg,
                    "installed":      vuln.get("InstalledVersion", ""),
                    "fixed_version":  fixed,
                    "cvss_score":     score,
                    "title":          f"[{sev}] {vid} in {pkg} on {target['name']}",
                    "description":    (
                        f"{vid}: {title}. "
                        f"Package: {pkg} ({vuln.get('InstalledVersion', '?')}). "
                        + (f"Fixed in: {fixed}." if fixed else "No fix available.")
                    ),
                    "references":     vuln.get("References", [])[:3],
                }
                findings.append(finding)

        return findings
